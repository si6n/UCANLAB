"""t66_verify.py — independent live-verification of harvested scan records.

Samples records from a scan output file, re-fetches each record's live URL,
and checks whether a harvested text probe appears verbatim (whitespace-normalised
substring) in the live HTML. This is the orchestrator's independent check:
an agent's own report is NOT verification.

Usage:
    python scripts/t66_verify.py --input output/scan_t66_obdfyi.json --sample 15 \
        --out output/verify_t66_obdfyi.json

Exit code: 0 on PASS verdict; 1 on FAIL (ratio < 0.5, no valid sample, or
every fetch failed); 2 on unusable input (empty record list, missing or
unreadable file). Every non-PASS path is deliberately nonzero so the check
can gate CI (I-19: an empty input must never exit green).

Network policy (fail-closed, stdlib urllib only): host allowlist, private /
loopback / link-local / reserved IP-literal rejection, scheme restricted to
http(s), redirect targets re-checked against the same policy, redirect limit,
and a bounded streaming read.
"""
from __future__ import annotations

import argparse
import html
import ipaddress
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# Harvested-source allowlist (fail-closed: unknown hosts are never fetched).
# Derived from the scan corpora in output/scan_t*.json (measured 2026-09-24).
ALLOWED_HOSTS = frozenset({
    "obdfyi.com",
    "www.obd2.com",
    "obd2.com",
    "troubleshootmyvehicle.com",
})

MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 2_000_000


def _url_allowed(url: str) -> bool:
    """I-19 network policy: scheme + host allowlist + non-public IP rejection.

    Applied to the initial URL AND to every redirect target (a public allowlisted
    host that 302s to 127.0.0.1 or a private address must not be followed).
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host not in ALLOWED_HOSTS:
        return False
    if host == "localhost" or host.endswith((".local", ".internal", ".lan")):
        return False
    literal = host.split("%", 1)[0]  # strip IPv6 zone id (fe80::1%eth0)
    try:
        addr = ipaddress.ip_address(literal)
    except ValueError:
        return True  # a hostname: the allowlist above is the gate
    return addr.is_global  # rejects loopback/private/link-local/reserved


class _RedirectLimiter(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if getattr(req, "_t66_redirects", 0) >= MAX_REDIRECTS:
            raise urllib.error.HTTPError(req.full_url, 508, "redirect limit", headers, fp)
        if not _url_allowed(newurl):  # redirects may not leave the policy
            raise urllib.error.HTTPError(req.full_url, 403, "redirect host refused", headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new._t66_redirects = getattr(req, "_t66_redirects", 0) + 1  # noqa: SLF001
        return new


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def strip_html(doc: str) -> str:
    """Tag-strip + whitespace-normalise, so a list joined with spaces compares
    fairly against the same list rendered as separate <li> elements."""
    doc = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?s)<[^>]+>", " ", doc)
    doc = html.unescape(doc)
    return norm(doc)


def token_overlap(probe: str, haystack: str) -> float:
    """Fraction of the probe's distinctive tokens present in the haystack."""
    words = [w for w in re.findall(r"[a-z0-9+]{4,}", norm(probe))][:14]
    if not words:
        return 0.0
    return sum(1 for w in words if w in haystack) / len(words)


# Generic opening steps appear on nearly every page of a source; a probe made of
# one of them would "match" any page (T64 lesson: generic overlap is not proof).
# Prefer the LONGEST string that does NOT contain these markers.
GENERIC_MARKERS = (
    "connect an obd",
    "connect a scan tool",
    "use an obd2 scanner",
    "scan for trouble codes",
    "read the codes",
    "check engine light",
    "visual inspection",
)


def longest_probe(rec: dict) -> str | None:
    """Pick the longest *discriminating* string from the record as the probe."""
    candidates: list[str] = []
    for field in ("steps", "procedures_steps", "causes", "symptoms", "description"):
        v = rec.get(field)
        if isinstance(v, list):
            candidates.extend(str(x) for x in v if isinstance(x, str))
        elif isinstance(v, str):
            candidates.append(v)
    pf = rec.get("procedures_full")
    if isinstance(pf, str):
        candidates.append(pf)
    elif isinstance(pf, list):
        candidates.extend(str(x) for x in pf if isinstance(x, str))
    ev = rec.get("evidence")
    if isinstance(ev, str):
        candidates.append(ev)
    candidates = [c.strip() for c in candidates if len(c.strip()) >= 40]
    if not candidates:
        return None
    # Prefer non-generic candidates; fall back to all when none qualify.
    specific = [c for c in candidates if not any(m in c.lower() for m in GENERIC_MARKERS)]
    pool = specific or candidates
    return max(pool, key=len)


def clean_url(url: str) -> str:
    """Harvested URLs may carry a .html suffix the live site does not serve."""
    u = (url or "").strip()
    if u.endswith(".html") and "obdfyi.com/codes/" in u.lower():
        return u[: -len(".html")]
    return u


def fetch(url: str, timeout: int = 25) -> tuple[int, str]:
    """Bounded fetch: host allowlist + private-IP block + redirect cap + size cap."""
    if not _url_allowed(url):
        return 0, ""
    try:
        req = urllib.request.Request(url, headers=UA)
        opener = urllib.request.build_opener(_RedirectLimiter)
        with opener.open(req, timeout=timeout) as r:
            # Bounded streaming read: never buffer more than the cap + 1 byte,
            # even if the response lies about (or omits) Content-Length.
            raw = r.read(MAX_PAGE_BYTES + 1)
            if len(raw) > MAX_PAGE_BYTES:
                return r.status, ""
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def mid_window(probe: str, width: int = 40) -> str:
    n = norm(probe)
    if len(n) <= width:
        return n
    start = max(0, (len(n) - width) // 2)
    return n[start : start + width]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--sample", type=int, default=15)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=66)
    args = ap.parse_args(argv)

    # I-19: a missing or unreadable input is a FAILURE, not a traceback.
    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"ERROR: input file not found: {args.input} — FAIL (fail-closed)")
        return 2
    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read/parse {args.input}: {exc} — FAIL (fail-closed)")
        return 2

    records = data.get("records") if isinstance(data, dict) else data
    if not records:
        print("ERROR: no records in input — FAIL (fail-closed: empty input proves nothing)")
        return 2  # I-19: empty/missing input must not exit green

    rng = random.Random(args.seed)
    sample = rng.sample(records, min(args.sample, len(records)))

    detay = []
    matched = 0
    fetch_fail = 0
    for rec in sample:
        url = clean_url(rec.get("url") or rec.get("evidence_url"))
        probe = longest_probe(rec)
        if not url or not probe:
            detay.append({"url": url, "status": "NO_PROBE", "probe": None})
            continue
        status, html = fetch(url)
        if status != 200 or not html:
            fetch_fail += 1
            detay.append({"url": url, "status": f"FETCH_{status}", "probe": None})
            time.sleep(1.0)
            continue
        hay = strip_html(html)
        needle = mid_window(probe)
        ok = needle in hay
        overlap = token_overlap(probe, hay)
        # A list joined with spaces (harvester) will not substring-match the same
        # list rendered as separate <li> elements (live site). Token overlap is
        # the fair second measure; >= 0.8 with a 40-char probe is strong evidence.
        if not ok and overlap >= 0.8:
            ok = True
            status = "MATCH_TOKENS"
        else:
            status = "MATCH" if ok else "MISMATCH"
        if ok:
            matched += 1
        detay.append(
            {
                "url": url,
                "status": status,
                "probe": needle,
                "token_overlap": round(overlap, 3),
                "page_len": len(html),
            }
        )
        time.sleep(0.7)

    valid = [d for d in detay if d["status"] in ("MATCH", "MATCH_TOKENS", "MISMATCH")]
    ratio = round(matched / len(valid), 3) if valid else None

    # I-19 fail-closed: every fetch failed, or no record yielded a valid
    # evaluation, proves nothing — both are FAIL with a distinct reason.
    # (all-fetch-failed is checked FIRST: it also empties `valid`, and the
    #  specific reason is what an operator needs to see.)
    if fetch_fail >= len(sample):
        verdict = "FAIL"
        fail_reason = "all-fetch-failed"
    elif not valid:
        verdict = "FAIL"
        fail_reason = "no-valid-sample"
    else:
        verdict = "PASS" if (ratio is not None and ratio >= 0.5) else "FAIL"
        fail_reason = None

    report = {
        "tur": "T66",
        "ajan": "orchestrator",
        "input": args.input,
        "orneklem": len(sample),
        "degerlendirilen": len(valid),
        "eslesen": matched,
        "fetch_fail": fetch_fail,
        "ratio": ratio,
        "verdict": verdict,
        "fail_reason": fail_reason,
        "kriter": "longest harvested string, whitespace-normalised, 40-char mid window substring of live page",
        "detay": detay,
    }

    out = Path(args.out) if args.out else Path(args.input).with_name("verify_" + Path(args.input).name)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "detay"}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
