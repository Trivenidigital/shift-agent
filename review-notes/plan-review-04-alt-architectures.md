# Plan Review 4/5 — Alternative Architectures (general-purpose)

**Verdict:** Keep current architecture + adopt TWO specific hardening changes (~2-3h extra work).

## Alt 1: LLM-as-language-only (deterministic Python orchestrates)
- Pros: bulletproof audit, no model-skip class, cheaper, faster, testable.
- Cons: requires writing NLU for sick-call extraction (50+ phrasings incl. Hinglish), ambiguous approvals painful without LLM.
- Build: ~12-16h vs current ~10h (NLU tax).
- **Reject** — NLU tax not worth it at n=1 customer with tail-logger already providing audit.

## Alt 2: Serverless / managed (Twilio + Supabase + Modal)
- **Non-starter.** WhatsApp Business API on Twilio requires Meta business verification + template approval (3-14 days). Blocks 48h deadline.
- Customer wants linked-device = Baileys only (not Twilio Business API).
- Supabase for 1 customer + <50 JSON rows is absurd overhead.

## Alt 3: Hermes + deterministic gates
- **Plan already does this partially:** tail-logger = audit gate, identify-sender = identity gate, send-coverage-message = choke point.
- Gap: script currently takes raw phone from LLM. Should take `(employee_id, proposal_id)` and re-resolve phone from roster.json + verify proposal_id is pending in decisions.log. LLM can't invent a phone, can't send without approval, can't double-send.

## Alt 4 (NEW): Two-phase approval with signed proposal IDs
- Include 4-char code (`#A3F2`) in owner's proposal message.
- Owner replies with code to approve, not "yes."
- Removes "match recent reply by recency/keyword" fuzziness (plan's shakiest component).
- ~1h extra.

## Final recommendation

**Keep Hermes/LLM-driven architecture**, make TWO changes before build:

1. **Change `send-coverage-message` signature** from `(phone, text)` → `(employee_id, proposal_id, text)`. Script re-reads roster.json + decisions.log, refuses to send if they don't reconcile. Closes LLM-invented-phone attack surface.
2. **Add explicit proposal codes.** Owner approves `#A3F2`, not "yes." Fixes approval-tracker fuzziness (architect BLOCKER B1 + devil's R1).

Total added work: ~2-3h. Material risk reduction on 2 highest-severity rows. Ship it.
