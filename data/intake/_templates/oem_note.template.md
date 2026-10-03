<!-- intake-meta
{
  "schema_version": 1,
  "intake_id": "template-oem-note-p0401-example",
  "record_type": "oem_note",
  "submitted_at": "2026-10-02",
  "submitter": {
    "type": "curator",
    "id": "workshop-berlin-01",
    "role": "author"
  },
  "source": {
    "title": "Example: manufacturer service bulletin index entry",
    "path": "https://example.invalid/replace-with-the-real-document-url",
    "type": "service_bulletin",
    "publisher": "Example Motorservice",
    "revision": "2025-01",
    "licence": "proprietary",
    "access_date": "2026-10-02",
    "snapshot": {
      "archive_url": null,
      "sha256": null,
      "bytes": null,
      "pages_cited": []
    }
  },
  "confidence": "single_source",
  "draft": true,
  "knowledge_base": null,
  "payload": {
    "make": "Example Motors",
    "model": "Example Sedan",
    "year": 2019,
    "oem_code": "EA01-21-501A",
    "system": "Exhaust / emissions",
    "evidence_refs": [],
    "related_dtcs": [
      "P0401"
    ],
    "vin_masked": "WBA*****X2M"
  },
  "notes": "Body text lives below the meta block, not inside it. Keep it free of VIN, customer name, plate and e-mail."
}
-->

# Example OEM note — P0401 on Example Sedan 2019

**Why it is here:** the OEM bulletin uses a *different* wording for the EGR flow
insufficient condition than the generic databases, and the workshop keeps
re-reading this entry. The note is staged here; it is **not** merged into
`data/diagnostics/`.

**Observed in the workshop**

- EGR valve duty cycle stays at the mechanical stop after warm-up.
- Freeze frame shows EGR temperature within the expected band, so the cooler is
  not the first suspect.
- The same bulletin number appears on a sibling model year; the applicability
  row is copied verbatim from the bulletin, not inferred.

**What is still unknown**

- Whether the bulletin applies to this VIN range is unconfirmed — the mask is
  the only vehicle identifier kept here.
- No freeze-frame trace has been attached yet, so `evidence_refs` is empty.

**Promotion path**

1. A reviewer confirms the applicability row against the bulletin.
2. The note is merged into the OEM layer with its own `source.licence` and
   `_source_ref` (same rule as `data/diagnostics/dtc_database_oem_layer.json`).
3. Only then may a linked case be promoted into `data/golden_traces/cases/`.
