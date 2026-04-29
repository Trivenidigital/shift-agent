# Design v2 — Catering v0.4: LLM-drafted customer quote

**Status:** Final after 5-design-review synthesis (2026-04-29). Supersedes `tasks/catering-v04-llm-drafted-quote-design.md`.

**Source reviews:** schema/migration (Review 1), Hermes/SKILL integration (Review 2), silent-failure (Review 3), ops/deploy (Review 4), test-strategy (Review 5). ~50 findings across the 5 lenses.

**Drift-check tag:** extends-Hermes (adds a SKILL; no Hermes-internal change)

---

## 1. Structural change from v1 (review consensus)

**Single most important change**: move the `AWAITING_OWNER_APPROVAL → OWNER_APPROVED` state transition OUT of prepare INTO finalize.

**Why:** Review 3's recommendation, corroborated by Reviews 1 (H2 stuck-state), 4 (M4 re-entrant ambiguity), 5 (C2 phase-split idempotency).

**v1 design:**
```
prepare:
  - lock-1: write CateringQuoteAttempted audit + status→OWNER_APPROVED + persist
  - return context bundle
SKILL:
  - LLM drafts quote_text
finalize:
  - anti-hallucination guards
  - persist quote_text
  - bridge POST
  - lock-2: status→SENT_TO_CUSTOMER
```
**Problem**: if SKILL never invokes finalize (rate limit, drift, crash), lead stuck in `OWNER_APPROVED` indefinitely with no `quote_text`. Watchdog needed. Retry-detection ambiguous.

**v2 design:**
```
prepare (pure read; ZERO state mutation):
  - lock-read leads, find lead by code
  - validate state == AWAITING_OWNER_APPROVAL
  - load filtered menu (3-way return; refuse on corrupt/io_error)
  - assemble owner-voice samples
  - return context bundle JSON on stdout
SKILL (LLM):
  - draft quote_text per draft_catering_customer_quote prompt
  - pipe quote_text on stdin to finalize
finalize (the only state-mutator):
  - read quote_text from stdin (closes argv-limit issue from Review 2)
  - anti-hallucination guards (rewritten — see §3)
  - if guards fail → emit CateringQuoteHallucinationDetected, exit EXIT_HALLUCINATION (NEW: 7)
  - lock-1: status AWAITING→OWNER_APPROVED + persist quote_text + write CateringQuoteAttempted anchor
  - release lock
  - bridge POST quote_text
  - on success: lock-2 → status OWNER_APPROVED→SENT_TO_CUSTOMER + write CateringQuoteSent
  - on bridge failure: lead stays OWNER_APPROVED with anchor; retry detects anchor, skips re-draft, re-POSTs only
```

**Net effect:** prepare is idempotent and read-only. If SKILL drifts/crashes/dies: lead is still in `AWAITING_OWNER_APPROVAL` with no audit row. Owner can re-approve by resending the WhatsApp code; no operator intervention needed. Retry detection lives entirely in finalize via the anchor.

**LOC impact**: zero. Risk reduction: substantial. This single change dissolves Review 1's H2, Review 3's C4 + H4, Review 4's M4, Review 5's C2.

---

## 2. Pre-v0.4 backport patch (must ship before v0.4)

Reviews 1 (H1), 3 (H3), 4 (C1) all flagged: `CustomerConfig` is `extra="forbid"`. Adding `tone_profile` in v0.4 means rollback to v0.3 fails the SMOKE GATE (per Review 4) — auto-rollback can't complete cleanly, operator stuck SSH-fixing config.

**Fix:** ship a tiny patch onto **v0.3** branch (PR #29's `fix/catering-comprehensive`) that flips `CustomerConfig` to `extra="ignore"` BEFORE v0.4 lands. After this patch is merged + deployed, v0.4 can safely add `tone_profile`. Rollback then drops the field silently.

**Equivalent issue for `LogEntry`** (Reviews 1 C1, 4 C2): once v0.4 ships and writes `CateringQuoteHallucinationDetected` entries to decisions.log, rolling back to v0.3 means readers (`shift-agent-tail-logger.py`, `shift-agent-reconcile.py`) crash on the unknown discriminator. The standard fix is a try/except-skip-unknown wrapper around `TypeAdapter(LogEntry).validate_json(line)`. **Backport this wrapper to v0.3 too** so the rollback is safe.

**Two backport commits to v0.3 branch (PR #29) before v0.4 merge:**
1. `CustomerConfig.model_config = ConfigDict(extra="ignore")` (one-line + comment + 2 tests for fwd/bwd compat)
2. Wrap log-readers in try/except-skip-unknown (`shift-agent-tail-logger.py`, `shift-agent-reconcile.py`, plus any other consumers of `LogEntry` discriminated union)

These are **deploy-blockers for v0.4** — flagged.

---

## 3. Anti-hallucination guards — rewritten (Reviews 1 C2, 3 C1+C2+C3, 5 C1+H2)

v1's guards have multiple silent-failure paths. v2 rewrites:

```python
import re
from decimal import Decimal

# Strip thousands separators + Unicode spaces before numeric comparison.
_PRICE_TOKEN_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_NORMALIZE_PRICE_CHARS = re.compile(r"[,\s  ]")


def _extract_prices(text: str) -> set[Decimal]:
    """Return all $-prefixed numeric values in text as Decimals (normalized).
    Catches: $25, $25.00, $1,200, $1,200.50, '$ 25', '$ 1 200'.
    Misses (intentional): €50, ¥100, $1.5K, plain '25' without $.
    """
    out: set[Decimal] = set()
    for raw in _PRICE_TOKEN_RE.findall(text):
        cleaned = _NORMALIZE_PRICE_CHARS.sub("", raw)
        try:
            out.add(Decimal(cleaned))
        except (ValueError, ArithmeticError):
            pass
    return out


def _validate_drafted_quote(quote_text: str, ctx: dict) -> tuple[bool, str]:
    """5 guards. Returns (ok, reason)."""
    # Guard 1: headcount appears as a count phrase, not a substring of larger numbers
    headcount = ctx["headcount"]
    headcount_phrases = [
        rf"\b{headcount}\s+(guests?|people|attendees?|pax|persons?)\b",
        rf"\bfor\s+{headcount}\b",
    ]
    if not any(re.search(p, quote_text, re.IGNORECASE) for p in headcount_phrases):
        return False, f"headcount {headcount} not present as count phrase"

    # Guard 2: event_date present in ISO OR a normalized natural form the prompt enumerated
    event_date = ctx["event_date"]
    event_natural = ctx.get("event_date_natural")  # e.g. "Sept 5, 2026"
    if event_date not in quote_text and (not event_natural or event_natural not in quote_text):
        return False, f"event_date {event_date} not present (neither ISO nor natural form)"

    # Guard 3+4 (combined): every $-price in drafted quote must match a menu item;
    # at least one menu item must appear (case-insensitive).
    drafted_prices = _extract_prices(quote_text)
    valid_prices = {Decimal(str(item["price_usd"])) for item in ctx["filtered_menu_items"]
                    if item.get("price_usd") is not None}
    fabricated = drafted_prices - valid_prices
    if fabricated:
        return False, f"fabricated prices not in menu: {fabricated}"

    # Guard 5 (NEW per Review 3 C2): quote must mention at least one menu item OR
    # explicitly defer pricing. Refuse zero-content quotes that bypass guards 1-4.
    quote_lower = quote_text.lower()
    has_menu_item = any(item["name"].lower() in quote_lower
                        for item in ctx["filtered_menu_items"])
    has_pricing_defer = any(phrase in quote_lower for phrase in [
        "pricing depends", "we'll firm up", "quote separately", "quote each item",
    ])
    if not has_menu_item and not has_pricing_defer:
        return False, "quote contains no menu items and no pricing-defer phrasing"

    # Guard 6 (NEW per Review 3 L2): off-menu items must NOT appear with a $-price
    # within 30 chars (LLM should defer pricing for them).
    for off_item in ctx.get("off_menu_items", []):
        for m in re.finditer(re.escape(off_item.lower()), quote_lower):
            window = quote_text[max(0, m.start()-30):m.end()+30]
            if _PRICE_TOKEN_RE.search(window):
                return False, f"off-menu item {off_item!r} appeared with a price"

    return True, "ok"
```

**Net change from v1:** 4 guards → 6 guards. Added: zero-content guard (G5), off-menu-no-price guard (G6). Hardened: regex normalizes thousands separators / unicode whitespace; numeric comparison via `Decimal` (closes Review 1 C2 + Review 5 H2 false-positive on `$4.0`). Headcount + event_date checks anchored to phrase context (closes Review 3 C3).

---

## 4. Other resolutions

| Concern | Reviewer(s) | Resolution |
|---|---|---|
| `--quote-text "<text>"` argv limit (4096 chars) | 2 (CRITICAL 1) | finalize reads `quote_text` from **stdin** (piped from SKILL output). No argv limit. CLI contract: `apply-catering-owner-decision --phase finalize --code <CODE> < quote.txt` |
| Exit code collision (`EXIT_DEPENDENCY_DOWN` reused) | 2 (CRITICAL 2) | NEW `EXIT_HALLUCINATION = 7` distinct from `EXIT_DEPENDENCY_DOWN = 6`. SKILL routes 7→Pushover priority=2, 6→retry. |
| `quote_text` field on CateringLead — schema unclear | 2 (CRITICAL 3) | Pre-existing in v0.3 (`schemas.py` line 444 per current branch). Documented explicitly in v2 §6 ("no model change needed"). |
| LLM model selection | 2 (HIGH 4), 4 | **Sonnet for catering quotes** (low hallucination tolerance + financial consequence). Cost: ~$0.008/call × ~10 inquiries/week × 52 = ~$4/year per customer. Trivial. Document in §5 cost table. |
| Cold-start `tone_profile` source | 2 (HIGH 5), 1, 3, 4 | (a) Owner-onboarding fills `cfg.customer.tone_profile` (1-paragraph hint). (b) Pre-v0.4 migration `tools/catering-state-migrate.py --backfill-samples` action seeds samples from existing v0.3 SENT leads' `quote_text` (after Q1 fix took effect). (c) Hard-coded prompt fallback if both empty: "warm, professional, concise." |
| Owner-voice sample poisoning | 1 (H3), 3 (H2) | Add `CateringLeadStore.recent_sent_quotes(exclude_lead_id, n=3) -> list[str]` method. Filter sentinels (`PRE_QUOTE_DRAFT_SENTINEL`, `LEGACY_QUOTE_TEXT_SENTINEL`). Owner can flag bad samples via reply: `#A3F2X bad-tone` → adds `voice_quality="bad"` field on lead → excluded from samples. |
| Pushover routing on LLM failure | 3 (H1) | **Single source of truth**: refuse + retry. Max 2 retries with exponential backoff (1s, 4s). On terminal failure: emit `CateringQuoteSkillFailed` audit + Pushover priority=2. Lead stays `AWAITING_OWNER_APPROVAL` (per §1 structural change — prepare doesn't transition state). Operator re-sends approval to retry. **Delete v1 design's contradictory "fall back to v0.3 template" row.** |
| SKILL invocation contract | 2, 3 (M2) | Concrete contract: prepare exits 0 with stdout `{"phase":"prepare_ok","context":{...}}`. SKILL parses, drafts via Sonnet, then `subprocess.run(["apply-catering-owner-decision","--phase","finalize","--code",code], input=quote_text, text=True)`. Non-zero prepare exit means abort. |
| Migration script for sample backfill | 1 (M1), 3 (M1), 4 (H2) | `tools/catering-state-migrate.py --backfill-samples` reads `decisions.log` for `CateringQuoteSent` entries, joins to leads.json, populates an in-memory sample pool. Run pre-v0.4 deploy. |
| C16/C17 v0.3 B1 tests will break | 5 (H3) | **Rewrite C16/C17 to assert against the SKILL-input bundle (deterministic JSON contract: `filtered_menu_items`, `menu_filter_status`)** instead of LLM-output text. Migration to bundle-based assertion is a structural improvement — tests of the contract survive prompt edits. Add to v0.4 commit-2. |
| `CateringLeadStatusChange.from_status` Literal coverage for OWNER_APPROVED | 1 (L1) | Verified in v0.3 schema (line 1311 area; CateringLeadStatus Literal includes OWNER_APPROVED). No action. |
| Smoke test missing audit class instantiation | 4 (H1) | Extend `shift-agent-smoke-test.sh` step 10 to instantiate `CateringQuoteHallucinationDetected` with representative data + roundtrip via `LogEntry` union. |
| Soak monitoring no synthetic LLM exercise | 4 (M1) | Add to soak runbook: invoke a dry-run finalize with a synthetic context bundle (mocking the SKILL by piping a hand-crafted quote on stdin). Verify guards run, exit code is correct. |
| API key rotation runbook gap | 4 (M3) | One-line addition: "OPENROUTER_API_KEY now used by both `parse-menu-photo` and `apply-catering-owner-decision --phase finalize`. Rotate via `/root/.hermes/.env` reload." |

---

## 5. Schema additions (Commit 1)

```python
# v0.4 NEW audit classes — added to LogEntry union + __all__
class CateringQuoteHallucinationDetected(_BaseEntry):
    """v0.4: LLM-drafted quote failed anti-hallucination guards. Refuse approve."""
    type: Literal["catering_quote_hallucination_detected"]
    lead_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
    failed_guard: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4096)  # increased from 2000 (Review 5 L1)


class CateringQuoteSkillFailed(_BaseEntry):
    """v0.4: SKILL/LLM call failed terminally (after retries). Lead stays
    AWAITING_OWNER_APPROVAL; owner re-sends approval to retry."""
    type: Literal["catering_quote_skill_failed"]
    lead_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
    reason: Literal["llm_unreachable_after_retries", "guards_failed_2x", "timeout"]
    retry_count: int = Field(ge=0, le=10)


# v0.4 CustomerConfig addition
class CustomerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")  # NOTE: was "forbid" in v0.3 — flipped in pre-v0.4 backport patch
    name: str
    location_id: str
    timezone: str
    languages: list[str] = []
    country_code: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    # v0.4: optional 1-paragraph hint for owner's tone, used by
    # draft_catering_customer_quote when no SENT-history samples exist.
    tone_profile: Optional[str] = Field(default=None, max_length=2000)


# v0.4 method on CateringLeadStore (new helper, not a field)
class CateringLeadStore(BaseModel):
    # ... existing ...

    def recent_sent_quotes(self, exclude_lead_id: str, n: int = 3) -> list[str]:
        """v0.4 §4 (Review 1 H3 + Review 3 H2): return last N owner-approved
        SENT_TO_CUSTOMER quotes excluding sentinels and the current lead.
        Single source of truth for sample-collection — sentinel-filter logic
        lives here so it can't drift across script callsites.
        """
        eligible = [l for l in self.leads
                    if l.status == "SENT_TO_CUSTOMER"
                    and l.lead_id != exclude_lead_id
                    and l.quote_text
                    and l.quote_text not in (PRE_QUOTE_DRAFT_SENTINEL,
                                              LEGACY_QUOTE_TEXT_SENTINEL)
                    and getattr(l, "voice_quality", "neutral") != "bad"]
        eligible.sort(key=lambda l: l.updated_at, reverse=True)
        return [l.quote_text for l in eligible[:n]]


# Optional v0.4 field on CateringLead for sample-poisoning break-loop
class CateringLead(BaseModel):
    # ... existing ...
    voice_quality: Literal["good", "neutral", "bad"] = "neutral"
```

---

## 6. Build sequence — revised commit count

| # | Scope | LOC est | **Test count (revised per Review 5)** |
|---|---|---|---|
| **0a** (pre-v0.4 backport) | `CustomerConfig.extra="ignore"` flip | ~5 | **2** (fwd-compat: extra field accepted; bwd-compat: missing field accepted) |
| **0b** (pre-v0.4 backport) | LogEntry-reader skip-unknown wrapper in `shift-agent-tail-logger.py` + `shift-agent-reconcile.py` | ~30 | **4** (each reader: known type + unknown discriminator skip) |
| 1 | Schema: `CateringQuoteHallucinationDetected`, `CateringQuoteSkillFailed`, `CustomerConfig.tone_profile`, `CateringLead.voice_quality`, `CateringLeadStore.recent_sent_quotes()` method, LogEntry/exports updates | ~80 | **12** (audit classes: 4×2; tone_profile: 4; voice_quality: 2; recent_sent_quotes: 2 — sample pool + sentinel filter) |
| 2 | `draft_catering_customer_quote` SKILL file + apply-script `--phase prepare\|finalize` split + 6 anti-hallucination guards + remove `_render_quote()` and `_format_menu_section()` and `catering_quote_to_customer.txt` template | ~350 (net -250 vs current) | **42** (6 guards × 4 cases = 24; phase-split idempotency = 8; SKILL mock fixture = 4; C16/C17 rewrite = 4; integration happy path = 2) |
| 3 | Owner-voice sample backfill in migration tool + cold-start fallback ladder (samples → tone_profile → hardcoded default) + `--backfill-samples` action + bad-tone owner reply parser + `voice_quality` field flow | ~150 | **18** (sample filter = 8; cold-start ladder = 4; backfill migration = 4; bad-tone reply = 2) |

**Test totals**: 6 (backport) + 72 (v0.4) = **78 tests** vs v1's 40. Matches Review 5's "realistic floor 60-75" and the v0.3 PR #29 precedent ("final was 80+").

**Commit-1 deferred**: don't add `CateringQuoteHallucinationDetected` or `CateringQuoteSkillFailed` to `LogEntry` union UNTIL the pre-v0.4 backport patch (commit 0b — skip-unknown wrapper) is deployed. Otherwise rollback breaks readers immediately.

---

## 7. Deploy sequence

1. PR #29 (v0.3 hardening, currently DRAFT, paused per architecture review) — merge first
2. **Commit 0a backport patch** to v0.3 branch: `CustomerConfig.extra="ignore"`. Deploy to VPS.
3. **Commit 0b backport patch**: skip-unknown wrappers in log-readers. Deploy to VPS. Verify rollback-from-v0.4 will work.
4. v0.4 PR (commits 1-3) opens. 5 design reviews (this doc) + 5 code reviews + merge.
5. Pre-deploy: `tools/catering-state-migrate.py --backfill-samples` populates voice samples from existing leads.
6. Deploy v0.4 via standard tarball flow. Smoke test extension validates new audit classes.
7. **20-min soak** with synthetic LLM exercise (dry-run finalize with hand-crafted quote on stdin).

---

## 8. Pipeline status

- ✅ Plan + Design v1 + 5 design reviews
- ✅ **Design v2 (this doc)** — 50 review concerns synthesized; structural state-machine simplification (§1)
- ⏳ User approval to proceed (needed: structural change scope, pre-v0.4 backport, 78-test scope)
- ⏳ Build (3 commits + 2 backport commits, ~615 LOC net change)
- ⏳ PR + 5 code reviews
- ⏳ Pre-merge VPS validation (smoke + staging-tests)
- ⏳ Merge + deploy + 20-min soak with synthetic exercise

---

## 9. Key inversions vs v1 design

| v1 | v2 |
|---|---|
| State transition in prepare phase | **State transition in finalize phase only** (prepare is read-only) |
| Stuck-state needs watchdog | No stuck-state; SKILL drift = lead stays AWAITING + user re-sends code |
| 4 anti-hallucination guards | **6 guards** with Decimal-based price comparison + zero-content + off-menu-price |
| `--quote-text "<text>"` CLI arg | **stdin pipe** (closes argv-limit issue) |
| `EXIT_DEPENDENCY_DOWN` reused for hallucination | **NEW `EXIT_HALLUCINATION = 7`** distinct |
| `cfg.customer.tone_profile` added; rollback risk noted but unmitigated | **Pre-v0.4 backport patch flips `CustomerConfig.extra="ignore"`** before v0.4 lands |
| `CateringQuoteRenderFailed` audit (template error) | **`CateringQuoteSkillFailed` audit** (LLM error) — distinct concept |
| 40-test build estimate | **78 tests** including 6 backport tests |
| Kimi/Haiku model | **Sonnet** for catering quotes (low hallucination tolerance) |
| Owner-voice samples filtered in-script | **`CateringLeadStore.recent_sent_quotes()` method** as single source of truth |
| Sample poisoning has no break-loop | **`voice_quality` field + bad-tone owner reply** breaks the loop |
| Self-contradicting "refuse + retry" vs "fall back to template" | **Refuse + retry only** (table contradiction deleted) |
| LLM call cost ~$0.0005/call | **~$0.008/call (Sonnet)** documented honestly |
