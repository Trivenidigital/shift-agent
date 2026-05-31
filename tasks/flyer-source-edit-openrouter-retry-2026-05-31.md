**Drift-check tag:** extends-Hermes

# Flyer OpenRouter Source-Edit Retry Parity

## New primitives introduced

- No new substrate. This reuses the existing Flyer/OpenRouter provider call shape and adds bounded transient retry behavior to the source-edit branch.

## Hermes-first analysis

Hermes already owns runtime orchestration, provider credential injection, WhatsApp ingress/egress, and audit conventions. Flyer renderer already owns the OpenRouter image-generation call and has a retry loop for transient image-generation reads. The source-edit provider call is the same provider boundary but currently lacks that retry protection.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Provider retry policy | none in Hermes Skills Hub (`https://hermes-agent.nousresearch.com/docs/skills`) | Keep in Flyer renderer provider adapter; use the existing image-generation retry pattern. |
| Source-edit routing/manual queue | existing Flyer/cf-router substrate | No routing or queue change. |
| Messaging | existing deterministic fallback acks | No copy change. |

Awesome Hermes Agent ecosystem check: reviewed `https://github.com/0xNyk/awesome-hermes-agent`; no Flyer-specific provider retry skill applies.

## Drift check

- `src/agents/flyer/render.py::_openrouter_image_bytes()` retries `URLError`, `IncompleteRead`, and `TimeoutError` up to three attempts.
- `src/agents/flyer/render.py::_openrouter_source_edit_bytes()` currently makes one request and fails on the same transient classes.
- `tests/test_flyer_renderer.py` already has an image-generation incomplete-read retry regression and source-edit OpenRouter call shape coverage.

## Plan

- [x] Add RED test: OpenRouter source-edit incomplete read retries and succeeds.
- [x] Add RED test: OpenRouter source-edit transient connection error retries and succeeds.
- [x] Add hard-error guard: OpenRouter source-edit HTTP 400 does not retry.
- [x] Implement bounded retries only for transient classes, matching the existing OpenRouter image-generation adapter. HTTP errors remain fail-fast.
- [x] Run focused renderer tests.
- [x] Multi-vector review.
- [ ] Full verification, PR, merge, deploy.

## Review notes

- Reliability/code-structure review: no blocking issues. Confirmed three bounded attempts, fail-fast HTTP errors, safe request reuse, and no local project mutation before success.
- Hermes/drift/customer-safety review: no blocking issues. Confirmed no Hermes substrate duplication, manual-review routing unchanged, new-flyer path untouched, and plan doc contains required drift/Hermes-first sections.
- Local Claude review: no blocking issues. Noted the post-loop empty-body guard matches the existing image adapter's shape but can attribute an empty success body after a retry to the earlier transient error; fail-closed behavior is preserved. Kept for parity in this slice.
