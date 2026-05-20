# Follow-up: align autonomous report `--out` writes with Hermes `safe_io` pattern

**Drift-check tag:** extends-Hermes

**Status:** open / not started

**Origin:** Re-review of PR #139 (merge commit `308af93`, 2026-05-20). The HIGH/MEDIUM reviewer findings closed cleanly in `030c111`, but the `--out` write paths in both new tools still use plain `open(path, "w").write(...)` rather than the deployed `safe_io.atomic_write_text` pattern (CLAUDE.md Drift Rules Part 1: "JSON-on-disk + `safe_io.atomic_write_json` + `fcntl.flock`"). This is a deployed-pattern adherence gap, not a correctness or safety issue — PR #139 is report-only and the `--out` consumers are short-lived single writers, so no torn-write risk exists today. Recorded as a small hygiene cleanup, not a hotfix.

## Scope

- `tools/flyer-autonomous-train.py` — `write_or_print` currently uses `open(path, "w", encoding="utf-8", newline="\n")`. Replace with `safe_io.atomic_write_text(path, output)` (or the equivalent existing helper).
- `tools/hermes-fleet-upgrade.py` — same `write_or_print` pattern; same replacement.
- Both tools need the `sys.path` shim that `tests/conftest.py` already does, so `from safe_io import atomic_write_text` resolves when the tool is invoked directly via CLI rather than under pytest.

## Acceptance criteria

- Replace plain `open(path, "w").write(...)` writes in both tools with `safe_io.atomic_write_text` (or an equivalent existing helper).
- Add focused tests that:
  - `--out` creates missing parent directories.
  - `--out` writes through the atomic helper / no stray `.tmp-*` sibling files left behind in the parent dir.
  - Repeated invocations with the same `--out` path produce a complete, valid file each time (no torn-write window observable under normal scheduling).
- Do NOT change behaviour observable to consumers — output bytes and CLI exit codes stay identical.

## Constraints

- No deploy required. PR #139 was report-only and is already merged; this is local-tooling hygiene.
- Do not expand scope to the cooldown-state write path — that path is intentionally external to these tools per #139's design (eligibility reads, never writes).
- Do not add Pydantic schemas for PR-metadata in this PR (separate scope-cut deferred to the future-runner PR).
- Keep the static guard test (`test_static_guard_no_live_network_or_mutation_paths`) green — `safe_io` imports must not introduce banned tokens into the tool's own source text.

## Verification (suggested)

- `python -m pytest tests/test_flyer_autonomous_train.py tests/test_hermes_fleet_upgrade.py tests/test_operator_brief.py -q`
- `python -m py_compile tools/flyer-autonomous-train.py tools/hermes-fleet-upgrade.py tools/operator-brief.py`
- `git diff --check`
- CLI smoke: confirm `--out` paths still produce expected output for `eligibility`, `report`, `next-candidate`, `normalization-report`.

## Out of scope (record-but-defer)

- Pydantic schemas in `src/platform/schemas.py` for the persisted-state contract — deferred to the future-runner PR alongside the live `record-run`-style writer.
- Re-aligning the offline normalization path with the live `check` data path inside `tools/hermes-fleet-upgrade.py` — separate refactor; ticketed elsewhere if pursued.
