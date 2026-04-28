#!/usr/bin/env bash
# Verify shift-agent-sender-id patches are present and correctly anchored
# in the live Hermes install. Exit 1 (fail-closed) on any drift.
#
# Run before any deploy that depends on Phase B sender-id injection.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASELINE_FILE="$SCRIPT_DIR/hermes-patch-baseline.txt"

H=/root/.hermes/hermes-agent
RUN=$H/gateway/run.py
WA=$H/gateway/platforms/whatsapp.py
BR=$H/scripts/whatsapp-bridge/bridge.js

fail() { echo "FAIL: $1" >&2; exit 1; }
warn() { echo "WARN: $1" >&2; }

# 1. Markers present in all three target files.
for f in "$RUN" "$WA" "$BR"; do
  [ -f "$f" ] || fail "missing target file $f"
  grep -q "BEGIN shift-agent-sender-id" "$f" || fail "$f missing BEGIN marker"
  grep -q "END shift-agent-sender-id" "$f" || fail "$f missing END marker"
done

# 2. Anchor proximity in run.py — the INJECT-SITE marker (not the flag-block
#    marker near `import os` at line ~20) must live within ±60 lines of
#    `_prepare_inbound_message_text`. run.py has TWO markers: the flag block
#    and the inject site. We pick the LAST one (tail -1) — that's the inject.
RB=$(grep -n "BEGIN shift-agent-sender-id" "$RUN" | tail -1 | cut -d: -f1)
RA=$(grep -n "_prepare_inbound_message_text" "$RUN" | head -1 | cut -d: -f1)
[ -n "$RB" ] && [ -n "$RA" ] || fail "$RUN missing BEGIN marker or anchor symbol"
DIFF=$(( RB > RA ? RB - RA : RA - RB ))
[ "$DIFF" -le 60 ] || fail "$RUN BEGIN marker drifted from anchor (delta=$DIFF lines)"

# 3. Anchor proximity in whatsapp.py — `_resolve_sender_context` helper
WB=$(grep -n "BEGIN shift-agent-sender-id" "$WA" | head -1 | cut -d: -f1)
WA_=$(grep -n "_build_message_event\|_resolve_sender_context" "$WA" | head -1 | cut -d: -f1)
[ -n "$WB" ] && [ -n "$WA_" ] || fail "$WA missing BEGIN marker or anchor symbol"
DIFF2=$(( WB > WA_ ? WB - WA_ : WA_ - WB ))
[ "$DIFF2" -le 50 ] || fail "$WA BEGIN marker drifted from anchor (delta=$DIFF2 lines)"

# 4. Anchor proximity in bridge.js — `messageQueue.push` is the inject site
BB=$(grep -n "BEGIN shift-agent-sender-id" "$BR" | head -1 | cut -d: -f1)
BA=$(grep -n "messageQueue.push" "$BR" | head -1 | cut -d: -f1)
[ -n "$BB" ] && [ -n "$BA" ] || fail "$BR missing BEGIN marker or anchor symbol"
# Bridge has multiple injects (helpers + ingest); allow larger window
DIFF3=$(( BB > BA ? BB - BA : BA - BB ))
[ "$DIFF3" -le 200 ] || fail "$BR BEGIN marker drifted from anchor (delta=$DIFF3 lines)"

# 5. Hermes version drift (warn only — operator must verify semantics).
if [ -r "$BASELINE_FILE" ]; then
  EXPECTED=$(cat "$BASELINE_FILE")
  CURRENT=$(/root/.hermes/hermes-agent/venv/bin/python -c \
    "import hermes_agent; print(hermes_agent.__version__)" 2>/dev/null || echo "unknown")
  if [ "$EXPECTED" != "$CURRENT" ]; then
    warn "Hermes version drift expected=$EXPECTED current=$CURRENT — re-validate semantically"
  fi
fi

echo "OK: shift-agent-sender-id patches verified."
