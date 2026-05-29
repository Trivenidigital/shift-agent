"""Commerce Stripe livemode-match deploy gate (logic module).

Deploy-time, fail-closed gate that asserts the Stripe API key's account mode
matches `cfg.commerce.stripe_livemode_expected` — *when, and only when,* commerce
is actively configured for Stripe (`cfg.commerce.enabled` and
`cfg.commerce.provider == "stripe"`).

Why it exists (slice-3 §13.5 B-MEDIUM-1; runbook commerce-stripe-onboarding.md
Step 8): catches the "live key in a test-mode config" (or vice versa) footgun
*before* any customer pays — a money-safety check.

Dormant-safe: when commerce is off or `provider != "stripe"`, the gate is not
applicable and exits 0. The Stripe API key is read and the network call is made
ONLY on the active path, so a dormant VPS reads no secret and touches no network.

Stdlib + PyYAML + Pydantic `CommerceConfig` only — the Stripe account mode is
fetched via raw `urllib` (GET https://api.stripe.com/v1/account), mirroring
`src/agents/catering/scripts/vision-auth-smoke`. No `stripe` SDK dependency, so
the gate imports and tests cleanly even where the SDK is not installed.

NEVER logs the API key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_CONFIG_ERROR = 2

DEFAULT_CONFIG_PATH = "/opt/shift-agent/config.yaml"
DEFAULT_ENV_PATH = "/opt/shift-agent/.env"
STRIPE_ACCOUNT_URL = os.environ.get("STRIPE_ACCOUNT_URL", "https://api.stripe.com/v1/account")
_TIMEOUT_SEC = int(os.environ.get("COMMERCE_LIVEMODE_TIMEOUT_SEC", "20"))
_MAX_RETRIES = int(os.environ.get("COMMERCE_LIVEMODE_RETRIES", "2"))
_PLACEHOLDER_KEYS = ("", "placeholder", "your-api-key", "<key>", "sk_test_xxx", "changeme")


@dataclass
class CommerceLivemodeFields:
    enabled: bool
    provider: str
    livemode_expected: bool


def load_commerce_fields(config_path: str) -> CommerceLivemodeFields:
    """Read+validate the ``commerce`` subsection. Raises on unreadable /
    unparseable config or invalid shape (caller maps to EXIT_CONFIG_ERROR)."""
    from schemas import CommerceConfig

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
    return CommerceLivemodeFields(
        enabled=cfg.enabled,
        provider=cfg.provider,
        livemode_expected=cfg.stripe_livemode_expected,
    )


def default_key_reader() -> str:
    """Read STRIPE_API_KEY from env, else from /opt/shift-agent/.env.

    Mirrors vision-auth-smoke._read_api_key. Returns "" if absent.
    """
    key = os.environ.get("STRIPE_API_KEY", "").strip()
    if key:
        return key
    env_path = os.environ.get("SHIFT_AGENT_ENV_PATH", DEFAULT_ENV_PATH)
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("STRIPE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def default_account_fetcher(api_key: str) -> tuple[str, "bool | None", str]:
    """GET https://api.stripe.com/v1/account and return (status, livemode, message).

    status: "ok" (livemode is bool) | "auth" (401/403) | "transient" (timeout /
    5xx / network / non-JSON / missing field). Retries transient with backoff,
    mirroring vision-auth-smoke. Never returns or logs the key.
    """
    last_msg = ""
    for attempt in range(_MAX_RETRIES + 1):
        req = urllib.request.Request(
            STRIPE_ACCOUNT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            doc = json.loads(body)
            if "livemode" not in doc or not isinstance(doc.get("livemode"), bool):
                last_msg = "200 response missing boolean 'livemode' field"
            else:
                return "ok", bool(doc["livemode"]), "ok"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return "auth", None, f"HTTP {e.code} (auth): {e.reason}"
            last_msg = f"HTTP {e.code}: {e.reason}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_msg = f"network: {e}"
        except (json.JSONDecodeError, ValueError) as e:
            last_msg = f"non-JSON response: {e}"
        if attempt < _MAX_RETRIES:
            time.sleep(2 ** attempt)
    return "transient", None, last_msg


def _is_placeholder(key: str) -> bool:
    return key.strip().lower() in _PLACEHOLDER_KEYS


def run(
    config_path: str = DEFAULT_CONFIG_PATH,
    *,
    key_reader=None,
    account_fetcher=None,
) -> tuple[int, str, str]:
    """Return (exit_code, stdout_text, stderr_text)."""
    try:
        fields = load_commerce_fields(config_path)
    except FileNotFoundError as exc:
        return EXIT_CONFIG_ERROR, "", f"ERROR: config not found: {config_path} ({exc})"
    except Exception as exc:
        return EXIT_CONFIG_ERROR, "", f"ERROR: could not load commerce config from {config_path}: {exc}"

    if not (fields.enabled and fields.provider == "stripe"):
        return (
            EXIT_OK,
            (
                f"commerce: provider={fields.provider} (enabled={fields.enabled}) "
                f"— Stripe livemode gate not applicable, skipping."
            ),
            "",
        )

    read_key = key_reader or default_key_reader
    key = (read_key() or "").strip()
    if _is_placeholder(key):
        return (
            EXIT_CONFIG_ERROR,
            "",
            (
                "ERROR: commerce.provider=stripe but STRIPE_API_KEY is missing or a "
                "placeholder in /opt/shift-agent/.env. Set the Stripe API key per "
                "docs/runbooks/commerce-stripe-onboarding.md (do not paste it into logs)."
            ),
        )

    fetch = account_fetcher or default_account_fetcher
    status, livemode, message = fetch(key)

    if status == "auth":
        return (
            EXIT_CONFIG_ERROR,
            "",
            (
                f"ERROR: Stripe rejected the API key ({message}); cannot determine "
                f"account livemode. Verify STRIPE_API_KEY per "
                f"docs/runbooks/commerce-stripe-onboarding.md."
            ),
        )
    if status != "ok":
        return (
            EXIT_CONFIG_ERROR,
            "",
            (
                f"ERROR: could not reach Stripe to read account livemode ({message}). "
                f"Refusing to pass the livemode gate (fail-closed); retry once Stripe "
                f"is reachable."
            ),
        )

    if livemode == fields.livemode_expected:
        mode = "live" if livemode else "test"
        return (
            EXIT_OK,
            (
                f"OK: Stripe account livemode={livemode} ({mode} mode) matches "
                f"commerce.stripe_livemode_expected={fields.livemode_expected}."
            ),
            "",
        )

    observed_mode = "LIVE" if livemode else "TEST"
    expected_mode = "LIVE" if fields.livemode_expected else "TEST"
    return (
        EXIT_MISMATCH,
        "",
        (
            f"FATAL: Stripe API key is a {observed_mode}-mode key "
            f"(account livemode={livemode}) but commerce.stripe_livemode_expected="
            f"{fields.livemode_expected} ({expected_mode} mode). This is the "
            f"'{observed_mode.lower()} key in {expected_mode.lower()} config' footgun "
            f"— refusing to deploy before a customer can pay against the wrong mode.\n"
            f"Fix per docs/runbooks/commerce-stripe-onboarding.md Step 8: either set "
            f"the matching STRIPE_API_KEY or correct stripe_livemode_expected. "
            f"(API key not shown.)"
        ),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Commerce Stripe livemode-match deploy gate (fail-closed on mismatch)."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    code, out, err = run(args.config)
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
