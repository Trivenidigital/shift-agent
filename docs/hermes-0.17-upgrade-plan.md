# Track B — Hermes 0.17 Upgrade Plan (PLANNING ONLY)

**Drift-check tag:** `extends-Hermes` (planning doc — no code, no runtime change).

> ⚠️ **STATUS: NOT STARTED. DO NOT EXECUTE.**
> This document is a forward plan for *if/when* the fleet moves off the pinned
> Hermes 0.14. It is the companion to **Track A** (the read-only
> `hermes-version-check` monitor + the weekly `hermes-drift-check` CI workflow),
> which only *detects* and *reports* when upstream moves. Nothing here authorizes
> a Hermes update, a gateway restart, a baseline rewrite, a skill install, a
> WhatsApp migration, or a patch-port. Each path below is gated on explicit
> operator approval and its own PR.

---

## 1. Current pinned state (the thing Track A monitors)

| Item | Value | Source of truth |
|---|---|---|
| Pinned Hermes commit | `486b692ddd801f8f665d3fff023149fb1cb6509e` | `tools/hermes-patch-baseline.txt` |
| Pinned version | `unknown` (module `__version__` lags; commit is authoritative) | same |
| Post-patch `bridge.js` sha256 | `de178b6fa6227f923f479ff2d34a3419b4a2e5f83bc5e5408137712cd25ed7ec` | same |
| Effective Hermes line | **0.14** | MEMORY `project_hermes_update_blocked_patch_port` |
| Auto-update | **disabled** (manual fleet upgrades only) | same |
| Deploy gate | `tools/check-shift-agent-patch.sh` fail-closes on drift | deploy.sh first gate |
| Patches applied | sender-id (run.py / whatsapp.py / bridge.js) + `/send-cta` CTA support | `tools/patch-hermes.py` |

The shift-agent patches inject a `BEGIN/END shift-agent-sender-id` block plus the
WhatsApp bridge endpoints (`/send-media`, `/send-cta`, button-response inbound
extraction) that Flyer Studio + sender-identity routing depend on.

## 2. Why 0.17 is blocked today

Per MEMORY `project_hermes_update_blocked_patch_port` (2026-06-27): an in-place
0.14 → 0.17 upgrade is **blocked** because `patch-hermes.py`'s anchors are gone
in the 0.17 tree:

- `gateway/platforms/whatsapp.py` — the `_resolve_sender_context` /
  `_build_message_event` anchor region the sender-id patch targets is **absent
  (404)** in 0.17.
- `gateway/run.py` — refactored; the `_prepare_inbound_message_text` inject site
  and the `pre_gateway_dispatch` hook surface (used by the cf-router plugin) have
  moved/renamed.

Consequence if attempted blindly: `patch-hermes.py` fails to find its anchors →
the deploy gate (`check-shift-agent-patch.sh`) fail-closes → **WhatsApp inbound
routing + Flyer delivery break**. This is exactly the silent-failure class the
deploy gate + Track A monitor exist to prevent.

## 3. The two forward paths (mutually exclusive; pick at decision time)

### Path A — Port the patches to the 0.17 tree (keep the self-hosted bridge)

Re-derive the sender-id + CTA patches against 0.17's refactored
`whatsapp.py` / `run.py` / `bridge.js`, then re-baseline.

**Work outline (each its own PR, none in this Track A monitoring PR):**
1. Clone 0.17 upstream into a throwaway tree (CI already does this in
   `hermes-drift-check.yml`; reuse that output).
2. Locate the new equivalents of: sender-context resolution in `whatsapp.py`,
   the inbound-text inject site + `pre_gateway_dispatch` hook in `run.py`, and
   the `messageQueue.push` site in `bridge.js`.
3. Rewrite `tools/patch-hermes.py` anchors; keep the marker contract
   (`BEGIN/END shift-agent-sender-id`) so `check-shift-agent-patch.sh` still
   verifies them.
4. Re-run patch against the 0.17 tree on a **test VPS**; capture the new
   post-patch `bridge.js` sha256.
5. Update `tools/hermes-patch-baseline.txt` (new commit + version + sha) **in a
   PR**, then deploy with `HERMES_PIN_OVERRIDE` once, on the test VPS only.
6. Full smoke + live WhatsApp probe on the test VPS before any fleet rollout.

**Pros:** smallest behavioral change (same bridge, same routing). **Cons:**
re-incurs the patch-maintenance treadmill on every future Hermes bump.

### Path B — Move to the official 0.17 WhatsApp Business Cloud API (retire the bridge patches)

Adopt 0.17's first-class WhatsApp Business Cloud integration and **delete** the
self-hosted bridge + its patches entirely.

**Work outline (each its own PR + an external-provisioning track):**
1. Provision a Meta WhatsApp Business Cloud account + phone number + system-user
   token (external, non-code; operator-driven).
2. Map the current bridge endpoints (`/send`, `/send-media`, `/send-cta`,
   button-response inbound) onto the Cloud API equivalents (template messages,
   interactive messages, media upload).
3. Replace `safe_io.bridge_post` / `bridge_send_media` / `bridge_send_cta`
   call-sites with the Cloud API client (keep the same internal 2-tuple
   contracts so agents are unchanged).
4. Re-validate sender identity: the Cloud API delivers a different inbound
   payload shape than the bridge — the sender-id patch logic must be re-expressed
   (possibly natively, removing the need for a `run.py`/`whatsapp.py` patch).
5. Retire `tools/patch-hermes.py`, `bridge.js`, and the bridge sha256 baseline
   field once Path B is live.

**Pros:** removes the patch treadmill + the self-hosted bridge entirely; Meta-
supported delivery. **Cons:** larger blast radius (template pre-approval,
24-hour customer-care window rules, per-message pricing); external dependency on
Meta provisioning; must re-validate every agent's send path.

## 4. Decision gates (before EITHER path starts)

1. **Track A signal:** the monitor / CI drift workflow reports upstream is ahead
   AND a tagged 0.17 release is the target — i.e. there is a concrete reason to
   move, not a speculative one.
2. **Operator go:** explicit approval to start a port (this doc does not grant
   it).
3. **Test VPS available:** a non-customer VPS to absorb the override-deploy +
   live WhatsApp probe before any fleet rollout.
4. **Rollback rehearsed:** a tarball of the current 0.14 + baseline kept; the
   override is THIS-RUN-ONLY and the baseline change is a separate reviewed PR.

## 5. How Track A feeds this

- **`hermes-version-check`** (this PR, runtime): tells the operator, via the
  notify chokepoint, when the live VPS pin drifts or upstream moves ahead — with
  an *advisory* `patch_port_review_required` flag (it never validates patches
  itself).
- **`.github/workflows/hermes-drift-check.yml`** (weekly CI): does the heavy
  lifting — clones upstream, dry-runs `patch-hermes.py` against the new tree, and
  opens a GitHub issue stating whether the patches still apply. **That issue is
  the trigger to open Path A / Path B work.**

Track A is detection. Track B (this doc) is the response plan. Keep them
separate: detection must never auto-trigger a mutation.

## 6. Explicit non-goals of the current monitoring PR

The PR that ships `hermes-version-check` does **none** of the above. It does not
update Hermes, restart the gateway, rewrite the baseline, install skills, run
`patch-hermes.py`, clone upstream, enable auto-update, or migrate WhatsApp. Path
A and Path B are future, operator-approved, separately-reviewed work.
