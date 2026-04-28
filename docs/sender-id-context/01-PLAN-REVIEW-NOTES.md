# Plan v1 review consolidation (5 parallel agents)

Synthesized findings, deduplicated, ranked by severity. Each item is mapped
to a concrete fix in Plan v2.

## Critical (must address before design)

- **C1. Existing Hermes pattern is the correct integration point — not
  build_session_context_prompt at line 4084.** Agent #4 traced the code:
  `_prepare_inbound_message_text` at `gateway/run.py:3886` already prepends
  `[{user_name}] {message_text}` for shared multi-user sessions (line 3908).
  This is the exact pattern Plan v1 reinvented. v2 extends THIS function with
  the sender block, matching Hermes' own convention. Smaller blast radius,
  matches existing semantics, easier to upstream.
- **C2. Prompt-injection of the sender block.** Reviewers #1, #2, #3, #5 all
  flagged that any sender can include `[shift-agent-sender ...]` in their own
  message body and Kimi may resolve to two blocks (the legit one Hermes
  prepended + the user-controlled one). Fix: sanitize user body before
  prepending — strip any pre-existing `[shift-agent-sender ` substring.
- **C3. `fromMe=true` privilege escalation** by spoofing in body (#3). Fix:
  the `dispatch_shift_agent` SKILL must NEVER use `fromMe` alone for owner
  routing. It must cross-check `phone_normalized == config.owner.phone`. Block
  format keeps `fromMe` for diagnostic value but skill ignores it for
  routing.
- **C4. Schema rollback bricks files** (#1, #2, #3). `Employee`, `OwnerConfig`,
  `Config` all use `extra="forbid"`. Adding `lid` then reverting `schemas.py`
  causes ValidationError on every roster/config read. Fix: rollback runbook
  must `jq 'del(.employees[].lid)'` before reverting code; PLUS `Employee`
  config will be changed to `extra="ignore"` (matches `Roster` already at
  line 118).
- **C5. Phase A "off-by-default" claim is wrong** for bridge.js (#1, #2, #5).
  bridge.js writes the cache always. Fix: gate bridge.js cache write on
  `WHATSAPP_LID_CACHE_WRITE` env (default off in Phase A; on in Phase B).

## High

- **H1. lid-cache.json must use atomic tmp+rename, not cross-language flock.**
  bridge.js (Node) and Python use different lock primitives. Fix: bridge.js
  writes via `fs.writeFileSync(tmp); fs.renameSync(tmp, target)`. Learner
  reads with no lock; reads are always coherent because rename is atomic.
- **H2. lid-cache.json schema must be versioned** (#5). v=1 top-level field
  + `pairs[]` array form. Learner refuses unknown versions.
- **H3. roster.json lock contention with cockpit PATCH** (#3). `lid-learn`
  must use the SAME lock-target as `roster_session()` — the roster file path
  itself, not a sibling lock file. (Verify in Plan v2 — current `flock()`
  helper takes path + .lock; need to be consistent.)
- **H4. `RawInbound.sender_phone` is required E164Phone** (#1). LID-only
  senders crash audit logging. Fix: relax `sender_phone` to `Optional`,
  add `sender_lid: Optional[str]`, add a model_validator requiring at least
  one to be set.
- **H5. `identify-sender` LID input must be unambiguous.** Today's regex
  `^\+\d{10,15}$` would coincidentally accept some LID-derived strings (#1).
  Fix: explicit input dispatch — if input ends with `@lid`, parse as LID;
  else if starts with `+`, parse as phone; else error. Never silently
  canonicalize an LID into a phone.
- **H6. Hermes patch verification must be fail-closed** (#3, #5). The
  `tools/check-shift-agent-patch.sh` must `exit 1` (not warn) on missing
  markers AND on anchor-symbol drift. Block deploy.
- **H7. SKILL.md absent-block fallback must be fail-closed** (#3). When
  `[shift-agent-sender ...]` block is missing from the user message,
  dispatch treats sender as `unknown` — never as owner. Document and test.
- **H8. Block format needs `v=1` and quoted values** (#1, #5). Format:
  `[shift-agent-sender v=1 platform=whatsapp phone="+17329837841"
  lid="201975216009469@lid" fromMe=true chat_id="918522041562@s.whatsapp.net"]`.
  Helper validates each value against an explicit regex before formatting.

## Medium

- **M1. lid-learn idempotency** — track `processed_at` per cache entry; on
  conflict (existing lid != cache lid), the helper logs the conflict to
  `decisions.log` and uses last-wins (which matches "device re-paired"
  expectation), but emits a warning so a flapping cache becomes visible.
- **M2. Concurrent multi-bridge** — out of scope (we have one bridge).
  Document the assumption in the plan; tests assume single-bridge.
- **M3. Cron entry environment** — explicit Python interpreter path,
  PYTHONPATH, MAILTO. Mirrors the existing JWT rotation cron pattern.
- **M4. PII classification** — LID is treated as personal data. Audit log
  redaction extended to mask LID in exports (last 6 digits visible only,
  matching phone redaction policy).
- **M5. New E2E test must exercise the new block format** end-to-end
  (#2). Existing `test_e2e_proposal_lifecycle.py` does NOT use the new
  format; we add `test_e2e_sender_id_context.py`.
- **M6. Owner re-pair invalidation test** — explicit test case where
  cache contains a different LID than what's currently in roster, and
  `lid-learn` updates correctly with audit log entry.

## Low / future-proofing

- **L1. Upstream API surface naming** (#5) — name the helper
  `_resolve_sender_context` returning a `dict`; thin shim renders to text.
  This pre-positions the work for upstream contribution as a Hermes plugin
  interface.
- **L2. Sender block inside SKILL.md must be parsed in one place.**
  `dispatch_shift_agent` parses the block, extracts phone/LID/fromMe,
  then passes them as named inputs to handle_sick_call /
  handle_owner_command. Other skills DO NOT re-parse.
- **L3. WhatsApp adapter render path** — confirm Hermes does NOT
  markdown-process the user message body before sending to Kimi. If it does,
  the `[...]` characters need different delimiter (`<<<...>>>` or similar).
  Verified during design phase.

## Items I'm explicitly choosing NOT to do (with rationale)

- **HMAC signing of the block.** Reviewer #3 suggested it as defense-in-depth
  against impersonation. Sanitization (C2) closes the same window with
  smaller surface; HMAC adds a key-management burden. Revisit if a stronger
  threat model emerges.
- **Bumping lid-cache.json schema_version on the same upgrade**. v=1 is the
  initial format; we don't anticipate v=2 in this PR.
- **Hook-based sender injection** (suggested by #5). After investigation
  (agent #4 confirmed at line 3886), the existing `_prepare_inbound_message_text`
  IS the right place. Hooks fire too late (after message_text finalized).
  We use the existing pattern instead of inventing a new one.
