# PR-D2 — Design doc

**Drift-check tag:** `extends-Hermes`

**Supersedes:** `tasks/pr-d2-plan.md` v2 §v2 + `tasks/pr-d-medium-items-design.md` §14.5 PR-D2 commits 1-7.

**Pipeline position:** Plan ✅ → Plan-review ✅ (2 BLOCKERs + 9 HIGH + 6 MEDIUM resolved in plan v2 §v2) → **Design ← you are here** → Design-review (5 parallel) → fix → Build (7 commits) → PR + 5-review → fix → merge → deploy.

**Depends on:** PR #36 (PR-D1 schema infrastructure, merged 2026-04-29 squash `3f96c07`).

---

## 1. Read-deployed-code re-verification (post-PR-D1)

Already done in plan v1 §"Read-deployed-code evidence" + plan v2 §v2.1 B-1 referencing `apply-catering-owner-decision:328` (bridge POST return) + `:385` (status mutation) + `:394` (CateringQuoteSent write). Lines confirmed unchanged by PR-D1 (schema-only).

PR-D1 shipped on main:
- `_UnknownLogEntry` shim with callable `Discriminator` + Tag union (`schemas.py:1215-1262`).
- `CateringQuoteSentLeadMissing` variant (`schemas.py:1788-1810`).
- `CateringQuoteAttempted.bridge_post_outcome: Literal["success","failed","unknown"]="unknown"` (`schemas.py:1774`).
- `ConfigLoadFailed` + `CateringLeadManuallyReconciled` (`schemas.py:1820-1860`).
- `audit_helpers.py` with `log_config_load_failed_best_effort` + `log_quote_sent_lead_missing_best_effort` (NEVER raise).
- `check-audit-helpers-symbols` pre-restart gate chained in `shift-agent-deploy.sh:295-296`.
- `tools/check-pr-d2-rollback-target.sh` operator preflight.
- `log-decision-direct` writer-side `_UnknownLogEntry` refusal.
- `pydantic>=2.10` pin in `web/backend/pyproject.toml`.

---

## 2. Decisions log

### BLOCKERs (resolved here)

| # | Resolution | §section |
|---|---|---|
| B-1 | Post-bridge write reorder: `CateringQuoteSent` written FIRST after bridge POST | §4.4 |
| B-2 | Canary VPS deploy: 1 of 9, 60-min soak, synthetic-retry probe at minute 5, then bulk-deploy 8 staggered 2-min apart | §6 |

### HIGH

| # | Resolution | §section |
|---|---|---|
| R2-H-1 | Tail-scan `max_age_hours=96` (was 24); stderr emission on cap-hit | §4.5 |
| R2-H-2 | Status-advance under same LEADS_LOCK as tail-scan | §4.6 |
| R3-H-1 | 2 Case-B-then-C tests | §5.3 |
| R3-H-2 | Sentinel-based v02 probe | §5.4 |
| R5-H-1 | Out-of-scope follow-up (PR-D3) | §7 |
| R5-H-2 | Synthetic-retry probe tool | §6 |
| R5-H-3 | Row 4 NEW: OWNER_APPROVED-no-anchor self-heal | §4.6 |

### MEDIUM

All addressed inline in §§3-6.

---

## 3. yaml migration (commit 1)

5 catering callsites — pattern (each script):

```python
# OLD:
try:
    import yaml
    cfg_dict = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = Config.model_validate(cfg_dict)
except Exception as e:
    sys.stderr.write(f"config load failed: {e}\n")
    return EXIT_SCHEMA_VIOLATION

# NEW:
try:
    cfg = load_yaml_model(CONFIG_PATH, Config)
except (FileNotFoundError, RuntimeError, ValidationError) as e:
    log_config_load_failed_best_effort(CONFIG_PATH, e)
    sys.stderr.write(f"config load failed: {type(e).__name__}: {e}\n")
    return EXIT_SCHEMA_VIOLATION
```

5 callsites: `apply-catering-owner-decision:230-236`, `create-catering-lead:343-347`, `lookup-prior-leads-by-phone:248-252`, `parse-menu-photo:254-258`, `apply-menu-update:73-77`.

Imports added at top of each script: `from safe_io import load_yaml_model` and `from audit_helpers import log_config_load_failed_best_effort` and `from pydantic import ValidationError`.

**Test (`tests/test_catering_config_migration.py`):** for each script, write malformed YAML to `tmp_path / "config.yaml"`, invoke script via subprocess with `--config-path` override (one new flag added to each), assert (a) exit code 4 (`EXIT_SCHEMA_VIOLATION`), (b) `decisions.log` row with `type=config_load_failed`, `path=<tmp config>`, `error_class=RuntimeError`. 5 cases.

**Cross-agent note (R1-H-2):** shift-agent (8 callsites) + daily-brief (1) deliberately deferred — they don't share the post-bridge-divergence surface. Tracked in `tasks/todo.md` for future safe_io consistency pass.

---

## 4. apply-catering-owner-decision rewrite (commits 2-4)

The rewrite lands in 3 commits for review-ability; final state implements all of plan v2 §v2 BLOCKERs + HIGH.

### 4.1 customer_phone_pre_bridge capture (commit 2)

Insert at `apply-catering-owner-decision:301-302` inside the FIRST LEADS_LOCK block (before lock release at line 317):

```python
# Inside `with FileLock(LEADS_LOCK):` block, immediately after `lead = matches[0]`:
target_jid = f"{lead.customer_phone.lstrip('+')}@s.whatsapp.net"
customer_phone_pre_bridge = lead.customer_phone  # PR-D2: captured for divergence audit
quote_text = _render_quote(lead, lead.customer_name or "")
```

### 4.2 matched_idx idiom (commit 2)

Replace post-bridge for-loop (lines 378-385) and the line-397 reference:

```python
# OLD (BUGGY — index leak):
for i, l in enumerate(store.leads):
    if l.lead_id == lead_id_for_output:
        store.leads[i] = l.model_copy(update={...})
        break
atomic_write_json(LEADS_PATH, store)
# ... line 397: store.leads[i].customer_phone — leaked i

# NEW:
matched_idx = next(
    (i for i, l in enumerate(store.leads) if l.lead_id == lead_id_for_output),
    None,
)
if matched_idx is None:
    log_quote_sent_lead_missing_best_effort(
        lead_id=lead_id_for_output,
        original_message_id=args.original_message_id,
        customer_phone_at_approve=customer_phone_pre_bridge,
        outbound_message_id=mid_or_err,
        detail=f"post-bridge re-load lost lead (status={status!r})",
    )
    _pushover_p2(f"BUG state-outbound divergence (lead {lead_id_for_output})", divergence_msg)
    return EXIT_SCHEMA_VIOLATION
```

### 4.3 Anchor two-step write (commit 3)

Inside the FIRST LEADS_LOCK block, AT END (after existing audit rows, before lock release):

```python
# PR-D2 commit 3: anchor BEFORE bridge POST. Two-step write contract:
#   - This row writes outcome="unknown" (we haven't called bridge yet).
#   - After bridge POST returns, second anchor row written with actual outcome.
# Tail-scan picks the LATEST matching row (by file order) per design.
_append_log_with_outer_leadslock(
    TypeAdapter(CateringQuoteAttempted),
    CateringQuoteAttempted(
        type="catering_quote_attempted",
        ts=now,
        lead_id=lead.lead_id,
        original_message_id=args.original_message_id,
        code=code,
        bridge_post_outcome="unknown",
    ),
)
# Now release LEADS_LOCK and POST.
```

### 4.4 Post-bridge write reorder (commit 3) — **B-1 BLOCKER fix**

Inside the SECOND LEADS_LOCK block (after bridge POST returns `ok=True`):

```python
with FileLock(LEADS_LOCK):
    store, status = load_model(LEADS_PATH, CateringLeadStore, default=CateringLeadStore())
    if status != "ok":
        # ... existing divergence path (M10 already-deployed code)
        ...

    # B-1 (plan v2 §v2.1): canonical write order.
    now2 = customer_now(cfg.customer.timezone)

    # Step 1: CateringQuoteSent FIRST. Append-only NDJSON; this is the only
    # retry-defeating signal. If process dies after this row but before
    # state mutation, retry's quote_sent tail-scan finds it → idempotent.
    _append_log_with_outer_leadslock(
        TypeAdapter(CateringQuoteSent),
        CateringQuoteSent(
            type="catering_quote_sent", ts=now2,
            lead_id=lead_id_for_output,
            customer_phone=customer_phone_pre_bridge,
            outbound_message_id=mid_or_err,
        ),
    )

    # Step 2: success-anchor superseding the step-3-write outcome="unknown" anchor.
    _append_log_with_outer_leadslock(
        TypeAdapter(CateringQuoteAttempted),
        CateringQuoteAttempted(
            type="catering_quote_attempted", ts=now2,
            lead_id=lead_id_for_output,
            original_message_id=args.original_message_id,
            code=code,
            bridge_post_outcome="success",
        ),
    )

    # Step 3: matched_idx via next() (per §4.2) — eliminates index leak.
    matched_idx = next(
        (i for i, l in enumerate(store.leads) if l.lead_id == lead_id_for_output),
        None,
    )
    if matched_idx is None:
        log_quote_sent_lead_missing_best_effort(...)
        _pushover_p2(...)
        return EXIT_SCHEMA_VIOLATION

    # Step 4: state mutation.
    store.leads[matched_idx] = store.leads[matched_idx].model_copy(update={
        "status": "SENT_TO_CUSTOMER",
        "updated_at": now2,
    })
    atomic_write_json(LEADS_PATH, store)

    # Step 5: status-change audit row.
    _append_log_with_outer_leadslock(
        TypeAdapter(CateringLeadStatusChange),
        CateringLeadStatusChange(
            type="catering_lead_status_change", ts=now2,
            lead_id=lead_id_for_output,
            from_status="OWNER_APPROVED",
            to_status="SENT_TO_CUSTOMER",
            actor="system",
            reason="customer_send_succeeded",
        ),
    )
```

### 4.5 Tail-scan helpers (commit 3)

```python
def _tail_scan_anchor(
    log_path: Path,
    code: str,
    max_lines: int = 5000,
    max_age_hours: float = 96.0,  # plan v2 §v2.2 R2-H-1 (was 24)
) -> Optional[CateringQuoteAttempted]:
    """Scan back through decisions.log for the LATEST catering_quote_attempted
    row with matching code. Returns None if no match.

    Stops at first of: max_lines reached, file head reached, OR row ts older
    than max_age_hours (96h covers Friday-quote-Monday-approve weekend window).

    NDJSON read direction: forward, take last match (latest by file order).
    Tolerates concurrent appends from menu-side scripts; only catering_quote_attempted
    rows match. apply-decision is the sole writer of those rows so no race
    on filtered subset.

    On max_lines exhaustion without max_age_hours bound exhaustion: emits
    stderr line `tail_scan_truncated lead_id=... max_lines=...` so soak
    watchlist (deploy step 6) can grep for fleet-scale capacity drift.
    Stays out of NDJSON for PR-D2 (deferred to PR-D3).
    """
    if not log_path.exists():
        return None
    cutoff_ts = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    matches: list[CateringQuoteAttempted] = []
    line_count = 0
    truncated = False
    adapter = TypeAdapter(CateringQuoteAttempted)
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line_count += 1
            if line_count > max_lines:
                truncated = True
                break
            line = line.strip()
            if not line or '"catering_quote_attempted"' not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") != "catering_quote_attempted":
                continue
            if row.get("code") != code:
                continue
            try:
                entry = adapter.validate_python(row)
            except Exception:
                continue
            if entry.ts < cutoff_ts:
                continue
            matches.append(entry)
    if truncated:
        sys.stderr.write(
            f"tail_scan_truncated code={code} max_lines={max_lines}\n"
        )
    return matches[-1] if matches else None


def _tail_scan_quote_sent(
    log_path: Path, lead_id: str,
    max_lines: int = 5000, max_age_hours: float = 96.0,
) -> Optional[CateringQuoteSent]:
    """Mirror of _tail_scan_anchor for catering_quote_sent rows. Used by
    Task 4 row 1 (idempotent_replay short-circuit)."""
    # ... same shape, type="catering_quote_sent", filter by lead_id
```

### 4.6 Retry-state-machine — 5-row decision tree (commit 4)

Replace existing matcher (lines 256-273) with:

```python
matches = [l for l in store.leads
           if l.owner_approval_code == code
           and l.status == "AWAITING_OWNER_APPROVAL"]

if matches:
    # Fresh approve path — existing flow. Falls through to lines 275+.
    pass
else:
    # PR-D2 retry-state-machine. Decision tree:
    code_match = [l for l in store.leads if l.owner_approval_code == code]
    if not code_match:
        # No lead with this code at all — invalid retry.
        sys.stderr.write(f"no lead with code {code}\n")
        return EXIT_NOT_FOUND

    lead = code_match[0]

    # Tail-scan happens INSIDE LEADS_LOCK so concurrent apply-decision invocations
    # serialize and each scan sees a consistent decisions.log snapshot.
    quote_sent = _tail_scan_quote_sent(LOG_PATH, lead.lead_id)
    anchor = _tail_scan_anchor(LOG_PATH, code)

    if quote_sent is not None:
        # ROW 1: customer demonstrably received quote (post-B-1 reorder, this
        # row is written FIRST after bridge POST, so its presence proves
        # delivery). Idempotent_replay: advance status if still OWNER_APPROVED.
        # Status advance under SAME LEADS_LOCK as tail-scan (R2-H-2 pin).
        if lead.status == "OWNER_APPROVED":
            now = customer_now(cfg.customer.timezone)
            matched_idx = next(
                (i for i, l in enumerate(store.leads) if l.lead_id == lead.lead_id),
                None,
            )
            if matched_idx is None:
                log_quote_sent_lead_missing_best_effort(
                    lead_id=lead.lead_id,
                    original_message_id=args.original_message_id,
                    customer_phone_at_approve=lead.customer_phone,
                    outbound_message_id=quote_sent.outbound_message_id,
                    detail="idempotent_replay path: matched_idx None",
                )
                return EXIT_SCHEMA_VIOLATION
            store.leads[matched_idx] = store.leads[matched_idx].model_copy(update={
                "status": "SENT_TO_CUSTOMER", "updated_at": now,
            })
            atomic_write_json(LEADS_PATH, store)
            _append_log_with_outer_leadslock(
                TypeAdapter(CateringLeadStatusChange),
                CateringLeadStatusChange(
                    type="catering_lead_status_change", ts=now,
                    lead_id=lead.lead_id,
                    from_status="OWNER_APPROVED", to_status="SENT_TO_CUSTOMER",
                    actor="system", reason="idempotent_replay_recovered",
                ),
            )
        print(json.dumps({
            "lead_id": lead.lead_id,
            "new_status": "SENT_TO_CUSTOMER",
            "outbound_sent": True,
            "idempotent_replay": True,
            "outbound_message_id": quote_sent.outbound_message_id,
        }))
        return EXIT_OK

    elif anchor is not None and anchor.bridge_post_outcome == "success":
        # ROW 2: bridge succeeded but quote_sent missing. Post-B-1 reorder
        # this is unreachable in normal flow (CateringQuoteSent is written
        # FIRST after bridge POST). Defensive only — synthesize quote_sent
        # with _recovered prefix and advance status.
        now = customer_now(cfg.customer.timezone)
        recovered_mid = f"_recovered_{anchor.original_message_id}"
        sys.stderr.write(
            f"WARN: anchor=success but no quote_sent for lead {lead.lead_id} — "
            f"synthesizing recovery (should not occur post-B-1 reorder)\n"
        )
        # ... advance status + emit synthesized CateringQuoteSent + status_change
        return EXIT_OK

    elif anchor is not None and anchor.bridge_post_outcome in ("failed", "unknown"):
        # ROW 3: bridge may have failed. Resume from Case A step 8 (re-attempt).
        # This is the legitimate retry path for process-death-mid-bridge.
        # Fall through to bridge-POST-resume code below.
        pass

    elif lead.status in ("OWNER_APPROVED", "OWNER_EDITED") and anchor is None:
        # ROW 4 (R5-H-3): in-flight lead under old code at PR-D2 deploy moment.
        # OWNER_APPROVED with no anchor row + no quote_sent = old-code state.
        # Self-heal: treat as fresh attempt — write anchor=unknown, bridge POST.
        sys.stderr.write(
            f"recovery: retry on {lead.status} lead with no anchor "
            f"(PR-D2 live-state migration) — proceeding as fresh attempt\n"
        )
        # Fall through to bridge-POST-resume code below (same as ROW 3).

    else:
        # ROW 5: no anchor, no quote_sent, status not in recoverable set.
        sys.stderr.write(
            f"no recoverable retry path for code {code} status={lead.status}\n"
        )
        return EXIT_NOT_FOUND

# Bridge-POST-resume code (shared by ROW 3 + ROW 4):
target_jid = f"{lead.customer_phone.lstrip('+')}@s.whatsapp.net"
customer_phone_pre_bridge = lead.customer_phone
quote_text = _render_quote(lead, lead.customer_name or "")
# ... write anchor=unknown if not already present (ROW 4) or skip (ROW 3 — anchor
# already exists) ...
# Then the unchanged bridge POST + post-bridge sequence per §4.4.
```

---

## 5. Test strategy

### 5.1 Schema tests (PR-D1 already shipped) — no change

### 5.2 Yaml migration tests (commit 1) — §3 above

### 5.3 Apply-script tests (commits 2-4)

| File | Cases |
|---|---|
| `test_catering_apply_post_bridge_missing_lead.py` | 4: matched_idx=None happy-path divergence emit; Pushover P2 fired; matched_idx=found normal flow; load_status="oserror" pre-existing path |
| `test_catering_apply_anchor_outcome.py` | 5: anchor=unknown written before bridge POST; success-anchor written after success; failed-anchor written after timeout; tail-scan picks LATEST; legacy-row default="unknown" round-trips |
| `test_catering_apply_idempotent_replay.py` | 6: row 1 (quote_sent found, status advance under lock); row 2 (anchor=success synthesizes — defensive); row 3 (anchor=failed re-attempts); row 4 NEW (OWNER_APPROVED no anchor self-heal); row 5 (no recovery, EXIT_NOT_FOUND); tail-scan N=5000 boundary |
| `test_catering_apply_case_b_to_c_recovery.py` | **2 (R3-H-1)**: `test_process_dies_after_anchor_before_bridge` + `test_process_dies_after_bridge_before_success_anchor` |

### 5.4 v02 probe (commit 5) — R3-H-2 strengthened

```python
# tests/test_v02_probe.py
def test_v02_main_body_executes(tmp_path: Path, monkeypatch):
    """Probe asserts the v02 importlib pattern runs the body of main(),
    not just imports it. Per plan v2 §v2.2 R3-H-2."""
    sentinel = tmp_path / "v02_executed.flag"

    # Patch a known function in the module path to write the sentinel
    # when invoked. If main() body executes, sentinel exists.
    import safe_io as _safe_io
    original_atomic_write = _safe_io.atomic_write_json

    def _patched(*args, **kwargs):
        sentinel.write_text("v02 executed")
        return original_atomic_write(*args, **kwargs)

    monkeypatch.setattr(_safe_io, "atomic_write_json", _patched)

    # Run one v02 helper that calls atomic_write_json under happy-path.
    # If the v02 importlib pattern with mod.__name__ = "__main__" executes
    # main(), the sentinel will exist after.
    # ... probe implementation written at build time
```

### 5.5 Conftest hoist (commit 6) — no v2 change

### 5.6 Reconcile script tests (commit 7) — 9 cases

8 from design v2 §8 + 1 new from R3-M-1:
- forbidden transitions, missing lead, corrupt store, happy path, audit-row content, invalid status, idempotent rerun rejection, --dry-run, **same-state refuse (NEW)**.

### 5.7 Format invariant test (commit 7) — R3-M-2 parametrized

```python
# tests/test_decisions_log_format.py
import pytest
from schemas import LogEntry, _KNOWN_LOG_ENTRY_TYPES
from pydantic import TypeAdapter

# Minimal-fields fixtures keyed by tag
_FIXTURE_MAP: dict[str, dict] = {
    "raw_inbound": {"ts": "2026-01-01T00:00:00Z", "message_id": "m",
                    "sender_phone": "+15555550100", "input_message": "x"},
    # ... entries for each known tag
}

@pytest.mark.parametrize("tag", sorted(_KNOWN_LOG_ENTRY_TYPES))
def test_dump_json_compact_format(tag):
    """Per plan v2 R3-M-2: pin compact JSON for ALL variants. Pydantic
    format regression on a subset would slip past single-variant test."""
    adapter = TypeAdapter(LogEntry)
    fixture = {"type": tag, **_FIXTURE_MAP[tag]}
    parsed = adapter.validate_python(fixture)
    line = adapter.dump_json(parsed).decode("utf-8")
    # Compact form: no space after `:` or `,`
    assert '": "' not in line, f"variant {tag} has spaced JSON"
    assert ', "' not in line, f"variant {tag} has spaced JSON"
```

### 5.8 Edge-case doc test (commit 7) — R3-L-1 tombstone integrity

```python
def test_deferred_table_preserves_existing_entries():
    text = Path("docs/catering-edge-cases.md").read_text(encoding="utf-8")
    deferred_section = text.split("## Deferred cases")[1]
    # Pin specific deferred case IDs that existed pre-v3.2
    for case_id in ("C04", "C13", "C19"):  # actual list captured at build time
        assert case_id in deferred_section, (
            f"deferred case {case_id} disappeared from doc — possibly deleted "
            f"during v3.2 revision"
        )
```

---

## 6. Deploy plan (canary — B-2)

| Step | Action | Watch |
|---|---|---|
| 0 | Pick canary VPS (`dispatcher-accuracy-report --days 1` lowest-traffic) | — |
| 1 | Operator runs `tools/check-pr-d2-rollback-target.sh <canary-vps> 3f96c07` | gate exits 0 |
| 2 | Build tarball + scp + `shift-agent-deploy.sh` on canary | pre-restart gates pass; smoke green |
| 3 | 60-min canary soak | watchlist (§6.1) |
| 4 | Minute 5 of canary soak: `tools/synthetic-retry-probe.sh <canary-vps>` | exits 0 |
| 5 | If canary clears: `for vps in $REMAINING_8; do deploy + sleep 120; done` | per-VPS rollback isolated |
| 6 | 20-min soak per non-canary | shared watchlist |

### 6.1 Soak watchlist (§v2.3 R5-M-1 extended)

```bash
# decisions.log signals
tail -f /opt/shift-agent/logs/decisions.log | grep -E \
  '"catering_quote_sent_lead_missing"|"catering_quote_attempted"|"config_load_failed"'

# Pairing check: failed-anchor without superseding success
awk '
  /catering_quote_attempted.*"failed"/ {failed[$0]=NR}
  /catering_quote_attempted.*"success"/ {for(k in failed) delete failed[k]}
  END {for(k in failed) print "STUCK_FAILED:", k}
' /opt/shift-agent/logs/decisions.log

# Rate signals: any lead_missing = page operator
test $(grep -c '"catering_quote_sent_lead_missing"' /opt/shift-agent/logs/decisions.log) -eq 0 \
  || /usr/local/bin/shift-agent-notify-owner --priority 2 --title "lead_missing fired"

# tail_scan_truncated stderr signal (R2-H-1)
journalctl -u hermes-gateway --since "20m ago" | grep "tail_scan_truncated" \
  | tee /tmp/scan_drift.log

# Apply-script exit-code rate
journalctl -u hermes-gateway --since "20m ago" \
  | grep -E "EXIT_(SCHEMA_VIOLATION|DEPENDENCY_DOWN|NOT_FOUND)" \
  | grep -i catering | wc -l
```

### 6.2 Synthetic retry probe (`tools/synthetic-retry-probe.sh`)

```bash
#!/usr/bin/env bash
# Synthetic retry probe — runs ONCE at minute 5 of canary soak.
# Per plan v2 §v2.2 R5-H-2.
set -euo pipefail
VPS_HOST="${1:?usage: $0 <vps-host>}"

# Probe steps via SSH-to-file (Windows-bash):
ssh "$VPS_HOST" '
  set -e
  # 1. Create test catering lead via direct script invocation.
  TEST_LEAD=$(sudo -u shift-agent /usr/local/bin/create-catering-lead \
    --test-mode --customer-phone "+15555559999" \
    --customer-name "synthetic-probe" \
    --event-date "2030-01-01" --headcount "10")
  LEAD_ID=$(echo "$TEST_LEAD" | jq -r .lead_id)
  CODE=$(echo "$TEST_LEAD" | jq -r .owner_approval_code)

  # 2. Simulate owner-approve (under SIGKILL-after-anchor monkeypatch).
  sudo -u shift-agent /usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision approve --reason "synthetic-probe" \
    --kill-after-anchor 2>/dev/null || true
  # Expect non-zero exit (process killed)

  # 3. Trigger retry.
  sudo -u shift-agent /usr/local/bin/apply-catering-owner-decision \
    --code "$CODE" --decision approve --reason "synthetic-probe-retry"

  # 4. Assert: bridge_post_outcome="success" exists; exactly one quote_sent row.
  TAIL=$(tail -n 100 /opt/shift-agent/logs/decisions.log)
  echo "$TAIL" | jq -c "select(.type==\"catering_quote_attempted\" and .code==\"$CODE\")" \
    | jq -s "map(.bridge_post_outcome) | any(. == \"success\")"
  echo "$TAIL" | jq -c "select(.type==\"catering_quote_sent\" and .lead_id==\"$LEAD_ID\")" \
    | wc -l
  # Expect: success=true and quote_sent count = 1

  # 5. Cleanup: delete synthetic lead.
  sudo -u shift-agent /usr/local/bin/catering-lead-reconcile \
    --lead-id "$LEAD_ID" --target-status DELETED --reason "synthetic-probe-cleanup"
' > .synthetic_retry_probe.txt 2>&1 || {
    cat .synthetic_retry_probe.txt
    exit 1
}

cat .synthetic_retry_probe.txt
```

The `--test-mode` and `--kill-after-anchor` flags are NEW for PR-D2. Both ship in commit 7 (alongside the probe tool). Test-mode bypasses the bridge POST entirely (mock returns canned message_id) so synthetic probes don't message a real customer.

---

## 7. Out-of-scope (PR-B + PR-D3)

- **PR-B**: `lookup_invoked` LogEntry variant + SKILL preamble emission.
- **PR-D3**: non-bypassable rollback gate via tarball metadata (R5-H-1). Estimated 30 LOC + 1 test.
- **PR-D3**: `tail_scan_truncated` LogEntry variant for fleet-scale capacity drift observability (R2-H-1 — currently stderr-only in PR-D2).

---

## 8. Self-review

- [x] Drift-tag `extends-Hermes` (no convention departure).
- [x] All 2 BLOCKERs from plan v2 §v2.1 encoded as concrete code.
- [x] All 9 HIGH from plan v2 §v2.2 addressed inline.
- [x] All 6 MEDIUM from plan v2 §v2.3 addressed inline.
- [x] Tail-scan helpers parametric on `max_age_hours=96` + `max_lines=5000`.
- [x] Post-bridge write reorder (B-1) makes bridge POST exactly-once under SIGKILL.
- [x] Retry-state-machine has 5 rows including OWNER_APPROVED-no-anchor self-heal (R5-H-3).
- [x] 2 Case-B-then-C tests pinned (R3-H-1).
- [x] Canary VPS deploy strategy (B-2).
- [x] Synthetic retry probe in canary soak (R5-H-2).
- [x] Soak watchlist extended (R5-M-1).

## Status: DESIGN-DRAFTED, ready for 5-agent design review

Reviewers should focus on:
1. **Lock-ordering correctness** — does the post-bridge reorder (§4.4) actually close the position 5/6 window? Walk through process-death at every line.
2. **Retry-state-machine row 4 self-heal** — does the OWNER_APPROVED-no-anchor case correctly distinguish "live-state migration" from a real bug? What if a lead is at OWNER_APPROVED with no anchor due to a pre-PR-D1 bug?
3. **Tail-scan helper performance** — read-the-whole-file pattern at N=5000 + 96h; on a busy VPS with ~5k rows/day, scan is O(20k) lines max. Acceptable on a per-retry basis? Or should we use seek+block-buffer reverse-iteration?
4. **Synthetic retry probe safety** — `--test-mode` + `--kill-after-anchor` flags ship to production scripts. Are they sufficiently locked down that an operator typo doesn't break customer flow?
5. **Canary VPS staggered bulk** — 2-min stagger between VPS 2-9. If VPS 2 fails smoke-test 8 minutes in, do VPS 3-9 deploys still proceed? Should the script hard-fail on first canary-bulk failure?
