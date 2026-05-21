# Flyer Contract Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize Flyer Studio's project facts and customer-visible lifecycle so natural flyer requests cannot poison business identity, leak project IDs, or send duplicate/inconsistent initial acknowledgements.

**Architecture:** Keep Hermes/cf-router identity, routing, audit, state, provider, and messaging substrate. Add only Flyer-specific contract policy: profile-authoritative business/contact facts, campaign-title separation, a pre-generation fact sanity gate, outcome-only customer copy, and transcript-level regression tests.

**Tech Stack:** Python 3, Pydantic v2 Flyer schemas, JSON-on-disk state via existing safe_io/FileLock paths, cf-router plugin hooks/actions, pytest.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Flyer business-identity override policy helper, profile-backed locked fact extraction, required `campaign_title` locked fact, pre-generation malformed business fact blockers, customer-copy lifecycle tests, shared post-processing-ack failure policy, optional self-eval incidents for malformed business facts and duplicate initial acks.

## Hermes-first analysis

| Domain | Hermes/in-tree capability found? | Decision |
|---|---|---|
| Identity/routing | yes - cf-router sender block, `identify-sender`, Flyer sender/customer helpers | reuse; add no new auth substrate |
| State | yes - Flyer JSON stores and existing file locks | reuse existing `customers.json` / `projects.json`; no DB |
| Audit | yes - cf-router audit details and existing log chokepoints | keep internal IDs/reasons in audit, not WhatsApp |
| Structured extraction | partial - Hermes pattern exists, Flyer parser is deterministic today | narrow deterministic contract policy now; defer Hermes-style structured request extractor |
| Visual QA | yes - locked-fact required loop | improve facts before QA; do not weaken QA |
| Customer copy | substrate yes, copy policy Flyer-specific | net-new outcome-only lifecycle policy |
| Source edit/provider policy | yes/current PRs | out of scope; do not touch provider resolution or source-vs-new semantics |

Awesome Hermes Agent ecosystem check: no turnkey Flyer request contract/lifecycle skill is present in the project notes or recent Hermes-first docs. Verdict: reuse Hermes/cf-router substrate and implement only the Flyer-specific fact/copy policy.

## Hermes-first checklist

| Step | Hermes-owned? | Decision |
|---|---|---|
| WhatsApp sender identity/chat routing | yes | reuse existing cf-router/Hermes identity helpers |
| Inbound message capture/audit | yes-ish | reuse existing decisions.log / cf-router audit chokepoints |
| Customer profile lookup | Flyer-specific on Hermes state | reuse existing Flyer customer store helpers |
| Natural language request extraction | Hermes-capable substrate | prefer structured extraction pattern long term; for this PR, implement conservative deterministic contract rules and document Hermes extractor follow-up |
| Business identity authority | Flyer-specific policy | profile business_name/contact/address win unless explicit override |
| Campaign/offer title extraction | Flyer-specific policy | keep `fields.event_or_business_name` as campaign/event/offer title, not required business identity |
| Source-contract / visual QA | Flyer-specific schema, Hermes vision substrate | do not weaken QA; make facts better before QA |
| Operator/self-eval reporting | Hermes/operator brief substrate | extend existing self-eval only if small and non-duplicative with PR #151 |
| Audit emission | Hermes pattern | use existing safe_io / LogEntry / audit helper patterns |
| Provider routing/source-edit | out of scope | do not touch provider policy |
| Customer copy | Flyer-specific policy on Hermes messaging substrate | outcome-only, no project IDs/internal queue/provider wording |

## Drift Checks Performed

- Read `AGENTS.md`, `docs/hermes-alignment.md`, `tasks/lessons.md`, `tasks/todo.md`.
- Read recent Flyer plans/docs: source-contract-first, self-evaluation anti-silent-failure, routing preview tripwires.
- Read PR notes for #147, #148, #150, #151 via `gh pr view`; all are merged into `origin/main`.
- Read current code/tests for:
  - `src/agents/flyer/scripts/create-flyer-project`
  - `src/agents/flyer/facts.py`
  - `src/agents/flyer/render.py`
  - `src/agents/flyer/visual_qa.py`
  - `src/plugins/cf-router/actions.py`
  - `src/plugins/cf-router/hooks.py`
  - `tools/flyer-self-evaluation.py`
  - `tests/test_flyer_create_project.py`
  - `tests/test_flyer_renderer.py`
  - `tests/test_cf_router_flyer_routing.py`
  - `tests/test_flyer_state_reply_table.py`
  - `tests/test_flyer_self_evaluation.py`

## Failure Analysis

- `create-flyer-project` hydrates `fields.event_or_business_name` from the customer profile only when parser extraction leaves it blank.
- `facts.extract_text_facts()` then emits locked `business_name` from `fields.event_or_business_name` with source `customer_text`.
- `merge_locked_facts()` gives `customer_text` higher priority than `customer_profile`, so a parsed campaign fragment can outrank registered customer identity.
- Render and QA consume locked `business_name` as required visible business identity; once poisoned, QA fails downstream with a misleading visual-QA error after generation work.
- `send_flyer_processing_ack()` and `send_flyer_intake_ack()` still contain project IDs/internal creation language; hooks can call both processing ack and intake/fallback ack after a generation failure.
- PR #151 added read-only routing tripwires, but not fact-contract or WhatsApp lifecycle enforcement.

## File Plan

- Modify `src/agents/flyer/facts.py`: add profile fact extraction, required campaign-title fact extraction, and explicit business override detection; keep campaign fields separate from business identity.
- Modify `src/agents/flyer/scripts/create-flyer-project`: add `--chat-id`, load the customer profile once using sender-aware lookup (`phone` plus `chat_id`), hydrate profile-backed facts from the actual `FlyerCustomerProfile`, run malformed-business pre-generation gate, and preserve campaign title in `fields.event_or_business_name`.
- Modify `src/agents/flyer/render.py`: render business/brand from locked `business_name` and poster title from locked `campaign_title`/headline when present.
- Modify `src/agents/flyer/visual_qa.py` only if needed for direct tests; do not weaken the existing required locked-fact loop.
- Modify `src/plugins/cf-router/actions.py`: rewrite initial ack helpers to outcome-only copy and add a small copy sanitizer/testable constants if useful.
- Modify `src/plugins/cf-router/hooks.py`: centralize processing-ack generation failure handling across all equivalent paths; fallback copy should be sent only when there was no processing ack or when a true manual/failure outcome must be communicated.
- Modify `src/agents/flyer/workflow.py`: remove project IDs/internal terms from customer-visible status copy while preserving reason-specific status intent.
- Possibly modify `tools/flyer-self-evaluation.py`: add tiny report-only tripwires for malformed business_name facts, customer-copy project-ID leaks, and duplicate initial acks if existing PR #151 coverage does not already catch them.
- Add/modify tests in `tests/test_flyer_create_project.py`, `tests/test_flyer_renderer.py`, `tests/test_flyer_visual_qa.py`, `tests/test_cf_router_flyer_routing.py`, `tests/test_flyer_state_reply_table.py`, `tests/test_flyer_customer_lifecycle_copy.py` (new), `tests/test_flyer_scripts_static.py`, and possibly `tests/test_flyer_self_evaluation.py`.

## Plan Review Fold-Ins

- **Critical folded:** campaign/offer title cannot live only in `fields.event_or_business_name`; it must become a separate required locked fact when present, render as the poster title, and be enforced by Visual QA.
- **High folded:** profile-backed `business_name`, `contact_phone`, and `location` must be constructed directly from `FlyerCustomerProfile`, not attributed from generic parsed `fields.*`.
- **High folded:** duplicate initial ack handling must cover direct new project, reference/media new project, resumed active project, and active intake-ready branches.
- **High folded:** all customer-visible Flyer status/ack/fallback copy, not just initial helpers, must omit project IDs/internal workflow terms.
- **High folded:** location is required when a registered trial/active profile has a nonblank business address; guest/unregistered flows can use sane text-derived business/contact facts.
- **High folded:** profile lookup must be cf-router sender-aware so LID-only/primary_chat_id customers get profile facts.
- **High folded:** `campaign_title` must reject values equal to the registered business name; use headline/fallback title instead for brand/logo prompts.

## Task 1: RED Tests For Fact Contract

- [ ] Add a failing test for the F0065 evening-snacks request from registered customer `Lakshmis Kitchn`.
- [ ] Assert locked `business_name` is `Lakshmis Kitchn` with source `customer_profile`.
- [ ] Assert locked `contact_phone` and `location` are profile-backed.
- [ ] Assert LID/`primary_chat_id` lookup hydrates the same profile facts when `--chat-id` is provided and phone lookup alone would miss.
- [ ] Assert `fields.event_or_business_name` is a campaign title such as `Evening Snacks`, not an instruction fragment.
- [ ] Assert locked `campaign_title` is `Evening Snacks`, source `customer_text`, and required.
- [ ] Assert no locked fact value contains `help me with`, `flier from`, or the malformed fragment `d like you to help me with evening snacks flier`.

Red command:

```powershell
python -m pytest tests/test_flyer_create_project.py -q
```

## Task 2: RED Tests For Customer Lifecycle Copy

- [ ] Add customer-copy tests that call `send_flyer_processing_ack`, `send_flyer_intake_ack`, `send_flyer_manual_review_ack`, and `send_flyer_manual_edit_ack` with a fake `bridge_post`.
- [ ] Add status-copy tests for `build_project_status_reply`, `flyer_manual_edit_status_reply`, `flyer_closed_no_send_status_reply`, active-intake fallback copy, regeneration failure copy, and brand-asset generation failure copy.
- [ ] Assert customer-facing bodies contain no `F0065`, `project F`, `created flyer project`, `queued project`, `operator`, `source-preserving`, `provider`, `reason_code`, or raw request echoes.
- [ ] Add hook lifecycle tests for direct new request, media/reference new request, existing active project retry, and active intake-ready generation failure after a processing ack: none may send both processing ack and generic intake ack.
- [ ] Add source-vs-new transcript tests for SOURCE provider-unavailable, SOURCE provider-ready generation failure, and NEW-from-source paths using the same forbidden-term sanitizer.
- [ ] Preserve source-edit/manual-review copy tests from #140/#143/#146.

Red command:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_customer_lifecycle_copy.py -q
```

## Task 3: Implement Profile-Authoritative Locked Facts

- [ ] Add a helper that builds profile facts from the actual `FlyerCustomerProfile`: `business_name`, `contact_phone`, and required `location` when `business_address` is nonblank, all with source `customer_profile`.
- [ ] Add explicit business-name override detection limited to patterns such as `business name is X`, `change business name to X`, and `replace OLD with NEW` where `OLD` matches the registered business name.
- [ ] Update `extract_text_facts()` or call-site composition so generic `fields.event_or_business_name` no longer becomes locked `business_name`.
- [ ] Add a `campaign_title` locked fact from sane `fields.event_or_business_name`; make it required when present and instruction-like fragments are rejected.
- [ ] Reject `campaign_title` when it equals normalized locked `business_name`; brand/logo prompts should use `headline` or safe fallback as poster title.
- [ ] Remove `location` and `contact_phone` profile attribution from generic text extraction. If a text override is needed later, it must be explicit and tested.
- [ ] Preserve paid guest/unregistered behavior: when no trial/active profile exists, allow sane text-derived `business_name` and `contact_phone` with source `customer_text`; add paid guest render-ready and missing-contact tests.
- [ ] Preserve customer-text priority for headline, tagline, items, prices, schedule, and source-contract replacement facts.
- [ ] Keep `fields.event_or_business_name` available as campaign/event/offer title for existing render title behavior where appropriate.

Green command:

```powershell
python -m pytest tests/test_flyer_create_project.py -q
```

## Task 3b: Render And QA Campaign Title Separately

- [ ] Update renderer title selection so poster `Title:` prefers `campaign_title` or headline, while `Business/brand:` uses locked profile business name.
- [ ] Update menu overlay/poster copy payloads consistently so generated copy and text manifest agree.
- [ ] Add Visual QA tests proving required `business_name`, `campaign_title`, and `contact_phone` must all appear when locked.
- [ ] Pin location requiredness: profile-backed location is required when registered profile has a nonblank address; guest text location remains optional.

Green command:

```powershell
python -m pytest tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py -q
```

## Task 4: Add Pre-Generation Contract Gate

- [ ] Add a sanity helper for locked `business_name`: reject instruction-like fragments containing `I'd like`, `help me with`, `create flyer`, `flier from`, `include`, and long instruction fragments.
- [ ] Run this gate in `create-flyer-project` before generation can start; queue `manual_edit_required` with a specific manual detail only if profile/explicit override cannot repair the fact.
- [ ] Ensure F0065 is repaired upstream by profile facts, so this gate does not fire for the registered customer.
- [ ] Do not weaken visual QA required-fact checks.

Green command:

```powershell
python -m pytest tests/test_flyer_create_project.py tests/test_flyer_renderer.py -q
```

## Task 5: Rewrite Initial Customer Copy And Duplicate-Ack Flow

- [ ] Change `send_flyer_processing_ack()` to outcome-only copy with no project ID/internal workflow detail.
- [ ] Change `send_flyer_intake_ack()` to outcome-only fallback copy with no project ID/internal workflow detail.
- [ ] Change status/manual/failure copy helpers and inline hook sends so customer-visible text has no project IDs/internal workflow detail.
- [ ] Keep branch predicates/order, `flyer_source_edit_preflight`, provider resolution, and source-vs-new choice semantics unchanged.
- [ ] Leave project ID and reason details in audit/Cockpit paths only.
- [ ] Add one shared helper for post-processing generation failure. In hooks, if a processing ack was already sent and generation fails without manual-review specificity, do not send a second generic intake ack. Keep manual/failure fallback copy when it communicates a real outcome.
- [ ] Replace equivalent direct new project, reference/media, existing active project, and active intake-ready branches with that helper.
- [ ] Keep preview/finalization paths unchanged except for avoiding duplicate initial ack behavior.

Green command:

```powershell
python -m pytest tests/test_cf_router_flyer_routing.py tests/test_flyer_customer_lifecycle_copy.py tests/test_flyer_state_reply_table.py -q
```

## Task 6: Self-Eval Tripwire If Small

- [ ] Check whether PR #151 already covers customer-copy project-ID leaks and routing duplicates sufficiently.
- [ ] If not duplicative, add report-only incidents:
  - `malformed_business_name_fact`
  - `customer_copy_project_id_leak`
  - `duplicate_initial_ack`
- [ ] Ensure copy tripwires inspect outbound/audit transcript bodies when available and do not rely only on helper source scanning.
- [ ] Keep this read-only and offline; no cron, deploy, WhatsApp, or state mutation.

Green command if touched:

```powershell
python -m pytest tests/test_flyer_self_evaluation.py tests/test_operator_brief.py -q
```

## Task 7: Full Focused Verification

- [ ] Run the focused Flyer suite:

```powershell
python -m pytest tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_visual_qa.py tests/test_cf_router_flyer_routing.py tests/test_cf_router_plugin.py tests/test_flyer_state_reply_table.py tests/test_flyer_customer_lifecycle_copy.py tests/test_flyer_manual_review_ack_copy.py tests/test_flyer_manual_edit_ack_copy.py -q
```

- [ ] If self-eval touched:

```powershell
python -m pytest tests/test_flyer_self_evaluation.py -q
```

- [ ] Compile touched Python:

```powershell
python -m py_compile src/agents/flyer/scripts/create-flyer-project src/agents/flyer/facts.py src/agents/flyer/render.py src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py tools/flyer-self-evaluation.py
```

- [ ] Run whitespace check:

```powershell
git diff --check
```

## Acceptance Criteria

- F0065-class request no longer poisons locked `business_name`.
- Registered profile identity is authoritative unless explicit override language is present.
- `contact_phone` and `location` locked facts hydrate from profile by default.
- Campaign/offer title remains available separately from business identity.
- Instruction-like business facts are blocked upstream before model generation.
- Customer WhatsApp initial/fallback copy contains no project IDs or internal workflow detail.
- Normal new request does not send processing ack plus generic intake ack without preview.
- Routing behavior from PR #150 remains intact.
- PR #140/#143/#146 manual/source-edit copy intent remains intact.
- No provider/source-edit policy changes.
- No deploy performed.

## Risks

- Regex extraction remains transitional; long-term solution should use Hermes-style structured extraction.
- Separating business identity from campaign title may affect old render/title tests that assumed `event_or_business_name` was the visible business name.
- Copy helpers serve multiple contexts; call-site tests must prevent repeating the PR #146 context mistake.

## Deferred Items

- Hermes structured extractor for Flyer natural-language requests.
- Full source-contract facts enforced as locked facts and QA blockers across every source-edit path.
- Operator push alert for active customer-risk incidents.
- Dashboard active-risk lane.
