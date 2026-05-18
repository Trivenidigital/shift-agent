# Flyer Studio QA fixes — plan

**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. All fixes are bug fixes against deployed code paths; no new schema variant types, no new scripts, no new state files. Two existing schema Literal values are added (BUG-003a prerequisite).

## Hermes-first capability checklist

| # | Step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | Operator triggers payment-completion / project-delivery retry that calls `consume_guest_order(project_id="P1")` a second time. | `[Hermes]` — Per-VPS state + retry trigger via existing payment-completion flow. |
| 2 | Function looks up the guest order in `state/flyer/guest_orders.json`. | `[Hermes]` — `load_guest_order_store` uses `safe_io`. |
| 3 | Function decides whether the order has already been consumed for this `project_id` and returns idempotent success. | `[net-new]` — Idempotency branch is unreachable on replay; Flyer-specific business logic. ~25 LOC. |
| 4 | Operator dashboard route `/flyer/customers?segment=free_trial` is requested. | `[Hermes]` — Existing FastAPI route + `require_auth`. |
| 5 | Endpoint sorts customers newest-first by `updated_at` and caps results at 300 rows. | `[net-new]` — Cap + sort, reuses the `/projects` pattern. ~6 LOC. |
| 6 | Inbound WhatsApp message triggers a Flyer cf-router intercept; `audit_intercepted(reason="flyer_starter_brief")` writes a `cf_router_intercepted` audit row via `safe_io.ndjson_append`. | `[Hermes]` — Audit chain + discriminated-union entry deployed. |
| 7 | `CfRouterIntercepted.reason` Literal accepts the runtime-emitted Flyer reasons (`flyer_starter_brief`, `flyer_customer_not_active`). | `[net-new]` — Schema bug: PR #102/#105 emitted new reasons without extending the Literal; Pydantic ValidationError is silently caught by `audit_intercepted` and the row is never written. ~2 LOC. |
| 8 | `dispatcher-accuracy-report` pairs the raw_inbound with that audit row so it does not show up as "Kimi skipped dispatcher". | `[net-new]` — Pairing whitelist excludes Flyer reasons; extending the set is a tooling fix. ~25 LOC. |
| 9 | Flyer renderer (`render.py`) draws preview/final assets via Pillow (in-process) or `/usr/bin/python3` subprocess fallback. | `[Hermes]` — Image rendering pipeline + subprocess pattern deployed. |
| 10 | Customer-facing footer reads `"Send APPROVE to finalize - Flyer Studio"` (no internal "Hermes" branding). The internal `X-Title` HTTP header at render.py:1180 stays "Hermes Flyer Studio" — OpenRouter API metadata, not customer-visible. | `[net-new]` — Two literal string scrubs in footers; X-Title intentionally untouched. ~2 LOC. |
| 11 | Any flyer state mutation goes through `safe_io.atomic_write_text` on the local Windows dev box. | `[Hermes]` — Atomic writer is deployed substrate. |
| 12 | `atomic_write_text` POSIX directory-fsync runs only on POSIX; Windows skips the fsync silently while still verifying the data-write fsync of the file descriptor. | `[net-new]` — `if os.name == "posix":` gate on the parent-directory fsync. ~3 LOC. |

Awesome-Hermes-Agent ecosystem check: no installable skill (`productivity/google-workspace`, `productivity/maps`, `productivity/airtable`, `productivity/ocr-and-documents`, `productivity/notion`, `mcp/native-mcp`) overlaps with these fixes. Pure in-tree bug fixes.

Net-new effort: 6 net-new steps; **~63 LOC implementation + ~135 LOC tests** total.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/guest_order.py` (`consume_guest_order` + `_find_reserved_guest_order` at lines 175-261) before drafting BUG-001 idempotency fix.
- ✅ Read `src/platform/schemas.py` (`FlyerGuestOrder` + `FlyerGuestOrderStore` at lines 870-1245; `FlyerCustomerProfile.updated_at` at line 920; `CfRouterIntercepted.reason` Literal at 3556-3586) before drafting BUG-001, BUG-002, BUG-003.
- ✅ Read `web/backend/app/routers/flyer.py` (`/customers` at 357-385; existing `/projects` and `/guest-orders` cap pattern at 414-433; `_customer_row` emits `updated_at`) before drafting BUG-002.
- ✅ Read `src/platform/scripts/dispatcher-accuracy-report` (`pair_inbounds` whitelist at lines 119-180 and report formatters at 187-309) before drafting BUG-003b.
- ✅ Read `src/plugins/cf-router/actions.py` (`audit_intercepted` at 487-512 swallows ValidationError; `find_dispatcher_routed_for` at 2156-2214 is the catering F7 watchdog and is unaffected).
- ✅ Read `src/plugins/cf-router/hooks.py` and confirmed runtime-emitted reasons via grep: of 18 distinct `flyer_*` reasons emitted, only `flyer_starter_brief` and `flyer_customer_not_active` are absent from the schema Literal. Four schema entries (`flyer_reference_scope_use_reference`, `flyer_reference_scope_authorization_requested`, `flyer_reference_scope_authorization_followup`, `flyer_reference_scope_authorized_generated`) are listed in the Literal but never emitted — leave alone (orphan but harmless).
- ✅ Read `src/agents/flyer/render.py` (4 "Hermes" occurrences: line 1 + 3 module docstring [internal — keep]; line 1180 X-Title HTTP header [internal API metadata — keep]; lines 1548 + 1616 customer-facing footer [scrub]).
- ✅ Read `src/platform/safe_io.py` (`atomic_write_text` directory-fsync at lines 215-239) and confirmed: file-descriptor fsync at line 230 stays for both platforms; only the parent-directory fsync at 235-239 is gated on `os.name == "posix"`.
- ✅ Read `web/backend/tests/test_flyer_admin.py` (verified fixture pattern: `tmp_path` + monkeypatch + direct route function calls; no `TestClient` required). No existing test for `/flyer/customers` — adding `test_flyer_customers_caps_at_300_sorted_by_updated_at`.

## Scope (5 bugs + 1 schema prerequisite)

### BUG-FLYER-QA-001 (P0) — `consume_guest_order` idempotency on replay

**Cause:** `_find_reserved_guest_order` requires `status == "reserved" and reserved_project_id == project_id`. After the first successful consume, `status` flips to `used` (or `paid` for multi-flyer orders) and `reserved_project_id` is cleared. Second call returns `reserved_guest_order_not_found`. The existing idempotency branch at `guest_order.py:188-190` is unreachable on replay.

**Fix:**
- Before the `_find_reserved_guest_order` lookup, check for an order that has already consumed `project_id`. Add helper `_find_consumed_guest_order(store, sender_phone, chat_id, project_id)`:
  - Normalize phone via `E164Phone.from_any(sender_phone, country_code="US")` (mirrors `_find_reserved_guest_order`).
  - Match orders where `project_id in order.used_project_ids` AND `order.sender_phone == canonical` AND `(not chat_id or order.chat_id == chat_id)`.
  - If no `chat_id`-scoped match and `chat_id` is non-empty, fall back to matching without `chat_id` (mirrors lines 252-258 of the reserved finder, for cross-chat replay scenarios).
  - Exclude orders where `reserved_project_id == project_id AND status == "reserved"` — those represent in-flight first consumes, not replays.
  - Return the most-recently-updated match (mirrors `max(matches, key=lambda o: o.updated_at)`).
- If the helper returns an order, return `GuestOrderResult(True, True, "", order.order_id, order.status, order.payment_checkout_url)` — same shape as the existing in-function idempotency branch.

**Files touched:** `src/agents/flyer/guest_order.py`.

**Tests (`tests/test_flyer_guest_order.py`):**
- Two consecutive `consume_guest_order(project_id="P1")` calls; second returns `ok=True, detail=""`, `used_project_ids == ["P1"]` (no double-append), `reply_text == ""`.
- Multi-flyer order (`flyer_count_purchased=2`): consume P1, then consume P2 (still works, `status="paid"` after first), then replay P1 (idempotent, returns `status="paid"`), then replay P2 (idempotent, returns final status).
- Cross-chat replay: consume P1 in chat A, replay with chat B → helper falls back to chat-less match and returns idempotent success.

### BUG-FLYER-QA-002 (P1) — `/flyer/customers` cap + sort

**Cause:** Endpoint returns all matching rows. `/projects` and `/guest-orders` already cap at 300 with `updated_at` desc sort.

**Fix:**
- Sort rows by `row.get("updated_at", "")` desc (lexicographic on ISO-8601 strings works correctly).
- Slice `[:300]`.
- Apply both AFTER existing `query` + `segment` filters so the cap reflects what the operator searched for.
- Response shape stays `{"customers": rows}` — front-end consumer (`web/frontend/src/sections/FlyerAdmin.tsx`) unchanged.

**Files touched:** `web/backend/app/routers/flyer.py`.

**Tests (`web/backend/tests/test_flyer_admin.py`):**
- Use existing `tmp_path` + monkeypatch + direct `flyer.customers(...)` call pattern.
- Seed >300 customers with strictly-increasing `updated_at`. Assert `flyer.customers(query="", segment="")` returns exactly 300 rows; rows are sorted by `updated_at` desc; the newest customer is first.
- Verify filters still apply: with `segment="free_trial"`, only free-trial customers are returned, and the cap applies after filtering.

**Frontend pagination (revised 2026-05-18 post-PR review):** PR review caught that a backend cap alone leaves rows beyond 300 reachable via API but invisible in the UI — the operator dashboard table silently dropped the tail. Wire `web/frontend/src/sections/FlyerAdmin.tsx` to:
- Send `offset=<state>&limit=300` on `/flyer/customers` requests.
- Read `total` + `truncated` from the response.
- Render "Showing X–Y of N" + Previous/Next buttons under the customers table.
- Reset `offset` to 0 whenever `query` or `segment` changes (otherwise the offset overshoots the new result set).
The backend response shape becomes `{customers, total, offset, limit, truncated}`. Existing front-end consumer is back-compat because the `customers` field is preserved.

### BUG-FLYER-QA-003a (P1, prerequisite) — Add missing reasons to `CfRouterIntercepted.reason` Literal

**Cause:** Two runtime reasons emitted by `hooks.py` are not declared in the schema Literal:
- `flyer_starter_brief` (hooks.py:188)
- `flyer_customer_not_active` (hooks.py:201)

`audit_intercepted` wraps the construction in `try/except` and silently swallows the Pydantic `ValidationError`, so audit rows for these reasons are **never written** to `decisions.log`. The downstream pairing fix in BUG-003b would have no rows to pair without this.

**Fix:** Append the two literal values to the `CfRouterIntercepted.reason` Literal in `src/platform/schemas.py` (lines 3556-3586).

**Files touched:** `src/platform/schemas.py`.

**Tests (`tests/test_schemas.py` or `tests/test_cf_router_flyer_routing.py`):**
- Construct a `CfRouterIntercepted` with each of the two new reasons; assert no `ValidationError`.

### BUG-FLYER-QA-003b (P1) — Pair cf-router Flyer intercepts in `dispatcher-accuracy-report`

**Cause:** `pair_inbounds` filters `cf_router_intercepted` to `reason in {"f7_proposal_request","f7_proposal_selection"}`. Every Flyer reason is excluded.

**Fix:**
- Define module-level constant `CF_ROUTER_DISPATCHER_EQUIV_REASONS` listing all non-failure intercept reasons. Maintain as explicit enumeration (not prefix-match) so the report stays auditable and explicit. Include: existing catering F7 reasons + all `flyer_*` success reasons (excluding `_failed` and `error`).
- Maintain TWO distinct counters with separate semantics (revised 2026-05-18 after PR review — initial draft conflated them, which silently changed the legacy key's value for existing dashboard consumers):
  - `cf_router_proposal_selection_count` (legacy key, F7-only) — counts only `f7_proposal_request` + `f7_proposal_selection`. Preserves the pre-2026-05-18 meaning so existing dashboards reading this key see unchanged values.
  - `cf_router_intercepted_count` (new key, additive) — counts ALL whitelisted cf-router intercepts (catering F7 + flyer). Reports the broader total without overloading the legacy key.
- Update `format_text_report` label from `"CF router proposal selections: N"` to `"CF router intercepts: N"`, driven by the new total counter (text report renders the broader signal; legacy callers of the JSON report stay back-compat).
- Both `format_text_report` and `format_json_report` maintain both counters internally; the JSON output emits both keys with their distinct values.

**Files touched:** `src/platform/scripts/dispatcher-accuracy-report`.

**Tests (`tests/test_dispatcher_accuracy_report.py`):**
- Seed a `raw_inbound` + matching `cf_router_intercepted{reason="flyer_starter_brief"}` within the 10-s pairing window; assert paired_count == 1, unpaired_count == 0, JSON has `cf_router_intercepted_count == 1` AND legacy `cf_router_proposal_selection_count == 0` (the legacy key is F7-only, so a flyer-reason intercept must not increment it).
- Negative case: `cf_router_intercepted{reason="flyer_primary_failed"}` does NOT pair (failures are not "dispatcher routed" events).

### BUG-FLYER-QA-004 (P1) — Remove `Hermes` from customer-facing footer

**Cause:** Two literals — `_draw_flyer_pil` at line 1548 and `SUBPROCESS_RENDERER` template at line 1616 — emit `"Send APPROVE to finalize - Hermes Flyer Studio"`. Customer-visible.

**Other Hermes occurrences left alone (intentional):**
- `render.py:1` and `render.py:3` — module docstring, internal-only.
- `render.py:1180` — `X-Title: "Hermes Flyer Studio"` HTTP header to OpenRouter, internal API metadata, not customer-facing.

**Fix:** Replace both customer-facing footer literals with `"Send APPROVE to finalize - Flyer Studio"`. No code-flow change.

**Files touched:** `src/agents/flyer/render.py`.

**Tests:**
- Defensive source-string assertion in `tests/test_flyer_renderer.py`: load `render.py` text and assert the `_draw_flyer_pil` footer literal and the `SUBPROCESS_RENDERER` constant both contain `"Flyer Studio"` and do NOT contain `"Hermes Flyer Studio"`.
- If an existing render test produces a Pillow image, also assert the rendered string `"Hermes Flyer Studio"` is absent from any text layer (best-effort — covered by the source assertion if visual is not testable in unit context).

### BUG-FLYER-QA-005 (P2) — Windows-friendly directory fsync

**Cause:** `safe_io.atomic_write_text:235-239` always calls `os.open(parent_dir, os.O_RDONLY) + os.fsync(dfd)`. Windows raises `PermissionError [Errno 13]`.

**Fix:** Guard the parent-directory fsync block with `if os.name == "posix":`. Production POSIX VPS unaffected; Windows tests pass without external shim.

**Files touched:** `src/platform/safe_io.py`.

**Tests (`tests/test_safe_io.py`):**
- Windows-style: `monkeypatch.setattr(os, "name", "nt")` then call `atomic_write_text(tmp_path / "file.txt", "data")`; assert no `PermissionError` and `file.txt` contains `"data"`.
- POSIX-branch regression guard: `monkeypatch.setattr(os, "name", "posix")`, monkeypatch `os.open` and `os.fsync` to track calls; verify the parent-dir fsync IS called on POSIX. This catches accidental future removal of the production durability path.

## Out of scope (deliberately)

- New audit row TYPE for cf-router (would require a `DispatcherRouted` extension). Rejected — workbook contract is satisfied by extending pairing whitelist.
- Pagination cursors / `total` metadata on `/customers`. Rejected — workbook contract is "max 300 + sort"; `total` is a future enhancement.
- Scrubbing `"Hermes"` from internal log/spec/runbook strings and the `X-Title` HTTP header. Rejected — Hermes IS the internal platform; only customer-facing artifacts need scrubbing.
- General-purpose `safe_io` portability layer. Rejected — one-line POSIX gate is the minimal correct fix.
- Renaming `cf_router_proposal_selection_count` JSON key in the report. Rejected — additive `cf_router_intercepted_count` preserves back-compat.
- Removing the 4 orphan reasons (`flyer_reference_scope_use_reference`, etc.) from the schema Literal. Rejected — harmless dead values; cleanup is a separate housekeeping PR.

## Build sequence

Branch `codex/flyer-qa-bug-fixes` from `main`.

1. Commit 1 (BUG-005, ~3 LOC + ~25 LOC tests) — unblocks local Windows testing.
2. Commit 2 (BUG-003a, ~2 LOC + ~10 LOC tests) — schema prerequisite for BUG-003b.
3. Commit 3 (BUG-001, ~25 LOC + ~40 LOC tests).
4. Commit 4 (BUG-002, ~6 LOC + ~35 LOC tests).
5. Commit 5 (BUG-003b, ~25 LOC + ~30 LOC tests).
6. Commit 6 (BUG-004, ~2 LOC + ~15 LOC tests).
7. Run full focused suite: `pytest tests/test_flyer_*.py tests/test_dispatcher_accuracy_report.py tests/test_safe_io.py tests/test_schemas.py web/backend/tests/test_flyer_admin.py -q`. Expect green delta over the 186-passed baseline.
8. `git diff --check`, `py_compile`.
9. Push, open PR, dispatch 3 reviewers.

## Verification matrix

| Bug | Pass criterion |
|---|---|
| 001 | Replay test (single-flyer): `consume_guest_order(project_id="P1")` twice → second call returns `ok=True, detail="", reply_text=""`; `used_project_ids == ["P1"]`. Multi-flyer + cross-chat variants pass. |
| 002 | Seeded 305 customers → `/flyer/customers` returns 300 rows, sorted by `updated_at` desc; query/segment filters apply before the slice. |
| 003a | `CfRouterIntercepted(type=..., reason="flyer_starter_brief", chat_id="x")` constructs without `ValidationError`; same for `flyer_customer_not_active`. |
| 003b | Seeded `raw_inbound` + `cf_router_intercepted{reason="flyer_starter_brief"}` within 10s → report `paired_count == 1, unpaired_count == 0`, JSON `cf_router_intercepted_count == 1 AND cf_router_proposal_selection_count == 0` (legacy key is F7-only). With both F7 and flyer reasons seeded, the legacy count tracks only F7 entries while `cf_router_intercepted_count` is the broader total. Negative case: `flyer_primary_failed` does NOT pair. |
| 004 | Rendered PIL footer text contains `"Flyer Studio"` and not `"Hermes"`. `SUBPROCESS_RENDERER` constant string contains `"Flyer Studio"` and not `"Hermes Flyer Studio"`. |
| 005 | Windows-style (`os.name=="nt"`) monkeypatch test of `atomic_write_text` does not raise `PermissionError`; file content correct. POSIX-style monkeypatch test confirms parent-dir fsync IS called. |

## Risk

- BUG-001 — the new helper must NOT match orders where `reserved_project_id == project_id AND status == "reserved"` (an in-flight first consume). Test the strict ordering: helper runs first, returns None when no consumed match → falls through to existing reserved-lookup path → first consume completes normally.
- BUG-003a — only two reasons need adding. The four orphan reasons in the Literal (never-emitted reference-scope variants) are harmless and left for a future housekeeping pass.
- BUG-003b — legacy JSON key `cf_router_proposal_selection_count` keeps its original F7-only semantics; the broader catering+flyer total lives on the new additive key `cf_router_intercepted_count`. The text-report label change is a soft contract; the report is observability, not a stable consumer interface.
- BUG-004 — X-Title HTTP header at render.py:1180 is intentionally left as `"Hermes Flyer Studio"`. This is internal API metadata for OpenRouter cost-attribution; not customer-facing.
- BUG-005 — POSIX-branch regression guard test catches accidental future removal of `os.fsync(dfd)` on the production VPS path.
