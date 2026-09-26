"""t66_merge.py — T66 orchestrator-owned merge (single writer).

Merges T66-adapted scan records into the production diagnostics DBs.

Safety contract (mirrors scripts/t63_merge.py + scripts/merge_t45_dtc.py):
  * single writer: only the orchestrator runs this with --apply
  * .bak backup written before any DB write
  * record counts are FROZEN (new keys are skipped, never added)
  * evidence gate: every record must carry a verbatim `evidence` string >= 20 chars
  * never overwrite populated content; only fill empty fields / append bundles
  * B-09 concurrency: an exclusive cross-process lock
    (data/diagnostics/.t66_merge.lock — msvcrt on Windows, flock on POSIX) is
    taken BEFORE the DBs are read and held through both commits; a SHA-256+size
    compare-and-swap over BOTH DBs aborts nonzero if either moved under us; the
    commit stages both files, fsyncs, replaces both, rolls the first back from
    .bak if the second replace fails, and re-hashes both after the write.
  * I-18 evidence provenance: every KEPT record stores the exact evidence text
    + its SHA-256, source URL/ID, retrieval timestamp, adapter version and an
    explicit verification status; unverified records are marked
    non-authoritative (`evidence_authoritative: false`).
  * English text NEVER lands in Turkish DTC fields (tests/unit/test_t46_merge.py):
      DTC  symptoms_en (list) / causes_en (str) / description_en (str) / title_en (str)
           + matching *_en_source URL fields
      DTC  procedures_full (list of English step strings) + procedures_source(_url)
      J1939 fields are English-primary (T63 precedent): symptoms / diagnostic_steps /
           causes fill-if-empty; procedures_full is append-only by source_url.

Usage:
    python scripts/t66_merge.py --inputs output/mr_t66_obdfyi.json \
        --report output/merge_t66_report.json          # dry-run
    python scripts/t66_merge.py --inputs ... --apply    # write
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DTC_DB = ROOT / "data" / "diagnostics" / "dtc_database.json"
J1939_DB = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"

# T80h: the subject-anchor gate lives in the AI package (it is a pure text
# classifier with no HAL/TX/network imports, so it is legal there under the
# offline-AI rule). Imported defensively: a merge must still run if the AI
# package cannot be imported (e.g. an isolated test harness).
try:
    sys.path.insert(0, str(ROOT))
    from src.engine.ai.subject_anchor import check_subject_anchor
except Exception:  # pragma: no cover - defensive
    check_subject_anchor = None

# B-09: cross-process lock lives NEXT TO the DBs (not in output/) so every
# writer of this directory shares one rendezvous point.
LOCK_PATH = ROOT / "data" / "diagnostics" / ".t66_merge.lock"

MIN_TEXT = 30
MIN_EVIDENCE = 20

# I-18: a merged record's evidence chain is authoritative ONLY when the
# producing channel says a human/independent verification happened. The
# adapter always emits "unverified", so every t66 merge lands
# non-authoritative; this allowlist is the single reviewable upgrade gate.
AUTHORITATIVE_STATUSES = frozenset({"verified", "operator_verified", "human_verified"})

# DTC English-target fields: (record_field, db_field, db_source_field, shape)
DTC_EN_MAP = (
    ("symptoms_en", "symptoms_en", "symptoms_en_source", "list"),
    ("causes_en", "causes_en", "causes_en_source", "str"),
    ("description_en", "description_en", "description_en_source", "str"),
    ("title_en", "title_en", "title_en_source", "str"),
)

J1939_FILLABLE = ("symptoms", "diagnostic_steps", "causes")


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lock_backend(fh) -> None:
    """One non-blocking exclusive-lock attempt on an open lock-file handle."""
    try:
        import msvcrt  # type: ignore[import-not-found]
    except ImportError:
        import fcntl  # type: ignore[import-not-found]

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock_backend(fh) -> None:
    try:
        import msvcrt  # type: ignore[import-not-found]
    except ImportError:
        import fcntl  # type: ignore[import-not-found]

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    else:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _file_lock(path: Path, timeout_s: float = 60.0):
    """B-09: exclusive cross-process lock over the whole read-modify-write.

    Windows uses ``msvcrt.locking`` (LK_NBLCK) on the first byte; POSIX uses
    ``fcntl.flock`` LOCK_EX. The lock file sits NEXT TO the DBs so every writer
    of the directory shares one rendezvous point. Polls until ``timeout_s``,
    then aborts nonzero: proceeding unlocked could silently lose another
    writer's update (the exact failure B-09 documents).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: PTH123
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                _lock_backend(fh)
                break
            except ImportError as exc:  # neither msvcrt nor fcntl available
                raise SystemExit(
                    f"no file-lock backend available ({exc}) — aborting (fail-closed)"
                ) from exc
            except OSError:
                if time.monotonic() >= deadline:
                    raise SystemExit(
                        f"another writer holds {path.name} after {timeout_s:.0f}s "
                        "— aborting (fail-closed)"
                    ) from None
                time.sleep(0.2)
        try:
            yield fh
        finally:
            try:
                _unlock_backend(fh)
            except OSError:
                pass
    finally:
        fh.close()


def _fingerprint_bytes(raw: bytes) -> dict:
    """SHA-256 + size — the B-09 compare-and-swap basis for one DB file."""
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def file_fingerprint(path: Path) -> dict:
    return _fingerprint_bytes(path.read_bytes())


def read_databases(dtc_db: Path, j1939_db: Path) -> tuple[dict, dict, dict]:
    """Read both DBs ONCE; return (dtc, j1939, fingerprints).

    The fingerprints are taken from the exact bytes that were parsed, so the
    pre-commit CAS check can detect any write that landed in between.
    """
    dtc_raw = dtc_db.read_bytes()
    j1939_raw = j1939_db.read_bytes()
    fingerprints = {
        "dtc": _fingerprint_bytes(dtc_raw),
        "j1939": _fingerprint_bytes(j1939_raw),
    }
    return json.loads(dtc_raw), json.loads(j1939_raw), fingerprints


def _verify_unchanged(path: Path, expected: dict) -> None:
    """B-09 compare-and-swap: abort nonzero if the file moved since it was read."""
    try:
        cur = file_fingerprint(path)
    except OSError as exc:
        raise SystemExit(f"{path.name} unreadable during CAS check ({exc}) — aborting") from exc
    if cur != expected:
        raise SystemExit(
            f"{path.name} changed since it was read "
            f"(sha256 {expected['sha256'][:12]}->{cur['sha256'][:12]}, "
            f"size {expected['size']}->{cur['size']}): another writer touched it "
            "— aborting (lost-update guard, fail-closed)"
        )


def _stage_tmp(path: Path, text: str) -> Path:
    """Stage a new version as ``<name>.tmp`` in the same dir, fsync, verify."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = text.encode("utf-8")
    with open(tmp, "wb") as fh:  # noqa: PTH123
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if tmp.read_bytes() != data:
        raise SystemExit(f"staged tmp mismatch for {path.name} — aborting")
    return tmp


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync (POSIX); a no-op on Windows."""
    try:
        fd = os.open(path, os.O_RDONLY)  # noqa: PTH123
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _commit_databases(
    dtc_db: Path,
    j1939_db: Path,
    dtc: dict,
    j1939: dict,
    fingerprints: dict,
    report: dict,
) -> None:
    """B-09 both-DB atomic commit.

    Order: CAS on both files -> .bak both -> stage+fsync both tmps -> replace
    both -> rollback the first from .bak if the second replace fails ->
    verify-after-write (re-hash both) with rollback on mismatch.
    """
    _verify_unchanged(dtc_db, fingerprints["dtc"])
    _verify_unchanged(j1939_db, fingerprints["j1939"])

    # (path, fingerprint key) in commit order: J1939 first (T63 order).
    order: tuple[tuple[Path, str], ...] = ((j1939_db, "j1939"), (dtc_db, "dtc"))
    backups = {path: path.with_suffix(path.suffix + ".bak_t66") for path, _ in order}
    for path, _ in order:
        shutil.copy2(path, backups[path])

    payloads = {
        j1939_db: json.dumps(j1939, ensure_ascii=False, indent=1) + "\n",
        dtc_db: json.dumps(dtc, ensure_ascii=False, indent=1) + "\n",
    }
    tmps = {path: _stage_tmp(path, payloads[path]) for path, _ in order}

    def _restore_from_bak(path: Path, key: str) -> None:
        """Copy .bak back over the DB and prove the original bytes returned."""
        try:
            shutil.copy2(backups[path], path)
        except OSError as exc:
            raise SystemExit(
                f"ROLLBACK FAILED for {path.name} ({exc}) — DBs may be inconsistent; "
                f"restore from {backups[path].name} manually"
            ) from exc
        if file_fingerprint(path) != fingerprints[key]:
            raise SystemExit(
                f"ROLLBACK of {path.name} did not restore the original bytes — "
                f"restore from {backups[path].name} manually"
            )

    def _rollback(reason: str) -> None:
        """Restore BOTH DBs from their .bak (idempotent for untouched files)."""
        for path, key in order:
            if file_fingerprint(path) != fingerprints[key]:
                _restore_from_bak(path, key)
        raise SystemExit(f"{reason} — both DBs restored from .bak_t66 (fail-closed)")

    for path, _key in order:
        # Windows: os.replace onto a file that another process (antivirus,
        # indexer, an open handle) holds raises WinError 5. Measured three
        # times in one session. Retry with backoff before declaring failure —
        # the handle is almost always released within a second.
        last_exc: OSError | None = None
        for attempt in range(6):
            try:
                tmps[path].replace(path)
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc
                time.sleep(0.4 * (attempt + 1))
        if last_exc is not None:
            _rollback(f"commit failed replacing {path.name}: {last_exc}")

    # Verify-after-write: re-hash both replaced files against the staged payload.
    for path, _key in order:
        want = _fingerprint_bytes(payloads[path].encode("utf-8"))
        if file_fingerprint(path) != want:
            _rollback(f"post-write verification failed for {path.name}")
    _fsync_dir(dtc_db.parent)

    report["db_sha256_before"] = {
        "dtc": fingerprints["dtc"]["sha256"], "j1939": fingerprints["j1939"]["sha256"],
    }
    report["db_sha256_after"] = {
        "dtc": file_fingerprint(dtc_db)["sha256"],
        "j1939": file_fingerprint(j1939_db)["sha256"],
    }


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _norm_list(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _norm_str(value) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, (list, tuple)):
        return " | ".join(str(x).strip() for x in value if str(x).strip())
    return ""


def _title_norm_key(text) -> str:
    """Polarity-aware normalization for cross-code title comparison.

    T80: (+) and (-) bus halves are DIFFERENT codes (U0012 "(+) Open" vs
    U0015 "(-) Open"), so a plain punctuation strip would fold them into one
    key and hide real shifts. They are expanded to words BEFORE stripping;
    the remaining punctuation (quotes, dashes, slashes) is noise and goes.
    """
    s = str(text or "").lower()
    s = s.replace("(+)", " plus ").replace("(-)", " minus ")
    s = s.replace("+", " plus ").replace("-", " minus ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_index(dtc: dict) -> dict[str, set[str]]:
    """Normalized `title` -> set of codes carrying it (built once per merge)."""
    index: dict[str, set[str]] = {}
    for _code, _entry in dtc.items():
        if not isinstance(_entry, dict):
            continue
        _key = _title_norm_key(_entry.get("title"))
        if _key:
            index.setdefault(_key, set()).add(_str_code(_code))
    return index


def _str_code(value) -> str:
    return str(value or "").strip().upper()


# T80: per-DB title index cache. Keyed by ``id(dtc)`` and revalidated by
# identity, so a merge over a 14k-record DB pays the O(n) scan ONCE while the
# public ``merge_dtc(rec, dtc, report, apply)`` signature stays unchanged
# (existing callers and test monkeypatches keep working).
_TITLE_INDEX_CACHE: dict[int, tuple[dict, dict[str, set[str]]]] = {}


def _cached_title_index(dtc: dict) -> dict[str, set[str]]:
    hit = _TITLE_INDEX_CACHE.get(id(dtc))
    if hit is not None and hit[0] is dtc:
        return hit[1]
    index = _title_index(dtc)
    if len(_TITLE_INDEX_CACHE) > 4:  # tiny bound; merges are one DB at a time
        _TITLE_INDEX_CACHE.clear()
    _TITLE_INDEX_CACHE[id(dtc)] = (dtc, index)
    return index


def _title_en_is_foreign_shift(
    candidate: str, own_title, index: dict[str, set[str]], code: str
) -> bool:
    """T80: is `candidate` another code's `title` rather than THIS code's?

    True when the normalized candidate matches the `title` of a DIFFERENT
    record and does NOT match this record's own `title`. The index is built
    once per merge run (see ``_cached_title_index``) and never stored on the
    DB dict itself — the count-frozen gate would abort on a stray key.
    """
    key = _title_norm_key(candidate)
    if not key:
        return False
    own = _title_norm_key(own_title)
    if own and key == own:
        return False  # a legit reword of the record's own meaning
    owners = index.get(key) or set()
    return any(owner != _str_code(code) for owner in owners)


def _write_provenance(entry: dict, rec: dict, src_url: str) -> None:
    """I-18: attach the evidence chain to a KEPT merged record (fill-if-empty).

    Carries the exact evidence text AND its SHA-256, the source URL/ID, the
    retrieval timestamp, the adapter version, and an explicit verification
    status. A record whose status is not in AUTHORITATIVE_STATUSES is marked
    ``evidence_authoritative: false`` so no reader can mistake harvest output
    for independently verified evidence (quality "unverified" stays visible).
    """
    prov = rec.get("provenance") if isinstance(rec.get("provenance"), dict) else {}
    evidence = rec.get("evidence")
    status = str(
        prov.get("verification_status") or rec.get("quality") or "unverified"
    ).strip() or "unverified"
    if isinstance(evidence, str) and evidence.strip():
        entry.setdefault("evidence", evidence)
        entry.setdefault(
            "evidence_sha256",
            prov.get("evidence_sha256") or sha256_text(evidence),
        )
    if src_url:
        entry.setdefault("evidence_url", src_url)
        entry.setdefault("evidence_source_url", src_url)
    for field, value in (
        ("evidence_source_id", prov.get("source_id")),
        ("evidence_retrieved_at", prov.get("retrieved_at")),
    ):
        if value:
            entry.setdefault(field, value)
    entry.setdefault(
        "evidence_adapter_version",
        prov.get("adapter_version") or "t66_adapter/<=1.0-legacy",
    )
    entry.setdefault("evidence_verification_status", status)
    entry.setdefault("evidence_authoritative", status in AUTHORITATIVE_STATUSES)


def _url(value) -> str:
    # Bundle dedup compares source_url as an EXACT string (merge_dtc
    # richness loop, merge_j1939 `seen` set). Every stored bundle URL is
    # trailing-slash-free (161/161 measured) while sitemap-derived
    # inventories carry the slash — without this rstrip a re-harvest of an
    # already-stored page appends a duplicate bundle instead of hitting
    # the richer/poorer rule.
    v = str(value or "").strip().rstrip("/")
    return v if v.startswith(("http://", "https://")) else ""


def key_for_dtc(rec: dict) -> str | None:
    url = rec.get("url") or ""
    m = re.search(r"([pbcu][0-9a-f]{4})", url, re.I)
    if m:
        return m.group(1).upper()
    code = rec.get("key") or rec.get("code")
    return str(code).upper() if code else None


def key_for_j1939(rec: dict) -> str | None:
    if rec.get("key"):
        m = re.match(r"SPN_(\d+)(?:_FMI_\d+)?$", str(rec["key"]))
        if m:
            return f"SPN_{m.group(1)}"
        return str(rec["key"])
    if rec.get("spn") is not None:
        return f"SPN_{int(rec['spn'])}"
    return None


def merge_dtc(rec: dict, dtc: dict, report: dict, apply: bool) -> bool:
    code = key_for_dtc(rec)
    if not code or code not in dtc:
        report["skipped"]["unknown_key"] += 1
        return False
    entry = dtc[code]
    src_url = _url(rec.get("url") or rec.get("evidence_url"))
    filled: list[str] = []

    # 0) T80 (silent title_en shift): reject a payload whose `title_en` is the
    #    DB's OWN `title` of a DIFFERENT code. Measured 2026-09-26: 217 codes
    #    carry a `title_en` that equals another record's `title` and differs
    #    from their own — a source-side off-by-N shift (openlaborproject /
    #    obdhut / OBDex all reproduce it) that the merge happily propagated
    #    because it only checked "target empty". Without this gate the SAME
    #    shifted text can re-enter the DB through any future harvest. The
    #    comparison is polarity-aware ((+) / (-) are not interchangeable:
    #    U0012 "(+) Open" vs U0015 "(-) Open" are different codes) and
    #    normalized for case/whitespace/punctuation. Rejected payloads are
    #    counted and dropped; every other field on the record still merges.
    _te = rec.get("title_en")
    if not _is_empty(_te) and _title_en_is_foreign_shift(
            _norm_str(_te), entry.get("title"), _cached_title_index(dtc), code):
        report["skipped"]["title_en_shift"] = (
            report["skipped"].get("title_en_shift", 0) + 1)
        _te = None

    # 0b) T80h (subject misattribution): an adapter that knows the page's OWN
    #     meaning claim (h1 / subtitle / meta title) passes it as
    #     `subject_claim`. Measured 2026-09-26: geekobd.com pages were about a
    #     DIFFERENT subject than the record for 72.4% of 6,330 codes (obdhut
    #     22.1%), yet every one passed the length/evidence/shape/distinctness
    #     gates because none asked "is this page about THIS code?". A blocked
    #     claim drops only the EN payload; the record's other fields still
    #     merge. An abstain (either side names no system) does NOT block —
    #     an unverifiable claim is not evidence of a mismatch.
    _claim = rec.get("subject_claim")
    if (check_subject_anchor is not None and not _is_empty(_claim)):
        _verdict = check_subject_anchor(entry.get("title"), _claim)
        if _verdict.status == "block":
            report["skipped"]["subject_conflict"] = (
                report["skipped"].get("subject_conflict", 0) + 1)
            for _f in ("symptoms_en", "causes_en", "description_en", "title_en"):
                rec.pop(_f, None)
            _te = None

    # 1) English payload -> *_en fields, only when the target is empty.
    for rec_field, db_field, db_src_field, shape in DTC_EN_MAP:
        val = rec.get(rec_field)
        if rec_field == "title_en" and _te is None:
            continue
        if _is_empty(val):
            continue
        if not _is_empty(entry.get(db_field)):
            report["skipped"]["already_full"] += 1
            continue
        if not src_url:
            report["skipped"]["no_source_url"] += 1
            continue
        norm = _norm_list(val) if shape == "list" else _norm_str(val)
        if _is_empty(norm):
            continue
        if apply:
            entry[db_field] = norm
            entry[db_src_field] = src_url
        filled.append(db_field)

    # 2) English steps -> procedures_full (list), only when empty.
    #
    # T78 (silent field-name mismatch): three adapters (T74b engine-codes,
    # T74c wayback-hex, T76 obd2) wrote the payload under the DB's OWN field
    # name — `procedures_full` — while this reader only looked for the adapter
    # keys `procedures_steps`/`steps`. Measured: 36 codes' step lists (32 T76,
    # 3 T74c, 1 T74b) were silently dropped, and the merge report still said
    # "applied" because a different field on the same record filled. The alias
    # below closes the class; the T69 append path further down catches the
    # already-full codes so a second independent source is never discarded.
    # T78: the flat `procedures_full` alias is DB-shaped input — accept it ONLY
    # as a flat list of strings. A list of DICTS (J1939-style bundle rows that
    # occasionally sit on DTC rows — the copilot's own reader guards for this
    # exact shape) would be stringified into garbage by _norm_list.
    _step_src = rec.get("procedures_steps") or rec.get("steps")
    if _step_src is None:
        _pf_alias = rec.get("procedures_full")
        if (isinstance(_pf_alias, (list, tuple)) and _pf_alias
                and all(isinstance(x, str) for x in _pf_alias)):
            _step_src = _pf_alias
    steps = _norm_list(_step_src)
    filled_single_steps = False
    if steps and _is_empty(entry.get("procedures_full")):
        if apply:
            entry["procedures_full"] = steps
            entry.setdefault("procedures_source", rec.get("source"))
            entry.setdefault("procedures_source_url", src_url)
        filled.append("procedures_full")
        filled_single_steps = True

    # 3) T69: multi-source append. The DTC-side `procedures_full` is a flat
    # list[str] with ONE `procedures_source_url`, so fill-if-empty above can
    # only ever capture the FIRST source. Measured on the live DB: P0420 held a
    # 7-step obd2.com summary while troubleshootmyvehicle.com carries a 77-step
    # test procedure for the same code — the richer harvest had nowhere to go
    # and was silently dropped. The append path stores ADDITIONAL sources in a
    # sibling list so the original is never overwritten and every source keeps
    # its own URL. Dedup is by source_url, exactly like the J1939 side.
    appended_bundle = 0
    bundle = rec.get("procedures_full_append")
    # T78 alias: an adapter that wrote a FLAT `procedures_full` list (no bundle
    # wrapper) is treated as a one-source bundle here. Without this, a code whose
    # `procedures_full` was already populated from source A silently discarded
    # source B's independent step list — measured on 325 T76 codes, all of whose
    # step sets were fully disjoint from the stored faultcode.org text. The
    # source_url dedup below (and T69-G richness upgrade) keeps re-application
    # idempotent. Shape guard: flat list of STRINGS only (see above).
    if not isinstance(bundle, dict) and isinstance(rec.get("procedures_full"), (list, tuple)):
        _flat_raw = rec.get("procedures_full")
        if _flat_raw and all(isinstance(x, str) for x in _flat_raw):
            _flat = _norm_list(_flat_raw)
            if _flat:
                bundle = {"steps": _flat, "source_site": rec.get("source"),
                          "quality": "unverified", "method": "free"}
    if isinstance(bundle, dict) and src_url:
        _bundle_steps = _norm_list(bundle.get("steps"))
        if _bundle_steps:
            existing = entry.get("procedures_full_multi")
            if not isinstance(existing, list):
                existing = []
            # Never duplicate the single-source payload already stored above.
            # T78: `filled_single_steps` means THIS record just became the
            # single-source owner (its URL is written to procedures_source_url
            # in apply mode). Without the flag, a dry-run — which does not write
            # the URL — would report the same payload twice (once as
            # procedures_full, once as a multi bundle) while an apply reports it
            # once. The report must not depend on --apply.
            _already_single = filled_single_steps or (
                src_url == str(entry.get("procedures_source_url") or "")
            )
            if not _already_single:
                _clean_bundle = {
                    "source_url": src_url,
                    "source_site": bundle.get("source_site") or rec.get("source"),
                    "steps": _bundle_steps,
                    "causes": _norm_list(bundle.get("causes")),
                    "symptoms": _norm_list(bundle.get("symptoms")),
                    "quality": str(bundle.get("quality") or "unverified"),
                    "method": str(bundle.get("method") or "free"),
                }
                # T69-G: the same URL can be re-harvested later and come back
                # RICHER. The original rule was "URL already present -> skip",
                # which silently kept the poorer copy. Measured on the live DB:
                # honda/2200-2300/how-to-test-code-p0117 held a 40-step bundle
                # while the newer harvest of the SAME URL carried 75 steps — 35
                # steps were dropped with no trace. The rule is now
                # richness-aware: a richer re-harvest REPLACES its own older
                # bundle; an equal or poorer one is still refused, so re-running
                # a merge can never shrink the DB.
                _idx = None
                for _i, _b in enumerate(existing):
                    if isinstance(_b, dict) and str(_b.get("source_url")) == src_url:
                        _idx = _i
                        break
                if _idx is None:
                    if apply:
                        existing.append(_clean_bundle)
                        entry["procedures_full_multi"] = existing
                    appended_bundle = 1
                    filled.append("procedures_full_multi")
                else:
                    _old_steps = _norm_list(existing[_idx].get("steps"))
                    if len(_bundle_steps) > len(_old_steps):
                        if apply:
                            existing[_idx] = _clean_bundle
                            entry["procedures_full_multi"] = existing
                        report.setdefault("upgraded", []).append(
                            {
                                "key": code,
                                "url": src_url,
                                "steps_before": len(_old_steps),
                                "steps_after": len(_bundle_steps),
                            }
                        )
                        appended_bundle = 1
                        filled.append("procedures_full_multi")
                    else:
                        report["skipped"]["poorer_reharvest"] = (
                            report["skipped"].get("poorer_reharvest", 0) + 1
                        )

    if filled:
        if apply:
            entry.setdefault("source", rec.get("source"))
            # I-18: evidence of KEPT records must survive the merge — exact
            # text/hash + source URL/ID + retrieval timestamp + adapter
            # version + verification status (unverified => non-authoritative).
            _write_provenance(entry, rec, src_url)
        report["applied"].append(
            {
                "key": code,
                "db": "dtc",
                "fields": filled,
                "url": src_url,
                "appended_bundle": appended_bundle,
            }
        )
        return True
    report["skipped"]["no_fillable"] += 1
    return False


def merge_j1939(rec: dict, spns: dict, report: dict, apply: bool) -> bool:
    key = key_for_j1939(rec)
    if not key or key not in spns:
        report["skipped"]["unknown_key"] += 1
        return False
    entry = spns[key]
    src_url = _url(rec.get("url") or rec.get("evidence_url"))
    filled: list[str] = []
    appended = 0

    # 1) Structured bundle -> procedures_full append (dedup by source_url).
    bundle = rec.get("procedures_full_append")
    if isinstance(bundle, dict) and src_url:
        existing = entry.get("procedures_full")
        if not isinstance(existing, list):
            existing = []
        seen = {str(b.get("source_url")) for b in existing if isinstance(b, dict)}
        if src_url not in seen:
            if apply:
                existing.append(bundle)
                entry["procedures_full"] = existing
                entry.setdefault("procedures_source", rec.get("source"))
                entry.setdefault("procedures_license", "belirsiz")
                entry.setdefault("procedures_match_key", "spn_fmi")
            appended = 1

    # 2) Flat fields: fill only when empty (English-primary per T63 precedent).
    for field in J1939_FILLABLE:
        val = _norm_list(rec.get(field))
        if not val:
            continue
        if not _is_empty(entry.get(field)):
            report["skipped"]["already_full"] += 1
            continue
        if apply:
            entry[field] = val
            entry.setdefault("evidence_url", src_url)
        filled.append(field)

    if filled or appended:
        report["applied"].append(
            {"key": key, "db": "j1939", "fields": filled, "url": src_url,
             "procedures_appended": appended}
        )
        if apply:
            _write_provenance(entry, rec, src_url)
        return True
    report["skipped"]["no_fillable"] += 1
    return False


def _run_merge(args, dtc_db: Path, j1939_db: Path) -> int:
    """Read-modify-write body. Caller holds the B-09 lock when applying."""
    dtc, j1939, fingerprints = read_databases(dtc_db, j1939_db)
    spns = j1939["spns"]
    dtc_count_before, spn_count_before = len(dtc), len(spns)

    report: dict = {
        "tur": "T66",
        "dry_run": not args.apply,
        "db_md5_before": {"j1939": md5(j1939_db), "dtc": md5(dtc_db)},
        "db_sha256_before": {
            "j1939": fingerprints["j1939"]["sha256"],
            "dtc": fingerprints["dtc"]["sha256"],
        },
        "db_counts_before": {"dtc": dtc_count_before, "j1939_spns": spn_count_before},
        "inputs": [],
        "applied": [],
        "skipped": {"placeholder": 0, "no_evidence": 0, "unknown_key": 0,
                    "already_full": 0, "no_fillable": 0, "no_source_url": 0},
        "totals": {"dtc_filled": 0, "j1939_filled": 0, "j1939_bundles_appended": 0},
    }

    for inp in args.inputs:
        path = Path(inp)
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records") if isinstance(data, dict) else data
        if not records:
            report["inputs"].append({"path": inp, "records": 0})
            continue
        target = (data.get("target_db") if isinstance(data, dict) else None) or "auto"
        summary = {"path": inp, "records": len(records), "target_db": target,
                   "filled": 0, "new": 0}

        for rec in records:
            if not isinstance(rec, dict) or rec.get("placeholder") is True:
                report["skipped"]["placeholder"] += 1
                continue
            ev = rec.get("evidence")
            if not isinstance(ev, str) or len(ev.strip()) < MIN_EVIDENCE:
                report["skipped"]["no_evidence"] += 1
                continue

            tgt = rec.get("target_db") or target
            if tgt == "auto":
                tgt = "j1939" if rec.get("spn") is not None else "dtc"

            if tgt == "j1939":
                if merge_j1939(rec, spns, report, args.apply):
                    summary["filled"] += 1
                    report["totals"]["j1939_filled"] += 1
                    report["totals"]["j1939_bundles_appended"] += 1 if rec.get("procedures_full_append") else 0
            else:
                if merge_dtc(rec, dtc, report, args.apply):
                    summary["filled"] += 1
                    report["totals"]["dtc_filled"] += 1
        report["inputs"].append(summary)

    # Integrity gate: counts must not move.
    if len(dtc) != dtc_count_before or len(spns) != spn_count_before:
        raise SystemExit(
            f"RECORD COUNT CHANGED: dtc {dtc_count_before}->{len(dtc)}, "
            f"j1939 {spn_count_before}->{len(spns)} — aborting"
        )

    if args.apply:
        spns_meta = j1939.get("metadata", {})
        spns_meta["total_spns"] = len(spns)
        _commit_databases(dtc_db, j1939_db, dtc, j1939, fingerprints, report)
        report["db_md5_after"] = {"j1939": md5(j1939_db), "dtc": md5(dtc_db)}
        report["db_counts_after"] = {"dtc": len(dtc), "j1939_spns": len(spns)}

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "skipped": report["skipped"]},
                     ensure_ascii=False))
    return 0


def main(
    argv: list[str] | None = None,
    *,
    dtc_db: Path = DTC_DB,
    j1939_db: Path = J1939_DB,
    lock_path: Path = LOCK_PATH,
) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--report", default="output/merge_t66_report.json")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    # Fail fast: a missing required input must not produce a green exit.
    missing = [i for i in args.inputs if not Path(i).exists()]
    if missing:
        raise SystemExit(f"missing input(s): {', '.join(missing)} — aborting")

    if args.apply:
        # B-09: the exclusive lock is taken BEFORE the DBs are read and held
        # through the commit, so the whole read-modify-write cycle is
        # serialised against any other writer.
        with _file_lock(lock_path):
            return _run_merge(args, dtc_db, j1939_db)
    return _run_merge(args, dtc_db, j1939_db)


if __name__ == "__main__":
    raise SystemExit(main())
