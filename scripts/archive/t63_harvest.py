"""T63 telemetry harvester — free/urllib. Writes output/scan_t63_<domain>_2026-09-19.json.

Producer only. NEVER writes to data/diagnostics/*.json.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CACHE = os.path.join(OUT, "_t63_cache")
os.makedirs(CACHE, exist_ok=True)


def fetch(url: str, timeout: int = 25, use_cache: bool = True) -> tuple[int, str]:
    key = re.sub(r"[^A-Za-z0-9]+", "_", url)[:150]
    cp = os.path.join(CACHE, key + ".html")
    if use_cache and os.path.exists(cp):
        with open(cp, "r", encoding="utf-8", errors="replace") as fh:
            return 200, fh.read()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""
    ctype = ""
    txt = raw.decode("utf-8", "replace")
    if status == 200:
        with open(cp, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(txt)
    return status, txt


def strip_tags(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?is)<br\s*/?>", "\n", doc)
    doc = re.sub(r"(?is)</(p|div|li|tr|h[1-6]|section)>", "\n", doc)
    doc = re.sub(r"(?s)<[^>]+>", " ", doc)
    doc = html.unescape(doc)
    doc = doc.replace("\xa0", " ")
    doc = re.sub(r"[ \t]+", " ", doc)
    doc = re.sub(r"\n\s*\n+", "\n", doc)
    return doc.strip()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def meta(source, pattern, found, fetched, real, ph, target, records):
    return {
        "_meta": {
            "source": source,
            "url_pattern": pattern,
            "sitemap_pages_found": found,
            "pages_fetched": fetched,
            "pages_real": real,
            "pages_placeholder": ph,
            "credits_used": 0,
            "ajan": "telemetry",
            "tur": "T63",
            "tarih": "2026-09-19",
            "target_field": target,
        },
        "records": records,
    }


def write_scan(domain: str, payload: dict):
    p = os.path.join(OUT, f"scan_t63_{domain}_2026-09-19.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return p


def sitemap_urls(sm_url: str, limit: int = 6000) -> list[str]:
    st, txt = fetch(sm_url)
    if st != 200:
        return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", txt)
    subs = [u for u in locs if u.endswith(".xml") or ".xml" in u.split("?")[0]]
    if subs and len(locs) < 5:
        out: list[str] = []
        for s in subs[:30]:
            out.extend(sitemap_urls(s, limit))
            if len(out) >= limit:
                break
        return out
    return locs[:limit]
