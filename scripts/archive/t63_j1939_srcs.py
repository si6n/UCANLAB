"""T63 — detroitdieselengines.info + dieselenginespec.com + j1939hub.com parsers."""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_harvest as H


def sec(txt, start, stops):
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


def lines(block, lo=4, hi=600):
    out = []
    for ln in block.split("\n"):
        x = H.norm(ln)
        if lo < len(x) < hi:
            out.append(x)
    return out


# ---------------- Detroit Diesel ----------------
def dd_parse(url, txt):
    m = re.search(r"/([a-z0-9\-]+?)/(?:[a-z0-9\-]*?)spn-?(\d+)[\-/]fmi-?(\d+)-troubleshooting", url, re.I)
    if not m:
        m = re.search(r"/([a-z0-9\-]+?)/(?:[a-z0-9\-]*?)-(\d+)-fmi-(\d+)-troubleshooting", url, re.I)
    if not m:
        return None
    engine, spn, fmi = m.group(1), int(m.group(2)), int(m.group(3))
    if len(txt) < 700:
        return None

    body = txt
    steps = []

    # Detroit pages are a flat instruction list beginning after "Check as follows:" /
    # "Repair the ..." etc. Capture from the SPN x/FMI y header to "Related Posts".
    hm = re.search(r"SPN\s*%d\s*/\s*FMI\s*%d" % (spn, fmi), body)
    start = hm.end() if hm else 0
    end = body.find("Related Posts", start)
    if end < 0:
        end = body.find("Posted in", start)
    if end < 0:
        end = min(len(body), start + 9000)
    core = body[start:end].strip()

    # split instruction sentences; keep multi-line structure too
    for ln in core.split("\n"):
        x = H.norm(ln)
        if 12 < len(x) < 700 and not x.startswith("Leave a Reply"):
            steps.append(x)
    # also catch run-together instructions (Detroit uses U+202A separators)
    flat = core.replace("\u202a", "\n").replace("\u202c", "\n")
    if len(steps) < 2:
        for ln in flat.split("\n"):
            x = H.norm(ln)
            if 15 < len(x) < 700:
                steps.append(x)
    # de-dup preserving order
    seen = set()
    steps = [s for s in steps if not (s in seen or seen.add(s))]
    # drop boilerplate
    steps = [s for s in steps if "Related Posts" not in s and "Wisteria Theme" not in s]
    if not steps:
        return None
    causes = [s for s in steps if re.search(r"(?i)\b(cause|because|due to|indicates|typically)\b", s)]
    symptoms = [s for s in steps if re.search(r"(?i)\b(symptom|no start|rough|stall|smoke|noise|low power|derate)\b", s)]
    pf = []
    if symptoms:
        pf.append("Symptoms:\n- " + "\n- ".join(symptoms))
    if causes:
        pf.append("Possible Causes:\n- " + "\n- ".join(causes))
    if steps:
        pf.append("Diagnostic Steps:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)))
    return {
        "source": "detroitdieselengines.info",
        "url": url,
        "key": f"SPN_{spn}_FMI_{fmi}",
        "spn": spn,
        "fmi": fmi,
        "code": None,
        "engine": engine,
        "symptoms": symptoms,
        "causes": causes,
        "steps": steps,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": "",
        "evidence": H.norm(txt[:600]),
        "placeholder": False,
    }


# ---------------- dieselenginespec ----------------
def des_parse(url, txt):
    m = re.search(r"/spn-(\d+)-fmi-(\d+)", url)
    spn = int(m.group(1)) if m else None
    fmi = int(m.group(2)) if m else None
    if len(txt) < 900:
        return None
    causes = lines(sec(txt, "Possible Causes", ["Symptoms", "Diagnostic", "Repair", "How to", "FAQ"]))
    symptoms = lines(sec(txt, "Symptoms", ["Possible Causes", "Diagnostic", "Repair", "FAQ", "How to"]))
    steps = lines(sec(txt, "Diagnostic", ["Symptoms", "Possible Causes", "Repair", "FAQ", "How to"]))
    pinout = lines(sec(txt, "Pinout", ["Symptoms", "Possible Causes", "Diagnostic", "FAQ"]))
    if not (causes or symptoms or steps):
        return None
    pf = []
    for nm, b in (("Symptoms", symptoms), ("Possible Causes", causes), ("Diagnostic Steps", steps), ("Pinout / Wiring", pinout)):
        if b:
            pf.append(nm + ":\n- " + "\n- ".join(b))
    key = f"SPN_{spn}_FMI_{fmi}" if spn is not None else url
    return {
        "source": "dieselenginespec.com",
        "url": url,
        "key": key,
        "spn": spn,
        "fmi": fmi,
        "code": None,
        "symptoms": symptoms,
        "causes": causes,
        "steps": steps,
        "pinout": pinout,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": "",
        "evidence": H.norm(txt[:600]),
        "placeholder": False,
    }


# ---------------- j1939hub ----------------
def hub_parse(url, txt):
    m = re.search(r"/spn-(\d+)-fmi-(\d+)", url) or re.search(r"/spn-(\d+)", url)
    spn = int(m.group(1)) if m else None
    fmi = int(m.group(2)) if (m and m.lastindex and m.lastindex >= 2) else None
    if len(txt) < 900:
        return None
    causes = lines(sec(txt, "Causes", ["Symptoms", "Diagnostic", "Steps", "How to", "Fix", "FAQ"]))
    symptoms = lines(sec(txt, "Symptoms", ["Causes", "Diagnostic", "Steps", "Fix", "FAQ"]))
    steps = lines(sec(txt, "Diagnostic", ["Symptoms", "Causes", "FAQ", "Related"]))
    if not (causes or symptoms or steps):
        return None
    pf = []
    for nm, b in (("Symptoms", symptoms), ("Causes", causes), ("Diagnostic Steps", steps)):
        if b:
            pf.append(nm + ":\n- " + "\n- ".join(b))
    key = f"SPN_{spn}_FMI_{fmi}" if fmi is not None else (f"SPN_{spn}" if spn is not None else url)
    return {
        "source": "j1939hub.com",
        "url": url,
        "key": key,
        "spn": spn,
        "fmi": fmi,
        "code": None,
        "symptoms": symptoms,
        "causes": causes,
        "steps": steps,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": "",
        "evidence": H.norm(txt[:600]),
        "placeholder": False,
    }


def run(domain, urls, parser, pattern):
    recs, ph, fetched = [], 0, 0
    for i, u in enumerate(urls):
        st, t = H.fetch(u)
        fetched += 1
        if st != 200:
            ph += 1
            continue
        try:
            r = parser(u, H.strip_tags(t))
        except Exception:
            r = None
        if r and len(r["procedures_full"]) > 40:
            recs.append(r)
        else:
            ph += 1
        if i and i % 30 == 0:
            time.sleep(0.3)
    payload = H.meta(domain, pattern, len(urls), fetched, len(recs), ph, "procedures_full, symptoms, diagnostic_steps", recs)
    p = H.write_scan(domain, payload)
    print("WROTE", p, "recs", len(recs), "ph", ph, flush=True)
    return recs
