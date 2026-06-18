# Flyer Deterministic-Recovery Routing — Design

**Date:** 2026-06-18
**Status:** Approved direction (operator 2026-06-18); design for review before implementation plan.
**Drift-check tag:** `extends-Hermes` — adds custom recovery-routing + a brand-typo recoverability rule on top of the existing (Hermes-invoked) flyer render/QA primitives. No new storage, no new audit chokepoint, no Hermes-convention violations.

---

## 1. Problem (grounded in a live case: F0174, 2026-06-18)

A customer (+17329837841) sent a 6-item "Weekend Specials, Any item $7.99" brief. The pipeline went **straight to manual** ("I couldn't finish this automatically"), never producing a preview.

Traced end-to-end (decisions.log + QA sidecar + recovery code + a live simulation against F0174's real project data):

1. The **integrated gemini render** ran (`FlyerIntegratedAttempted`) and produced `F0174-C1-preview.png`.
2. `visual_qa` read it back and **correctly blocked** it — gemini garbled the text:
   - OCR read: `LAKSMI'S KITCHEN` (misspelled), `UTMM`, duplicated `VADA`, four prices dropped.
   - Blockers: `visible wrong business/brand: Laksmi'S Kitchen`, `missing required visible fact: business_name`, `item price mismatch: item:1 expected Dosa $7.99` (+ item:3/4/5).
3. `classify_flyer_qa_for_autorepair` (`src/agents/flyer/recovery.py:174`) matched crude string tokens in `_AUTOREPAIR_TRUST_RISK_TOKENS` (`"wrong business"`, `"price mismatch"`) → returned `hard_stop / customer_trust_risk` (`recovery.py:182`) → routed to `manual_edit_required`.

**The failure mode (gemini can't reliably spell the brand / render exact prices) is exactly what the deterministic Fix C overlay solves** — the overlay draws every fact from `locked_facts` (correct by construction). But Fix C lives in the deterministic-overlay render path, and the integrated-failure recovery ladder routes text-fidelity / wrong-brand failures to manual **before** any deterministic re-render. The lever exists but is structurally bypassed upstream (§9c).

Confirmed by live simulation on the box against F0174's stored project:
- `item:0:price` … `item:5:price` **are locked** → the price-mismatch blockers are already in the recoverable class.
- `_is_brand_typo("Laksmi'S Kitchen", "Lakshmi's Kitchen") = True` → it is an own-brand spelling variant, **not** a different business.
- `classify_flyer_qa_for_autorepair(...) = hard_stop / customer_trust_risk` → the path that sent it to manual.

## 2. Core principle (operator, 2026-06-18)

> Integrated render fails on exact text → if facts are locked and the defect is recoverable → deterministic recovery path → Fix C premium overlay if enabled → flat overlay if Fix C cannot fit or is off → manual only if deterministic recovery also fails or the defect is truly dangerous.

And specifically: **do not treat all wrong-brand blockers the same** — a *truly different business* hard-blocks; an *own-brand spelling variant* is a text-fidelity failure that the deterministic overlay fixes.

## 3. Hermes-first analysis

| Step | Hermes skill found? | Decision |
|---|---|---|
| Inbound brief → skill dispatch → render invocation | yes — Hermes substrate (cf-router + skill chain) | use it (unchanged) |
| Image generation (integrated + textless background) | none (OpenRouter via flyer render primitives) | reuse existing `render_concept_previews` / `_render_model` |
| Vision read-back QA | none (flyer `visual_qa`) | reuse existing `run_visual_qa` |
| Deterministic text overlay (Fix C) | none (flyer `_apply_critical_text_overlay`) | reuse existing |
| Recoverable-vs-dangerous QA partition | none | reuse existing `_qa_failed_exact_text_recoverable` (extend for brand-typo) |
| Own-brand-variant vs different-business discrimination | none | reuse existing `visual_qa._is_brand_typo` |
| **Recovery-ladder routing decision** | none | **net-new** (the only genuinely new logic) |

awesome-hermes-agent ecosystem check: no skill covers "route a failed integrated render to a deterministic re-render based on a recoverable/dangerous blocker partition." This is per-agent business logic. **Verdict:** one net-new routing rung + one recoverability-rule extension; everything else is reuse.

## 4. Drift-check — what already exists vs net-new

Authoritative source = `origin/main` (reconciled to equal the deployed box, `1166d16`).

**Already exists (reuse — do NOT reinvent):**
- `_qa_failed_exact_text_recoverable(reports, *, locked_fact_ids=None)` — generate-flyer-concepts ("Operator Option 4", 2026-06-15). Partitions recoverable (missing fact, missing item count, duplicate/near-duplicate item, `visible text defect reported by QA:`, `inferred item not rendered:`, **item price mismatch when `item:N:price` is locked**) vs dangerous (fabricated price/offer, unverified phone). Returns True only if EVERY blocker is recoverable.
- `_qa_failed_has_fabrication(reports)` — fabrication detector (`fabricated price visible:`, `fabricated offer claim visible:`).
- `_is_brand_typo(extracted, project_brand)` — `visual_qa.py:1410` (operator 2026-05-28). AND-of-3 gate: edit-distance ≤2 AND token-overlap ≥0.5 AND (common-prefix ≥4 OR overlap ≥0.75). `_project_business_name(project)` — `visual_qa.py:1440`.
- Mode-2 render path (gemini **textless background** + `_apply_critical_text_overlay` = Fix C) — `render.py:_render_model` lines ~3861–3903; reached when `_background_only_eligible(project)`.
- `FlyerIntegratedFellBackDeterministic` audit event (already emitted for the provider-error deterministic fallback, FIX 7).
- Fix C premium overlay + its fit-or-flat degrade (follow-up #1, PR #495) — `_apply_critical_text_overlay` degrades to flat when premium can't fit.

**Net-new (this design):**
1. A **deterministic-recovery rung** in generate-flyer-concepts, gated by a new flag, that intercepts recoverable integrated text-fidelity failures and re-renders deterministically before the existing classify→hard_stop→manual ladder.
2. An **extension to the recoverable partition**: a `visible wrong business/brand: X` blocker is recoverable **iff `_is_brand_typo(X, registered_brand)`** (own-brand variant); a different business stays dangerous.
3. A **`force_background_only` parameter** so the re-render uses mode 2 (textless bg + Fix C overlay) even for an integrated-eligible project.

## 5. Architecture

### 5.1 The three render modes (context)
- **Mode 1 — integrated** (`_integrated_poster_eligible`, gated by `FLYER_ALLOW_INTEGRATED_POSTER=1`): gemini composes the full poster incl. text. F0174's failing path.
- **Mode 2 — background-only + overlay** (`_background_only_eligible`, the complement of mode 1): gemini renders a **textless** background; `_apply_critical_text_overlay` draws ALL text (Fix C premium, or flat if Fix C off / can't fit). **This is where correct-by-construction text lives.**
- **Mode 3 — pure deterministic** (`model="deterministic-renderer"` → `_render`): pure-Pillow flat template; no gemini background, no Fix C. The panic/kill-switch output. **Not** the recovery target (lower quality than mode 2).

The recovery must reach **mode 2**, not mode 3.

### 5.2 The new rung (placement + logic)
Inserted in generate-flyer-concepts **after** the (flag-OFF) Slice 2 premium-repair block and **before** the `classify_flyer_qa_for_autorepair` ladder (≈ line 1315 on origin/main):

```
deterministic_recovery_eligible =
    failed_qa
    and _deterministic_recovery_enabled(current)        # FLYER_DETERMINISTIC_RECOVERY==1 AND allowlist match
    and integrated_path_attempted                        # only after an integrated (mode-1) attempt
    and not _qa_failed_has_fabrication(failed_qa)         # fabrication never recovers here
    and _qa_failed_exact_text_recoverable(               # the (extended) recoverable partition
            failed_qa, locked_fact_ids=locked_fact_ids, project=current)

if deterministic_recovery_eligible:
    re-render MODE 2: render_concept_previews(current, asset_dir, model=draft_model,
                      quality=..., concept_count=..., force_background_only=True)
    qa_reports = run_visual_qa(...); failed_qa = [r for r in qa_reports if r.status != "passed"]
    if not failed_qa:
        emit FlyerIntegratedFellBackDeterministic(reason="qa_text_fidelity")
        # proceed into the normal persist/ship flow (premium Fix C if FLYER_PREMIUM_OVERLAY on, else flat)
    else:
        # deterministic recovery ALSO failed → fall through to the EXISTING manual path with the new blockers
```

Everything not eligible (fabrication, different-business, dependency failure, unrecoverable, or flag/allowlist off) falls through to **today's unchanged** ladder.

### 5.3 Gating / scope (operator decision 2026-06-18)
- New flag **`FLYER_DETERMINISTIC_RECOVERY=1`** (independent of `FLYER_PREMIUM_OVERLAY`).
- Scope reuses **`FLYER_PREMIUM_OVERLAY_ALLOWLIST`** (e.g. `+17329837841`). `_deterministic_recovery_enabled(project)` mirrors the existing `_premium_overlay_enabled` / `_premium_repair_enabled` shape: flag `=="1"` AND (allowlist empty ⇒ on, else `_normalize_sender(project.customer_phone) in allowlist`).
- Flag OFF ⇒ the rung is skipped ⇒ **byte-identical** to today.
- Independence: `FLYER_DETERMINISTIC_RECOVERY` controls *routing to deterministic recovery*; `FLYER_PREMIUM_OVERLAY` independently controls *premium Fix C vs flat overlay* inside mode 2. With recovery on + overlay off, recovered flyers ship the flat overlay.

### 5.4 Brand-typo recoverability extension
`_qa_failed_exact_text_recoverable` gains a `project` keyword parameter (used only to resolve the registered brand for the brand-typo check; default `None` ⇒ a brand blocker is treated as dangerous, preserving today's behavior for existing callers). For a `visible wrong business/brand: <name>` blocker:
- Parse `<name>` (reuse visual_qa's existing brand-blocker regex).
- Recoverable **iff** `is_own_brand_variant(<name>, project)` — a new thin **public** wrapper in `visual_qa.py` over `_is_brand_typo(<name>, _project_business_name(project))`.
- Otherwise dangerous → poisons the set → manual (unchanged).

This implements "own-brand spelling variant → recoverable; truly different business → hard-block" using the discriminator the operator already approved on 2026-05-28.

### 5.5 Forcing mode 2 (`force_background_only`)
A new keyword-only `force_background_only: bool = False` threaded `render_concept_previews → _render_model` and into the prompt builder (`build_image_generation_prompt`, which branches on `_background_only_eligible`). When True, `_render_model` skips the mode-1 integrated branch and takes the textless-bg + `_apply_critical_text_overlay` branch; the prompt builder emits the textless-background contract. Default `False` ⇒ no change to any existing caller.

## 6. Recoverable vs dangerous partition (final, per operator spec)

| Blocker | Class | Why |
|---|---|---|
| `missing required visible fact: …` (business_name, item:N:name, schedule, location, …) | recoverable | overlay redraws from locked_facts |
| `missing required visible item count: …` | recoverable | overlay renders all items |
| `duplicate item visible:` / `near-duplicate item visible:` | recoverable | overlay renders each item once |
| `visible text defect reported by QA:` (misspelling/garble) | recoverable | overlay redraws exact text |
| `inferred item not rendered:` | recoverable | overlay renders every item |
| `item price mismatch: item:N …` **with `item:N:price` locked** | recoverable | overlay draws the customer's own locked price |
| `item price mismatch:` **without** a locked `item:N:price` | **dangerous** | no authoritative price to draw |
| `visible wrong business/brand: X` **where `_is_brand_typo(X)` = True** | recoverable (new) | own-brand spelling variant; overlay draws registered name |
| `visible wrong business/brand: X` **where `_is_brand_typo(X)` = False** | **dangerous** | truly different business |
| `fabricated price visible:` / `fabricated offer claim visible:` | **dangerous** | not backed by locked_facts |
| `unverified phone number visible:` | **dangerous** | a phone that is not the registered one |
| any unrecognized blocker | **dangerous** | conservative default |

A single dangerous (or unrecognized) blocker poisons the set → the rung is skipped → existing manual path.

## 7. Data flow

```
brief → cf-router → generate-flyer-concepts
  → MODE 1 integrated render (gemini)               [FLYER_ALLOW_INTEGRATED_POSTER=1]
  → run_visual_qa → failed_qa
      ├─ (Slice 2 premium repair: FLAG OFF — skipped)
      ├─ NEW: deterministic-recovery rung           [FLYER_DETERMINISTIC_RECOVERY=1 + allowlist]
      │     if recoverable & not fabrication:
      │         MODE 2 re-render (textless bg + Fix C overlay) → run_visual_qa
      │             pass → SHIP (premium Fix C, or flat if overlay off / can't fit)
      │             fail → ↓ existing manual path
      └─ existing classify_flyer_qa_for_autorepair ladder (hermes regenerate / hard_stop / manual)
```

## 8. Error handling & safety
- The mode-2 re-render is wrapped; any exception → fall through to the existing manual path (never crash, never ship unverified).
- The recovery output is **always** re-verified by `run_visual_qa` before shipping — no path ships an unverified deterministic render.
- Fabrication / unverified-phone / different-business never reach the rung (excluded by `_qa_failed_has_fabrication` + the partition).
- Fix C's own fit-or-flat degrade (follow-up #1) governs premium-vs-flat inside mode 2; if even flat fails QA, the flyer goes manual.
- Flag OFF ⇒ rung skipped ⇒ byte-identical. Rollback = unset `FLYER_DETERMINISTIC_RECOVERY` + restart.
- No new storage, no data migration. `FlyerIntegratedFellBackDeterministic` already exists; its `reason` is `Literal["retries_exhausted","referee_unavailable","generation_error","fabrication"]` (`schemas.py:4492`). This design adds one **additive** Literal value `"qa_text_fidelity"` for the new rung (a low-risk, append-only change to a `LogEntry` variant — no migration; old rows still validate).

## 9. Testing (to be detailed in the plan)
- **F0174 replay**: its exact blocker set + locked fact ids → recoverable=True (regression anchor).
- own-brand typo (`Laksmi'S Kitchen` vs `Lakshmi's Kitchen`) → recoverable.
- truly different business (`Triveni Indian Cafe & Bakery`) → not recoverable.
- fabricated price / fabricated offer → still hard-block.
- unverified phone → still hard-block.
- item price mismatch recoverable **only** when the locked `item:N:price` exists (locked → recoverable; not locked → dangerous).
- flag OFF ⇒ byte-identical (rung skipped; existing ladder unchanged).
- forced mode-2 render path (`force_background_only=True` reaches textless-bg + `_apply_critical_text_overlay`, not mode-1, not mode-3).
- QA pass after recovery ⇒ ships; QA fail after recovery ⇒ falls through to manual.

## 10. Out of scope (explicitly deferred)
- The combo near-duplicate referee quirk (separate follow-up, per operator).
- Slice 2 cleanup / `projects.json` migration (separate task).
- Always-on rollout (kept scoped behind the new flag + allowlist).
- Any change to `visual_qa` severity tiers or the integrated render itself.
