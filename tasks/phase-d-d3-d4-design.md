# Phase D — PR-D3 & PR-D4 design (YES → draft-as-text; GBP API posting)

**Drift-check tag:** `extends-Hermes` — PR-D3 adds one self-gated cf-router
intercept slot + a sidecar pending-offer store + additive `LogEntry` variants +
promotes the deterministic composer into `src/`, all on top of unmodified
Hermes primitives (dispatch chain, approval gesture, JSON+flock sidecar, audit
chokepoint, `send_flyer_text` delivery). PR-D4 adds the external GBP write
boundary behind the §3 account decision. No Part-1 pattern is violated; no new
storage engine, no parallel approval namespace, no new fact extraction.

**Status:** DESIGN ONLY (2026-07-06). Refreshes the integration-point map of
`tasks/phase-d-flyer-to-gbp-spec.md` (§4, verified there @ origin/main
`aff7251`) against **current main `e908c39`** — line numbers shifted after #565
(stale-quote-on-delivered) and #566 (census hygiene). No runtime code in this
PR. GBP API facts + the account-model decision live in
`tasks/phase-d-gbp-api-scoping.md`.

## Hermes-first capability checklist

| Step | Tag | Net-new |
|---|---|---|
| Offer emission after delivery (PR-D2, recapped) | `[Hermes]` | sibling of the trial-upsell send; net-new is the call site + copy string |
| Pending-offer sidecar (+4h TTL) | `[net-new]` thin | mirrors the deployed `quote_echo_pending.json` flock+TTL pattern |
| YES / quoted-YES intercept | `[net-new]` | one self-gated intercept slot; dispatch chain + approval gesture are Hermes |
| Compose GBP post + IG caption | `[net-new]` thin | composer shipped offline (#564); D3 promotes it into `src/`, no logic change |
| Copy-contract screen | `[Hermes]` | `screen_draft` + authored vocab shipped (#564); wrapper copy reuses `customer_copy_policy` |
| Draft-as-text delivery | `[Hermes]` | `send_flyer_text` chokepoint |
| Audit rows | `[Hermes]` | `ndjson_append` chokepoint; net-new is 3 additive `LogEntry` variants |
| GBP posting (PR-D4) | `[net-new]` gated | external write; `mcp/native-mcp` gate + §3 account decision first |

### Ecosystem check

Same result as the spec (#564) and the scoping doc: the messaging/approval/
audit/storage substrate is entirely Hermes; the only genuinely new engineering
is the intercept glue (D3) and the external GBP write (D4). No Hermes or
community skill covers GBP/IG posting (`tasks/skills-roadmap.md`, grep-verified
zero social-posting entries). `mcp/native-mcp` community-MCP check is a
mandatory gate before any D4 custom OAuth/POST LOC.

## Drift-rule self-checks (deployed code READ before drafting, current main)

- ✅ Read `src/agents/flyer/scripts/send-flyer-package` — `_run_delivery` def
  :300; `delivered` transition-legality guard :478 + write :497-501;
  `FlyerAssetsDelivered` append :503-510; trial-upsell sibling send :511-523
  (error captured to a field, never raises — the emission-hook template).
- ✅ Read `src/plugins/cf-router/hooks.py` — flyer dispatch block opens :351;
  the `_try_flyer_*` ordering (full list in §D3.1); approval-text active-project
  block :377-382 (call :380); quote-echo guard :388; delivery-state guard call
  :434 / def :2770; raw-body capture :270.
- ✅ Read `src/plugins/cf-router/actions.py` — `_FLYER_APPROVAL_ALIASES`
  :1699-1709; `_FLYER_FINAL_APPROVAL_STATUSES` :1711 (`delivered` deliberately
  absent); quote-echo pending path const :39 + save :3142 / get :3192 / pop
  :3201; `extract_quoted_message_id` :2861 (reads `hasQuotedMessage` :2886 /
  `quotedMessageId` :2888); `resolve_flyer_binding_project` :2956
  (binding_source assigned :2980/:2987/:2994; #565 `stale_quote_approve_fallback`
  :2997); `send_flyer_text` :5827; `finalize_and_send_flyer` :5878;
  `find_flyer_project_by_quoted_mid` :2914 / `_flyer_project_outbound_mids`
  :2896.
- ✅ Read `src/platform/schemas.py` — `FlyerWorkflowStatus` :588-606;
  `FLYER_TRANSITIONS` :813; `FlyerRecoveryConfig` :843 (mounted on `FlyerConfig`
  :919; `FlyerConfig.enabled` :890; `Config.flyer` :3045); `FlyerLockedFact`
  :1664; `FlyerProject` :1894 (occasion field :1901 + ruling :1884-1890;
  `creative_direction` rollback-hazard :1958-1969); `preview_message_ids` :1938
  (+ quoted-APPROVE binding comment :1929-1937); `LogEntry` union :5917;
  additive-variant template `FlyerAssetsDelivered` :3837.
- ✅ Read `src/agents/flyer/customer_copy_policy.py` — banned terms :15-29;
  completion verbs :79-97; `lint_no_unverified_completion` :169.
- ✅ Read `src/agents/flyer/render.py` — allowlist plumbing post-#554
  (explicit-allow / empty=disabled): `_premium_overlay_allowlist` :3756 +
  `_premium_overlay_enabled` :3763 — the mirror for the D3 scoped-enable gate.
- ✅ Read `src/platform/safe_io.py` — `atomic_write_json` + `flock` helpers, the
  sidecar-store substrate the `social_offers.json` helpers reuse.
- ✅ Read `tests/test_phase_d_social_draft_golden.py` — the golden suite the D3
  composer-promotion repoints (byte-exact goldens, contract screen, awaiting-gate
  refusal, occasion non-leak, F0213 lossy-shape pin).
- ✅ Confirmed (grep-clean) NO Phase-D runtime exists yet: no
  `FlyerSocialOfferConfig`, no `social_offers.json`, no
  `src/agents/flyer/social_drafts.py`; only `tools/phase-d-prototype/` is
  present.

### Anchor delta vs spec §4 (@ aff7251 → e908c39)

| Anchor | spec §4 line | current main line |
|---|---|---|
| `send_flyer_text` | 5780-5808 | **5827** |
| `finalize_and_send_flyer` | 5825-5841 | **5878** |
| quote-echo pending save/get/pop | 3088-3153 | **3142 / 3192 / 3201** |
| `_FLYER_FINAL_APPROVAL_STATUSES` | 1711 | **1711** (unchanged; now includes `delivered_with_warning`) |
| approval-text active-project block | 377-382 | **377-382 (call :380)** |
| quote-echo guard | 388 | **388** |
| `FlyerProject.occasion` | 1884-1891 | field **:1901**, ruling **:1884-1890** |
| `preview_message_ids` | 1929-1938 | field **:1938**, comment **:1929-1937** |
| `LogEntry` union | 5907 | **5917** |
| delivery-state guard | 2770-2857 | def **:2770**, call **:434** |

The `resolve_flyer_binding_project` / `stale_quote_approve_fallback` path
(actions.py:2956-2997) is NEW since the spec was written (#565) and materially
affects D3 intercept ordering — see §D3.2.

---

## PR-D3 — YES intercept + draft-as-text delivery + composer promotion

**Prerequisite (PR-D2, recap):** the delivered branch of `send-flyer-package`
`_run_delivery` (:300), as a sibling of the trial-upsell send (:511-523),
emits the offer text and writes an `OFFERED` row to the sidecar
`social_offers.json` (mirroring `quote_echo_pending.json`), keyed by `chat_id`,
holding `{project_id, offer_message_id, offered_at, expires_at=+4h}`, and
appends `flyer_social_offer_sent`. D2 ships flag-off; D3 activates the claim leg.

### D3.1 YES intercept slot — exact position (current hooks.py)

Insert `_try_flyer_social_offer_choice_intercept(text, chat_id, event)`
**immediately after the approval-text `_try_flyer_active_project_intercept` call
(hooks.py:380) and before `_try_flyer_quote_echo_guard` (hooks.py:388).** The
current `_try_flyer_*` order for context:

```
354 campaign_cta      368 account            374 regulated_account_guard
361 quote_echo_choice 371 sample_prompt      380 active_project (approval/send-now)
      >>> INSERT _try_flyer_social_offer_choice_intercept HERE <<<
388 quote_echo_guard  391 intake             394 reference_scope_choice
... 427 active_project  434 delivery_state_guard  446/455 primary
```

Position rationale (unchanged in intent from spec §4, re-verified against the
current chain):

- **(a) Final-approval wins.** `active_project` at :380 self-gates on a project
  in `_FLYER_FINAL_APPROVAL_STATUSES` (actions.py:1711 — `delivered` is
  deliberately NOT in the set). So a concurrent pre-delivery project's approval
  YES is claimed at :380 before the social-offer intercept ever runs — the
  spec §3 ambiguity rule, preserved.
- **(b) Before intake/quote-echo.** Running before :388/:391 means a bare `yes`
  is never misread as a brief or an intake answer.
- **(c) Self-gated, inert by default.** The intercept returns `None` unless
  `get_social_offer_pending(chat_id)` yields a fresh (non-expired) `OFFERED`
  row — exactly the self-gate shape of `_try_flyer_quote_echo_choice`
  (hooks.py:361). Everyone without a pending offer is unaffected; behavior is
  byte-identical when the feature is off.

### D3.2 Interaction with #558 quoted-APPROVE + #565 stale-quote fallback (NEW)

This is the material change since the spec. `resolve_flyer_binding_project`
(actions.py:2956) now binds a quoted APPROVE to a project via
`preview_message_ids`, and #565 added `_bind_override_strands_approval` →
`stale_quote_approve_fallback` (actions.py:2996-2997) so a quoted-YES on an
**already-delivered** project's preview does NOT re-bind. The social-offer
intercept must own the offer-YES BEFORE that path:

- The offer's own `offer_message_id` is stored in `social_offers.json`, and it
  is **not** a member of any project's `preview_message_ids`. A **quoted-YES on
  the offer message** is matched by the social-offer intercept against the
  stored `offer_message_id` (reusing `extract_quoted_message_id`,
  actions.py:2861) and claimed at position ~381 — **before** the flyer
  approval-binding path at :427/:434 runs. So an offer-YES can never be
  misrouted into #565's `stale_quote_approve_fallback`.
- Conversely, a swipe-reply APPROVE quoting a delivered project's *preview*
  (the F0212/F0213-class incident #565 fixed) does NOT carry the offer mid, so
  the social-offer intercept declines it and the existing #565 fallback still
  governs. The two features are disjoint by the mid they match on. **This
  disjointness is the load-bearing invariant and gets an explicit test
  (§D3.6).**

### D3.3 Draft-as-text delivery

On a claimed YES within TTL:

1. Load the project row; recompose GBP post + IG caption from `locked_facts`
   via the promoted composer (§D3.4). Run `screen_draft` again at send time —
   never trust a stale draft.
2. Deliver via `send_flyer_text` (actions.py:5827) with a **non-regulated**
   `action_context`, as TWO messages: (i) the GBP post body + a one-line
   pointer that the already-delivered `final_instagram_post` square image is
   the photo to attach; (ii) the IG caption.
3. Wrapper copy (offer message + draft-delivery messages) MUST pass
   `scan_customer_text` + `lint_no_unverified_completion`
   (customer_copy_policy.py:169) — it says "ready to paste," never "posted /
   sent / scheduled" (FORBIDDEN_COMPLETION_VERBS :79-97). We prepare; the OWNER
   posts. This also keeps the feature outside the regulated-action firewall's
   completion-claim class.
4. Write `flyer_social_offer_claimed` then `flyer_social_draft_delivered`;
   transition the sidecar row `CLAIMED → DRAFTS_DELIVERED` (kept with delivered
   mids for idempotency).

### D3.4 Composer promotion

Promote `tools/phase-d-prototype/generate_social_drafts.py` →
**`src/agents/flyer/social_drafts.py`** (pure functions: `compose_gbp_post`,
`compose_ig_caption`, `screen_draft`, `hashtag_slug`, `_fact_map`,
`_item_names`, the vocab + forbidden-list constants + the char caps). No logic
change — it is the reviewed, golden-pinned code from #564.

- The intercept + `send-flyer-package` import from the `src` module; no store
  writes happen inside the pure functions.
- **Goldens travel as the replay corpus.** The five fixtures (F0209, F0210,
  F0212, F0213, F0214) + their goldens move under `tests/` (e.g.
  `tests/fixtures/social_drafts/`) or stay in `tools/phase-d-prototype/`;
  `tests/test_phase_d_social_draft_golden.py` repoints its import to
  `src.agents.flyer.social_drafts` and keeps every existing assertion (byte-
  exact goldens, contract screen, delivered-gate refusal on the real awaiting
  F0214 row, occasion non-leak, F0213 lossy-shape pin). The thin CLI in
  `tools/` either stays as a wrapper importing the src module or is removed.
- **Carry the D3 gaps (from the golden expansion, do NOT hack here):** F0213
  surfaced that (1) a missing `campaign_title` fact degrades the headline to
  the bare business name, and (2) per-item prices are dropped (Menu line is
  names-only) so a non-uniform menu loses per-item pricing and a nounless
  `pricing_structure` renders as a bare "$12.99". Both are fact-safe by
  omission (screen-clean). A v0 composer polish MAY surface item prices and a
  nounless price more gracefully — but any change regenerates the goldens as a
  deliberate review moment (leak law). Tracked as D3 composer-polish items, not
  a prototype hack.

### D3.5 Sidecar + audit + config

- **Sidecar** `/opt/shift-agent/state/flyer/social_offers.json` — new helpers
  `save/get/pop_social_offer_pending` mirroring
  `save/get/pop_flyer_quote_echo_pending` (actions.py:3142/3192/3201): flock +
  `atomic_write_json` + lazy TTL prune. Keyed by `chat_id`. NOT a `FlyerProject`
  field — the occasion ruling (schemas.py:1884-1890) gave `occasion` a project
  field because it is render-scoped/write-once/closed-enum; the pending offer is
  the opposite (mutable post-delivery lifecycle + TTL + message ids), and
  embedding it in `FlyerProject` (`extra="forbid"`) is the `creative_direction`
  rollback hazard (schemas.py:1958-1969). Sidecar, per the deployed pending-
  choice home.
- **Audit** — three additive `LogEntry` variants subclassing `_BaseEntry`,
  added to the union at schemas.py:5917, mirroring `FlyerAssetsDelivered`
  (schemas.py:3837): `flyer_social_offer_sent`, `flyer_social_offer_claimed`,
  `flyer_social_draft_delivered` — each with `project_id`, `customer_phone`,
  message ids. Emitted via `ndjson_append`.
- **Config / kill-switch** — new `FlyerSocialOfferConfig` mounted on
  `FlyerConfig` next to `recovery` (schemas.py:919), `enabled: bool = False`
  (byte-identical behavior when off). **Per-phone scoping is an ENV allowlist,
  NOT a config field** (the #554 unification moved scoping to ENV explicit-allow
  / empty=disabled): add `_social_offer_enabled(project)` mirroring
  `_premium_overlay_enabled` (render.py:3763) — flag ENV must be `"1"` AND the
  normalized `customer_phone` must be in the ENV allowlist (empty ⇒ DISABLED).
  Pilot-enable for `+17329837841` only. The config `enabled` flag is the global
  master; the ENV allowlist is the per-number gate — both must be affirmative.

### D3.6 Test plan (D3)

Deterministic Python tests (matching `test_catering_v02_scripts.py` +
`test_phase_d_social_draft_golden.py` styles):

- **Composer/goldens** — the existing golden suite, repointed to the src module
  (byte-exact ×5 fixtures, screen-clean, awaiting-gate refusal, occasion
  non-leak, F0213 lossy-shape pin). Already green in this PR.
- **Sidecar** — save/get/pop round-trip; TTL expiry prunes lazily; one-offer-
  per-project idempotency (a second delivery invocation does not double-offer).
- **Intercept ordering (the load-bearing set)** — replay fixtures for:
  YES-inside-TTL claims + delivers; YES-after-TTL falls through untouched;
  quoted-YES on the offer message binds to the offer; **concurrent-approval
  ambiguity** (a pending final-approval project + a pending offer → the :380
  active-project intercept wins, offer stays pending); **disjointness from
  #565** (a quoted-APPROVE on a delivered project's preview mid is NOT claimed
  by the social intercept and still hits `stale_quote_approve_fallback`).
- **Wrapper copy** — offer + draft-delivery strings pass
  `lint_no_unverified_completion` + `scan_customer_text`.
- **Flag-off parity** — `enabled=False` (or empty ENV allowlist) ⇒ no offer
  emitted, no intercept fires, dispatch output byte-identical.

### D3.7 Rollout / kill-switch (D3)

Deploy flag-off; enable ENV allowlist for `+17329837841` only. Kill-switch =
flip `enabled` off (or empty the ENV allowlist) — no redeploy needed for the
ENV path (CLIs read config/env fresh). The intercept self-gates on the sidecar,
so disabling emission drains offers within one TTL (4h) with no re-offer.

---

## PR-D4 — GBP API posting (GATED)

Blocked on, in order: (a) the §3 operator account-model decision
(recommend manager-access), (b) the `mcp/native-mcp` community-MCP investigation
(a maintained GBP MCP server would replace most custom LOC), (c) the GBP Basic
API Access approval + OAuth app verification (§2 of the scoping doc), (d) the
§1.3 media fork (public image host vs manual photo attach). None of these gate
D2/D3.

### D4.1 Shape (if custom, post-`mcp/native-mcp`)

- **Credential:** a real Google identity's `business.manage` token (manager of
  the location under flow (a), or the owner's own token under flow (b)) — never
  a service account (scoping §2, impossible). Store the refresh token in a
  flock+`atomic_write_json` sidecar per VPS (Hermes storage substrate), mode
  600, service-user owned.
- **Post:** `POST mybusiness.googleapis.com/v4/accounts/{a}/locations/{l}/localPosts`
  with `summary` = the D3 GBP-post text (≤ the verified cap, guard ≤1500),
  `topicType=STANDARD`, `callToAction.actionType=CALL` (or omitted). Photo per
  the scoping §1.3 fork.
- **Approval:** YES-per-post via the existing gesture — the D3 draft-delivery
  message gains a "reply POST to publish to Google" affordance bound like the
  offer YES; auto-posting is never silent.

### D4.2 Silent-failure discipline (from scoping §6)

- **§12b** — `localPosts.create` failure alerts at the write site, plain text
  (`parse_mode=None`), with `*_alert_dispatched`/`*_alert_delivered` logs.
- **§12a** — any post-queue/outcome table ships with a freshness watchdog in
  the SAME PR.
- **Idempotency** — per-(project, location) key so a retry never double-posts.
- **Revocation = fail-closed** — expired/revoked manager access or token →
  "cannot post, owner action needed," never a silent skip; completion-verb
  discipline holds until the API returns success.

### D4.3 Test plan (D4)

- Mocked GBP client: create-success maps to a `flyer_gbp_post_published` audit;
  4xx/auth-expired maps to a §12b alert + fail-closed copy (no "posted" claim).
- Idempotency: same (project, location) twice ⇒ one live create + one no-op.
- Token-store: refresh-token round-trip; revoked-token path returns the
  fail-closed branch.
- No live API in CI — the network boundary is always mocked.

### D4.4 Rollout (D4)

Flag-off + ENV allowlist of one location, single manager identity, one manual
YES-approved post to a Hisaku-owned test profile as the canary, before any
customer location. Post-queue watchdog live before the second post.

---

## Ordered rollout (recap, unchanged from spec §6)

1. **PR-D1** (done, #564) — spec + offline prototype + fixtures + goldens.
   Golden set expanded to 5 fixtures this PR (F0209/F0213/F0214 added).
2. **PR-D2** — offer flag (default OFF) + emission in the delivered branch +
   `social_offers.json` sidecar + `flyer_social_offer_sent`. Pilot-scoped.
3. **PR-D3** — this design: YES intercept + draft-as-text + composer promotion
   + claimed/delivered audits + the ordering replay fixtures.
4. **PR-D4** — GBP API phase, gated on §3 account decision + `mcp/native-mcp` +
   GBP approval + media fork. YES-per-post; §12a/§12b; canary on a Hisaku-owned
   profile first.
