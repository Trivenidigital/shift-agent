# Hermes 0.14 → 0.17 Upgrade Plan (PLAN — no code yet)

**Status:** PLAN ONLY. No code, no upgrade, no runtime mutation. Decide Option A vs B before any work.
**Date:** 2026-06-27
**Current:** Hermes **0.14.0** (commit `1e71b71`), pinned via `/usr/local/bin/hermes-patch-baseline.txt` + the deploy patch gate. **Upstream:** 0.17.0. Test beds only (no production). The read-only version-check monitor (Track A) is live and reports this drift.

**Drift-check tag:** `extends-Hermes` — we maintain custom Hermes-side patches (sender-id-context + CTA buttons) on top of the gateway/bridge; this upgrade either re-establishes that extension on 0.17 (Option A) or migrates it onto a Hermes-native channel (Option B).

**New primitives introduced:** none net-new conceptually — this is platform-upgrade + patch-port/replacement work on existing custom integration.

## Hermes-first per-step checklist

| Step | Tag | Notes | net-new effort |
|---|---|---|---|
| Snapshot + restore | `[Hermes]` | per-VPS state + encrypted backups | 0 |
| Checkout 0.17 + reinstall venv | `[Hermes]` | `hermes update` platform path | 0 |
| Port sender-id/CTA patches (Option A) | `[net-new]` | our custom gateway/bridge integration; Hermes has no equivalent | re-anchor ~400 LOC + tests |
| OR official WhatsApp Business Cloud API (Option B) | `[Hermes]` | 0.17-native channel — removes the bridge patch entirely | adapt sender_context only |
| `hermes config migrate` | `[Hermes]` | config/secrets schema migration | 0 |
| Regenerate baseline + patch gate | `[net-new]` | `hermes-patch-baseline.txt` + `check-shift-agent-patch.sh` | small |
| Gateway restart + WhatsApp channel | `[Hermes]` | gateway/LLM substrate | 0 |
| Smoke (channels/skills/gateway) | `[Hermes]` | + `[net-new]` sender-id/Flyer assertions | small |
| Promotion/rollback orchestration | `[net-new]` | fleet tooling + manual | small |

**awesome-hermes-agent ecosystem check + verdict:** no third-party skill ports a custom WhatsApp sender-id patch for us; the only Hermes-native lever that removes the burden is 0.17's **official WhatsApp Business Cloud API** (Option B). Verdict: the genuine net-new is the patch-port (A) or the sender_context adaptation (B); the platform upgrade itself is Hermes substrate.

## Drift-rule self-checks
- ✅ Read `tools/patch-hermes.py` — the 3 patch payloads + anchors (`gateway/run.py`, `gateway/platforms/whatsapp.py`, `scripts/whatsapp-bridge/bridge.js`); idempotent + fail-closed.
- ✅ Read `tools/hermes-patch-baseline.txt` + `tools/check-shift-agent-patch.sh` — the pin baseline (commit/version + `BRIDGE_POST_PATCH_SHA256`) + the deploy gate that enforces it.
- ✅ Read `tools/hermes-fleet-upgrade.py` — the READ-ONLY fleet planner (stop conditions: gateway inactive, bridge not listening :3000, env symlink, patch gate fail).
- ✅ Verified (web, 2026-06-27) live 0.17 source: `gateway/platforms/whatsapp.py` 404; `gateway/run.py` anchors removed; `scripts/whatsapp-bridge/bridge.js` present-but-changed.

---

## 1. Current custom patches (what we maintain on top of Hermes)

`tools/patch-hermes.py` (idempotent, fail-closed, BEGIN/END-marker guarded) injects the "sender-id-context + CTA-buttons" integration into **three** Hermes 0.14 files:

| Patch | File | Anchors (0.14) | Purpose |
|---|---|---|---|
| **sender-id-context (whatsapp.py)** | `gateway/platforms/whatsapp.py` | `class *Platform:` | `_resolve_sender_context` (phone/LID/fromMe), invisible-char scrub, `[shift-agent-sender]` block injection — so the agent reliably identifies the sender (phone vs `@lid`). |
| **sender-id-context (run.py)** | `gateway/run.py` | `^import os`, `async def _prepare_inbound_message_text`, `if _is_shared_multi_user` | inject the sender block into the inbound message text the agent sees, before multi-user handling. |
| **CTA-button bridge (bridge.js)** | `scripts/whatsapp-bridge/bridge.js` | sender-id + CTA JS anchors | LID→phone mapping cache + the CTA-button HTTP route (interactive buttons / button-response body). |

These are foundational: `src/sender_context.py` is the canonical implementation; the cf-router (`src/plugins/cf-router/hooks.py`) and all dispatch/identity/allowlist logic depend on a correct resolved sender. Pin baseline records `BRIDGE_POST_PATCH_SHA256` so the deploy gate detects bridge drift.

## 2. Why they break on 0.17 (verified)

- **`gateway/platforms/whatsapp.py` → 404** in 0.17 — the WhatsApp platform was relocated/refactored (0.17 added the official WhatsApp Business Cloud API). The `class *Platform:` anchor target file is gone.
- **`gateway/run.py` → anchors removed** — 0.15's breaking run-agent refactor (16k→3.8k lines, 14 `agent/*` modules) removed `_prepare_inbound_message_text` and `if _is_shared_multi_user`.
- **`scripts/whatsapp-bridge/bridge.js` → present but changed** (Baileys-based; LID mapping still there, but JS anchors likely shifted).
- ⇒ `patch-hermes.py` is **fail-closed**: on 0.17 it exits 1 (anchors/file missing), so `hermes update` would leave the gateway running **without** our sender-id integration → WhatsApp identity/routing breaks. A blind update is unsafe; this is the documented blocker.

## 3. Two upgrade options

**Option A — Port the unofficial-bridge patches to 0.17.** Re-locate each patch onto 0.17's structure: find the new WhatsApp platform module + the new inbound-text seam in the refactored gateway + re-anchor the bridge.js CTA/LID code; rewrite `patch-hermes.py` anchors; regenerate the baseline (new commit/version + new bridge SHA). Keeps the unofficial Baileys bridge.

**Option B — Migrate to the official WhatsApp Business Cloud API (0.17-native).** Configure Hermes 0.17's official WhatsApp Business Cloud API channel; **delete** the three bridge patches + `patch-hermes.py`'s bridge/whatsapp arms; adapt `src/sender_context.py` to the Business API inbound event shape (it exposes sender identity natively); replace CTA buttons with the Business API's interactive message type (or accept a documented downgrade). Requires a Meta WhatsApp Business account + migrating the `+17329837841` number.

## 4. Comparison

| Axis | Option A (port patches) | Option B (Business Cloud API) |
|---|---|---|
| Engineering effort | ~3–5 days; high reverse-engineering uncertainty (re-anchor in unfamiliar 0.17 internals) | ~2–4 days; mostly config + `sender_context` adaptation + CTA re-expression |
| WhatsApp reliability | unchanged (unofficial Baileys bridge — the current cycling/ban-risk persists) | **higher** (official Meta API; no Baileys cycling/ban risk) |
| Future upgrade burden | **recurring** — every Hermes upgrade re-breaks the anchors → re-port forever | **near-zero** — Hermes maintains the native channel; no custom gateway patches to re-port |
| Risk | medium-high: fragile anchors, fail-closed but easy to misapply; bridge SHA drift | medium: external account/number migration + identity-shape change; but no fragile patching |
| Testing needs | heavy: sender-id correctness across phone/LID, CTA buttons, receive/send, multi-user | heavy: sender-id mapping on the new event shape, interactive-message parity, receive/send |
| Rollback complexity | clean: `git checkout 1e71b71` + restore baseline/bridge | harder: number is migrated to the Business API; reverting the channel + number is slower |

**Recommendation:** strategically **Option B** — it removes the recurring patch-port tax permanently and improves WhatsApp reliability (kills the known bridge-cycling pain), at the cost of a one-time Meta Business onboarding + number migration. **Option A** is the faster tactical path if the Meta Business account/number migration is not yet acceptable (operator has currently scoped Business migration OUT). Given that constraint, the **near-term path is A** (or simply stay pinned at 0.14 until B is approved); the version-check monitor keeps us informed meanwhile. Decision is the operator's.

## 5. Test plan (either option, on the snapshot-backed test VPS)
- **Gateway starts** (`systemctl status hermes-gateway` active; clean journal).
- **WhatsApp identity/routing works** — inbound from `+17329837841` resolves to the correct phone (not raw `@lid`); allowlist + dispatch route correctly.
- **Sender-id context correct** — the resolved sender block matches `src/sender_context.py` expectations (port the `tests/test_sender_context.py` cases against the new path).
- **CTA buttons** work (A) OR are intentionally replaced with the Business API interactive type / documented downgrade (B).
- **Receive/send smoke** — a test message round-trips (no live cap abuse; dedicated test path).
- **Flyer Studio WhatsApp test** — `+17329837841` → CCA headline renders + delivers (the F0190 path), CD v2 scope unchanged.
- **Model config + switch smoke** — `/model`, fallback provider.
- **Skills load + `hermes skills audit`** — our 123 skills load; no bundled skill we depend on got trimmed (0.16).
- **Secrets** — `.env` provider keys still load (or migrate per 0.15 Bitwarden); no key regression.
- **Rollback drill** — prove `git checkout 1e71b71` + baseline/bridge restore returns to green before promoting.

## 6. Non-goals / boundaries
No auto-update; no runtime mutation from the monitor; no new community skill install; **no WhatsApp Business migration until explicitly approved** (so Option B stays a plan); no production rollout (test beds only). Upgrade work, when approved, is staged + snapshot-backed + rollback-drilled, gated on the full test plan.

## 7. Build sequence (after a decision — NOT started)
**If A:** (1) re-anchor `patch-hermes.py` to 0.17 + unit-test the anchors; (2) port `sender_context` if the event shape changed; (3) regenerate baseline + patch gate; (4) staged upgrade + full smoke; (5) rollback drill; (6) bump monitor baseline.
**If B:** (1) provision Meta WhatsApp Business + migrate number; (2) configure the 0.17 Business API channel; (3) adapt `sender_context` to the Business event shape + re-express CTA; (4) remove bridge patches + the bridge arms of `patch-hermes.py`; (5) staged upgrade + full smoke; (6) rollback plan + monitor baseline bump.
