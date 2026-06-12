**Drift-check tag:** extends-Hermes

# Flyer Studio — Edit / Provider Backlog (reorganized 2026-05-30)

**Purpose.** Stop re-litigating "OpenRouter vs OpenAI for edits." The provider
split is **already implemented in code**. The recurring back-and-forth comes from
mixing three different layers under the same name — *provider choice*, *provider
availability*, and *workflow quality/safety*. This doc reorganizes the open work
into three non-overlapping buckets so the same item stops getting renamed.

**Verified against `origin/main` `90e61ae` (2026-05-30).** The `tasks/todo.md`
backlog lags the code badly (multiple "open" items are shipped) — treat THIS doc,
grounded in file:line evidence, as the source of truth for the edit/provider area.

## Hermes-first analysis

| Domain | Hermes owns it? | Decision |
|---|---|---|
| Draft/final image generation | yes — LLM/image gateway via OpenRouter | use it (implemented) |
| Source-preserving edits | partial — OpenAI image-edit API behind config | provider-specific integration (implemented) |
| Provider config + routing | yes — config knobs + dispatch | use it (implemented) |
| Provider availability (creds) | n/a — operator-set env | operator action, not code |

Net-new remaining is only the operator-readiness glue and a spend-gated quality
smoke. The provider split itself is settled — no new product decision is open.

---

## The settled decision — DO NOT re-open

**Provider split BY WORKFLOW is implemented.** "Use OpenRouter" and "use OpenAI
for edits" are not competing options; they are the two halves of the same
implemented split:

| Workflow | Provider | Code |
|---|---|---|
| Draft + final generation | OpenRouter | `src/agents/flyer/render.py:1524` `_openrouter_image_bytes` |
| Source-preserving edits | OpenAI image-edit | `src/agents/flyer/render.py:1736` `_openai_source_edit_bytes` → `render_source_edit_preview` (`:2362`) |
| Config knobs (separate) | — | `src/platform/schemas.py:916-920` `draft_image_model` / `final_image_model` / `edit_image_model` (default `gpt-image-1`); `_legacy_provider_for_model` maps model→provider |
| Routing | — | `generate-flyer-concepts` routes source-edit requests to `render_source_edit_preview` using `cfg.flyer.edit_image_model` |

If a future item says "decide the provider for edits," it is a duplicate of a
closed decision — close it and point here.

---

## Bucket 1 — Provider split  ·  STATUS: DONE (reference only)

Shipped; listed so the decision stays closed. No open work.

- [x] OpenRouter generation path (`_openrouter_image_bytes`).
- [x] OpenAI source-edit path (`_openai_source_edit_bytes`, `render_source_edit_preview`).
- [x] Separate `draft/final/edit_image_model` config knobs + model→provider mapping.
- [x] Source-edit routing wired in `generate-flyer-concepts`.

## Bucket 2 — Provider readiness  ·  STATUS: OPERATOR / CREDENTIAL (not code)

"Is the chosen provider actually available on the box?" — env/skill config the
code already degrades around. None of these are code slices.

- [ ] **`OPENAI_API_KEY` on main-vps** — enables the source-edit path. Without it
  the code correctly routes to `manual_edit_required` (no silent failure).
  Confirmed unset at last deploy (`credential-minimized-readiness`: `OPENAI_API_KEY: unset`).
- [ ] **Hermes OCR skill enabled on main-vps** — enables reference-menu
  extraction (`reference_extract` providers; abstract base is intentional).
- [ ] (verify) provider routing health surfaced in `credential-minimized-readiness`
  / pilot-readiness report so an unset edit provider is visible pre-incident.

## Bucket 3 — Workflow hardening  ·  STATUS: mostly SHIPPED; one spend-gated gate open

Quality/safety of the edit workflow, independent of which provider runs it.

- [x] Revision parsing + state-safety edge cases — regressions shipped
  (F0023/F0024/F0029 covered in `test_cf_router_flyer_routing.py`,
  `test_flyer_golden_scenarios.py`).
- [x] Operator-visible manual-edit behavior — `manual_edit_required` per-state
  status replies via `_select_flyer_status_reply` (state→reply table).
- [x] Source-edit provider preflight (deploy/startup awareness) —
  `test_flyer_source_edit_preflight.py` present.
- [x] Send-time format-truthfulness + downgrade observability (PRs #339/#351).
- [ ] **Spend-gated 5–10 case source-edit visual-quality smoke** — the ONE real
  remaining gate before treating automated OpenAI source edits as customer-grade.
  Operator-authorization-gated (cost). Blocks "green" rollout posture on the
  source-edit path only; everything else is already customer-safe.
- [ ] (spend-gated) real-model golden eval — `test_flyer_golden_scenarios_real_model.py`
  skipped pending budget; final pre-broad-launch confidence gate.

---

## Conclusion

The only genuine product decision in this area — provider split — is **closed in
code**. What remains is:
1. **Provider readiness** (Bucket 2): operator sets `OPENAI_API_KEY` + Hermes OCR.
2. **One spend-gated quality smoke** (Bucket 3): operator authorizes the cost.

No new code slice is required to settle the provider question. Future backlog
churn on "OpenRouter vs OpenAI edits" should be closed as a duplicate of the
settled decision above.
