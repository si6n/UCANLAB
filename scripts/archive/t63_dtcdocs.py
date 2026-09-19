"""T63 — dtcdocs.com parser."""
from __future__ import annotations

import json
import re
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_harvest as H

SEC = re.compile(r"(?m)^\s*([A-Z][A-Za-z& /\-–]{3,70})\s*$")


def section(txt: str, start: str, stops: list[str]) -> str:
    i = txt.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = len(txt)
    for s in stops:
        k = txt.find(s, i)
        if 0 <= k < j:
            j = k
    return txt[i:j].strip()


def bullets(block: str) -> list[str]:
    if not block:
        return []
    out = []
    for line in block.split("\n"):
        ln = H.norm(line)
        if ln and 3 < len(ln) < 400:
            out.append(ln)
    return out


def parse(url: str, txt: str) -> dict | None:
    m = re.search(r"/code/(?:([a-z0-9\-]+?)-)?(?:spn-(\d+)|mid-(\d+)-pid-(\d+)|mid-(\d+)-sid-(\d+))-fmi-(\d+)/", url)
    spn = fmi = code = None
    brand_engine = ""
    if m:
        brand_engine = m.group(1) or ""
        spn = int(m.group(2)) if m.group(2) else None
        fmi = int(m.group(7))
    if "Meaning, Causes & Fix" not in txt:
        return None

    h1m = re.search(r"([A-Za-z0-9 \-]+ (?:SPN \d+ FMI \d+|MID \d+ (?:PID|SID) \d+)): ([^\n]+)", txt)
    title = H.norm(h1m.group(0)) if h1m else ""

    sev = ""
    sm = re.search(r"Severity:?\s*([^\n·]{2,60})", txt)
    if sm:
        sev = H.norm(sm.group(1))
    for k in ("STOP ENGINE", "CRITICAL", "STOP DRIVING", "HIGH", "MEDIUM", "LOW", "WARNING"):
        if k in txt[:3000]:
            sev = sev or k
            break

    causes = bullets(section(txt, "Possible Causes", ["Top Causes Ranked", "In-Depth Diagnostic", "Repair & Cost"]))
    ranked = bullets(section(txt, "Top Causes Ranked by Frequency", ["In-Depth Diagnostic", "Repair & Cost", "Frequently Asked"]))
    steps = bullets(
        section(
            txt,
            "In-Depth Diagnostic Procedure",
            ["Repair & Cost Estimate", "Frequently Asked Questions"],
        )
    )
    steps = [s for s in steps if not s.startswith("Follow these diagnostic steps")]

    faq = section(txt, "Frequently Asked Questions", ["Related Codes", "Related", "About DTC Hub", "Footer"])
    # strip trailing navigation
    noise = ["Brands", "Symptoms", "About", "DTC Hub"]
    faq_lines = bullets(faq)
    keep = []
    for ln in faq_lines:
        if ln in noise:
            continue
        keep.append(ln)
    faq_txt = "\n".join(keep)

    related = re.findall(r"/code/([a-z0-9\-]+?)-spn-(\d+)-fmi-(\d+)/", txt)
    rel = sorted({f"SPN_{a}_FMI_{b}" for _, a, b in related})

    proc_parts = []
    if causes:
        proc_parts.append("Possible Causes:\n- " + "\n- ".join(causes))
    if ranked:
        proc_parts.append("Top Causes Ranked by Frequency:\n- " + "\n- ".join(ranked))
    if steps:
        proc_parts.append("Diagnostic Procedure:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)))
    if faq_txt:
        proc_parts.append("Technical Reference / FAQ:\n" + faq_txt)
    procedures_full = "\n\n".join(proc_parts).strip()

    if not (causes or steps):
        return None

    key = f"SPN_{spn}_FMI_{fmi}" if spn is not None else url
    return {
        "source": "dtcdocs.com",
        "url": url,
        "key": key,
        "spn": spn,
        "fmi": fmi,
        "code": code,
        "brand_engine": brand_engine,
        "title": title,
        "symptoms": [],  # dtcdocs code pages carry no distinct symptom list (ranked causes only)
        "causes": ranked or causes,
        "causes_plain": causes,
        "steps": steps,
        "procedures_full": procedures_full,
        "severity": sev,
        "related_codes": rel,
        "evidence": H.norm((title or "") + " || " + (causes[0] if causes else "") + " || " + (steps[0] if steps else ""))[:400],
        "placeholder": False,
    }


def main():
    urls = H.sitemap_urls("https://dtcdocs.com/sitemap.xml")
    code_urls = [u for u in urls if "/code/" in u]
    sym_urls = [u for u in urls if "symptom" in u]
    recs = []
    ph = 0
    fetched = 0
    for i, u in enumerate(code_urls):
        st, t = H.fetch(u)
        fetched += 1
        if st != 200:
            ph += 1
            continue
        txt = H.strip_tags(t)
        try:
            r = parse(u, txt)
        except Exception as e:
            r = None
        if r:
            recs.append(r)
        else:
            ph += 1
        if i % 40 == 0:
            time.sleep(0.4)
        if i % 100 == 0:
            print(f"  dtcdocs {i}/{len(code_urls)} recs={len(recs)}", flush=True)
    # symptom index pages
    sym_recs = []
    for u in sym_urls[:25]:
        st, t = H.fetch(u)
        if st != 200:
            continue
        txt = H.strip_tags(t)
        if len(txt) < 1500:
            continue
        sym_recs.append(
            {
                "source": "dtcdocs.com",
                "url": u,
                "key": u.rstrip("/").split("/")[-1],
                "spn": None,
                "fmi": None,
                "code": None,
                "symptoms": [],
                "causes": [],
                "steps": [],
                "procedures_full": txt[:12000],
                "severity": "",
                "evidence": H.norm(txt[:300]),
                "placeholder": False,
                "kind": "symptom_index",
            }
        )
    for r in sym_recs:
        fetched += 1
    payload = H.meta(
        "dtcdocs.com",
        "/code/{brand}-{engine}-spn-{n}-fmi-{m}/ + /symptoms/",
        len(urls),
        fetched,
        len(recs) + len(sym_recs),
        ph,
        "procedures_full, causes, symptoms, severity",
        recs + sym_recs,
    )
    p = H.write_scan("dtcdocs.com", payload)
    print("WROTE", p, "recs", len(recs), "sym", len(sym_recs), "ph", ph)


if __name__ == "__main__":
    main()
