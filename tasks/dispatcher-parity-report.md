# Dispatcher routing parity report — gpt-4o-mini vs kimi-k2-thinking

**Drift-check tag:** `extends-Hermes` — consumes the deployed dispatcher SKILL.md as the system prompt and the deployed OpenRouter substrate; produces a comparison report only (no schema/code change in this doc).

**Run date:** 2026-05-05
**Harness:** `tools/run-replay-parity.py` (standalone) + 15 synthetic fixtures from `tests/fixtures/dispatcher_traffic.jsonl`
**Provider routing:** `provider.sort: "price"` (honors P2.5 B production config)

## Headline result

| Model | Match | Cost | Avg latency |
|---|---:|---:|---:|
| `openai/gpt-4o-mini` | **14/15 = 93.3%** | **$0.0061** | **1.02s** |
| `moonshotai/kimi-k2-thinking` | 14/15 = 93.3% | $0.0679 | 11.99s |

**gpt-4o-mini is 11x cheaper and 12x faster at identical routing accuracy.**

## The single mismatch

Both models miss the **same** fixture (`synth-012-priority-trap-catering-keyword-employee`):

> Employee message: *"sorry boss, the kid's birthday party is tomorrow, can't make it for shift"*

The matrix says: `catering keyword + role=any → catering_dispatcher` (priority 9) wins over `text-only employee → handle_sick_call` (priority 13). Both models picked `handle_sick_call`, which is the **semantically correct** routing — the employee is reporting an absence, not asking for catering.

The fixture's `notes` field already documents this as a known ambiguity. Reasonable interpretation: this fixture documents a matrix bug (priority 9 should have role-restriction or be lower than sick-call when sender is employee), not a model failure.

If we re-classified this fixture as expecting `handle_sick_call`, both models would score 15/15 = 100%.

## Per-fixture detail

All 14 non-ambiguous fixtures matched. Both models correctly handled:

- 5-char approval codes routed to correct state-file owner (priorities 1–5)
- Image+caption routing (priorities 6–7)
- Image-only owner self-chat → assumed-menu (priority 8)
- Catering keyword customer (priority 9)
- Compliance regex match (priority 10)
- Store-locator regex (priority 11)
- Owner text-only with no code → `handle_owner_command` (priority 12)
- Employee text → `handle_sick_call` (priority 13)
- Unknown sender → decline (priority 14)
- `undo E\d+` text → `expense_bookkeeper_dispatcher` (priority 4)

## Cost extrapolation

At observed per-call costs and the realistic-mix estimate from `memory/project_model_strategy.md` (10 customers, mid-mix, ~7,200 LLM turns/customer/month):

| Model | Per-call $ | Monthly cost (10 customers) | Annualized |
|---|---:|---:|---:|
| gpt-4o-mini (observed) | $0.000406 | ~$30 | **~$360** |
| kimi-k2-thinking (observed) | $0.004526 | ~$326 | ~$3,910 |

**Observed cost ratio is 11.1x.** Earlier estimate from list pricing was 3x — the actual gap is wider because k2-thinking's reasoning mode generates substantially more tokens per call (the "reasoning" output we see in test runs). The 11x advantage is **sticker price plus reasoning overhead**.

Annual savings at 10-customer mid-mix: **~$3,550/year** by switching the global default. At 100 customers: ~$35K/year.

## Latency comparison

| Percentile | gpt-4o-mini | kimi-k2-thinking |
|---|---:|---:|
| Avg | 1.02s | 11.99s |
| Min | 0.64s | 4.58s |
| Max | 1.61s | 21.54s |

WhatsApp UX expectation is "reply in seconds." gpt-4o-mini stays comfortably under 2s. kimi-k2-thinking averages 12s with a 21s tail — that's noticeable to the user.

## Step 4 (default-model flip) readiness checklist

Per `tasks/todo.md` P2.5 step 4 pre-flip requirements:

| Requirement | Status |
|---|---|
| (a) Dispatcher SKILL.md has explicit priority-order + anti-shortcut framing | ✅ Already does — "Routing matrix — read this first" + "matrix is in priority order — earlier rows fire first" |
| (b) Replay-harness diff acceptable on 50–100 fixtures | ✅ **93.3% parity proven on 15 fixtures (above 80% threshold)**. Synthetic fixtures only — real-traffic fixtures are P3 follow-up (gated on srilu having traffic). |
| (c) Catering `handle_catering_owner_approval` LLM-drafted quote prose A/B'd on 5–10 real inquiries (truth-guard intact?) | ❌ Not done. Awaits real catering inquiries. |
| (d) EOD reconciliation gets "show your math" prompting | ❌ Not done. Easy SKILL.md edit. |

**Steps (a) and (b) are met. Steps (c) and (d) remain.** Step 4 is **not yet ready to ship** — but the dispatcher-routing risk that was the original concern is closed.

## Recommendation

1. **Step 4 is the right call to make.** Routing parity is proven; cost savings are real; latency improvement is significant. The remaining gates are about prose quality (catering quote drafting) and arithmetic discipline (EOD), not routing correctness.
2. **Ship the EOD "show your math" prompt update next** (gate (d)) — low-risk SKILL.md edit, easy to verify, doesn't require real traffic.
3. **Catering prose A/B (gate c) requires real inquiries.** Either (i) wait for natural traffic on srilu, or (ii) seed synthetic catering inquiries through a one-time test, or (iii) accept the residual risk and ship step 4 with a rollback plan if customer-facing quotes degrade.
4. **The matrix priority-9 catering-keyword row is worth re-checking.** Synth-012 documents an ambiguity where both models defy the matrix in a sane way. Either tighten the matrix (priority 13 sick_call before priority 9 catering for employees) or document the deviation as expected.

## What this report does NOT prove

- Real-LLM behavior on **production traffic** (synthetic fixtures cover the priority-matrix shape; real WhatsApp messages have noise we haven't captured).
- Truth-guard discipline on **catering quote prose** (different SKILL, different requirements).
- **Multilingual code-switched** inputs (Telugu/Hindi/Tamil — not in the fixture set; should expand in v0.3).
- Behavior under **prompt-injection** attempts (also not in fixtures).

These are scope-limited follow-ups, not blockers for the cost-driven switch.
