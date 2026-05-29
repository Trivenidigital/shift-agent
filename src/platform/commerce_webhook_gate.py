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

Stdlib + PyYAML + Pydantic `CommerceConfig` only — no fcntl/safe_io — so it is
importable in-process for tests and runnable pre-restart via the Hermes venv.
"""
from __future__ import annotations

import argparse
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


def default_list_runner(hermes_bin: str) -> str:
    """Run ``<hermes_bin> webhook list`` and return combined stdout+stderr.

    Combined because Hermes prints the "platform not enabled" banner and the
    subscription listing to different streams across versions; the gate only
    needs to substring-match the subscription name regardless of stream.
    """
    proc = subprocess.run(
        [hermes_bin, "webhook", "list"],
        capture_output=True,
        text=True,
        timeout=_LIST_TIMEOUT_SECONDS,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def evaluate(fields: CommerceWebhookFields, list_output: str) -> tuple[int, str]:
    """Pure decision for the *active* path. Returns (exit_code, message)."""
    if fields.subscription_name and fields.subscription_name in list_output:
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

    runner = list_runner or default_list_runner
    try:
        list_output = runner(hermes_bin)
    except FileNotFoundError as exc:
        return EXIT_CONFIG_ERROR, "", f"ERROR: hermes binary not found: {hermes_bin} ({exc})"
    except subprocess.SubprocessError as exc:
        return EXIT_CONFIG_ERROR, "", f"ERROR: `{hermes_bin} webhook list` failed: {exc}"

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
