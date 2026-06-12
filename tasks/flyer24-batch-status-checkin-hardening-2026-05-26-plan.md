# Flyer24 Batch - SOURCE/NEW Status Check-in Hardening (2026-05-26)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist

1. Receive WhatsApp message while SOURCE/NEW clarification is pending -> **[Hermes]** ingress/routing substrate already exists.
2. Normalize visible text from sender-block wrapper -> **[Hermes]** existing cf-router helper `flyer_visible_message_text`.
3. Detect status check-in intent during pending SOURCE/NEW decision -> **[net-new]** Flyer policy regex/heuristic coverage.
4. Re-send SOURCE/NEW clarification without consuming pending row -> **[Hermes]** existing state + bridge send path already exists.
5. Avoid accidental edit/project creation during status check-in -> **[net-new]** Flyer branch guard ordering + phrase recognition.
6. Prove behavior with replay-like unit coverage -> **[net-new]** tests only.

Net-new scope is only steps 3, 5, and 6.

## Batch issues (related)

1. `flyer_is_status_checkin` uses a narrower phrase matcher than current live status heuristics, creating divergence risk.
2. Pending SOURCE/NEW branch does not use the dedicated check-in helper, so helper drift can silently bypass this branch.
3. Polite suffix variants (`any update please`, `status please`, `eta please`) are not normalized consistently in check-in helper.
4. Progress wording variants (`any progress`, `what's the progress`) are not explicitly accepted in check-in helper.
5. Time-estimate wording (`how long`, `when ready`) is inconsistent between helper and broader status-request logic.
6. No explicit tests pinning these variants in both helper-level and SOURCE/NEW pending-branch behavior.

## Implementation plan

- Add failing tests first in `tests/test_cf_router_flyer_routing.py` for phrase variants and SOURCE/NEW pending branch skip behavior.
- Make `flyer_is_status_checkin` reuse `is_flyer_project_status_request` as the single-source intent gate and keep edit-request exclusion fail-closed.
- Switch SOURCE/NEW pending branch in `hooks.py` to call `flyer_is_status_checkin` for the pre-claim check-in branch.
- Re-run focused Flyer routing tests + static checks.

## Verification

- `python3 -m py_compile src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "status_checkin or source_vs_new"`
- `git diff --check`
