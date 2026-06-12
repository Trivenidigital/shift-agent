# Commerce slice-3.5 — webhook-subscription deploy gate (design)

**Drift-check tag:** `extends-Hermes` — adds a deploy-time gate on top of the
existing `hermes webhook` CLI and `CommerceConfig`; introduces no new substrate,
no SQLite, no parallel approval/code generators.

**Status:** approved by operator 2026-05-29; build now. Dormant-safe.

## Hermes-first capability checklist

| Step | Hermes? | Decision |
|---|---|---|
| Enumerate webhook subscriptions | `[Hermes]` — `hermes webhook list` CLI (verified main-vps: `/usr/local/bin/hermes`; subcommands subscribe/list/remove/test) | use it |
| Read `cfg.commerce.*` | `[Hermes]` — Pydantic `Config` in `schemas.py` (`CommerceConfig` has `enabled`, `provider`, `webhook_subscription_name`) | use it |
| Applicability/skip + assertion + exit-code contract | `[net-new]` — a few LOC of script logic | build (this PR) |
| Deploy wire-in + rollback | `[Hermes]` — existing pre-restart gate framework in `shift-agent-deploy.sh` | extend it |

Ecosystem check: no Hermes/community skill provides "assert my webhook
subscription is registered at deploy time" — per-agent deploy plumbing. Verdict:
thin net-new gate over Hermes primitives. Justified.

## Drift-rule self-checks

- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (full file — pre-restart gate sequence, `$VENV_PY` usage, rollback path) before drafting the wire-in.
- ✅ Read `src/platform/schemas.py` (`CommerceConfig` at line 2333 — `provider`, `webhook_subscription_name`, `enabled`, `stripe_livemode_expected`) before relying on those fields.
- ✅ Read `tasks/hermes-commerce-slice3-provider-webhook-design.md` (§13.5 A-LOW-1) — confirmed the gate was reassigned to PR-2 but never landed (grep of `src/`+`tools/` found only a schema comment).
- Will mirror `src/platform/scripts/check-hermes-config-yaml` (closest-similar config-reading gate) and `tests/test_catering_v02_scripts.py` (subprocess-invoke + assert pattern) for the script + test idiom.

## Runtime-state verification (§9a) — done before design

- `hermes webhook list` exists; dormant state returns **exit 0** with a
  "Webhook platform is not enabled" banner (NOT an error). → the gate must decide
  applicability from config, not from the CLI exit code.
- `/root/.hermes/webhook_subscriptions.json` does **not** exist while dormant →
  CLI output is the canonical interface, not a file path.
- main-vps today: `commerce.enabled` default `False`, `provider=placeholder` →
  the gate's skip path applies → deploy unaffected.

## The gate

New script `src/platform/scripts/check-commerce-webhook-subscription`:

1. Read `cfg.commerce.{enabled, provider, webhook_subscription_name}` from
   `--config` (default `/opt/shift-agent/config.yaml`) via the Hermes-venv Python
   + Pydantic `Config`.
2. **Dormant-clean path:** if `not (enabled and provider == "stripe")` → print
   `commerce: provider=<x> (enabled=<b>) — webhook-subscription gate not applicable, skipping`
   and **exit 0**. Keeps non-commerce *and* dormant-commerce deploys unaffected.
3. **Active path:** run `<hermes-bin> webhook list` (default `hermes`, overridable
   via `--hermes-bin` for tests). Assert `webhook_subscription_name` appears in
   stdout. If absent (or platform-not-enabled) → **exit 1** with an actionable,
   secret-free message naming the exact subscription + command shape
   `hermes webhook subscribe stripe-commerce-payments ...`.

Exit codes: `0` pass/skip, `1` active-but-missing, `2` config unreadable/parse error.

## Deploy wiring (operator tightening point #3)

In `shift-agent-deploy.sh`, among the pre-restart gates, prefer the **staging
source path** so the very first deploy that introduces the script still runs it:

```sh
COMMERCE_GATE="$STAGING/src/platform/scripts/check-commerce-webhook-subscription"
[ -x "$COMMERCE_GATE" ] || COMMERCE_GATE=/usr/local/bin/check-commerce-webhook-subscription  # rollback compat
if [ -x "$COMMERCE_GATE" ]; then
    if ! "$VENV_PY" "$COMMERCE_GATE" --config /opt/shift-agent/config.yaml; then
        # standard pre-restart rollback path
    fi
fi
```

Fail-closed (blocks deploy) only on the active-but-missing path — per §13.5
A-LOW-1 "deploy aborts" intent. Operator confirmed warn-only would preserve the
silent money-moved/no-confirmation failure.

## Tests (TDD)

`tests/test_commerce_webhook_subscription_gate.py` — subprocess-invoke + assert,
mirroring `test_catering_v02_scripts.py`. `--hermes-bin` points at a fake script
emitting canned `webhook list` output.

1. dormant (enabled=False) → exit 0, "not applicable" stdout.
2. enabled=True, provider=placeholder → exit 0, skip.
3. enabled=True, provider=stripe, subscription present → exit 0.
4. enabled=True, provider=stripe, subscription absent → exit 1, stderr names the
   subscription + `hermes webhook subscribe stripe-commerce-payments`.
5. enabled=True, provider=stripe, platform-not-enabled banner → exit 1.
6. unreadable/malformed config → exit 2.

## Explicitly deferred (unchanged)

- PR 4 (refund/chargeback handlers + chargeback watchdog) — deferred until Stripe
  is activated in test/live or a first paying-customer path makes it relevant.
- Livemode-match smoke (slice-3.1) — next dormant-safe guard; needs Stripe SDK +
  API key + live `stripe.Account.retrieve()`, so it can't run dormant. After this.
