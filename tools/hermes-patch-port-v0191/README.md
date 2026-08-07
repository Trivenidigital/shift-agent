# Hermes 0.19.1 patch-port — verbatim production snapshot

**This directory is a provenance snapshot, not new work.** Every file here was copied
**byte-for-byte** from the running production VPS on 2026-08-07 and committed unmodified. The code
was authored directly on the box during the 2026-08-01 Hermes 0.14 → 0.19.1 upgrade and existed in
**no commit** until this import. Nothing was cleaned up, reformatted or "modernised" during import —
cleanup, if warranted, happens in a later change so that Git first captures the bytes that are
actually executing.

## SHA-256 attestation (production == committed)

Verified with `sha256sum -c` at import time; every file matched.

| File | SHA-256 | Production source |
|---|---|---|
| `../../src/plugins/shift-agent-policy/policy.py` | `db0d762e…fe351` | `/root/.hermes/plugins/shift-agent-policy/policy.py` |
| `../../src/plugins/shift-agent-policy/__init__.py` | `0c506a03…ce673` | same dir |
| `../../src/plugins/shift-agent-policy/plugin.yaml` | `be1898e1…19b3a` | same dir |
| `patch1_port_v0191.py` | `a91e0a6d…f8c5` | `/opt/shift-agent/hermes-patch-probes/` |
| `patch2_failclosed.py` | `2cbefb66…ad16` | same dir |
| `probe_front_brain.py` | `038d0000…8585` | same dir |
| `probe_preflight_refusal.py` | `571c4864…e634` | same dir |
| `probe_sender_context.py` | `5f95eeae…bccf` | same dir |
| `probe_bridge.mjs` | `e9a311e2…5503` | same dir |
| `probe_button_branches.mjs` | `3921f545…24cd` | same dir |
| `emit_events.mjs` | `b89c0551…7278` | same dir |
| `test_shift_agent_policy.py` | `4749fd69…02c3` | same dir |

No line-ending or permission normalization was required: all files were already LF, and the
executable bits (755 on the two patch scripts and the three Python probes) are preserved.

## ✅ Bridge reproducibility — PROVEN 2026-08-07

Run in an isolated `/tmp` tree (production untouched), starting from the pristine unpatched 0.19.1
sources via `git show HEAD:<path>`:

```
pristine  bridge.js         9e1c4745da7d385a56fe3e48ff510e94f577ccd4cd01daa66c02d69267226185
  + patch1_port_v0191.py -> 70c2aaeed99e58f785d385d5bd8936ccd351b1687e29cec1f1549be917adb8af
  + patch2_failclosed.py -> f8bdb2abc2a2a5bc8f80b9eb6373fa67dc78a23793db92fd7e5552d11724bb0d
running production bridge  f8bdb2abc2a2a5bc8f80b9eb6373fa67dc78a23793db92fd7e5552d11724bb0d   ✅ MATCH
```

`BRIDGE_POST_PATCH_SHA256=f8bdb2ab…` is therefore a **reproducible post-patch attestation** derived
from versioned source — no longer merely "the hash of today's box". This is the precondition for
regenerating `tools/hermes-patch-baseline.txt`.

Both scripts are marker-guarded and idempotent, and abort fail-closed (`rc=2`, no files written) if an
anchor is missing or ambiguous.

## ⚠⚠ DO NOT re-run these scripts against production

They are a **superset** of what production actually runs. In the isolated reproduction they also
patched `plugins/platforms/whatsapp/adapter.py`, `gateway/platforms/whatsapp_common.py` and
`gateway/run.py` — but production carries **no** `shift-agent` markers in any of those files (only in
the two JS files). The Python half was **superseded by the `shift-agent-policy` plugin**, whose whole
purpose is to keep the Hermes checkout stock. Re-running these scripts on the box would re-introduce
in-tree core patches that the current architecture deliberately removed, and would double up the
screening. Use them for reproduction and audit only.

## ⚠ These are box-only tools. Do NOT wire them into CI.

`test_shift_agent_policy.py` is an **operator acceptance gate**, not a unit test. It lives here rather
than in `tests/` on purpose: it imports `hermes_cli.plugins` and `gateway.*` from
`/usr/local/lib/hermes-agent` at module scope and shells out to `node`, so `pytest tests/` would fail
at collection on any machine that is not the VPS. It is preserved for provenance and for running
**on the box**.

## ⚠⚠ `probe_preflight_refusal.py` MUTATES LIVE PRODUCTION CONFIG

It deliberately rewrites `/root/.hermes/config.yaml` — removing `shift-agent-policy` from
`plugins.enabled`, and separately breaking the screening import — to prove the preflight **refuses**,
then restores the original. **If it is interrupted mid-run, production is left with outbound
screening disabled.** Do not run it casually, and never run it concurrently with live traffic.
`probe_front_brain.py` is pure (no writes, no subprocess, no network) and is the safe one to run.

The two `patch*.py` scripts write to the Hermes tree by design — that is their function. Their only
network references are `http://127.0.0.1:<bridge_port>/edit`, i.e. loopback to the local WhatsApp
bridge, inside the code they inject.

## What this code does

`shift-agent-policy` supersedes the former in-tree Hermes core patches so the Hermes checkout stays
stock. It provides two invariants through supported plugin seams:

* **Outbound** — `ScreenedWhatsAppAdapter` subclasses the bundled WhatsApp adapter and routes every
  `send()` and streamed `edit_message()` through `safe_io.front_brain_screen_gateway_send`,
  **fail-closed** on exception or timeout (`BLOCK_SEND` sentinel compared by identity).
* **Inbound** — a `pre_gateway_dispatch` hook stamps an authenticated `[shift-agent-sender v=1 …]`
  block onto inbound messages and sanitises the untrusted body (NFKC, invisible-character strip,
  spoofed-block rename). **Gated on `HERMES_INJECT_SENDER_CONTEXT=1`**, which is set in production.

`assert_registry_override` guards the load-order dependency the design rests on: platform
registration is last-writer-wins, so a plugin registering after this one would silently displace the
screening adapter. The check compares class identity via a tag on the winning factory and refuses to
construct rather than let unscreened output reach live chats.
