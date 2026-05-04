# PR-CF7 — F7 catering-dispatcher-watchdog → cf-router plugin migration

**Drift-check tag:** `extends-Hermes`

(Hermes substrate is used unchanged. The plugin extends `cf-router` (PR-CF6) with a new hook path that uses `threading.Timer` for the 30s rescue-window. No new audit infrastructure — reuses existing `CateringDispatcherWatchdogFired` / `CateringDispatcherWatchdogSuppressed` variants. Net code change is REMOVAL of a 427-LOC daemon + systemd unit, adding ~250 LOC inside the existing cf-router plugin.

**Pattern-precedent disclosure (per CLAUDE.md drift Part 3):** `threading.Timer` spawned from inside an async `pre_gateway_dispatch` hook has no deployed precedent in this codebase. The cf-router PR-CF6 plugin uses no threading. Risk + safety analysis is in §"Risks" #4 below — the callback path is sync-only (`safe_io.ndjson_append` uses `os.open`/`os.write`/`os.fsync` with no asyncio coupling, verified at src/platform/safe_io.py), so the Timer thread does not need to interact with the gateway's event loop.)

## TL;DR

Migrate F7 (`catering-dispatcher-watchdog` daemon + `catering-dispatcher-watchdog.service` systemd unit) into the existing `cf-router` Hermes plugin (PR-CF6). Continues the F8/F9 pattern: replace custom watchdog daemons with `pre_gateway_dispatch` hooks. F7's specific wrinkle — a 30s rescue window after the LLM gets first-attempt — is handled by `threading.Timer` inside the plugin process.

**Net change (final, after PR-review fixes):** roughly -300 LOC of code/tests + 1 systemd unit + 1 long-running daemon process removed. Plan doc adds ~330 LOC of design content (doesn't ship). Original design v1 estimated -177 LOC; the actual delta is close because the audit-log-scan hardening (file-size snapshot + bounded reverse scan + upper-bound timestamp guard) added ~50 LOC vs the planned port, and the new smoke-test plugin-import check added ~30 LOC. Run `git diff --stat main..HEAD` for the authoritative numbers.

## Hermes-first checklist

| Step | [Hermes] / [net-new] | Notes |
|---|---|---|
| `pre_gateway_dispatch` hook to observe inbounds | [Hermes] | Already wired in PR-CF6; PR-CF7 extends the existing `cf-router` plugin's hooks.py with a new code path |
| Threading.Timer for 30s delayed rescue | [Python stdlib] | No infrastructure beyond stdlib; preserves F7's "give LLM a chance first" semantic |
| F7 classifier (multi-signal regex) | [net-new] (port from F7 daemon) | The 26 classifier tests in `tests/test_catering_dispatcher_classifier.py` become the regression suite. Two-line edit needed: the test file's `DISPATCHER_WATCHDOG = REPO / "src" / "agents" / "catering" / "scripts" / "catering-dispatcher-watchdog"` constant becomes `PLUGIN_ACTIONS = REPO / "src" / "plugins" / "cf-router" / "actions.py"`, and the `_load(DISPATCHER_WATCHDOG, ...)` call retargets accordingly. **Sequencing constraint:** Commit 3 (test re-target) MUST land before Commit 4 (daemon delete) or CI breaks with `FileNotFoundError` on the dead path. |
| Audit log scan for `dispatcher_routed` | [net-new] (port from F7 daemon) | Same logic as F7's `find_dispatcher_routed_for()` — read decisions.log, filter by chat_id + ts |
| Subprocess invocation of `create-catering-lead` | [Hermes] (deployed script) | Already exists; plugin just calls it |
| Audit emission via NDJSON chokepoint | [Hermes] | Reuses safe_io.ndjson_append, same pattern as cf-router today |
| `CateringDispatcherWatchdogFired` / `...Suppressed` audit variants | [Hermes] (existing) | Schemas unchanged — plugin emits the SAME audit-row shapes the daemon does, so observability tooling (dispatcher-accuracy-report, etc) keeps working |

**Net-new effort estimate:** ~250 LOC inside `src/plugins/cf-router/`, ~50 LOC of new tests. Removes 427 LOC daemon + 30 LOC systemd unit + 1 long-running process. Total ~3-4 hours including 5-agent review cycles.

## Why this PR (and why now)

PR-CF6 established the cf-router plugin pattern for replacing F8/F9 watchdog daemons. F7 was deferred at that time because:

> F7 catches missed catering inquiries (lead-creation gap). The plugin's `pre_gateway_dispatch` could replace it too, but inquiry-detection requires content classification (regex with multi-signal scoring) — already implemented in F7 watchdog and works.

— `cf-router-plugin-plan.md` (deleted in PR #58 bucket-C cleanup; quote preserved at git tag `pre-tasks-cleanup-2026-05-04`)

The deferral was about scope-control during PR-CF6, not a structural blocker. With PR-CF6 live + soaked + the F7 classifier tests preserved as a regression suite, PR-CF7 is now low-risk pattern-reuse.

This PR-CF7 is also the LAST clear plugin-collapse opportunity from current Hermes 0.12.0 hooks, per the hook enumeration audit (2026-05-04):

| Other candidate | Verdict |
|---|---|
| F11 cross-agent leakage prevention | NOT viable — `_bridge_post()` is a private function in apply-script, not a Hermes tool, so `pre_tool_call` would never fire |
| Routing-accuracy reporter migration | NOT viable — Hermes 0.12.0 has no `cron` hook nor `post_gateway_dispatch` hook |
| Cron-based agents (Daily Brief, EOD) | NOT viable — same |

PR-CF7 is the logical conclusion of the cf-router-pattern arc.

## Design

### Plugin extension structure

`src/plugins/cf-router/hooks.py` adds an F7 path to the existing `pre_gateway_dispatch` handler. Two new module-level additions: a `F7_ENABLED` feature flag (rollback target) and an `_extract_message_id` helper with a deterministic fallback (the event object may not expose a message_id natively, but `CateringDispatcherWatchdogFired/Suppressed` schemas require `min_length=1`):

```python
# Module-level — at top of hooks.py
F7_ENABLED = True  # Rollback Option A: sed this to False + restart hermes-gateway

F7_WATCHDOG_TIMEOUT_SEC = 30  # Matches deployed F7 daemon


def _extract_message_id(event) -> str:
    """Defensive message_id extraction with deterministic fallback.

    Hermes MessageEvent shape varies across adapters; not all expose a
    native message_id. The CateringDispatcherWatchdog* audit variants
    require min_length=1, so we ALWAYS produce a non-empty string. The
    fallback mirrors the deployed F7 daemon's `bridge_notify_<chat>_<ms>`
    pattern so historical audit-log greps continue to work.
    """
    for attr in ("message_id", "id", "msg_id"):
        val = getattr(event, attr, None)
        if isinstance(val, str) and val:
            return val
    chat_id = _extract_chat_id(event) or "unknown"
    return f"cf_router_f7_{chat_id}_{int(time.time() * 1000)}"


def pre_gateway_dispatch(event, gateway=None, session_store=None, **_kwargs):
    try:
        text = _extract_text(event)
        chat_id = _extract_chat_id(event)
        if not text or not chat_id:
            return None

        # F8 path (existing — owner self-chat + #XXXXX code → bypass LLM)
        if actions.is_owner_chat(chat_id):
            f8_result = _try_f8_intercept(text, chat_id)
            if f8_result is not None:
                return f8_result

        # F9 path (existing — employee + sick-call regex → alert only)
        if _is_sick_call(text) and actions.is_employee_chat(chat_id):
            _try_f9_alert(text, chat_id)
            return None

        # F7 path (NEW — non-owner/non-employee + catering classifier → schedule 30s rescue)
        # Gated on F7_ENABLED so operators can sed-disable as a fast rollback.
        if F7_ENABLED:
            is_catering, signals = actions.classify_catering(text)
            if is_catering:
                message_id = _extract_message_id(event)
                _schedule_f7_rescue(text, chat_id, message_id, signals)
                # Don't return skip — let LLM handle the inquiry first;
                # rescue only fires if LLM misses (preserves F7's exact
                # semantic).

        return None

    except Exception as e:
        # Plugin must never crash the gateway
        ...
        return None
```

### F7 rescue scheduling (`_schedule_f7_rescue`)

```python
def _schedule_f7_rescue(text, chat_id, message_id, signals):
    """Schedule a 30s delayed check. If no `dispatcher_routed` audit row
    appears for this chat_id within the window, invoke create-catering-lead."""
    ts_at_schedule = time.time()
    threading.Timer(
        F7_WATCHDOG_TIMEOUT_SEC,  # 30s — matches deployed F7 daemon
        actions.f7_rescue_check,
        args=(text, chat_id, message_id, signals, ts_at_schedule),
    ).start()
    # Note: threading.Timer is daemon-style; it dies if the gateway process
    # restarts. That's acceptable — gateway restart implies new inbounds
    # will be handled fresh anyway, and the orphan-text recovery is a
    # rescue path, not the primary flow.
```

### F7 rescue check (`actions.f7_rescue_check`)

```python
def f7_rescue_check(text, chat_id, message_id, signals, ts_at_schedule):
    """Background-thread callback fired 30s after pre_gateway_dispatch.
    Mirrors process_inbound() in the deployed F7 daemon."""
    try:
        # 1. Did the LLM handle it? Check audit log for dispatcher_routed
        if _find_dispatcher_routed_for(chat_id, ts_at_schedule):
            return  # SKILL ran successfully — no rescue needed

        # 2. Resolve sender role (must be customer, not owner/employee)
        phone, role = _lid_to_phone_via_identify_sender(chat_id)
        if role in {"owner", "employee"}:
            audit_dispatcher_watchdog_suppressed(
                chat_id=chat_id, message_id=message_id,
                reason="non_customer_role", detail=f"role={role}",
            )
            return

        # 3. Phone resolution required (need E.164 for create-catering-lead)
        if not phone:
            audit_dispatcher_watchdog_suppressed(
                chat_id=chat_id, message_id=message_id,
                reason="lid_no_phone_resolution",
                detail=f"signals={','.join(signals)} text_preview={text[:60]!r}",
            )
            return

        # 4. Fire the rescue: invoke create-catering-lead with empty extracted
        success, detail = _trigger_create_catering_lead(
            customer_phone=phone, customer_name="",
            raw_inquiry=text, message_id=f"watchdog:{message_id}",
        )
        audit_dispatcher_watchdog_fired(
            chat_id=chat_id, message_id=message_id, customer_phone=phone,
            signals=signals, success=success, detail=detail[:2000],
        )
    except Exception as e:
        sys.stderr.write(f"cf-router F7 rescue failed (non-fatal): {e}\n")
```

### What changes vs the deployed F7 daemon

| F7 daemon does | Plugin equivalent |
|---|---|
| Tails `bridge.log` for inbound notify events | Hook `pre_gateway_dispatch` fires natively per inbound — no log-tail needed |
| Scans `agent.log` to match the inbound text back to the notify | Plugin gets `event.text` directly — no cross-log matching |
| `time.sleep(2)` polling loop on `dispatcher_routed` | `threading.Timer(30, ...)` callback (no busy-poll) |
| `process_inbound()` runs in the daemon's main loop | `f7_rescue_check()` runs in a background thread |
| Long-running systemd-managed Python process (`catering-dispatcher-watchdog.service`) | In-process inside `hermes-gateway.service` (no separate unit) |

### What does NOT change

- Same classifier regex (`_CATERING_PRIMARY`, `_HEADCOUNT_PATTERNS`, `_EVENT_KEYWORDS`, `_FOOD_KEYWORDS`, `_DELIVERY_KEYWORDS`)
- Same multi-signal threshold logic (`is_catering = (catering AND any-other) OR (headcount AND event) OR (headcount AND food AND (delivery OR event))`)
- Same 30s rescue window
- Same role-gating (owner/employee → suppressed, customer → fire)
- Same phone-resolution requirement
- Same audit-row shapes (`CateringDispatcherWatchdogFired`, `CateringDispatcherWatchdogSuppressed`)
- Same downstream `create-catering-lead` invocation with empty extracted fields

The 26 classifier tests in `tests/test_catering_dispatcher_classifier.py` (preserved by PR #58) become the regression suite. Two-line edit: retarget the `DISPATCHER_WATCHDOG` path constant + the `_load(...)` call to `src/plugins/cf-router/actions.py`. The test bodies (which call `dispatcher_mod.classify_catering(text)`) are unchanged.

### Audit-row reachability after migration (BEHAVIOR DELTA — explicit)

The deployed `CateringDispatcherWatchdogSuppressed.reason` Literal has 4 values. After migration, only 2 remain reachable from the plugin code path:

| `reason` value | Deployed F7 emits? | PR-CF7 plugin emits? | Why the change |
|---|---|---|---|
| `non_customer_role` | ✅ Yes | ✅ Yes | Same logic in `f7_rescue_check` |
| `lid_no_phone_resolution` | ✅ Yes | ✅ Yes | Same logic in `f7_rescue_check` |
| `text_unavailable` | ✅ Yes | ❌ No (unreachable) | Plugin gets `event.text` directly — never has missing text |
| `not_catering` | ✅ Yes (after 30s + scan) | ❌ No (unreachable) | Plugin runs classifier at `pre_gateway_dispatch` time and only schedules the Timer when `is_catering=True` — non-catering messages never enter the rescue path |

**Operator impact:** `tail -F decisions.log \| grep '"reason":"not_catering"'` will report 0 matches after migration. Operators currently use this row to tune the classifier's false-negative rate. The compensating mechanism is the 26-case test suite in `test_catering_dispatcher_classifier.py` — operators tune the classifier via test additions, not via audit-log sampling.

**Schema decision:** keep `text_unavailable` and `not_catering` in the `Literal` for two reasons: (a) backward-compat with historical audit rows that may still need to be parsed; (b) if we ever DO want to emit them from the plugin (e.g. add a "classifier rejected" diagnostic row), the schema doesn't need re-migrating. PR #58's `_UnknownLogEntry` passthrough already handles forward-compat in the other direction.

## What gets DELETED after this PR ships

- `src/agents/catering/scripts/catering-dispatcher-watchdog` (427 LOC)
- `src/agents/catering/systemd/catering-dispatcher-watchdog.service` (38 LOC)
- The deployed `/usr/local/bin/catering-dispatcher-watchdog` binary
- The deployed `catering-dispatcher-watchdog.service` systemd unit
- Backup tag `pre-srilu-cleanup-2026-05-04` already covers the F8/F9 deletions; PR-CF7 will add a new tag `pre-cf7-cleanup-2026-05-04` before the F7 deletion.

## 30s rescue threshold — calibration note

The deployed F7 daemon sets `WATCHDOG_TIMEOUT_SECS=30` based on observation that the LLM had ~25% miss rate for catering inquiries before F7 shipped (daemon docstring 2026-05-01). PR-CF7 keeps the same 30s window. Open question — has the miss rate changed since PR-CF6 deployed?

- **No new measurement.** We have not re-quantified the LLM miss rate post-PR-CF6. The rescue is best-effort safety-net regardless of rate.
- **Gateway-hang interaction.** docs/hermes-alignment.md Part 2 documents a known "gateway dispatcher reasoning hang" failure mode where the LLM never fires `dispatcher_routed` within ~minutes. In that scenario the F7 plugin would always rescue, potentially flooding the owner with auto-created leads. The deployed F7 daemon has the same exposure today; PR-CF7 does not change it. If operationally this becomes a problem, the next step is rate-limiting `f7_rescue_check` per chat_id (similar to the F9 5-minute Pushover throttle we already have in cf-router/actions.py).
- **Recommendation:** ship at 30s unchanged; instrument the audit-row counter manually for the first 7 days post-deploy via `grep '"type":"catering_dispatcher_watchdog_fired"' /opt/shift-agent/logs/decisions.log | wc -l` (compared against the same period's `raw_inbound` count from `dispatcher-accuracy-report`); tune if the rescue rate exceeds ~10% of all customer inquiries. Adding a `--watchdog-rescue-rate` mode to `dispatcher-accuracy-report` is a separate follow-up — not currently implemented.

## Risks

1. **Threading.Timer dies on gateway restart.** A pending rescue task is lost if the gateway restarts during the 30s window. **Mitigation:** acceptable — gateway restart implies operator presence; the lost rescue is one missed inquiry, recoverable via dispatcher-accuracy-report the next day.

2. **In-process state means no cross-process visibility.** Operators can no longer `journalctl -u catering-dispatcher-watchdog` to see the watchdog's specific logs — plugin output is mixed into the gateway's stdout. **Mitigation:** the plugin emits the same audit rows; observability shifts from journalctl-per-unit to grep-per-audit-row, which is already the canonical pattern (`tail -F /opt/shift-agent/logs/decisions.log | grep catering_dispatcher_watchdog_*`).

3. **Plugin error swallows the message silently** — Hermes wraps each plugin's hook in try/except; if the plugin crashes, the LLM sees the message normally. Acceptable failure mode (same as cf-router today).

4. **Threading inside async gateway** — The Hermes gateway is async; spawning `threading.Timer` from inside an async hook crosses the async/sync boundary. Plugin uses pure threading (no asyncio coupling), so the Timer thread is independent. **Mitigation:** verified pattern — `threading.Timer.start()` does not require an event loop; it spawns a vanilla OS thread.

5. **Pre-existing cf-router plugin keeps working** — F8/F9 paths in `_try_f8_intercept` and `_try_f9_alert` are NOT modified. F7 is added as a separate code path that runs after F8/F9 checks fail. No regression risk to PR-CF6 functionality.

## Reviewer lens for design phase

- **Hermes-first correctness**: confirm we extend the existing cf-router plugin rather than create a new one (consolidation per CLAUDE.md "don't introduce abstractions beyond what the task requires")
- **Threading safety**: is `threading.Timer` actually safe inside the Hermes gateway's async event loop? Verify there's no GIL deadlock or atomic-write race with the audit log
- **Test coverage parity**: the 26 classifier tests in `test_catering_dispatcher_classifier.py` need to continue passing after the loader target moves from the daemon to the plugin
- **Audit row shape preservation**: existing `dispatcher-accuracy-report` and observability tooling read these audit rows; ensure the plugin emits IDENTICAL JSON (same `type`, `chat_id`, `message_id`, `signals`, etc)
- **F8/F9 regression risk**: PR-CF6's existing 31 tests (`test_cf_router_plugin.py`) must continue passing — F7 is additive, not modifying

## Build sequence

**Sequencing constraint:** Commit 3 (test re-target) MUST land before Commit 4 (daemon delete), or the test suite will fail at fixture-load time with `FileNotFoundError` on the dead path. The PR will be a single squash-merge so internal commit order matters less than the in-PR test pass, but landing them out of order during local development WILL break `pytest tests/`.

1. **Commit 1**: extend `src/plugins/cf-router/actions.py` with F7 helpers (`f7_rescue_check`, `classify_catering`, `_find_dispatcher_routed_for`, `_lid_to_phone_via_identify_sender`, `_trigger_create_catering_lead`, `audit_dispatcher_watchdog_fired`, `audit_dispatcher_watchdog_suppressed`). Port classifier regex + threshold + audit emit logic verbatim from deployed F7 daemon. ~150 LOC.

2. **Commit 2**: extend `src/plugins/cf-router/hooks.py` with the F7 path inside `pre_gateway_dispatch`. Add module-level `F7_ENABLED = True` flag + `F7_WATCHDOG_TIMEOUT_SEC = 30` constant + `_extract_message_id` helper with deterministic fallback. Wire `threading.Timer(30, ...)` for delayed rescue. ~80 LOC.

3. **Commit 3** (sequencing-critical): retarget `tests/test_catering_dispatcher_classifier.py` to load `src/plugins/cf-router/actions.py` instead of the watchdog script. Two-line edit: change `DISPATCHER_WATCHDOG` constant + the `_load(...)` call. Test bodies (which call `dispatcher_mod.classify_catering(text)`) are unchanged because the function signature is preserved. **Verify pytest passes here before Commit 4.**

4. **Commit 4** (depends on Commit 3): add new F7-path tests to `tests/test_cf_router_plugin.py` using the existing synthetic-package loader (`_load_plugin_modules()` from PR-CF6). New tests cover: F7 fires when classifier matches; F7 skipped when `F7_ENABLED=False`; rescue check finds `dispatcher_routed` → suppressed `non_customer_role` for owner/employee; rescue fires with `audit_dispatcher_watchdog_fired` when no dispatcher_routed + customer + phone resolves. ~10 tests.

5. **Commit 5** (depends on Commit 4): deletion — remove `src/agents/catering/scripts/catering-dispatcher-watchdog` + `src/agents/catering/systemd/catering-dispatcher-watchdog.service`. Update `shift-agent-deploy.sh` `install_artifacts()` to disable + stop the live `catering-dispatcher-watchdog.service` unit on next deploy (mirror the F8/F9 pattern from cf-router PR-CF6, but actually executable since the unit really does exist on srilu).

6. **Commit 6**: update plan doc + memory (`project_srilu_canonical_state.md` + new `project_pr_cf7_state.md`).

Total estimate: ~250 LOC plugin extension + ~50 LOC test refactor + ~30 LOC deploy-script updates. ~3-4 hours including 5-agent PR review cycle.

## Rollback runbook

If the F7 plugin path misbehaves on srilu:

```bash
# Option A — Disable F7 plugin path only (keep F8/F9 working)
# Edit /root/.hermes/plugins/cf-router/hooks.py: set F7_ENABLED = False at top
# Restart hermes-gateway. NOTE: This sed-edit will be OVERWRITTEN by the
# next `shift-agent-deploy.sh` run (rsync replaces /root/.hermes/plugins/
# from the new tarball). For a deploy that must survive across redeploys,
# you need a follow-up PR adding `cf_router.f7_enabled` to config.yaml +
# wiring the hook to read from there. For an immediate hotfix this is
# sufficient — operators are not deploying mid-incident.
sudo sed -i 's/^F7_ENABLED = True/F7_ENABLED = False/' /root/.hermes/plugins/cf-router/hooks.py
sudo systemctl restart hermes-gateway

# Option B — Full restore via backup tag (re-installs the F7 daemon + systemd unit)
#
# CRITICAL ORDERING: the operator must run the OLD tarball's
# shift-agent-deploy.sh, NOT the currently-installed one. The current
# script (this PR's version) has stop+disable+rm logic that would
# immediately undo the daemon restoration. Three-step procedure:

# 1. Build the pre-CF7 tarball locally
git worktree add /tmp/pre-cf7 pre-cf7-cleanup-2026-05-04
cd /tmp/pre-cf7
bash tools/build-deploy-tarball.sh --skip-pytest

# 2. Ship + EXTRACT (do NOT run the installed deploy yet)
scp shift-agent-deploy.tgz root@srilu-vps:/tmp/
ssh root@srilu-vps 'sudo rm -rf /opt/shift-agent/staging-new/* && \
                    sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/'

# 3. Replace /usr/local/bin/shift-agent-deploy.sh with the OLD tarball's
#    version BEFORE running it — this bootstraps the rollback to use the
#    pre-CF7 deploy logic that does NOT delete the F7 daemon.
ssh root@srilu-vps 'sudo install -m 755 \
    /opt/shift-agent/staging-new/src/agents/shift/scripts/shift-agent-deploy.sh \
    /usr/local/bin/shift-agent-deploy.sh && \
    sudo /usr/local/bin/shift-agent-deploy.sh'

# 4. Verify: the F7 daemon should now be enabled + active. If install_artifacts()
#    in the OLD deploy script enables the timer automatically, no manual step;
#    otherwise:
ssh root@srilu-vps 'sudo systemctl status catering-dispatcher-watchdog.service'
# (manual enable only if the OLD install_artifacts didn't auto-enable it)
ssh root@srilu-vps 'sudo systemctl enable --now catering-dispatcher-watchdog.service'

# 5. Disable cf-router's F7 path so it doesn't dual-fire alongside the daemon
ssh root@srilu-vps 'sudo sed -i "s/^F7_ENABLED = True/F7_ENABLED = False/" \
    /root/.hermes/plugins/cf-router/hooks.py && \
    sudo systemctl restart hermes-gateway'
```

Option A is the fast revert (under 30s) — sufficient for a hotfix when the operator can ship a fix tarball afterward. Option B is the full structural restore for the case where the plugin path itself is structurally broken AND the daemon must be brought back.

## Out of scope (future work)

- Festival calendar integration (Agent #11) — separate PR
- Productivity skill credential setup (Agent operator action #41) — separate operator step

## Test plan

- [ ] F7 classifier tests pass against the new plugin location (no regex changes — same 26 tests)
- [ ] New plugin-level tests cover: F7 path fires when classifier returns is_catering=True; F7 path skipped when sender is owner/employee; F7 rescue suppressed when dispatcher_routed appears; F7 rescue fired when no dispatcher_routed; threading.Timer cancellation on plugin reload
- [ ] PR-CF6's 31 existing tests pass (no F8/F9 regression)
- [ ] `bash tools/build-deploy-tarball.sh` builds clean (no `--skip-pytest` needed)
- [ ] On srilu post-deploy: F7 systemd unit disabled + stopped + removed; cf-router plugin loaded with F7_ENABLED=True; synthetic E2E test confirms a missed inquiry triggers rescue within 30s
