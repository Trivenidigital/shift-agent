# Pilot Readiness Location Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pilot-readiness-check` fail when `config.customer.location_id` disagrees with `roster.location.id` or when the roster location still contains test/placeholder labels.

**Architecture:** Keep the existing readiness CLI and JSON/text output. Thread the validated `Config` object into `_check_roster`, validate the roster's location metadata after schema validation, and add focused pytest coverage for the two hidden-placeholder classes found during the production pilot.

**Tech Stack:** Python, Pydantic v2 schemas from `src/platform/schemas.py`, pytest subprocess tests, existing Hermes tarball deploy.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. This hardens an existing readiness gate.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Production readiness gate | yes - deployed `pilot-readiness-check` | extend it rather than creating a new checker |
| Customer/runtime config | yes - `Config` schema already validates `customer.location_id` | reuse validated config object |
| Roster state | yes - `Roster` schema already validates roster shape | reuse validated roster object and inspect existing `location` dict |
| Deployment | yes - existing deploy script installs `src/agents/shift/scripts/*` | no deploy-script change |
| Testing | yes - `tests/test_pilot_readiness_check.py` already subprocess-invokes the CLI | extend existing test file |

Live VPS capability check: `main-vps` already has the project `pilot-readiness-check`; this work hardens that deployed gate rather than adding a parallel health tool.

Hermes ecosystem check: reviewed the official Hermes Skills System, bundled skills catalog, and Awesome-Hermes-Agent. They provide skills/orchestration substrate, but no customer-state readiness validator for project-specific `config.yaml` and `roster.json` coherence. Verdict: extend the local readiness gate.

## Drift grounding

Read before plan:

- `src/agents/shift/scripts/pilot-readiness-check` - existing config, roster, menu checks and output contract.
- `tests/test_pilot_readiness_check.py` - subprocess-based test pattern and base fixtures.
- `src/platform/schemas.py` - `Config.customer.location_id` and `Roster.location` shape.
- `tasks/todo.md` - active pilot follow-up explicitly names the hidden Jacksonville test-label gap.

Deployed-pattern compliance:

- No new state file.
- No schema migration.
- No audit-log variant.
- No systemd unit.
- No deploy-script install changes.
- Existing JSON report remains backward-compatible except for additional check rows.

## File structure

Modify:

- `src/agents/shift/scripts/pilot-readiness-check`
  - Add placeholder-token helper for broader text checks.
  - Change `_check_roster(path, checks)` to `_check_roster(path, checks, cfg)`.
  - Fail `roster.location_id_match` when `cfg.customer.location_id != roster.location["id"]`.
  - Fail `roster.location_name_match` when a meaningful location-id token such as `pineville` is absent from `roster.location.name`.
  - Fail `roster.location_label` when `roster.location.name` or `roster.location.id` contains placeholder/test labels.

- `tests/test_pilot_readiness_check.py`
  - Add one regression test for config/roster location-id mismatch.
  - Add one regression test for roster location name containing a test label.

- `tasks/todo.md`
  - Mark the readiness-hardening item complete after verification.

## Task 1: Add Failing Tests

**Files:**

- Modify: `tests/test_pilot_readiness_check.py`

- [x] **Step 1: Add config/roster mismatch test**

Add this test after `test_placeholder_customer_blocks_production`:

```python
def test_roster_location_id_must_match_config_customer_location_id(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = "loc_jacksonville_test"
    roster["location"]["name"] = "Triveni Pineville"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location.id does not match customer.location_id" in messages
```

- [x] **Step 2: Add roster test-label test**

Add this test after the mismatch test:

```python
def test_roster_location_name_must_not_contain_test_label(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["name"] = "Triveni Jacksonville (TEST)"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location contains test/placeholder label" in messages
```

- [x] **Step 3: Add roster location-id test-label test**

Add this test after the name-label test:

```python
def test_roster_location_id_must_not_contain_test_label_even_when_it_matches_config(tmp_path: Path):
    cfg = _base_config()
    cfg["customer"]["location_id"] = "loc_jacksonville_test"
    roster = _base_roster()
    roster["location"]["id"] = "loc_jacksonville_test"
    roster["location"]["name"] = "Triveni Pineville"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=cfg, roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location contains test/placeholder label" in messages
```

- [x] **Step 4: Add null/non-string location metadata test**

Add this test after the id-label test:

```python
def test_roster_location_null_and_non_string_metadata_blocks_without_traceback(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = 123
    roster["location"]["name"] = None
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    assert result.stderr == ""
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location.id does not match customer.location_id" in messages
    assert "roster.location contains test/placeholder label" in messages
```

- [x] **Step 5: Add stale real roster name test**

Add this test after the null/non-string test:

```python
def test_roster_location_name_must_match_meaningful_location_id_token(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = "loc_pineville_01"
    roster["location"]["name"] = "Triveni Jacksonville"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location.name does not match customer.location_id token" in messages
```

- [x] **Step 6: Add config-invalid no-false-pass test**

Add this test after the test-label test:

```python
def test_roster_location_match_not_reported_pass_when_config_invalid(tmp_path: Path):
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config={"schema_version": 1}, roster=_base_roster(), menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    checks = _report(result)["checks"]
    assert any(c["id"] == "config.schema" and c["status"] == "fail" for c in checks)
    assert any(
        c["id"] == "roster.location_id_match"
        and c["status"] == "fail"
        and c["message"] == "roster.location.id not compared because config invalid"
        for c in checks
    )
```

- [x] **Step 7: Add text-mode output regression**

Add this helper after `_run`:

```python
def _run_text(config_path: Path, roster_path: Path, state_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config_path),
            "--roster",
            str(roster_path),
            "--state-dir",
            str(state_dir),
            "--text",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
```

Then add this test after the config-invalid test:

```python
def test_text_output_includes_roster_location_failures(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = "loc_jacksonville_test"
    roster["location"]["name"] = "Triveni Jacksonville (TEST)"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run_text(config_path, roster_path, state_dir)

    assert result.returncode == 1
    assert result.stderr == ""
    assert "FAIL roster.location_id_match: roster.location.id does not match customer.location_id" in result.stdout
    assert "FAIL roster.location_label: roster.location contains test/placeholder label" in result.stdout
```

- [x] **Step 8: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_pilot_readiness_check.py -q
```

Expected result:

- The new tests fail because the current CLI returns ready for hidden drift cases and lacks the new check rows.
- Existing tests still exercise the script through subprocess.

## Task 2: Implement Readiness Hardening

**Files:**

- Modify: `src/agents/shift/scripts/pilot-readiness-check`

- [x] **Step 1: Add imports and placeholder-label helpers**

Add `import re` near the top imports.

Add after `_is_placeholder`:

```python
_PLACEHOLDER_LABEL_RE = re.compile(
    r"(^|[^a-z0-9])(placeholder|todo|test|dummy|sample|rehearsal)([^a-z0-9]|$)",
    re.IGNORECASE,
)
_LOCATION_ID_SKIP_TOKENS = {"loc", "location", "store", "site", "branch"}


def _location_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _contains_placeholder_label(value: Any) -> bool:
    normalized = _location_value(value).lower()
    if normalized == "":
        return True
    return _PLACEHOLDER_LABEL_RE.search(normalized) is not None


def _meaningful_location_id_tokens(value: Any) -> list[str]:
    normalized = _location_value(value).lower()
    tokens = re.findall(r"[a-z]+", normalized)
    return [token for token in tokens if len(token) >= 4 and token not in _LOCATION_ID_SKIP_TOKENS]
```

- [x] **Step 2: Change roster checker signature**

Change:

```python
def _check_roster(path: Path, checks: Checks) -> None:
```

to:

```python
def _check_roster(path: Path, checks: Checks, cfg: Config | None) -> None:
```

- [x] **Step 3: Add roster location checks after roster schema passes**

Insert immediately after:

```python
    checks.pass_("roster.schema", "roster.json validates")
```

the following block:

```python
    roster_location = roster.location if isinstance(roster.location, dict) else {}
    roster_location_id_raw = roster_location.get("id")
    roster_location_name_raw = roster_location.get("name")
    roster_location_id = _location_value(roster_location_id_raw)
    roster_location_name = _location_value(roster_location_name_raw)

    if cfg is None:
        checks.fail("roster.location_id_match", "roster.location.id not compared because config invalid")
    elif roster_location_id != cfg.customer.location_id:
        checks.fail("roster.location_id_match", "roster.location.id does not match customer.location_id")
    else:
        checks.pass_("roster.location_id_match", "roster.location.id matches customer.location_id")

    id_tokens = _meaningful_location_id_tokens(roster_location_id)
    roster_name_normalized = roster_location_name.lower()
    if id_tokens and not any(token in roster_name_normalized for token in id_tokens):
        checks.fail("roster.location_name_match", "roster.location.name does not match customer.location_id token")
    elif id_tokens:
        checks.pass_("roster.location_name_match", "roster.location.name matches customer.location_id token")

    if (
        _contains_placeholder_label(roster_location_id_raw)
        or _contains_placeholder_label(roster_location_name_raw)
    ):
        checks.fail("roster.location_label", "roster.location contains test/placeholder label")
    else:
        checks.pass_("roster.location_label", "roster.location label is production")
```

- [x] **Step 4: Thread config through `build_report`**

Change:

```python
    _check_config(config_path, checks)
    _check_roster(roster_path, checks)
```

to:

```python
    cfg = _check_config(config_path, checks)
    _check_roster(roster_path, checks, cfg)
```

- [x] **Step 5: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_pilot_readiness_check.py -q
```

Expected result:

- All readiness tests pass.
- The ready fixture now reports two extra passing rows and still returns `status == "ready"`.

## Task 3: Update Backlog Evidence

**Files:**

- Modify: `tasks/todo.md`

- [x] **Step 1: Mark the active operating-layer readiness item complete**

Change:

```markdown
- [ ] Phase 1 readiness hardening: tighten `pilot-readiness-check` so config customer location and roster location must agree, reject test/placeholder roster labels, and block stale real labels when the location-id token is absent from the roster location name.
```

to:

```markdown
- [x] Phase 1 readiness hardening: tighten `pilot-readiness-check` so config customer location and roster location must agree, reject test/placeholder roster labels, and block stale real labels when the location-id token is absent from the roster location name.
```

- [x] **Step 2: Mark the production pilot follow-up complete with evidence**

Change:

```markdown
- [ ] Tighten `pilot-readiness-check` to require `config.customer.location_id == roster.location.id`, reject roster location names containing test/placeholder labels, and require meaningful location-id tokens such as `pineville` to appear in `roster.location.name`; live config had a hidden Jacksonville test label that the placeholder-only gate did not catch.
```

to:

```markdown
- [x] Tighten `pilot-readiness-check` to require `config.customer.location_id == roster.location.id`, reject roster location names containing test/placeholder labels, and require meaningful location-id tokens such as `pineville` to appear in `roster.location.name`; local regression tests cover location-id mismatch, stale real name, id/name test labels, invalid-config comparison semantics, non-string/null location metadata, and text output.
```

## Verification

Run:

```powershell
python -m pytest tests/test_pilot_readiness_check.py -q
python -m py_compile src\agents\shift\scripts\pilot-readiness-check
git diff --check
```

Expected:

- `tests/test_pilot_readiness_check.py` passes.
- `py_compile` succeeds.
- `git diff --check` produces no whitespace errors.

## Deploy plan

After PR review and merge:

1. Build/deploy through existing tarball deploy path for `main-vps`.
2. Use the Windows SSH two-step output pattern for every remote command.
3. Runtime assumptions to verify:

| Assumption | Expected value |
|---|---|
| `config.customer.location_id` | `loc_pineville_01` |
| `roster.location.id` | `loc_pineville_01` |
| `roster.location.name` | `Triveni Pineville` |
| `pilot-readiness-check --text` | `READY` |

4. Run on VPS:

```bash
ssh main-vps '/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/pilot-readiness-check --text' > .ssh_pilot_readiness_after_hardening.txt 2>&1
```

5. Dump the concrete runtime tuple:

```bash
ssh main-vps 'python3 - <<'"'"'PY'"'"'
import json, yaml
cfg = yaml.safe_load(open("/opt/shift-agent/config.yaml"))
roster = json.load(open("/opt/shift-agent/roster.json"))
print(json.dumps({
  "customer_name": cfg.get("customer", {}).get("name"),
  "customer_location_id": cfg.get("customer", {}).get("location_id"),
  "roster_location_id": roster.get("location", {}).get("id"),
  "roster_location_name": roster.get("location", {}).get("name"),
}, sort_keys=True))
PY' > .ssh_pilot_runtime_location_tuple.txt 2>&1
```

6. Read `.ssh_pilot_readiness_after_hardening.txt` and `.ssh_pilot_runtime_location_tuple.txt`.
7. Expected: readiness remains READY and the runtime tuple exactly matches Pineville. If it fails, stop and report the actual stale runtime field instead of patching around the failure.

## Self-review

- Scope is one existing gate; no parallel checker.
- Hermes-first analysis present.
- Drift-check tag present.
- No placeholder sections.
- Tests prove both hidden production failure classes.
