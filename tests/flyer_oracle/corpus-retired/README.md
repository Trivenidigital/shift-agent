# Retired oracle corpus — planner-dependent fixtures

Retired with creative_planner (graduation commit 6, 2026-07-04): these
fixtures enabled the planner and pinned inferred-item states that are now
UNREACHABLE (the producer is removed; hermes_inferred has no sanctioned
emitter). Kept as historical artifacts, excluded from oracle runs.

F0144's provenance-lifecycle expectation (hermes_inferred -> customer_confirmed
on approval) remains valid in principle via the relocated
facts.promote_inferred_to_confirmed, but no producer can create the input
state — re-baseline against a future producer if one ever ships.
