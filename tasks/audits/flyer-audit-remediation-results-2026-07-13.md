# Flyer Studio E2E Audit — Remediation Results (2026-07-13)

Completes `flyer-studio-e2e-adversarial-audit-2026-07-13.md` (20 findings). Plan:
`tasks/flyer-audit-remediation-plan.md`. Branch `feat/flyer-audit-remediation-20260713`
(worktree, off `origin/main` @ 2711436). **Not committed / merged / deployed — awaiting
operator go.** Full flyer + cf-router suite green on Windows (cf-router *hooks* tests are
Linux-only and skip locally; they run on `flyer-premium-ci`).

## Disposition of all 20 findings

| # | Finding | Disposition | Where |
|---|---------|-------------|-------|
| SW-1 | Wrong-brand ships (no-suffix competitor) | **Fixed** — ingest style-only default + QA masthead backstop gated on external-reference-ingested | render.py, semantic_brief.py |
| SW-2 | `Dosa` → `Dosa Biryani` fabrication | **Fixed** — suffix only onto protein/veg modifiers | facts.py |
| SW-3 | Multi-price phantom `also` item | **Fixed** — connectors rejected, fail-closed | facts.py |
| SW-4 | Silent same-item price conflict | **Fixed** — `price_conflict` reason wired end-to-end (detect → manual review → reply) | facts.py, schemas.py, workflow.py, create-flyer-project |
| SW-5 | Code-mix garbled headline | **Deferred → v2 track** (LLM-classifiable; regex = treadmill) | — |
| BC-1 | Rupee / bare prices dropped | **Fixed** — `$`/`₹`/`Rs`/bare-decimal; bare integer rejected as quantity | facts.py |
| BC-2 | Bare menu list dropped | **Fixed** — bounded bare-name extraction | facts.py |
| BC-3 | 9-word approval allowlist | **Fixed** — widened; exact-match keeps "looks good but change X" → revision | actions.py |
| BC-4 | Typos miss flyer intent | **Fixed** — curated typo set (NOT fuzzy: "roster"→"poster" collision) | actions.py |
| BC-5 | "new flyer for diwali" swallowed | **Fixed** — festival detail cues | actions.py |
| BC-6 | Sample choice only digit 1/2 | **Fixed** — ordinals/cardinals/"option N", "yes"→None | intake.py |
| AN-1 | Early APPROVE → silent revision | **Fixed** — progress reply before preview exists | hooks.py |
| AN-2 | Decorated/emoji APPROVE fails | **Fixed** — strips `*`/curly-quotes/emoji; bare 👍/🙏 approve; 👎 never | actions.py |
| AN-3 | Echo "new" too rigid | **Fixed** — "make a new one"/"another"/"redo" | actions.py |
| AN-4 | Silent brand-asset deactivation | **Fixed** — audit row via existing `FlyerBrandAssetStateChanged` chokepoint | onboarding.py |
| AN-5 | Address-less ships with warning | **Kept as design** (warn, not block) | — |
| IN-1 | Fact-key literals unscreened | **Fixed** — QA denylist screen | visual_qa.py |
| IN-2 | Uniform-price marker-gated | **Kept as design** — screening legacy renders false-positives real menus | — |
| IN-3 | No closure on unclassified failure | **Fixed** — plain-language closure after processing-ack | hooks.py |
| IN-4 | Occasion not set (deterministic) | **Fixed** — bounded festival→occasion map (4 enum values) | extraction_seam.py |

**17 fixed · 2 kept-as-design (IN-2, AN-5) · 1 deferred to v2 (SW-5).**

## Deviations from the plan (operator should note)

1. **SW-1b critical FP gate (adversarial-review catch).** The first masthead backstop
   blocked the owner's OWN near-universal taglines ("Pure Veg", "Home Delivery", "Taste
   Of India") → hard-stop manual review → a near-broken auto-send pilot. Fixed by gating
   the suffix-less block on the project having ingested an external reference/template
   (the only wrong-brand vector); pure-text briefs are never screened.
2. **IN-2 kept marker-gated.** Deeper reading showed screening legacy/non-typeset renders
   false-positives on a legitimate uniform-price menu (4 items × $5.99 = 4 appearances),
   which the design deliberately avoids. Same lesson as #1: don't add a screen that FPs.
   You chose "add both"; the safe answer here is to keep it as-is.
3. **SW-5 deferred to the v2 track**, per your own Fork-1 reasoning (headline generation
   is LLM-classifiable; regex-patching is the treadmill).

## SW-1 residual bypasses (→ the broader wrong-brand strategy you already deferred)

Not regressions — all bypassed before SW-1b too. Need the structured
`reference_extractions.source_contract` name path, not the blind masthead loop:
single-word competitor names (Bawarchi), 4+ word names, offer-word names ("Grand Sweets"),
and owner-confirmation trusting caption text.

## Layer-2 box-verification (only your phone can prove these)

Send from a non-owner trial number for "Lakshmi's Kitchen". Ranked by embarrassment.

| # | Send | PASS iff | Probes |
|---|------|----------|--------|
| 1 | Competitor flyer, caption **`use this theme going forward`** | delivered flyer shows ONLY Lakshmi's Kitchen | SW-1 (needs real image render) |
| 2 | Reply **`👍`** to a ready preview | finalized & delivered (not "what to change?") | BC-3/AN-2 |
| 3 | **`perfect, thank you!`** to a ready preview | finalized | BC-3 |
| 4 | **`weekend special idli 5.99 vada 4.99 dosa 6.99`** (no `$`) | flyer lists all 3 items with prices | BC-1 |
| 5 | **`weekend thali idli ₹120 vada ₹90 dosa ₹150`** | items with rupee prices | BC-1 |
| 6 | **`weekend menu`** + newlines `Idli`/`Dosa`/`Vada`/`Pongal` | all four items render | BC-2 |
| 7 | **`helo pls mak me a flyr for wekend brekfast specal`** | Flyer Studio responds (not silence/wrong agent) | BC-4 |
| 8 | **`diwali dinner special flyer for saturday`** | Diwali-themed, not swallowed into prior project | BC-5, IN-4 |
| 9 | With a project open: **`create a new flyer for diwali dinner`** | a NEW project, not an edit | BC-5 |
| 10 | **`weekend combo everything $5.99, family combo $7.99, idli dosa vada`** | real items, no phantom "also" | SW-3 |
| 11 | **`weekend special biryani $10, dosa $6, vada $5`** | flyer says `Dosa`/`Vada` (not `Dosa Biryani`) | SW-2 |
| 12 | **`Biryani $10 Biryani $12`** | routed to review / asks which price (not silent ship) | SW-4 |
| 13 | Reply **`APPROVE`** while it's still generating | "still preparing, preview coming" (not a revision) | AN-1 |
| 14 | Sample-prompt flow: **`the first one`** | advances (doesn't re-ask) | BC-6 |
| 15 | The final delivered flyer — check every format | WhatsApp + Instagram + story + PDF all arrive, un-cropped | render fidelity (unprovable offline) |

Do #1–#3 first (highest embarrassment, lowest cost). #15 is the classic CLI-masking surface.

## v2 graduation (separate operator-gated track)

Legacy is v2's fail-closed fallback (`extraction_seam.py`), so the legacy patches above are
permanent floor-safety regardless. To graduate v2: validate the persona corpus on-box
(needs `OPENROUTER_API_KEY`) + flip `FLYER_EXTRACTION_V2` behind the patched floor,
pilot-number-scoped, with the shadow soak. Parity-guard note: `value_has_source_parity` is
token-SET membership, so v2 does NOT auto-fix SW-2 recombination — the legacy SW-2 fix
stays the floor even post-graduation.

## Post-review fixes (2026-07-18)

Code-review of the remediation branch found nine issues (F1–F8 verified by
execution against branch HEAD; F10 a test-coverage gap). All fixed on-branch with
new coverage in `tests/test_flyer_audit_remediation_review_fixes.py`.

- **F1 (BLOCKER)** — `facts.py` `name_before_price`: the `(?!\s*[%\d])` lookahead
  sat after the whole price alternation and backtracked the symbol branch's
  decimals, truncating/dropping prices before a trailing quantity ("$5.99 2pc" →
  $5; "$10 30 pieces" → dropped). Moved the lookahead inside the bare-price
  alternative only; symbol prices keep their full value.
- **F2 (MAJOR)** — `facts.py` `_BARE_NAME_STOPWORD_RE`: color / style / adjective
  words (green, gold, maroon, elegant, …) now disqualify a bare token, so palette
  lines ("Green"/"Gold", "green, gold") never become phantom items. Genuine bare
  dish lists still extract; branch (b) kept (a legit pure-comma dish list is a
  pinned positive case) — it is gated by the same disqualifier.
- **F3 (MAJOR)** — `intake.py` `_parse_sample_choice`: bare "one"/"two" no longer
  match free-floating in prose ("no one likes it", "give me one more"); they
  select only as a near-standalone reply (politeness/selection filler stripped).
  Digits, first/second/1st/2nd, "option N", "number two" still select.
- **F4 (MAJOR)** — `semantic_brief.py` `_OFFER_VOCAB_RE`: removed the menu/course
  food tokens (combo(s)/buffet/menu/thali/meal(s)/breakfast/brunch/lunch/dinner)
  that were excusing food-word competitor mastheads ("Bombay Thali", "Madras
  Meals", "Sunday Brunch"). "feast" retained as an occasion word (pinned by the
  "Family Combo Feast" positive case). "Grand Combo" stays excused via "grand".
- **F5 (MAJOR)** — `semantic_brief.py` `_project_ingested_external_reference`: now
  also True when the customer has an ACTIVE `template` brand asset (the F0217
  vector), reusing render's read-only `_active_brand_assets`; falls back to False
  with no customer / unreadable store; never keys on brief text.
- **F6 (MINOR)** — `onboarding.py`: the §12b re-upload-deactivation audit row is
  now emitted only AFTER `write_customer_store` succeeds, in both sites
  (`store_brand_asset` and `_connect_recovered_sender`, the latter returning the
  deferred flips for the caller to emit post-persist).
- **F7 (MINOR)** — `actions.py` `_FLYER_APPROVAL_EMOJI_RE`: dropped 🙏 (U+1F64F);
  a bare 🙏 no longer finalizes/ships (reads as thanks/please in Indian-SMB use).
  👍 still approves; 🙏 alongside approval text still approves via the text path.
- **F8 (NOTE)** — `actions.py` `_FLYER_ECHO_NEW_RE`: "redo it" (and "regenerate
  it") now classify as NEW, matching the docstring.
- **F10 (test-gap)** — added an integration trace proving a numeric "1" in
  `awaiting_concept_selection` resolves to concept selection and is not approval
  text, so it cannot trip the AN-1 early-approval progress reply.

### Recorded decisions

1. **SW-4 price_conflict routes legitimate multi-size menus to manual review.**
   "Biryani $10 Biryani $15" (a real small/large listing) is flagged as a
   price conflict and sent to manual review rather than auto-shipped. This is
   accepted operator friction: the extractor cannot tell a size ladder from a
   contradiction, and failing safe (ask a human) beats silently shipping one of
   two prices.
2. **Rollback caution — the new `price_conflict` reason_code is persisted.**
   `FlyerManualReview.reason_code="price_conflict"` is written into `project.json`;
   an older-schema reader would fail to load a store that contains it. Full-tree
   pinned deploys revert schema + code together, so this is safe for the normal
   deploy path — but a MANUAL rollback of code-without-schema would trip it. Noted
   for any hand-rolled rollback.

## Approvals log

- 2026-07-16: operator directive "complete every finding before I move on to marketing manager" — authorized remediation implementation (session e785035c).
- 2026-07-18: operator "Fix all findings" — authorized post-review fix pass F1-F10 (session dd4a8de7).
- 2026-07-18: operator "yes go ahead" — authorized commit + PR of remediation + review fixes (session dd4a8de7). Merge/deploy NOT yet authorized.
