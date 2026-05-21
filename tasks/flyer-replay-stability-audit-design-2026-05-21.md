**Drift-check tag:** extends-Hermes

# Flyer Replay Stability Audit Design - 2026-05-21

## New primitives introduced

- `tests/fixtures/flyer_incident_replay/*.json`: redacted, offline, WhatsApp-shaped incident replay scenarios for Flyer-specific hook/state/customer-output failures.
- `tests/test_flyer_incident_replay.py`: a thin Flyer hook replay adapter around existing cf-router test seams. It does not replace `tests/_dispatcher_replay.py`.
- Shared customer-copy policy helper, extracted from existing self-eval/test constants, used by `tools/flyer-self-evaluation.py` and customer-copy tests.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress, sender identity, bridge delivery | Hermes/cf-router owns live ingress and delivery | Replay uses redacted event-shaped fixtures; no live sends or new ingress. |
| Dispatcher replay | Existing `tests/_dispatcher_replay.py` + `extract-replay-fixtures` | Reuse for handler-selection questions; do not duplicate dispatcher replay. |
| Audit/state substrate | Hermes/Flyer JSON state, decisions.log, `safe_io` | Use temp JSON/NDJSON fixtures and existing load paths; no new storage. |
| LLM/vision/OCR | Hermes gateway and installed OCR/vision skills | Mock provider/generation/QA paths; no provider calls. |
| Flyer product contract | No Hermes generic skill for business identity, campaign title, source-contract QA, or customer-copy wording | Flyer owns deterministic policy, replay assertions, and incident reporting. |
| Manual source-edit SLA alerting | PR #149 watchdog owns timer/alert | Do not add an alert timer; only preserve/read existing self-eval/operator signals. |

Awesome-Hermes ecosystem check: checked the Hermes Skills Hub and awesome-hermes-agent index; no turnkey Flyer Studio incident replay/customer-copy policy exists. Verdict: reuse Hermes substrate and add only the Flyer-owned offline guardrails.

## Failure Taxonomy

1. **Route/lifecycle mismatch:** a customer status check, small revision, or new flyer request reaches the wrong branch because active-project state dominates message intent.
2. **Fact-contract poisoning:** normal campaign text overwrites profile `business_name`, or source-contract facts are faithfully checked after the wrong identity was locked.
3. **Source-contract visibility gap:** source/reference projects can proceed without source-aware QA evidence or queue manual review without active-risk reporting.
4. **Customer-copy leak:** outbound text exposes project ids, provider/runtime terms, reason codes, raw request echoes, or operator workflow terms.
5. **Duplicate lifecycle ack:** one inbound produces both intake and processing acknowledgements.
6. **Preview/final QA mismatch:** customer receives preview/approval loop, then final QA fails without active operator risk visibility.
7. **Silent side effect in tests:** replay accidentally invokes bridge, subprocess, `/opt/shift-agent`, SSH/scp, or production-like mutation instead of failing noisily.

## Fixture Shape

Each fixture is a single JSON object:

```json
{
  "id": "F0065-evening-snacks-new-over-active",
  "description": "new campaign request while old project exists",
  "initial_state": {
    "projects": [
      {
        "project_id": "F0064",
        "status": "awaiting_final_approval",
        "raw_request": "Old lunch special flyer",
        "fields": {"event_or_business_name": "Lunch Specials"},
        "concepts": [{"concept_id": "C1"}],
        "updated_at": "2026-05-20T20:00:00Z"
      }
    ],
    "customers": [
      {
        "customer_id": "CUST0001",
        "status": "trial",
        "business_name": "Lakshmis Kitchen",
        "phone": "+17329837841",
        "primary_chat_id": "17329837841@lid"
      }
    ]
  },
  "events": [
    {
      "message_id": "m-1",
      "chat_id": "17329837841@lid",
      "text": "Create flyer for evening snacks Wednesday to Saturday 5pm to 9pm",
      "media_path": null,
      "resolved_identity": {"phone": "+17329837841", "role": "customer"},
      "shape": "nested_source"
    }
  ],
  "mocks": {
    "active_project": "from_initial_state",
    "create_project": {"mode": "real_tmp_state"},
    "generate_concepts": {"ok": false, "detail": "visual_qa_failed: missing required visible fact: business_name"}
  },
  "expect": {
    "route": "new_project",
    "project_status": "manual_edit_required",
    "business_name": "Lakshmis Kitchen",
    "campaign_title": "Evening Snacks",
    "outbound_policy": {"must_pass": true, "raw_request_echo": false},
    "duplicate_initial_ack": false,
    "manual_review_reason_code": "visual_qa_failed",
    "qa": "fail"
  },
  "expect_order": [
    "identity_resolved",
    "active_project_bypassed_audit",
    "create_project",
    "processing_ack",
    "generate_concepts",
    "manual_review_ack"
  ],
  "forbidden_calls": ["invoke_update_flyer_project", "finalize_and_send_flyer"]
}
```

Fixtures intentionally assert product outcomes, not exact prose, except for banned customer-copy policy matches and duplicate-ack markers. This avoids brittle wording tests while still catching customer-visible leaks.

## Replay Adapter

`tests/test_flyer_incident_replay.py` will:

- Load fixtures from `tests/fixtures/flyer_incident_replay/*.json`.
- Build event objects matching cf-router hook extraction helpers, including direct `text/chat_id`, nested `source.chat_id`, `body`, `message`, and media-path variants promoted from redacted live fixture shapes.
- Load cf-router modules with the existing artificial-package pattern from `tests/test_cf_router_flyer_routing.py`; patch the returned `actions_mod` object used by `hooks_mod.actions`, not an import path string.
- Capture outbound bodies from `send_flyer_text`, `send_flyer_processing_ack`, `send_flyer_intake_ack`, `send_flyer_manual_review_ack`, `send_flyer_manual_edit_ack`, `send_flyer_edit_processing_ack`, and preview/media-send wrappers.
- Capture final approval/package output surfaces by mocking `actions.finalize_and_send_flyer` and, where the package script is tested directly, `bridge_send_media`/upsell text from `src/agents/flyer/scripts/send-flyer-package` without invoking live subprocesses.
- Fail closed on unexpected live surfaces: `subprocess.run`, bridge imports, SSH/scp command strings, `/opt/shift-agent` reads or writes, and manual/payment/customer mutations not explicitly mocked by the fixture. All state paths are rebound to `tmp_path`.
- Run each event through `hooks.pre_gateway_dispatch` in order and assert route/action, audit/call order, forbidden calls, create/update/finalize calls, project/fact evidence, outbound policy scan, duplicate ack status, typed `manual_review.reason_code`, and QA expectation.

The adapter is deliberately narrower than dispatcher replay. Dispatcher replay answers “which handler should the LLM choose?” The Flyer adapter answers “once cf-router owns this message, did the deterministic Flyer lifecycle branch produce safe state and safe customer output?”

Scenario execution modes:

- `real_tmp_state`: use the real `create-flyer-project` script/module against temp Flyer state and customer files, while mocking generation/send/provider surfaces. This is required for F0065-class business/campaign/fact-contract replay.
- `strict_fake`: use stateful fakes that assert exact input arguments and emit realistic audit/project shapes. This is allowed for branch-order-only scenarios where the real script would duplicate existing create-project tests.
- `report_only`: build temp projects/decisions logs and run self-eval/operator brief without invoking hooks.

## Scenario Coverage

| Scenario | Existing coverage | Replay residual covered here |
|---|---|---|
| Source flyer exact edit + co-owner reply | create-project source-contract tests and source/new choice tests | Branch-order through `pre_gateway_dispatch`; SOURCE/NEW clarification/claim path; outbound policy scan. |
| `any update?` on queued edit | status reply tests and self-eval SLA reports | No create/revise; customer-safe status copy; active-risk classification remains report-only. |
| New evening snacks while old project exists | #150/#152 routing/create tests | Multi-message stale-active replay; new-project path wins; no revision ack. |
| APPROVE after preview then final QA fails | self-eval preview/final mismatch | Approval route captures failure and active customer risk; customer copy stays outcome-only. |
| Vague `create flyer` | starter/clarification tests | No production mutation beyond safe clarification; no internal leak. |
| Small revision `make it red` | active-project revision tests | No new project; one safe revision ack; no duplicate initial ack. |
| Status check must not create/revise | latest-status tests | Full branch-order replay with create/update/generate/finalize surfaces fail-closed. |
| F0065 business/campaign split | create-project `business_name`/`campaign_title` tests | Prompt/manifest/QA evidence preserves profile business and campaign title separately. |

Each classifier row gets at least one nearby non-incident control where useful: vague start vs complete flyer brief, small revision vs new red sale poster, status check vs fresh request, and active delivered historical vs active manual queue risk.

## Customer-Copy Policy Helper

Preferred module: `src/agents/flyer/customer_copy_policy.py`.

It will be deterministic and offline:

- `BANNED_CUSTOMER_COPY_TERMS`: existing `INTERNAL_COPY_TERMS` moved without changing incident semantics.
- `STATIC_CUSTOMER_COPY_FUNCTIONS`: existing source-scan allowlist moved.
- `DUPLICATE_INITIAL_ACK_MARKERS`: existing processing/intake marker phrases moved.
- Project-id/internal pattern scanner: normalized `F0065`, `F-0065`, `Project F0065`, workflow terms, provider/runtime terms, and raw-request echo checks with explicit allowlists for audit-only text.
- `scan_customer_text(text) -> list[str]`: case-insensitive banned-term scan.
- `scan_outbound_entry(entry) -> CustomerCopyScan`: extracts known outbound fields and scans them.
- `classify_initial_ack(text) -> set[str]`: returns `processing`/`intake` markers.
- `extract_customer_copy_literals(source, function_names)`: AST literal extraction currently embedded in self-eval.
- `extract_send_call_literals(source, function_names=None)`: AST scan for `actions.send_flyer_text(...)`, `send_flyer_text(...)`, `bridge_post(...)`, and `bridge_send_media(..., caption=...)` calls so hook-local copy surfaces cannot drift outside the static scan allowlist.

`tools/flyer-self-evaluation.py` will import this helper after inserting repo `src` on `sys.path`, matching existing tool patterns. Tests will import the same constants so self-eval and lifecycle-copy tests cannot drift apart.

## Self-Eval And Operator Brief Integration

- Preserve incident names: `customer_copy_internal_leak`, `customer_copy_static_internal_leak`, `duplicate_initial_ack`.
- Refactor self-eval customer-copy and duplicate-ack detectors to use the helper.
- Pass a project index into customer-copy and duplicate-ack detectors so `evidence_details.active_customer_risk` is deterministic. Matrix:
  - project status in `delivered`, `completed`, `closed_no_send`, `cancelled`, `archived`: false unless a newer outbound row for the same inbound indicates unresolved failure,
  - active project statuses or manual review `queued`/`in_progress`: true,
  - missing project id/state plus outbound customer leak: true for current run, audit-only for explicitly historical fixtures,
  - duplicate acks are keyed by inbound `message_id` plus project/customer/chat to avoid flagging intentional multi-message onboarding flows.
- Preserve PR #149 stale source-edit incident/reporting. No new timer or alert path.
- Extend operator brief only if a new incident lacks grouping; otherwise rely on existing category/risk grouping.

## Test Strategy

RED-first where practical:

- Add helper-policy tests proving self-eval and lifecycle-copy tests import the same banned-term constants.
- Add replay fixture tests that initially fail if the adapter cannot load scenarios, capture outbound copy, or fail closed on live side effects.
- Add regression assertions for F0065-style identity split: project locked facts, prompt/manifest text, and QA evidence must not treat campaign title/request text as profile business.
- Add self-eval tests that a fixture with a banned term follows the same helper path and keeps the existing incident name.
- Add static source-copy test coverage for `created flyer project`, `Project F`, `provider`, `reason_code`, and raw request echo terms.
- Add duplicate-ack tests using helper markers, preserving current behavior.
- Add preview/final QA replay that emits representative decision rows and proves `preview_approved_final_qa_failed` appears with `active_customer_risk=true`.
- Add SOURCE/NEW and authorization chained fixtures that verify pending state transitions and consumption rather than a single mocked pending row.

Verification command set:

```powershell
python -m pytest tests/test_flyer_incident_replay.py tests/test_flyer_self_evaluation.py tests/test_operator_brief.py tests/test_flyer_customer_lifecycle_copy.py tests/test_cf_router_flyer_routing.py tests/test_flyer_create_project.py tests/test_flyer_workflow.py tests/test_flyer_visual_qa.py tests/test_flyer_source_edit_sla_watchdog.py -q
python -m py_compile tools/flyer-self-evaluation.py tools/operator-brief.py src/agents/flyer/customer_copy_policy.py
python tools/flyer-self-evaluation.py --projects <fixture-projects> --decisions-log <fixture-log> --format json --scan-source-copy
python tools/flyer-self-evaluation.py --projects <fixture-projects> --decisions-log <fixture-log> --format markdown --scan-source-copy
git diff --check
```

## Non-Mutation Contract

The replay suite must not require network, SSH, VPS state, WhatsApp bridge, provider keys, or live filesystem paths. Any unmocked call to these surfaces is a test failure. The only file I/O allowed is reading committed fixtures/source files and writing pytest `tmp_path` state. Allowed side effects are registered per fixture; any unregistered subprocess/wrapper call, bridge send, `/opt/shift-agent` read/write, customer/payment/manual mutation, or provider call fails the test.

## Design Review Findings Folded

- Structural reviewer: final approval/package output was missing; design now captures `finalize_and_send_flyer` and package-send surfaces without live subprocess.
- Structural reviewer: module patching must target the loaded artificial cf-router package; design now requires reusing the existing loader and patching `hooks_mod.actions`.
- Structural reviewer: `/opt/shift-agent` reads can poison replay; design now rebinds all state paths and fails on live reads and writes.
- Structural reviewer: hook-local customer copy was outside static scan; design now adds AST scanning for send call literals in `hooks.py`.
- Fixture-validity reviewer: stale-active sample had no stale active project; fixture shape now includes realistic old active state and ordered audit assertions.
- Fixture-validity reviewer: F0065 cannot be mocked through expected facts; design now requires real temp-state `create-flyer-project` execution for that class.
- Fixture-validity reviewer: `identity` is not hook input; fixture uses `resolved_identity` as a mocked resolver output and asserts resolver calls.
- Both reviewers: active-vs-historical risk needed a deterministic matrix; self-eval integration now passes project state into copy/duplicate detectors.

## Final PR Review Findings Folded

- Live-behavior reviewer found replay fixtures were not all traversing top-level hook order; `tests/test_flyer_incident_replay.py` now runs every fixture through `pre_gateway_dispatch`.
- Live-behavior reviewer found silent `None` could pass; replay assertions now validate the expected route, required calls/audits, forbidden calls, manual reason-code, and final-QA audit detail.
- Runtime reviewer found `_real_create_project` did not bind `--asset-dir`; real temp-state creation now passes a temp asset directory.
- Runtime reviewer found replay copy capture used canned helper strings; replay now uses the live `send_flyer_*` helper bodies through fake `safe_io.bridge_post`.
- Runtime reviewer found static self-eval source-copy scanning used only literal terms; static scans now use the shared `scan_customer_text` policy so project-id and raw-request echo patterns are included.
- Live-behavior reviewer found duplicate-ack grouping treated outbound `message_id` as the replay identity; grouping now keys by inbound/source/trigger id when available, otherwise project/chat, so distinct outbound ack ids cannot hide duplicate initial acknowledgements.
- Live-behavior reviewer found static copy scanning missed dynamic f-string project id leaks such as `Project {project_id}`; AST literal extraction now preserves placeholder names and the shared scanner flags dynamic project-id placeholders.

## Deferred Items

- Real-model dispatcher replay remains in the existing dispatcher replay lane, not this PR.
- Source-edit SLA alerting is owned by PR #149 and deployed tag `deploy-20260521-043544-8986597e.tgz`.
- Dashboard/UI work is deferred unless a read-only grouping gap blocks operator brief acceptance.
- Hermes self-evolution remains offline/staging only; no production self-modification path is introduced.
