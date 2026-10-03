# -*- coding: utf-8 -*-
"""Link liveness for the URLs the knowledge base cites as sources.

A provenance chain that points at a dead URL cannot be walked today, and nothing
in the repository checks that. This is a **maintenance signal**, not a data
finding, so it emits a report instead of intake records: the fix (a new snapshot,
a different citation) is a provenance decision, not a merge.

Network access lives here, at build time, like the upstream tarball scan
(``scripts/intake_scan_sources.py``). It is deliberately bounded: a long timeout
on hundreds of dead hosts is not useful, so the scan probes the most-cited
URLs first and stops at ``--limit``.

    python scripts/intake_check_sources.py --limit 60
    python scripts/intake_check_sources.py --limit 60 --report docs/audit/source_links.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
URL_RE = re.compile(r"https?://[^\s\"'`,;)\]]+")
SOURCE_FIELDS = ("_source_ref", "_source_ref_sitrak", "source", "evidence_url", "url", "_source")
PROVENANCE_DOCS = ("data/PROVENANCE.md", "data/diagnostics/PROVENANCE.md")
USER_AGENT = "ucanlab-provenance-check/1.0"


def cited_urls(root: Path = ROOT) -> Counter:
    # Distinct URLs cited by the knowledge base. The DTC database contributes
    # ~9.7k of them (one evidence_url per code), almost all single-occurrence,
    # which is why --sample matters: the long tail is the chain that rots.
    """Every http(s) URL the knowledge base cites, with an occurrence count."""
    counts: Counter = Counter()

    def add(text: str) -> None:
        for url in URL_RE.findall(text):
            counts[url.rstrip(".,;)]\"")] += 1

    for relative in PROVENANCE_DOCS:
        path = root / relative
        if path.is_file():
            add(path.read_text(encoding="utf-8"))
    for path in sorted((root / "data" / "diagnostics").rglob("*.json")):
        if "quarantine" in path.parts:
            continue
        try:
            blob: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [blob]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for field in SOURCE_FIELDS:
                    value = node.get(field)
                    if isinstance(value, str) and value.strip().startswith("http"):
                        add(value)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return counts


def probe(url: str, timeout: int = 8) -> tuple[str, str]:
    """Return ``(status, detail)`` for one URL. Never raises, never hangs."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return "live", str(response.status)
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 405, 429, 501}:
            # blocked, rate limited or HEAD-less: alive as far as we can tell
            return "unverified", f"HTTP {exc.code}"
        if 300 <= exc.code < 400:
            # A redirect is NOT a dead link. urllib only follows 301/302/303/307
            # (308 support landed in 3.11), so a 308 here is our tooling's limit,
            # not the source's state — classifying it as dead would be a false
            # positive, which is worse than saying "unknown".
            return "redirect", f"HTTP {exc.code}"
        return "dead", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return "dead", str(exc.reason)[:48]
    except Exception as exc:  # noqa: BLE001 - a scan must report, never crash
        return "dead", type(exc).__name__


def run(root: Path = ROOT, limit: int = 60, timeout: int = 8, sample: int = 0,
        seed: int = 20261003) -> dict[str, Any]:
    """Probe the most-cited ``limit`` URLs plus ``sample`` random ones from the tail.

    The DTC database cites ~9.7k distinct URLs, almost all with a single
    occurrence, so the top-N alone would measure nothing about the long tail that
    actually makes the provenance chain un-walkable. The sample is deterministic
    (fixed seed) so the rate is reproducible.
    """
    import random

    counts = cited_urls(root)
    ranked = [url for url, _ in counts.most_common(limit)]
    if sample:
        tail = [url for url in counts if url not in set(ranked)]
        ranked += random.Random(seed).sample(tail, min(sample, len(tail)))
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = dict(zip(ranked, pool.map(lambda u: probe(u, timeout), ranked), strict=True))
    tally = Counter(status for status, _ in results.values())
    return {
        "scanned": len(ranked),
        "total_cited": len(counts),
        "tally": dict(tally),
        "dead": sorted(((url, counts[url], detail) for url, (status, detail) in results.items()
                        if status == "dead"), key=lambda row: -row[1]),
        "unverified": sorted(((url, counts[url], detail) for url, (status, detail) in results.items()
                              if status in {"unverified", "redirect"}), key=lambda row: -row[1]),
        "seconds": round(time.perf_counter() - started, 1),
    }


def render(result: dict[str, Any]) -> str:
    lines = [
        "# Kaynak bağlantı canlılığı (link liveness)", "",
        f"- Taranan: **{result['scanned']}** URL (toplam alıntılanan: {result['total_cited']})",
        f"- Sonuç: `{result['tally']}` · süre {result['seconds']} s",
        "",
        "## Ölü bağlantılar", "",
        "| Durum | Alıntı | URL |", "|---|---|---|",
    ]
    for url, count, detail in result["dead"]:
        lines.append(f"| {detail} | {count} | `{url}` |")
    lines += ["", "## Doğrulanamayan (403/405/429 ve yönlendirme — ölü demek değil)", "",
              "| Durum | Alıntı | URL |", "|---|---|---|"]
    for url, count, detail in result["unverified"][:25]:
        lines.append(f"| {detail} | {count} | `{url}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--limit", type=int, default=60, help="how many of the most-cited URLs to probe")
    ap.add_argument("--timeout", type=int, default=8)
    ap.add_argument("--sample", type=int, default=0,
                    help="additionally probe N random URLs from the long tail (deterministic seed)")
    ap.add_argument("--report", help="write a markdown report to this path")
    ap.add_argument("--json", dest="json_out", help="write the result as JSON")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    result = run(root, limit=args.limit, timeout=args.timeout, sample=args.sample)
    print(f"[*] scanned={result['scanned']} of {result['total_cited']} cited URLs "
          f"tally={result['tally']} ({result['seconds']}s)")
    for url, count, detail in result["dead"][:15]:
        print(f"  [dead] {detail:32s} x{count:5d} {url}")
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(result), encoding="utf-8")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
