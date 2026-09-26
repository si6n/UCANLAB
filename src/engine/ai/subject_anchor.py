# -*- coding: utf-8 -*-
"""T80h subject-anchor gate — a source's own meaning claim must match the
record's `title` before its content may be merged.

THE DEFECT (measured 2026-09-26, T80d/T80h)
-------------------------------------------
Two large harvests merged English content whose SUBJECT does not match the
record it landed on. The existing gates measured length, evidence, shape and
(distinctness) — none asked "is this page about THIS code?":

    U011B  DB title : Lost Communication With Rocker Arm Control Module A
           page     : Lost Communication with Anti-lock Brake System (ABS)
                      Control Module
    P1156  DB title : HO2S Rich Average Bank 2 Sensor 1
           obdhut   : Manifold Abs.Pressure Sensor Circ. Open/Short to Ground

Measured rates: geekobd.com 72.4% of pages (4,583/6,330), obdhut.com 22.1%
(69/312 evaluated). obd2.com measured clean at 5.6%.

THE GATE
--------
An adapter that knows the page's own meaning claim (h1, subtitle, meta title)
passes it as `subject_claim`. The gate extracts the SYSTEMS named on each side
and requires an overlap. Abbreviations are mapped (EGT = Exhaust Gas
Temperature, HO2S = O2 sensor, CMP = camshaft, …) because a token comparison
alone produces false conflicts.

**How much text to pass (measured).** The gate's power scales with the claim's
richness, because a bare h1 often names no system at all. Replaying the cached
GeekOBD harvest (6,330 codes) through the gate:

    claim = h1 only                    ->  block 1,025 (16.2%)
    claim = h1 + symptoms + causes     ->  block 2,783 (44.0%)

and the richer claim catches 45 of the 79 evidence-backed reverts (the h1-only
claim catches 17). Adapters SHOULD therefore pass the page's h1 **plus** its
first few symptom/cause lines. The residual abstains are pages whose text
genuinely names no system ("Check engine light stays on") — those cannot be
judged by any text gate and need an independent vote instead.

Fail-closed rules:
  * no claim supplied            -> gate does not block (backwards compatible);
  * claim present, no system on either side -> ABSTAIN (record `unverifiable`);
  * claim present, systems disjoint          -> BLOCK;
  * systems overlap                          -> PASS.

KNOWN LIMITS (measured — do not oversell this gate)
---------------------------------------------------
1. A PASS is not proof of correctness. On the cached GeekOBD harvest the
   shared system is a BROAD token (hybrid, transmission, brake, ignition,
   fuel) for the majority of passes, and within one broad token two texts can
   still name different components (P0EDD "Battery Pack Deterioration" vs a
   page about electric motor control).

2. A BLOCK is a strong but not infallible signal. Measured after the T81
   lexicon work, 10.9% of blocks pair two classes that a human would call the
   same subsystem. Reading them by hand (P0CBC "Drive Motor Coolant Sensor"
   vs "Battery Current Sensor"; P05E2 "A/C Refrigerant" vs "coolant") shows
   most are GENUINE conflicts — the classes are distinct components that
   merely sit in one vehicle system. Treat a block as "the page names a
   different component", not "the page is definitely about a different car
   system", and confirm with an independent vote before deleting content.

3. The lexicon must be tuned with measurements, not intuition. Three
   corrections came out of measurement alone:
     * `light` matched the generic phrase "check engine light" on
       nearly every page (381 spurious blocks removed);
     * transmission / gear / shift / clutch were separate classes, so
       "Stuck in Gear 8" blocked against a TCM page (the P07Dx family);
     * 40 component classes (clutch, solenoid, actuator, converter, …) were
       missing entirely, leaving 1,956 records unjudgeable.
   After the work: block 3,049 / pass 1,309 / abstain 1,972 on 6,330 codes
   (was 1,025 / 1,003 / 4,769).

The gate is therefore a CONSERVATIVE FILTER for clear conflicts, not an
oracle. Where certainty matters, pair a verdict with an independent vote —
the T80 repair discipline — and treat `block` as the actionable signal.

Read-only, deterministic, no network. Pure function — safe to call from any
adapter or from the orchestrator before a merge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# System lexicon: (canonical name, pattern). Ordered; a text may name several.
# Patterns are deliberately generous on abbreviations because the sources use
# them heavily ("HO2S", "EGT", "CMP", "MAP", "IAT").
_SYSTEMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pat, re.I))
    for name, pat in (
        ("egt", r"\begt\b|exhaust gas temp"),
        ("o2", r"\bo2\b|oxygen sensor|ho2s|\ba/f sensor|air[- ]fuel ratio sensor"),
        ("coolant", r"coolant|\bect\b|thermostat|soğutma"),
        ("iat", r"intake air temp|\biat\b|dynamic chamber"),
        ("camshaft", r"camshaft|\bcmp\b"),
        ("crankshaft", r"crankshaft|\bckp\b"),
        ("turbo", r"turbo|supercharg|boost|charge pressure|şarj basınc"),
        ("injector", r"injector|fuel inject"),
        ("fuel pressure", r"fuel pressure|rail pressure"),
        ("fuel level", r"fuel level|yakıt seviye"),
        ("fuel pump", r"fuel pump|yakıt pompa"),
        ("egr", r"\begr\b|exhaust gas recircul"),
        ("evap", r"\bevap\b|evaporative emission"),
        ("maf", r"\bmaf\b|mass air flow"),
        ("map", r"\bmap\b|manifold abs|manifold absolute"),
        ("throttle", r"throttle|\btps\b"),
        ("pedal", r"pedal|accelerator position"),
        ("ignition", r"ignition|spark plug|misfire|\bcoil\b"),
        ("knock", r"\bknock\b"),
        ("glow plug", r"glow plug"),
        ("vvt", r"\bvvt\b|variable valve|valve timing"),
        # T81: transmission / gear / shift / clutch are ONE subsystem — a title
        # saying "Stuck in Gear 8" and a page about the TCM describe the same
        # subject. Keeping them as separate classes produced false blocks
        # (measured: the P07Dx family; 14.4% of all blocks were same-subsystem).
        ("transmission", r"transmission|\btcm\b|gearbox|şanzıman|"
                         r"\bgear\b|\bshift\b|\bshifter\b|\bclutch\b"),
        ("abs", r"\babs\b|anti[- ]lock"),
        ("wheel speed", r"wheel speed|\bwss\b|tone wheel"),
        ("brake", r"\bbrake\b|fren"),
        ("steering", r"steering|direksiyon"),
        ("airbag", r"airbag|\bsrs\b|deployment"),
        ("battery", r"\bbattery\b|\bbecm\b|charging system|alternator|generator|akü"),
        ("hybrid", r"hybrid|inverter|\bbms\b|drive motor"),
        ("hvac", r"\bhvac\b|air condition|\ba/c\b|climate|blower|compressor"),
        ("dpf", r"\bdpf\b|particulate"),
        ("catalyst", r"catalyst|catalytic"),
        ("nox", r"\bnox\b"),
        ("exhaust", r"exhaust|egzoz"),
        ("seat", r"\bseat\b|koltuk"),
        ("door", r"\bdoor\b|kapı"),
        ("window", r"\bwindow\b|window motor"),
        ("mirror", r"\bmirror\b|ayna"),
        ("suspension", r"suspension|süspansiyon"),
        ("tire", r"\btire\b|lastik|\btpms\b"),
        ("yaw", r"\byaw\b"),
        ("accel", r"acceleration sensor|accelerometer|lateral accel"),
        ("immobilizer", r"immobiliz|anti[- ]theft"),
        # T81 fix: `\blight\b` matched the generic symptom phrase "check engine
        # light" that appears on nearly every harvested page, so the class
        # fired on pages that name no real system. Restricted to actual lamp
        # assemblies; the bare word "light" is not a system claim.
        ("lighting", r"\blamp\b|\blamp assembly\b|headlamp|taillamp|"
                     r"tail lamp|turn signal|aydınlat"),
        ("wiper", r"\bwiper\b|silecek"),
        ("horn", r"\bhorn\b|korna"),
        ("reductant", r"reductant|\bdef\b|urea"),
        ("gps", r"\bgps\b|navigation"),
        ("telematics", r"telematics|radio|audio|speaker|amplifier"),
        ("camera", r"camera|image processing"),
        ("parking", r"parking assist|park brake"),
        ("rocker arm", r"rocker arm"),
        ("vacuum", r"\bvacuum\b"),
        ("pcv", r"\bpcv\b|positive crankcase|crankcase ventilation|"
                r"oil separator|oil\/air separator"),
        ("cylinder", r"cylinder|cyl\.?\s*\d|\bcyl\b"),
        ("injector pump", r"injection pump|injector pump"),
        ("glow", r"glow plug|preheat"),
        ("evap leak", r"evap.*leak|small leak|large leak"),
        ("purge", r"purge (valve|control|solenoid)"),
        ("cooling fan", r"cooling fan|fan control|radiator fan"),
        ("water pump", r"water pump|coolant pump"),
        ("oil", r"engine oil|oil pressure|oil temp|oil level|oil pump"),
        ("starter", r"starter|cranking|start(er)? relay"),
        ("charging", r"charging|alternator|generator control"),
        ("cruise", r"cruise control|speed control"),
        ("pto", r"\bpto\b|power take[- ]off"),
        ("differential", r"differential|transfer case|4wd|awd"),
        ("park brake", r"park(ing)? brake|parking brake|electric park"),
        ("traction", r"traction control|\besc\b|stability control"),
        ("ride height", r"ride height|air suspension|level control"),
        ("restraint", r"restraint|pretensioner|occupant"),
        # T81: extended COMPONENT classes. Measured on the 1,956 flagged codes
        # whose title named no taxonomy system: these classes unlock 1,196 of
        # them (61.1%). Every entry names a physical component; qualifiers
        # ("current", "lost", "invalid", "position") are deliberately excluded
        # because they would fold unrelated faults into one bucket.
        ("solenoid", r"\bsolenoid\b"),
        ("actuator", r"\bactuator\b"),
        ("converter", r"\bconverter\b"),
        ("reductant", r"reductant|\bdef\b|urea"),
        ("heater", r"\bheater\b"),
        ("relay", r"\brelay\b"),
        ("pump", r"\bpump\b"),
        ("particulate", r"particulate|\bdpf\b"),
        ("refrigerant", r"refrigerant"),
        ("shaft", r"\bshaft\b"),
        ("filter", r"\bfilter\b"),
        ("washer", r"\bwasher\b"),
        ("pilot", r"\bpilot\b"),
        ("shutter", r"\bshutter\b|grille"),
        ("lever", r"\blever\b"),
        ("motor", r"\bmotor\b"),
        ("valve", r"\bvalve\b"),
        ("disconnect", r"disconnect"),
        ("manifold", r"manifold"),
        ("intake", r"intake"),
        ("drive", r"\bdrive\b|driveline"),
        ("generator", r"generator|alternator"),
        ("charger", r"\bcharger\b"),
        ("coupler", r"coupler"),
        ("contactor", r"contactor"),
        ("precharge", r"precharge"),
        ("inverter", r"inverter"),
        ("image", r"image|camera|lidar|radar|night vision"),
        ("display", r"display|screen|cluster"),
        ("roof", r"\broof\b|sunroof"),
        ("axle", r"\baxle\b"),
        ("telematics", r"telematics|\bgps\b|navigation"),
        ("emission", r"emission"),
        ("fuel", r"\bfuel\b"),
        ("exhaust", r"exhaust"),
        ("ignition", r"ignition"),
        ("brake", r"\bbrake\b"),
        ("steering", r"steering"),
        ("suspension", r"suspension"),
    )
)

# Placeholder titles name the CODE CLASS, not a system — the gate cannot judge
# them, so it abstains rather than blocking real content.
_PLACEHOLDER_TITLE = re.compile(
    r"generic \(sae|reserved|manufacturer-specific|body dtc code|"
    r"iso/sae reserved",
    re.I,
)


def systems_in(text) -> set[str]:
    """Canonical system names mentioned in `text` (deterministic)."""
    t = str(text or "")
    return {name for name, pat in _SYSTEMS if pat.search(t)}


@dataclass
class AnchorVerdict:
    """Outcome of one subject-anchor check."""

    status: str                      # pass | block | abstain
    reason: str
    title_systems: list[str] = field(default_factory=list)
    claim_systems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != "block"


def check_subject_anchor(title, subject_claim) -> AnchorVerdict:
    """Does the source's own meaning claim describe the record's subject?

    Returns `pass` when the two texts share a system (or when either side
    names no system at all — an unverifiable claim is not evidence of a
    mismatch), `block` when both name systems and they are disjoint.
    """
    title = str(title or "")
    claim = str(subject_claim or "")
    if not claim.strip():
        return AnchorVerdict("pass", "no claim supplied")
    if _PLACEHOLDER_TITLE.search(title):
        return AnchorVerdict("abstain", "record title is a placeholder")

    t_sys = systems_in(title)
    c_sys = systems_in(claim)
    if not t_sys or not c_sys:
        return AnchorVerdict(
            "abstain", "one side names no system", sorted(t_sys), sorted(c_sys))
    shared = t_sys & c_sys
    if shared:
        return AnchorVerdict(
            "pass", f"shared system(s): {sorted(shared)}",
            sorted(t_sys), sorted(c_sys))
    return AnchorVerdict(
        "block",
        f"subject conflict: record names {sorted(t_sys)}, "
        f"source names {sorted(c_sys)}",
        sorted(t_sys), sorted(c_sys))


# ============================================================================
# T82: LAYER ADMISSION — the two-test discipline
# ============================================================================
#
# MEASURED (2026-09-26). Two independent defect classes were found in
# harvested content, and each needs its OWN test. Passing one does not imply
# passing the other:
#
#   * geekobd.com `symptoms_en` — the text was well-written and unique per
#     code (masked distinctness 1.0000) but bound to the WRONG code for 72.4%
#     of records. Template test passed; attribution test failed.
#   * obdhut.com `diagnose` — the text was bound to the right code but 969 of
#     1,085 entries were byte-identical apart from the code name (masked
#     distinctness 0.108). Attribution test irrelevant; template test failed.
#
# So a layer is admissible only when BOTH hold:
#   1. TEMPLATE TEST  — masked distinctness >= 0.60
#   2. ATTRIBUTION TEST — subject anchor passes per record
#
# The masked form replaces the code token before comparing, so a source that
# merely substitutes the code into one sentence cannot pass by accident.

# T82 fix: the first version used `[PBCU]\d{3,4}`, which MISSES hex-suffix
# codes (P04AB, U01B0, C12FF — the largest single block in this DB: 431 of
# the 924 description-gap codes are hex-suffix). A masked-distinctness test
# that fails to mask the code cannot detect a template, so the pattern must
# cover the full DTC shape. The test suite caught this.
_CODE_TOKEN = re.compile(
    r"\b[PBCU][0-9A-F]{4}\b"      # hex-suffix form (P04AB, U01B0)
    r"|\b[PBCU]\d{3,4}\b"          # plain numeric form (P0301)
    r"|\bSPN\s?\d+\b",             # J1939 SPN
    re.I,
)


def masked_text(text) -> str:
    """Replace any DTC/SPN code token so template text can be compared."""
    return _CODE_TOKEN.sub("<C>", str(text or ""))


def masked_distinctness(texts) -> float:
    """Distinctness after masking code tokens (1.0 = every text is unique
    beyond the code name; low = one template with the code substituted)."""
    norm = [masked_text(t).strip() for t in texts]
    norm = [t for t in norm if t]
    if not norm:
        return 0.0
    return len(set(norm)) / len(norm)


@dataclass
class LayerVerdict:
    """Admission result for one harvested layer."""

    admissible: bool
    template_ok: bool
    attribution_ok: bool
    masked_distinctness: float
    anchor_pass: int
    anchor_block: int
    anchor_abstain: int
    reason: str


def check_layer_admission(
    records,
    *,
    min_masked_distinctness: float = 0.60,
) -> LayerVerdict:
    """Apply BOTH tests to a candidate layer before it may be merged.

    ``records`` is an iterable of dicts with:
        text   — the harvested string for this record
        title  — the record's authoritative title

    Returns a LayerVerdict. ``admissible`` is True only when the template test
    and the attribution test both pass. No I/O, deterministic.
    """
    texts, title_pairs = [], []
    for rec in records:
        text = str(rec.get("text") or "").strip()
        if not text:
            continue
        texts.append(text)
        title_pairs.append((str(rec.get("title") or ""), text))

    dist = masked_distinctness(texts)
    template_ok = dist >= min_masked_distinctness

    passed = blocked = abstained = 0
    for title, text in title_pairs:
        v = check_subject_anchor(title, text)
        if v.status == "pass":
            passed += 1
        elif v.status == "block":
            blocked += 1
        else:
            abstained += 1
    # T82 fix: the attribution test fails on a BLOCK, not on an abstain. An
    # abstain means the classifier could not judge (neither side named a
    # system) — that is a coverage limit, not evidence of misattribution.
    # Requiring `passed > 0` wrongly rejected a layer whose texts are short
    # labels naming no system at all. The invariant is: zero blocks.
    attribution_ok = blocked == 0

    if template_ok and attribution_ok:
        reason = (f"both tests pass (masked distinctness {dist:.4f}, "
                  f"{passed} anchor passes, 0 blocks)")
    elif not template_ok:
        reason = (f"TEMPLATE FAIL: masked distinctness {dist:.4f} < "
                  f"{min_masked_distinctness} — one template with the code "
                  f"substituted")
    else:
        reason = (f"ATTRIBUTION FAIL: {blocked} records name a different "
                  f"system than their title")
    return LayerVerdict(admissible=template_ok and attribution_ok,
                        template_ok=template_ok, attribution_ok=attribution_ok,
                        masked_distinctness=dist, anchor_pass=passed,
                        anchor_block=blocked, anchor_abstain=abstained,
                        reason=reason)
