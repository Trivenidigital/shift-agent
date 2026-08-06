# Per-turn skill-selection observability — PLAN

```
STATUS: PLAN_INCOMPLETE — IMPLEMENTATION HOLD
```

**No production code is authorized from this document.** Six proof items and the observability
matrix remain mandatory before any implementation. See "STILL OUTSTANDING" below.

## Preserved conclusions — evidence, not assumptions

1. The pinned Hermes runtime's `VALID_HOOKS` (`hermes_cli/plugins.py`) **admits** the required
   lifecycle hooks. Verified by reading the set, not inferred from documentation.
2. The **shipped Langfuse plugin** (`plugins/observability/langfuse/__init__.py:1132-1137`)
   registers six hooks, proving multi-hook registration is an established Hermes pattern in this
   runtime rather than a theoretical capability.
3. **`api_request_error` provides affirmative provider-error observation.** Therefore a missing
   `post_llm_call` must **NOT** be classified `MODEL_ACCESS_FAILED`. That earlier transition is
   formally **withdrawn**.
4. **Missing lifecycle events map only** to `TRACE_INCOMPLETE`,
   `TURN_FINALIZED_WITHOUT_LLM_RESULT`, or `TURN_CANCELLED` — never to a provider diagnosis.
5. **Skill exposure, skill selection, and tool invocation remain distinct.** `pre_tool_call` proves
   only that a call reached the tool layer. Exact-field proof is still required; anything
   unsupported must be labelled `NOT_OBSERVABLE_WITH_CURRENT_HOOKS`.
6. **Deterministic Catering receipt correlation is currently unproven and likely absent** —
   `menu_update_proposed` is written by our own scripts, which have no access to Hermes turn ids.
7. Consequently **`VERIFIED` must remain unreachable** unless a stable shared identifier
   (`turn_id` / `task_id` / `tool_call_id` / explicit receipt id) is demonstrated. Timestamp
   proximity, phone number, or latest-row matching is **NOT** acceptable. Until then the honest
   terminal state is `TOOL_SUCCEEDED_RECEIPT_CORRELATION_UNAVAILABLE`, and correlation support is a
   **separate** schema change requiring its own review.
8. The six remaining proof items and the observability matrix are **mandatory** before
   implementation begins.

**Drift-check tag:** `extends-Hermes` — registers additional *native* Hermes plugin hooks on the
existing `cf-router` plugin and writes bounded, redacted events through the existing audit
chokepoint. No new telemetry subsystem, no Hermes core patch, no new storage.

**New primitives introduced:** one new `LogEntry` discriminated-union variant (`turn_trace`) and a
turn-state classifier. Nothing else.

## Hermes-first capability checklist

The resume doc (`tasks/catering-menu-ingestion-resume-2026-08-01.md`) recorded *"there is no
per-turn skill-selection or tool-call logging"*. That was a correct **observation** and an
incorrect **inference**. The capability exists in the pinned runtime and was never registered.
Verified against `/usr/local/lib/hermes-agent` (v0.19.1) — hook names and payload shapes were read
from `hermes_cli/hooks.py`, not assumed.

| # | Step | Tag | Basis |
|---|---|---|---|
| 1 | Owner sends WhatsApp message; gateway receives the turn | `[Hermes]` | Source origins: WhatsApp inbound media |
| 2 | `pre_gateway_dispatch` fires; routing/cession decided | `[Hermes]` | already registered by cf-router; skill dispatch substrate |
| 3 | Open a turn trace keyed by session/task/turn ids | `[net-new]` | no existing LogEntry variant carries turn-scoped selection state |
| 4 | `pre_llm_call` fires with model/platform/is_first_turn | `[Hermes]` | LLM gateway: text + vision, swappable provider |
| 5 | Serialize hook payload into a redacted event | `[net-new]` | thin handler; shared with steps 9 and 11 |
| 6 | `post_llm_call` fires, or is absent on provider error | `[Hermes]` | LLM gateway |
| 7 | Classify provider failure as `MODEL_ACCESS_FAILED` | `[net-new]` | Hermes surfaces the error but assigns it no state |
| 8 | `pre_tool_call` fires with tool_name/tool_call_id | `[Hermes]` | skill dispatch substrate |
| 9 | Drop `args`; emit tool_name only | `[net-new]` | redaction contract below |
| 10 | Tool executes (e.g. `parse-menu-photo`) | `[Hermes]` | SKILLs are scripts with filesystem + subprocess access |
| 11 | Map result to a bounded error class; drop result body | `[net-new]` | redaction contract below |
| 12 | `on_session_end` fires with `turn_exit_reason` | `[Hermes]` | session lifecycle hooks |
| 13 | Classify the turn into one of eight terminal states | `[net-new]` | the actual diagnostic value |
| 14 | Cross-check `decisions.log` for a durable receipt row | `[Hermes]` | Audit chain: decisions.log discriminated-union entries |
| 15 | Flag success language emitted without a `VERIFIED` state | `[net-new]` | outbound screen exists; receipt correlation does not |

**Red-flag check:** 7 of 15 net-new — under half. Re-examined the four common misses: the audit
write reuses `log-decision-direct`/`ndjson_append`; no separate operator script; no new state file
(correlation is in-memory per turn); validation reuses Pydantic `extra="forbid"`.

awesome-hermes-agent ecosystem check: not applicable — this registers first-party hooks already
present in the pinned runtime, not a missing ecosystem capability.

## Hook-registration proof (reviewer correction 1–2) — PARTIAL

**Proven.** `hermes_cli/plugins.py` defines `VALID_HOOKS: Set[str]` containing `pre_tool_call`,
`post_tool_call`, `transform_tool_result`, `transform_llm_output`, `pre_llm_call`, `post_llm_call`,
`pre_api_request`, `post_api_request`, **`api_request_error`**, `on_session_start`,
`on_session_end`, `on_session_finalize`, `on_session_reset`, `subagent_start`, `subagent_stop`,
`pre_verify`. `provides_hooks` is parsed at `plugins.py:1661` into the dataclass field at
`plugins.py:290`; `register_hook` is `plugins.py:1177`.

**Proven by precedent, in the pinned runtime:** `plugins/observability/langfuse/__init__.py`
registers **six** hooks at lines 1132–1137 (`pre_api_request`, `post_api_request`, `pre_llm_call`,
`post_llm_call`, `pre_tool_call`, `post_tool_call`). `plugins/platforms/raft/adapter.py:850`
registers `post_llm_call`. Multi-hook registration by a single plugin is therefore supported in
practice, not merely permitted by the manifest schema.

**`MODEL_ACCESS_FAILED` no longer requires inference.** `api_request_error` is an affirmative
provider-error event. The plan's earlier transition — `pre_llm_call` observed + `post_llm_call`
absent ⇒ `MODEL_ACCESS_FAILED` — is **withdrawn**. Absence now maps only to
`TRACE_INCOMPLETE` / `TURN_FINALIZED_WITHOUT_LLM_RESULT` / `TURN_CANCELLED`.

**`post_llm_call` payload (from the langfuse worked example, lines 908 + 928–950):**
`task_id`, `session_id`, `provider`, `base_url`, `assistant_message` (object), `response` (object),
`assistant_response` (str). The `post_api_request` path additionally carries `usage` (dict),
`assistant_content_chars`, and **`assistant_tool_call_count`** — a bounded integer directly
relevant to "selected but emitted no tool call".

**Concurrency reference:** langfuse keeps `_TRACE_STATE` under `_STATE_LOCK`, keyed by `task_key`
with per-request `generations` keyed by `req_key` — the in-runtime pattern to mirror for
reviewer correction 7.

### STILL OUTSTANDING before implementation

- [ ] **Loader-level test** proving cf-router *receives* these hooks on a WhatsApp gateway turn,
      and that an undeclared hook is not silently ignored. Precedent ≠ proof for this plugin.
- [ ] **`HERMES_ACCEPT_HOOKS=1`** — confirm it gates these specific hooks.
- [ ] **Skill exposure vs selection (correction 3)** — locate the authoritative field, or mark
      `NOT_OBSERVABLE_WITH_CURRENT_HOOKS`. `pre_tool_call` proves only the tool-call layer.
- [ ] **Receipt correlation (correction 4)** — determine whether `menu_update_proposed` can carry
      `turn_id`/`task_id`/`tool_call_id`. It is written by our scripts, which today have no access
      to Hermes turn ids, so `TOOL_SUCCEEDED_RECEIPT_CORRELATION_UNAVAILABLE` is the likely
      starting state and correlation support is a **separate** schema change.
- [ ] **Sink analysis (correction 6)** — enumerate every `LogEntry` consumer; confirm unknown-variant
      tolerance, rotation, volume, non-recursion, fail-open. If `decisions.log` is a
      business-decision ledger, use the existing structured gateway logging path instead.
- [ ] **Final-claim hook (correction 5)** — prove `transform_llm_output` or `on_session_finalize`
      is the outbound path; reuse the existing outbound-screen classification; persist only a
      derived boolean/class.
- [ ] **Observability matrix** — required state × hook × exact field × correlation key × fires-on
      success/error/cancellation × sink × confidence × test.
- [ ] **Scope** — collection limited to the Catering owner/menu-ingestion route; no implicit
      fleet-wide telemetry.

**This PR is observability-only: flag unverified operational claims; do NOT suppress, rewrite, or
block them.**

## Drift-rule self-checks

- ✅ Read `src/platform/schemas.py` — `LogEntry` discriminated union (variants at ~4210–4716,
  `menu_update_proposed` at 4664) before proposing a new variant.
- ✅ Read `src/plugins/cf-router/__init__.py` — the `register(ctx)` / `ctx.register_hook(...)`
  pattern to mirror, and `plugin.yaml` `provides_hooks:` which today lists only
  `pre_gateway_dispatch`.
- ✅ Read `/usr/local/lib/hermes-agent/hermes_cli/hooks.py` — `_DEFAULT_PAYLOADS` is the
  authoritative payload contract for `pre/post_tool_call`, `pre/post_llm_call`, `on_session_*`.
- ✅ Read `src/platform/audit_helpers.py` and `src/platform/safe_io.py` — confirmed
  `ndjson_append` + `FileLock` is the existing append path; no new writer needed.

## Why this matters beyond the catering blocker

The 2026-08-01 diagnosis could not distinguish "model never selected the skill" from "skill selected
but its bash step never ran" — both look identical in the audit log as `menu_update_proposed = 0`.
These hooks make the distinction observable, which is the precondition for diagnosing the blocker
**once OpenRouter credit is restored**. The extractor currently fails closed at `exit 6` with
`HTTP 402`, so no live diagnosis is possible today.

## State machine

Correlation key: `session_id` + `task_id` + `turn_id` (+ `tool_call_id` per call).

| state | derived from |
|---|---|
| `MODEL_ACCESS_FAILED` | `pre_llm_call` fired, `post_llm_call` errored/absent |
| `NO_SKILL_SELECTED` | `post_llm_call` ok, no `pre_tool_call`, `turn_exit_reason` = text response |
| `SKILL_SELECTED_NO_TOOL_CALL` | skill context present, no `pre_tool_call` before turn end |
| `TOOL_CALL_NOT_INVOKED` | `pre_tool_call` fired, no `post_tool_call` |
| `TOOL_INVOCATION_FAILED` | `post_tool_call` with non-zero/error result |
| `INVOKED_WITHOUT_RECEIPT` | `post_tool_call` ok, no durable receipt row for the turn |
| `VERIFIED` | `post_tool_call` ok **and** durable receipt row present |
| `CLAIM_WITHOUT_RECEIPT` | operational-success language emitted with no `VERIFIED` state |

## Redaction contract (enforced by test, not convention)

**Never written:** prompts, `user_message`, conversation history, tool `args` values, tool `result`
bodies, image bytes or paths, API keys, OTPs, full phone numbers, menu/customer data.

**Written:** `session_id`, `task_id`, `turn_id`, `tool_call_id`, `model`, `platform`, `tool_name`,
`duration_ms`, a bounded error *class*, boolean shape flags (`has_media`), and existing approved
identifiers only. Phone numbers use the existing masking helper; never raw.

## Scope

**In:** register the six hooks on `cf-router`; add the `turn_trace` LogEntry variant; classifier;
tests.

**Out (explicitly, per ruling):** funding or modifying the OpenRouter account; credential rotation
or exposure; provider change or direct-Kimi; any new fallback; enabling
`FRONT_BRAIN_CONVERSE_CHATS`; resending the menu image; any live menu mutation; customer-facing
behaviour change; unrelated logging infrastructure. Direct-Kimi fallback and a balance watchdog are
**separate** workstreams per the ruling.

## Test plan (deterministic, no live calls)

1. OpenRouter `402` ⇒ `MODEL_ACCESS_FAILED`, no selection, no success claim.
2. `NO_SKILL_SELECTED` distinguishable from `TOOL_INVOCATION_FAILED`.
3. `SKILL_SELECTED_NO_TOOL_CALL` visible as its own state.
4. Tool failure cannot yield a success receipt.
5. `VERIFIED` requires the durable receipt/state transition.
6. Operational-success language without a verified receipt is flagged/suppressed.
7. Emitted events contain no secrets, message bodies, full phone numbers, or image data.

## Status

- [x] Drift-check reads (above)
- [x] Hermes-first: enumerated real hook names + payloads from the installed runtime
- [ ] `turn_trace` LogEntry variant
- [ ] hook handlers + registration
- [ ] eight-state classifier
- [ ] tests (7 cases)
- [ ] focused test run + review
