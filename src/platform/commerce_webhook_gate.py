"""Commerce webhook-subscription deploy gate (logic module).

Deploy-time, fail-closed gate that asserts the Stripe webhook subscription is
registered *when, and only when,* commerce is actively configured for Stripe
(`cfg.commerce.enabled` and `cfg.commerce.provider == "stripe"`).

Why it exists (slice-3 §13.5 A-LOW-1): without the subscription, Stripe
`payment_intent.succeeded` events silently 404 and a customer who paid is never
confirmed — a §12a/§12b silent-failure surface, and the worst kind (money moved,
no confirmation).

Dormant-safe: when commerce is off or `provider != "stripe"`, the gate is not
applicable and exits 0 — so non-commerce *and* dormant-commerce deploys are
unaffected. Verified against main-vps runtime: `hermes webhook list` returns
exit 0 with a "Webhook platform is not enabled" banner while dormant, so the
gate decides applicability from config (not from the CLI exit code).

Money-safety matching rules (Codex review 2026-05-29):
- The subscription name is matched as a whole, delimiter-bounded token on some
  line of `hermes webhook list` — never a raw substring — so a different
  subscription such as ``<name>-v2`` or the name buried in free text cannot
  produce a false pass.
- A non-zero `hermes webhook list` exit is a gate/runtime error (EXIT_CONFIG_ERROR),
  never silently classified as pass or as "subscription missing".

Stdlib + PyYAML + Pydantic `CommerceConfig` only — no fcntl/safe_io — so it is
importable in-process for tests and runnable pre-restart via the Hermes venv.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

EXIT_OK = 0
EXIT_MISSING = 1
EXIT_CONFIG_ERROR = 2

DEFAULT_CONFIG_PATH = "/opt/shift-agent/config.yaml"
DEFAULT_HERMES_BIN = "hermes"
_LIST_TIMEOUT_SECONDS = 30

# Split a list line into candidate tokens on whitespace and the common
# key/value + record separators Hermes uses ("name=...", "route: ...", commas).
_TOKEN_SPLIT = re.compile(r"[\s,=:]+")
# Decorations stripped from the edge of a token before exact comparison
# (bullets, quotes, brackets). Hyphens are NOT stripped — they are part of the
# subscription name.
_EDGE_PUNCT = "\"'`()[]<>"
_LINE_BULLETS = "-*• \t"


@dataclass
class CommerceWebhookFields:
    enabled: bool
    provider: str
    subscription_name: str


def load_commerce_fields(config_path: str) -> CommerceWebhookFields:
    """Read the ``commerce`` subsection of ``config_path`` and validate it
    through ``CommerceConfig``.

    Raises on unreadable / unparseable config or invalid commerce shape; the
    caller maps those to EXIT_CONFIG_ERROR. Validating only the commerce
    subsection (rather than the whole ``Config``) keeps this gate from failing
    on unrelated config issues that other gates already cover.
    """
    from schemas import CommerceConfig  # resolved via sys.path set by the wrapper

    text = Path(config_path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"config root is not a mapping: {config_path}")
    commerce_raw = data.get("commerce") or {}
    if not isinstance(commerce_raw, dict):
        raise ValueError("commerce section is not a mapping")
    cfg = CommerceConfig.model_validate(commerce_raw)
    return CommerceWebhookFields(
        enabled=cfg.enabled,
        provider=cfg.provider,
        subscription_name=cfg.webhook_subscription_name,
    )


def default_list_runner(hermes_bin: str) -> tuple[int, str]:
    """Run ``<hermes_bin> webhook list`` and return ``(returncode, output)``.

    Output is combined stdout+stderr because Hermes prints the "platform not
    enabled" banner and the subscription listing to different streams across
    versions; the gate only needs to token-match the subscription name. The
    return code is surfaced so the caller can fail closed on a CLI failure.
    """
    proc = subprocess.run(
        [hermes_bin, "webhook", "list"],
        capture_output=True,
        text=True,
        timeout=_LIST_TIMEOUT_SECONDS,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def is_subscription_registered(name: str, list_output: str) -> bool:
    """True iff ``name`` appears as a whole, delimiter-bounded token on some
    line of ``list_output``. Never a raw substring — prevents a longer name
    (``<name>-v2``) or the name embedded in free text from passing.
    """
    if not name:
        return False
    for line in list_output.splitlines():
        cleaned = line.strip().strip(_LINE_BULLETS)
        for token in _TOKEN_SPLIT.split(cleaned):
            if token.strip(_EDGE_PUNCT) == name:
                return True
    return False


def evaluate(fields: CommerceWebhookFields, list_output: str) -> tuple[int, str]:
    """Pure decision for the *active* path. Returns (exit_code, message).

    Caller has already confirmed applicability and a clean (rc==0) CLI run.
    """
    if is_subscription_registered(fields.subscription_name, list_output):
        return EXIT_OK, (
            f"OK: commerce webhook subscription "
            f"'{fields.subscription_name}' is registered."
        )
    return EXIT_MISSING, (
        f"FATAL: commerce.provider=stripe but webhook subscription "
        f"'{fields.subscription_name}' is not registered. Stripe "
        f"payment_intent.succeeded events will silently 404 and customer "
        f"payments will never be confirmed.\n"
        f"Register it (see docs/runbooks/commerce-stripe-onboarding.md Step 5):\n"
        f"  hermes webhook subscribe {fields.subscription_name} "
        f"--route <route> --secret <secret>\n"
        f"(do not paste the secret into logs or config — follow the runbook)."
    )


def run(
    config_path: str = DEFAULT_CONFIG_PATH,
    hermes_bin: str = DEFAULT_HERMES_BIN,
    *,
    list_runner=None,
) -> tuple[int, str, str]:
    """Return (exit_code, stdout_text, stderr_text)."""
    try:
        fields = load_commerce_fields(config_path)
    except FileNotFoundError as exc:
        return EXIT_CONFIG_ERROR, "", f"ERROR: config not found: {config_path} ({exc})"
    except Exception as exc:  # yaml / pydantic / value errors
        return (
            EXIT_CONFIG_ERROR,
            "",
            f"ERROR: could not load commerce config from {config_path}: {exc}",
        )

    if not (fields.enabled and fields.provider == "stripe"):
        return (
            EXIT_OK,
            (
                f"commerce: provider={fields.provider} (enabled={fields.enabled}) "
                f"— webhook-subscription gate not applicable, skipping."
            ),
            "",
        )

    # Active-for-Stripe but the subscription name is blank is a config-shape
    # error (exit 2), not a "missing subscription" (exit 1) — the latter would
    # also emit an invalid remediation command (`hermes webhook subscribe ` with
    # no name). Catch it before touching the CLI.
    if not fields.subscription_name.strip():
        return (
            EXIT_CONFIG_ERROR,
            "",
            (
                "ERROR: commerce.provider=stripe but commerce.webhook_subscription_name "
                "is empty. Set it (default 'stripe-commerce-payments') so the gate can "
                "verify the Stripe webhook subscription. See "
                "docs/runbooks/commerce-stripe-onboarding.md."
            ),
        )

    runner = list_runner or default_list_runner
    try:
        returncode, list_output = runner(hermes_bin)
    except FileNotFoundError as exc:
        return EXIT_CONFIG_ERROR, "", f"ERROR: hermes binary not found: {hermes_bin} ({exc})"
    except (OSError, subprocess.SubprocessError) as exc:
        # Any other launch/run failure (PermissionError, not-executable,
        # TimeoutExpired, ...) is a gate/runtime error — fail closed, never a
        # silent pass or an uncaught traceback.
        return EXIT_CONFIG_ERROR, "", f"ERROR: `{hermes_bin} webhook list` could not run: {exc}"

    if returncode != 0:
        # Fail closed: a CLI failure means we cannot determine subscription
        # state. Do NOT classify as pass, and do NOT classify as "missing"
        # (which would mislead the operator) — report a gate/runtime error.
        return (
            EXIT_CONFIG_ERROR,
            "",
            (
                f"ERROR: `{hermes_bin} webhook list` exited {returncode}; cannot "
                f"determine whether subscription '{fields.subscription_name}' is "
                f"registered. Refusing to pass the commerce webhook gate "
                f"(fail-closed).\n--- output ---\n{list_output}"
            ),
        )

    code, msg = evaluate(fields, list_output)
    if code == EXIT_OK:
        return EXIT_OK, msg, ""
    return code, "", msg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Commerce webhook-subscription deploy gate (fail-closed when "
        "commerce.provider=stripe and the subscription is missing)."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--hermes-bin", default=DEFAULT_HERMES_BIN)
    args = parser.parse_args(argv)

    code, out, err = run(args.config, args.hermes_bin)
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
