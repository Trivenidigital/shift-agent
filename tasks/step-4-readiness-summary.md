# Step 4 readiness summary — flip default model to gpt-4o-mini

**Drift-check tag:** `extends-Hermes` — proposes a substrate config change (`hermes config set model.default openai/gpt-4o-mini`) backed by composition of evidence from dispatcher routing parity (PR #73) + EOD path investigation + catering prose A/B (this doc). No schema/code change in this doc.

**Date:** 2026-05-05
**Tracker:** `tasks/todo.md` P2.5 step 4
**Decision required from:** human operator (this doc is data + recommendation, not authorization)

## Status update — 2026-05-05 SHIPPED

**Operator authorized "SHIP" — applied on srilu-vps at 2026-05-05 20:35 UTC.**

- `model.default`: `moonshotai/kimi-k2-thinking` → `openai/gpt-4o-mini`
- `fallback_providers`: 1 entry (`moonshotai/kimi-k2-thinking` via OpenRouter) — soak-window safety net
- Backup: `/root/.hermes/config.yaml.pre-step4-20260505-203536`
- hermes-gateway service: active+running post-restart
- Other config (B `provider_routing.sort=price`, vision-auth `auxiliary.vision.provider=auto`): preserved

Soak window in progress; retirement of kimi fallback after ~30 clean days tracked in `tasks/todo.md` P2.5.

---

## Bottom line

**All four step-4 gates are closed.** Data unanimously supports flipping the default model. The decision is the operator's per the project's "Never auto-commit / authorize-before-act" rule.

| Metric | gpt-4o-mini | kimi-k2-thinking | Δ |
|---|---:|---:|---|
| Dispatcher routing match (15 fixtures) | 14/15 = 93.3% | 14/15 = 93.3% | identical |
| Catering quote prose truth-guard (5 leads) | **5/5 = 100%** | 4/5 = 80% | **gpt-4o-mini better** |
| Cost per dispatcher call | $0.000406 | $0.004526 | **11.1x cheaper** |
| Cost per catering quote | $0.00034 | $0.00400 | **11.8x cheaper** |
| Latency (dispatcher avg) | 1.02s | 11.99s | **12x faster** |
| Latency (catering avg) | 1.37s | 34.55s | **25x faster** |

## Step 4 pre-flip checklist — final state

| Gate | Status | Evidence |
|---|---|---|
| (a) Dispatcher SKILL has explicit priority + anti-shortcut framing | ✅ PASS | `dispatch_shift_agent/SKILL.md` already has "Routing matrix — read this first" + "matrix is in priority order — earlier rows fire first" + "Hard rule: this skill runs BEFORE any other Shift / Catering / Menu skill" |
| (b) Replay-harness diff acceptable on dispatcher routing | ✅ PASS | 93.3% match rate, both models miss the same documented-ambiguity fixture (synth-012). See `tasks/dispatcher-parity-report.md` |
| (c) Catering quote prose truth-guard intact on real-shape inquiries | ✅ PASS | **5/5 = 100% truth-guard pass on gpt-4o-mini vs 4/5 = 80% on kimi-k2-thinking.** All headcount + ISO date + length + markdown + customer-greeting + lead-ref checks passed for gpt-4o-mini. See "Catering prose A/B" section below. |
| (d) EOD reconciliation gets "show your math" prompting | ✅ N/A | EOD reconcile (`src/agents/eod_reconcile/scripts/eod-reconcile`) is fully deterministic Python. Daily Brief is template-based ("`Render the brief by interpolating into the template (no LLM in v0.1).`"). No LLM in either path → no prompt to add. Refocus to future agents (expense_bookkeeper RealQBOClient, pnl_anomaly) when they ship. |

## Catering prose A/B — detail

Run: `tools/run-catering-prose-parity.py` on srilu-vps, 2026-05-05.

**5 synthetic leads** covering:
- Wedding (vegetarian, 80 guests, hotel)
- Corporate lunch (gluten-free, 25 guests, office)
- Large birthday (no dietary, 150 guests, backyard)
- Anonymous customer (vegan, 40 guests, park) — tests fallback to "Hi there"
- **Headcount-50 collision trap** (50 guests, jain dietary) — tests truth-guard against substring matches like "150 people" or "50% off"

**Per-lead result:**

| Lead | gpt-4o-mini | kimi-k2-thinking |
|---|---|---|
| Wedding (Priya, 80, vegetarian) | ✅ all 6 checks | ✅ all 6 checks |
| Corporate lunch (Acme, 25, gluten-free) | ✅ all 6 checks | ✅ all 6 checks |
| Birthday (Raj, 150) | ✅ all 6 checks | ❌ **0-char response** — model returned empty text |
| Anonymous (40, vegan) | ✅ all 6 checks | ✅ all 6 checks |
| Headcount-50 collision trap (Suresh, 50, jain) | ✅ all 6 checks (word-boundary match worked) | ✅ all 6 checks |

**The kimi-k2-thinking failure is the same failure mode `docs/hermes-alignment.md:115` documented for production:** `response=0 chars` after a long call (44.78s here, 320s in production). This isn't a fixture-construction bug — it's the model's reasoning-mode interaction with the catering prompt occasionally producing no output. **Switching to gpt-4o-mini eliminates this failure mode.**

## Cost & latency at scale (10-customer, mid-mix)

Annualized projections using observed per-call rates:

| Path | gpt-4o-mini /yr | kimi-k2-thinking /yr | Annual savings |
|---|---:|---:|---:|
| Dispatcher routing (~7,200 turns/cust/mo × 12 × 10) | ~$350 | ~$3,910 | $3,560 |
| Catering quote drafting (~10/cust/mo × 12 × 10) | ~$0.40 | ~$4.80 | $4.40 |
| **Total** | **~$350** | **~$3,915** | **~$3,565** |

At 100 customers: **~$35K/yr saved** by flipping default.

Latency improvement is also material: WhatsApp owners + customers see replies in ~1s instead of ~12-35s. Better UX, less perceived "did the message go through?" anxiety.

## Residual risks

1. **Synthetic leads ≠ real leads.** The 5 leads cover common shapes but real customers may produce edge cases the harness didn't cover (multi-event bookings, non-Latin scripts in customer_name, embedded prompt-injection attempts). The harness is a directional answer, not a guarantee.
2. **Multilingual code-switching not tested.** Telugu/Hindi/Tamil/Gujarati mixed with English. Both models claim multilingual; neither has been A/B'd on this specific repo's customer base. Tracked as P3 v0.3 work.
3. **First-traffic provider verification still pending.** P2.5 has an open item to capture the `provider` field on the first real Hermes-issued call after flip — verifies that B (cheapest-provider routing) is actually picking cheap providers in production. Already verified by composition + direct curl test; just lacks a single-trace observation from a Hermes-issued call.
4. **Reasoning-heavy future agents.** pnl_anomaly, compliance, expense_bookkeeper full-prod will benefit from a reasoning model. Step 4 sets the global default — future agents that need reasoning will need either a per-skill override (P3 future work, requires Hermes upstream change) OR multi-profile architecture (P2.5 deferred A).

## Recommendation: SHIP — with rollback plan

Recommend flipping the default. The data is decisive:
- Routing accuracy: identical (93.3% both)
- Prose quality: gpt-4o-mini WINS (100% vs 80%)
- Cost: 11x reduction
- Latency: 12-25x reduction
- Stability: gpt-4o-mini eliminates the 0-char response failure mode that's been documented in production

**The flip itself:**

```bash
# On srilu-vps (canonical clean per memory):
ssh root@srilu-vps 'cp /root/.hermes/config.yaml /root/.hermes/config.yaml.pre-step4-$(date +%Y%m%d-%H%M%S)'
ssh root@srilu-vps 'sudo -u shift-agent python3 -c "
import yaml
p = \"/root/.hermes/config.yaml\"
with open(p) as f: c = yaml.safe_load(f)
c[\"model\"][\"default\"] = \"openai/gpt-4o-mini\"
with open(p, \"w\") as f: yaml.safe_dump(c, f, default_flow_style=False, sort_keys=False)
"'
ssh root@srilu-vps 'systemctl restart hermes-gateway'
```

**Rollback** (one config flip back):

```bash
ssh root@srilu-vps 'cp /root/.hermes/config.yaml.pre-step4-<TIMESTAMP> /root/.hermes/config.yaml'
ssh root@srilu-vps 'systemctl restart hermes-gateway'
```

**Soak window:** watch `/opt/shift-agent/logs/hermes-gateway.log` for first 24h after flip. Specifically:
- 0 `AuthenticationError` (already 0 from vision-auth fix)
- 0 `0-char response` patterns (was the kimi failure mode)
- `dispatcher_routed` audit entries written for inbound messages
- Customer-facing catering quotes contain headcount + ISO date

**Resilience:** keep `kimi-k2-thinking` configured as `hermes fallback` so any gpt-4o-mini outage falls back to the previous default automatically.

## What this doc does NOT authorize

- The flip itself. The operator must run the commands above (or grant explicit "go ahead and flip" authorization).
- Bulk-deploy across the fleet. Recommended sequence: srilu-vps first, soak 24-48h, then bulk-deploy to other VPSs via existing tarball process (config-only changes don't need tarball — operator runs the same `hermes config` commands on each VPS).

## Open follow-ups (after flip ships)

- First-traffic provider observation (P2.5 — closes the loop on B from PR #72).
- Multilingual fixture set v0.3 (P3).
- Real-fixture extraction once srilu has WhatsApp traffic (P3).
- The matrix priority-9 catering-keyword vs priority-13 sick-call ambiguity (synth-012) — both models defied the matrix in a sane way; consider tightening the matrix.
