---
name: org-catering-approved-data-proposal
description: "Draft a catering proposal strictly from an approved pricebook fixture, with per-claim provenance."
version: 0.1.0-stage-a
---
# Approved-data proposal drafting (STAGE A — NOT FOR PRODUCTION)

You are given an approved pricebook. It is the ONLY source of commercial truth.

## Procedure
1. For every line you propose, cite the exact pricebook field id supporting it.
2. If a required price is absent (`null`), you MUST list it under `unresolved:` and MUST NOT
   estimate, average, interpolate, or infer it.
3. If an item is `available: false`, you MUST NOT offer it. State it is unavailable.
4. If a condition is marked UNRESOLVED, carry it forward as unresolved — do not invent terms.
5. Output is a DRAFT for human review. Never present it as an approved or final quote.

## Output format
```
draft_lines: [{item, price, source_field}]
unresolved: [{what, why}]
excluded: [{item, reason}]
status: DRAFT_FOR_HUMAN_REVIEW
```
