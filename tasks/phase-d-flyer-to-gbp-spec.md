# Phase D — Flyer → Google Business Profile post + Instagram caption (spec + offline prototype)

**Drift-check tag:** `extends-Hermes` — adds a deterministic copy composer, one new
cf-router intercept slot, and an additive sidecar state store on top of unmodified
Hermes primitives (bridge chokepoint, JSON state + flock, audit chain, dispatch
chain, YES approval gesture). No Part-1 pattern is violated; no new storage engine,
no parallel code generator, no new approval namespace.

**Status:** DESIGN + OFFLINE PROTOTYPE ONLY (2026-07-06). Hard gates honored:
nothing customer-visible, no external accounts or API calls, GBP/Instagram API
scoping is PAPER-ONLY, prototype reads checked-in fixture copies of real rows
(F0210, F0212) — never the live store.

**New primitives introduced:**
1. `tools/phase-d-prototype/generate_social_drafts.py` — offline deterministic
   composer: locked facts → GBP post body + IG caption, with a mechanical
   copy-contract screen (`screen_draft`).
2. (future, PR-D2+) `social_offers.json` sidecar pending-offer store + one
   cf-router intercept slot + 3 additive `LogEntry` variants. Specified here,
   NOT implemented in this PR.

## Hermes-first capability checklist

Run via `/hermes-check` 2026-07-06 (receipt:
`tasks/.hermes-check-receipts/phase-d-flyer-to-gbp.json`). Per-step:

| # | Step | Tag | Net-new LOC |
|---|---|---|---|
| 1 | Finals delivered + `flyer_assets_delivered` audited | `[Hermes]` — existing send-flyer-package + audit chain | 0 |
| 2 | Offer text in same WhatsApp thread | `[Hermes]` — `bridge_post` chokepoint; net-new is only the call site + copy string | ~10 |
| 3 | Pending-offer marker with 4h TTL | `[net-new]` — new sidecar rows; storage substrate (JSON + flock) is Hermes | ~40 + tests |
| 4 | YES / quoted-YES routing | `[net-new]` — new self-gated intercept slot; dispatch chain + approval gesture are Hermes | ~80 + tests |
| 5 | GBP post composition from locked facts | `[net-new]` — deterministic template (this PR, offline); LLM gateway is the future upgrade slot | ~150 (shipped offline) |
| 6 | IG caption composition | `[net-new]` — same module, marginal | (incl. above) |
| 7 | Copy-contract screening | `[Hermes]`-adjacent — `customer_copy_policy` module deployed; net-new is the authored caption forbidden lists + `screen_draft` | ~30 |
| 8 | Draft-as-text delivery | `[Hermes]` — `actions.send_flyer_text` chokepoint | 0 |
| 9 | Audit rows | `[Hermes]` — `ndjson_append` chokepoint; net-new is 3 additive `LogEntry` variants | ~30 |
| 10 | GBP API posting | `[net-new, deferred]` — external write API, paper-only this phase; `mcp/native-mcp` gate first | 0 this phase |

5 `[Hermes]` / 5 `[net-new]`, every net-new item thin glue on a deployed pattern —
no red flag (the heavy substrate is all Hermes).

### Ecosystem check

| Domain | Hermes skill found? | Decision |
|---|---|---|
| GBP post publishing | none found — skills hub (hermes-agent.nousresearch.com/docs/skills) fetched 2026-07-06 but catalog is JS-rendered and returned no listable entries; 4-source ecosystem audit `tasks/skills-roadmap.md` has zero GBP/Google-Business/social-posting entries (grep-verified: no `instagram\|google business\|GBP\|social\|posting` hits) | build later, phase-gated; per CLAUDE.md external WRITE APIs are genuine net-new — but `mcp/native-mcp` community-MCP check is a MANDATORY gate before any custom OAuth/post LOC (rollout step PR-D4) |
| Instagram caption publishing | none found (same audit) | do NOT build; caption ships as paste-ready text indefinitely (IG Graph API needs Business account + FB Page + app review — violates no-new-accounts) |
| Marketing copy composition | no deterministic composer primitive; Hermes LLM gateway exists for a future upgrade | build deterministic template v0 (this PR, offline); LLM upgrade slots BEHIND `screen_draft` later |
| WhatsApp offer + draft delivery | yes — `bridge_post` chokepoint via `actions.send_flyer_text` (actions.py:5780-5808) | use it |
| YES approval gesture | yes — approval-alias + quoted-reply binding patterns deployed (actions.py:1699-1711, schemas.py:1929-1938) | reuse gesture; new self-gated intercept slot only |
| Pending-choice state + TTL | yes — sidecar-with-flock pattern deployed (`quote_echo_pending.json`, actions.py:39, 3088-3153) | mirror it (`social_offers.json`) |
| Audit | yes — `LogEntry` union + `ndjson_append` chokepoint (schemas.py:5907) | additive variants only |

awesome-hermes-agent ecosystem check: no GBP/Instagram/social-publishing entry in
the 2026-05-03 4-source audit (`tasks/skills-roadmap.md`), and the trap-skill list
doesn't name a social poster either. **Verdict: the entire messaging substrate is
Hermes; the only genuinely new engineering is the deterministic composer (shipped
offline here) and, in a gated future phase, the GBP external-write API.**

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/scripts/send-flyer-package` (full script; delivered
  transition :454-510, `FlyerAssetsDelivered` append :503-510, trial-upsell
  sibling send :511-523) before placing the offer-emission hook.
- ✅ Read `src/plugins/cf-router/hooks.py` (dispatch order :243-659; quote-echo
  choice :361-367; approval-text block :377-382; quote-echo guard :388;
  delivery-state guard :2770-2857) before naming the intercept position.
- ✅ Read `src/plugins/cf-router/actions.py` (`_FLYER_APPROVAL_ALIASES` :1699-1710,
  quote-echo pending store :39 + :3088-3153, `send_flyer_text` :5780-5808,
  `finalize_and_send_flyer` :5825-5841) before designing YES binding + sidecar.
- ✅ Read `src/platform/schemas.py` (`FlyerWorkflowStatus` :588-606,
  `FLYER_TRANSITIONS` :813-836, `FlyerLockedFact` :1664-1688, `FlyerProject` +
  occasion ruling :1884-1984, `LogEntry` union :5907) before the schema-rows
  decision.
- ✅ Read `src/agents/flyer/customer_copy_policy.py` (banned terms :15-29,
  completion verbs :79-97, `lint_no_unverified_completion` :169) before authoring
  the caption vocabulary and wrapper-copy rules.
- ✅ Read `tests/test_flyer_qa_hardening.py` (header + fixtures) before writing
  the golden pin test.

---

## 1. Feature (operator-scoped)

After a flyer's finals package is delivered, offer in the same WhatsApp thread:
*"Want this flyer on your Google Business Profile too? Reply YES and I'll prepare
a ready-to-paste Google post and Instagram caption."* On YES within TTL, deliver
(a) a GBP post draft (body text; the flyer's `final_instagram_post` square image
is the post photo the owner attaches), (b) an IG caption — both as plain TEXT the
owner pastes. REUSE EVERYTHING: locked facts (ZERO new fact extraction), the
existing delivery loop, the YES/quoted-YES approval gesture.

## 2. Copy contract (the bright line, extended verbatim)

Captions contain **ZERO claims beyond locked facts**. Concretely:

- Every content word in a draft comes from a `locked_facts` value of the SAME
  project. Allowed transformations: whitespace/punctuation reshaping, casing,
  concatenation, list joining, hashtag slugging (`hashtag_slug` — fact value with
  apostrophes removed, non-alnum split, title-cased). No synonyms, no elaboration.
- The ONLY non-fact text is the authored connective vocabulary
  (`ALLOWED_CONNECTIVES = {"at", "menu", "call"}` + punctuation/`#`). Extending it
  is a copy-contract change reviewed together with the forbidden lists.
- **Leak law:** the vocabulary ships WITH its forbidden-substrings at authoring
  time, in the same file (`generate_social_drafts.py`):
  - `FORBIDDEN_SUBSTRINGS_JARGON` — operator/internal terms (extends
    `customer_copy_policy.BANNED_CUSTOMER_COPY_TERMS`, customer_copy_policy.py:15-29).
    Screened against the FULL draft — a fact value containing jargon is a poisoned
    fact and must block.
  - `FORBIDDEN_SUBSTRINGS_CLAIMS` — unverified-claim vocabulary ("best",
    "authentic", "guaranteed", dietary claims, urgency claims…). Screened against
    the RESIDUE only (text left after stripping fact values + fact-derived slugs):
    facts are the licensed claims; the composer may never add one.
- **Mechanical enforcement — `screen_draft(text, row)` residue check:** strip
  every fact value and slug, then every remaining word must be an authored
  connective. This is the screen a future LLM composer runs behind: LLM output
  failing `screen_draft` is discarded in favor of the deterministic template.
  (LLM upgrade slot: swap `compose_*` implementations; the screen and goldens
  stay.)
- **Completion-verb discipline:** all wrapper copy (offer message, draft
  delivery message) must pass `lint_no_unverified_completion`
  (customer_copy_policy.py:169) — never "posted"/"sent"/"scheduled"
  (FORBIDDEN_COMPLETION_VERBS, customer_copy_policy.py:79-97). We prepare drafts;
  the OWNER posts. Wrapper copy says "ready to paste", never claims the post
  happened. This also keeps the feature outside the regulated-action firewall's
  completion-claim class.

## 3. Offer flow state machine

No `FlyerWorkflowStatus` change and no `FLYER_TRANSITIONS` edit (schemas.py:588-606,
813-836). The project stays `delivered` (its existing exits `completed` /
`revising_design`, schemas.py:829, are untouched). Offer lifecycle lives in a
**sidecar store** `/opt/shift-agent/state/flyer/social_offers.json`, keyed by
`chat_id`, mirroring the deployed quote-echo pending pattern
(`FLYER_QUOTE_ECHO_PENDING_PATH`, actions.py:39; save/get/pop with flock +
`atomic_write_json` + TTL freshness, actions.py:3088-3153).

```
delivered finals sent
        │  (same send-flyer-package invocation, after FlyerAssetsDelivered)
        ▼
   OFFERED ── row {project_id, offer_message_id, offered_at, expires_at=+4h}
        │ YES / quoted-YES on offer message, within TTL
        ▼
   CLAIMED ── compose drafts from locked facts → screen_draft → deliver as text
        │ success
        ▼
   DRAFTS_DELIVERED (terminal; row kept with delivered mids for idempotency)

  any other reply → fall through untouched (offer stays pending until TTL)
  TTL expiry      → row pruned lazily on next store access (quote-echo semantics);
                    NO reminder, NO re-offer for the same project
```

Decisions:
- **TTL = 4h**, matching the platform proposal-TTL convention and quote-echo.
- **YES binding:** bare `yes` (already in `_FLYER_APPROVAL_ALIASES`,
  actions.py:1699-1710) or swipe-reply YES whose `quotedMessageId` equals the
  stored `offer_message_id` (precedent: `preview_message_ids` quoted-APPROVE
  binding, schemas.py:1929-1938; raw-body capture hooks.py:267-270).
- **Ambiguity rule:** `delivered` is NOT in `_FLYER_FINAL_APPROVAL_STATUSES`
  (actions.py:1711), so today a post-delivery YES falls through the approval
  intercept — that unclaimed gesture is exactly what this feature claims. If the
  same sender ALSO has a project awaiting final approval, final-approval wins
  (intercept ordering, §4); a quoted-YES on the offer message still binds to the
  offer because the quote disambiguates.
- **One offer per project**, fired only on the delivery invocation that performs
  the `delivered` transition (not on `retry_send_flyer_package` re-entries where
  the project is already delivered) — idempotent by construction plus the
  sidecar row check.

### Schema rows: sidecar, not a FlyerProject field (occasion-precedent applied)

The occasion ruling (schemas.py:1884-1891) gave `occasion` a project-level field
because it is render-scoped, write-once, a closed enum with a fail-neutral
default. The pending offer is the opposite: mutable post-delivery lifecycle state
with TTL and message ids. Embedding it in `FlyerProject` (`extra="forbid"`) is
rollback-hazardous — an old schema rejects unknown keys on load, the exact hazard
documented at `creative_direction` (schemas.py:1953-1969) — and the deployed home
for pending-choice state is already a sidecar (`quote_echo_pending.json`).
**Decision: sidecar store.** (Additive-field alternative considered and rejected
for the rollback reason; revisit only if the offer ever needs to survive
`projects.json`-level replay.)

New audit rows (additive `LogEntry` variants, union at schemas.py:5907, deployed
additive pattern e.g. schemas.py:6088-6090):
`flyer_social_offer_sent`, `flyer_social_offer_claimed`,
`flyer_social_draft_delivered` — each with `project_id`, `customer_phone`,
message ids. Emitted via the existing `ndjson_append` chokepoint.

## 4. Integration-point map (file:line, verified in this worktree @ origin/main aff7251)

| Hook | Where | What |
|---|---|---|
| Offer emission | `src/agents/flyer/scripts/send-flyer-package` `_run_delivery`, immediately after the `FlyerAssetsDelivered` append at :503-510, as a SIBLING of the trial-upsell text send at :511-523 (same shape: post-delivery `bridge_post`, dry-run aware, failure recorded but never fails the delivery) | send offer text; write OFFERED sidecar row with the returned mid; append `flyer_social_offer_sent` |
| Why script-side, not cf-router-side | `actions.finalize_and_send_flyer` (actions.py:5825-5841) is only ONE caller of the script; the flyer_dispatcher SKILL and recovery paths also invoke `/usr/local/bin/send-flyer-package`. The script's delivered-transition block (:454-510) is the single chokepoint where "finals actually delivered" is known | offer can never fire without a real delivery |
| YES intercept slot | `src/plugins/cf-router/hooks.py` — new `_try_flyer_social_offer_choice_intercept(text, chat_id, event)` inserted AFTER the approval-text active-project block at hooks.py:377-382 and BEFORE `_try_flyer_quote_echo_guard` at hooks.py:388 | position rationale: (a) final-approval YES for a concurrent pre-delivery project must win (ambiguity rule §3); (b) must run before quote-echo/intake so a bare YES is never misread as a brief; (c) self-gates on a fresh sidecar row exactly like `_try_flyer_quote_echo_choice` (hooks.py:361-367) so it is inert for everyone without a pending offer |
| Draft delivery | `actions.send_flyer_text` chokepoint (actions.py:5780-5808) with a non-regulated `action_context`, two messages: GBP draft (+ pointer to the already-delivered `final_instagram_post` image as the photo to attach), IG caption | wrapper copy passes `scan_customer_text` + `lint_no_unverified_completion` |
| Composer | promote `tools/phase-d-prototype/generate_social_drafts.py` → `src/agents/flyer/social_drafts.py` at PR-D3, goldens carried along as replay fixtures | pure functions; no store writes |
| Flag | new `FlyerSocialOfferConfig` in schemas.py next to `FlyerRecoveryConfig` (schemas.py:843) — `enabled: bool = False` + pilot allowlist, reusing the premium-poster scoped-enable precedent | default-off, byte-identical behavior when off |

## 5. GBP / Instagram API — PAPER-ONLY scope (no accounts, no calls, this phase)

To-verify at PR-D4 time (knowledge cutoff caveat; quotas/endpoints move):

- **GBP posts** live on the LEGACY Google My Business API **v4.9**
  `accounts.locations.localPosts.create` (post body `summary` ≤ 1500 chars —
  prototype enforces this cap — plus `callToAction {actionType: CALL, …}` and
  `media`). The newer split Business Profile APIs do NOT cover local posts;
  **Business Profile Performance API** is metrics-only (relevant later for
  "your post got N views" reporting, not for posting).
- **OAuth:** GCP project + OAuth 2.0 scope `https://www.googleapis.com/auth/business.manage`;
  GBP API access request form required — default quota is 0 until Google approves
  the project. Practical quota after approval is low tens of QPM on edit calls —
  fine for this fleet.
- **Account model (operator decision, gates PR-D4):** one operator-owned GCP
  app; each BUSINESS OWNER grants OAuth consent for their own GBP location
  (consent link deliverable over the same WhatsApp thread). Owner-consent is an
  account action → outside the no-new-accounts constraint of THIS phase, so the
  entire API leg stays paper until the operator decides.
- **Instagram:** Graph API content publishing requires an IG Business/Creator
  account linked to a Facebook Page + `instagram_content_publish` app review.
  Materially heavier than GBP. Decision: caption stays paste-ready text
  indefinitely; no IG API phase is scheduled.
- **`mcp/native-mcp` gate:** before ANY custom GBP OAuth/post code, check the
  community MCP ecosystem for a maintained GBP server (skills-roadmap escape
  hatch). Estimated custom LOC if none: ~250 (OAuth token store + one POST
  wrapper + §12b alert on post failure).

## 6. Ordered rollout

1. **PR-D1 (this PR):** spec + offline prototype + fixtures + goldens + pin test.
   Zero runtime surface.
2. **PR-D2 — offer flag, pilot-scoped:** `FlyerSocialOfferConfig` (default OFF) +
   offer emission in send-flyer-package delivered branch + `social_offers.json`
   sidecar + `flyer_social_offer_sent` audit. Deploy flag-off; enable for
   +17329837841 only.
3. **PR-D3 — YES intercept + draft-as-text:** intercept slot (§4 position) +
   composer promotion with goldens + `flyer_social_offer_claimed` /
   `flyer_social_draft_delivered` audits + replay fixtures for YES-inside-TTL,
   YES-after-TTL, quoted-YES, concurrent-approval ambiguity.
4. **PR-D4 — GBP API phase (GATED):** blocked on (a) operator account-model
   decision (§5), (b) `mcp/native-mcp` investigation, (c) OAuth consent UX.
   Approval stays YES-per-post; auto-posting failure alerts at the write site
   (§12b); any post queue table ships with freshness watchdog (§12a).

## 7. Prototype (shipped in this PR)

`tools/phase-d-prototype/`:
- `fixtures/F0210.json`, `fixtures/F0212.json` — checked-in FIXTURE COPIES of the
  two real delivered rows (fetched read-only from the live store 2026-07-06;
  the prototype never reads the live store).
- `generate_social_drafts.py` — composer + `screen_draft` + authored vocab and
  forbidden lists (§2).
- `golden/F0210-gbp-post.txt`, `golden/F0210-ig-caption.txt`,
  `golden/F0212-gbp-post.txt`, `golden/F0212-ig-caption.txt` — committed output.
- Pinned by `tests/test_phase_d_social_draft_golden.py` (7 tests: byte-exact
  goldens ×2, contract screen on goldens ×2, screen catches
  jargon/claims/non-fact words, refuses non-delivered rows, vocab/forbidden
  lists authored together).

Run: `python tools/phase-d-prototype/generate_social_drafts.py --fixture tools/phase-d-prototype/fixtures/F0210.json --out <dir>`
