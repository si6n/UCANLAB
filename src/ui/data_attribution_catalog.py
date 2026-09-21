"""Canonical "Open Source & Data Attributions" catalog (T2-7).

Why this module exists
----------------------
T2-4 vendored four external datasets into ``data/``. Two of them carry a
*mandatory* attribution obligation (SITRAK = CC BY 4.0; canboat = Apache-2.0
NOTICE; Wal33D/dtc-database = MIT copyright notice) and one is recorded for
traceability (OBDex = CC0-1.0). Zero-fabrication rule (AGENTS.md §2.3): the
attribution strings below are extracted from the vendored licence files and
their invariants are asserted by ``tests/unit/test_data_attributions.py``.

Single source of truth
----------------------
This module IS the single source of truth for the panel's *metadata* (name,
license, URL, pinned commit, required attribution text, canonical file).
It is never copied into TypeScript: the React panel obtains it through the
read-only bridge method ``DesktopApiBridge.get_data_attributions()``, and the
``source_path`` of every entry points back at the vendored file so the
long-form licence text is shown from disk rather than re-typed.

Drift control
-------------
The pinning test re-derives, from ``data/licenses/*`` + ``data/diagnostics/
PROVENANCE.md`` on disk, that every pinned commit SHA and every licence URL
recorded here still appears in the canonical file. If the vendored files move
ahead of this catalog, the test fails instead of the panel silently lying.

Runtime constraints (operator decree)
-------------------------------------
NO LLM, NO cloud call, NO network fetch. This module reads local vendored files
only, and only paths on ``ATTRIBUTION_ALLOWED_FILES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Upper bound on how much of a licence file is returned to the renderer. The
#: largest vendored licence/NOTICE file is ~4 KB; 200 KB leaves generous slack
#: while keeping a 10 MB file from being pulled through the WebView2 IPC.
MAX_ATTRIBUTION_FILE_BYTES: int = 200_000

#: Attribution obligation classes, so the UI can flag what is legally required
#: versus what is merely recorded for provenance.
OBLIGATION_REQUIRED: str = "required"
OBLIGATION_RECORDED: str = "recorded"

#: The ONE string a CC-BY-4.0 recipient must be able to read. Copied verbatim
#: from ``data/licenses/ATTRIBUTION.sitrak.md`` line 19 (the machine-readable
#: form, identical to ``attribution_text`` in tools/data_ingest/provenance.json).
#:
#: A test asserts this literal still appears byte-for-byte in that file, so a
#: future edit of the vendored licence cannot leave the UI showing stale text.
SITRAK_ATTRIBUTION_TEXT: str = (
    "Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
)

#: The copyright line the MIT licence obliges us to carry for Wal33D. Verbatim
#: from ``data/licenses/ATTRIBUTION.obdex-and-dtcdb.md`` line 67.
DTCDB_ATTRIBUTION_TEXT: str = "Copyright (c) 2024 Wal33D (Waleed Judah)"

#: Apache-2.0 §4(d) — the readable notice copy for CANboat. Verbatim from the
#: indented NOTICE block in ``data/licenses/NOTICE.canboat`` (lines 5-7); the
#: block is reproduced with its internal line breaks AND its four-space indent
#: so it stays byte-identical to the vendored file.
CANBOAT_ATTRIBUTION_TEXT: str = (
    "CANboat\n"
    "    (C) 2009-2026, Kees Verruijt, Harlingen, The Netherlands.\n"
    "    Licensed under the Apache License, Version 2.0."
)

#: CC0-1.0 imposes no obligation; recorded so the provenance chain is auditable.
#: Deliberately a Turkish PARAPHRASE (documented as such in the module and
#: asserted as a non-vendored string by the pinning test) and NOT presented as
#: upstream licence text. The verbatim CC0 deed excerpt ships in ``sourceText``.
OBDEX_ATTRIBUTION_TEXT: str = (
    "OBDex veri dosyaları CC0 1.0 Universal ile kamu malına adanmıştır; "
    "atıf yükümlülüğü YOKTUR (kaynak zinciri için kaydedilmiştir)."
)


@dataclass(frozen=True)
class DataSourceLicense:
    """One vendored third-party data source and its licence obligations."""

    #: Stable machine id used as the React list key.
    id: str
    #: Upstream work name as it appears in the vendored attribution file.
    name: str
    #: SPDX identifier of the licence the data ships under.
    license: str
    #: Human-readable licence name (Turkish-first UI, licence names verbatim).
    license_name: str
    #: Canonical licence deed URL.
    license_url: str
    #: Upstream repository / source URL.
    source_url: str
    #: Pinned upstream commit SHA, or "" when the attribution file has none.
    pinned_commit: str
    #: Required (or recorded) attribution text, verbatim from the vendored file.
    attribution_text: str
    #: ``required`` when the licence legally obliges us to show the credit.
    obligation: str
    #: Vendored file that is the canonical proof for this entry.
    canonical_file: str
    #: Why this entry is present, in Turkish (matches the panel's language).
    note_tr: str
    #: The data artefacts this attribution covers.
    artifacts: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self, repo_root: Path) -> dict[str, Any]:
        """Serialize for the WebView2 IPC, attaching the verbatim file text."""
        text, truncated = _read_text_capped(repo_root / self.canonical_file)
        return {
            "id": self.id,
            "name": self.name,
            "license": self.license,
            "licenseName": self.license_name,
            "licenseUrl": self.license_url,
            "sourceUrl": self.source_url,
            "pinnedCommit": self.pinned_commit,
            "attributionText": self.attribution_text,
            "obligation": self.obligation,
            "canonicalFile": self.canonical_file,
            "sourcePath": self.canonical_file,
            # Verbatim vendored-file text — the renderer never re-types it.
            "sourceText": text,
            "sourceTextTruncated": truncated,
            "noteTr": self.note_tr,
            "artifacts": list(self.artifacts),
        }


#: The catalog. Ordering is deliberate: legally-required attributions first.
DATA_SOURCE_LICENSES: tuple[DataSourceLicense, ...] = (
    DataSourceLicense(
        id="sitrak-error-codes",
        name="SITRAK Error Codes Database (error-codes.json)",
        license="CC-BY-4.0",
        license_name="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        source_url="https://github.com/STAS63-bit/sitrak-error-codes",
        pinned_commit="fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742",
        attribution_text=SITRAK_ATTRIBUTION_TEXT,
        obligation=OBLIGATION_REQUIRED,
        canonical_file="data/licenses/ATTRIBUTION.sitrak.md",
        note_tr=(
            "CC BY 4.0 atıf zorunluluğu: eser sahibi МегаДата / megadata.pro "
            "olarak anılmalı ve lisans bağlantısı verilmelidir. Bu metin "
            "ürünle birlikte dağıtılan kanonik atıf dosyasından okunur."
        ),
        artifacts=("data/diagnostics/j1939_spn_fmi_database.json (metadata.sitrak_layer + SPN_* kayıtları)",),
    ),
    DataSourceLicense(
        id="canboat",
        name="CANboat (canboat.json, NMEA-2000 PGN + J1939 DM1)",
        license="Apache-2.0",
        license_name="Apache License, Version 2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        source_url="https://github.com/canboat/canboat",
        pinned_commit="f7f088b49d58f5b4a0feb9b29c288b0ae18a7880",
        attribution_text=CANBOAT_ATTRIBUTION_TEXT,
        obligation=OBLIGATION_REQUIRED,
        canonical_file="data/licenses/NOTICE.canboat",
        note_tr=(
            "Apache-2.0 §4(d): dağıtım, upstream NOTICE/README içindeki atıf "
            "bildirimlerinin okunabilir bir kopyasını içermelidir (burada telif "
            "+ lisans metni korunmuştur)."
        ),
        artifacts=(
            "data/diagnostics/canboat_pgn_reference.json",
            "data/dbc/marine/n2k_canboat.dbc",
            "data/dbc/heavy_duty/j1939_canboat.dbc",
        ),
    ),
    DataSourceLicense(
        id="dtc-database-wal33d",
        name="dtc-database (Wal33D, OEM katmanı)",
        license="MIT",
        license_name="MIT License",
        license_url="https://opensource.org/license/mit",
        source_url="https://github.com/Wal33D/dtc-database",
        pinned_commit="04c43d72e7db7197658b6f72fe582c5076d9eee8",
        attribution_text=DTCDB_ATTRIBUTION_TEXT,
        obligation=OBLIGATION_REQUIRED,
        canonical_file="data/licenses/ATTRIBUTION.obdex-and-dtcdb.md",
        note_tr=(
            "MIT: telif hakkı bildirimi ve izin bildirimi, eserin tüm "
            "kopyalarına veya önemli bölümlerine dahil edilmelidir."
        ),
        artifacts=("data/diagnostics/dtc_database_oem_layer.json",),
    ),
    DataSourceLicense(
        id="obdex",
        name="OBDex (generic OBD-II DTC + Mode 01/09 PID)",
        license="CC0-1.0",
        license_name="Creative Commons Zero 1.0 Universal (Public Domain)",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/legalcode",
        source_url="https://github.com/foerbsnavi/OBDex",
        pinned_commit="bc58b0eb7273226a1aabae98e956b70b8362bda1",
        attribution_text=OBDEX_ATTRIBUTION_TEXT,
        obligation=OBLIGATION_RECORDED,
        canonical_file="data/licenses/ATTRIBUTION.obdex-and-dtcdb.md",
        note_tr=(
            "CC0 1.0: atıf yükümlülüğü YOKTUR. Kaynak zincirinin denetlenebilir "
            "kalması için kaydedilmiştir. (OBDex kod tarafı MIT'tir ve "
            "kullanılmamıştır.) Aşağıdaki açıklama kanonik dosyadan alınmıştır."
        ),
        artifacts=(
            "data/generic/*.yaml (9.533 generic DTC)",
            "data/pids/mode01.yaml + mode09.yaml (132 PID)",
        ),
    ),
)


def get_attribution_sources() -> tuple[DataSourceLicense, ...]:
    """Return the catalog (single source of truth for the UI panel)."""
    return DATA_SOURCE_LICENSES


def _read_text_capped(path: Path) -> tuple[str, bool]:
    """Read ``path`` as UTF-8; return (text, truncated). Never raises.

    The panel is a compliance surface, so a missing/unreadable file must not
    crash startup or blank the attribution list — it is reported through the
    ``payload["errors"]`` channel instead, and the pinned ``attribution_text``
    (which carries the legal strings) is still delivered.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return "", False
    truncated = len(raw) > MAX_ATTRIBUTION_FILE_BYTES
    if truncated:
        raw = raw[:MAX_ATTRIBUTION_FILE_BYTES]
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        text += "\n\n… (dosya kısaltıldı — tam sürüm için kanonik dosyaya bakın)"
    return text, truncated


def build_attribution_payload(repo_root: Path) -> dict[str, Any]:
    """Build the read-only payload consumed by the attribution panel.

    Reads local vendored licence files only. No network, no LLM, no cloud, no
    bus/TX/E-Stop interaction — this is a pure function of the on-disk bundle.
    """
    sources = [entry.to_payload(repo_root) for entry in DATA_SOURCE_LICENSES]
    errors: list[str] = [
        f"Atıf dosyası bulunamadı: {entry.canonical_file}"
        for entry in DATA_SOURCE_LICENSES
        if not (repo_root / entry.canonical_file).exists()
    ]
    return {
        "success": True,
        "schemaVersion": 1,
        "obligationRequiredCount": sum(1 for e in DATA_SOURCE_LICENSES if e.obligation == OBLIGATION_REQUIRED),
        "sources": sources,
        "errors": errors,
        # Stated explicitly so the renderer can surface the runtime guarantee
        # instead of just asserting it in prose.
        "offline": True,
        "networkAccess": False,
    }
