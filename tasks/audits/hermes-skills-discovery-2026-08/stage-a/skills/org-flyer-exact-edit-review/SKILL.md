---
name: org-flyer-exact-edit-review
description: "Report what changed between an approved flyer and a revision, from a deterministic diff."
version: 0.1.0-stage-a
---
# Flyer revision review (STAGE A — NOT FOR PRODUCTION)

You are given an approved reference, a revision, and a DETERMINISTIC diff report
(dimensions, changed regions, region-level flags).

## Procedure
Report only what the deterministic diff supports, in these categories:
dimensions/aspect ratio · logo · QR · approved text · price/offer · address/phone ·
brand colour · unrequested regions.

## Rules
- You do NOT judge whether a logo or QR "looks correct". You report whether the deterministic
  comparison says it changed.
- Do NOT invent differences the diff does not show. A clean diff means report NO changes.
- Never modify an image.

## Output format
```
changed: [{category, evidence}]
unchanged_verified: [categories]
verdict: CLEAN | DEFECTS_FOUND
```
