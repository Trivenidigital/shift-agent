# Design — Catering v0.4: LLM-drafted customer quote

**Context:** The catering agent's v0.2/v0.3 customer-facing quote is template-rendered Python (`apply-catering-owner-decision._render_quote()` + `catering_quote_to_customer.txt`). Per the agent's stated value prop ("draft a quote in the **owner's voice**, gate behind a single approval"), this should be LLM-drafted, not template-substituted. v0.4 closes that gap.

**Premise (challenged + reaffirmed by 2026-04-29 architecture review):** Hermes does ~90% of the work; Python is a thin layer for atomicity, idempotency, schema invariants, and audit. The customer quote is currently the wrong layer.

**Drift-check tag:** extends-Hermes (adds a new SKILL; no Hermes-internal change)

---

## 1. Scope

**In:**
- New Hermes SKILL: `draft_catering_customer_quote`
- Slimmer `apply-catering-owner-decision`: removes `_render_quote()` template-format path, keeps menu-load + idempotency + state-write
- Owner-voice sample collection: 3 most recent owner-approved quotes per customer (or, if none, owner's overall-tone hint from config)
- Anti-hallucination guards: output must contain `headcount` integer and `event_date` ISO string verbatim; price floats from menu must appear unchanged
- Removes `catering_quote_to_customer.txt` template (no longer used)

**Out:**
- Owner approval card (`catering_approval_card_to_owner.txt`) — stays template-rendered. The owner sees the EXACT quote that gets sent to the customer; deterministic format + audit-pinned content. **The owner card displays the LLM-drafted quote_text, not a separate template.**
- New extraction logic in `parse_catering_inquiry` — out of scope, that SKILL stays as-is
- Owner-card flow's bridge POST and idempotency — already hardened in v0.3

---

## 2. Architecture

### 2.1 New flow (apply-script approve path)

```
apply-catering-owner-decision (Python, slimmer):
  1. Parse args (--code, --decision)
  2. Load config + leads store under FileLock
  3. Find lead by code (matches AWAITING_OWNER_APPROVAL)
  4. is_catering_transition_allowed(AWAITING -> OWNER_APPROVED) check
  5. Load filtered menu via _load_menu_filtered(dietary) — keep three-way return (A3 fix is durable)
  6. Build quote_context_bundle (see 2.2)
  7. Invoke draft_catering_customer_quote SKILL → returns quote_text str
  8. Anti-hallucination guards (see 2.4) — refuse on failure
  9. Persist quote_text on lead (Q1 fix is durable)
  10. Write CateringQuoteAttempted audit (A1+A2 anchor is durable)
  11. atomic_write_json(LEADS_PATH, store)
  12. Release lock
  13. Bridge POST quote_text
  14. On success: lock-2 → status=SENT_TO_CUSTOMER, audit CateringQuoteSent
```

**SKILL invocation mechanism:** the Python script writes a structured-input JSON to a temp file and exits with a special exit code that Hermes interprets as "invoke this SKILL with this input." Alternatively, the SKILL is invoked via `subprocess` calling a Hermes CLI — same pattern as `dispatch_shift_agent` invoking `parse_catering_inquiry`. **Decision deferred to design review** — depends on existing Hermes integration patterns (read `hermes-gateway` source before finalizing).

Actually, simpler: `apply-catering-owner-decision` IS already invoked by the SKILL `handle_catering_owner_approval`. So this whole flow happens INSIDE that SKILL's context. The SKILL invokes the Python script for state ops, gets the quote_text back as part of the script's stdout, then the SKILL handles the bridge POST. **Resolved:** Hermes orchestrates; Python writes state; the LLM drafts the quote AS PART OF THE SKILL's natural-language step before invoking the bridge tool.

**Revised flow:**

```
handle_catering_owner_approval SKILL (Hermes-orchestrated):
  1. Parse owner reply → decode code + verb (LLM)
  2. Invoke /usr/local/bin/apply-catering-owner-decision-prepare
       --code <CODE> --decision approve
     → Python script: validates code, transitions state to OWNER_APPROVED,
       writes attempted audit, returns JSON with {customer_phone, headcount,
       event_date, dietary, off_menu_items, filtered_menu_items,
       owner_voice_samples} on stdout
  3. SKILL prompt: "Draft a quote in the owner's voice. Use these
     prior owner-approved quotes for tone matching: [samples].
     Use ONLY the provided menu items and prices verbatim.
     Customer asked about [off_menu_items] — mention 'we'll discuss'.
     Headcount=N, event_date=YYYY-MM-DD are facts — preserve verbatim."
     → LLM generates quote_text
  4. SKILL invokes /usr/local/bin/apply-catering-owner-decision-finalize
       --code <CODE> --quote-text "<text>"
     → Python script: anti-hallucination guards, persist quote_text,
       bridge POST, lock-2 → SENT_TO_CUSTOMER
```

**Key insight: split the Python script into two steps** so the LLM-drafting lives between them:
- `apply-catering-owner-decision-prepare`: state transition (AWAITING → OWNER_APPROVED), CateringQuoteAttempted anchor, returns context for LLM
- `apply-catering-owner-decision-finalize`: anti-hallucination guards, persist quote_text, bridge POST, transition to SENT_TO_CUSTOMER

Or single script with `--phase prepare|finalize` flag. Either works. **Single-script with phase flag is preferable** — preserves single-source-of-truth for state transitions and lock acquisition order.

### 2.2 Quote context bundle (Python → SKILL prompt)

```json
{
  "lead_id": "L0007",
  "customer_name": "Priya Reddy",
  "customer_phone": "+19045551234",
  "headcount": 50,
  "event_date": "2026-09-05",
  "event_time": "20:00",
  "dietary_restrictions": ["vegetarian"],
  "unknown_dietary_tags": ["halaal"],
  "off_menu_items": ["butter chicken", "lamb biryani"],
  "filtered_menu_items": [
    {"name": "Aloo Paratha", "price_usd": 4.0, "dietary_tags": ["veg"], "category": "side"},
    ...
  ],
  "menu_filter_status": "ok",
  "owner_voice_samples": [
    "Hi Sarah! ...",
    "Hey Mike, thanks for reaching out...",
    "Hi Anjali, we'd love to..."
  ],
  "lead_id_for_ref": "L0007"
}
```

**Owner voice samples**: most recent 3 quotes from leads where:
- `lead.status == "SENT_TO_CUSTOMER"` (owner approved + customer received)
- `lead.quote_text` matches anti-hallucination guards (i.e., was a real quote, not the legacy sentinel)
- Not the current lead (avoid self-reference)

Cold-start (no prior quotes): omit samples; SKILL falls back to a tone hint from `cfg.customer.tone_profile` (new optional config field).

### 2.3 SKILL design — `draft_catering_customer_quote`

```yaml
---
name: draft_catering_customer_quote
description: |
  Use when handle_catering_owner_approval has invoked
  apply-catering-owner-decision --phase prepare and received the
  quote context bundle. Draft a customer-facing quote in the
  owner's voice. Output is plain text, ready for bridge POST.
---

# Draft Catering Customer Quote (Agent #2 — v0.4)

You are drafting a catering quote that the OWNER has already approved
to send. Your job: render it in the owner's voice using the prior
sample quotes as tone reference, keeping all facts verbatim.

## Hard constraints (anti-hallucination)

- Headcount appears ONCE, verbatim: {headcount}
- Event date appears ONCE, verbatim: {event_date} (ISO format)
- Event time, if non-null, appears verbatim: {event_time}
- Menu item names appear EXACTLY as in filtered_menu_items
- Menu item prices appear EXACTLY as in filtered_menu_items (with $ prefix, no rounding)
- DO NOT invent menu items not in filtered_menu_items
- DO NOT invent prices
- DO NOT mention items in off_menu_items as if they're on the menu — instead say "we'll discuss" or "we can quote separately"

## Tone matching

Match the voice of the most recent owner-approved quote in
owner_voice_samples (first item). If samples is empty, use a
warm-but-professional default tone.

## Structure

1. Greeting (use customer_name; "Hi" or "Hello" or whatever sample uses)
2. Confirmation of facts: headcount + event_date (+ event_time if present)
3. Menu list — bulleted, item name + price
4. If off_menu_items non-empty: short paragraph offering to discuss
5. If unknown_dietary_tags non-empty: short note acknowledging
6. Closing — match sample's typical closing
7. Reference: "(Ref: {lead_id})"

## Output

Plain text, max 4096 chars, no markdown headers (WhatsApp doesn't render them).

Return ONLY the rendered quote text on stdout — nothing else.
```

### 2.4 Anti-hallucination guards (Python finalize)

```python
def _validate_drafted_quote(quote_text: str, ctx: dict) -> tuple[bool, str]:
    """Returns (ok, reason). Refuses approve if any guard fails."""
    # Guard 1: headcount appears verbatim
    if str(ctx["headcount"]) not in quote_text:
        return False, f"headcount {ctx['headcount']} not in drafted quote"
    # Guard 2: event_date appears verbatim
    if ctx["event_date"] not in quote_text:
        return False, f"event_date {ctx['event_date']} not in drafted quote"
    # Guard 3: every price in filtered_menu_items either appears verbatim OR
    #         the corresponding item name is absent (LLM may have skipped some)
    for item in ctx["filtered_menu_items"]:
        name, price = item["name"], item.get("price_usd")
        if name in quote_text and price is not None:
            price_str = f"${price:.0f}" if price == int(price) else f"${price:.2f}"
            if price_str not in quote_text:
                return False, f"item {name!r} appeared but price {price_str} did not"
    # Guard 4: no fabricated items (every $-prefixed token in quote must come
    #         from filtered_menu_items)
    drafted_prices = re.findall(r"\$\d+(?:\.\d+)?", quote_text)
    valid_prices = {f"${item['price_usd']:.0f}" for item in ctx["filtered_menu_items"]
                    if item.get("price_usd") is not None}
    valid_prices |= {f"${item['price_usd']:.2f}" for item in ctx["filtered_menu_items"]
                     if item.get("price_usd") is not None}
    for price in drafted_prices:
        if price not in valid_prices:
            return False, f"price {price} appears in quote but not in menu"
    return True, "ok"
```

On guard failure: emit `CateringQuoteHallucinationDetected` audit (NEW), refuse approve, return EXIT_DEPENDENCY_DOWN. Operator sees the issue + can manually approve via cockpit (when cockpit ships) or fall back to template-rendering.

### 2.5 New schema additions (Commit-1 of v0.4)

```python
class CateringQuoteHallucinationDetected(_BaseEntry):
    """v0.4: LLM-drafted quote failed anti-hallucination guards. Approve
    refused; operator must intervene."""
    type: Literal["catering_quote_hallucination_detected"]
    lead_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
    failed_guard: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=2000)


# CustomerConfig addition
class CustomerConfig(BaseModel):
    # ... existing ...
    tone_profile: Optional[str] = Field(default=None, max_length=2000,
        description="Owner's tone hint for cold-start quote drafting")
```

---

## 3. Removed surface (compared to v0.3)

| Removed | Why |
|---|---|
| `_render_quote()` template-format path in apply-script | LLM drafts directly |
| `_format_menu_section()` mechanical bulleting | LLM bullets in prose |
| `catering_quote_to_customer.txt` template file | No template needed |
| `_normalize_dietary_tags()` (in customer-quote path) | LLM handles in prose |
| A4 (template format error LOUD) handling | No template to fail |
| A5 (off_menu_items rendering) | LLM handles |
| A8 (dietary note section) | LLM handles |
| `CateringQuoteRenderFailed` audit class | Replaced by `CateringQuoteHallucinationDetected` |
| `dietary_note_section`, `off_menu_items_section` template slots | Gone with template |

**Total removed Python LOC: ~250.**

## 4. Preserved surface (durable infrastructure)

| Kept | Why |
|---|---|
| All Pydantic schema validators (S1, S2, S3, S4, S6, L1) | Schema integrity is a Python concern |
| `CATERING_TRANSITIONS` table + `is_catering_transition_allowed` | Deterministic state machine |
| `CateringQuoteAttempted` (A1+A2 idempotency anchor) | Durable, used by both v0.3 + v0.4 |
| `CateringOwnerApprovalCardAttempted` (C3 anchor) | Same |
| `CateringDeclineAttempted` (A6 anchor) | Same |
| `CateringOwnerApprovalCardFailed/Skipped` (C4/C5) | Same |
| `CateringOwnerEdited` audit class | Same |
| `_load_menu_filtered` three-way return (A3) | Still needed — Python loads menu before SKILL call |
| Phone canonicalization (PM2/L0) | Deterministic |
| Migration tool | Operational |
| Smoke test extension | Operational |
| FileLock + atomic_write_json + audit ndjson | Atomicity |
| Status machine enforcement at every transition (Q1 quote_text persist + invariants) | Schema invariant |
| Sentinels (PRE_QUOTE_DRAFT_SENTINEL, etc.) | mode="before" backfill compat |
| Owner approval card template + render | Owner sees deterministic facts |

**Total preserved Python LOC: ~1850 (of v0.3's 2700).**

## 5. Risk analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM hallucinates prices | Medium | Critical | Anti-hallucination guards refuse approve; operator manual-fallback path |
| LLM hallucinates items not on menu | Medium | High | Same guards |
| LLM omits headcount or event_date | Low | High | Explicit guards |
| Tone-sample-poisoning (bad early quote becomes "the voice") | Low | Medium | Sample weighting (recent more, but rotate); operator can manually trigger sample reset |
| Two LLM calls per inquiry doubles cost | Certain | Low | Kimi/Haiku per-call cost ~$0.0005; 100 inquiries/month ≈ $0.10 extra |
| Rollback to v0.3 after v0.4 ships requires schema_version check | Low | Medium | Schema is forward-compatible (extra="ignore" on store); v0.3 reads v0.4 leads with sentinel quote_text fine |
| Customer quote drift from owner approval card | Medium | Medium | Owner approval card displays the LLM-drafted quote_text VERBATIM (not a separate template). Owner sees exactly what gets sent. |
| LLM call fails (rate limit, network) | Low | High | Retry once with backoff; on second failure → fall back to v0.3 template path (new fallback flag); audit `CateringQuoteSkillFailed` |

## 6. Build sequence (3 commits)

| # | Scope | LOC est | Tests |
|---|---|---|---|
| 1 | Schema additions (`CateringQuoteHallucinationDetected`, `CustomerConfig.tone_profile`) + LogEntry/exports | ~50 | 5 |
| 2 | `draft_catering_customer_quote` SKILL file + apply-script `--phase` flag refactor + anti-hallucination guards + remove `_render_quote()`/`_format_menu_section()`/template | ~300 (net -250) | 25 |
| 3 | Owner-voice-sample collection helper + cold-start fallback + `CateringQuoteSkillFailed` audit class | ~100 | 10 |

## 7. Pre-condition: this PR (#29 catering hardening) must land first

**Path B carve** (per 2026-04-29 architecture review):
- Drop A4, A5, A8 changes from PR #29 (they dissolve in v0.4)
- Drop `CateringQuoteRenderFailed` audit class addition (becomes `CateringQuoteHallucinationDetected` in v0.4)
- Drop `_normalize_dietary_tags()` in apply-script (LLM handles)
- Keep everything else (schema, idempotency, phone canon, migration, smoke, audit anchors, A3 menu-load three-way, A9 collision)

After Path B carve: PR #29 ships ~2000 LOC of durable hardening. v0.4 then adds ~450 LOC net (300+ removed, 750+ added) for the LLM drafting flow.

## 8. Open design questions

1. **SKILL invocation mechanism**: how does Python signal "draft quote here" → Hermes? Re-read hermes-gateway source. Options: stdout-marker, special exit code, separate phase script, or just have the SKILL itself orchestrate (recommended).
2. **Owner-voice sample collection**: triggers automatically on every SENT_TO_CUSTOMER? Or owner manually tags "this was good"? **Recommend automatic + decay** — most recent 3 within the last 90 days.
3. **Cold-start without samples**: prompt-only tone profile or `tone_profile` config field? **Recommend config field** — owners can write a one-paragraph hint during onboarding.
4. **Fallback path on LLM failure**: keep template as a feature-flagged fallback, OR refuse approve with a clear "quote-drafting failed, please retry" message? **Recommend refuse + retry** — silent template fallback would be exactly the silent-failure pattern v0.3 just hardened against.
5. **Owner card display of LLM-drafted quote**: replace `catering_approval_card_to_owner.txt` to show `quote_text` verbatim? **Recommend yes** — single-source-of-truth for what the customer will see.
6. **What about edits?** Owner sends `#A3F2X edit make it 30 not 50`. Should the LLM re-draft incorporating the edit? **Yes** — same SKILL, with the `--edit-text` injected into the context bundle. This is actually one of the strongest arguments for v0.4: edits today produce a re-rendered template that may not naturally weave in the edit; LLM drafting handles it elegantly.

## 9. Pipeline status

- ✅ v0.4 design (this doc)
- ⏳ 5 design reviews (parallel)
- ⏳ Build (3 commits)
- ⏳ PR + 5 code reviews
- ⏳ Pre-merge VPS validation
- ⏳ Merge + deploy + 20-min soak
- ⏳ Owner-voice sample backfill (one-time migration: extract last 3 SENT quotes per customer)

## 10. Decision matrix for owner-card vs customer-quote architecture

| Aspect | Owner approval card | Customer quote |
|---|---|---|
| Template-rendered? | YES | NO (v0.4 LLM-drafted) |
| Why? | Owner needs deterministic preview; what owner sees IS what gets sent | Customer-facing tone is the value prop; "owner's voice" requires LLM |
| Hallucination risk | None (mechanical) | Mitigated via anti-hallucination guards |
| State integrity | quote_text persisted (Q1 fix) | quote_text persisted (Q1 fix) |

**Both paths persist `quote_text` on the lead.** The difference is who composes it. Owner card composition is mechanical; customer quote composition is LLM-drafted from the same fact bundle.

This means the v0.3 hardening of `quote_text` persistence (Q1) is fully durable — it's the integrity layer that v0.4 builds on, not replaces.
