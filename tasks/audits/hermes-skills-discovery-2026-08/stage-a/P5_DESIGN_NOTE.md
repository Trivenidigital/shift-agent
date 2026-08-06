# P5_DESIGN_NOTE — deferred to the Flyer Studio visual pipeline

**Status:** `DEFERRED_TO_FLYER_STUDIO_VISUAL_PIPELINE`. Not a failed pilot.
**Reason:** `hermes -z` exposes no image, attachment, or multimodal option; no verified
image-ingestion path exists for the text-only harness. Text substitutes were **not** used, since
they would test text reasoning rather than flyer revision QA.

**Preserved and reusable:** `fixtures/P5_flyers/` (1 reference + 8 seeded revisions + 2 clean
controls) and `answer-keys/P5_answer_key.json`, all frozen and hash-validated.

## Intended execution surface

P5 should run through Flyer Studio's **existing** vision/image path, not a new harness.

## Division of authority — deterministic checks are authoritative

| Property | Owner | Authority |
|---|---|---|
| image dimensions | deterministic | **authoritative** |
| aspect ratio | deterministic | **authoritative** |
| exact approved text | deterministic (OCR/string compare) | **authoritative** |
| QR identity / payload | deterministic (decoder) | **authoritative** |
| logo asset identity | deterministic (hash / perceptual hash of the asset region) | **authoritative** |
| addresses, phone numbers | deterministic | **authoritative** |
| prices, offers | deterministic (traced to approved data) | **authoritative** |
| changed-region masks | deterministic (diff + mask) | **authoritative** |
| perceptual / stylistic change | vision model | advisory only |

The visual model identifies **perceptual** changes and explains them. It must never adjudicate an
exact value. Every exact-value verdict comes from deterministic code.

## Prerequisite before P5 can run

An independent QR **decoder** must be available. Until then P5 can test detection of unauthorised
QR-region modification but **cannot** establish payload-level QR validation. No decoder or image
package was installed in this phase.

## Success criteria (unchanged, carried forward)

≥5/6 seeded defects identified and localized; **0** false "unchanged" on a seeded defect; **0**
invented differences on the two clean controls.
