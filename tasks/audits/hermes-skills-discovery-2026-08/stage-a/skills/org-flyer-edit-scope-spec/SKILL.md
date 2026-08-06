---
name: org-flyer-edit-scope-spec
description: "Bounded flyer edit instruction with explicit allowed/frozen element schema."
version: 0.2.0-stage-a
---
# Flyer edit-scope specification (STAGE A — NOT FOR PRODUCTION)

## Output — emit EXACTLY this structure
```
requested_change: <verbatim>
allowed_regions: [<one region>]
allowed_elements: [<only what the request names>]
frozen_elements: [logo, QR, footer address, phone, price, offer, approved headline, dimensions, aspect ratio]
exact_replacement_content: <verbatim or NONE>
dimension_constraints: <explicit>
logo_constraints: MUST NOT be regenerated, redrawn, or re-rendered
qr_constraints: MUST NOT be regenerated or re-encoded
commercial_claim_constraints: no price, offer, product or contact detail may be added or altered
post_edit_validation: [<checks>]
```

## Hard rules
- `frozen_elements` MUST list all nine protected elements every time.
- Every element the request names MUST appear in `allowed_elements`.
- NEVER authorise redesign, regeneration, recreation, or starting from scratch.
- NEVER permit regenerating a logo or QR.
- NEVER fabricate an offer, price, product, or contact detail.
- Preserve exact requested replacement text verbatim.
