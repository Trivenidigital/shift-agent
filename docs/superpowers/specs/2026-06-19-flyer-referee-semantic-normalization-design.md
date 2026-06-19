# Flyer Referee — Semantic Normalization for Formatting-Only Variants — Design

**Date:** 2026-06-19
**Status:** Design for review (no implementation until the plan is approved).
**Drift-check tag:** `extends-Hermes` — refines the existing `visual_qa` fact-matching normalization; no new storage, no schema change, no new flag, no Hermes-convention change.

---

## 1. Problem (live: F0176)

The referee (`visual_qa.run_visual_qa`) sent a premium flyer to manual on:
`missing required visible fact: schedule`.

Root cause: the schedule fact value is `"Saturday & Sunday, 4 PM–8 PM"` (**en-dash** `–`); the OCR read it back as `"Saturday & Sunday, 4 PM-8 PM"` (**hyphen** `-`). The shared normalizer `_normalize_text_for_match` does only *casefold + whitespace-collapse + apostrophe-strip* — it does **not** fold dash variants — so the exact-string match failed and the (correctly-rendered) schedule was reported missing → fail-closed → manual.

**Principle to enforce:** *same meaning → pass; different fact → fail.* A formatting-only difference (dash variant, spacing, case, `&`/`and`, `PM`/`p.m.`, `4PM`/`4 PM`, punctuation) must not cause a false manual review. **No safety check may be weakened.**

## 2. How the referee matches today (verified in `visual_qa.py`)

`_value_present_in(text, value, *, phone_match, address_match, schedule_match, price_match)` dispatches by fact class:

| Class | Matcher | Normalization today | Verdict |
|---|---|---|---|
| Phone | `_phone_value_present_in` | digits-only inside a contiguous OCR digit run | **strict — keep** |
| Price / offer amount | `_price_value_present_in` | parse → **cents**; require currency when fact has `$/₹` | **strict — keep** |
| Address | `_address_value_present_in` | `_normalize_address_for_match` token aliases (Saint↔St, Drive↔Dr…) + word boundary | already semantic |
| Schedule | `_schedule_value_present_in` | `_normalize_text_for_match` + a few day/"every week" rewrites | **too literal — fix** |
| Other descriptive text (title, item names) | `_text_value_present_in` | `_normalize_text_for_match` + word boundary | **fix (bounded)** |
| Business identity (brand) | `_text_value_present_in` **gated by `_is_brand_typo`** | normalize + Levenshtein/token gate (own-brand typo = warn; different = block) | **gate stays strict — keep** |

The gap is entirely in `_normalize_text_for_match` (no dash/punctuation/abbreviation folding), which feeds the **schedule** and **descriptive-text** paths.

## 3. Per-class strictness table (the contract)

| Fact class | Comparison basis | Formatting normalization allowed? | Rationale |
|---|---|---|---|
| **Price** | exact **cents** + currency-present | NO (value-strict) | wrong amount / dropped `$` must block |
| **Phone** | exact **digits** | NO (value-strict) | wrong digit must block |
| **Business identity** | normalized text **+ `_is_brand_typo` gate** | spelling/whitespace only, via the existing typo gate — **a structurally different business still blocks** | identity is safety-critical |
| **Fabricated offer / claim** | existing fabrication detectors | NO | a claim not backed by locked facts must block |
| **Schedule / date / time** | normalized text (semantic) | **YES** — dash, spacing, case, `&`/`and`, `PM`/`p.m.`, `4PM`/`4 PM`, punctuation | formatting-only; a different day/time still differs token-wise → still fails |
| **Other descriptive text** (campaign title, item names) | normalized text + word boundary | **YES (bounded)** — same formatting folds, word-boundary preserved so `Idli`≠`Idlisugar` | formatting-only |

## 4. Soft-text normalizer boundary

Introduce a dedicated **`_normalize_soft_text(text)`** layer used **only** by the schedule + descriptive-text classes (not by price/phone, which never route through text normalization for their value). It applies, on top of the current casefold + whitespace-collapse + apostrophe-strip:

- **Dash folding:** `–` (en), `—` (em), `−` (minus), `·` (middle dot) → `-`; collapse spaces around dashes (`4 pm – 8 pm` → `4 pm-8 pm`).
- **Abbreviation / time equivalence:** `p.m.`→`pm`, `a.m.`→`am`; `4pm`↔`4 pm` (insert/normalize the space between a digit and `am/pm`); optionally `–`/`to` between two times.
- **Ampersand:** `&` ↔ `and` (normalize to one form on both sides).
- **Punctuation-only:** strip trailing/standalone punctuation that OCR adds/drops (commas, periods not part of a token), normalize multiple spaces.
- **Accents/diacritics:** fold to ASCII for matching (display unchanged).

**Explicit non-goals (must NOT normalize):** digits, currency symbols/amounts, day-of-week tokens, distinct words. The folds above change *punctuation/spacing/case/abbreviation*, never *content* — so "Saturday"≠"Sunday", "4 PM"≠"5 PM", "$7.99"≠"$9.99" still fail.

Business identity keeps using `_text_value_present_in` **and** `_is_brand_typo`; the soft folds may apply to the *presence* check, but the **typo-distance gate remains the safety control** (a different business name fails the gate regardless of formatting).

## 5. Caller audit requirement (mandatory, pre-implementation)

Before changing any shared function, the plan **must** enumerate every caller of `_normalize_text_for_match` and confirm none is a strict gate that the soft folds could weaken:
- Confirm `_price_value_present_in` / `_phone_value_present_in` derive their value from cents/digits, **not** from `_normalize_text_for_match` (so they are unaffected).
- Confirm `_is_brand_typo` uses its own `_normalize_brand_for_match` (separate) so identity strictness is independent.
- Decide placement: prefer adding `_normalize_soft_text` and calling it **only** from `_schedule_value_present_in` and the descriptive-text path — leaving `_normalize_text_for_match` untouched where any strict-ish use exists. Only broaden the shared base if the audit proves it is safe everywhere.

This audit is the gate that guarantees "no safety check weakened."

## 6. F0176 replay case (regression anchor)

Test: schedule fact `"Saturday & Sunday, 4 PM–8 PM"` (en-dash) vs OCR text `"saturday & sunday, 4 pm-8 pm"` (hyphen) → **must be covered** (no `missing schedule`). Plus the exact F0176 referee inputs as a stored fixture so the regression can never silently return.

## 7. Tests — formatting variants that MUST pass (same meaning → pass)

For schedule + descriptive text, each pair must match after normalization:
- `4 PM–8 PM` (en-dash) ≡ `4 PM-8 PM` (hyphen) ≡ `4 PM — 8 PM` (em-dash, spaced).
- `Saturday & Sunday` ≡ `Saturday and Sunday`.
- `4 PM` ≡ `4 p.m.` ≡ `4PM` ≡ `4pm`.
- case/spacing: `WEEKEND SPECIALS` ≡ `Weekend Specials`; `4 PM-8 PM` ≡ `4 pm - 8 pm`.
- accents: `Café` ≡ `Cafe` (descriptive text).

## 8. Tests — real fact differences that MUST still fail (different fact → fail)

- **Schedule:** `Saturday & Sunday` vs OCR `Friday & Saturday` → fail; `4 PM–8 PM` vs `4 PM–9 PM` → fail.
- **Price (unchanged):** `$7.99` vs `$9.99` → fail; `$7.99` vs `7.99` (currency dropped where required) → fail.
- **Phone (unchanged):** one wrong digit → fail.
- **Business identity (unchanged):** a structurally different business (`Triveni Indian Cafe`) vs `Lakshmi's Kitchen` → `_is_brand_typo`=False → block.
- **Fabricated offer (unchanged):** fabricated price/offer claim → block.
- **Word-boundary integrity:** `Idli` must not match `Idlisugar` even with folds.

## 9. Scope & safety summary
- Goal: **dangerous-leak stays 0**, false manual reviews from formatting-only differences drop to ~0.
- No new flag; no schema/state change; applies to all flyers (this is a referee-correctness fix, not a scoped feature) — but it can only ever make matching *more lenient on formatting*, never on content, so it does not affect the scoped Fix C rollout posture.
- Money / contact / identity / fabrication remain exactly as strict as today.

## 10. Secondary finding (SEPARATE — F0176 shipped flat, not v2)

**Status: open, separate from this normalization work. Confirmed NOT caused by the schedule normalization.**

F0176's *shipped* preview was the **flat** overlay, not the Fix C v2 editorial render — even though `render_premium_overlay` re-renders the v2 editorial cleanly over the same raw background offline.

- It is **not** caused by the schedule issue: `render_premium_overlay`'s own coverage compares its ink log (en-dash) against the fact (en-dash) → matches → it does not raise on schedule.
- A **reproducible import-order anomaly** exists: importing `flyer_premium_overlay` → `flyer_visual_qa` → `flyer_render` leaves `flyer_render` **missing the late-defined `_premium_overlay_enabled`** (a partial-module/circular-import edge). If that path were hit at runtime, `_apply_critical_text_overlay` would `NameError → except → degrade to flat`.
- **But** the gateway (`generate-flyer-concepts`) imports `flyer_render` **first** (line 63) and explicitly imports `_deterministic_recovery_enabled` from it (which would crash if partial), and F0176 *did* process — so the gateway's `flyer_render` is complete. So this anomaly is **not yet proven to be the live cause**.

**Next step for this finding (not in this plan):** a focused end-to-end repro — run `generate-flyer-concepts` for an F0176-class brief and capture which overlay path is taken + any degrade log line. Candidate fixes if confirmed: define `_premium_overlay_enabled` / `_deterministic_recovery_enabled` earlier in `flyer_render` (before `_apply_critical_text_overlay`) and/or harden `_apply_critical_text_overlay`'s helper resolution, plus a regression test asserting `flyer_render` exposes these symbols under any import order.

**Sequencing:** fix the schedule normalization first (it unblocks delivery regardless). Re-validate F0176: if it then ships the v2 editorial, the flat-degrade did not recur; if it ships flat, pursue this finding with the focused repro.
