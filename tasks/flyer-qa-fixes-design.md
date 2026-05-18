# Flyer Studio QA fixes — design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. Two existing Literal values added to `CfRouterIntercepted.reason`.

Plan reference: `tasks/flyer-qa-fixes-plan.md`.

## Hermes-first capability checklist

| # | Implementation step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | Edit `safe_io.atomic_write_text` to gate parent-dir fsync on `os.name == "posix"`. | `[net-new]` ~3 LOC. |
| 2 | Append two literals to `CfRouterIntercepted.reason`. | `[net-new]` ~2 LOC. |
| 3 | Add `_find_consumed_guest_order` + call from `consume_guest_order`. | `[net-new]` ~25 LOC. |
| 4 | Sort + slice in `flyer.customers` route. | `[net-new]` ~6 LOC. |
| 5 | Whitelist + label change in `dispatcher-accuracy-report`. | `[net-new]` ~25 LOC. |
| 6 | Two string literal edits in `render.py`. | `[net-new]` ~2 LOC. |
| 7 | Load/save customer + guest_order state through `safe_io.atomic_write_text`. | `[Hermes]` — substrate. |
| 8 | `audit_intercepted` wraps `safe_io.ndjson_append` to write `CfRouterIntercepted` rows. | `[Hermes]` — substrate. |
| 9 | `pair_inbounds` reads NDJSON via existing `load_entries`. | `[Hermes]` — substrate. |
| 10 | All test fixtures use existing `tmp_path` + `monkeypatch` pytest pattern. | `[Hermes]` — substrate. |

Awesome-Hermes-Agent ecosystem check: same conclusion as plan — no installable skill applies.

Net-new: 6 net-new steps; ~63 LOC impl + ~135 LOC tests.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/guest_order.py` (`_find_reserved_guest_order` at lines 234-261 — uses `E164Phone.from_any`, fallback without `chat_id`, `max(matches, key=updated_at)`); mirroring this signature in `_find_consumed_guest_order`.
- ✅ Read `src/platform/schemas.py` (`CfRouterIntercepted.reason` Literal at 3556-3586; `FlyerGuestOrder.used_project_ids` at line 888; `FlyerCustomerProfile.updated_at` at line 920).
- ✅ Read `web/backend/app/routers/flyer.py` (`/customers` at 357-385; `/projects` slice pattern at line 426).
- ✅ Read `src/platform/scripts/dispatcher-accuracy-report` (`pair_inbounds` `cf_proposal_intercepts` whitelist at line 131-135; `format_text_report` label at line 246; `format_json_report` cf-router count at line 274/292).
- ✅ Read `src/agents/flyer/render.py` lines 1548 + 1616 (footer literals).
- ✅ Read `src/platform/safe_io.py` lines 215-239 (atomic_write_text).
- ✅ Read `web/backend/tests/test_flyer_admin.py` (fixture pattern + monkeypatch of `flyer.get_settings()`; direct call to route function `flyer.summary()` / `flyer.extend_trial(...)`).

## Design per bug

### BUG-005 — Windows-friendly directory fsync

`src/platform/safe_io.py`, replace lines 234-239:

```python
# fsync parent directory so the rename entry is durable (POSIX only;
# Windows does not allow open(dir, O_RDONLY) and the rename is
# already durable enough at the FS-layer for local dev/test).
if os.name == "posix":
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
```

Test in `tests/test_safe_io.py` (file already exists — append to it; do not overwrite):

```python
import os
from pathlib import Path
import pytest
from safe_io import atomic_write_text


def test_atomic_write_text_skips_dir_fsync_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_fsyncs_parent_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    dir_fds: set[int] = set()
    fsynced_fds: list[int] = []
    real_open = os.open
    real_fsync = os.fsync

    def tracking_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        try:
            if Path(path).is_dir():
                dir_fds.add(fd)
        except OSError:
            pass
        return fd

    def tracking_fsync(fd):
        fsynced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fsync", tracking_fsync)
    target = tmp_path / "y.txt"
    atomic_write_text(target, "world")
    assert target.read_text(encoding="utf-8") == "world"
    # Production durability invariant: at least one of the dir FDs that we
    # opened must have been fsynced. Without this, the test would pass even
    # if a future edit silently removed the os.fsync(dfd) call.
    assert dir_fds, "atomic_write_text did not open the parent directory at all"
    assert any(fd in fsynced_fds for fd in dir_fds), "parent-directory FD was opened but not fsynced"
```

### BUG-003a — Add missing reasons to schema Literal

`src/platform/schemas.py` `CfRouterIntercepted.reason` Literal — insert immediately after `"flyer_onboarding_failed"` (line 3570) so the new entries group with the related Flyer-routing reasons rather than with `_guest_order_*`:

```python
        "flyer_onboarding",
        "flyer_onboarding_failed",
        "flyer_starter_brief",            # NEW (PR #102, hooks.py:188)
        "flyer_customer_not_active",      # NEW (PR #105, hooks.py:201)
        "flyer_quota_blocked",
```

Test in `tests/test_schemas.py` (or `tests/test_cf_router_flyer_routing.py` — pick file where the change reads most naturally):

```python
import pytest
from datetime import datetime, timezone
from schemas import CfRouterIntercepted


@pytest.mark.parametrize("reason", ["flyer_starter_brief", "flyer_customer_not_active"])
def test_cf_router_intercepted_accepts_new_flyer_reasons(reason):
    entry = CfRouterIntercepted(
        type="cf_router_intercepted",
        ts=datetime.now(timezone.utc),
        reason=reason,
        chat_id="2125550101@s.whatsapp.net",
    )
    assert entry.reason == reason
```

### BUG-001 — `consume_guest_order` idempotency on replay

`src/agents/flyer/guest_order.py`, add helper after `_find_reserved_guest_order`:

```python
def _find_consumed_guest_order(
    store: FlyerGuestOrderStore,
    *,
    sender_phone: str,
    chat_id: str,
    project_id: str,
) -> Optional[FlyerGuestOrder]:
    """Return an order that already consumed `project_id` for this sender.

    Used to make `consume_guest_order` idempotent on replay: after a
    successful first consume the order's status becomes 'used' or 'paid'
    and `reserved_project_id` is cleared, so `_find_reserved_guest_order`
    can no longer locate it. This helper instead matches on
    `project_id in order.used_project_ids` so replays return success.

    Excludes orders where `reserved_project_id == project_id AND
    status == "reserved"` — those represent in-flight first consumes,
    not replays.
    """
    try:
        canonical = E164Phone.from_any(sender_phone, country_code="US")
    except ValueError:
        return None
    def is_replay(order: FlyerGuestOrder) -> bool:
        if project_id not in order.used_project_ids:
            return False
        if order.status == "reserved" and order.reserved_project_id == project_id:
            return False
        return order.sender_phone == canonical
    matches = [
        order for order in store.orders
        if is_replay(order) and (not chat_id or order.chat_id == chat_id)
    ]
    if not matches and chat_id:
        matches = [order for order in store.orders if is_replay(order)]
    if not matches:
        return None
    return max(matches, key=lambda order: order.updated_at)
```

Modify `consume_guest_order`:

```python
def consume_guest_order(
    *,
    state_path: Path,
    sender_phone: str,
    chat_id: str,
    project_id: str,
    now: Optional[datetime] = None,
) -> GuestOrderResult:
    now = now or datetime.now(timezone.utc)
    store = load_guest_order_store(state_path)
    # Idempotent replay: order already consumed this project_id.
    replayed = _find_consumed_guest_order(
        store, sender_phone=sender_phone, chat_id=chat_id, project_id=project_id,
    )
    if replayed is not None:
        return GuestOrderResult(
            True, True, "",
            replayed.order_id, replayed.status, replayed.payment_checkout_url,
        )
    order = _find_reserved_guest_order(
        store, sender_phone=sender_phone, chat_id=chat_id, project_id=project_id,
    )
    if order is None:
        return GuestOrderResult(False, True, "", detail="reserved_guest_order_not_found")
    # ... (existing append + status + write logic stays unchanged)
```

Tests in `tests/test_flyer_guest_order.py`:

```python
def test_consume_guest_order_idempotent_on_replay(tmp_path):
    state_path = tmp_path / "guest_orders.json"
    # ... seed an order, reserve it for P1, then call consume
    first = consume_guest_order(state_path=state_path, sender_phone=PHONE, chat_id=CHAT, project_id="P1")
    second = consume_guest_order(state_path=state_path, sender_phone=PHONE, chat_id=CHAT, project_id="P1")
    assert first.ok is True
    assert second.ok is True
    assert second.detail == ""
    assert second.reply_text == ""
    assert second.order_id == first.order_id
    assert second.status == first.status
    store = load_guest_order_store(state_path)
    order = store.find_order_by_id(first.order_id)
    assert order is not None
    assert order.used_project_ids == ["P1"]  # not double-appended


def test_consume_guest_order_multi_flyer_replay(tmp_path):
    # flyer_count_purchased=2; consume P1, consume P2, replay P1, replay P2
    # all four return ok=True; used_project_ids stays at ["P1", "P2"]
    ...


def test_consume_guest_order_cross_chat_replay(tmp_path):
    # Consume P1 in chat A; replay in chat B → idempotent success via fallback
    ...
```

### BUG-002 — `/flyer/customers` cap + sort

`web/backend/app/routers/flyer.py`, modify the `/customers` handler:

```python
@router.get("/customers")
async def customers(query: str = "", segment: str = "", _=Depends(require_auth)):
    store = load_customer_store()
    projects = load_project_store()
    project_counts: dict[str, int] = {}
    by_phone = {phone: c.customer_id for c in store.customers for phone in c.routable_phones()}
    for project in projects.projects:
        cid = by_phone.get(str(project.customer_phone), "")
        if cid:
            project_counts[cid] = project_counts.get(cid, 0) + 1
    q = query.strip().lower()
    rows = []
    for customer in store.customers:
        row = _customer_row(customer, project_counts.get(customer.customer_id, 0))
        haystack = " ".join(
            [
                row["customer_id"], row["business_name"], row["public_phone"],
                row["business_whatsapp_number"], " ".join(row["authorized_request_numbers"]),
            ]
        ).lower()
        if q and q not in haystack:
            continue
        if segment and row["category"] != segment:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return {"customers": rows[:300]}
```

Test in `web/backend/tests/test_flyer_admin.py`:

```python
from datetime import datetime, timedelta, timezone

def test_flyer_customers_caps_at_300_sorted_by_updated_at(tmp_path, monkeypatch):
    import asyncio
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    customers = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(305):
        c = _customer(f"CUST{i:04d}", phone=f"+1555010{i:04d}")
        c["created_at"] = base.isoformat()
        c["updated_at"] = (base + timedelta(minutes=i)).isoformat()
        customers.append(c)
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {"schema_version": 1, "next_customer_sequence": 306, "customers": customers},
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_project_sequence": 1, "projects": []},
    )
    result = asyncio.run(flyer.customers(query="", segment="", _=None))
    rows = result["customers"]
    assert len(rows) == 300
    assert rows[0]["customer_id"] == "CUST0304"  # newest first
    assert rows[-1]["customer_id"] == "CUST0005"  # oldest of the kept 300
```

### BUG-003b — Pair Flyer reasons in `dispatcher-accuracy-report`

`src/platform/scripts/dispatcher-accuracy-report`, add module-level constant:

```python
# All cf_router_intercepted reasons that represent a successful dispatcher-
# equivalent route (LLM bypassed). Failures and the bare "error" reason
# are NOT dispatcher-equivalent — they indicate the LLM still ran.
CF_ROUTER_DISPATCHER_EQUIV_REASONS: frozenset[str] = frozenset({
    # Catering F7 paths (pre-existing).
    "f7_proposal_request",
    "f7_proposal_selection",
    # Flyer paths (added 2026-05-18). Order matches schemas.py for diffability.
    "flyer_primary_project_created",
    "flyer_intake_started",
    "flyer_intake",
    "flyer_onboarding",
    "flyer_quota_blocked",
    "flyer_brand_asset_saved",
    "flyer_reference_scope_blocked",
    "flyer_reference_exact_edit_queued",
    "flyer_location_blocked",
    "flyer_account_command",
    "flyer_guest_order_started",
    "flyer_starter_brief",
    "flyer_customer_not_active",
})
```

Modify `pair_inbounds` line 131-135:

```python
    cf_proposal_intercepts = [
        e for e in entries
        if e.get("type") == "cf_router_intercepted"
        and e.get("reason") in CF_ROUTER_DISPATCHER_EQUIV_REASONS
    ]
```

Maintain TWO distinct counters in both `format_text_report` and `format_json_report`. The pairing loop checks each cf-router intercept's `reason` to bucket it correctly. This preserves the legacy key's F7-only semantics — initial draft conflated them (identical values) which silently changed what existing dashboards saw.

In both formatter functions:

```python
    # Two counters with DISTINCT semantics so the legacy key keeps its
    # original meaning. cf_proposal_selection_count counts only
    # f7_proposal_request / f7_proposal_selection (the pre-2026-05-18
    # whitelist). cf_router_intercepted_count is the new total across
    # all dispatcher-equivalent cf-router intercepts (catering + flyer).
    cf_proposal_selection_count = 0
    cf_router_intercepted_count = 0
    for _inb, match, kind in paired:
        # ... other counters unchanged ...
        elif kind == "cf_router_intercepted":
            cf_router_intercepted_count += 1
            if match.get("reason") in {"f7_proposal_request", "f7_proposal_selection"}:
                cf_proposal_selection_count += 1
```

In `format_text_report` replace the cf-router summary line:

```python
    if cf_router_intercepted_count:
        lines.append(f"CF router intercepts: {cf_router_intercepted_count}")
```

In `format_json_report` emit both keys with their distinct values:

```python
    return json.dumps(
        {
            # ... earlier keys unchanged ...
            "cf_router_proposal_selection_count": cf_proposal_selection_count,  # legacy: F7 only
            "cf_router_intercepted_count": cf_router_intercepted_count,         # new: all whitelisted
            # ... rest unchanged ...
        },
        indent=2,
    )
```

Test in `tests/test_dispatcher_accuracy_report.py` — the file already imports the script as `mod` via `importlib.machinery.SourceFileLoader` (see existing lines 19-22). All `pair_inbounds` calls must use `mod.pair_inbounds(...)`:

```python
def test_pair_inbounds_pairs_flyer_starter_brief():
    entries = [
        {"type": "raw_inbound", "ts": "2026-05-18T10:00:00+00:00", "message_id": "M1",
         "sender_phone": "+15551234567"},
        {"type": "cf_router_intercepted", "ts": "2026-05-18T10:00:02+00:00",
         "reason": "flyer_starter_brief", "chat_id": "15551234567@s.whatsapp.net"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 1
    assert len(unpaired) == 0
    assert paired[0][2] == "cf_router_intercepted"


def test_pair_inbounds_does_not_pair_flyer_failure_reasons():
    entries = [
        {"type": "raw_inbound", "ts": "2026-05-18T10:00:00+00:00", "message_id": "M1",
         "sender_phone": "+15551234567"},
        {"type": "cf_router_intercepted", "ts": "2026-05-18T10:00:02+00:00",
         "reason": "flyer_primary_failed", "chat_id": "15551234567@s.whatsapp.net"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 0
    assert len(unpaired) == 1


def test_json_report_legacy_key_keeps_f7_proposal_semantics():
    # Seed 2 F7 reasons + 2 flyer reasons. Assert:
    #   cf_router_proposal_selection_count == 2  (legacy, F7-only)
    #   cf_router_intercepted_count == 4         (new, all whitelisted)
    # via mod.format_json_report.
    ...
```

**Counter-semantics decision (revised 2026-05-18 post-PR review):** Keep two distinct local counters, NOT one. Initial design conflated them ("emit both keys with identical values") which the PR-106 reviewer correctly flagged as a back-compat break — the legacy key's value silently shifted from "F7 proposals only" to "all dispatcher-equivalent intercepts". The fix: `cf_proposal_selection_count` stays F7-only; the broader `cf_router_intercepted_count` is the new additive key. The text-report label change `"CF router intercepts: N"` is fed from the new total counter (operators see the broader signal); legacy JSON consumers continue to see the unchanged F7-only count.

### BUG-004 — Remove `Hermes` from customer-facing footer

`src/agents/flyer/render.py`, line 1548:

```python
    footer = "Send APPROVE to finalize - Flyer Studio"
```

`src/agents/flyer/render.py`, line 1616 (inside the `SUBPROCESS_RENDERER` triple-quoted string):

```python
footer="Send APPROVE to finalize - Flyer Studio"; box=draw.textbbox((0,0),footer,font=sm)
```

Test addition in `tests/test_flyer_renderer.py` (or add the file if not present):

```python
from pathlib import Path

RENDER_PY = Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer" / "render.py"


def test_render_footer_strings_have_no_hermes_brand():
    src = RENDER_PY.read_text(encoding="utf-8")
    # In-process Pillow path
    assert '"Send APPROVE to finalize - Flyer Studio"' in src
    # Subprocess renderer template
    assert '"Send APPROVE to finalize - Flyer Studio"' in src
    # No customer-facing footer should still carry the legacy "Hermes Flyer Studio"
    customer_facing_legacy = "Send APPROVE to finalize - Hermes Flyer Studio"
    assert customer_facing_legacy not in src
```

(The X-Title HTTP header `"Hermes Flyer Studio"` at render.py:1180 is left untouched — internal-only API metadata for OpenRouter, not customer-visible. The module docstring at lines 1-3 is also left untouched.)

## Build sequence (matches plan)

1. BUG-005 (commit 1) — unblocks Windows tests.
2. BUG-003a (commit 2) — schema prerequisite.
3. BUG-001 (commit 3).
4. BUG-002 (commit 4).
5. BUG-003b (commit 5).
6. BUG-004 (commit 6).

After each commit: run the impacted tests + `git diff --check`.

## Test fixture conventions

- All tests use `tmp_path` + `monkeypatch` (no global state).
- File seeds use raw JSON via `_write_json` helper (matches `test_flyer_admin.py` style).
- Direct route function invocation via `asyncio.run(flyer.customers(...))` — no `TestClient` needed.
- Datetime comparisons use `datetime.timezone.utc` + ISO-8601 strings (matches `_customer_row` output).
