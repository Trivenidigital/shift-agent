# Flyer Studio — Weekly Cost-per-Accepted Report (week ending 2026-07-05)

**Window:** Mon 2026-06-29 → Sun 2026-07-05 (the F0209–F0214 delivery era spills a few hours
into Mon 2026-07-06 — F0212 00:02Z, F0213 01:04Z — noted where it matters).
**Compiled:** 2026-07-06, against `origin/main` `e908c39` and live box state.
**Cap under test:** operator weekly guardrail **$25/week** (context: $200/mo OpenRouter account
cap; $300/mo per-store retainer).

---

## Headline

- **Under cap.** Week's measurable OpenRouter spend ≈ **$12.5**, ~**50%** of the $25/week cap.
- Spend was **~87% one-time R&D** (register/model evaluation) and **~13% production delivery**.
- **Marginal cost per delivered premium flyer ≈ $0.126** — trivial against the retainer
  (a single delivery is ~0.04% of one store's monthly $300).
- **7 flyers delivered** in the week (**5** in the F0209–F0214 era; F0214 was held, not delivered).

---

## 1. Accepted / delivered denominator (real box read)

Source: `/opt/shift-agent/logs/decisions.log`, `flyer_assets_delivered` rows (9 lifetime; 7 in
window). Timestamps verbatim from the log.

| Project | Delivered (UTC) | Kind |
|---|---|---|
| F0201 | 2026-07-03 19:01:40 | Canary — first v2 + letterbox delivery |
| F0203 | 2026-07-04 00:05:32 | Finale — first full v2 + premium delivery |
| F0210 | 2026-07-04 18:31:23 | C1 pass — crowned register served live |
| F0211 | 2026-07-05 02:42:17 | Swipe-probe **misroute duplicate** (non-organic) |
| F0209 | 2026-07-05 22:56:21 | Quoted-APPROVE delivery #2 |
| F0212 | 2026-07-06 00:02:23 | Organic quoted-APPROVE #3 |
| F0213 | 2026-07-06 01:04:06 | Premium-dark exhibit / **replay incident** (non-organic) |

- **F0209–F0214 era delivered: 5** (F0209, F0210, F0211, F0212, F0213). **F0214 = HELD**
  (exhibit 2, no `flyer_assets_delivered` row — confirmed).
- **Full week delivered: 7.**
- Composition caveat: 2 of the 7 (F0211 probe-misroute, F0213 replay incident) were **not
  organic customer approvals**. Genuinely operator/customer-approved: 5 (F0201, F0203, F0209,
  F0210, F0212).

## 2. Cost per delivered

| Basis | Per delivered | Era (5) | Week (7) |
|---|---|---|---|
| **Marginal, measured** ($0.126/flyer — gemini-3.1 gen + gpt-4o-mini extraction/QA; readiness packet §2) | $0.126 | ~$0.63 | ~$0.88 |
| **Prod-key envelope cross-check** (all July production activity incl. retries + non-delivered attempts) | — | see §3 | ~$1.60 |

The envelope ($1.60, §3) sits just above 7×$0.126 because it also carries render retries and
attempts that never delivered — consistent, same order of magnitude.

## 3. Spend breakdown (OpenRouter — live-queried 2026-07-06)

There is **no on-box dollar ledger** (see §5). Dollar cost lives at OpenRouter; both keys were
queried live via `GET /api/v1/key`.

| Key | Role | usage_monthly (≈ this week) | limit | remaining |
|---|---|---|---|---|
| Production (`OPENROUTER_API_KEY`, active) | Customer renders + extraction/QA | **$1.60** | $30 | $28.40 |
| ws0 (`/root/.hermes/ws0-openrouter.key`) | Register/model R&D, A/B evals | **$10.86** | $50 | $39.14 |

- **Production ($1.60):** all July production render/extraction traffic on the *current* prod key.
  The key was **re-issued mid-week** (the prior prod key was exhausted ~2026-07-03/04 at ~$132.76
  lifetime and retired), so this $1.60 covers roughly the F0210-era onward; the earlier F0201/F0203
  renders were on the now-retired key (bounded, ~$0.25 marginal-estimated, not separately queryable).
- **R&D ($10.86, ws0):** the register aesthetic rounds (R1→R3.5), the model A/B
  (gemini-3.1 vs gpt-5.4-image-2), the Leg-2 extraction A/B, and verifier calibration — the cost
  of *proving* register + model choices before they ship. Readiness packet §2 attributes $2.47 to
  the "19-sample evaluation funnel" specifically; the remainder is the earlier legs on the same key.
  The gpt-5.4-image-2 arm (~$1.30/image) ran on the *old prod account* and helped exhaust it;
  gpt-5.4 is now shelved as a premium-tier-only candidate.

## 4. Week total vs $25/week cap

| Bucket | Amount | Note |
|---|---|---|
| Production delivery (current prod key, all July) | ~$1.60 | 7 deliveries + attempts/retries |
| Pre-re-key production (F0201/F0203 era, retired key) | ~$0.25 | marginal-estimated |
| R&D / evaluation (ws0 key) | ~$10.86 | one-time; not per-customer |
| **Week total (measurable)** | **≈ $12.5** | **~50% of the $25/week cap → UNDER** |

**Interpretation:** the cap is comfortably respected. Almost all spend was **one-time R&D** to
crown the register and settle the model question — not recurring cost. Steady-state customer
delivery runs at ~$0.126/flyer, i.e. a full week of 7 organic deliveries costs **under $1**. The
economic risk is not per-delivery cost; it is unbounded R&D experimentation, which the
single-priced-probe protocol (open every new model arm with one priced probe, reported before the
batch) and the ws0-key $50 sub-limit already bound.

## 5. Method & honesty note (data-source caveat)

The task brief assumed an on-box "usage_daily" dollar ledger. **No such ledger exists.** Verified
2026-07-06:
- No `usage_daily` file/dir on the box; `state.db` has only `messages`/`sessions` tables (no
  cost/token tables).
- The only on-box "usage" ledger is `flyer_usage_recorded` (`src/agents/flyer/account.py`; 255
  rows in the decisions log) — a **project-count quota ledger** for the $300/mo retainer, **not
  dollars** (`count=1` per project; kinds reserved/used/released).
- Dollar cost is therefore reconstructed from **OpenRouter's live per-key usage** (numerator) ×
  the **real on-box delivery count** (denominator), plus the session-measured $0.126 marginal.

Two figures are estimates, flagged inline: the pre-re-key production remainder (~$0.25) and the
R&D-vs-production split of the ws0 key. OpenRouter's `usage_weekly`/`usage_daily` counters reset
Monday 2026-07-06 UTC (both keys showed `usage_weekly == usage_daily` at query time), so
`usage_monthly` — which for both keys falls entirely within this report week — is the correct
weekly proxy, not `usage_weekly`.

**Follow-up worth its cost (~5 min at ship time):** if per-week dollar reporting becomes routine,
have the render path log OpenRouter's returned generation cost per call into the decisions log
(a `flyer_render_cost` row). Today "what did the week cost" is only answerable by live-querying
OpenRouter and is un-attributable per project after a key rotation.
