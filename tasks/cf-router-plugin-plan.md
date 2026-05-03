# PR-CF6 — cf-router Hermes plugin (replaces F8/F9 watchdogs)

**Drift-check tag:** `extends-Hermes`

(Hermes substrate is used unchanged, but the PR adds: a per-VPS throttle JSON file, a new `CfRouterIntercepted` audit variant in the `LogEntry` discriminated union, owner-LID resolution via `identify-sender`, and ~120 LOC of net-new interception logic. All net-new is plugin-local and uses the `safe_io` chokepoints; no parallel storage / audit infrastructure is introduced.)

The whole point: replace custom watchdog daemons (F8, F9) with native Hermes plugin hooks. Moves rescue-layer logic from `/usr/local/bin/*-watchdog` (custom code + systemd timers) to `~/.hermes/plugins/cf-router/` (Hermes substrate). F11 deferred to v2.

## Hermes-first checklist

| Step | [Hermes] / [net-new] | Notes |
|---|---|---|
| `pre_gateway_dispatch` hook to intercept inbounds | [Hermes] | Native hook (verified 2026-05-03 in `gateway/run.py:4197-4231`); returns `{"action": "skip"|"rewrite"|"allow", ...}` |
| Plugin manifest + register pattern | [Hermes] | Standard `plugin.yaml` + `__init__.py` pattern documented in `website/docs/guides/build-a-hermes-plugin.md` |
| Subprocess invocation of `apply-catering-owner-decision` / `apply-menu-update` | [Hermes] (deployed scripts) | Already exists; plugin just calls them |
| Pushover P2 alert via `shift-agent-notify-owner` | [Hermes pattern] | Mirrors existing apply-script Pushover pattern |
| Audit emission via NDJSON chokepoint | [Hermes] (existing variant `cf_router_intercepted` — NEW) | New audit variant in LogEntry union |
| F8 owner-approval interception logic | [net-new] (~80 LOC) | Regex extract `#XXXXX (verb)`; lookup state file; subprocess invoke; audit + skip |
| F9 sick-call detection logic | [net-new] (~40 LOC) | Regex pattern match + Pushover alert; LLM still runs (no skip) |

**Net-new effort**: ~120 LOC plugin + ~30 LOC audit variant + ~80 LOC tests. Replaces ~600 LOC of F8 watchdog + F9 notifier daemons + their systemd timers.

## What this PR ships

1. **`~/.hermes/plugins/cf-router/`** — new Hermes plugin (deployed via tarball + symlink)
   - `plugin.yaml` — manifest declaring `pre_gateway_dispatch` hook
   - `__init__.py` — register() entry point
   - `hooks.py` — `pre_gateway_dispatch_handler(event, gateway, session_store)` implementation
   - `actions.py` — subprocess helpers (apply-catering-owner-decision invoker, apply-menu-update invoker, Pushover invoker)

2. **`src/platform/schemas.py`** — new audit variant `CfRouterIntercepted` in LogEntry union with reason enum (`f8_owner_approve`, `f8_owner_reject`, `f8_menu_yes`, `f8_menu_no`, `f9_sick_call_alert`, `error`)

3. **`tests/test_cf_router_plugin.py`** — Linux-only pytest covering:
   - F8 owner-approve path: synthetic event → plugin extracts code → mocked subprocess → returns skip + audit row
   - F8 owner-reject path
   - F8 menu-yes path
   - F9 sick-call alert path: pattern detected → Pushover called → returns allow (LLM still runs)
   - Non-owner sender → returns None (no interception)
   - Owner sender + non-code text → returns None (LLM handles normally)
   - Code matches but no lead/menu found → returns None (graceful)

## What gets DELETED after this PR ships

After cf-router is verified live on srilu:
- `/usr/local/bin/catering-owner-action-watchdog` (F8) — replaced by plugin
- F8 systemd timer + service unit
- `/usr/local/bin/shift-missed-dispatch-notifier` (F9) — replaced by plugin
- F9 systemd timer + service unit
- All F8/F9 audit-row plumbing (kept as deprecated for 1-week post-merge then removed)

## What stays (NOT replaced by this PR)

- F7 `catering-dispatcher-watchdog` — STAYS. F7 catches missed catering inquiries (lead-creation gap). The plugin's `pre_gateway_dispatch` could replace it too, but inquiry-detection requires content classification (regex with multi-signal scoring) — already implemented in F7 watchdog and works. Migrating F7 to a plugin is a separate PR-CF7 if/when motivated.
- F11 cross-agent leakage prevention — STAYS as-is. Moving F11 to a `pre_tool_call` veto requires intercepting bridge-POST tool calls; the bridge isn't a Hermes tool (it's a script + HTTP), so the veto pattern doesn't cleanly apply. Defer to v2.
- F12/F13 (watchdog log-path + owner-LID detection) — these are F7 internals; stay with F7.
- F14 (proposal-first flow) — production feature, not a watchdog.

## Deployed-pattern checklist

- ✅ Hermes-native plugin loaded from `~/.hermes/plugins/` per documented pattern
- ✅ Subprocess invocation of deployed scripts (apply-catering-owner-decision, apply-menu-update, shift-agent-notify-owner) — no new external commands
- ✅ NDJSON audit via safe_io.ndjson_append chokepoint (new variant `cf_router_intercepted`)
- ✅ Pydantic v2 + extra="forbid" on new audit variant (inherited from `_BaseEntry`)
- ✅ No new approval codes (reuses existing `#[A-HJ-NP-Z2-9]{5}` alphabet)
- ✅ No new SKILL files, no dispatcher routing changes
- ✅ Tests: pytest + synthetic events + mocked subprocess + assert on audit rows + Linux-only via pytest.mark.skipif
- ✅ Owner identity check via config.yaml `owner.self_chat_jid` field (NOT pattern-match content) — preserves CLAUDE.md drift-rule "metadata not content"

## Build sequence

1. **Commit 1**: `CfRouterIntercepted` audit variant in schemas.py + LogEntry union entry + forward-compat test
2. **Commit 2**: Plugin skeleton — `plugin.yaml` + `__init__.py` + minimal `hooks.py` (allow-everything stub)
3. **Commit 3**: F8 owner-approval interception + tests
4. **Commit 4**: F9 sick-call detection + Pushover alert + tests
5. **Commit 5**: Deploy script integration — install plugin to `~/.hermes/plugins/` per VPS
6. **Commit 6**: Live verification on srilu + delete F8/F9 watchdog files + systemd timer disable
7. **Commit 7**: Documentation in canonical-template.md

Total estimate: ~250 LOC. ~3-4 hours including review cycle.

## Risks

- **Plugin reload requires gateway restart** — operator-touch event; OK for srilu (no real customers), needs canary discipline once we have customers.
- **Plugin error swallows the message silently** — Hermes wraps each plugin's hook in try/except per `gateway/run.py:4213-4215`; if the plugin crashes, the LLM sees the message normally. Acceptable failure mode.
- **F8 plugin invokes apply-script with the lead's existing quote_text (drafted by F14 at lead-creation), not a fresh LLM-drafted quote.** This matches what F8 watchdog did. PR-B v3 LLM-drafted-quote paradigm is preserved when the LLM IS in the loop; the plugin only fires when the LLM is bypassed.
- **F9 only alerts; doesn't actually create the proposal.** A real coverage flow needs LLM extraction (sender, date, role). The plugin just ensures the operator sees "something happened" if the LLM misses. Acceptable simplification for a notifier (was F9's role anyway).

## Reviewer lens for build phase

- **Hermes-first correctness**: confirm zero new substrate; plugin pattern matches the documented build guide
- **Drift compliance**: confirm new audit variant inherits `_BaseEntry` (extra="forbid"); subprocess invocations point at deployed scripts (not arbitrary commands)
- **F8 quote-text source**: confirm lead's persisted quote_text is non-empty before invoking apply-script (otherwise --quote-text-stdin gets empty string → exit 2)
- **F9 dedup**: confirm same sick-call message doesn't fire Pushover twice on retries (idempotency mechanism — likely cooldown window)
- **Test isolation**: each test uses tmp_path + monkey-patched subprocess; no real apply-script calls
- **Plugin error handling**: confirm hook returns None (not exception) if state files unreadable

## Out of scope (PR-CF7 follow-ups)

- F7 catering-dispatcher-watchdog → plugin migration
- F11 cross-agent leakage → `pre_tool_call` veto (needs bridge-POST tool wrapping first)
- Plugin-side LLM extraction for F9 (would let plugin actually invoke create-proposal with extracted fields)

## Rollback runbook (operator-facing)

If the plugin misbehaves in production (silently dropping owner approvals, firing wrong subprocesses, blocking the gateway, etc.):

```bash
# 1. Disable the plugin (remove from Hermes plugin dir)
sudo rm -rf /root/.hermes/plugins/cf-router

# 2. Restart hermes-gateway so the plugin no longer loads
sudo systemctl restart hermes-gateway

# 3. Re-enable the legacy F8/F9 watchdog timers (they were disabled at deploy time
#    by install_artifacts() when cf-router was installed)
sudo systemctl enable --now catering-owner-action-watchdog.timer
sudo systemctl enable --now shift-missed-dispatch-notifier.timer

# 4. Verify both are running
systemctl status catering-owner-action-watchdog.timer shift-missed-dispatch-notifier.timer

# 5. (Optional) Audit verification — recent intercepts should stop appearing
tail -f /opt/shift-agent/logs/decisions.log | grep cf_router_intercepted
```

Total revert time: under 30 seconds. The watchdog scripts and timers ship with every deploy and are only *disabled* (not deleted) when cf-router is present, so they are immediately re-armable.

## Cutover policy (avoiding F8/F9 dual-fire)

The plugin writes `cf_router_intercepted` audit entries; the F8/F9 watchdogs scan only for `dispatcher_routed` entries. If both are running simultaneously, the watchdogs would NOT see the plugin's intercept and would re-fire `apply-catering-owner-decision` (sending a duplicate quote to the customer) or emit a redundant Pushover.

Therefore the deploy script **disables F8/F9 systemd timers at install time** when `/root/.hermes/plugins/cf-router/` is present (see `install_artifacts()` in `shift-agent-deploy.sh`). The plan's earlier "24h dual-run observation window" idea was unsafe and has been removed.

If the operator needs to back out (see rollback runbook above), the timers are re-enabled and the watchdogs resume their old behavior.
