# Transport-evidence patch — source-closure provenance (candidate-head verification)

**Drift-check tag:** extends-Hermes (a marker-fenced, fail-closed, idempotent patch
on top of the pinned Hermes gateway source; adds no new Hermes-owned behaviour).

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Gateway lifecycle / dispatch / send path | none (Hermes IS the substrate being patched) | reuse the real `start`/`stop`/`_handle_message[_with_agent]`/`_send_with_retry`/budget/front-brain surfaces via a marker-fenced patch — no custom transport |

This artifact records the exact Hermes source closure the transport-evidence
patch (`tools/patch-hermes.py` `_apply_run_transport_evidence` /
`_apply_wa_transport_evidence`) is authored against, the anchor counts, and the
deterministic patched-output result. It is evidence for the seven-review
candidate head. The harness binds to the FUTURE RC commit; the RC build MUST
re-run these checks against the Hermes source shipped in that RC (the deploy
gate `tools/check-shift-agent-patch.sh` + the fail-closed `PatchError` enforce
this — anchor drift → no build/deploy).

## Inspected source closure (read-only, `main-vps` pinned baseline, 2026-07-28)

Host: `main-vps` `/root/.hermes/hermes-agent` (the pinned Hermes the current
deployed patches were authored against; carries the sender-id / front-brain /
turn-budget markers, NOT yet the transport-evidence markers).

| File | sha256 | bytes |
|---|---|---|
| `gateway/run.py` | `759df6cff744fcd261586311ff965b94ad1c29d125566675d291872901ae2a3e` | 858246 |
| `gateway/platforms/whatsapp.py` | `325d5a306579b8e4459a2a6f82188d686cee1b50510d0165b34b5fcdb77474bf` | 62478 |
| `gateway/platforms/base.py` (read-only ref; not patched) | `964312fb742072b0f1c5d2eabb29609bb683b67703ebe29e09b263cf245374d0` | 161278 |

## Anchor counts (each EXACTLY 1 — deterministic single-site inserts)

`run.py`:
- `^import os$` (module control-plane block): **1**
- `logger.info("Press Ctrl+C to stop")` (async startup hook, end of `start()`): **1**
- `"""Stop the gateway and disconnect all adapters."""` (async shutdown hook in `stop()`): **1**
- `^    async def _handle_message_with_agent\(` (diagnostic-dispatch branch): **1**

`whatsapp.py`:
- `class WhatsAppAdapter(BasePlatformAdapter):` (provider-entry observer helper): **1**
- bridge POST `/send` boundary (`async with self._http_session.post( … /send",`) (provider-entry observer inject, 16-space indent): **1**

## Deterministic patched output (real transform applied to the real source)

| Patched file | parses | post-patch markers each once | sha256 | bytes |
|---|---|---|---|---|
| `gateway/run.py` | OK | OK (probe/startup/shutdown/diag) | `e22362c7e7a4d12b2ec03db9afe6bed7ffb083f1faf697784226aba98b65ee93` | 867199 |
| `gateway/platforms/whatsapp.py` | OK | OK (probe/provider-entry) | `b152048aeb07a1576b4a4ddd6bdda8b31205f030b3542c21f91242040f9a2150` | 63418 |

- Safe second application (idempotent): re-applying the transform to the patched
  output is a no-op (byte-identical). Confirmed for both files.
- Fail-closed on anchor drift: each `_apply_*` raises `PatchError` if any anchor
  is absent (unit-verified); the all-or-nothing writer leaves every target
  byte-identical.

The production-equivalent synthetic gateway fixture used by
`tests/test_transport_evidence_harness.py` mirrors exactly these anchors
(`start()`/`stop()`/`_handle_message[_with_agent]` + the `send()` budget-gate →
POST `/send` boundary), so the integration test exercises the same patched shape
proven here against the real source.
