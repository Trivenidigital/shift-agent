#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# 59 -> 1 legacy backlog release — STAGED 2026-07-06, EXECUTES ONLY ON
# EXPLICIT OPERATOR APPROVAL (line-item; window expiry does NOT authorize).
#
# Closes the 58 stale non-terminal Flyer Studio projects (census
# 2026-07-06T03:1xZ, all customer_phone=+17329837841) via the guarded
# flyer-manual-queue --close CLI, silent (--no-notify: audit row kept,
# customer notice deliberately skipped per the C2 census E-verdict ruling).
# F0214 (exhibit 2, operator HOLD) is explicitly excluded and guarded.
#
# Run ON the box as root:            bash /root/legacy-release-20260706.sh
# Or from a checkout:                ssh root@46.62.206.192 'bash -s' < tools/legacy-release-20260706.sh
# Canary-only staged execution:      add --canary-only (closes F0139, then stops)
#
# PREREQUISITE: the intake-abandonment close edge (intake_started ->
# closed_no_send, this PR) must be DEPLOYED before running — F0184 sits at
# intake_started. The pre-flight probe below fail-fasts against the box's
# installed schemas BEFORE any close if the deploy hasn't landed.
#
# Properties:
#   - idempotent: already-closed rows are skipped (safe to re-run after abort)
#   - ordered: canary F0139 -> 38 legacy -> 14 SLA-stuck -> 3 reference ->
#     F0197 (close-with-preserve note) -> F0184 (intake, last)
#   - per-close verification: store status readback must be closed_no_send
#   - abort-on-first-failure (set -e + explicit readback aborts)
#   - final assertion: non-terminal == 1 and it is exactly F0214
#   - all mutations run as the service user (sudo -u shift-agent) — root-run
#     rewrites poison store ownership (2026-07-03 canary-bounce incident)
#
# Zero-emission proof for the silent close path: store-isolated dry-run
# 2026-07-06T03:18Z (F0139 on a store COPY, safe_io.bridge_post tripwire,
# 0 calls; notify_customer_of_closure is structurally unreachable under
# --no-notify). Evidence: /tmp/legacy-release-dryrun-20260706-031803.
#
# NOTE on the final assertion: if a NEW organic project arrives mid-run the
# count will be 2+; the diagnostic lists the extras so the operator can
# distinguish "organic newcomer" (fine, release still complete) from a
# genuinely unclosed row.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CLI=/usr/local/bin/flyer-manual-queue
STATE=/opt/shift-agent/state/flyer/projects.json
RUN_LOG=/root/legacy-release-20260706-run-$(date -u +%Y%m%d-%H%M%S).log
HOLD_ID=F0214
CANARY_ONLY=0
[ "${1:-}" = "--canary-only" ] && CANARY_ONLY=1

# ── Buckets (census 2026-07-06, tranche-2 audit reconciled) ─────────────────
CANARY=(F0139)

# 38 legacy awaiting_final_approval (pre-v2 era, mids=0, un-approvable).
LEGACY=(
  F0143 F0144 F0145 F0146 F0147 F0148 F0149 F0150 F0151 F0152
  F0153 F0154 F0155 F0156 F0158 F0166 F0167 F0169 F0170 F0173
  F0175 F0177 F0178 F0180 F0181 F0182 F0185 F0186 F0187 F0188
  F0189 F0190 F0191 F0192 F0193 F0194 F0195 F0196
)

# 14 SLA-stuck manual_edit_required (visual_qa_failed cohort = the
# flyer_source_edit_sla_alert spam source; closing drains the stuck set).
SLA_STUCK=(
  F0157 F0159 F0160 F0161 F0162 F0164 F0165 F0168 F0171 F0172
  F0174 F0176 F0179 F0183
)

# 3 reference-intake manual_edit_required (reference_low_confidence).
REFERENCE=(F0140 F0141 F0142)

# F0197: closes WITH a preserve-as-exhibit note (first premium live entry;
# QA contract-mismatch evidence — artifacts must not be deleted).
EXHIBIT=(F0197)

# F0184: intake_started, abandoned 2026-06-20. Needs the intake edge deploy.
INTAKE=(F0184)

REASON_LEGACY="operator_request: legacy backlog release 2026-07-06 (pre-v2-era awaiting_final_approval, no recorded preview mids, un-approvable)"
REASON_SLA="operator_request: SLA-stuck manual backlog release 2026-07-06 (stale visual_qa_failed cohort superseded by v2 stack; drains SLA alert spam source)"
REASON_REFERENCE="operator_request: reference-intake backlog release 2026-07-06 (stale reference_low_confidence rows)"
REASON_EXHIBIT="operator_request: backlog release 2026-07-06 - PRESERVE ARTIFACTS AS EXHIBIT (first premium live entry, verifier contract-mismatch evidence; do not delete renders or sidecars)"
REASON_INTAKE="operator_request: intake abandoned 2026-06-20, released 2026-07-06"

say() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$RUN_LOG"; }

status_of() {
  python3 - "$1" "$STATE" <<'PY'
import json, sys
doc = json.load(open(sys.argv[2], encoding="utf-8"))
projects = doc["projects"] if isinstance(doc, dict) else doc
for p in projects:
    if p["project_id"] == sys.argv[1]:
        print(p["status"])
        break
else:
    print("NOT_FOUND")
PY
}

close_one() {
  local id="$1" reason="$2"
  local st
  st=$(status_of "$id")
  case "$st" in
    closed_no_send)
      say "SKIP  $id already closed_no_send (idempotent re-run)"
      return 0 ;;
    NOT_FOUND)
      say "ABORT $id not found in store"
      exit 1 ;;
  esac
  say "CLOSE $id (from $st)"
  sudo -u shift-agent "$CLI" --close "$id" --reason "$reason" --no-notify --force >>"$RUN_LOG" 2>&1
  st=$(status_of "$id")
  if [ "$st" != "closed_no_send" ]; then
    say "ABORT readback for $id returned '$st' (expected closed_no_send)"
    exit 1
  fi
  say "OK    $id closed_no_send"
}

# ── Pre-flight ───────────────────────────────────────────────────────────────
say "pre-flight: legacy release 20260706 starting (canary_only=$CANARY_ONLY)"
[ -x "$CLI" ]   || { say "ABORT: $CLI missing/not executable"; exit 1; }
[ -f "$STATE" ] || { say "ABORT: $STATE missing"; exit 1; }

# HOLD guard: refuse to run if F0214 appears in ANY close bucket.
ALL_IDS=("${CANARY[@]}" "${LEGACY[@]}" "${SLA_STUCK[@]}" "${REFERENCE[@]}" "${EXHIBIT[@]}" "${INTAKE[@]}")
for id in "${ALL_IDS[@]}"; do
  if [ "$id" = "$HOLD_ID" ]; then
    say "ABORT: HOLD project $HOLD_ID found in a close bucket — refusing to run"
    exit 1
  fi
done
if [ "${#ALL_IDS[@]}" -ne 58 ]; then
  say "ABORT: expected 58 close targets, got ${#ALL_IDS[@]}"
  exit 1
fi
DUPES=$(printf '%s\n' "${ALL_IDS[@]}" | sort | uniq -d)
if [ -n "$DUPES" ]; then
  say "ABORT: duplicate ids in buckets: $DUPES"
  exit 1
fi

# Intake-edge deploy probe: fail-fast BEFORE any close if the box's installed
# schemas predate the intake_started -> closed_no_send edge (F0184 would
# strand the run at the very end otherwise).
if ! python3 -c "import sys; sys.path.insert(0, '/opt/shift-agent'); from schemas import is_flyer_transition_allowed; sys.exit(0 if is_flyer_transition_allowed('intake_started', 'closed_no_send') else 1)"; then
  say "ABORT: deployed schemas lack the intake_started->closed_no_send edge — deploy this PR first"
  exit 1
fi
say "pre-flight OK: CLI present, 58 unique targets, $HOLD_ID excluded, intake edge deployed"

# ── Ordered execution ────────────────────────────────────────────────────────
say "── bucket 1/6: canary ──"
for id in "${CANARY[@]}";    do close_one "$id" "$REASON_LEGACY"; done
say "canary audit row (live log):"
grep '"type": "flyer_closure_customer_notified"' /opt/shift-agent/logs/decisions.log | grep '"F0139"' | tail -1 | tee -a "$RUN_LOG" || true

if [ "$CANARY_ONLY" = "1" ]; then
  say "canary-only mode: stopping after F0139. Re-run without --canary-only to finish."
  exit 0
fi

say "── bucket 2/6: 38 legacy awaiting ──"
for id in "${LEGACY[@]}";    do close_one "$id" "$REASON_LEGACY"; done
say "── bucket 3/6: 14 SLA-stuck manual ──"
for id in "${SLA_STUCK[@]}"; do close_one "$id" "$REASON_SLA"; done
say "── bucket 4/6: 3 reference manual ──"
for id in "${REFERENCE[@]}"; do close_one "$id" "$REASON_REFERENCE"; done
say "── bucket 5/6: F0197 close-with-preserve ──"
for id in "${EXHIBIT[@]}";   do close_one "$id" "$REASON_EXHIBIT"; done
say "── bucket 6/6: F0184 intake-abandoned (last) ──"
for id in "${INTAKE[@]}";    do close_one "$id" "$REASON_INTAKE"; done

# ── Final assertion: non-terminal == 1 and it is exactly F0214 ──────────────
say "final assertion:"
python3 - "$STATE" "$HOLD_ID" <<'PY' 2>&1 | tee -a "$RUN_LOG"
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
projects = doc["projects"] if isinstance(doc, dict) else doc
TERMINAL = {"delivered", "completed", "closed_no_send"}
nonterm = sorted(p["project_id"] for p in projects if p["status"] not in TERMINAL)
print("non_terminal:", nonterm)
if nonterm == [sys.argv[2]]:
    print("FINAL ASSERTION PASSED: non-terminal == 1 ==", sys.argv[2])
    sys.exit(0)
extras = [pid for pid in nonterm if pid != sys.argv[2]]
print("FINAL ASSERTION FAILED: extras =", extras,
      "(organic newcomers are fine; anything from the 58-target list is NOT)")
sys.exit(1)
PY
say "legacy release 20260706 COMPLETE — run log: $RUN_LOG"
