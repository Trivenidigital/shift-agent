"""Hermes config.yaml shape gate — stdlib + PyYAML only.

Deploy-time gate over /root/.hermes/config.yaml. Mirrors the pre-install
stdlib-only pattern used by src/platform/credential_readiness.py — runs
BEFORE /opt/shift-agent artifacts are installed, so it must NOT depend on
pydantic, safe_io, or any other /opt/shift-agent module.

Closes the M2 silent-failure surface: typo'd Hermes config keys silently
fall back to defaults because hermes config check / hermes doctor do not
validate YAML shape (verified live 2026-05-16). See
tasks/check-hermes-config-yaml-gate-plan.md for the full Hermes-first
analysis.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml  # part of the Hermes venv — verified via `hermes doctor` 2026-05-16


DEFAULT_CONFIG_PATH = Path("/root/.hermes/config.yaml")

ALLOWED_VISION_PROVIDERS = ("auto", "openai", "openrouter", "anthropic")
ALLOWED_PROVIDER_ROUTING_SORT = ("price", "latency", "throughput")
KNOWN_MODEL_SUBKEYS = ("default", "provider", "base_url", "context_length", "max_tokens")
KNOWN_AUXILIARY_SUBKEYS = ("vision",)
KNOWN_VISION_SUBKEYS = ("provider", "model")

# Sanity cap on YAML file size. Live main-vps config is ~2 KB; 1 MiB is 500×
# headroom and bounds memory exhaustion if an operator accidentally redirects
# a large file into the config path.
MAX_CONFIG_BYTES = 1_048_576


@dataclass
class GateResult:
    """Structured result for the shape gate. Consumed via JSON envelope by the
    bash wrapper and via dataclass attributes by tests. `ok` mirrors
    `exit_code == 0` and is kept as a serialized field because smoke-test
    grep + downstream audit consumers read it directly. `baseline_path`
    field dropped — was never read by any consumer.
    """
    ok: bool
    exit_code: int  # 0 clean, 1 fail-closed, 2 parse/io error
    error: str = ""
    missing_required: list[str] = field(default_factory=list)
    wrong_shape: list[dict[str, str]] = field(default_factory=list)
    unknown_top_level: list[str] = field(default_factory=list)
    unknown_subkeys: list[dict[str, str]] = field(default_factory=list)
    advisory_warnings: list[str] = field(default_factory=list)
    config_path: str = ""


def load_baseline(baseline_path: Path) -> set[str]:
    """Parse hermes-config-yaml-baseline.txt → set of known top-level keys.

    Mirrors check-shift-agent-patch.sh _read_pin: tolerates CRLF + quoted values.
    Returns empty set if baseline file is missing (graceful fresh-checkout case).
    """
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


def check_config(config_path: Path, baseline_path: Path, *, baseline_required: bool = False) -> GateResult:
    """Inspect the Hermes config.yaml and return a GateResult.

    When `baseline_required` is True (used when caller explicitly supplied
    --baseline), a missing baseline file is a hard error rather than a
    graceful skip. Prevents the silent-failure mode where a misplaced
    baseline file disables unknown-top-level-key WARN enumeration.
    """
    result = GateResult(
        ok=False,
        exit_code=2,
        config_path=str(config_path),
    )

    # 0. Baseline-required check (per PR-review P2-1: missing baseline silently
    # disables WARN enumeration; treat as hard error when caller explicitly
    # supplied a baseline path so deploy.sh can't silently skip top-level
    # validation due to a packaging bug).
    if baseline_required and not baseline_path.exists():
        result.error = f"baseline file missing: {baseline_path}"
        return result

    # 1. Pre-load existence check (Path.exists follows symlinks → catches
    # dangling symlinks since the target won't exist).
    if not config_path.exists():
        result.error = f"missing or unreadable: {config_path}"
        return result

    # 2. Size sanity cap (per PR-review P2-4: bound memory-exhaust if an
    # operator accidentally redirects a large file into the config path).
    try:
        size = config_path.stat().st_size
    except OSError as e:
        result.error = f"OSError stat {config_path}: {e}"
        return result
    if size > MAX_CONFIG_BYTES:
        result.error = f"config.yaml size {size} bytes exceeds sanity cap of {MAX_CONFIG_BYTES} bytes"
        return result

    # 3. Load + parse YAML
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as e:
        result.error = f"OSError reading {config_path}: {e}"
        return result
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line_hint = f"line {mark.line + 1}" if mark else "unknown line"
        result.error = f"could not parse YAML at {line_hint}"
        return result

    # 4. None / non-mapping guard
    if not isinstance(doc, dict):
        result.error = "empty or non-mapping YAML"
        return result

    # 5a. Required: model.default + model.provider
    # Security: never echo raw config values in `wrong_shape.got` — only the
    # Python type-name. Operator-typed strings could contain accidentally-pasted
    # secrets (e.g. an API key fat-fingered into model.default); the JSON envelope
    # is captured by the bash wrapper and rendered in deploy logs.
    # Per PR-review P3-F8: also enumerate subkeys under model.* to catch typos
    # like `model.dafault: openai/gpt-4o-mini` (paired with the missing-required
    # entry, gives the operator both signals).
    model_block = doc.get("model")
    if not isinstance(model_block, dict):
        result.missing_required.append("model")
    else:
        for k in model_block:
            if k not in KNOWN_MODEL_SUBKEYS:
                result.unknown_subkeys.append({"parent": "model", "key": str(k)})
        md = model_block.get("default")
        if md is None or md == "":
            result.missing_required.append("model.default")
        elif not isinstance(md, str):
            result.wrong_shape.append({
                "field": "model.default",
                "got": type(md).__name__ + " (value redacted)",
                "want": "non-empty string",
            })
        elif "/" not in md:
            result.wrong_shape.append({
                "field": "model.default",
                "got": "str (value redacted - missing '/')",
                "want": "<provider>/<model> shape (must contain '/')",
            })
        mp = model_block.get("provider")
        if mp is None or mp == "":
            result.missing_required.append("model.provider")
        elif not isinstance(mp, str):
            result.wrong_shape.append({
                "field": "model.provider",
                "got": type(mp).__name__ + " (value redacted)",
                "want": "non-empty string",
            })

    # 4b. Conditional: auxiliary.vision.*
    aux = doc.get("auxiliary")
    if isinstance(aux, dict):
        # 2-level subkey enumeration under auxiliary
        for k in aux:
            if k not in KNOWN_AUXILIARY_SUBKEYS:
                result.unknown_subkeys.append({"parent": "auxiliary", "key": str(k)})
        vision = aux.get("vision")
        if isinstance(vision, dict):
            for k in vision:
                if k not in KNOWN_VISION_SUBKEYS:
                    result.unknown_subkeys.append({"parent": "auxiliary.vision", "key": str(k)})
            vp = vision.get("provider")
            if vp is not None:
                if not isinstance(vp, str) or vp not in ALLOWED_VISION_PROVIDERS:
                    result.wrong_shape.append({
                        "field": "auxiliary.vision.provider",
                        "got": type(vp).__name__ + " (value redacted)",
                        "want": f"one of {ALLOWED_VISION_PROVIDERS}",
                    })
            vm = vision.get("model")
            if vm is not None:
                if not isinstance(vm, str) or vm == "":
                    result.wrong_shape.append({
                        "field": "auxiliary.vision.model",
                        "got": type(vm).__name__ + " (value redacted)",
                        "want": "non-empty string",
                    })

    # 4c. Advisory: provider_routing.sort
    pr = doc.get("provider_routing")
    if isinstance(pr, dict):
        sort_val = pr.get("sort")
        if sort_val is not None:
            if not isinstance(sort_val, str) or sort_val not in ALLOWED_PROVIDER_ROUTING_SORT:
                result.advisory_warnings.append(
                    f"provider_routing.sort has type={type(sort_val).__name__} "
                    f"(value redacted); expected one of {ALLOWED_PROVIDER_ROUTING_SORT} (advisory only)"
                )

    # 4d. Unknown top-level keys
    known_top = load_baseline(baseline_path)
    if known_top:  # if no baseline, skip WARN (fresh-checkout / test case)
        for k in doc:
            if k not in known_top:
                result.unknown_top_level.append(str(k))

    # 6. Compute exit_code + ok
    if result.missing_required or result.wrong_shape:
        result.exit_code = 1
        result.ok = False
    else:
        result.exit_code = 0
        result.ok = True

    return result


def emit_text(result: GateResult, stream) -> None:
    """Pretty-print human-readable summary to stream (stderr in production)."""
    if result.exit_code == 2:
        stream.write(f"FAIL: {result.error}\n")
        stream.write(f"  config_path={result.config_path}\n")
        return

    if result.exit_code == 0 and not (
        result.unknown_top_level or result.unknown_subkeys or result.advisory_warnings
    ):
        stream.write(f"OK: {result.config_path} shape gate passed (clean).\n")
        return

    if result.exit_code == 1:
        stream.write(f"FAIL: {result.config_path} shape gate detected issues:\n")
        for f in result.missing_required:
            stream.write(f"  - MISSING REQUIRED: {f}\n")
        for w in result.wrong_shape:
            stream.write(f"  - WRONG SHAPE: {w['field']} (got {w['got']}; want {w['want']})\n")
    else:
        stream.write(f"OK: {result.config_path} shape gate passed (with warnings):\n")

    for k in result.unknown_top_level:
        stream.write(f"  WARN: unknown top-level key '{k}' "
                     "(typo? OR new Hermes section — check baseline)\n")
    for sk in result.unknown_subkeys:
        stream.write(f"  WARN: unknown subkey '{sk['key']}' under '{sk['parent']}' "
                     "(typo? OR new Hermes feature)\n")
    for adv in result.advisory_warnings:
        stream.write(f"  ADVISORY: {adv}\n")


def emit_json(result: GateResult, stream) -> None:
    """Emit single-line JSON envelope to stream (stdout)."""
    json.dump(asdict(result), stream)
    stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hermes config.yaml shape gate")
    p.add_argument(
        "config_path",
        nargs="?",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to /root/.hermes/config.yaml (default: %(default)s)",
    )
    p.add_argument(
        "--baseline",
        default=None,
        help="Path to hermes-config-yaml-baseline.txt (default: alongside repo tools/)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Also emit single-line JSON envelope to stdout (text-to-stderr always emitted)",
    )
    args = p.parse_args(argv)

    config_path = Path(args.config_path)
    # Per PR-review P2-1: when caller explicitly passes --baseline, a missing
    # baseline file must be a hard error (silent skip of unknown-key WARN
    # enumeration is exactly the failure mode the gate exists to prevent).
    # When no --baseline supplied (e.g. fresh checkout running tests), keep
    # the graceful-fallback behavior so tests don't need to write a baseline
    # in every fixture.
    baseline_required = args.baseline is not None
    if args.baseline:
        baseline_path = Path(args.baseline)
    else:
        # Default search: same directory tree as this module, ../tools/hermes-config-yaml-baseline.txt
        here = Path(__file__).resolve()
        candidates = [
            here.parent.parent.parent / "tools" / "hermes-config-yaml-baseline.txt",  # repo checkout
            Path("/opt/shift-agent/staging-new/tools/hermes-config-yaml-baseline.txt"),  # VPS staging
        ]
        baseline_path = next((c for c in candidates if c.exists()), candidates[0])

    result = check_config(config_path, baseline_path, baseline_required=baseline_required)

    # Single helper invocation emits BOTH human text (stderr) AND JSON envelope
    # (stdout, when --json). Eliminates the TOCTOU window where the bash wrapper
    # would otherwise call the helper twice (config file could change between
    # calls). See M2 closure rationale in PR #99.
    emit_text(result, sys.stderr)
    if args.json:
        emit_json(result, sys.stdout)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
