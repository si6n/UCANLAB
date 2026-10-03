"""Build data/diagnostics/dtc_oem_meanings.json: per-manufacturer meaning of each OEM code.

Why: the Wal33D OEM layer keeps ONE description per code ("last write wins"),
but 877 manufacturer codes mean different things per make (P1101 is a mass air
flow fault on a Ford and an oxygen sensor fault in the surviving Volkswagen
text). The intake scan (docs/audit/intake_source_scan_2026-10-02.md §4b, §4o)
staged the per-make upstream text as ``oem_divergence`` records.

Inputs, all already in the repository and pinned to Wal33D commit 04c43d72:

* ``data/intake/oem/wal33d-divergence-*.json``: rows whose upstream per-make
  text differs from the layer text, with the source list's sha256;
* ``data/diagnostics/dtc_database_oem_layer.json``: the surviving text and the
  makes that list the code (``oem_index``). A make that lists the code and has
  no divergence row carries exactly the layer text (intake §4o: every layer
  text equals one source row; the divergence rows are all the others).

Generic lists (other/p/c/u/b codes) are not a make and are left out. Text is
copied verbatim; nothing is translated, merged or inferred. Only codes whose
makes disagree are written. Build-time tool; the copilot only reads the output.

    python scripts/build_dtc_oem_meanings.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "data" / "intake" / "oem"
LAYER = ROOT / "data" / "diagnostics" / "dtc_database_oem_layer.json"
OUT = ROOT / "data" / "diagnostics" / "dtc_oem_meanings.json"
GENERIC_LISTS = frozenset({"OTHER", "P", "C", "U", "B"})

# Lookup make (copilot vocabulary, folded) -> Wal33D list names, most specific first.
# GM divisions also fall back to the shared GM list.
MAKE_ALIASES: dict[str, list[str]] = {
    "ford": ["FORD"], "lincoln": ["LINCOLN"], "mercury": ["MERCURY"],
    "chevrolet": ["CHEVY", "GM"], "chevy": ["CHEVY", "GM"], "gmc": ["GMC", "GM"], "cadillac": ["CADILLAC", "GM"],
    "buick": ["BUICK", "GM"], "saturn": ["SATURN", "GM"], "oldsmobile": ["OLDSMOBILE", "GM"],
    "pontiac": ["PONTIAC", "GM"], "geo": ["GEO", "GM"], "gm": ["GM"],
    "toyota": ["TOYOTA"], "lexus": ["LEXUS"], "honda": ["HONDA"], "acura": ["ACURA"],
    "nissan": ["NISSAN"], "infiniti": ["INFINITI"], "subaru": ["SUBARU"], "mazda": ["MAZDA"],
    "mitsubishi": ["MITSUBISHI"], "suzuki": ["SUZUKI"], "volkswagen": ["VOLKSWAGEN"], "vw": ["VOLKSWAGEN"],
    "audi": ["AUDI"], "bmw": ["BMW"], "mercedes": ["MERCEDES"], "kia": ["KIA"], "jaguar": ["JAGUAR"],
    "dodge": ["DODGE"], "jeep": ["JEEP"], "chrysler": ["CHRYSLER"], "plymouth": ["PLYMOUTH"],
}

# Makes with no Wal33D list. A label naming one of these next to a listed make
# ("Hyundai-Kia") is a brand group and resolves to no meaning, not to Kia's.
OTHER_MAKE_WORDS: list[str] = ["hyundai", "tesla", "volvo", "renault", "dacia", "opel", "vauxhall", "seat", "skoda",
                               "porsche", "fiat", "peugeot", "citroen", "stellantis", "mini", "smart", "landrover"]


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("/", " ").split())


def build() -> dict:
    layer = json.loads(LAYER.read_text(encoding="utf-8"))
    layer_text = {c: str((e.get("description") or {}).get("en") or "") for c, e in layer["codes"].items()}
    per_code: dict[str, dict[str, str]] = {}
    sources = []
    for path in sorted(INTAKE.glob("wal33d-divergence-*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        payload = rec["payload"]
        make = str(payload["make"]).upper()
        sources.append({"intake_id": rec["intake_id"], "make": make, "list": rec["source"]["path"],
                        "sha256": rec["source"]["snapshot"]["sha256"]})
        if make in GENERIC_LISTS:
            continue
        for row in payload["divergences"]:
            per_code.setdefault(row["code"], {})[make] = str(row["source_description_en"]).strip()
    codes: dict[str, dict[str, str]] = {}
    for code, by_make in sorted(per_code.items()):
        meanings = dict(by_make)
        for make in layer.get("oem_index", {}).get(code) or []:
            make = str(make).upper()
            if make not in GENERIC_LISTS and make not in meanings and layer_text.get(code):
                meanings[make] = layer_text[code]
        if len({_norm(t) for t in meanings.values() if t}) >= 2:
            codes[code] = dict(sorted(meanings.items()))
    return {
        "metadata": {
            "title": "Per-manufacturer meaning of OEM fault codes (Wal33D dtc-database source lists)",
            "license": "MIT",
            "attribution": "data/licenses/ATTRIBUTION.obdex-and-dtcdb.md",
            "upstream": "https://github.com/Wal33D/dtc-database/tree/04c43d72e7db7197658b6f72fe582c5076d9eee8",
            "generator": "scripts/build_dtc_oem_meanings.py",
            "evidence": "docs/audit/intake_source_scan_2026-10-02.md §4b, §4o",
            "rule": "Verbatim upstream text per make. A make listing the code without a divergence row carries the "
                    "layer text. Generic lists are not a make. Only codes whose makes disagree are kept.",
            "total_codes": len(codes),
            "sources": sources,
        },
        "make_aliases": MAKE_ALIASES,
        "other_make_words": OTHER_MAKE_WORDS,
        "codes": codes,
    }


def main() -> None:
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
