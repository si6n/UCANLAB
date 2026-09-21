import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63_harvest as H

CODE_RE = re.compile(r"\b([PBCU]\d{4})\b", re.I)


def lines(b, lo=6, hi=700):
    out = []
    for ln in b.split("\n"):
        x = H.norm(ln)
        if lo < len(x) < hi:
            out.append(x)
    return out


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


def ck(txt):
    m = re.search(r"\b([PBCU]\d{4})\b", txt)
    return m.group(1).upper() if m else None


NUMH = re.compile(r"^\d{2}\.\s")


def _strip_num(s):
    return re.sub(r"^\d{2}\.\s*", "", s).strip()


# ---------- obd2hub ----------
def o2h(u, txt):
    if "What does the" not in txt or "code mean" not in txt:
        return None
    c = ck(txt)
    title = ""
    tm = re.search(r"(%s:[^\n]{5,120})" % c, txt)
    if tm:
        title = H.norm(tm.group(1))
    sev = ""
    sm = re.search(r"Severity (High|Medium|Low|Critical)", txt)
    if sm:
        sev = sm.group(1)
    causes = lines(sec(txt, "Most Common Causes", ["Symptoms You Will Notice", "Step-by-Step", "Tools"]))
    symptoms = lines(sec(txt, "Symptoms You Will Notice", ["Step-by-Step", "Tools", "Frequently", "Most Common Causes"]))
    causes = [_strip_num(x) for x in causes]
    symptoms = [_strip_num(x) for x in symptoms]
    symptoms = [x for x in symptoms if not re.match(r"^\d{2}\.\s", x)]
    causes = [x for x in causes if not re.match(r"^\d{2}\.\s", x)]
    steps = lines(sec(txt, "Step-by-Step Diagnosis", ["Tools You Will Need", "Frequently", "Need an OBD2"]))
    steps = [re.sub(r"^\d+\s*", "", s) for s in steps]
    faq = lines(sec(txt, "1. Frequently Asked Questions", ["Related", "OBD2 Hub", "Footer"]), 10, 400)
    if not (causes or symptoms or steps):
        return None
    pf = []
    for nm, b in (("Symptoms", symptoms), ("Causes", causes), ("Diagnostic Steps", steps), ("FAQ", faq)):
        if b:
            pf.append(nm + ":\n- " + "\n- ".join(b))
    return {
        "source": "obd2hub.com",
        "url": u,
        "key": c,
        "spn": None,
        "fmi": None,
        "code": c,
        "title": title,
        "symptoms": symptoms,
        "causes": causes,
        "steps": steps,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": sev,
        "evidence": H.norm(txt[:500]),
        "placeholder": False,
    }


# ---------- autofaultcodes ----------
def afc(u, txt):
    if "—" not in txt or "Possible symptoms" not in txt:
        return None
    c = ck(txt)
    title = ""
    tm = re.search(r"%s — ([^\n]{5,140})" % c, txt)
    if tm:
        title = H.norm(tm.group(1))
    symptoms = lines(sec(txt, "Possible symptoms", ["Causes to test", "Step-by-step", "How to interpret"]))
    causes = lines(sec(txt, "Causes to test", ["Step-by-step diagnostic workflow", "How to interpret", "Frequently"]))
    steps = lines(sec(txt, "Step-by-step diagnostic workflow", ["Frequently", "Related", "About", "Interpretation"]))
    steps = [re.sub(r"^\d+[.)]\s*", "", s) for s in steps]
    interp = lines(sec(txt, "How to interpret the code wording", ["Possible symptoms", "Causes to test", "Step-by-step"]))
    meas = lines(sec(txt, "Recommended measurements", ["Frequently", "Related", "About"]))
    if not (causes or symptoms or steps):
        return None
    pf = []
    for nm, b in (
        ("Interpretation", interp),
        ("Symptoms", symptoms),
        ("Causes to test", causes),
        ("Diagnostic workflow", steps),
        ("Recommended measurements", meas),
    ):
        if b:
            pf.append(nm + ":\n- " + "\n- ".join(b))
    return {
        "source": "autofaultcodes.com",
        "url": u,
        "key": c,
        "spn": None,
        "fmi": None,
        "code": c,
        "title": title,
        "symptoms": symptoms,
        "causes": causes,
        "steps": steps,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": "",
        "evidence": H.norm(txt[:500]),
        "placeholder": False,
    }


# ---------- faultcode.org ----------
def fc(u, txt):
    if "What are the symptoms of the" not in txt:
        return None
    c = ck(txt)
    short = ""
    sm = re.search(r"Short Description\s*\n([^\n]{5,160})", txt)
    if sm:
        short = H.norm(sm.group(1))
    begins = lines(sec(txt, "Symptoms", ["Causes", "Possible Solutions"]))
    # drop the boilerplate question line
    begins = [x for x in begins if "Drivers may experience" not in x and not x.startswith("What are")]
    causes = [x for x in lines(sec(txt, "Causes", ["Possible Solutions", "Related Fault Codes", "What causes"])) if "Common causes include" not in x]
    steps = lines(sec(txt, "Possible Solutions", ["Related Fault Codes", "Pro tip", "FaultCode.org"]))
    steps = [re.sub(r"^\d+[.)]\s*", "", s) for s in steps]
    rel = re.findall(r"\b([PBCU]\d{4})\b", sec(txt, "Related Fault Codes", ["Footer", "About"]))
    rel = sorted({r.upper() for r in rel if r.upper() != c})
    meaning = lines(sec(txt, "What does trouble code", ["Symptoms", "Causes"]))
    if not (causes or begins or steps):
        return None
    pf = []
    for nm, b in (("Meaning", meaning), ("Symptoms", begins), ("Causes", causes), ("Solutions", steps)):
        if b:
            pf.append(nm + ":\n- " + "\n- ".join(b))
    return {
        "source": "faultcode.org",
        "url": u,
        "key": c,
        "spn": None,
        "fmi": None,
        "code": c,
        "title": short,
        "symptoms": begins,
        "causes": causes,
        "steps": steps,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": "",
        "related_codes": rel,
        "evidence": H.norm(txt[:500]),
        "placeholder": False,
    }


# ---------- obdfyi ----------
def fy(u, txt):
    c = ck(txt)
    if not c or "About %s" % c not in txt:
        return None
    title = ""
    tm = re.search(r"%s\s*\n[^\n]*Copy\s*\n([^\n]{5,140})" % c, txt)
    if tm:
        title = H.norm(tm.group(1))
    sev = ""
    sm = re.search(r"\n([A-Z][a-z]+)\s*\n?(?:intake|engine|fuel|transmission|electrical|emission|body|network)", txt)
    if sm:
        sev = H.norm(sm.group(1))
    qa = lines(sec(txt, "Quick Answer", ["Overview", "Frequently", "Related"]), 15, 500)
    ov = lines(sec(txt, "Overview", ["Frequently Asked", "Related", "Codes", "Categories"]), 20, 900)
    symptoms = []
    m2 = re.search(r"key things to understand:([^\n]{10,900})", txt)
    if m2:
        symptoms = [H.norm(x) for x in re.split(r"\.\s+", m2.group(1)) if len(H.norm(x)) > 5]
    causes = []
    m3 = re.search(r"most common reasons this occurs include:([^\n]{10,900})", txt)
    if m3:
        causes = [H.norm(x) for x in re.split(r"\.\s+", m3.group(1)) if len(H.norm(x)) > 5]
    steps = []
    m4 = re.search(r"follow these recommended steps:([^\n]{10,1600})", txt)
    if m4:
        steps = [H.norm(x) for x in re.split(r"\.\s+", m4.group(1)) if len(H.norm(x)) > 8]
        steps = [re.sub(r"^and\s+", "", s) for s in steps]
    if not (causes or symptoms or steps or ov):
        return None
    pf = []
    for nm, b in (("Overview", ov + qa), ("Symptoms", symptoms), ("Causes", causes), ("Steps", steps)):
        if b:
            pf.append(nm + ":\n- " + "\n- ".join(b))
    return {
        "source": "obdfyi.com",
        "url": u,
        "key": c,
        "spn": None,
        "fmi": None,
        "code": c,
        "title": title,
        "symptoms": symptoms,
        "causes": causes,
        "steps": steps,
        "procedures_full": "\n\n".join(pf).strip(),
        "severity": sev,
        "evidence": H.norm(txt[:500]),
        "placeholder": False,
    }


def run(domain, urls, parser, pattern, cap=None):
    if cap:
        urls = urls[:cap]
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
        if i and i % 50 == 0:
            time.sleep(0.3)
    payload = H.meta(domain, pattern, len(urls), fetched, len(recs), ph, "procedures_full, symptoms, causes", recs)
    p = H.write_scan(domain, payload)
    print("WROTE", p, "recs", len(recs), "ph", ph, flush=True)
    return recs
