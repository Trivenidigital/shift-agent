# Flyer Hermes Shadow Adapter And Training Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** extends-Hermes

**Goal:** Build two merge-ready, non-deployed follow-up PRs after PR #171: a live Hermes classifier shadow adapter and a deterministic Hermes offline self-learning training export pipeline for Flyer intent examples.

**Architecture:** Keep cf-router as the authoritative runtime router while Hermes produces advisory JSON decisions in shadow. Store training examples from existing typed audit rows and deterministic outcome evidence, not from raw WhatsApp text, and never mutate code, prompts, SKILLs, providers, customer state, or routing behavior.

**Tech Stack:** Python stdlib, Pydantic v2 schemas, existing Hermes/cf-router plugin, `safe_io.ndjson_append`, JSON-on-disk state/report files, pytest.

---

## New Primitives Introduced

- `FlyerHermesClassifierAdapter`: pure adapter seam that parses strict JSON from an injected Hermes gateway callable when explicitly enabled and returns a strict `FlyerIntentDecision`.
- `classifier_status`, `classifier_error_kind`, and `classifier_latency_ms` on the existing `flyer_hermes_intent_decision` audit row. Do not add a second audit variant unless implementation proves the current row cannot represent the failure.
- `FlyerIntentTrainingExample`: deterministic, redacted, versioned export example derived from `flyer_hermes_intent_decision` rows plus coarse outcome context.
- `tools/flyer-intent-training-export`: offline/local CLI that reads decisions.log and writes an explicit training/example JSONL artifact for operator/Hermes self-evolution ingestion.
- Optional operator brief/self-eval counters for training-export freshness and example quality.

## Drift-Check Findings

| Area | Existing primitive | Residual gap |
|---|---|---|
| Intent contract | `src/agents/flyer/intent.py` already defines `FlyerIntentDecision`, validation, action normalization, and training-example builder | Live advisory decisions are still `decision_source="none"`; no gateway-backed classifier adapter exists |
| Shadow context | `src/plugins/cf-router/actions.py::begin_flyer_intent_shadow` and `finalize_flyer_intent_shadow` already emit terminal `flyer_hermes_intent_decision` rows | Context cannot call a real Hermes classifier and cannot record classifier errors separately |
| Audit schema | `FlyerHermesIntentDecision` exists in `src/platform/schemas.py` | No training-export artifact/schema exists; classifier failure typing may be missing |
| Self-eval/operator brief | `tools/flyer-self-evaluation.py` and `tools/operator-brief.py` already group Hermes intent incidents | No training export freshness/quality summary exists |
| Operating layer | `src/agents/flyer/operating_layer.py` reports brand-memory readiness | It does not persist intent training examples |
| Deploy/install | `shift-agent-deploy.sh` installs many flat Flyer helper modules | Verify/install any new helper modules and smoke-import them; do not repeat the prior missing `flyer_intent.py` install gap |
| Backlog | `tasks/todo.md` already lists live classifier and training export follow-ups | This work closes those two follow-ups only; active low-risk routing remains deferred |

## Hermes-First Analysis

| Step | Hermes or net-new? | Decision |
|---|---|---|
| WhatsApp ingress, sender identity, media paths, bridge delivery | `[Hermes]` | Reuse current cf-router hook; no new ingress or message sender |
| Intent classification | yes - Hermes gateway/LLM substrate can classify language; no Flyer-specific classifier skill found in Hermes Skills Hub | Use injected Hermes gateway callable only; no direct provider client |
| Structured schema validation | no generic Hermes skill for Flyer contract | Keep in Flyer `FlyerIntentDecision` / validator |
| Memory/session learning | yes - Hermes memory persists compact facts and session search stores conversation history; external memory providers exist | Export redacted examples for operator/Hermes ingestion; do not write production memory automatically |
| Self-evolution/eval ingestion | yes - Hermes Self-Evolution Kit exists for offline/staging improvement loops | Produce training examples and reports; code/prompt/SKILL changes still require PR |
| Observability/operator brief | existing repo tools, not a Hermes ecosystem skill | Extend current `flyer-self-evaluation` / `operator-brief` only |
| Strict Flyer intent schema, legal actions, source-edit safety, copy lint | `[net-new]` | Keep in `src/agents/flyer/intent.py`; Hermes returns JSON, Flyer validates |
| Typed audit and decisions.log | `[Hermes]` substrate | Append typed rows through existing `safe_io.ndjson_append` / schema union |
| Self-eval/operator brief | `[Hermes]` substrate + `[net-new]` Flyer policy | Extend existing tools only if needed for visibility |
| Active routing | deferred | Out of scope until shadow soak passes per-family thresholds |

Awesome-Hermes-Agent ecosystem check: Hermes provides gateway, memory/session search, background tasks, skills, and self-evolution substrate, but no Flyer-specific intent contract, validator, route-outcome mapping, or customer-copy policy. Verdict: use Hermes for classification/memory plumbing; build only the Flyer product contract and redacted export glue.

## PR-A: Hermes Live Classifier Shadow Adapter

### Scope

In scope:
- Add a narrow adapter that converts `(text, has_media, customer/project/intake context)` into a strict `FlyerIntentDecision`.
- Gate the adapter behind `FLYER_HERMES_INTENT_CLASSIFIER=off|shadow`.
- Keep `FLYER_HERMES_INTENT_MODE` semantics unchanged: `off`, `shadow`, unsupported active modes remain inert.
- In shadow mode, capture context before the live router but run the classifier only after deterministic routing has returned, in the `finally` finalization path. A slow classifier must never delay the initial deterministic customer acknowledgement beyond a hard budget.
- If adapter fails, times out, returns invalid JSON, or violates the validator, record `classifier_status`, `classifier_error_kind`, bounded redacted detail, and latency in the typed intent row without affecting customer behavior.
- Prove with tests that route result and customer sends remain identical.

Out of scope:
- No active routing.
- No direct provider/OpenRouter/OpenAI/urllib/http client in cf-router or `intent.py`.
- No new classifier router. The only live dependency is an injectable Hermes gateway callable supplied by the plugin/hook context; absent callable means classifier skipped.
- No customer-visible Hermes reply.
- No source-edit automation changes.
- No deploy.

### Files

- Modify: `src/agents/flyer/intent.py`
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/platform/schemas.py` only if classifier-error row is needed after design review
- Modify: `src/agents/shift/scripts/shift-agent-deploy.sh`
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh`
- Test: `tests/test_flyer_intent_layer.py`
- Test: `tests/test_cf_router_flyer_routing.py`
- Test: `tests/test_flyer_scripts_static.py`

### Tasks

- [ ] **Step A1: Add adapter tests before implementation.**

  Add tests proving:
  - valid fixture/gateway JSON becomes `FlyerIntentDecision(decision_source="hermes_gateway_future")`;
  - invalid JSON becomes a safe fallback decision with `decision_source="none"` or a classifier-error marker;
  - banned customer copy in adapter output fails validation;
  - adapter timeout/failure does not raise out of `begin_flyer_intent_shadow`;
  - mode `off` skips the adapter entirely.
  - slow adapter does not change `pre_gateway_dispatch` route result, outbound sends, or exceed the configured shadow latency budget.
  - static guard rejects direct provider imports or HTTP clients in `src/agents/flyer/intent.py` and cf-router classifier glue.

  Run:

  ```powershell
  python -m pytest tests/test_flyer_intent_layer.py -q
  ```

  Expected before code: new adapter tests fail because adapter functions are absent.

- [ ] **Step A2: Implement a pure adapter seam.**

  Add functions in `src/agents/flyer/intent.py`:

  ```python
  def classifier_setting_from_env(value: str | None) -> str:
      normalized = str(value or "").strip().lower()
      return "shadow" if normalized == "shadow" else "off"

  def parse_classifier_payload(payload: str | dict[str, Any]) -> FlyerIntentDecision:
      data = json.loads(payload) if isinstance(payload, str) else payload
      decision = FlyerIntentDecision.model_validate(data)
      return decision.model_copy(update={"decision_source": "hermes_gateway_future"})
  ```

  Add a callable injection point so tests can provide the classifier without network. The callable is the only classification provider:

  ```python
  FlyerClassifierCallable = Callable[[FlyerClassifierRequest], str | dict[str, Any]]
  ```

  The production hook may pass a Hermes gateway-backed callable later. If no callable is available, return classifier status `skipped_no_gateway`.

- [ ] **Step A3: Wire shadow context to the adapter without changing routes.**

  In `actions.begin_flyer_intent_shadow`, capture only cheap context. In `finalize_flyer_intent_shadow`, after the deterministic route and branch return are known, call the classifier when all of these are true:

  - `FLYER_HERMES_INTENT_CLASSIFIER=shadow`;
  - current mode is `shadow` or `unsupported_active_mode`;
  - the message is a Flyer candidate or a Flyer route event occurred;
  - an injectable Hermes gateway classifier callable is available;
  - remaining hard latency budget permits the call.

  Required behavior:
  - adapter exception -> `classifier_status="error"`, `decision_source="none"`, route unchanged;
  - invalid decision -> `classifier_status="invalid"`, validator rejection or fallback, route unchanged;
  - timeout/budget exceeded -> `classifier_status="timeout"` or `skipped_budget`, route unchanged;
  - valid decision -> audit row shows advisory intent/action/confidence, route unchanged.

- [ ] **Step A4: Add route-invariance tests.**

  Use existing cf-router tests with monkeypatched adapter decisions:
  - Hermes says `new_flyer`, deterministic route creates project;
  - Hermes says `clarify`, deterministic route still creates project;
  - Hermes says `revise_flyer`, deterministic route still handles status/approval according to existing logic;
  - all cases emit typed audit rows and do not send Hermes customer reply.

- [ ] **Step A5: Install/smoke path.**

  Choose one runtime import convention and gate it. For this repo, prefer package-compatible install so `agents.flyer.intent` and any new `agents.flyer.*` modules import exactly the same way locally and on the VPS. Add deploy/smoke checks for the exact deployed import path and avoid silent `ImportError -> no shadow` behavior.

  Add smoke-import checks so a missing classifier/intent helper fails deploy smoke before traffic.

- [ ] **Step A6: Verification and PR-A review.**

  Run:

  ```powershell
  python -m pytest tests/test_flyer_intent_layer.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py -q
  python -m py_compile src/agents/flyer/intent.py src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py
  git diff --check
  ```

  Open PR-A with "No deploy performed." Request two reviewers:
  1. Live-behavior reviewer: prove advisory classifier cannot alter route/customer response.
  2. Hermes-first/runtime reviewer: prove no duplicate provider client or self-modifying behavior.

## PR-B: Flyer Intent Training Export For Hermes Self-Learning

### Scope

In scope:
- Build an offline/read-only training-example export CLI that converts typed intent audit rows into redacted learning examples.
- Reuse or move PR #171's `build_training_example()` instead of inventing a parallel schema. Add only the fields needed to make examples useful for Hermes Self-Evolution Kit / operator-curated memory.
- Include outcome fields from route/action, validator result, route disagreement, and coarse later project outcome when safely derivable from existing state/logs.
- Write JSONL to an explicit output path using `safe_io.atomic_write_text` under `safe_io.flock(out_path)` for the whole build/write.
- Add self-eval/operator brief visibility for stale/missing/low-quality training examples when `--expect-flyer-intent-training-export` is set.

Out of scope:
- No production memory write unless operator runs the CLI and separately imports the artifact into Hermes memory/self-evolution tooling.
- No automatic prompt/SKILL/code/model edits.
- No live routing.
- No raw phone/chat IDs or raw WhatsApp request text.
- No deployment.

### Files

- Create: `src/agents/flyer/intent_training.py`
- Create: `src/agents/flyer/scripts/flyer-intent-training-export`
- Modify: `src/agents/shift/scripts/shift-agent-deploy.sh`
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh`
- Modify: `tools/flyer-self-evaluation.py`
- Modify: `tools/operator-brief.py`
- Test: `tests/test_flyer_intent_training.py`
- Test: `tests/test_flyer_self_evaluation.py`
- Test: `tests/test_operator_brief.py`
- Test: `tests/test_flyer_scripts_static.py`

### Tasks

- [ ] **Step B1: Add training export tests before implementation.**

  Add tests proving:
  - a `flyer_hermes_intent_decision` row becomes a redacted training example using the existing `build_training_example()` contract where possible;
  - raw request/chat/message values are absent;
  - validator rejection and route disagreement are preserved;
  - historical rows can be filtered by time window;
  - duplicate message hashes dedupe deterministically.
  - recursive value redaction blocks names, addresses, E.164/US phones, WhatsApp JIDs, local paths, URLs, provider keys, and raw request echoes even if keys are innocuous.

  Run:

  ```powershell
  python -m pytest tests/test_flyer_intent_training.py -q
  ```

  Expected before code: fail because module/CLI does not exist.

- [ ] **Step B2: Implement pure training example builder.**

  In `src/agents/flyer/intent_training.py`, add:
  - `FlyerIntentTrainingExample` Pydantic model with `extra="forbid"` only if `build_training_example()` cannot cover the necessary fields;
  - `example_from_intent_row(row)` backed by `build_training_example()` where possible;
  - `build_examples(decision_entries, *, since=None, max_examples=...)`;
  - explicit allowlist of PII-light fields copied from audit rows;
  - recursive value redaction guard that rejects unsafe values before write.

- [ ] **Step B3: Implement training export CLI.**

  CLI inputs:
  - `--decisions-log`
  - `--out`
  - `--since-hours`
  - `--max-examples`
  - `--format json|jsonl`

  Output includes summary counts plus examples. Writes only to `--out`; stdout is summary-only and redacted.

- [ ] **Step B4: Extend self-eval/operator brief.**

  Add optional `--flyer-intent-training-json` plus `--expect-flyer-intent-training-export` or equivalent input to:
  - count examples by actual action and advisory intent;
  - flag `flyer_intent_training_export_missing` when shadow intent rows exist but no fresh training artifact is supplied for a requested check;
  - flag `flyer_intent_training_export_stale` when the artifact is older than the configured threshold;
  - flag `flyer_intent_training_export_redaction_failed` if unsafe fields appear.

- [ ] **Step B5: Install/smoke path.**

  Install `flyer-intent-training-export` as a Flyer script and smoke `--help` plus a fixture-based dry run.

- [ ] **Step B6: Verification and PR-B review.**

  Run:

  ```powershell
  python -m pytest tests/test_flyer_intent_training.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py tests/test_flyer_scripts_static.py -q
  python -m py_compile src/agents/flyer/intent_training.py tools/flyer-self-evaluation.py tools/operator-brief.py
  python src/agents/flyer/scripts/flyer-intent-training-export --decisions-log tests/fixtures/flyer_self_eval/decisions.log --out .qa_outputs/flyer_intent_training_smoke.jsonl --format jsonl
  git diff --check
  ```

  Open PR-B with "No deploy performed." Request two reviewers:
  1. Learning-data reviewer: prove examples are useful, redacted, deduped, and outcome-linked.
  2. Hermes-first/runtime reviewer: prove training export is offline/read-only and does not mutate prompts/code/customer state.

## Acceptance

- PR-A produces real advisory Hermes classifier decisions in shadow mode without changing customer behavior.
- PR-A keeps unsupported active modes inert and loud.
- PR-A route-invariance tests pass with conflicting Hermes decisions.
- PR-A surfaces classifier `success|skipped|timeout|invalid|error` distinctly from classifier intentionally off.
- PR-B produces redacted, deterministic training examples from typed audit rows without copying raw state/project/customer dictionaries.
- PR-B makes training export freshness/quality visible to self-eval/operator brief when expected.
- Neither PR deploys, sends WhatsApp messages, changes provider routing, changes source-edit behavior, or mutates production state.
- Active low-risk routing remains a future PR after soak thresholds.

## Shadow Soak Gate For Future Active Routing

This plan does not enable active routing. A later active-mode PR must prove all of the following from deployed shadow evidence:

- At least 25 shadow classifier fires total and at least 5 fires each for `new_project`, `revision`, `approval/status`, and `account/onboarding` route families, or an operator-approved narrower family gate.
- `classifier_status="success"` for at least 95% of eligible candidate rows.
- No route mutation and no customer-send delta attributable to shadow.
- p95 shadow classifier latency under 250 ms when run in the post-route finalization path, or a non-blocking/background execution proof.
- Validator rejection rate below 10% overall and zero customer-copy policy violations in accepted decisions.
- Source-edit automation decisions remain rejected unless source-edit provider smoke is separately approved.
- Self-eval command with `--expected-hermes-intent-mode shadow --expect-flyer-intent-training-export` reports no active high/critical Hermes intent or memory incidents.
