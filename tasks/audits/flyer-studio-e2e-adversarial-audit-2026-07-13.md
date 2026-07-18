# Flyer Studio — Full E2E Adversarial Customer-Journey Audit

**Date:** 2026-07-13
**Persona:** "Lakshmi" — a busy, non-technical owner of a small Indian restaurant. Types on a phone,
makes typos, doesn't know the "right" format, sends photos without captions, replies "yes ok" / "looks
good" / "👍" instead of APPROVE, changes her mind mid-flow, re-sends last week's special, and once
uploads a competitor's flyer as her "theme." **She is not a QA engineer and will not be taught the
magic words. Every place the system requires her to behave like an engineer is a finding.**

**Drift-check tag:** `Hermes-native` — this is read-only observation of the deployed Hermes-substrate
behavior. No code changed. No live store touched. No send. No deploy. Report-and-stop.

## Hermes-first analysis

N/A for scope — this audit writes no code. It observes the existing Flyer Studio surface (a mix of
Hermes substrate + net-new flyer workflow) through the same deterministic seams cf-router uses. The
one methodological note: item extraction, routing, and the QA gate are **net-new deterministic Python**
(not Hermes LLM); the semantic brief/occasion layer is the Hermes LLM path, which this offline harness
could not exercise (see Coverage Honesty).

---

## 0. How this was tested (method + honesty upfront)

**Layer 1 (automated, me).** A store-isolated harness in a throwaway tmp tree (`FLYER_*` state paths →
tmp, `HERMES_BRIDGE_URL` → the closed fake-sink `127.0.0.1:1`, audit log → tmp). The fake-bridge
tripwire (`conftest` `_force_fake_bridge_sink` + `safe_io.LiveBridgeSendInTestError`) stayed armed. I
drove the **real deployed code** at three seams:

- **Routing/approval** — cf-router `actions.py` pure classifiers, loaded via the same `SourceFileLoader`
  path `tests/_flyer_replay_helpers.py` uses.
- **Intake** — the real `create-flyer-project` script + `facts.extract_text_facts` on 12 persona briefs.
- **Visual QA** — the real `run_visual_qa` gate via its documented sidecar-OCR seam
  (`FLYER_QA_ALLOW_SIDECAR=1` + `<artifact>.ocr.txt`), feeding hand-crafted OCR strings.

**What Layer 1 CANNOT prove** (and I did not pretend it did): the actual image render (PIL), the actual
vision/OCR read of a real flyer, WhatsApp swipe-reply binding, voice-note transcription, and whether the
**live LLM** semantic-brief provider (which has an OpenRouter key on the box; my offline harness does not)
rescues any of the deterministic-extraction failures below. This codebase has a documented history of CLI
tests masking live failures (PIL venv bug, PPv1 routing bug, DETERMINISTIC_FIRST veto) — so everything
render/vision/send-shaped is pushed to the Layer-2 human-send script, not claimed green here.

Harness scripts: `scratchpad/harness_{A,B,C,C2}_*.py`, `confirm_bareprice.py` (session-local, not committed).

---

## 1. Customer-journey verdict

**Could a real, untrained restaurant owner get a good flyer? Only if she already types like an engineer.**
The pipeline is genuinely strong at *refusing to ship wrong facts* — the visual-QA gate caught every
fabricated price, fake offer, placeholder, internal-asset-id, and unexpected phone I threw at it, and the
customer-facing copy is uniformly plain-language. **The failures are concentrated at the two ends the
persona actually touches: getting her request understood, and telling the system "yes, I like it."**

Ranked by customer impact:

1. **"I said yes and it acted like I wanted changes."** The single most common approval a human sends —
   `👍`, `perfect`, `looks great`, `yes please`, `okay` — does **not** approve. Only 9 exact tokens do.
   Everything else is silently re-interpreted as a *revision request*. This is the highest-frequency,
   most-embarrassing failure: a happy customer who thumbs-up her flyer gets treated as though she asked
   for edits.

2. **"My menu disappeared."** If she writes prices the natural way — `Idli 5.99` (no `$`), or in rupees
   `Idli ₹120` / `Idli Rs 120`, or as a plain list with no prices — the extractor pulls **zero items**.
   The flyer generates with her name and phone but **no food on it**. The target market is Indian SMBs;
   rupee and bare pricing are exactly what they type.

3. **"It put another restaurant's name on my flyer."** If she uploads a competitor's flyer as her "theme"
   (she will), the competitor's image is handed to the model as the identity source, and the post-render
   name-check only catches names containing an English org-suffix word (Kitchen/Restaurant/Cafe/…). A
   competitor named *Saravana Bhavan*, *Paradise Biryani*, *Adyar Ananda Bhavan*, or *Bawarchi* renders
   **alongside her own name and ships clean** — verified.

4. **"It garbled my request."** Typos defeat intent recognition entirely (a `flyr` request isn't seen as
   a flyer request); a `Dosa` next to a `Biryani` becomes `Dosa Biryani`; a Telugu-English brief produces
   the headline `Weekend Ki Oka`; a multi-price offer produces a phantom item `also $7.99`.

5. **"I asked for a new one and it edited the old one."** `create a new flyer for diwali` sent while a
   project is open is routed as a *revision* to the open project, because `diwali` (and every Indian
   festival) is absent from the router's "new-work detail" keyword list.

Where the persona gets stuck or silently fails is documented per-finding below. **None of these are
render-quality problems — they are understanding and hand-shake problems, and they gate a paying pilot.**

---

## 2. Findings ledger

Tiers: **SHIPS-WRONG** (customer receives a flyer with wrong/garbled content) · **BLOCKS-CUSTOMER**
(customer cannot get a good flyer / gets stuck) · **ANNOYS-CUSTOMER** (friction, recoverable) ·
**INTERNAL** (observability / low customer-probability). "Verified" = I executed it and observed the
output. "Mapped" = read from code (file:line) but not executed.

### SHIPS-WRONG

| ID | Finding | Evidence | file:line | New? |
|----|---------|----------|-----------|------|
| **SW-1** | **Wrong-brand ships when competitor name lacks an English org-suffix.** Upload competitor flyer as "theme" → role `inspiration` → competitor image handed to model as *"source of truth for visual identity"*; the only backstop, `visible_wrong_brand_blockers`, requires an org-suffix word. Competitor name rendered **alongside** the owner's name passes QA and ships. | **Verified** C2-crux: `Saravana Bhavan`, `Paradise Biryani`, `Adyar Ananda Bhavan`, `Bawarchi` all `status=passed severity=pass → WOULD SHIP`. | gate: `semantic_brief.py:688-706` (`_ORG_SUFFIX_RE` required); style-only markers the persona never says: `render.py:1914-1926`; non-style-only identity-source prompt: `render.py:2628-2633` | Known **class** (2026-07-11 register-lock diagnostic, F0217); the org-suffix-miss **mechanism** is newly pinned |
| **SW-2** | **Category-suffix logic fabricates item names.** When "biryani" appears anywhere in the brief, a non-complete-dish item without "biryani" gets it appended: `Dosa` → `Dosa Biryani`, `Dosaa` → `Dosaa Biryani`. Ships a dish the owner never named. | **Verified** B6b, B12: `item:1:name='Dosa Biryani'`, `item:1:name='Dosaa Biryani'`. | `facts.py:347-348` | New |
| **SW-3** | **Multi-price offer produces a phantom item and drops the real ones.** `everything $5.99 and also $7.99 combo, idli, dosa, vada` → single item `name='also' price='$7.99'`; idli/dosa/vada dropped; the $5.99 lost. Not fail-closed — fail-wrong. | **Verified** B6a. | `facts.py:373-403` (`price_before_name` greedy name capture) | New |
| **SW-4** | **Same-item conflicting prices silently resolve, no signal.** `Biryani $10 Biryani $12` → ships `Biryani $12` (last-wins). No manual-review reason code exists for price conflict. | **Verified** B6b (`item:0:price='$12'`). | dedup `facts.py:319-323`; reconcile last-wins `facts.py:987-989`; no `price_conflict` in `FlyerManualReviewReason` `schemas.py:718-732` | New |
| **SW-5** | **Code-mixed brief yields a garbled campaign headline.** `weekend ki oka flyer kavali, idli $5.99…` → `campaign_title='Weekend Ki Oka'`. The Telugu filler becomes the poster headline. | **Verified** B11. | deterministic title path `semantic_brief.py:132-151` / `intake_fields.py:387-391` (offline path; live LLM may differ — see Coverage) | New |

### BLOCKS-CUSTOMER

| ID | Finding | Evidence | file:line | New? |
|----|---------|----------|-----------|------|
| **BC-1** | **Menu silently vanishes unless every price is `$`-prefixed.** `Idli 5.99`, `Idli - 5.99`, `Idli 5.99 each`, `Idli ₹5.99`, `Idli Rs 120` → **0 items extracted**. Flyer ships with name+phone but no food. Rupee/bare pricing is exactly what the ethnic-SMB target market types. | **Verified** confirm_bareprice (only `$` extracts), B2. | extractor anchors on `$` only: `facts.py:373-403` (QA understands `[$₹]` at `visual_qa.py:63` but the extractor does not) | New |
| **BC-2** | **Bare-line menu (names, no prices) → 0 items.** `Idli\nDosa\nVada\nPongal` extracts nothing unless prefixed by `include …` or an `N items:` colon list. Silent drop, no operator signal. | **Verified** B3. | name path requires trigger: `facts.py:578-641` (`include`), `facts.py:554-575` (`items:` colon) | New |
| **BC-3** | **Approval vocabulary is a 9-word allowlist; normal approvals are treated as revisions.** Only `{approve, approved, ok, yes, looks good, go ahead, send it, finalize, finalise}` approve. `👍`, `perfect`, `looks great`, `great`, `yes please`, `yep`, `okay`, `love it`, `that works`, `ship it`, `good to go`, `send it out`, `cool`, `nice`, `go`, `thumbs up` → **route=revision** (silently interpreted as an edit request). `done` → `status_reply`. | **Verified** A1 (full table). | alias set `actions.py:1937-1949`; exact-match `is_flyer_approval_text` `actions.py:1952-1956`; non-alias→revision `flyer_routing_decision_preview` `actions.py:2001-2003` | New |
| **BC-4** | **Typos defeat intent recognition — the request isn't seen as a flyer at all.** `helo pls make me a flyr for this weekend…` → `classify_flyer_intent=False`, `should_bypass_intake_for_clear_intent=None`. A misspelled `flyr`/`wnat` isn't routed to Flyer. | **Verified** A4. | single regex `_FLYER_INTENT` in `classify_flyer_intent` `actions.py:1691-1702` | New |
| **BC-5** | **Explicit "new flyer for <festival>" is swallowed as a revision of the open project.** `create a new flyer for diwali dinner` with an active project → `route=revision`, because `diwali` (and every Indian festival) is absent from the vague/new detail-keyword regex, so it's classified "vague" and attached to the open project. | **Verified** A2. | `is_vague_flyer_start` detail regex `actions.py:2311-2320` (has `grand opening/graduation/party` but no festival); short-circuit `should_start_new_flyer_over_active` `actions.py:2212-2213` | New (festival-specific) |
| **BC-6** | **Sample-idea selection only accepts a literal digit 1/2.** "the first one", "restaurant", "yes" → `None` → re-prompts the same menu without advancing. | **Mapped** (intake agent) | `_parse_sample_choice` `intake.py:870-875`; re-prompt loop `intake.py:308-326` | New |

### ANNOYS-CUSTOMER

| ID | Finding | Evidence | file:line | New? |
|----|---------|----------|-----------|------|
| **AN-1** | **APPROVE sent before the preview arrives → revision.** While `generating_concepts`/`awaiting_concept_selection`, even exact `APPROVE` routes to `revision`. Defensible (nothing to approve yet) but the early approval silently becomes an edit with no "hang on, still working" signal. | **Verified** A1b. | status gate `actions.py:1976`, `_FLYER_FINAL_APPROVAL_STATUSES` `actions.py:1949` | New |
| **AN-2** | **Wrapped/decorated APPROVE fails.** `is_flyer_approval_text` strips only `" .!,:;"` — curly-quoted `“APPROVE”`, WhatsApp-bold `*APPROVE*`, or `looks good 👍` are not normalized and do **not** approve. | **Verified** A5 (visible text unchanged) + A1 (`looks good 👍` → revision). | `flyer_visible_message_text` + strip set `actions.py:1954-1955` | New |
| **AN-3** | **Echo/re-send disambiguation is brittle.** Reply classifier for the NEW/APPROVE echo prompt returns `None` for "make a new one", "the second one", "1", "2" — only bare "new"/"approve"/"yes" work. | **Verified** A3. | `classify_flyer_quote_echo_choice` `actions.py:3399-3415` | New |
| **AN-4** | **Silent same-kind brand-asset deactivation (no audit row).** Re-uploading a logo/template flips the prior active asset to `active=False` with no audit entry, even though a sanctioned audited path (`set-flyer-brand-asset-state`) exists to prevent exactly the 2026-06-17 incident. | **Mapped** (brand agent) | `onboarding.py:347-353`, `:386-392`, `:610-617`; sanctioned path `scripts/set-flyer-brand-asset-state` | Known-adjacent |
| **AN-5** | **Address-less flyer ships with a warning, not a block.** Missing `location` → `severity=warn` → `delivered_with_warning`. A flyer with no address goes out (with a nudge). By design, but worth confirming it's intended for a pilot. | **Verified** C7. | warn-tier spec `visual_qa.py:1430-1433` | Known (design) |

### INTERNAL

| ID | Finding | Evidence | file:line | New? |
|----|---------|----------|-----------|------|
| **IN-1** | **Fact-key / spec-vocabulary literal leaks are not screened.** `item:0:name`, `business_name`, `contact_phone`, `locked_facts`, `sender_role`, `raw_request` rendered into the art all pass QA. Low probability (model rarely paints a schema key) but the leak class is unguarded (only the authored style-vocab list is screened). | **Verified** C3d, C2-crux-B. | positive screens `visual_qa.py:2026-2033`; style-vocab-only `_style_vocab_blockers` `visual_qa.py:1768-1780` | New |
| **IN-2** | **Uniform-price column defect is only screened under the typeset marker.** Repeated shared-price column (`$5.99` ×4) blocks **only** when a render-time `.typeset.json` marker is present; legacy/non-typeset renders are unscreened. | **Verified** C6 (no-marker passes; marker blocks). | `_uniform_price_column_blockers` marker-gate `visual_qa.py:1945` | Known (design) |
| **IN-3** | **Generation failure after a processing-ack can send no closure message.** If `generate-flyer-concepts` exits non-zero for a reason not recognized as manual-review (e.g. the concurrency guard, an unclassified crash) and a processing-ack was already sent, the customer gets **no** failure/closure message; only an audit row + the reactive "any update?" path exist. | **Verified** hooks read (`proc_ok` → `return True,"",""`). | `_send_generation_failure_customer_update` `hooks.py:2401-2402` | New |
| **IN-4** | **Occasion (festival theming) not set on the deterministic path.** `diwali dinner special flyer` → `occasion=none` offline. Festival theming keys off `project.occasion`; the deterministic extractor sets none. **Whether the live LLM sets `occasion=diwali` is unverified (see Coverage).** | **Verified offline** B9; occasion source `extraction_seam.py:53` | style-register occasion drive `style_registers.py:89-135`; `render.py:2321` | Caveated |

### Corrected — a subagent claim that did NOT survive my own execution

- **"A competitor phone number is checked by nothing" — FALSE.** `_unexpected_phone_blockers` flags any
  phone-shaped number in the OCR that is not the registered phone. Verified C2: `973-555-1234` alongside
  the owner's phone → `unverified phone number visible: 973-555-1234` → **block**. Runs whenever a locked
  phone exists (always true for a registered customer). `visual_qa.py:212-256`. This is *not* a finding.
  (I flag it because own-eyes verification is the whole point of this pass — a reviewer's map is not proof.)

---

## 3. Coverage honesty table

| Coverage area | Exercised FOR REAL | Simulated (flagged) | Unprovable without a live inbound |
|---|---|---|---|
| Routing / approval classifiers | ✅ real `actions.py` funcs, 40+ persona messages (Group A) | — | swipe-reply/quoted-APPROVE **binding** (needs WhatsApp `quotedMessageId`) |
| Intake extraction | ✅ real `create-flyer-project` + `extract_text_facts`, 12 briefs (Group B) | — | whether the **live LLM** semantic brief rescues bare-price/occasion/code-mix (box has a key; harness does not) |
| Visual QA gate logic | ✅ real `run_visual_qa` via sidecar seam, 25+ OCR scenarios (Group C/C2) | **OCR text was hand-fed** — the gate logic is real, the *vision read* is simulated | the real vision/OCR model reading a real rendered flyer; provider-note blockers (garbled/duplicate) need the live vision path |
| Wrong-brand backstop | ✅ QA gate verified to miss no-suffix competitor names | brand-asset ingest path **mapped** (agent), not run | does the model actually bake the competitor identity in? (needs real image + render) |
| Recovery / quarantine ladder | quarantine COPY verified (`quarantine.py:267`); S1 silent-drop verified (`hooks.py:2401`) | 7-rung ladder **mapped** (agent), not driven end-to-end | provider outage mid-render, concurrency guard trip, project-expiry sweep (need live/stateful runs) |
| Render fidelity / formats | ❌ **not run** | — | **all 4 formats present + un-cropped, PDF, delivery markers** — needs PIL render + finalize (the classic CLI-masking surface) |
| Voice-note / photo-no-caption intake | ❌ | — | needs Hermes media transcription + a real inbound |
| Approval delivery end-to-end | approval *classification* verified | — | the actual APPROVE→finalize→send→delivery-marker chain |

**No green is claimed on a simulation.** Every ✅ above is a real invocation of deployed code whose raw
output I read; every render/vision/send row is explicitly deferred to Layer 2.

---

## 4. Layer-2 human-send script for SriniY (≤15 sends, ranked by embarrassment)

Only your phone can send real WhatsApp. These prove what Layer 1 cannot. Send from a **non-owner** number
that is (or becomes) a trial customer for Lakshmi's Kitchen. Ranked by "what would embarrass us most in
front of a paying customer." Each: exact text/gesture → expected → **PASS iff**.

| # | Send exactly | Expected (good) | PASS criteria | Probes |
|---|---|---|---|---|
| 1 | **Upload a real competitor's flyer** (e.g. a Saravana Bhavan / Paradise Biryani poster) with caption **`use this theme going forward`** | System either strips the competitor identity or routes to manual review | **PASS iff** the delivered flyer shows **only Lakshmi's Kitchen** — no competitor name/phone/logo anywhere | SW-1 |
| 2 | To a ready preview, reply **`👍`** | Flyer is finalized & delivered | **PASS iff** you receive the final flyer (not a "what would you like to change?" / revision reply) | BC-3 |
| 3 | To a ready preview, reply **`perfect, thank you!`** | Finalized & delivered | **PASS iff** delivered, not treated as a revision | BC-3 |
| 4 | New brief, no `$`: **`weekend special idli 5.99 vada 4.99 dosa 6.99 open sat and sun morning`** | Flyer shows all three items with prices | **PASS iff** the flyer visibly lists Idli/Vada/Dosa **with** their prices | BC-1 |
| 5 | New brief in rupees: **`weekend thali special idli ₹120 vada ₹90 dosa ₹150`** | Flyer shows items + rupee prices | **PASS iff** items appear (not a name-only or empty-menu flyer) | BC-1 |
| 6 | Bare list (no prices): **`weekend menu`** then on new lines **`Idli` / `Dosa` / `Vada` / `Pongal`** | Flyer shows the four items | **PASS iff** all four item names render | BC-2 |
| 7 | Typo brief: **`helo pls mak me a flyr for wekend brekfast specal`** | Recognized as a flyer request | **PASS iff** you get a Flyer Studio response (intake/preview), not silence or a wrong agent | BC-4 |
| 8 | Festival brief, no `$`: **`diwali dinner special flyer for this saturday`** | Diwali-themed flyer (festive treatment) | **PASS iff** the flyer reads as Diwali (not a generic dark/gold default) **and** is not swallowed into a prior project | BC-5, IN-4 |
| 9 | With a project already open, send **`create a new flyer for diwali dinner`** | A **new** project starts | **PASS iff** you get a fresh flyer for Diwali, not an edit of the open weekend flyer | BC-5 |
| 10 | Re-send **last week's exact brief** verbatim | Clear disambiguation or a clean new project | **PASS iff** you either get a new flyer or a clear "new one or approve the last?" — **not** a silent edit of a stale project | AN-3 |
| 11 | Multi-price offer: **`weekend combo everything $5.99, family combo $7.99, idli dosa vada`** | Coherent flyer with real items | **PASS iff** the flyer shows Idli/Dosa/Vada (not a phantom "also" item) with sensible pricing | SW-3 |
| 12 | Item near "biryani": **`weekend special biryani $10, dosa $6, vada $5`** | Items render as named | **PASS iff** the flyer says **`Dosa`** (not `Dosa Biryani`) and **`Vada`** (not `Vada Biryani`) | SW-2 |
| 13 | Sample-prompt flow: when offered "Reply 1 or 2", reply **`the first one`** | Advances to that idea | **PASS iff** it proceeds (does not just re-ask the same menu) | BC-6 |
| 14 | On the **final delivered** flyer, check every format | 4 outputs, all un-cropped, PDF present | **PASS iff** WhatsApp image + Instagram + story + printable PDF all arrive, none cropped, PDF opens | render fidelity (unprovable offline) |
| 15 | Send a **photo of a handwritten menu with no caption** | Handled (extract or ask) | **PASS iff** you get a sensible response, not silence | photo-no-caption (unprovable offline) |

Budget note: #1–#3 are the highest-embarrassment, lowest-cost proofs — do those first. #14 is the classic
CLI-masking surface (PIL/finalize) and must be human-verified regardless of Layer-1 results.

---

## 5. Finish-the-current-surface ledger (the gate for expansion work)

**Merged-not-deployed:** none identified in this pass (audit did not diff deploy state; the live box is
`deploy-20260712-065803`).

**Built-not-wired / dormant:**
- `extraction_v2` (potentially better extraction) — flag-off (`FLYER_EXTRACTION_V2` unset). BC-1/BC-2/SW-3
  live on the legacy path *because* v2 is off. `extraction_seam.py:25-26`.
- `flyer_brief.py` / `flyer_brief_validator.py` firewall (strong fail-closed gates) — dormant behind
  `FLYER_CREATIVE_DIRECTOR_ENABLED`. Its required-fact-coverage gate would change several findings.
- Style registers — flag + allowlist gated (`FLYER_STYLE_REGISTERS` + per-phone allowlist); occasion is
  the only customer-data-driven theme input.

**Deployed-not-proven (this audit's honest gaps):** render fidelity/formats/PDF, live-LLM extraction
rescue of BC-1/SW-5/IN-4, real vision-OCR reads, swipe-reply binding, recovery-ladder end-to-end,
voice-note/photo-no-caption intake. → **all mapped to the Layer-2 script above.**

**Open findings (triage owner = operator):** 5 SHIPS-WRONG, 6 BLOCKS-CUSTOMER, 5 ANNOYS, 4 INTERNAL
(§2). The three that most directly gate a paying pilot: **BC-3** (approval vocabulary), **BC-1** (bare/₹
price menu drop), **SW-1** (wrong-brand no-suffix ship).

**Recommendation for the gate:** Do not start marketing-manager expansion until BC-3, BC-1, and SW-1 are
triaged — they are high-frequency, low-code-radius, and each one is a first-impression failure a pilot
customer will hit in her first three messages. This pass **finds**; fixing comes after your triage.
