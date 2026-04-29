# Catering Comprehensive Fix — Build Continuation Plan

**Branch:** `fix/catering-comprehensive`
**PR:** #29 (DRAFT)
**Status as of session-end:** Commit 1 of 8 completed + pushed.
**Resumption point:** Commit 3 (highest-impact remaining work).

This file is a self-contained handoff for resuming the build. Read this and `tasks/catering-comprehensive-fix-design-v2.md` to continue.

---

## What's done (6 commits — branch `fix/catering-comprehensive`)

**As of session-end-2 (resume point):**

| # | Hash | Scope |
|---|---|---|
| 1 | `1bfd9e6` | Schema layer + smoke + migration tool |
| 1.5 | `24b0deb` | Build-continuation handoff doc |
| 3a | `2679979` | apply-script Q1+A3+A4+A5 + sentinel module-scope bugfix |
| 2 | `3dd927c` | create-script C1+C2+C4+C5+L4-CL+M1-CL+M2-CL+M3-CL |
| 5 | `6335809` | E164Phone.from_any country_code (PM2/L0) + lookup threading |
| 4a | `edb3d75` | apply-menu-update M1 narrow-except + always-archive |

VPS Linux: 19/19 B1 tests pass after each commit.

---

### Original commit-1 detail

✅ All schema-layer additions (`src/platform/schemas.py`):
- `_CODE_FULL_PATTERN`, `CATERING_TRANSITIONS` table, `is_catering_transition_allowed`, `assert_rejection_reason_complete`
- `CustomerConfig.country_code` field
- `CateringLead` mode="before"+"after" validators (S1 quote_text invariant)
- `CateringLeadStore.schema_version` + `extra="ignore"`
- `Menu.updated_by` validator, `MenuPendingUpdate.confirmation_code` regex unification
- `_BaseEntry.ts` mode="before" tz-aware shim
- `CateringOwnerDecision` mode="before" + "after" edit_text shim/validator
- `CateringLeadRejected.reason` Literal extended with `message_id_phone_mismatch`
- 6 new audit classes (CateringQuoteAttempted, CateringOwnerApprovalCardAttempted, CateringOwnerApprovalCardFailed, CateringOwnerApprovalCardSkipped, CateringOwnerEdited, CateringDeclineAttempted) — added to LogEntry union + `__all__`
- `MenuUpdateProposed.extraction_dropped_count` field

✅ `src/agents/shift/scripts/shift-agent-smoke-test.sh`: catering schema validation step (catches S1/S6/L0 at smoke-time → auto-rollback)

✅ `tools/catering-state-migrate.py`: idempotent backup-first migration tool
✅ All design docs (plan v1, design v1, design v2 — 5+5 reviews synthesized)
✅ Pytest baseline: 162 passed, 155 skipped (preserved)

---

## What's next (Commits 2-8 — priority order by customer impact)

### Commit 3 (HIGHEST PRIORITY — apply-script) — `src/agents/catering/scripts/apply-catering-owner-decision`

**Critical fixes** (per `tasks/catering-comprehensive-fix-design-v2.md` §3.3):

1. **Q1 (CRITICAL)** — persist `quote_text`:
   - After `rendered = _render_quote(lead, menu_section)` succeeds, set `lead.quote_text = rendered` BEFORE `atomic_write_json`. Currently NEVER persisted (all 9 production leads have empty quote_text).

2. **A1+A2 idempotency anchor** — new `CateringQuoteAttempted` audit row written INSIDE state-lock BEFORE bridge POST:
   ```python
   with FileLock(LEADS_LOCK):
       # 1. Write attempted-audit row FIRST
       _log(CateringQuoteAttempted(
           ts=now, lead_id=lead.lead_id,
           original_message_id=lead.original_message_id,
           code=code,
       ))
       # 2. Mutate state
       lead.quote_text = rendered                                  # Q1 fix
       lead.status = "OWNER_APPROVED"
       lead.updated_at = now
       atomic_write_json(LEADS_PATH, store)
   # 3. Release lock, then bridge POST
   ok, mid_or_err = _bridge_post(...)
   if ok:
       with FileLock(LEADS_LOCK):
           lead.status = "SENT_TO_CUSTOMER"
           lead.updated_at = customer_now(...)
           atomic_write_json(LEADS_PATH, store)
   ```
   On retry, check for existing `CateringQuoteAttempted{lead_id, original_message_id}` audit → return idempotent_replay.

3. **A3 — narrow except in `_load_menu_filtered`** with three-way return:
   ```python
   def _load_menu_filtered(...) -> tuple[list[MenuItem], int, Literal["ok","absent","corrupt","io_error"]]:
       if not MENU_PATH.exists():
           return [], 0, "absent"  # OK to proceed — first-run customer
       try:
           menu, _ = load_model(MENU_PATH, Menu)
       except RuntimeError:  # corrupt-after-quarantine
           return [], 0, "corrupt"
       except (FileNotFoundError, PermissionError, OSError):
           return [], 0, "io_error"
       except ValidationError:
           return [], 0, "corrupt"
       # ... continue with filter logic, return (filtered, total, "ok")
   ```
   Approve flow refuses on `corrupt`/`io_error` (emit InvariantViolation + Pushover + EXIT_DEPENDENCY_DOWN). Allows `absent` (empty menu section).

4. **A4 — template format error must be loud**:
   ```python
   try:
       tmpl = template_path.read_text(encoding="utf-8")
       return tmpl.format(**fields)
   except KeyError as e:
       _log(InvariantViolation(check="catering_template_format", detail=str(e)))
       _send_pushover(title="Catering template format error", message=...)
       sys.exit(EXIT_DEPENDENCY_DOWN)
   ```

5. **A5 — off_menu_items in customer quote** — extend `_render_quote` substitution dict + template:
   ```python
   off_menu_str = ""
   if lead.extracted.off_menu_items:
       off_menu_str = "\n\n*Custom requests we'll discuss:*\n  - " + "\n  - ".join(lead.extracted.off_menu_items)
   fields["off_menu_items_section"] = off_menu_str
   ```
   And add `{off_menu_items_section}` slot in `catering_quote_to_customer.txt`.

6. **A6 — reject path messages customer** — new template + new `CateringDeclineAttempted` audit anchor + bridge POST:
   ```python
   if args.decision == "reject" and args.reason:
       decline_text = _render_decline(lead, args.reason)
       with FileLock(LEADS_LOCK):
           _log(CateringDeclineAttempted(...))
           lead.status = "OWNER_REJECTED"; atomic_write_json(...)
       ok, _ = _bridge_post(decline_text, lead.customer_phone)
       # ...
   ```

7. **A7-A9 + M1-M4-A + L1-A** — apply per design v2 §3.3.

### Commit 2 (create-catering-lead)

**Critical fixes** per design v2 §3.2:
- C1: catch `ValidationError` in lead constructor → `EXIT_INVALID_INPUT`
- C2: phone-mismatch idempotency softened (return existing lead, emit `InvariantViolation` + Pushover; do NOT crash UX)
- C3: bridge retry with one-retry-with-backoff
- C4: emit `CateringOwnerApprovalCardFailed` on bridge POST failure
- C5: JID-empty → emit `CateringOwnerApprovalCardSkipped` + `EXIT_DEPENDENCY_DOWN`
- C6: off-menu running-budget arithmetic fix
- M7-CL: KEEP `extra="ignore"` (per design v2 — don't switch to `extra="allow"`)
- L4-CL: `args.message_id.strip()` and `args.customer_phone.strip()`
- Add `CateringOwnerApprovalCardAttempted` audit row in state-write lock BEFORE bridge POST (mirrors A1)

### Commit 4 (menu scripts)

`parse-menu-photo`:
- PM1: pending-overwrite handling with TTL + clock-skew check
- M2-PM: extraction_dropped_count tracking
- M3-PM: counter race fix via FileLock
- M4-PM: OSError in image_path.read_bytes()
- L3-PM: empty-items menu refused

`apply-menu-update`:
- M1: narrow except for prior-menu read; never overwrite on corruption
- PM3: code regex via `_CODE_FULL_PATTERN`

### Commit 5 (lookup + phone canon)

- Extend `E164Phone.from_any` to accept `country_code` parameter
- `lookup-prior-leads-by-phone` passes `cfg.customer.country_code`
- Catch ValueError → return `{"lookup_status": "invalid_phone", ...}`
- Fix `rstrip("@s.whatsapp.net")` bug → use `split("@", 1)[0]`

### Commit 6a (test resurrection)

Port `tests/test_catering_v02_scripts.py` and `tests/test_lookup_prior_leads.py` to use `_b1_helpers.py` working pattern. Replace 5 broken `spec_from_file_location` callsites with calls to `_b1_helpers.run_create / run_apply / lookup_prior_leads_by_phone_helper`.

Extend `_b1_helpers`:
- `run_create`: add `customer_tz: Optional[str]` + `path_overrides: dict[str, Path]` params
- `run_apply`: add `now_override: Optional[datetime]` + `path_overrides: dict`
- `mk_lead`: add `quote_text: Optional[str] = None` defaulting to `"<test-quote-content>"` for post-NEW statuses

### Commit 6b (new tests — ~80)

Per design v2 §3.10. Distribute across:
- `tests/test_catering_schemas.py`: ~60 schema-related tests (S1, S2, S3, S4, S6, L1, audit classes)
- `tests/test_catering_v02_scripts.py`: extend with Q1, A1+A2, A3-A9 tests
- `tests/test_catering_lookup.py` or extend existing: PM2, L0 tests
- NEW `tests/test_catering_parse_menu_photo.py`: PM1 + parse-menu tests

### Commit 6c (staging-tests script)

`tools/run-catering-staging-tests.sh` — scp tests + pytest against staging-new on VPS.

---

## Deploy procedure (after all commits land)

Per design v2 §4 (corrected sequence):
1. `bash tools/build-deploy-tarball.sh`
2. `scp shift-agent-deploy.tgz main-vps:/tmp/`
3. `ssh main-vps 'sudo tar xzf /tmp/shift-agent-deploy.tgz -C /opt/shift-agent/staging-new/'`
4. **Pre-deploy gate** — uses NEW schema from `staging-new/`
5. **Run migration** — `sudo -u shift-agent /opt/shift-agent/venv/bin/python staging-new/tools/catering-state-migrate.py --leads-path /opt/shift-agent/state/catering-leads.json`
6. **Run staging tests** — `bash tools/run-catering-staging-tests.sh`
7. **Deploy** — `sudo /usr/local/bin/shift-agent-deploy.sh` (smoke includes catering schema validation now)
8. **20-min soak** — `tail -F /opt/shift-agent/logs/decisions.log | grep -E 'invariant_violation|catering.*failed|ValidationError'`
9. Manual rollback procedure documented in design v2 §5.4 if soak surfaces issues

---

## Resumption checklist

To resume the build:
1. `git checkout fix/catering-comprehensive` (already on branch if mid-session)
2. `git pull` if remote ahead
3. Read `tasks/catering-comprehensive-fix-design-v2.md` (full design)
4. Read this file (continuation context)
5. Start with Commit 3 (highest impact: Q1 + A1+A2 + A5)
6. Commit incrementally; each commit must pass `python -m pytest tests/ -q`
7. After all 8 commits: spawn 5 parallel code reviews, synthesize, fix, push
8. Pre-merge: run `tools/run-catering-staging-tests.sh` against VPS
9. Merge + deploy + 20-min soak per design v2 §4

---

## Estimated remaining effort

- Commit 2: ~30 min wall-clock (15 turns)
- Commit 3: ~45 min wall-clock (25 turns) — most complex
- Commit 4: ~25 min
- Commit 5: ~15 min
- Commit 6a: ~20 min
- Commit 6b: ~60 min (large LOC of tests)
- Commit 6c: ~10 min
- 5 parallel code reviews: ~10 min wall (parallel)
- Fix synthesis + push: ~30 min
- Pre-merge VPS validation: ~10 min
- Merge + deploy + soak: ~30 min

**Total: ~4–5 hours of focused work** across multiple sessions.

After this lands: same E2E + comprehensive fix exercise for Shift Agent (task #118).
