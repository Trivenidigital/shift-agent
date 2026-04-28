# Design v1 review consolidation (5 parallel agents)

## Critical (must fix in Design v2)

- **DC1.** `_prepare_inbound_message_text` actual signature is keyword-only
  with `event: MessageEvent`. Design v1's `raw_event: dict` is wrong — must
  use `event.raw_message` (already populated). Two call sites in `run.py`:
  line 4488 (primary) AND line 10773 (queued follow-up).
- **DC2.** Existing `[{user_name}] {message_text}` prefix at line 3908 means
  block ordering matters. Inject SHIFT-AGENT block FIRST so it's
  unconditionally line 1. User-name prefix becomes part of line-2 content.
- **DC3.** `bridge.js _writeLidCacheEntry` concurrent-write race: two async
  calls in flight read same baseline → second rename overwrites first → data
  loss. Fix: serialize via module-level promise chain.
- **DC4.** Block-format quote escaping: `q(v)` must escape `"` to `\"`. Even
  though current regex validators reject quotes, future field additions
  could leak.
- **DC5.** `_sanitize_user_body` regex bypassable via Unicode homoglyphs
  (Cyrillic 's'), zero-width chars, bidi marks. Fix: NFKC normalize body +
  strip zero-width/bidi BEFORE the regex match.
- **DC6.** `_resolve_sender_context`: `senderPhone` overwrites senderId-derived
  phone unconditionally. Risk: corrupt/inconsistent fields silently demote
  owner. Fix: use senderPhone only when `out["phone"] is None`.
- **DC7.** `dispatch_shift_agent/SKILL.md` (deployed) STILL routes on
  `fromMe` alone. C3 vulnerability remains in deployed code. Patch is in
  the design but not yet applied.
- **DC8.** `LidLearned` not added to `LogEntry` discriminated union →
  audit-log readers crash on `lid_learned` entries. Must add to union.
- **DC9.** `RawInbound` + `UnknownSenderDeclined` both need
  `Optional[E164Phone]` + `sender_lid` + `model_validator` requiring at
  least one. Design v1 missed `UnknownSenderDeclined`.
- **DC10.** Audit log write must come BEFORE roster mutation. Use
  `safe_io.ndjson_append` (existing helper at safe_io.py:164) — not raw
  `open(append)`.

## High

- **DH1.** Add deterministic `validate-sender-block` Python helper. SKILL
  calls it as first tool; LLM never parses block strings itself.
- **DH2.** `bridge.js` write needs `fsync` before rename + lid-learn must
  guard against empty-file reads.
- **DH3.** lid-learn must ALSO acquire `flock(CONFIG)` for owner edits
  (cockpit uses different lock target). Pattern: take roster lock first,
  config lock second (consistent ordering, no deadlock).
- **DH4.** lid-learn must use `atomic_write_yaml` (or tmp+rename inline)
  for config.yaml. Direct `write_text` is non-atomic.
- **DH5.** lid-learn must trim applied pairs from cache to bound size.
- **DH6.** Module-level feature-flag constant: `_INJECT_SENDER_CONTEXT =
  os.environ.get(...) == "1"` at run.py top, not per-message lookup.
  Cleaner test patching.
- **DH7.** `identify-sender` must accept `--roster` / `--config` or
  env var override (`SHIFT_AGENT_ROSTER_PATH`) so E2E tests can run in
  CI against temp fixtures. Currently hard-codes `/opt/shift-agent/`.
- **DH8.** Test for "no rewrite when content unchanged" must use content
  hash, not mtime. mtime always advances on `os.replace`.

## Medium / Defer to follow-up

- **DM1.** Trigger-file mechanism (SIGUSR1 or sentinel) for instant
  lid-learn after cache write. Reduces 4-min stale window. **Defer to
  Phase D follow-up** — cron at 5-min is acceptable for v1.
- **DM2.** Cross-language regex source-of-truth (JS module + Python
  module). Document only for v1; refactor later.
- **DM3.** Patch baseline file ownership: store baseline at
  `/root/shift-agent-patch-baseline.txt` (root:root 0600) to prevent
  shift-agent tampering. Defer.
- **DM4.** Bridge contract test (canary for field-rename refactors). Add
  in test suite.
- **DM5.** check-shift-agent-patch.sh: resolve baseline path relative to
  script location. Small change, include now.
- **DM6.** Separate `shift-agent-patches-deploy.sh`: don't append to
  cockpit deploy.sh. Include now.

## Items rejected with rationale

- **Hard size cap on lid-cache.json**: low likelihood of unbounded growth
  for our scale (≤20 employees). DH5 trim handles the realistic case.
- **lid-cache.json phone-keyed dict refactor (#6 from agent #4)**:
  array-of-pairs is fine for tens to hundreds of pairs. Schema v=1 lets
  us migrate later. No refactor now.
- **HMAC signing of sender block**: closed via DC4+DC5 sanitization.
  Unnecessary complexity.
