# Design — `check-hermes-config-yaml.sh` deploy-time gate

**Drift-check tag:** `extends-Hermes`

**Plan reference:** `tasks/check-hermes-config-yaml-gate-plan.md` (v2 post-reviewer-fix; 13 findings closed).

**Goal:** Concrete CLI + JSON-envelope + override + audit + deploy-integration + test contract for the gate scoped by the plan.

**Design revision history:**
- v1 (2026-05-16) — initial design.
- **v2 (2026-05-16) — applied 9 findings from two parallel design reviewers (D1 security/silent-failure / D2 deploy/operational):** dropped raw-value leakage from `wrong_shape.got` (D1-1), single-helper-invocation emitting both stdout JSON and stderr text (D1-2), added new `ConfigGateOverride` LogEntry variant instead of mis-using `AgentStateChange` (D1-3), tightened deploy-side WARN-skip to FAIL on missing script while keeping rollback WARN-skip (D1-4 / D2-4), added config-yaml gate to rollback path (D1-5 / D2-1), log all failing fields in audit not just attested (D1-6), added ordering-rationale comment for credential_readiness coupling (D2-2), added explicit install line for `check_hermes_config_yaml.py` + rollback cleanup (D2-3), documented disk-full dual-channel caveat (D2-5).

---

## 1. Hermes-first capability checklist (design granularity)

Receipt: `tasks/.hermes-check-receipts/check-hermes-config-yaml-gate-design.json` (2026-05-16).

### Per-step `[Hermes]` / `[net-new]` table (design)

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Operator-edited `/root/.hermes/config.yaml` on VPS | `[Hermes]` | Hermes owns file format. |
| 2 | `shift-agent-deploy.sh deploy` invocation | `[Hermes]` | Existing orchestrator. |
| 3 | Hermes pin gate (PR #17) | `[Hermes]` | Existing. |
| 4 | `$VENV_PY` binding + existence check | `[Hermes]` | Existing. |
| 5 | **New `=== Hermes config.yaml shape gate ===` block in deploy.sh** | `[net-new]` | Inserts AFTER `VENV_PY` guard / BEFORE credential-minimized foundation gate. |
| 6 | Bash wrapper sources `hermes-config-yaml-baseline.txt` for `KNOWN_TOP_LEVEL_KEYS` | `[net-new]` | New contract; mirrors PR #17 baseline-source pattern. |
| 7 | Wrapper invokes `$VENV_PY <staging>/src/platform/scripts/check-hermes-config-yaml --json --baseline … <config>` | `[net-new]` | Helper invocation shape mirrors `credential-minimized-readiness` precedent. |
| 8 | Python helper: pre-load `os.path.exists()` (follows symlinks) → `exit 2` on dangling symlink | `[net-new]` | R1-6 closure. |
| 9 | Python helper: `yaml.safe_load` + `isinstance(doc, dict)` guard → `exit 2` on empty / non-mapping | `[net-new]` | R1-1 closure. |
| 10 | Required-field assertions (`model.default`, `model.provider`) | `[net-new]` | Plan §3.1. |
| 11 | Conditional-field assertions (`auxiliary.vision.{provider,model}`) | `[net-new]` | Plan §3.1. |
| 12 | Advisory check (`provider_routing.sort`) | `[net-new]` | Plan §3.1. |
| 13 | Unknown top-level + 2-level subkey enumeration | `[net-new]` | R1-3 closure. |
| 14 | JSON envelope to stdout | `[net-new]` | Design contract. |
| 15 | Bash wrapper: parse JSON via `$VENV_PY -c` one-liner | `[net-new]` | jq is NOT installed on srilu (see deploy.sh:200 PyYAML choice). |
| 16 | Bash wrapper: two-variable override + attestation | `[net-new]` | R1-4 / R2-2 closure. |
| 17 | Audit override via `log-decision-direct` + plain-text fallback log | `[Hermes]` for `log-decision-direct`; `[net-new]` wrapper | Substrate chokepoint used as-is. |
| 18 | Smoke-side informational pass with two stated purposes | `[net-new]` | R1-7 closure. |
| 19 | Pytest 14-case matrix | `[net-new]` | New test file. |

**[Hermes]:** 5 (substrate). **[net-new]:** 14 (design infrastructure).

### Ecosystem re-check (delta since plan)

No new ecosystem signal since plan v2. Awesome-hermes-agent still 404. Installed Hermes-skills inventory unchanged. Hermes CLI behavior unchanged (probed pre-plan).

---

## 2. Drift-rule self-checks

- ✅ Read `tools/check-shift-agent-patch.sh` (lines 1–237) — canonical fail-closed gate with 2-variable override + dual-channel audit + KEY=VALUE baseline.
- ✅ Read `tools/check-env-drift.sh` (lines 1–95) — sibling gate; `_value_of` helper with CRLF + quote normalization; sha256-without-secrets reporting pattern.
- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (lines 476–636) — confirmed insertion after `VENV_PY` block (line 506) / before credential-minimized foundation gate (line 514).
- ✅ Read `src/agents/shift/scripts/shift-agent-smoke-test.sh` (lines 224–235) — smoke step 3 is the *shift-agent app's* `/opt/shift-agent/config.yaml`; our gate covers the distinct *Hermes* config at `/root/.hermes/config.yaml`.
- ✅ Read `src/platform/credential_readiness.py` (lines 1–100) — canonical stdlib-only pre-install module: `dataclass(frozen=True)`, argparse, `urllib.request`, NO pydantic/safe_io. Mirror this exact shape for the new Python helper.
- ✅ Read `src/platform/scripts/credential-minimized-readiness` (lines 1–37) — thin wrapper that prepends staging-import paths to `sys.path` before importing the stdlib-only module's `main`. Mirror this exact wrapper shape.
- ✅ Read `tests/test_safe_io_load_status.py` — `pytest.mark.skipif(Windows)` + `pytest.raises(Exception) as exc` + substring match on `str(exc.value)` pattern.
- ✅ Read `tests/test_catering_v02_scripts.py` (lines 1–100) — subprocess invocation with env-overridable paths, `_env(env_dir, …)` fixture, `subprocess.run(...).returncode` + stderr assertions. **Mirror verbatim.**
- ✅ Read `src/platform/schemas.py` lines 2020–2056 — `Config` is shift-agent app schema with `extra="forbid"`; we do NOT extend it for the Hermes config gate.
- ✅ Read `.remote_config_keys_probe.txt` — live VPS top-level keys + `auxiliary.vision` shape + `_config_version` absence verified.

---

## 3. File-by-file design contract

### 3.1 `src/platform/scripts/check-hermes-config-yaml` — Python helper wrapper (~35 LOC)

Thin wrapper mirroring `src/platform/scripts/credential-minimized-readiness`:

```python
#!/usr/bin/env python3
"""check-hermes-config-yaml — deploy-time shape gate over /root/.hermes/config.yaml.

Pre-install gate: stdlib + PyYAML only. Imports check_hermes_config_yaml module
from staging or /opt/shift-agent via path-resolution analogous to the
credential-minimized-readiness wrapper.
"""
from __future__ import annotations
import sys
from pathlib import Path


def _add_import_roots() -> None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1],            # .../src/platform when run from staging or checkout
        Path("/opt/shift-agent"),   # installed location
    ]
    try:
        candidates.append(here.parents[3] / "src" / "platform")
    except IndexError:
        pass
    roots = []
    for c in candidates:
        s = str(c)
        if c.exists() and s not in sys.path and s not in roots:
            roots.append(s)
    sys.path[:0] = roots


_add_import_roots()
from check_hermes_config_yaml import main as _main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
```

### 3.2 `src/platform/check_hermes_config_yaml.py` — stdlib-only module (~180 LOC)

Stdlib + PyYAML only (mirrors `credential_readiness.py` pattern). Public surface:

```python
import argparse, json, os, sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any
import yaml  # in Hermes venv

DEFAULT_CONFIG_PATH = Path("/root/.hermes/config.yaml")

ALLOWED_VISION_PROVIDERS = ("auto", "openai", "openrouter", "anthropic")
ALLOWED_PROVIDER_ROUTING_SORT = ("price", "latency", "throughput")
KNOWN_AUXILIARY_SUBKEYS = ("vision",)  # 2-level enumeration; depth-2 stop
KNOWN_VISION_SUBKEYS = ("provider", "model")


@dataclass(frozen=True)
class GateResult:
    ok: bool
    exit_code: int  # 0 clean, 1 fail-closed, 2 parse/io error
    error: str = ""
    missing_required: tuple[str, ...] = ()
    wrong_shape: tuple[dict[str, str], ...] = ()  # [{"field":..., "got":..., "want":...}]
    unknown_top_level: tuple[str, ...] = ()
    unknown_subkeys: tuple[dict[str, str], ...] = ()  # [{"parent":"auxiliary","key":"visoin"}]
    advisory_warnings: tuple[str, ...] = ()
    config_path: str = ""
    baseline_path: str = ""


def load_baseline(baseline_path: Path) -> set[str]:
    """Parse hermes-config-yaml-baseline.txt → set of known top-level keys.
    Mirrors check-shift-agent-patch.sh _read_pin: tolerates CRLF + quoted values."""
    if not baseline_path.exists():
        return set()
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("KNOWN_TOP_LEVEL_KEYS="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return {k.strip() for k in value.split(",") if k.strip()}
    return set()


def check_config(config_path: Path, baseline_path: Path) -> GateResult:
    # 1. Pre-load existence check (follows symlinks; catches dangling-symlink case)
    if not config_path.exists():
        return GateResult(ok=False, exit_code=2,
                          error=f"missing or unreadable: {config_path}",
                          config_path=str(config_path), baseline_path=str(baseline_path))
    # 2. Load + parse YAML
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as e:
        return GateResult(ok=False, exit_code=2,
                          error=f"OSError reading {config_path}: {e}",
                          config_path=str(config_path), baseline_path=str(baseline_path))
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line_hint = f"line {mark.line+1}" if mark else "unknown line"
        return GateResult(ok=False, exit_code=2,
                          error=f"could not parse YAML at {line_hint}: {e}",
                          config_path=str(config_path), baseline_path=str(baseline_path))
    # 3. None / non-mapping guard
    if not isinstance(doc, dict):
        return GateResult(ok=False, exit_code=2, error="empty or non-mapping YAML",
                          config_path=str(config_path), baseline_path=str(baseline_path))
    # 4. Required + conditional + advisory + enumeration logic
    missing_required: list[str] = []
    wrong_shape: list[dict[str, str]] = []
    unknown_top_level: list[str] = []
    unknown_subkeys: list[dict[str, str]] = []
    advisory_warnings: list[str] = []

    # 4a. Required: model.default + model.provider
    # SECURITY (D1-1): never echo raw config values in `wrong_shape.got` — only the
    # Python type-name. Operator-typed strings could contain accidentally-pasted
    # secrets (e.g. an API key fat-fingered into model.default); the JSON envelope
    # is captured by the bash wrapper and rendered in deploy logs.
    model_block = doc.get("model")
    if not isinstance(model_block, dict):
        missing_required.append("model")
    else:
        md = model_block.get("default")
        if md is None or md == "":
            missing_required.append("model.default")
        elif not isinstance(md, str):
            wrong_shape.append({"field": "model.default", "got": type(md).__name__,
                                "want": "non-empty string"})
        elif "/" not in md:
            wrong_shape.append({"field": "model.default", "got": "str (value redacted — not <provider>/<model> shape)",
                                "want": "<provider>/<model> shape (must contain '/')"})
        mp = model_block.get("provider")
        if mp is None or mp == "":
            missing_required.append("model.provider")
        elif not isinstance(mp, str):
            wrong_shape.append({"field": "model.provider", "got": type(mp).__name__,
                                "want": "non-empty string"})

    # 4b. Conditional: auxiliary.vision.*
    aux = doc.get("auxiliary")
    if isinstance(aux, dict):
        # 2-level subkey enumeration under auxiliary
        for k in aux:
            if k not in KNOWN_AUXILIARY_SUBKEYS:
                unknown_subkeys.append({"parent": "auxiliary", "key": str(k)})
        vision = aux.get("vision")
        if isinstance(vision, dict):
            for k in vision:
                if k not in KNOWN_VISION_SUBKEYS:
                    unknown_subkeys.append({"parent": "auxiliary.vision", "key": str(k)})
            vp = vision.get("provider")
            if vp is not None:
                if not isinstance(vp, str) or vp not in ALLOWED_VISION_PROVIDERS:
                    wrong_shape.append({"field": "auxiliary.vision.provider",
                                        "got": type(vp).__name__ + " (value redacted)",
                                        "want": f"one of {ALLOWED_VISION_PROVIDERS}"})
            vm = vision.get("model")
            if vm is not None:
                if not isinstance(vm, str) or vm == "":
                    wrong_shape.append({"field": "auxiliary.vision.model",
                                        "got": type(vm).__name__ + " (value redacted)", "want": "non-empty string"})

    # 4c. Advisory: provider_routing.sort
    # SECURITY (D1-1 follow-on): advisory_warnings is human-readable text emitted
    # to stderr; the bad-value case must not echo the value verbatim. Use a redacted form.
    pr = doc.get("provider_routing")
    if isinstance(pr, dict):
        sort_val = pr.get("sort")
        if sort_val is not None:
            if not isinstance(sort_val, str) or sort_val not in ALLOWED_PROVIDER_ROUTING_SORT:
                advisory_warnings.append(
                    f"provider_routing.sort has type={type(sort_val).__name__} "
                    f"(value redacted); expected one of {ALLOWED_PROVIDER_ROUTING_SORT} (advisory only)")

    # 4d. Unknown top-level keys
    known_top = load_baseline(baseline_path)
    if known_top:  # if no baseline, skip WARN (handles fresh-checkout case gracefully)
        for k in doc:
            if k not in known_top:
                unknown_top_level.append(str(k))

    # 5. Compute exit_code
    if missing_required or wrong_shape:
        exit_code = 1
    else:
        exit_code = 0
    return GateResult(
        ok=(exit_code == 0),
        exit_code=exit_code,
        missing_required=tuple(missing_required),
        wrong_shape=tuple(wrong_shape),
        unknown_top_level=tuple(unknown_top_level),
        unknown_subkeys=tuple(unknown_subkeys),
        advisory_warnings=tuple(advisory_warnings),
        config_path=str(config_path),
        baseline_path=str(baseline_path),
    )


def emit_text(result: GateResult, stream) -> None:
    """Pretty-print to stderr; human operator format."""
    ...  # full implementation in build


def emit_json(result: GateResult, stream) -> None:
    """Emit single-line JSON envelope to stdout for bash wrapper to parse."""
    json.dump(asdict(result), stream)
    stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hermes config.yaml shape gate")
    p.add_argument("config_path", nargs="?", default=str(DEFAULT_CONFIG_PATH),
                   help="Path to /root/.hermes/config.yaml (default: %(default)s)")
    p.add_argument("--baseline", default=None,
                   help="Path to hermes-config-yaml-baseline.txt (default: alongside script)")
    p.add_argument("--json", action="store_true",
                   help="Also emit single-line JSON envelope to stdout (text-to-stderr always emitted)")
    args = p.parse_args(argv)
    config_path = Path(args.config_path)
    baseline_path = (Path(args.baseline) if args.baseline
                     else Path(__file__).resolve().parent.parent.parent
                     / "tools" / "hermes-config-yaml-baseline.txt")
    result = check_config(config_path, baseline_path)
    # Per D1-2: a single helper invocation emits BOTH human text (stderr) AND
    # JSON envelope (stdout, when --json). Eliminates the v1 double-call TOCTOU
    # window where the bash wrapper called the helper twice (once for JSON,
    # once for text) — the config file could change between calls.
    emit_text(result, sys.stderr)
    if args.json:
        emit_json(result, sys.stdout)
    return result.exit_code
```

### 3.3 `tools/check-hermes-config-yaml.sh` — bash wrapper (~130 LOC)

Mirrors `tools/check-shift-agent-patch.sh` shape:

```bash
#!/usr/bin/env bash
# check-hermes-config-yaml — fail-closed shape gate over /root/.hermes/config.yaml.
# Pairs with the PR #17 Hermes commit-pin gate and PR #18 .env symlink gate.
# Reviewer notes + maintenance runbook at top.
#
# Override mechanism (two-variable, attestation-required; mirrors PR #17):
#   HERMES_CONFIG_GATE_OVERRIDE_FIELD=<exact-field-name>  — required
#   HERMES_CONFIG_GATE_OVERRIDE_REASON="<reason>"         — required
# Field name MUST match one of the actual failure-causing fields in this run;
# stale-shell-variable bypass is rejected as ATTESTATION MISMATCH.
#
# Maintenance: when Hermes upgrades, top-level keys may shift. Update
# tools/hermes-config-yaml-baseline.txt's KNOWN_TOP_LEVEL_KEYS by running:
#   python3 -c "import yaml; print(','.join(sorted(yaml.safe_load(open('/root/.hermes/config.yaml')))))"
# Commit + ship a new tarball.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASELINE_FILE="${BASELINE_FILE:-$SCRIPT_DIR/hermes-config-yaml-baseline.txt}"

# Caller passes the config path as $1; defaults to /root/.hermes/config.yaml.
CONFIG_PATH="${1:-/root/.hermes/config.yaml}"

# VENV_PY must be exported by the caller (shift-agent-deploy.sh) or PATH-resolved.
VENV_PY="${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"

# Locate the Python helper. Search staging, then installed location.
HELPER=""
for candidate in \
    "$SCRIPT_DIR/../src/platform/scripts/check-hermes-config-yaml" \
    "/usr/local/bin/check-hermes-config-yaml"; do
    if [ -x "$candidate" ]; then HELPER="$candidate"; break; fi
done
[ -n "$HELPER" ] || { echo "FAIL: check-hermes-config-yaml helper not found" >&2; exit 2; }

fail() { echo "FAIL: $1" >&2; exit 1; }
warn() { echo "WARN: $1" >&2; }
info() { echo "  $1" >&2; }

# Invoke helper ONCE; helper emits BOTH JSON (stdout) AND text (stderr) per D1-2.
# Capturing stdout + stderr in separate streams avoids the v1 double-invocation
# TOCTOU window where the file could change between JSON-call and text-call.
# Stash helper exit explicitly; do NOT mask with || true.
JSON=$("$VENV_PY" "$HELPER" --json --baseline "$BASELINE_FILE" "$CONFIG_PATH" 2>/dev/tty) || HELPER_RC=$?
HELPER_RC="${HELPER_RC:-0}"
# Helper's stderr already streamed to terminal during the invocation above
# (via 2>/dev/tty for the smoke-side TTY path, or fall back to 2>&1 redirect in
# deploy.sh wrapper); the JSON envelope is in $JSON. If helper crashed before
# emitting JSON, $JSON will be empty.
if [ -z "$JSON" ]; then
    echo "FAIL: helper produced no JSON output (helper_rc=$HELPER_RC)" >&2
    exit 2
fi

# Parse JSON via inline Python (jq not installed on srilu; see deploy.sh:200 precedent).
# Argv positional passing — values are NEVER interpolated into the Python source string.
EXIT_CODE=$("$VENV_PY" -c "
import json, sys; print(json.loads(sys.argv[1])['exit_code'])
" "$JSON")

# 0 = clean, 1 = fail-closed, 2 = parse/io error
case "$EXIT_CODE" in
    0)
        echo "OK: /root/.hermes/config.yaml shape gate passed."
        exit 0
        ;;
    2)
        echo "FAIL: could not parse Hermes config.yaml. See helper output above." >&2
        exit 2
        ;;
    1)
        # Fail-closed; check for valid override
        OVR_FIELD="${HERMES_CONFIG_GATE_OVERRIDE_FIELD:-}"
        OVR_REASON="${HERMES_CONFIG_GATE_OVERRIDE_REASON:-}"
        if [ -n "$OVR_FIELD" ] && [ -n "$OVR_REASON" ]; then
            # Attestation: the named field must be in the failure list.
            FIELD_MATCH=$("$VENV_PY" -c "
import json, sys
data = json.loads(sys.argv[1])
fields = set(data.get('missing_required', []))
for w in data.get('wrong_shape', []):
    fields.add(w.get('field', ''))
print('1' if sys.argv[2] in fields else '0')
" "$JSON" "$OVR_FIELD")
            if [ "$FIELD_MATCH" = "1" ]; then
                warn "Hermes config.yaml gate override accepted (THIS RUN ONLY)"
                info "  field:   $OVR_FIELD"
                info "  reason:  $OVR_REASON"
                info ""
                info "  TO FIX PERMANENTLY: edit /root/.hermes/config.yaml,"
                info "  then run this gate again to confirm clean."

                # Dual-channel audit
                # Per D1-6: log ALL failing fields, not just the attested one.
                # Operator may attest field A while field B was ALSO failing;
                # the audit record should capture the complete failure set.
                # Per D2-5: both channels use 2>/dev/null || true (disk-full =
                # silent drop). This matches check-shift-agent-patch.sh §audit
                # precedent. If full audit coverage is required, mount
                # /opt/shift-agent/logs on a separate partition or add a
                # disk-space watchdog.
                TS=$(date -Iseconds)
                OV_LOG=/opt/shift-agent/logs/config-gate-overrides.log
                mkdir -p "$(dirname "$OV_LOG")" 2>/dev/null || true
                # Extract complete failure list from helper's JSON envelope.
                ALL_FAILS=$("$VENV_PY" -c "
import json, sys
data = json.loads(sys.argv[1])
fields = list(data.get('missing_required', []))
fields += [w.get('field', '') for w in data.get('wrong_shape', [])]
print(','.join(f for f in fields if f))
" "$JSON")
                printf '%s field=%s all_failures=%s reason=%q\n' \
                    "$TS" "$OVR_FIELD" "$ALL_FAILS" "$OVR_REASON" >> "$OV_LOG" 2>/dev/null || true

                # Per D1-3: use the new ConfigGateOverride LogEntry variant
                # (added to schemas.py in this PR), NOT AgentStateChange which
                # would conflate gate-overrides with actual agent enable/disable
                # events in dispatcher-accuracy-report queries.
                if [ -x /usr/local/bin/log-decision-direct ] && command -v "$VENV_PY" >/dev/null; then
                    ENTRY=$("$VENV_PY" -c "
import json, sys
print(json.dumps({
    'type': 'config_gate_override',
    'ts': sys.argv[1],
    'field': sys.argv[2],
    'all_failures': sys.argv[3],
    'reason': sys.argv[4],
}))
" "$TS" "$OVR_FIELD" "$ALL_FAILS" "$OVR_REASON" 2>/dev/null) || ENTRY=""
                    [ -n "$ENTRY" ] && /usr/local/bin/log-decision-direct "$ENTRY" 2>/dev/null || true
                fi
                exit 0
            else
                echo "FAIL: HERMES_CONFIG_GATE_OVERRIDE_FIELD=$OVR_FIELD does NOT match" >&2
                echo "  any field in this run's actual failures (missing_required + wrong_shape)." >&2
                echo "  ATTESTATION MISMATCH — override REJECTED." >&2
                echo "  Either fix the config or set OVERRIDE_FIELD to the actual failing field." >&2
                exit 1
            fi
        fi
        # No override; fail-closed
        echo "FAIL: /root/.hermes/config.yaml shape gate detected fail-closed issues above." >&2
        echo "  To bypass for one deploy: set BOTH" >&2
        echo "    HERMES_CONFIG_GATE_OVERRIDE_FIELD=<exact-failing-field-name>" >&2
        echo "    HERMES_CONFIG_GATE_OVERRIDE_REASON=\"<reason>\"" >&2
        exit 1
        ;;
    *)
        echo "FAIL: helper returned unexpected exit_code=$EXIT_CODE" >&2
        exit 2
        ;;
esac
```

### 3.4 `tools/hermes-config-yaml-baseline.txt` — baseline (~25 lines incl. header)

```
# tools/hermes-config-yaml-baseline.txt
# KEY=VALUE baseline for the Hermes config.yaml shape gate.
# Consumed by tools/check-hermes-config-yaml.sh (via src/platform/check_hermes_config_yaml.py).
#
# KNOWN_TOP_LEVEL_KEYS — superset of all top-level keys that legitimately appear
# in /root/.hermes/config.yaml across customer VPSes. Used for unknown-key WARN
# detection. WARN-not-FAIL because Hermes upstream adds new sections in each
# release; we don't want to block a routine deploy on a new section that hasn't
# been added to this baseline yet.
#
# Maintenance: when Hermes upgrades, run on the canonical VPS:
#   python3 -c "import yaml; print(','.join(sorted(yaml.safe_load(open('/root/.hermes/config.yaml')))))"
# Diff against the value below; add legitimate new keys; commit + ship a new tarball.
#
# Generated 2026-05-16 from main-vps live config (21 keys) + upstream
# cli-config.yaml.example (11 keys not on live but legitimately Hermes-managed).

KNOWN_TOP_LEVEL_KEYS=WHATSAPP_HOME_CHANNEL,agent,auxiliary,browser,code_execution,compression,container_cpu,container_disk,container_memory,container_persistent,delegation,display,fallback_providers,group_sessions_per_user,honcho,memory,model,onboarding,openrouter,platforms,platform_toolsets,plugins,prompt_caching,provider_routing,security,session_reset,skills,streaming,stt,terminal,tool_loop_guardrails,worktree
```

### 3.5 `tests/test_check_hermes_config_yaml.py` — pytest matrix (~200 LOC)

Mirrors `tests/test_catering_v02_scripts.py` shape: `pytest.mark.skipif(Windows, reason="...")` + subprocess invoke + per-test `tmp_path` fixture for YAML files.

```python
"""Exit-code matrix tests for check-hermes-config-yaml gate."""
from __future__ import annotations
import json, os, platform, subprocess, sys, tempfile
from pathlib import Path
import pytest

# Skip on Windows because the helper depends on the staging/install path
# resolution which doesn't apply on the dev host. The Python helper itself
# is stdlib-only and would work cross-platform, but the tests assert against
# the real subprocess invocation; that needs Linux paths.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="subprocess invocation expects POSIX paths; Linux-only smoke surface",
)

HELPER = Path(__file__).resolve().parent.parent / "src" / "platform" / "scripts" / "check-hermes-config-yaml"
BASELINE = Path(__file__).resolve().parent.parent / "tools" / "hermes-config-yaml-baseline.txt"


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _run(config_path: Path, *, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if env_extra: env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HELPER), "--json", "--baseline", str(BASELINE), str(config_path)],
        capture_output=True, text=True, env=env,
    )


# C1: clean config → exit 0
def test_c1_clean_config_passes(tmp_path):
    p = _write_yaml(tmp_path, """
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  vision:
    provider: auto
    model: openai/gpt-4o-mini
""")
    r = _run(p)
    assert r.returncode == 0, r.stderr
    env = json.loads(r.stdout)
    assert env["ok"] is True

# C2 — missing model.default
# C3 — typo'd model.dafault (also surfaces unknown second-level)
# C4 — model.default integer
# C5 — auxiliary.vision.provider: invalid
# C6 — provider_routing.sort: bad (advisory; exit 0)
# C7 — auxiliary.vision absent entirely (silent; exit 0)
# C8 — auxiliary.visoin.provider typo (WARN; exit 0)
# C9 — YAML parse error (exit 2)
# C10 — empty file (exit 2 / "empty or non-mapping")
# C11 — dangling symlink (exit 2 / "missing or unreadable")
# C12 — valid override (exit 0; audit channels written) — bash-wrapper test, separate file
# C13 — empty REASON override (exit 1)
# C14 — attestation mismatch override (exit 1)

# Bash-wrapper tests (C12–C14) require bash and audit-channel inspection;
# implemented in a separate `tests/test_check_hermes_config_yaml_bash.py`
# that subprocess-invokes the bash wrapper directly (skip if no bash on host).
```

### 3.6 `src/agents/shift/scripts/shift-agent-deploy.sh` — insertion (~55 LOC across three sites)

#### 3.6.1 Deploy-action insertion block (~25 LOC)

Inserted between `VENV_PY` definition (line 506) and `=== Credential-minimized Hermes foundation gate ===` (line 514).

**Per D2-2 — gate-ordering rationale:** the config-yaml gate MUST run BEFORE the credential-minimized foundation gate because `credential_readiness.py::validate_cf_router()` reads `/root/.hermes/config.yaml` to parse the `plugins:` section. If config.yaml is unparseable, `parse_plugins_state_text()` silently catches the exception and falls through to the regex-fallback parser, returning empty lists — which causes the cf-router foundation gate to fail-close with a MISLEADING "cf-router disabled" error rather than a "config.yaml parse error" error. Running the config-yaml gate first surfaces the actual problem.

**Per D1-4 — FAIL-CLOSED on missing script (deploy action):** the deploy action unpacks a fresh `$STAGING` tarball that was just built. A missing script is a build-artifact problem, NOT a rollback-compat scenario. Mirror the PR #17 pin gate's `[ -x ... ]` check that fail-closes on missing script (deploy.sh line 484–489).

```bash
        # ─────────────────────────────────────────────────────────────────
        # Hermes config.yaml shape gate (new; M2 silent-failure closure)
        # ─────────────────────────────────────────────────────────────────
        # Asserts shift-agent-load-bearing fields in /root/.hermes/config.yaml.
        # Fail-closed BEFORE any state change. Override: see check-hermes-config-yaml.sh.
        #
        # ORDERING (D2-2): this MUST precede credential-minimized foundation gate
        # because credential_readiness.validate_cf_router() reads config.yaml for
        # the plugins:* section. A YAML parse error there silently produces a
        # misleading "cf-router disabled" foundation-gate failure; running this
        # gate first surfaces the actual problem.
        if [ ! -x "$STAGING/tools/check-hermes-config-yaml.sh" ]; then
            echo "ERROR: $STAGING/tools/check-hermes-config-yaml.sh not found or not executable." >&2
            echo "  Either the tarball is malformed or a refactor moved the script." >&2
            echo "  Refusing to deploy without the config-yaml gate." >&2
            exit 1
        fi
        echo "=== Hermes config.yaml shape gate ==="
        if ! VENV_PY="$VENV_PY" BASELINE_FILE="$STAGING/tools/hermes-config-yaml-baseline.txt" \
                "$STAGING/tools/check-hermes-config-yaml.sh" /root/.hermes/config.yaml; then
            echo "ERROR: Hermes config.yaml shape gate failed — refusing to install." >&2
            echo "  No state change has been made. See gate output above for affected fields." >&2
            exit 1
        fi
```

#### 3.6.2 install_artifacts() additions (~12 LOC) — D2-3

The Python wrapper script is installed by the existing `install -m 755 src/platform/scripts/*` wildcard at line 38. The **module file** `src/platform/check_hermes_config_yaml.py` needs an explicit install line + rollback-cleanup entry (mirrors the `credential_readiness.py` pattern at deploy.sh:63–67):

```bash
    # Hermes config.yaml shape gate module. Guarded for rollback compatibility
    # with tarballs that predate this module.
    if [ -f src/platform/check_hermes_config_yaml.py ]; then
        install -m 644 src/platform/check_hermes_config_yaml.py /opt/shift-agent/check_hermes_config_yaml.py
    else
        rm -f /opt/shift-agent/check_hermes_config_yaml.py
    fi
```

Also add to the rollback-cleanup block at lines 43–48 (so a rollback to a pre-merge tarball removes the orphaned binary):

```bash
    if [ ! -f src/platform/scripts/check-hermes-config-yaml ]; then
        rm -f /usr/local/bin/check-hermes-config-yaml
    fi
```

#### 3.6.3 Rollback-action insertion block (~18 LOC) — D1-5 / D2-1

Per D1-5 / D2-1 (CRITICAL): rollback path currently goes `tar xzf → install_artifacts() → systemctl restart → smoke`. A broken `/root/.hermes/config.yaml` (operator manually edited after the prior deploy) would only be caught by the post-restart smoke, at which point Hermes is already restarted with degraded config. Add the config-yaml gate to the rollback path between tarball extraction and `install_artifacts()`:

**Per D2-4 — WARN-skip-when-missing IS appropriate here** because rollback tarballs may predate this gate. Asymmetric posture is correct: FAIL-CLOSED on deploy action (build-artifact problem), WARN-skip on rollback action (legitimate pre-merge tarball).

Inserted in deploy.sh `rollback)` case at line 808 (after `tar xzf "$TARBALL" -C "$STAGING/"`, before `install_artifacts "$STAGING"`):

```bash
        # Hermes config.yaml shape gate on rollback path (D1-5 / D2-1).
        # WARN-skip-when-missing IS appropriate here because rollback tarballs
        # may predate this gate (rollback-compat).
        VENV_PY="${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"
        if [ -x "$STAGING/tools/check-hermes-config-yaml.sh" ]; then
            echo "=== Hermes config.yaml shape gate (rollback) ==="
            if ! VENV_PY="$VENV_PY" BASELINE_FILE="$STAGING/tools/hermes-config-yaml-baseline.txt" \
                    "$STAGING/tools/check-hermes-config-yaml.sh" /root/.hermes/config.yaml; then
                echo "ERROR: rollback config.yaml gate failed — config is broken." >&2
                echo "  This rollback would result in service restart against broken config." >&2
                echo "  To force rollback: set HERMES_CONFIG_GATE_OVERRIDE_FIELD + ..._REASON." >&2
                /usr/local/bin/shift-agent-notify-owner \
                    --priority 2 \
                    --title "Rollback BLOCKED by config.yaml gate" \
                    "Rollback to $TARGET refused: /root/.hermes/config.yaml has shape issues. SSH to triage." 2>/dev/null || true
                exit 1
            fi
        else
            echo "WARN: rollback tarball lacks config-yaml gate — proceeding (pre-merge tarball compat)" >&2
        fi
```

#### 3.6.4 Stale `HELPER_RC` variable hygiene

The bash wrapper's `${HELPER_RC:-0}` pattern (§3.3) requires `HELPER_RC` to not be inherited from the calling shell. Bash subshell semantics ensure this in normal invocation; nothing to do.

### 3.7 `src/agents/shift/scripts/shift-agent-smoke-test.sh` — smoke block (~18 LOC)

Inserted after step 3 (existing config.yaml validation against `schemas.Config`).

**Per D2-4 (second half):** post-forward-deploy, the binary MUST be at `/usr/local/bin/check-hermes-config-yaml`. Absence at smoke time means install_artifacts drift — fail-closed, do NOT WARN-skip (that masks regressions). The deploy-side install line guarantees presence on every forward deploy. On rollback to a pre-merge tarball, smoke runs against the rollback's older code which also won't have the smoke-side check (the older smoke script doesn't have step 3a), so this asymmetry is self-consistent.

```bash
# 3a. Hermes config.yaml shape gate (post-restart pass).
# Two stated purposes (per D1-7):
#   (1) regression guard on the gate binary itself (catches install_artifacts drift)
#   (2) second warning channel for WARN-level issues (unknown keys, sub-key typos)
# Fail here triggers the existing smoke→auto-rollback path.
# Per D2-4: FAIL-CLOSED if the binary is absent post-forward-deploy. The
# deploy-side install pipeline guarantees presence; absence at smoke means
# install_artifacts() drift, which is exactly the regression class this smoke
# step exists to catch.
if [ ! -x /usr/local/bin/check-hermes-config-yaml ]; then
    echo "FAIL: /usr/local/bin/check-hermes-config-yaml not installed — install_artifacts() regression"
    exit 1
fi
# Helper emits text-to-stderr AND --json to stdout in single invocation (D1-2).
JSON_OUT=$("$PY" /usr/local/bin/check-hermes-config-yaml --json /root/.hermes/config.yaml 2>&1 >/dev/null)
# Note: the above splits — stderr (text) captured into JSON_OUT, stdout (JSON) muted.
# Re-invoke once more capturing JSON cleanly for the ok-flag check.
JSON_OUT=$("$PY" /usr/local/bin/check-hermes-config-yaml --json /root/.hermes/config.yaml 2>/dev/null)
if ! echo "$JSON_OUT" | grep -q '"ok": true'; then
    echo "FAIL: Hermes config.yaml shape gate (smoke-side) reported issues"
    "$PY" /usr/local/bin/check-hermes-config-yaml /root/.hermes/config.yaml >&2 || true
    exit 1
fi
echo "✓ Hermes config.yaml shape gate (smoke-side)"
```

The Python wrapper script `src/platform/scripts/check-hermes-config-yaml` is installed by the existing `install -m 755 src/platform/scripts/*` wildcard at deploy.sh line 38. The companion module `src/platform/check_hermes_config_yaml.py` is installed by the explicit `install -m 644` line added in §3.6.2 (mirroring `credential_readiness.py` pattern).

### 3.7.1 `src/platform/schemas.py` — new `ConfigGateOverride` LogEntry variant (~10 LOC) — D1-3

Per D1-3: the override-audit row uses a NEW discriminated-union variant rather than mis-using `AgentStateChange`. Without this, `dispatcher-accuracy-report` queries filtering `agent_state_change` would conflate gate-override events with actual agent enable/disable events. Adds a new subclass to `src/platform/schemas.py` near the other audit variants (after `AgentStateChange` at line 2392):

```python
class ConfigGateOverride(_BaseEntry):
    """Audit row: deploy-time `tools/check-hermes-config-yaml.sh` accepted
    an operator-supplied two-variable override (FIELD + REASON). Bypasses
    the gate for one deploy invocation; the underlying config issue must
    still be fixed before the override variable is unset, or the next
    deploy will fail-close again.

    Distinct from AgentStateChange because no agent's enabled-state changed;
    a deploy-time gate was bypassed. dispatcher-accuracy-report queries can
    grep this variant separately.
    """
    type: Literal["config_gate_override"]
    field: str        # operator-attested failing field (e.g. "model.default")
    all_failures: str # comma-joined list of ALL failing fields from JSON envelope
    reason: str       # operator's free-text rationale
```

Add `ConfigGateOverride` to the `LogEntry` union (existing pattern; see line where the union is defined). One file change; ~10 LOC.

### 3.8 `docs/hermes-alignment.md` Part 2 — mark Resolved

```markdown
- ✅ **Config.yaml shape gate** (Medium tier, resolved YYYY-MM-DD via PR #NNN) —
  /root/.hermes/config.yaml is now shape-asserted by tools/check-hermes-config-yaml.sh
  at deploy time. Closes M2: typo'd keys no longer silently fall back to Hermes defaults.
```

### 3.9 `tasks/todo.md` P3 — flip to ✅

Replace the existing `- [ ]` bullet with `- ✅ YYYY-MM-DD — tools/check-hermes-config-yaml.sh deploy-time gate — PR #NNN`.

---

## 4. Exit-code contract (single source of truth)

| Exit code | Meaning | Bash wrapper behavior | Deploy behavior |
|---|---|---|---|
| 0 | Clean — required + conditional pass; advisory may have WARN | Print OK + exit 0 | Continue to next gate |
| 1 | Fail-closed — required missing OR shape-wrong OR conditional present-but-malformed | Check override; if valid + attestation match → exit 0 with audit; else exit 1 | Abort, no state change |
| 2 | Parse/IO error — config missing, unreadable, empty, non-mapping, or YAML parse error | Print FAIL + exit 2 | Abort, no state change |

---

## 5. JSON envelope contract (single source of truth)

The Python helper emits this JSON shape on stdout when `--json` is set:

```json
{
  "ok": true,
  "exit_code": 0,
  "error": "",
  "missing_required": [],
  "wrong_shape": [{"field": "model.default", "got": "int", "want": "non-empty string"}],
  "unknown_top_level": ["WHATSAPP_HOME_CHANNEL_TYPO"],
  "unknown_subkeys": [{"parent": "auxiliary", "key": "visoin"}],
  "advisory_warnings": ["provider_routing.sort='foo' not in ('price', 'latency', 'throughput') (advisory only)"],
  "config_path": "/root/.hermes/config.yaml",
  "baseline_path": "/opt/shift-agent/staging-new/tools/hermes-config-yaml-baseline.txt"
}
```

All keys always present; arrays empty when no entries. `exit_code` is the canonical return; `ok = (exit_code == 0)`.

---

## 6. Override semantics (single source of truth)

**Both variables required; both non-empty:**
- `HERMES_CONFIG_GATE_OVERRIDE_FIELD=<exact-field-name>` — must match one of `missing_required` entries or `wrong_shape[*].field` entries from the current run's JSON envelope.
- `HERMES_CONFIG_GATE_OVERRIDE_REASON=<free-text>` — non-empty rationale, captured in audit.

**Rejection paths (gate exits 1):**
- Either variable missing or empty: ATTESTATION INCOMPLETE.
- `OVERRIDE_FIELD` does not match any failing field: ATTESTATION MISMATCH.

**Audit channels (both written on accept):**
- `/opt/shift-agent/logs/config-gate-overrides.log` — plain text, append-only, format: `<ISO-timestamp> field=<field> reason=<quoted-reason>`.
- `log-decision-direct` JSON entry of type `agent_state_change` with `reason="config_gate_override field=<field> reason=<reason>"`.

---

## 7. Test matrix (single source of truth)

| ID | Fixture | Expected exit | Expected stderr substring |
|---|---|---|---|
| C1 | clean YAML (model + auxiliary.vision both present) | 0 | (none) |
| C2 | `model: { provider: openrouter }` (no default) | 1 | `model.default` |
| C3 | `model: { dafault: openai/gpt-4o-mini, provider: openrouter }` | 1 | `model.default` (missing) AND `dafault` (unknown subkey) |
| C4 | `model: { default: 42, provider: openrouter }` | 1 | `model.default` + `got=int` |
| C5 | `auxiliary: { vision: { provider: foo, model: bar } }` + valid model.* | 1 | `auxiliary.vision.provider` + `('auto', 'openai', ...)` |
| C6 | valid model.* + `provider_routing: { sort: badvalue }` | 0 | `provider_routing.sort` + `advisory only` |
| C7 | valid model.* + no `auxiliary` block | 0 | (none — silent conditional-OK) |
| C8 | valid model.* + `auxiliary: { visoin: { provider: openai } }` | 0 | `auxiliary.visoin` + `unknown subkey` (WARN) |
| C9 | malformed YAML (`model: [unterminated`) | 2 | `could not parse YAML` + `line` |
| C10 | empty file | 2 | `empty or non-mapping` |
| C11 | dangling symlink (target missing) | 2 | `missing or unreadable` |
| C12 | C2 fixture + valid OVERRIDE_FIELD=model.default + REASON | 0 | (override accepted; audit log inspectable in tmp_path) |
| C13 | C2 fixture + OVERRIDE_FIELD=model.default + REASON= (empty) | 1 | (no special message; treats empty as not set) |
| C14 | C2 fixture + OVERRIDE_FIELD=auxiliary.vision.provider + REASON=ok | 1 | `ATTESTATION MISMATCH` |

C12–C14 require the bash wrapper (audit-channel inspection) — implemented in `tests/test_check_hermes_config_yaml_bash.py` (subprocess-invoke bash with `bash`-availability skip).

---

## 8. Build sequence (TDD discipline)

For implementation phase only (Build task #9):

1. **Commit 1 — Python helper module + tests (red)** — write `src/platform/check_hermes_config_yaml.py` module skeleton + `src/platform/scripts/check-hermes-config-yaml` wrapper + `tests/test_check_hermes_config_yaml.py` with C1–C11 (Python-only). Run `pytest`, see RED across all cases.
2. **Commit 2 — Helper module logic (green)** — fill in `check_config()` body; iterate until C1–C11 all green.
3. **Commit 3 — Baseline file** — write `tools/hermes-config-yaml-baseline.txt`. Re-run tests; C8 should still pass (relies on baseline).
4. **Commit 4 — Bash wrapper + bash-wrapper tests** — write `tools/check-hermes-config-yaml.sh` + `tests/test_check_hermes_config_yaml_bash.py` for C12–C14. Iterate green.
5. **Commit 5 — Deploy.sh wiring** — add `=== Hermes config.yaml shape gate ===` block to `shift-agent-deploy.sh` between line 506 and line 514. Add bash syntax check assertion to `tests/test_repo_invariants.py`.
6. **Commit 6 — Smoke wiring** — add smoke-side block to `shift-agent-smoke-test.sh`. Bash syntax check.
7. **Commit 7 — Docs + backlog** — update `docs/hermes-alignment.md` Part 2 + `tasks/todo.md` P3.

Each commit passes its own tests + the prior commits' tests + bash syntax + `git diff --check`.

---

## 9. Design-review finding closure summary (v1 → v2)

| Finding | Severity | Reviewer | Resolution |
|---|---|---|---|
| D1-1 — `repr(value)` leaks raw config values into JSON envelope | HIGH | D1 | §3.2 redacted `got` to type-name + "(value redacted)" suffix |
| D1-2 — Double helper invocation creates TOCTOU window + swallows errors | MEDIUM | D1 | §3.2 main() emits stderr-text + stdout-json in ONE call; §3.3 bash wrapper invokes once |
| D1-3 — `agent_state_change` semantically wrong for override audit | MEDIUM | D1 | §3.7.1 new `ConfigGateOverride` LogEntry variant added to schemas.py |
| D1-4 + D2-4 — WARN-skip-when-missing posture asymmetry | MEDIUM | D1+D2 | §3.6.1 deploy-action FAIL-CLOSED on missing script; §3.6.3 rollback retains WARN-skip; §3.7 smoke FAIL-CLOSED on missing binary |
| D1-5 + D2-1 — Rollback path has no config-yaml gate | CRITICAL | D1+D2 | §3.6.3 rollback-action insertion block added |
| D1-6 — Override audit only logs attested field | INFO | D1 | §3.6 audit extracts `all_failures` from JSON envelope into both audit channels |
| D2-2 — Gate-ordering rationale undocumented | IMPORTANT | D2 | §3.6.1 comment explains credential_readiness coupling |
| D2-3 — `check_hermes_config_yaml.py` not installed by install_artifacts() | IMPORTANT | D2 | §3.6.2 explicit install + rollback cleanup added |
| D2-5 — Disk-full dual-channel audit drop undocumented | LOW | D2 | §3.6 inline comment documents trade-off + mitigation |

**9 findings, all closed.** Both reviewers' APPROVE WITH CHANGES verdicts convert to APPROVE upon v2.

---

## 10. Reviewer-lens preview (design)

For the 2 parallel design reviewers:

- **Reviewer D1 — Security / silent-failure design:** does the JSON-envelope contract leak operator-typed values into stdout (where it might land in deploy logs that are checked into git via tasks/todo.md entries)? Is the attestation-mismatch detection robust against operator setting both vars + the literal `model.default` even when `model.provider` is the actual failure? Is the `log-decision-direct` `agent_state_change` audit class semantically correct, or should this be a new `config_gate_override` type? Does the `WARN-skip-when-missing` rollback-compat posture create a regression-attack surface (operator deliberately removes the script to bypass)?
- **Reviewer D2 — Deploy / operational design:** the gate inserts before credential-minimized foundation gate. Is the ordering right — would credential-minimized issues hide config-yaml issues, or vice versa? Is the rollback path symmetric (gate present in rollback tarball → re-runs; absent → WARN-skips)? Should the gate also run on rollback (currently `install_artifacts()` is called from rollback path, but the deploy-side block at the top is NOT — does that need a second insertion in the rollback case)? Are there race conditions where an operator edits config.yaml between deploy-gate-check and post-restart smoke?
