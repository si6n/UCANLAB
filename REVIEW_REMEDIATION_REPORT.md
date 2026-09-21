# REVIEW Remediation Report — Aşama 1–6

**Repository:** `si6n/UCANLAB` (`Universal-CAN-BUS-Tool`)
**Review target commit:** `f97e943`
**Baseline at session start:** `6456d14` (branch `main`)
**Language:** Python 3.11+ / TypeScript (React 18 + WebView2)
**Method:** subagent-assisted verification (independent agents per review phase), then remediation + regression tests.

---

## 1. Executive summary

Six specialist review reports (Aşama 1–6) plus `review.md` were examined. **Every finding was
independently verified against the live tree** before any code was changed — verification was
performed by executing probes and reading the actual source, not by trusting the reports.

Result of that verification pass: **not every reported finding was real.**

* Some findings were **already fixed** before the review was written and are stale (false alarms).
* Some findings were **real but mis-described** — the code defect exists, but not by the mechanism
  the report named (which changes the correct fix).
* Some findings were **severity-overstated** — the defect is real but the flagged field/data flows
  into no security decision.

The remediation below fixes only what verification proved real, corrects the severities, and
documents the false alarms so they are not re-opened.

### Test evidence

| Check | Before | After |
|---|---|---|
| Full `pytest` suite | 2231 passed, 0 failed | **2429 passed, 1 skipped, 0 failed** |
| `tests/safety/` (all safety invariants) | — | **35 passed** |
| `ruff check .` (exact CI command) | failing (208 pre-existing archive errors) | **All checks passed** |
| `bandit -r src/ -ll` (CI gate) | 0 Medium / 0 High | **0 Medium / 0 High** |
| `npx tsc --noEmit` (frontend) | not run in CI | **exit 0** |
| Coverage (`--cov=src`) | floor 80, true value unmeasured | **81.95% measured; floor raised to 81** |
| UTF-8 BOM count in `.py` | 10 | **0** |

Net new regression tests: **+198**.



---

## 2. Verification verdicts

### 2.1 Real and fixed in this session

| ID | Phase | Defect (verified) | Verdict |
|---|---|---|---|
| S1-P1-1 | 1 | 29-bit `0x18DA00F9` ISO-TP (PF=0xDA) UDS frames carrying `ECUReset`/`RoutineControl`/`RequestDownload`/`LinkControl`/`WriteMemoryByAddress` were **whitelisted but `critical=False`** — a critical command bypassed the Stage-3/5 escalation path. | FIXED |
| S1-P1-2 | 1 | A J1939 **Request** (PGN 59904, `0x18EA00F9` + `D3 FE 00` = DM11 clear) was treated as a read-only frame because criticality looked only at the transport PGN, not the **requested** PGN. | FIXED |
| S1-P1-3 | 1 | `ReplaySafetyFilter` blocked a **different** prohibited-SID set than the live gateway (`{0x38,0x3D,0x87}` replay-only, `{0x35}` gateway-only) — a live injection was less guarded than the same frame replayed. | FIXED (unified on `src/safety/criticality.py`; only `0x3E` intentionally differs) |
| S1-P1-4 | 1 | `PCAN/Kvaser` `set_listen_only()` returned a **phantom success** when the backend raised `NotImplementedError`/`AttributeError`, i.e. it claimed RX-only while still TX-capable. | FIXED (fail-closed) |
| S1-P0-1 | 1 | `RP1210Bus` had **no** `set_listen_only()` at all, so the desktop fallback `getattr(bus, "listen_only", not listen_only) == listen_only` was **always False** — an RP1210 link could never be confirmed listen-only. | FIXED |
| S1-P1-6 | 1 | The WebView bridge exposed a `skipTargetIdentity` flash option letting the renderer bypass flash target-identity verification. | FIXED (requires BOTH `UCANLAB_FLASH_SKIP_IDENTITY=1` and `UCANLAB_TEST_MODE=1`) |
| S1-P2-5 | 1 | Two separate default `SecretProvider` instances were built, so an ephemeral provider observed `has_ESTOP_key=False` on one and `True` on the other — E-Stop state could disagree. | FIXED (memoised singleton per storage dir) |
| F1 | 3 | Firmware flattening was an **unbounded memory-amplification** primitive: a 79-byte Intel-HEX file with two far-apart segments drove a **256 MiB** allocation (`2**30` → 1 GB in 0.48 s). `max_image_bytes` was checked **after** padding. | FIXED (span/gap/payload caps + profile cap checked **before** allocation) |
| F3 | 3 | The HEX/S-Record parsers accepted unknown record types and under-validated EOF/S5. | FIXED (record allowlist, single-EOF, S5 count, line/file caps) |
| F4 | 3 | A signed cloud ticket with `"iat": NaN` / `"exp": NaN` was **accepted**; `Infinity` yielded an eternal license. A non-object payload raised a raw `AttributeError`. | FIXED (`parse_constant` hook, schema-version allowlist, bounded fields, non-finite rejection) |
| D3 | 2 | `_app_data_root()` anchored writable state to `Path(sys._MEIPASS).parent`. In the shipped `--onefile` build that directory is **deleted on exit**, so logs, blackbox, reports, exports — **and the license anti-rollback HWM** — vanished every run. | FIXED (persistent OS user-data root + separate read-only resource root) |
| D5 | 2 | The upload-accept log wrote the operator's **full absolute path** (username + project identity) into log aggregation. | FIXED (logs file name only) |
| D6 | 2 | `cloud_register_device` / `cloud_activate_license` forwarded unvalidated renderer input (1 MB string, `None`, `int`, `list`, CRLF) to the cloud client. | FIXED (shared bridge validator) |
| A5-1 | 5 | An empty/whitespace session token was stored as `b""` and reported `is_authenticated=True`. | FIXED (blank rejected at the boundary) |
| A5-3 | 5 | The machine seed — the **root key of the encrypted secret vault** — was read with no length/type/permission checks. A 3-byte seed, a directory, or a **symlink** was accepted. | FIXED (length/regular-file/symlink/permission validation, fail-closed) |
| A5-4 | 5 | `ask_copilot` accepted an **unbounded** query (the engine does O(n²) fuzzy matching) and a non-`str` raised uncaught. | FIXED (bounded, typed, structured error) |
| S2 | 4 | `ReplaySafetyFilter` mutated counters and the ISO-TP session ledger **without a lock**; a torn ISO-TP ledger is a policy bypass. | FIXED (`RLock` around evaluation + counters, atomic metrics snapshot) |
| S3 | 4 | The protection policy was plain **public mutable attributes** — `filter.block_diagnostic_write = False` re-enabled DM11/DM3 replay mid-session. `custom_blocked_ids or set()` **aliased** the caller's set. | FIXED (read-only properties, `frozenset` copy) |
| S4 | 4 | Linux CI ran only `-k "vcan or virtual_bus or hardware"`; the full 2200+ suite was Windows-only. | FIXED (new full-suite Linux job with the coverage gate) |
| S6 | 4 | `pip-audit --ignore-vuln PYSEC-2026-2447` was inlined with **no owner, justification or expiry** — an advisory could be suppressed forever. | FIXED (governed `security-exceptions.yaml` + expiry-enforcing checker wired into both pipelines) |
| C4 | 4 | Coverage gate drifted: GitHub enforced `--cov-fail-under=80`, GitLab `79`. | FIXED (both 80) |
| F6-3 | 6 | The frontend job ran only `npm ci` + `npm run build` — no typecheck, lint, or dependency audit. | FIXED (typecheck + lint + `npm audit --audit-level=high`; strict TS promoted via new scripts) |

### 2.2 Verified, but corrected — severity and/or mechanism

| ID | Reported | Verified reality | Action |
|---|---|---|---|
| **A5-1** | Medium | Old — the flagged field `is_authenticated` has **zero consumers** (`auth.py:33/116/138/164` are assignments only); the launch gate uses `has_valid_license` (`app.py:256/261/526`). | Fixed anyway (correctness), recorded as **Low** |
| **A5-5** | Medium | **Mitigated in practice** — all 8 HWID sinks already truncate to 8 chars; auth exception messages are static strings carrying no HWID. | No code change; recorded as **Low / defense-in-depth** |
| **A5-6** | Medium | Rule is comment-only (`send()` is public, no `PrivilegedTxPort`), **but no production bypass exists** — all 12 `.send(`/`._send_raw(` sites are gateway-port calls, the driver's own backend, or base shims. | No code change; recommend a CI architectural gate, not an urgent fix |
| **D4** | "setItem before await" | The mechanism is **codemically false** — the TS diff vs `f97e943` is empty and the handler is synchronous. The real (smaller) bug: a rejected URL still shows "Saved" and persists to `localStorage`; and no Python code reads `cloud_base_url`. | Recorded as **Low**, mechanism corrected |
| **D2** | Medium | Real but **unreachable from the shipped UI** — the only caller (`SettingsModal.tsx:103`) passes no token, so the shared-vault save/restore block is dead in production. | Recorded as **Low-Med**; request-scoped token remains a future hardening item |
| **F6/F7** | Medium | Blast radius is small — per AGENTS.md §2.7 only `MatExporter` is wired, and `pdf_report.py` is an **HTML generator**, not a PDF writer. | Recorded lower; not blocking |

### 2.3 False alarms — already fixed before the review (do NOT re-open)

| ID | Report claim | Reality |
|---|---|---|
| **F2** | 0x27 `SecurityAccess` used without a confirmation token in the flasher | **Already correct** — both methods take `confirmation_token`/`confirmation_context`; the flasher threads them. |
| **D1** | HWM anti-rollback silently reset | **Already fixed** — corrupt/missing/HMAC-invalid HWM raises `HWM_UNAVAILABLE`; persist failures raise. (Probe-proven.) |
| **C1** | GitLab security job could silently fail | **Already correct** — `.gitlab-ci.yml:73 allow_failure: false`. |
| **C2** | Unpinned dependencies | **Already fixed** — `requirements.lock` with 348 `--hash=sha256` pins. |
| **F6-6** | Google Fonts loaded (online dependency) | **Source fixed** — but a **stale checked-in `src/ui/frontend/dist/index.html`** still references `fonts.googleapis.com`. Release trap: `scripts/build_exe.py:77` rebuilds only when `dist/index.html` is *missing*. | Flagged as an operational risk (see §4). |

---

## 3. Key remediation details

### 3.1 Single source of truth for criticality (`src/safety/criticality.py`, new)

The gateway and the replay filter each maintained their own prohibited-SID set, and they had
**drifted apart** — the unsafe direction. A new module now owns:

```python
CRITICAL_UDS_SIDS               # 0x10,0x11,0x14,0x27,0x28,0x2E,0x2F,0x31,0x34..0x38,0x3D,0x85,0x87
REPLAY_ONLY_PROHIBITED_UDS_SIDS # {0x3E} TesterPresent — the one intentional divergence
PROHIBITED_UDS_SIDS             # union, imported by both the gateway and the replay filter
CRITICAL_J1939_PGNS             # DM11, DM3, DM4, DM5, CommandedAddress, TSC1, XBR
J1939_REQUEST_PGN = 59904       # Request — criticality follows the REQUESTED PGN
```

`TxSafetyGateway._frame_is_critical()` was rewritten to resolve criticality across:

* 11-bit UDS (SID at `data[1]`),
* 29-bit ISO-TP with `PF == 0xDA` (classic SF/FF SID offsets, plus the **CAN-FD escape** form
  where the SID sits at offset **6**),
* J1939 write/actuate PGNs,
* J1939 **Request** frames — critically, resolving the requested PGN from the payload
  (little-endian per J1939-21) and failing **closed** on a short payload.

The corrected offset matters: an earlier iteration of this fix had an off-by-one (offset 5), which
a purpose-built test caught. CAN-FD escape SF and FF are now covered by separate tests.

### 3.2 Listen-only must fail closed (`S1-P1-4`, `S1-P0-1`)

* `PCAN/Kvaser`: an unmutable backend now returns **failure**, not phantom success.
* `RP1210Bus`: gained `set_listen_only()` (honest software TX-gate flip) and
  `hardware_listen_only` → `False`.
* `VirtualBus`: gained `listen_only` state and `send()` raises
  `HardwareError(code="HARDWARE_LISTEN_ONLY_TX_BLOCKED")` while set.
* The pre-existing "virtual interface" exemption is preserved via
  `getattr(self, "interface", None) == "virtual"`, so a duck-typed/`__new__`-built bus does not
  raise `AttributeError`.

### 3.3 Persistent writable root (`D3`)

```python
_resource_root()      # bundled read-only assets: sys._MEIPASS (frozen) / repo (source)
_app_data_root()      # PERSISTENT writable state; frozen -> %LOCALAPPDATA%\UniversalCAN,
                      # ~/Library/Application Support/UniversalCAN, or $XDG_STATE_HOME/universal_can
_ensure_app_data_root()  # creates it (0700 on POSIX)
```

This is the highest-impact fix of the set. Because the license HWM is the anti-rollback clock
anchor and the (already-hardened) loader **fails closed**, the old `_MEIPASS` anchoring meant a
frozen build would raise `LicenseError(HWM_UNAVAILABLE)` on the *second* launch. A simulated-restart
test now proves the HWM survives `_MEIPASS` deletion.

### 3.4 Bounded firmware flattening (`F1`)

```python
FirmwareParseLimits(max_file_bytes=128 MiB, max_lines=4M, max_line_chars=1024,
                    max_segments=65 536, max_address_span=64 MiB,
                    max_gap=1 MiB, max_total_payload_bytes=64 MiB)
```

`get_continuous_binary()` enforces the span/gap caps **before** allocating; `FlashingConfig` checks
the ECU profile cap **before** padding and wraps the failure in `ValueError`. `MemoryError` is
converted to a `ProtocolError`.

### 3.5 Cloud ticket hardening (`F4`)

`json.loads(..., parse_constant=_reject_non_finite_json_constant)`, a dict-type guard, a
schema-version allowlist (`{1}`), bounded non-empty strings (`license_id`, `organization_id`,
`device_id`, `tier`, `kid` ≤ 256 chars), a bounded `features` list (≤ 128), and finite non-negative
`iat`/`exp`/`offline_until`.

**Deliberately no cross-field ordering constraints.** An initial `iat <= offline_until <= exp` rule
changed six existing error codes (`MALFORMED_SCHEMA` where `LICENSE_EXPIRED`/`OFFLINE_GRACE_EXPIRED`
was expected) and one test legitimately used `exp < iat`. Ordering is genuinely ambiguous in the
wild, and the *hazard* — `NaN` defeating every comparison — is fully closed by finiteness alone.

### 3.6 Replay filter concurrency + immutable policy (`S2`/`S3`)

```python
self._lock = threading.RLock()          # guards counters + the ISO-TP session ledger
self._custom_blocked_ids = frozenset(...)   # copies the caller's set
# read-only @property access for every policy flag; no setters
snapshot_metrics()                      # atomic counter read
reset_session_state()                   # clears a stale cross-session ISO-TP ledger
```

A concurrency test hammers the filter from four threads and asserts the counters sum exactly.

### 3.7 CI governance (`S4`, `S6`, `C4`, `F6-3`)

* **S4** — new `test-linux-full` job runs the whole suite with `--cov-fail-under=80`.
* **S6** — `security-exceptions.yaml` requires `id`, `owner`, `justification` and a future `expires`
  per advisory; `scripts/check_security_exceptions.py` fails the job once an entry expires (real
  calendar-date validation, duplicate-id detection) and emits the `--ignore-vuln` flags, so the list
  is never hand-maintained again. Wired into **both** GitHub Actions and GitLab.
* **C4** — GitLab `79` → `80`.
* **F6-3** — frontend job now runs typecheck, lint, and `npm audit --audit-level=high`.
  Because the repo had no `typecheck`/`lint` scripts, they were added to `package.json`
  (`tsc --noEmit`); the steps are `continue-on-error: true` so the pipeline is not red on arrival.

---

## 4. Second batch — residual items resolved

After the first remediation pass the remaining items in the register were resolved. Four of them
turned out to be hiding something more serious than the review described.

### 4.1 Fixed

| Item | What was done |
|---|---|
| **D2** request-scoped token | `CloudClient.request()` gained a `session_token=` parameter. The desktop bridge's *save → fire → restore* window (which let a concurrent request use the wrong session, and left the vault holding the wrong token if it crashed) was replaced with a per-request override. Verified by test: the vault is byte-identical after an override call. |
| **F5** typed cloud response | `CloudResponse.json_object()` raises `ProtocolError(code="CLOUD_MALFORMED_RESPONSE")` for empty bodies, invalid JSON, and valid-JSON-but-not-an-object. All 9 object-expecting call sites migrated off the loose `.json()` (which leaked raw `JSONDecodeError`/`AttributeError`). |
| **F6-1** CSP + navigation policy | New `src/ui/frontend_server.py`: a **loopback-only** (`127.0.0.1:0`) static server with a strict CSP (`default-src 'none'`, `connect-src 'self'`, `frame-ancestors 'none'`, no wildcard host), `nosniff`, `no-referrer`, and COOP/COEP/CORP. Path traversal is blocked for raw *and* percent-encoded forms; directory listings are disabled. The app now loads `http://127.0.0.1:<port>/index.html` instead of `file://`. |
| **F6-4** loose bridge wrappers | All **26** bridge wrappers that previously only checked "is a native shell present" now also assert their **concrete method** exists and throw in production when it is absent — no wrapper can fall through to a silent mock. Verified by an automated scan test. |
| **F6-6** stale-dist release trap | `build_exe.py` now rebuilds the frontend when any **source** is newer than the built `index.html` (not merely when the file is missing), and **refuses to package** a bundle that references a remote origin (Google Fonts, jsDelivr, unpkg). |
| **F7** export path confinement | Five duplicated `_resolve_export_path` helpers (mat/mdf4/kml/html/dbc) all carried the same waiver: when `exports_root` was omitted, anything under the **system temp dir** was accepted. Replaced by one shared `src/engine/exporters/path_guard.py` — explicit root required, no temp exemption, symlinked-parent check, bare-name enforcement for relative targets. |
| **A5-2** silent protection downgrade | `protection_level()` (defined but never called) is now consulted at startup; `EPHEMERAL`/`FALLBACK_FILE` log a **DEGRADED** warning. `SECURITY.md` gained a documented Secret-Store section covering the protection levels, the same-user trust boundary, and the A5-3 seed checks. |
| **A5-6** TX choke-point gate | New `tests/safety/test_tx_chokepoint_architecture.py` scans every module under `src/` for raw transmit primitives (`privileged_send`/`send`/`_send_raw`/`transmit`) and fails unless the module either is on a justified allowlist or routes through the gateway port. It also asserts the desktop composition root passes `tx_port=self.gateway`. |
| **ruff CI gate** | `ruff check .` (the exact CI command) was **failing** on 208 pre-existing errors. Auto-fixes applied; the frozen one-shot T63 scratch scripts were added to the existing `exclude` list, consistent with the precedent already in `pyproject.toml`. Now green. |

### 4.2 New findings discovered while resolving the above

These were **not** in any review report. They surfaced because the fixes forced a real build and a
real code scan.

| # | Finding | Severity | Action |
|---|---|---|---|
| **N-1** | **The stale-dist trap was real, not hypothetical.** The checked-in `src/ui/frontend/dist/index.html` genuinely still referenced `fonts.googleapis.com` and `fonts.gstatic.com`, and `build_exe.py` would have shipped it. | **High** (offline-invariant violation + network egress from a "fully offline" app) | Rebuilt the frontend; the bundle is now clean. A regression test asserts the checked-in dist carries no remote origin. |
| **N-2** | **Orphaned cloud-LLM clients were still in the source tree.** `src/services/geminiClient.ts` called `generativelanguage.googleapis.com` and `openAiClient.ts` called `api.openai.com` — both contradicting AGENTS.md §2.8 ("the cloud-LLM narration layer … was REMOVED"). Nothing imported them, so they were tree-shaken out of the bundle, but they were one `import` away from being live. | **Medium** (latent offline-invariant violation) | Deleted both files; added a test asserting no frontend source references a generative-AI endpoint. Legacy `localStorage` key purges were kept (with a clarifying comment) as upgrade hygiene. |
| **N-3** | **Encoding corruption introduced by my own first-batch edits.** Four files were rewritten with `Set-Content -Encoding utf8`, which added a UTF-8 **BOM** and double-encoded the Turkish text (`Güvenlik` → `GÃ¼venlik`). This broke 5 tests and would have shipped mojibake error strings to Turkish operators. | **High** (user-visible corruption in operator-facing messages) | Repaired with a verified inverse-mojibake transform (byte-level diagnosis: `ş` = `c5 9f` had become `ÅŸ` = `c3 85 c5 b8`), stripped the 4 new BOMs, and added a compile-check guard so a corrupted file is never written. `src/` is now free of both mojibake and new BOMs. |

### 4.3 Third batch — the residual register is now closed

Every item that §4.3 previously listed as "open / accepted / noted" has been resolved.

| Item | What was done |
|---|---|
| **D4** optimistic "Saved" toast | Both save sites (`SettingsView.tsx`, `SettingsModal.tsx`) now **await** `cloudSaveConfig` and only touch `localStorage` + show success after the backend confirms. A host outside the cloud allowlist now surfaces an error and leaves storage untouched. 3 tests. |
| **F6** atomic export writes | `path_guard.py` gained `atomic_write_text` / `atomic_write_bytes` / `atomic_producer_path` + `commit_producer_path` (write-to-`.part`, `fsync`, atomic `replace`; any failure deletes the scratch file). `kml_exporter`, `pdf_report`, and `mat_exporter` (via the producer path, since `scipy.io.savemat` opens the file itself) migrated. `mdf4_exporter` already did this correctly and was left alone. 6 tests. **The tests immediately found a real defect in my first implementation** — cleanup lived inside `_atomic_replace`, so patching the rename leaked a `.part` file. Cleanup now sits in a separate `_publish` boundary and is unconditional. |
| **S4/C4** coverage floor | Measured the real figure: **81.95%** (20 834 statements, 3 761 missed — displayed as "82%"). All three gates (GitHub ×2, GitLab ×1) raised 80 → **81**. Note it is deliberately *not* 82: a floor of exactly 82 **fails on this very tree** (`Required test coverage of 82% not reached. Total coverage: 81.95%`), which the gate caught when first tried. 81 is a genuine regression floor with one point of honest headroom. The governance test was rewritten to assert *agreement between pipelines* rather than a hard-coded number. |
| **Pre-existing UTF-8 BOMs** | All **10** BOMs removed (`src/launcher/**`, `src/engine/discovery/**`, 2 test files). Verified: modules still import, affected tests pass, ruff clean. |
| **F6-6** release trap, at its root | Corrected an error in the earlier report: **`dist/` is NOT tracked and already gitignored**, so the stale bundle was a *local build artefact*, not a committed one. The durable fix is therefore a **CI assertion**: the `frontend-build` job now runs `_assert_dist_offline()` on the freshly built bundle and fails if any remote origin is referenced, plus a scan failing on any generative-AI endpoint in `src/`. |
| **F6-7** release signing | Still deliberately out of scope — it is a signing-infrastructure decision (key custody, notarisation), not a source change. |

Residual register after the third batch: **only F6-7 remains**, and it is a release-process item rather than a code defect.

Session notes were also written to the project vault (`obsidian-vault/04-Ajan-Notlari/`):
`Tur-65-Review-Remediation-Kapanis.md` (closure note) and four new lessons appended to
`Tuzaklar-ve-Dersler.md` (§20–§23: self-inflicted encoding corruption, verifying before fixing,
atomic-write test design, and measure-don't-guess thresholds).


---

## 5. Files changed

### New
* `src/safety/criticality.py` — shared criticality catalogue
* `src/ui/frontend_server.py` — loopback-only CSP frontend server (F6-1)
* `src/engine/exporters/path_guard.py` — single export-path confinement (F7)
* `security-exceptions.yaml` — governed advisory exceptions
* `scripts/check_security_exceptions.py` — expiry/ownership enforcer
* `tests/safety/test_tx_chokepoint_architecture.py` — 10 tests (A5-6)
* `tests/unit/test_review_phase1_safety_fixes.py` — 46 tests
* `tests/unit/test_review_phase2to6_fixes.py` — 62 tests
* `tests/unit/test_review_ci_governance_fixes.py` — 19 tests
* `tests/unit/test_review_residual_risk_fixes.py` — 49 tests

### Deleted
* `src/ui/frontend/src/services/geminiClient.ts` — orphaned cloud-LLM client (N-2)
* `src/ui/frontend/src/services/openAiClient.ts` — orphaned cloud-LLM client (N-2)

### Modified (core)
`src/safety/gateway.py`, `src/safety/secret_provider.py`, `src/hal/replay/safety_filter.py`,
`src/hal/drivers/pcan_kvaser.py`, `src/hal/rp1210/bus.py`, `src/hal/virtual.py`,
`src/protocols/uds/firmware.py`, `src/protocols/uds/flasher.py`,
`src/security/cloud/license_flow.py`, `src/security/cloud/client.py`,
`src/security/cloud/telemetry_uploader.py`, `src/launcher/updater.py`,
`src/ui/desktop_app.py`, `src/launcher/auth.py`,
`src/engine/exporters/{mat,mdf4,kml,pdf_report}_exporter.py`, `src/engine/discovery/{engine,dbc_builder}.py`

### Modified (frontend)
`src/ui/frontend/src/services/bridge.ts` (F6-4), `src/ui/frontend/src/App.tsx`,
`src/ui/frontend/dist/**` (rebuilt to clear the remote-origin trap, N-1)

### Modified (CI / build / config)
`.github/workflows/ci.yml`, `.gitlab-ci.yml`, `src/ui/frontend/package.json`,
`scripts/build_exe.py`, `pyproject.toml` (ruff exclude for frozen scratch),
`SECURITY.md` (secret-store section)

### Modified (tests)
* `tests/unit/test_t47_tx_review_fixes.py` — `test_g2_j1939_read_only_requests_are_not_escalated`
  encoded the **vulnerable** behaviour (it asserted a J1939 Request must NOT be escalated). The
  rewrite preserves the original anti-false-positive intent while enabling the new payload-aware
  check; a new `test_g2_j1939_request_escalation_is_fail_closed_on_short_payload` locks in
  fail-closed behaviour.
* `tests/unit/test_exporters.py`, `tests/unit/test_review23_remediations.py`,
  `tests/unit/test_signal_discovery.py` — now pass an explicit `exports_root` (F7 made the root
  mandatory), which exercises the confinement check instead of the removed temp waiver.

---

## 6. Invariants honoured (AGENTS.md §2)

* **Single audited TX choke-point** — untouched and re-verified; no new direct driver injection.
* **Fail-closed** — every change in this set fails *closed*, not open (listen-only, HWM, seed,
  criticality, ticketing).
* **No fabricated telemetry** — no decoder touched.
* **Watchdog / E-Stop** — the singleton fix makes the E-Stop key observable consistently; reset
  flow unchanged.
* **Renderer cannot disarm safety** — `skipTargetIdentity` now requires a dual env-var test gate.
* **Speed interlock provenance** — untouched.
* **AI offline & TX-isolated** — `tests/safety/test_ai_tx_isolation.py` re-run: **12 passed**;
  additionally enforced at the frontend by deleting the orphaned cloud-LLM clients (N-2), refusing
  to package a bundle with remote origins (F6-6/N-1), and serving the UI under a CSP that forbids
  non-`self` connections (F6-1).
* **Wiring gate** — no unwired component was activated.

---

## 7. Verification commands

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"

# Full suite (must be green)
& $py -X utf8 -m pytest -q

# All safety invariants (isolation + TX choke-point architecture)
& $py -X utf8 -m pytest tests/safety/ -q

# New regression suites
& $py -X utf8 -m pytest tests/unit/test_review_phase1_safety_fixes.py `
                         tests/unit/test_review_phase2to6_fixes.py `
                         tests/unit/test_review_ci_governance_fixes.py `
                         tests/unit/test_review_residual_risk_fixes.py -q

# Quality / security gates (exactly what CI runs)
& $py -m ruff check .
& $py -m bandit -r src/ -ll -q
& $py scripts/check_security_exceptions.py

# Frontend gates
Push-Location src/ui/frontend; npx tsc --noEmit -p tsconfig.json; Pop-Location
```

> Use `-X utf8` (and `$env:PYTHONIOENCODING='utf-8'`) when running on Windows: the Turkish error
> strings in assertion output are otherwise mangled by the console codepage, which makes a passing
> comparison look like a failure. This is a **display** issue — but see N-3, where a genuine
> encoding corruption produced the same symptom, so always confirm against `ruff`/`compile()`.

---

*Report generated as part of the REVIEW Aşama 1–6 remediation. All findings were verified before
being acted on; false alarms and severity corrections are recorded above so they are not re-opened.
Three findings not present in any review report (N-1, N-2, N-3) were discovered while resolving the
residual items and are documented in §4.2.*
