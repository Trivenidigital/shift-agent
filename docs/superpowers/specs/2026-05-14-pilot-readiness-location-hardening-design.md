# Pilot Readiness Location Hardening Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. This design strengthens the existing `pilot-readiness-check` production gate.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Production pilot readiness | yes - deployed `pilot-readiness-check` | extend existing gate |
| Config schema | yes - `Config.customer.location_id` | reuse validated config |
| Roster schema | yes - `Roster.location` exists as tolerated dict | reuse, inspect location metadata after schema validation |
| Deployment | yes - existing tarball deploy installs shift scripts | no deploy-script change |
| Tests | yes - existing subprocess tests | extend existing test file |

Live VPS check: the gate already exists on `main-vps`; the production issue was a hidden stale/test roster label that the existing gate did not catch.

Hermes ecosystem and Awesome-Hermes-Agent check: official Hermes skills and community tooling do not provide a project-specific coherence validator for `config.yaml` and `roster.json`. Verdict: local readiness hardening is genuine net-new logic on top of existing Hermes substrate.

## Problem

The production pilot gate previously reported READY after placeholder `customer.name` and `customer.location_id` were patched, but a stale roster label still said `Triveni Jacksonville (TEST)` with a mismatched historical location id. The gate only validated that each file was individually shaped and non-placeholder; it did not validate cross-file coherence.

This can produce a dangerous operator state: the customer VPS appears ready while Shift, Catering, and Daily Brief are grounded in inconsistent customer/location identity.

## Scope

In scope:

- Require `roster.location.id` to match `config.customer.location_id`.
- Require meaningful tokens from `roster.location.id` to appear in `roster.location.name` when such tokens exist, so `loc_pineville_01` cannot pair with `Triveni Jacksonville`.
- Reject `roster.location.id` or `roster.location.name` containing test/placeholder labels.
- Keep JSON and `--text` output formats.
- Add regression tests for mismatch, test labels, invalid-config semantics, and text output.

Out of scope:

- Schema migration for `Roster.location`.
- Multi-location semantics.
- Live WhatsApp smoke.
- Runtime YAML edits.
- New audit log entries.
- New deploy scripts or systemd units.

## Behavior contract

### Config valid, roster valid, location matches

The report includes:

```json
{"id":"roster.location_id_match","status":"pass","message":"roster.location.id matches customer.location_id"}
{"id":"roster.location_label","status":"pass","message":"roster.location label is production"}
```

Readiness may be READY if all other checks pass.

### Config valid, roster location id differs

The report includes:

```json
{"id":"roster.location_id_match","status":"fail","message":"roster.location.id does not match customer.location_id"}
```

Readiness is BLOCKED.

### Config invalid or missing

The report includes the existing config failure and:

```json
{"id":"roster.location_id_match","status":"fail","message":"roster.location.id not compared because config invalid"}
```

This avoids a misleading pass row. Readiness is BLOCKED.

### Roster location contains a test/placeholder label

If either `roster.location.id` or `roster.location.name` is empty, null, or contains a boundary-delimited placeholder token (`placeholder`, `todo`, `test`, `dummy`, `sample`, or `rehearsal`), the report includes:

```json
{"id":"roster.location_label","status":"fail","message":"roster.location contains test/placeholder label"}
```

Readiness is BLOCKED.

### Roster location id matches config but name is stale

If `roster.location.id` is `loc_pineville_01` but `roster.location.name` does not contain the meaningful token `pineville`, the report includes:

```json
{"id":"roster.location_name_match","status":"fail","message":"roster.location.name does not match customer.location_id token"}
```

Readiness is BLOCKED.

## Implementation details

Add `import re`, then add to `src/agents/shift/scripts/pilot-readiness-check`:

```python
_PLACEHOLDER_LABEL_RE = re.compile(
    r"(^|[^a-z0-9])(placeholder|todo|test|dummy|sample|rehearsal)([^a-z0-9]|$)",
    re.IGNORECASE,
)


def _location_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _contains_placeholder_label(value: Any) -> bool:
    normalized = _location_value(value).lower()
    if normalized == "":
        return True
    return _PLACEHOLDER_LABEL_RE.search(normalized) is not None
```

Change `_check_roster` to accept `cfg: Config | None`.

After `roster.schema` passes, derive:

```python
roster_location = roster.location if isinstance(roster.location, dict) else {}
roster_location_id_raw = roster_location.get("id")
roster_location_name_raw = roster_location.get("name")
roster_location_id = _location_value(roster_location_id_raw)
```

Then append the two location checks with the behavior contract above.

Also derive meaningful alpha tokens from the roster location id, excluding generic tokens such as `loc`, `location`, `store`, `site`, and `branch`. If any token with length at least four exists, at least one must appear in the lowercased roster location name.

In `build_report`, save the config result and pass it to roster checking:

```python
cfg = _check_config(config_path, checks)
_check_roster(roster_path, checks, cfg)
```

## Test design

Extend `tests/test_pilot_readiness_check.py`.

New tests:

- `test_roster_location_id_must_match_config_customer_location_id`
- `test_roster_location_name_must_not_contain_test_label`
- `test_roster_location_id_must_not_contain_test_label_even_when_it_matches_config`
- `test_roster_location_name_must_match_meaningful_location_id_token`
- `test_roster_location_null_and_non_string_metadata_blocks_without_traceback`
- `test_roster_location_match_not_reported_pass_when_config_invalid`
- `test_text_output_includes_roster_location_failures`

Existing tests continue to cover:

- Ready fixture.
- Placeholder config fields.
- Missing roster.
- Missing menu.
- Disabled Daily Brief.
- Deploy/smoke install behavior.

## Runtime-state verification

Before or immediately after deploy, verify on `main-vps`:

| Field | Expected |
|---|---|
| `config.customer.location_id` | `loc_pineville_01` |
| `roster.location.id` | `loc_pineville_01` |
| `roster.location.name` | `Triveni Pineville` |
| `pilot-readiness-check --text` | `READY` |

Use the Windows SSH two-step redirect/read pattern.

In addition to the readiness command, dump the concrete runtime tuple from `/opt/shift-agent/config.yaml` and `/opt/shift-agent/roster.json`. A READY result proves coherence; the tuple proves it is the intended Pineville customer/location, not a coherent stale store.

## Failure handling

If the post-deploy gate fails, do not loosen the checker. Treat failure as real runtime-state drift and report the stale field. The checker's purpose is to block customer onboarding when state is inconsistent.

## Risk analysis

- False positive risk: placeholder detection uses boundary-delimited tokens, so `Triveni Jacksonville (TEST)` and `loc_jacksonville_test` fail while words containing those letters as part of a larger token do not fail solely because of that substring.
- Backward compatibility: adding check rows changes summary totals but preserves the JSON schema and text format.
- Schema tolerance: `Roster.location` remains a dict, so the checker must handle missing/null values explicitly.

## Acceptance criteria

- Tests fail before implementation and pass after.
- JSON output blocks mismatch and test labels.
- JSON output blocks a coherent id with a stale real roster location name.
- Text output renders the new failures.
- Invalid config never produces a misleading location-match pass.
- Existing ready fixture remains READY.
- Deploy does not require new files beyond the modified script.
