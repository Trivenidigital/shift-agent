# Flyer Studio — Accretion Timeline (archaeology, 2026-07-02)

**Drift-check tag:** Hermes-native (history documentation; no infrastructure).

## Hermes-first analysis
| Domain | Hermes skill found? | Decision |
|---|---|---|
| Git history documentation | n/a | document only |

Directive: post-freeze task 1 (operator, 2026-07-02). Sources: git log origin/main
(372 flyer commits since 2026-05-15), tasks/lessons.md, memory records.

## THE HEADLINE FINDING — the "restoration" premise is backwards

**There is no week-1 integrated Eden to restore.** The git record shows the OPPOSITE
sequence from the directive's narrative:

- **2026-05-15 (day one):** Flyer Studio was born DETERMINISTIC — Pillow renderer +
  text overlay (day-one commits: "prefer latin font for overlay text"). The image
  model of that era could not render exact text at all.
- **2026-05-30:** the "100% fail" root cause confirmed it: every integrated attempt
  failed visual QA on exact text (tasks lesson + memory). Deterministic overlay was
  the WORKAROUND for a model limitation, not a bureaucratic demotion.
- **2026-06-14/15:** gemini-3.1 changed the physics (20/20 pass@1 on integrated
  full-poster) -> integrated generation became the PRIMARY path (#489) with
  verify-and-retry safety nets. **It is still the primary path today** — F0193/F0195/
  F0196's previews (the flyers judged unshippable this week) ARE the one-call
  integrated architecture's live output.
- The premium composer (#517-#530) was never a demotion of the model; it was a scoped
  premium-quality branch for ONE number on top of the integrated primary.

**Corrected frame for v2:** the integrated+verify+reroll core the directive wants
already exists and runs every day. v2's real work is SUBTRACTION — deleting the
accreted rungs/gates around that core and fixing its two live weaknesses
(extraction poisoning; verifier reliability) — not resurrection.

## Accretion ledger (component -> date -> the failure it answered)

| # | Component | Landed | Failure it answered |
|---|---|---|---|
| 1 | Deterministic Pillow renderer + text overlay | 05-15 | image models could not render exact text (era constraint) |
| 2 | Text manifests + send gating | 05-16 | delivered files diverging from approved content |
| 3 | Visual QA (vision readback) | 05-19..27 | placeholders/wrong facts reaching customers (F0105 class) |
| 4 | Direct full-poster contract (bare opt-in) | 05-18..06-05 | overlay quality unacceptable for simple promos |
| 5 | Identity grounding + hard-fact QA (slice 1) | 06-03 | hallucinated phone/address in integrated eval |
| 6 | Integrated PRIMARY + kill-switch + fabrication referee + retry x2 | 06-14/15 | gemini-3.1 made integrated viable; needed fail-closed nets |
| 7 | Deterministic recovery (Fix C re-render) | 06-18/19 | integrated text-fidelity failures went to manual (customer stall) |
| 8 | Deterministic-first for fact-dense menus | 06-20 | dense menus: model text corrupted items/prices |
| 9 | Creative Director v2 (message-first poster) | 06-21 | composition quality (bottleneck moved to message quality) |
| 10 | Narrative referee | 06-24/25 | filler/restated campaign copy |
| 11 | Premium Poster composer ladder (#517-#524) | 06-29/30 | F0190: fact-correct but unreadable/weak-hierarchy premium output |
| 12 | Managed integration + footer fix (#527/#529) | 07-01 | phone clip; owner-review path needed the branch |
| 13 | Production hardening (#530) | 07-02 | 2026-07-02 review findings (fact mutation, observability, finals crop) |

Pattern confirmed as charged: each layer rationally answered the previous layer's
failure; nothing gated on "would an owner pay for this," so quality regressions
never blocked. The complexity-budget rule (below) is the antibody.

## Standing rules recorded (operator directive, 2026-07-02)

1. **Complexity budget:** no gate/stage/model call enters the pipeline without a
   labeled failure example it demonstrably prevents; regeneration preferred over
   machinery. Applies to directives too.
2. **Budget:** OpenRouter calls tagged by workstream; $200/month cap;
   weekly cost-per-accepted-flyer report. (Implementation for workstream #0:
   dedicated OpenRouter key = zero pipeline code.)
