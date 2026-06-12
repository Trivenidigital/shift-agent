**Drift-check tag:** extends-Hermes

# Flyer Studio customer-readiness stabilization gate — plan

Date: 2026-05-21
Branch: `codex/flyer-customer-readiness-gate`
Base: `origin/main` @ `ee8533a` (PR #159 merged)

## Why this exists

Flyer Studio is days away from customer rollout. Recent failures came from lifecycle drift, customer-copy leaks, stale active projects, poisoned facts, and missing replay coverage. The operator needs a *deterministic, offline* gate that tells them green / yellow / red with reasons BEFORE authorizing customer onboarding — without sending WhatsApp, without mutating customer state, without re-running paid model smokes.

## Hermes-first capability checklist

End-to-end flow:

1. Operator runs the readiness CLI on a host with the repo checked out.
2. CLI loads an optional input fixture for bridge / gateway / cockpit posture + deploy marker + open-PR list (host-side facts the repo cannot self-derive).
3. CLI enumerates open Flyer PRs and merged-not-deployed deltas from the fixture (offline; no live GitHub call).
4. CLI inspects the input fixture for `source_edit_provider_policy` posture (configured vs unset/manual_review) — runtime config.yaml is **not read** from the worktree; posture is host-supplied.
5. CLI scans `state/flyer/**` + `decisions.log` for stale manual-queue, active customer-risk, customer-copy-leak, and duplicate-ack incidents — already implemented as v0.1 incident detection.
6. CLI consumes a replay-summary input documenting outcomes of 8 deterministic rollout scenarios; the pytest harness in `tests/test_flyer_rollout_replay.py` actually runs the scenarios in CI.
7. CLI aggregates verdicts into a green/yellow/red rollout report with reasons (extends the existing self-eval status, which is incident-driven, into a rollout-decision verdict that also considers posture inputs + replay summary).
8. CLI renders JSON + Markdown.
9. Customer-copy guard reuses the existing `customer_copy_policy.py` scanner — 7 of 8 task-required tokens are already in `BANNED_CUSTOMER_COPY_TERMS`; the 8th (`raw/original request echo`) is detected via `scan_customer_text`'s `raw_request_echo` category.
10. Pytest replay harness asserts lifecycle + copy-guard for each scenario.
11. Source-edit yellow rule: `source_edit_provider_policy ∈ {unset, manual_review}` OR no spend-gated smoke evidence ⇒ readiness=yellow with explicit reason text.
12. CLI / docs ensure deferred-item exists for 5-10 case source-edit smoke.
13. Operator reads JSON/Markdown verdict and decides.

| Step | Tag | Capability cited |
|---|---|---|
| 1. Operator runs Python CLI | `[Hermes]` — per-VPS filesystem + operator-script pattern; `tools/operator-brief.py` is precedent | 0 |
| 2. Load posture input fixture | `[net-new]` — host-side posture inputs not produced by Hermes substrate | ~40 LOC |
| 3. Enumerate open PRs / merged-not-deployed | `[net-new]` — offline-only PR enumeration; no Hermes GitHub-list capability | ~25 LOC |
| 4. Read source-edit posture from fixture | `[net-new]` — product policy interpretation | ~15 LOC |
| 5. Scan state + decisions.log for incidents | `[Hermes]` — `decisions.log` audit-chain substrate; existing `customer_copy_incidents`, `manual_source_edit_stale`, `duplicate_initial_ack_incidents` already cover this | 0 |
| 6. Replay 8 rollout scenarios | `[Hermes]`-adjacent — `tests/test_flyer_incident_replay.py` already calls `hooks.pre_gateway_dispatch` end-to-end with mocked safe_io; we add 4 new fixtures + reuse the harness | ~80 LOC test wiring |
| 7. Aggregate verdict | `[net-new]` — rollout-decision rules (different from incident-only status) | ~90 LOC |
| 8. Render JSON + Markdown | `[Hermes]` — operator-brief substrate already renders Markdown; flyer-self-evaluation already emits JSON+Markdown | 0 |
| 9. Customer-copy guard | `[Hermes]` — `customer_copy_policy.py:scan_customer_text` already implemented; 8/8 task tokens already covered | 0 |
| 10. Pytest replay assertions | `[net-new]` — fixture data + assertion wiring (harness exists) | ~220 LOC |
| 11. Source-edit yellow rule | `[net-new]` — product policy | ~20 LOC |
| 12. Doc edit (deferred item) | `[net-new]` — trivial | ~10 LOC text |
| 13. Operator reads stdout | `[Hermes]` — CLI stdout | 0 |

**Hermes:** steps 1, 5, 6, 8, 9, 13. **Net-new:** steps 2, 3, 4, 7, 10, 11, 12.

Per the **install-now skill list** in CLAUDE.md (4-source ecosystem audit, 2026-05-03): none of `google-workspace`, `maps`, `airtable`, `ocr-and-documents`, `notion`, or `native-mcp` apply to an offline rollout-readiness gate. This is product-policy + test-fixtures + report-shape work — substrate-free.

## Drift-rule self-checks

- ✅ Read `tools/flyer-self-evaluation.py` (full 1217-LOC file; confirmed existing `build_report`, `customer_copy_incidents`, `manual_source_edit_stale`, `duplicate_initial_ack_incidents`, `sanitize_report`, severity-rank verdict at line 1099-1101) before drafting the readiness-report extension
- ✅ Read `src/agents/flyer/customer_copy_policy.py` (full 228-LOC module; confirmed all 8 task-required forbidden tokens already exist in `BANNED_CUSTOMER_COPY_TERMS` + `raw_request_echo` category at line 109) before designing the copy-guard surface — **no new copy-guard module is needed**
- ✅ Read `src/platform/schemas.py` (lines 825-933; `FlyerSourceEditProviderPolicy` + `resolve_source_edit_render_provider`) before drafting the source-edit posture rule
- ✅ Read `tests/test_flyer_incident_replay.py` (full 391-LOC test; harness `_install_common_replay_mocks` + `pre_gateway_dispatch` + `scan_customer_text` assertion at line 310) before designing the rollout-replay shape — **harness is reused, not rewritten**
- ✅ Read `tests/fixtures/flyer_incident_replay/flyer_incidents.json` (9 existing fixtures) before mapping 4 of the 8 task scenarios to existing IDs and 4 to net-new fixtures
- ✅ Read `tools/operator-brief.py` (full 525-LOC tool; confirmed `--flyer-evaluation-json` already consumes self-eval JSON) before deciding to layer rollout-readiness into the existing self-eval JSON instead of creating a new tool
- ✅ Grepped `src/plugins/cf-router/actions.py` for `consume_flyer_sample_idea`/`approved_brief`/`guided_mode` helper names (PR #158 surfaces) before scoping the new rollout-replay scenarios

## Drift-check on referenced PRs

The task brief listed both #154 and #159 as open. Reality at `origin/main` HEAD `ee8533a`:

| PR | Title | State (verified 2026-05-21) | Sequencing |
|---|---|---|---|
| #147 | source-edit provider config routing | **MERGED** 2026-05-20T23:49Z | Provider-policy schema lives on main. `source_edit_provider_policy.default` points to OpenRouter, but `resolve_source_edit_render_provider()` falls back to `manual_review` when the policy is not explicitly present in runtime `config.yaml`. The readiness gate must check the **runtime posture** (via input fixture), not the schema default. |
| #149 | stale source-edit manual queue alert | **MERGED** | Watchdog deployed. The gate consumes its incidents via self-eval; no duplicate work. |
| #154 | Fix Flyer schedule through-day ranges | **OPEN** as of now | Pure parsing fix; no replay-harness or self-eval surface overlap. Document but do NOT duplicate. Sequencing: ship #154 first (small, isolated) then rebase this PR if needed. |
| #157 | accept visible time text revisions | **MERGED** 2026-05-21T13:37Z | Powers the "visible text removal stays revision" replay scenario. Use as-merged in fixtures. |
| #158 | flyer brief builder intake | **MERGED** | Powers the sample-idea, guided-flow, text-intelligent-brief, and approved-brief rollout paths. Use as-merged in fixtures. |
| #159 | gate fuzzy revisions behind APPLY | **MERGED** 2026-05-21T18:00Z (task brief stated open) | No overlap; merged. |

**Net sequencing:** only #154 is open; it does not overlap this PR. **#159 sequencing note (task brief was stale): closed.**

## Deployed-pattern checklist (Part-1 discipline)

- **Storage:** read-only CLI; no JSON writes outside the explicit report `--out`. Uses `safe_io.atomic_write_text` (already wired in self-eval).
- **NDJSON audit log:** consume-only via existing `load_decisions_log`. No new `LogEntry` variant — the gate emits a report, not audit entries.
- **Schemas:** input-fixture model uses Pydantic v2 with `model_config = ConfigDict(extra="forbid")`.
- **Tests:** subprocess-invoke pattern for CLI smoke (matches `test_catering_v02_scripts.py`); in-process tests for pure helpers (verdict aggregator, posture rule).
- **Per-customer-VPS isolation:** the readiness gate is operator-side; it scans local files only — no cross-VPS state assumed.

No Part-1 pattern is violated.

## Scope (mapped to user-supplied scope items)

### Item 1 — Drift-check open PRs #154 / #159

Done above. **#159 is merged**; **#154 is open** and orthogonal. Sequencing documented: keep our PR out of #154's parser area; no overlap expected.

### Item 2 — Readiness CLI/report

**Preferred shape (chosen):** extend `tools/flyer-self-evaluation.py` with a `--rollout-readiness` mode that accepts an `--input` fixture and emits a rollout-readiness section layered on top of the existing self-eval report. Operator-brief already takes `--flyer-evaluation-json`; with the rollout-readiness section present in the JSON, operator-brief automatically picks it up via a small additive summarizer — no new top-level wrapper needed.

Report fields (additive to current `build_report` JSON):

| Field | Source | Defaults |
|---|---|---|
| `rollout.open_flyer_prs` | input fixture `open_prs` | `[]` |
| `rollout.merged_not_deployed` | input fixture `merged_not_deployed` | `[]` |
| `rollout.deploy_marker` | input fixture `deploy_marker` | `""` |
| `rollout.bridge_status` | input fixture `bridge_status` ∈ {`connected`,`disconnected`,`unknown`} | `unknown` |
| `rollout.gateway_status` | input fixture `gateway_status` ∈ {`active`,`inactive`,`unknown`} | `unknown` |
| `rollout.cockpit_status` | input fixture `cockpit_status` ∈ {`healthy`,`degraded`,`unknown`} | `unknown` |
| `rollout.source_edit_posture` | input fixture `host_supplied_source_edit_posture` ∈ {`configured_with_smoke`, `configured_with_smoke_stale`, `configured_no_smoke`, `manual_review`, `unset`} | `unset` |
| `rollout.stale_manual_queue_incidents` | count of `manual_source_edit_stale` from existing incident set | derived |
| `rollout.active_customer_risk_incidents` | count of existing incidents where `evidence_details.active_customer_risk == True` | derived |
| `rollout.customer_copy_leak_incidents` | count of `customer_copy_internal_leak` from existing incident set | derived |
| `rollout.duplicate_initial_ack_incidents` | count of `duplicate_initial_ack` | derived |
| `rollout.replay_summary` | `{ "total": N, "passed": M, "failed_ids": [...] }` from the new rollout-replay test layer (CI-supplied JSON or `--replay-summary-json <path>`) | derived |
| `rollout.verdict` | `green` / `yellow` / `red` | computed |
| `rollout.reasons` | list of human-readable reason strings explaining the verdict | computed |

**Replay summary source.** The CLI does not invoke pytest internally. The replay outcome is consumed from the input fixture's `replay_summary` field, populated by CI running the new `tests/test_flyer_rollout_replay.py`. This keeps the CLI fully offline and deterministic; the user can also pass `--replay-summary-json <path>` for ad-hoc operator runs.

### Item 3 — 8 deterministic rollout-replay scenarios

Existing fixtures in `tests/fixtures/flyer_incident_replay/flyer_incidents.json` (9 scenarios) already cover 4 of 8 task scenarios. The remaining 4 + the explicit "visible-text-removal stays revision" all fit the *existing harness* (`_install_common_replay_mocks` + `pre_gateway_dispatch`). The new fixtures live in `tests/fixtures/flyer_rollout_replay/flyer_rollout_paths.json` to keep the rollout-decision set separate from generic incident-replay (per recent lessons: "self-eval incidents with different meanings must not share the strictest evidence threshold" — same principle applies to fixture-set provenance).

Mapping:

| # | Task path | Source |
|---|---|---|
| 1 | active/trial sample idea → brief preview → approve → project | NEW — exercises PR #158 sample-idea + approve path |
| 2 | new trial chooses sample before onboarding → onboarding → compact ideas | NEW — exercises #158 + onboarding chain |
| 3 | text request → intelligent brief → approve → project | NEW — exercises #158 brief builder |
| 4 | guided flow → brief → approve | NEW — exercises #158 guided mode |
| 5 | vague "create flyer" → idea picker, not blank project | REUSE `vague-create-flyer-clarifies-without-project` |
| 6 | small revision like "make it red" stays revision | REUSE `small-revision-make-it-red-stays-revision` |
| 7 | visible text removal like duplicated HH:MM time stays revision | NEW — exercises #157 |
| 8 | source edit / co-owner path stays manual-review / provider-gated | REUSE `F0063-source-choice-queues-manual-edit` |
| 9 | status check does not create / revise a project | REUSE `status-check-does-not-create-or-revise` |
| 10 | LID-only sender taps "Start Free Trial" → onboarding (no phone resolution) | NEW — covers 2026-05-15 LID-only CTA fallthrough failure mode (folded from rollout reviewer C2) |
| 11 | duplicate-phone second sender → recognized as authorized requester, no duplicate-phone-error trap | NEW — covers 2026-05-15 duplicate-phone recovery failure mode (folded from rollout reviewer C2) |

Task says "at least 8" — we ship 11 (the listed mapping). The reused-from-incident set is referenced by id, not duplicated; the new fixture file lists 6 net-new fixtures + 4 cross-refs (string ids the rollout-replay test resolves from the incident-replay file).

**Customer-copy guard coverage caveat (folded from rollout reviewer C1).** `scan_customer_text` only emits a `raw_request_echo` hit when `len(normalized_raw) >= 8` (`customer_copy_policy.py:109`). Scenarios 5 (`create flyer`), 8 (`SOURCE`), and 9 (`status?`) have raw texts too short to exercise echo. To prove coverage of the `raw/original request echo` token across the rollout set, the rollout-replay test includes an **explicit echo-leak assertion** in scenarios 1, 3, 7, and 10 (all with raw_request length ≥ 20) — these MUST flag if any sent text contains the raw request substring. Short-text scenarios assert only the 7 fixed-token categories.

### Item 4 — Customer-copy guard

The 8 task-required forbidden tokens are **already in** `BANNED_CUSTOMER_COPY_TERMS` or detected via `raw_request_echo`:

| Task token | Coverage |
|---|---|
| `Project F` | `BANNED_CUSTOMER_COPY_TERMS` line 19 |
| `created flyer project` | `BANNED_CUSTOMER_COPY_TERMS` line 18 |
| `provider` | line 27 |
| `reason_code` | line 28 |
| `manual_edit_required` | line 26 |
| `operator` | line 25 |
| `raw/original request echo` | `scan_customer_text` `raw_request_echo` category (lines 109-110) |
| `source-preserving workflow` | line 23 |

The rollout-replay test asserts `scan_customer_text(sent_text, raw_request=fixture["text"]).hits == ()` for every captured outbound message in every fixture. **No new copy-guard module is created.** This means audit/Cockpit detail remains intact (those are inspected via different code paths that the policy scanner does NOT see).

### Item 5 — Source-edit yellow rule

Detection logic (`src/agents/flyer/rollout_readiness.py:compute_source_edit_posture`):

Field name is `host_supplied_source_edit_posture` (folded from drift-reviewer nit) — provenance is explicit at the field level: this is operator-supplied truth, not derived from runtime config.

| Input fixture `host_supplied_source_edit_posture` | Verdict contribution |
|---|---|
| `"configured_with_smoke"` (explicit policy + key + spend-gated smoke evidence + recent vs. last provider-routing commit) | green |
| `"configured_with_smoke_stale"` (smoke evidence exists but is older than the latest commit touching `resolve_source_edit_render_provider`; operator supplies via fixture `source_edit_smoke_evidence_age_days` field + a `provider_routing_changed_at_iso` field) | yellow with reason `"source-edit smoke evidence stale vs. latest provider-routing change"` |
| `"configured_no_smoke"` (policy set but no smoke evidence) | yellow with reason `"source-edit policy configured but spend-gated 5-10 case smoke evidence missing"` |
| `"manual_review"` (explicit `emergency_fallback` setting) | yellow with reason `"source-edit runs through manual_review fallback"` |
| `"unset"` (no posture data passed) | yellow with reason `"source-edit policy posture not supplied; defaulting to manual_review/yellow"` |

No code in this PR enables `source_edit_provider_policy`; the runtime config.yaml is not touched.

### Verdict rules

`compute_rollout_verdict(report) -> ("green" | "yellow" | "red", reasons[])`:

To avoid two parallel color-threshold computers, the helper first calls the existing severity-rank logic at `tools/flyer-self-evaluation.py:1099-1101` to derive the incident-only color, then ORs it with the non-incident dimensions below. Single-sourced color thresholds (folded from drift reviewer H1):

- **RED** if any one is true:
  - severity-rank incident color is `"red"` (e.g., any high/critical incident with `active_customer_risk=true`)
  - any `customer_copy_internal_leak` incident with `active_customer_risk=true`
  - any `duplicate_initial_ack` incident with `active_customer_risk=true`
  - `bridge_status` or `gateway_status` is `"disconnected"`/`"inactive"`
  - `rollout.replay_summary.failed_ids` is non-empty
  - any `manual_source_edit_stale` incident with `queued_age_minutes >= manual_stale_red_minutes` (default **30 min**, configurable; matches the detector's own 30 min default per `tools/flyer-self-evaluation.py:1183` — folded from rollout reviewer H1)
  - `merged_not_deployed` non-empty AND any entry has `customer_risk_label ∈ {"customer-routing","lifecycle","copy","payment"}` (folded from rollout reviewer H2)
- **YELLOW** if any one is true (and no RED):
  - severity-rank incident color is `"yellow"`
  - `source_edit_posture ∈ {configured_with_smoke_stale, configured_no_smoke, manual_review, unset}`
  - `bridge_status`/`gateway_status`/`cockpit_status == "unknown"` (no posture supplied)
  - any open PR labelled customer-risk (configurable via fixture `open_prs[].customer_risk = true`)
  - `merged_not_deployed` non-empty (and no customer-risk label, i.e. lower-severity merge backlog)
  - any `active_customer_risk_incidents > 0`
  - `manual_source_edit_stale` incidents present at age < manual_stale_red_minutes
  - `deploy_marker == ""`
  - `replay_summary` not supplied (folded from rollout reviewer H4) — reason `"replay summary not supplied; rollout decision is unsafe without it"`
- **GREEN** only if none of the above fire.

Note: the existing self-eval status (`report.status`) remains as-is and is preserved alongside `rollout.verdict`. They answer different questions:
- `report.status` = "are there any current Flyer incidents in state/decisions?" (operator-incident view)
- `rollout.verdict` = "is this VPS ready for paying customers in the next few days?" (rollout-decision view)

### Item 6 — Source-edit readiness language

`tasks/todo.md` gets:
- A new top-level item under the active rollout work: *Customer-readiness gate — green/yellow/red*
- A deferred entry: *5-10 case source-edit visual-quality smoke* (if not already present in active source-edit backlog; verified at build time)
- A short sequencing note for PR #154

### Out of scope

Per user: no provider-routing changes; no dashboard UI; no deploy; no WhatsApp sends; no customer/payment/manual-queue mutation; no new paid-model smoke; no broad refactor.

This plan adds: 1 input-fixture schema, 1 verdict-rule helper, 1 source-edit posture helper, 1 new replay-fixture file, 1 new pytest, and the report-shape extension. Everything else is reuse.

## Concrete file plan

```
tools/flyer-self-evaluation.py                  edit  ~80 LOC + ~30 LOC tests in existing test
src/agents/flyer/rollout_readiness.py           new   ~140 LOC (verdict rules, posture rule, input-fixture schema; calls severity_rank)
tests/test_flyer_rollout_readiness.py           new   ~180 LOC (~10 unit tests on verdict + posture + schema)
tests/test_flyer_rollout_replay.py              new   ~260 LOC (11 scenario tests reusing harness)
tests/_flyer_replay_helpers.py                  new   ~80 LOC (extract _install_common_replay_mocks; shared by both replay tests — folded from drift reviewer Important #1)
tests/fixtures/flyer_rollout_readiness/         new   3 small JSON fixtures (green / yellow / red exemplars)
tests/fixtures/flyer_rollout_replay/            new   1 JSON file + 6 net-new scenarios; cross-refs by id
tasks/todo.md                                   edit  add deferred 5-10 case source-edit smoke + sequencing notes
tools/operator-brief.py                         edit  +~25 LOC to summarize `rollout` block when present (banner line at top + grouped reasons)
```

Estimated total: **~700 LOC + ~20 tests across 7 commits** (one extra commit for the helper extraction).

## Commit sequence

| # | Commit | Files | LOC |
|---|---|---|---|
| C1 | `test(flyer): extract _install_common_replay_mocks into shared helper` | `tests/_flyer_replay_helpers.py` + 2 import edits | ~80 |
| C2 | `feat(flyer): add rollout-readiness input fixture schema + verdict helper` (calls existing severity_rank) | `src/agents/flyer/rollout_readiness.py` + unit tests | ~170 |
| C3 | `feat(flyer): add source-edit posture rule for rollout readiness` (5 posture states) | edit `rollout_readiness.py` + unit tests | ~70 |
| C4 | `feat(flyer): wire rollout-readiness mode into flyer-self-evaluation + operator-brief` | `tools/flyer-self-evaluation.py`, `tools/operator-brief.py` + CLI smoke | ~140 |
| C5 | `test(flyer): add 6 net-new rollout-replay scenarios + cross-refs` | `tests/fixtures/flyer_rollout_replay/*.json` | ~120 fixture lines |
| C6 | `test(flyer): add rollout-replay pytest reusing extracted harness` | `tests/test_flyer_rollout_replay.py` | ~260 |
| C7 | `docs: record source-edit smoke deferred item + #154 sequencing` | `tasks/todo.md` | ~30 text |

## Tests

In-process:
- `tests/test_flyer_rollout_readiness.py::test_input_fixture_extra_forbid` — Pydantic schema rejects unknown keys
- `..::test_verdict_green_when_all_clear` — happy-path fixture
- `..::test_verdict_yellow_on_unset_source_edit_policy` — source-edit yellow rule
- `..::test_verdict_yellow_on_merged_not_deployed`
- `..::test_verdict_yellow_on_bridge_unknown_no_posture`
- `..::test_verdict_red_on_customer_copy_leak_active_risk`
- `..::test_verdict_red_on_replay_failed`
- `..::test_verdict_red_on_disconnected_bridge`
- `..::test_source_edit_posture_all_four_states`

Replay (reusing `_install_common_replay_mocks` from `test_flyer_incident_replay.py`):
- 4 net-new + 4 cross-ref id assertions; total ≥8 scenarios; each asserts `scan_customer_text` is clean for every sent message.

CLI smoke (subprocess):
- `python tools/flyer-self-evaluation.py --rollout-readiness --input <fixture> --format json` → JSON valid + `rollout.verdict` present + reasons grouped by severity
- `python tools/flyer-self-evaluation.py --rollout-readiness --input <fixture> --format markdown` → contains `## Rollout Readiness` section with banner line `**Rollout: RED — N reasons**` at top and reasons grouped RED-first then YELLOW (folded from rollout reviewer Important #2 and #3)
- `python tools/operator-brief.py --flyer-evaluation-json <self-eval-out>` → brief contains rollout banner line as its first Flyer Studio line

## Verification

- `python -m pytest tests/test_flyer_incident_replay.py tests/test_flyer_rollout_replay.py tests/test_flyer_rollout_readiness.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py tests/test_cf_router_flyer_routing.py tests/test_flyer_customer_copy_policy.py -q`
- `python -m py_compile tools/flyer-self-evaluation.py tools/operator-brief.py src/agents/flyer/rollout_readiness.py tests/test_flyer_rollout_replay.py tests/test_flyer_rollout_readiness.py`
- CLI smoke pair (JSON + Markdown) — captured into a temp file and asserted by content
- `git diff --check`

## Acceptance (mapped to user's list)

- Customer-readiness report clearly tells operator green/yellow/red for rollout ✓ (`rollout.verdict` + reasons)
- ≥8 rollout replay scenarios ✓ (4 new + 4 cross-ref + 1 control = 9)
- Active customer-risk separated from historical/audit-only ✓ (already separated; rollout RED only fires on active risk)
- Existing #150 #151 #152 #155 #157 #158 behavior intact ✓ (no source edits; no fixture renames; only additive surfaces)
- #154 / #159 sequencing documented ✓ (above)
- Focused tests pass ✓
- py_compile passes ✓
- git diff --check passes ✓
- PR summary includes files / tests / risks / deferred items / "No deploy performed." ✓

## Risks

1. **Replay-harness coupling.** The new replay test imports the existing harness helpers. Risk: if `test_flyer_incident_replay.py` evolves, the rollout-replay test can drift. Mitigation: extract `_install_common_replay_mocks` into a shared `tests/_flyer_replay_helpers.py` module if and only if collision pressure appears at review time; otherwise duplicate the few lines of mock setup deliberately.
2. **Input-fixture schema lock-in.** Pydantic `extra="forbid"` means future fields require a coordinated host-side update. Mitigation: document the JSON schema in the plan + design + a comment block at the top of the rollout-readiness module.
3. **Source-edit policy interpretation drift.** The runtime config.yaml on prod could explicitly set `source_edit_provider_policy.default` to `openrouter` while the rollout-readiness fixture states `unset`. The gate would then say yellow while reality is configured. Mitigation: the input-fixture must be operator-supplied as the source of truth for the host's current posture; readiness language says "as reported by the host posture input." A separate `pilot-readiness-check`-style probe runs ON the VPS and is the canonical source — outside this PR's scope.
4. **PR #154 race.** If #154 lands before this PR, rebase. If after, the readiness gate is unaffected (different surfaces).
5. **Hermes ecosystem drift.** None expected — this is offline product-policy + test work.

## Deferred items

- Spend-gated 5-10 case source-edit visual-quality smoke. Owner: Session 3 / next operator authorization. Blocking for green rollout posture on source-edit path. (Recorded in `tasks/todo.md` if not already present; verified during build.)
- Optional fleet-style probe that *replaces* the input-fixture posture with a live on-VPS probe (pilot-readiness-check integration). Deferred; current offline shape is sufficient for the next-few-days rollout decision.
- Multi-turn co-owner reference-scope-memory replay variant (folded from rollout reviewer Important #4). Scenario 8 reuses `F0063-source-choice-queues-manual-edit` which is single-turn `SOURCE`; the 2026-05-19 lesson covering remembered relationship across follow-ups is not exercised here. Add when the source-edit pipeline graduates to a customer-facing path.

(The "Out of scope" section is the single one above the Acceptance / Risks block in §Scope — duplicate trailing section removed per drift reviewer nit.)

## Process

After this plan is reviewed by 2 parallel reviewers (one rollout-behavior-focused, one Hermes-first/drift-focused), I fold Critical/High/Important findings, then draft a design doc with concrete signatures / Pydantic field types / verdict-rule pseudocode, send it through 2 more parallel reviewers, fold findings, and only then start the build.
