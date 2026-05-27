**Drift-check tag:** extends-Hermes

# Flyer24 Batch - Sample Prompt Lexical Gaps (2026-05-27)

## Hermes-first checklist
1. WhatsApp ingress, sender identity, and router execution -> [Hermes]
2. Intercept decision scaffolding/audit emission -> [Hermes]
3. Flyer product policy for detecting explicit sample-idea requests -> [net-new]
4. Customer-facing sample-idea response path (`trigger_flyer_intake`, `start_source=sample_idea`) -> [Hermes]
5. Regression corpus for phrase routing -> [net-new]

Net-new scope only: phrase detection policy + tests.

## Batch issues (6)
1. `need flyer caption ideas` not detected as sample-prompt request.
2. `give me promo lines for my poster` not detected.
3. `example flyer text please` not detected.
4. `show me some template ideas` not detected.
5. `i need sample ad copy` not detected.
6. `need prompt ideas for ads` not detected.

## Implementation
- Add RED routing test coverage for these phrase forms.
- Extend `_SAMPLE_PROMPT_REQUEST` to cover `caption/captions`, `copy`, `line/lines`, `text`, `template/templates` lexical variants in sample-idea intent contexts.
- Keep preference commands (`don't show sample prompts`) unaffected.

## Verification
- `python3 -m py_compile` on touched files.
- Focused pytest for sample-prompt routing tests.
- `git diff --check`.

Risk: low (read-only routing heuristic + tests; no payment/account/quota/manual-close mutation).
