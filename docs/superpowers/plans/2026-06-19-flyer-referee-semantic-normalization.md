# Flyer Referee — Semantic Normalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]` tracking.

**Goal:** Stop false manual reviews caused by formatting-only differences (dash/spacing/case/`&`-`and`/`PM`-`p.m.`/`4PM`-`4 PM`/punctuation/accents) in **schedule + descriptive text**, without weakening any safety check (prices, currency, phone digits, business identity, fabricated offers stay strict).

**Architecture:** Add one `_normalize_soft_text` formatting-equivalence layer in `visual_qa.py`, wired **only** into the schedule matcher and the general descriptive-text path. Price/phone/address/identity-gate/fabrication paths are untouched. Principle: *same meaning → pass; different fact → fail.*

**Tech Stack:** Python 3 (`re`, `unicodedata`), pytest. File: `src/agents/flyer/visual_qa.py`; tests: `tests/test_flyer_visual_qa.py`. Authoritative source = `origin/main` (`e704c90`).

**Design doc:** `docs/superpowers/specs/2026-06-19-flyer-referee-semantic-normalization-design.md`
**Drift-check tag:** `extends-Hermes`.
**Test command (Windows git-bash):** `PYTHONPATH="src;src/platform" python -m pytest <path> -v`

**Invariant (every task must preserve):** prices compare cents+currency; phones compare digits; business identity uses the `_is_brand_typo` distance gate; fabricated offer/claim detectors unchanged. The soft layer changes only *punctuation/spacing/case/abbreviation/accents*, never *content* (digits, currency, day tokens, distinct words).

---

## Task 1: Caller audit (verification gate — no production code)

**Files:** read-only audit of `src/agents/flyer/visual_qa.py`; record findings in the plan's review section / a comment.

- [ ] **Step 1: Enumerate every caller of `_normalize_text_for_match`.**
Run: `PYTHONPATH="src;src/platform" python -c "import re; print([l for l in open('src/agents/flyer/visual_qa.py') if '_normalize_text_for_match' in l])"` (or grep). List each call site + its fact class.
- [ ] **Step 2: Confirm the strict paths do NOT derive their value from `_normalize_text_for_match`:**
  - `_price_value_present_in` → uses `_price_cents` + currency regex (not text normalize). Confirm.
  - `_phone_value_present_in` → digits-only. Confirm.
  - `_is_brand_typo` → uses `_normalize_brand_for_match` (separate). Confirm.
  - `_address_value_present_in` → `_normalize_address_for_match` (separate alias normalizer). Confirm (address keeps its own path).
- [ ] **Step 3: Decide wiring (record the decision):** apply `_normalize_soft_text` ONLY in (a) `_schedule_value_present_in` and (b) the general descriptive-text branch of `_value_present_in` (the `else` after the class dispatch). Do NOT modify `_normalize_text_for_match` itself (leave its other uses untouched) unless the audit proves every use is safe.
- [ ] **Step 4: Commit the audit note** (a docstring/comment block at the top of the soft normalizer added in Task 2, or a short note in the plan). No behavior change yet.

Expected outcome: a written confirmation that soft folding reaches only schedule + descriptive text, and that price/phone/identity are structurally unaffected.

---

## Task 2: `_normalize_soft_text`

**Files:** Modify `src/agents/flyer/visual_qa.py` (add after `_normalize_text_for_match`); Test `tests/test_flyer_visual_qa.py`.

- [ ] **Step 1: Write the failing unit tests**
```python
def test_normalize_soft_text_folds_formatting():
    from agents.flyer.visual_qa import _normalize_soft_text as N
    # dashes
    assert N("4 PM–8 PM") == N("4 PM-8 PM") == N("4 PM — 8 PM")
    # ampersand
    assert N("Saturday & Sunday") == N("Saturday and Sunday")
    # pm/a.m. + spacing
    assert N("4 PM") == N("4 p.m.") == N("4PM") == N("4pm")
    # case + whitespace
    assert N("WEEKEND   SPECIALS") == N("Weekend Specials")
    # accents
    assert N("Café") == N("Cafe")

def test_normalize_soft_text_preserves_content():
    from agents.flyer.visual_qa import _normalize_soft_text as N
    assert N("Saturday") != N("Sunday")           # different day
    assert N("4 PM-8 PM") != N("4 PM-9 PM")        # different time
    assert N("Idli") != N("Idli Sambar")           # different content
```
- [ ] **Step 2: Run — expect FAIL** (`_normalize_soft_text` missing).
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_visual_qa.py -k normalize_soft_text -v`
- [ ] **Step 3: Implement** (after `_normalize_text_for_match`):
```python
_SOFT_DASHES = "–—−‐·"  # en, em, minus, hyphen-bullet, middle-dot

def _normalize_soft_text(text: str) -> str:
    """Formatting-equivalence normalizer for SCHEDULE + descriptive text ONLY.

    Builds on _normalize_text_for_match (casefold + whitespace-collapse +
    apostrophe-strip) and additionally folds punctuation/spacing/abbreviation/
    accents that OCR varies — WITHOUT touching content. NEVER used for
    price/phone value comparison (those compare cents/digits) so money/contact
    safety is unaffected; business identity safety remains the _is_brand_typo
    distance gate.
    """
    import unicodedata
    s = _normalize_text_for_match(text)
    # accents/diacritics -> ascii (display unchanged; matching only)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    # ampersand -> "and"
    s = s.replace("&", " and ")
    # dash variants -> hyphen, then collapse spaces around hyphens
    for d in _SOFT_DASHES:
        s = s.replace(d, "-")
    s = re.sub(r"\s*-\s*", "-", s)
    # time abbreviations: "p.m." -> "pm", "a.m." -> "am"; "4 pm" -> "4pm"
    s = re.sub(r"\b([ap])\.m\.?", r"\1m", s)
    s = re.sub(r"(\d)\s+([ap]m)\b", r"\1\2", s)
    # collapse residual whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
```
- [ ] **Step 4: Run — expect PASS** (both tests).
- [ ] **Step 5: Commit** `feat(flyer): _normalize_soft_text formatting-equivalence normalizer (visual_qa)`.

---

## Task 3: Wire soft normalization into schedule + descriptive paths ONLY

**Files:** Modify `src/agents/flyer/visual_qa.py` (`_schedule_value_present_in` ~line 477; the `else` branch of `_value_present_in` ~line 523+); Test `tests/test_flyer_visual_qa.py`.

- [ ] **Step 1: Write the failing test** (schedule en-dash + descriptive accent)
```python
def test_schedule_endash_matches_hyphen_ocr():
    from agents.flyer.visual_qa import _value_present_in, _normalize_text_for_match
    ocr = _normalize_text_for_match("lakshmi's kitchen weekend specials saturday & sunday, 4 pm-8 pm")
    assert _value_present_in(ocr, "Saturday & Sunday, 4 PM–8 PM", schedule_match=True) is True

def test_descriptive_text_amp_and_accent_match():
    from agents.flyer.visual_qa import _value_present_in, _normalize_text_for_match
    ocr = _normalize_text_for_match("grand cafe and grill weekend")
    assert _value_present_in(ocr, "Grand Café & Grill") is True
```
- [ ] **Step 2: Run — expect FAIL** (en-dash schedule + `&`/accent descriptive currently miss).
- [ ] **Step 3: Implement:**
  - In `_schedule_value_present_in`, replace the two `_normalize_text_for_match(...)` calls with `_normalize_soft_text(...)` (both the text and the value), leaving the day/"every week" rewrites intact (they operate on the normalized value).
  - In `_value_present_in`, change the final descriptive branch from:
    ```python
    normalized_value = _normalize_text_for_match(fact_value)
    return _text_value_present_in(normalized_text, normalized_value)
    ```
    to:
    ```python
    soft_text = _normalize_soft_text(normalized_text)
    soft_value = _normalize_soft_text(fact_value)
    return _text_value_present_in(soft_text, soft_value)
    ```
  - Do NOT touch the `phone_match` / `address_match` / `price_match` branches.
- [ ] **Step 4: Run** the new tests (PASS) + the FULL referee suite (no regression):
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_visual_qa.py -q`
- [ ] **Step 5: Commit** `feat(flyer): soft-normalize schedule + descriptive text matching (not price/phone/identity)`.

---

## Task 4: F0176 replay (regression anchor)

**Files:** Test `tests/test_flyer_visual_qa.py`.

- [ ] **Step 1: Write the test** (the exact live failure)
```python
def test_f0176_endash_schedule_replay():
    # F0176 (2026-06-19): fact had en-dash "4 PM–8 PM"; OCR returned hyphen.
    # Must be covered (no "missing required visible fact: schedule").
    from agents.flyer.visual_qa import _value_present_in, _normalize_text_for_match
    ocr = _normalize_text_for_match(
        "lakshmi's kitchen weekend specials saturday & sunday, 4 pm-8 pm "
        "any item $7.99 idli $7.99 90 brybar dr st johns fl +17329837841"
    )
    assert _value_present_in(ocr, "Saturday & Sunday, 4 PM–8 PM", schedule_match=True) is True
```
- [ ] **Step 2: Run — PASS** (after Task 3).
- [ ] **Step 3: Commit** `test(flyer): F0176 en-dash schedule replay regression`.

---

## Task 5: Formatting-equivalence tests (same meaning → pass)

**Files:** Test `tests/test_flyer_visual_qa.py`.

- [ ] **Step 1: Add the matrix** (schedule + descriptive variants that must match)
```python
import pytest
from agents.flyer.visual_qa import _value_present_in as V, _normalize_text_for_match as Nt

@pytest.mark.parametrize("fact, ocr", [
    ("Saturday & Sunday, 4 PM–8 PM", "saturday and sunday, 4 pm-8 pm"),   # en-dash + &/and
    ("Mon–Fri 9 AM–5 PM",            "mon-fri 9 am-5 pm"),                 # em/en dashes
    ("4 p.m. to 8 p.m.",             "4 pm to 8 pm"),                      # p.m. -> pm
    ("Open 4PM",                     "open 4 pm"),                         # 4PM <-> 4 PM
])
def test_schedule_formatting_variants_pass(fact, ocr):
    assert V(Nt(ocr), fact, schedule_match=True) is True

@pytest.mark.parametrize("fact, ocr", [
    ("Grand Café & Grill", "grand cafe and grill"),     # accent + &
    ("WEEKEND  SPECIALS",  "weekend specials"),         # case + spacing
])
def test_descriptive_formatting_variants_pass(fact, ocr):
    assert V(Nt(ocr), fact) is True
```
- [ ] **Step 2: Run — PASS.**
- [ ] **Step 3: Commit** `test(flyer): formatting-equivalence variants pass`.

---

## Task 6: Real-difference-still-fails tests (different fact → fail; safety unchanged)

**Files:** Test `tests/test_flyer_visual_qa.py`.

- [ ] **Step 1: Add the safety matrix**
```python
from agents.flyer.visual_qa import _value_present_in as V, _normalize_text_for_match as Nt, is_own_brand_variant

def test_schedule_wrong_day_or_time_fails():
    assert V(Nt("friday & saturday, 4 pm-8 pm"), "Saturday & Sunday, 4 PM–8 PM", schedule_match=True) is False
    assert V(Nt("saturday & sunday, 4 pm-9 pm"), "Saturday & Sunday, 4 PM–8 PM", schedule_match=True) is False

def test_price_strict_unchanged():
    assert V(Nt("dosa $9.99"), "$7.99", price_match=True) is False          # wrong amount
    assert V(Nt("dosa 7.99"),  "$7.99", price_match=True) is False          # currency dropped

def test_phone_strict_unchanged():
    assert V(Nt("call +1 732 983 7842"), "+17329837841", phone_match=True) is False  # one wrong digit

def test_descriptive_word_boundary_unchanged():
    assert V(Nt("idlisugar special"), "Idli") is False                     # no substring false-positive

def test_business_identity_gate_unchanged():
    # a structurally different business must NOT be an own-brand variant
    from schemas import FlyerProject, FlyerLockedFact
    from datetime import datetime, timezone
    proj = FlyerProject(project_id="F0001", status="intake_started", customer_phone="+17329837841",
        created_at=datetime(2026,6,19,tzinfo=timezone.utc), updated_at=datetime(2026,6,19,tzinfo=timezone.utc),
        original_message_id="m", raw_request="x",
        locked_facts=[FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile")])
    assert is_own_brand_variant("Triveni Indian Cafe & Bakery", proj) is False
```
- [ ] **Step 2: Run — PASS** (these prove safety is intact — wrong fact still fails).
- [ ] **Step 3: Commit** `test(flyer): real-difference + safety-strict cases still fail`.

---

## Task 7: Full referee suite + full build

**Files:** test-only.

- [ ] **Step 1:** `PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_visual_qa.py -q` → all pass (incl. pre-existing brand-typo/price/phone/address tests — proving no regression).
- [ ] **Step 2:** Full suite `PYTHONPATH="src;src/platform" python -m pytest tests/ -q` → 0 failed.
- [ ] **Step 3: Commit** (if any incidental fixups).

---

## Task 8: Codex review

- [ ] **Step 1:** Codex review the branch diff vs `origin/main`. Focus: (a) soft folding reaches ONLY schedule + descriptive paths; (b) price/phone/address/identity-gate/fabrication strictness unchanged; (c) `_normalize_soft_text` never equates different content (days/times/amounts/digits/distinct words); (d) word-boundary integrity preserved.
- [ ] **Step 2:** Fix any BLOCKER/MAJOR; re-review until CLEAN.

---

## Self-Review (writing-plans)
**Spec coverage:** caller audit→T1; `_normalize_soft_text`→T2; schedule/descriptive wiring only→T3; F0176 replay→T4; formatting-equivalence pass→T5; real-difference fail→T6; full suite→T7; Codex→T8. ✓
**Placeholder scan:** all test/impl code is concrete. ✓
**Type consistency:** `_normalize_soft_text` / `_value_present_in` / `_schedule_value_present_in` names consistent across tasks. ✓

## Out of scope / deferred
- The **secondary flat-degrade** (F0176 shipped flat) — per operator: do NOT chase now. Fix schedule normalization first, revalidate F0176; pursue the import-order/fallback issue only if it still ships flat.
- No new flag; applies referee-wide (correctness fix), but only loosens *formatting*, never content.

## Post-build (operator-gated)
PR → CI → Codex → merge → deploy → re-validate F0176 (should now ship, not manual). Then combo + dessert briefs.
