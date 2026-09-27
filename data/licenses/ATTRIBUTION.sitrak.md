# ATTRIBUTION — SITRAK error codes (CC BY 4.0)

> **This attribution is a legal obligation of the CC BY 4.0 license.** It must
> remain reachable from the product's about/licenses surface for as long as
> SITRAK-derived data ships in this repository. Removing it makes the
> distribution non-compliant.

## Required attribution (verbatim, from the upstream LICENSE)

> База кодов ошибок SITRAK / SITRAK Error Codes Database
> © МегаДата (megadata.pro)
>
> Источник: МегаДата / megadata.pro — CC BY 4.0
> (https://creativecommons.org/licenses/by/4.0/)

Machine-readable form (identical to the `attribution_text` recorded in
`tools/data_ingest/provenance.json`):

    Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)

## Source

| Field | Value |
|---|---|
| Work | SITRAK Error Codes Database (`error-codes.json`) |
| Author / attribution holder | **МегаДата / megadata.pro** |
| Source URL | https://github.com/STAS63-bit/sitrak-error-codes |
| Pinned commit | `fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742` |
| License | **CC BY 4.0** (Creative Commons Attribution 4.0 International) |
| License URL | https://creativecommons.org/licenses/by/4.0/ |
| Vendored license file | `tools/data_ingest/licenses/LICENSE.sitrak` (881 B, sha256 `49ba32b704a635b18f06457a07bcc8e6c7aa57909b233b1ec66a030cb1575d45`) |
| Upstream artifact sha256 | `69d726d69d5612eb890de0aa2579beef2220a2f2b371b8cbcd560e75f12840d3` |
| Records | 8,042 (SPN + FMI), 44 systems |

**License verification note.** The GitHub API reports `NOASSERTION` for this
repository. That is a **classifier artifact**: the `LICENSE` file is a
Russian-language CC BY 4.0 deed, which GitHub's SPDX matcher cannot map. The
license *is* CC BY 4.0; the vendored license text is preserved under
`tools/data_ingest/licenses/LICENSE.sitrak`. Verdict: **REDISTRIBUTABLE with
attribution.**

## Where the attribution lives

| Location | Form |
|---|---|
| `data/licenses/ATTRIBUTION.sitrak.md` | This file — canonical human-readable attribution |
| `data/diagnostics/j1939_spn_fmi_database.json` → `metadata.sitrak_layer.attribution` | `© МегаДата / megadata.pro (https://megadata.pro) — CC BY 4.0` |
| `data/diagnostics/j1939_spn_fmi_database.json` → `spns.SPN_*` (every SITRAK-tagged SPN) | `sitrak_attribution` field on each record |
| `data/diagnostics/j1939_spn_fmi_database.json` → `spns.SPN_*.sitrak_fmi_family[*]` | `_source_license` = `CC-BY-4.0`, `_source_ref` = pinned commit URL |
| `data/PROVENANCE.md` | Per-artifact license + commit SHA table |
| `tools/data_ingest/PROVENANCE.md` | Original ingest-side license verification |

## Integration report — the product's about/licenses surface

**This report is a measured statement of the current repository state, not an
aspiration.** Stated precisely, because a false compliance claim is worse than
a known gap:

- The product **does** ship the attribution as committed, product-readable
  repository assets: this file plus the machine-readable fields listed above.
  These coexist with the pre-existing convention
  (`data/dbc/LICENSES.md`, which likewise dispositions CC-BY-4.0 and
  CC-BY-SA-4.0 sources).
- The React/pywebview UI is **owned by another agent (`cockpit`) and is under
  active concurrent edit**; my task card forbids writing to `src/ui/**`. I
  therefore did **not** add a rendered "Licenses / Hakkında" panel.
- Measured: an exhaustive search for an existing about/licenses UI surface
  found **none** carrying data-source attributions —
  `grep -i "attribution|licenses|about"` over `src/ui/**` returns only the
  Ed25519 *cloud license* card (`SettingsModal.tsx`) and unrelated prose. So
  there is no pre-existing surface I could have extended without touching
  another agent's files.

**Open action (routed to the orchestrator, not silently assumed):** exposing
`data/licenses/ATTRIBUTION.sitrak.md` in a rendered about/licenses panel is a
`cockpit`-owned UI change. Until it lands, CC BY 4.0 compliance is satisfied
**at the distribution-content level** (attribution ships with the artifacts)
but **not yet at a rendered-in-UI level**.
