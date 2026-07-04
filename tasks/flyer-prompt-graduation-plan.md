# Flyer Prompt-Layer Graduation Plan — harness wins → production code

**Drift-check tag:** extends-Hermes

**Date:** 2026-07-04 · **Status:** DRAFT — implementation starts per operator sequence (behind SriniY's accent-vs-full call; ahead of everything else)

**New primitives introduced:** style-register prompt blocks (data, not machinery); typeset-spec text assembly; interpretive `occasion` extraction field; theme-intensity parameter; three QA hardening checks. No new storage, no new services, no new approval flows.

## Hermes-first capability checklist

| Step | Tag | Net-new LOC |
|---|---|---|
| 1. Author style/occasion/intensity prompt blocks | `[net-new]` — pure data module mirroring the deployed `campaign_scene_prompts` precedent | ~250 (mostly prompt text) |
| 2. LLM gateway call for extraction + occasion field | `[Hermes]` — existing OpenRouter gateway via the extraction_v2 seam; schema extension only | ~20 |
| 3. Image generation | `[Hermes]` — existing render.py OpenRouter path; prompt text changes only | 0 |
| 4. Vision QA readback | `[Hermes]` — existing visual_qa OCR seam; new rules join the existing rule set | ~80 |
| 5. Typeset-spec prompt assembly | `[net-new]` — replaces the flat facts block inside the existing `_image_prompt`; flag-gated | ~120 |
| 6. Gibberish/vocab screen at candidate selection | `[net-new]` — small text screen inside the existing best-of-N director | ~40 |
| 7. Tagline judging (referee audition) | `[Hermes]` — in-tree `flyer_narrative_quality` already built + tested; audition wiring only (or deletion) | ~15 or −638 |

Hermes ecosystem verdict: no skill in the hub (productivity/*, mcp/native-mcp surveyed per skills-roadmap) covers poster art-direction prompt authoring; in-repo data modules mirroring deployed conventions are correct. 5 of 7 steps ride existing substrate.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/render.py` (`_image_prompt` at 2221, `_poster_copy_block` at 1195, `_campaign_scene_block_for_project` at 2078, `_render_model` ppv1 gates at 4883-4896) before proposing the assembly changes — the graduation edits the existing assembler, adds no new one.
- ✅ Read `src/agents/flyer/extraction_v2.py` (EXTRACTION_MODEL, `_SYSTEM_PROMPT`, parity guard at `value_has_source_parity`) and `src/agents/flyer/extraction_seam.py` before proposing the occasion field — interpretive exemption is an explicit exclusion, facts stay guarded.
- ✅ Read `src/agents/flyer/visual_qa.py` (`_item_price_pair_blockers` region, forbidden-text handling, `run_visual_qa` flow) during WS2 before proposing the QA hardening batch.
- ✅ Read `src/agents/flyer/campaign_scene_prompts.py` (Family A live selector) via the subsystem inventory before choosing it as the template-module precedent.
- ✅ Read `src/agents/flyer/premium_poster_v1_director.py` (`compose_best_of_n`, `build_textless_food_prompt`) via the inventory before siting the candidate-selection screen.

## Evidence base (harness, 2026-07-03/04, all quarantined + audited)

Register R1→R3.5 arc: festive-premium crowned (SriniY); typeset-spec decomposition fixed run-on headlines + key:value leaks; occasion detection 6/6 with fail-neutral held on both ambiguous baits; intensity dial accent/full both first-try clean at composite 8.0. Leak law (4 generations observed): every new prompt vocabulary leaks on first outing → vocabularies ship WITH forbidden-substrings entries at authoring time (standing rule). QA-blind classes with exhibits: gibberish text ("Degional $4.99 inide", "Huge Dunchanuf"), near-miss spelling ("FRIDAYS AND SATURDAY"), offer-wording drift (invented "COMBO"/"all month", dropped "sweets box").

## Commits (each: tests-first, one subagent reviewer pass, CI green)

1. **style_registers.py** — data module: festive-premium (default), pure-festive, festive-modern, clean-modern, premium-dark; occasion vocabularies (july4/diwali/ramadan/thanksgiving) with authored forbidden-substrings lists; intensity levels (accent default, full). Selector: register × occasion × intensity → prompt block. Flag: `FLYER_STYLE_REGISTERS` + allowlist (ppv1 semantics: empty = OFF, fail-closed).
2. **Typeset-spec assembly in render.py** — flag-gated replacement of the flat `_poster_copy_block` facts list with numbered-strings + separate role-instructions sections; strict-rules ban list ingests register vocabulary. Flag-off = byte-identical (pinned by test).
3. **Occasion field** — extraction_v2 schema + prompt gain `occasion` (explicit interpretive exemption from parity; fail-neutral to None; unknown labels → None); plumbed to the register selector. Golden fixtures extended (4 occasion + 2 ambiguous briefs).
4. **QA hardening batch** — (a) gibberish/dictionary-sanity screen + spec-vocabulary forbidden-substrings at best-of-N candidate selection (director) and in visual_qa; (b) offer-wording check: offer NOUNS/QUALIFIERS compared against locked pricing_structure, not just digits/dates.
5. **Referee audition** — wire `flyer_narrative_quality.select_campaign_narrative` as the CD-v2 headline-candidate judge (copy re-scope: extend copy archetypes under the zero-claims contract) OR delete the module in the same commit. Audition tests decide; no carry (operator ruling #1).
6. **Planner removal** — delete `creative_planner.py` (+ inert call sites in facts.py) unless a labeled failure surfaces during commits 1–5 that it would prevent (operator ruling #3).

## Rollout

Deploy flag-off (byte-identity pinned) → enable `FLYER_STYLE_REGISTERS` for +17329837841 → one live brief through the full stack → SriniY eyeball vs the harness renders → then the allowlist-semantics unification PR (pre-design-partner blocker, operator ruling #2) → then the design-partner send.

## Out of scope

Copy-vocabulary expansion beyond the referee audition (queued as CD-v2 archetype extension); guided-intake activation; BSP paperwork; gpt-5.4 premium tier.
