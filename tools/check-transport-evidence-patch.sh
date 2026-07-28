#!/usr/bin/env bash
# Transport-evidence harness deploy-gate checks (AMENDMENT 3), factored out of
# check-shift-agent-patch.sh so they are a single source of truth AND independently
# functionally-testable (tests/test_transport_evidence_deploy_gate.py drives the
# full accept/reject matrix against fixture trees).
#
# Repo-only, DEFAULT-OFF. Fail-closed so the immutable RC cannot be built with
# missing / partial wiring.
#
# Inputs (env): RUN, WA, PLATFORM  (gateway run.py, whatsapp.py, /opt/shift-agent).
# Exit 0 = all checks pass; non-zero (via fail) = a check failed.
set -euo pipefail

RUN="${RUN:?RUN (gateway/run.py path) must be set}"
WA="${WA:?WA (gateway/platforms/whatsapp.py path) must be set}"
PLATFORM="${PLATFORM:-/opt/shift-agent}"

# Define fail() only when run standalone (when sourced, the parent's fail wins).
if ! command -v fail >/dev/null 2>&1; then
    fail() { echo "FAIL: $1" >&2; exit 1; }
fi

# run.py markers — each EXACTLY once.
for mk in \
    "BEGIN shift-agent-transport-evidence-probe" "END shift-agent-transport-evidence-probe" \
    "BEGIN shift-agent-transport-evidence-startup" "END shift-agent-transport-evidence-startup" \
    "BEGIN shift-agent-transport-evidence-shutdown" "END shift-agent-transport-evidence-shutdown" \
    "BEGIN shift-agent-transport-evidence-diag" "END shift-agent-transport-evidence-diag"; do
    c=$(grep -cF "$mk" "$RUN" || true)
    [ "$c" = "1" ] || fail "$RUN transport-evidence marker '$mk' count=$c (expected exactly 1)"
done

# whatsapp.py markers — each EXACTLY once.
for mk in \
    "BEGIN shift-agent-transport-evidence-probe" "END shift-agent-transport-evidence-probe" \
    "BEGIN shift-agent-transport-evidence-provider-entry" "END shift-agent-transport-evidence-provider-entry"; do
    c=$(grep -cF "$mk" "$WA" || true)
    [ "$c" = "1" ] || fail "$WA transport-evidence marker '$mk' count=$c (expected exactly 1)"
done

# Hook + anchor presence (SEAMS 1/5/6/7): startup, shutdown, diagnostic dispatch
# into _handle_message_with_agent, and the provider-boundary observer immediately
# before the bridge POST /send.
grep -q "async def _handle_message_with_agent(" "$RUN" || fail "$RUN missing _handle_message_with_agent anchor (diagnostic dispatch hook)"
grep -q "_shift_te_on_startup(self)" "$RUN" || fail "$RUN missing transport-evidence startup hook call"
grep -q "_shift_te_on_shutdown(self)" "$RUN" || fail "$RUN missing transport-evidence shutdown hook call"
grep -q "_shift_te_run_diagnostic(self, source" "$RUN" || fail "$RUN missing transport-evidence diagnostic dispatch call"
grep -q "_shift_te_provider_entry_observer(chat_id)" "$WA" || fail "$WA missing transport-evidence provider-entry observer call"
grep -q "{self._bridge_port}/send" "$WA" || fail "$WA missing bridge POST /send provider boundary"

# DEFAULT-OFF closure 1: NO import-time arming symbol.
if grep -q "_shift_te_maybe_start" "$RUN"; then
    fail "$RUN carries import-time transport-evidence arming — must be default-OFF via the async start() hook only"
fi
# DEFAULT-OFF closure 2 (structural): the injected MODULE block must contain NO
# module-scope (column-0) executable statement/call — only import/from/def/async
# def/class/decorator/comment/blank. A top-level call would run at gateway import.
TE_BLOCK=$(sed -n '/^# BEGIN shift-agent-transport-evidence-probe$/,/^# END shift-agent-transport-evidence-probe$/p' "$RUN")
[ -n "$TE_BLOCK" ] || fail "$RUN transport-evidence module block not found between BEGIN/END markers"
TE_BAD=$(printf '%s\n' "$TE_BLOCK" | grep -nE '^[^[:space:]#]' | grep -vE ':(import |from |def |async def |class |@)' || true)
if [ -n "$TE_BAD" ]; then
    fail "$RUN transport-evidence module block has a module-scope statement/call (not default-OFF): $TE_BAD"
fi

# Version-skew closure: the wired shim lazily imports the flat harness modules;
# assert they are installed so an armed shim can never hit an ImportError.
for m in transport_evidence.py transport_evidence_ledger.py \
         transport_evidence_diagnostic.py transport_evidence_lease.py; do
    [ -f "$PLATFORM/$m" ] || fail "$PLATFORM/$m missing while the transport-evidence patch is installed (armed shim would ImportError)"
done
grep -q "def run_diagnostic_async" "$PLATFORM/transport_evidence_diagnostic.py" 2>/dev/null \
    || fail "$PLATFORM/transport_evidence_diagnostic.py missing run_diagnostic_async (diagnostic path absent)"

echo "OK: transport-evidence harness markers + hooks verified (default-OFF)."
