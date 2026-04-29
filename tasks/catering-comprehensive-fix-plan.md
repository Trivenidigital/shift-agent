# Plan — Catering Comprehensive Fix (all actionable findings)

> ⚠️ **SUPERSEDED** — see `tasks/catering-comprehensive-fix-design-v2.md`
> for the current design (post 5-plan-review + 5-design-review synthesis).
> This plan is the v0 surface inventory that fed Design v1; kept for the
> pipeline trail.

**Drift-check tag:** extends-Hermes (no Hermes-internal change; schema + script + test additions only)

**Source:** `tasks/catering-e2e-report.md` — 63 findings from 4-subagent E2E audit (10 CRITICAL, 19 HIGH, 19 MEDIUM, 15 LOW). User directive: "fix everything", full autonomous pipeline.

**Branch:** `fix/catering-comprehensive`

---

## 1. Scope

**In scope (50 of 63 findings + T1 test resurrection):**

All actionable bugs across:
- `src/agents/catering/scripts/create-catering-lead`
- `src/agents/catering/scripts/apply-catering-owner-decision`
- `src/agents/catering/scripts/parse-menu-photo`
- `src/agents/catering/scripts/apply-menu-update`
- `src/agents/catering/scripts/lookup-prior-leads-by-phone`
- `src/platform/schemas.py` (catering classes + audit entries)
- `tests/test_catering_v02_scripts.py` — port to working `SourceFileLoader` pattern (resurrect 30 tests)
- `tests/test_lookup_prior_leads.py` — same fix (resurrect 33 tests)
- `tests/test_catering_schemas.py` — extend to cover untested classes

**Out of scope (deferred — file as follow-up tickets):**

- L1 hardcoded `/opt/shift-agent` paths (cross-cutting, needs platform-wide env-var refactor)
- L2 `BRIDGE_URL` not configurable (same — platform-wide)
- L4 stripping `--message-id` (do; trivial)
- L5 401/403 distinct exit code (defer to next OpenRouter integration pass)
- M2 `parse-menu-photo` markdown-fence regex (defer; current works for current models)
- M5 `apply-menu-update` notes carry-over comment-only (defer)

13 LOW items deferred. **50 items in this PR.**

---

## 2. Fix matrix — by file + finding

### 2.1 `src/platform/schemas.py`

| ID | Finding | Fix |
|---|---|---|
| **S1** (CRITICAL) | `CateringLead` no validator: `status=SENT_TO_CUSTOMER` + `quote_text=""` constructible | Add `model_validator(mode="after")` on `CateringLead` requiring non-empty `quote_text` for statuses in `{AWAITING_OWNER_APPROVAL, OWNER_APPROVED, OWNER_EDITED, SENT_TO_CUSTOMER}` |
| **S2** (CRITICAL) | No transition enforcement on `CateringLeadStatus` | Add `CATERING_TRANSITIONS: dict[Status, set[Status]]` table + `is_catering_transition_allowed(from_s, to_s) -> bool` helper. Used by scripts at every status change. NOT enforced by schema (would break replay) — enforced at write-side in scripts. |
| **S3** (HIGH) | `_BaseEntry.ts` accepts naive datetime | Add `field_validator` rejecting naive datetimes |
| **S4** (HIGH) | `OWNER_EDITED` has no audit class; `edit_text` allows empty when `decision="edit"` | Add `model_validator` on `CateringOwnerDecision` requiring non-empty `edit_text` if `decision="edit"`. Add `CateringOwnerEdited` audit class with `edit_text: str = Field(min_length=1, max_length=2000)` |
| **S5** (HIGH) | `price_usd: float` rounding risk | Add comment + helper `format_price_usd(p: float) -> str`. Defer Decimal migration to follow-up (out-of-scope per cost-benefit). |
| **S6** (HIGH) | `ProposalCode` regex vs `confirmation_code` regex divergence | Unify both to `^#[A-HJKMNPQR-Z2-9]{5}$` (excludes I, O, 0, 1, L). Add module constant `_AVOID_CONFUSING_CHARS_REGEX` reused by both. |
| **M1-S** | `headcount` lower bound coupling to `deposit_threshold_guests` | Add comment in `CateringLeadExtractedFields` documenting the relationship. No code change (business rule belongs in script.) |
| **M2-S** | `CateringLeadRejected.reason` Literal vs `REASON_TO_ERR_PREFIX` runtime dict | Add a runtime check in `create-catering-lead` startup: `assert set(REASON_TO_ERR_PREFIX) ⊆ set(get_args(CateringLeadRejected.model_fields["reason"].annotation))`. Loud failure if drift. |
| **M3-S** | `Menu.updated_by` unconstrained string | Add `field_validator` accepting `Literal["photo-ocr", "manual"]` OR an E.164-shaped string |
| **M4-S** | `CateringLeadStore` no `schema_version` | Add `schema_version: int = Field(default=1, ge=1)` on `CateringLeadStore` |
| **L1-S** | `event_date: str` regex passes calendar-invalid | Add `field_validator` calling `datetime.fromisoformat(v).date()` to reject `2026-13-99` etc. |
| **L2-S** | `off_menu_items` write-only contract is comment | Keep; enforce via integration test in `test_catering_b1_cases.py` (already done in C18 row). No code change. |
| **L3-S** | Audit classes & status invariants untested | Extend `test_catering_schemas.py`: add tests for every audit class + invariant (S1, S2, S3, S4, M3, L1) |
| **L4-S** | `budget_hint_usd: int` vs `price_usd: float` | Document only; do not change types (would cascade). Add comment. |

### 2.2 `src/agents/catering/scripts/create-catering-lead`

| ID | Finding | Fix |
|---|---|---|
| **C1** (CRITICAL) | `customer_phone` ValidationError uncaught | Wrap `CateringLead(...)` constructor in try/except `ValidationError` → `EXIT_INVALID_INPUT` with structured error JSON |
| **C2** (CRITICAL) | Idempotency on `original_message_id` only | In dedup branch (line 374–377), also compare `existing.customer_phone == E164Phone.from_any(args.customer_phone)`. On mismatch, log new `CateringLeadRejected(reason="message_id_phone_mismatch")` and return EXIT_INVALID_INPUT. Add reason to `CateringLeadRejected.reason` Literal. |
| **C3** (HIGH) | `_bridge_post` no retry | Add one-retry-with-backoff (mirror `send-coverage-message:96` pattern). 0.5s backoff, single retry. |
| **C4** (HIGH) | Bridge timeout no follow-up audit | Emit new `CateringOwnerApprovalCardFailed` audit entry when bridge POST fails. Add to schemas. |
| **C5** (HIGH) | `self_chat_jid_empty` emits misleading `CateringOwnerApprovalRequested` | Move `CateringOwnerApprovalRequested` log to AFTER successful card_sent. Emit `CateringOwnerApprovalCardSkipped` (new) when JID empty. |
| **C6** (HIGH) | Off-menu running-budget over-counts separators | Fix loop arithmetic: `running += len(item) + (2 if i > 0 else 0)` |
| **C7** (HIGH) | Off-menu single-item budget clamp `[:WHATSAPP_OFF_MENU_BUDGET - 10]` | Add assertion `WHATSAPP_OFF_MENU_BUDGET >= 50` so the slice arithmetic is robust. |
| **M1-CL** | Generic `except Exception` in config load | Split into narrow excepts: `(FileNotFoundError, PermissionError, OSError) → EXIT_DEPENDENCY_DOWN`; `(yaml.YAMLError, ValidationError) → EXIT_SCHEMA_VIOLATION` |
| **M2-CL** | `model_validate(model_dump())` round-trip wasteful | Remove. Pydantic's eager validation at construction is sufficient. |
| **M3-CL** | `_generate_unique_code` collision RuntimeError uncaught | try/except RuntimeError at call site → JSON error + EXIT_INTERNAL |
| **M4-CL** | `_next_lead_id` 5-digit overflow at 10000 | Add comment + assertion `last < 99999`; emit `InvariantViolation` if exceeded. (Alternative: extend format to L%05d.) |
| **M6-CL** | Lock-ordering invariant undocumented | Add comment block in `safe_io.py` documenting `LEADS_LOCK → LOG_LOCK` invariant |
| **M7-CL** | `extra="ignore"` on extracted fields silently drops | Switch to `extra="allow"` and log unknown keys at WARN level (allows new SKILL fields without breaking; logs drift) |
| **L4-CL** | `--message-id`, `--customer-phone` not stripped | `args.message_id = args.message_id.strip()` and same for phone |

### 2.3 `src/agents/catering/scripts/apply-catering-owner-decision`

| ID | Finding | Fix |
|---|---|---|
| **A1** (CRITICAL) | Approve-path crash between two locks → orphan in OWNER_APPROVED | Add `CateringQuoteAttempted` audit row INSIDE first lock (mirror `OutboundAttempted` from line 822). Detect on retry → resume by completing second-lock work. |
| **A2** (CRITICAL) | No idempotency on bridge POST → duplicate quote on retry | (Same fix as A1 — `CateringQuoteAttempted` is the idempotency anchor.) Add check at script start: if attempted exists for this lead+code, refuse retry (return EXIT_OK with `idempotent_replay: true`). |
| **A3** (CRITICAL) | `_load_menu_filtered` swallows ALL exceptions | Replace bare `except Exception` with narrow `except (FileNotFoundError, RuntimeError, ValidationError, OSError)` returning `(items, total, error_kind)`; emit `InvariantViolation(check="catering_menu_load_failed", detail=...)`; refuse approve if menu load failed (don't send a no-menu quote). |
| **A4** (CRITICAL) | Template format `KeyError` falls through silently | Replace `except (KeyError, OSError): pass` with explicit log + `InvariantViolation(check="catering_template_format")`. Inline fallback acceptable but must surface loudly. |
| **A5** (HIGH) | `off_menu_items` not in customer quote | Add `off_menu_items` to `_render_quote` substitution dict; surface as "**Custom requests we'll discuss:** ..." section. Update template substitution. |
| **A6** (HIGH) | Reject `--reason` doesn't message customer | If `args.decision == "reject"` and `args.reason` non-empty, send a polite decline message via bridge POST (new template `catering_decline_to_customer.txt`). |
| **A7** (HIGH) | State-write + audit-log not atomic | Wrap in try/except: on log-write failure, attempt rollback `atomic_write_json(LEADS_PATH, prior_store)` then exit non-zero with explicit error. |
| **A8** (HIGH) | Dietary filter silently drops unknown tags | Add `else: unknown_tags.append(t)` branch; emit `InvariantViolation(check="catering_unknown_dietary_tag", detail=tags)` |
| **A9** (HIGH) | Code-collision: stderr only, no audit | Emit `InvariantViolation(check="catering_code_collision", detail={"code": code, "lead_ids": [...]})` |
| **M1-A** | Code-format check `len(code) != 6` weak | Validate against schema regex `^#[A-HJKMNPQR-Z2-9]{5}$` (M1-A also covers PM3 in apply-menu-update). |
| **M2-A** | `--edit-text` truncated to 1000 chars silently | Warn on truncation: `if len(args.edit_text) > 1000: stderr.write("warning: edit_text truncated to 1000 chars")`. Increase cap to 2000 to match new schema bound. |
| **M3-A** | `customer_now` called twice for same logical instant | Compute once at lock entry, reuse. |
| **M4-A** | `reason` field semantic mismatch (decision name passed as reason) | Set `reason=""` for `decision in {"approve", "reject"}`; only populate for edit. |
| **L1-A** | `lstrip('+')` strips multiple plus signs | Use `[1:]` instead. (E164Phone validates exactly one `+`.) |
| **L3-A** | No `--resume-send` mode after C1-style crash | Implement: if lead in OWNER_APPROVED, the (now idempotent) approve flow auto-resumes. Effectively closed by A1+A2 fix. |

### 2.4 `src/agents/catering/scripts/parse-menu-photo`

| ID | Finding | Fix |
|---|---|---|
| **PM1** (HIGH) | Pending update silently overwritten | Check `PENDING_PATH.exists()` under lock. If present and within `pending_proposal_ttl_hours` → emit `EXIT_ILLEGAL_TRANSITION` with diagnostic (existing code + age). If TTL expired → emit synthetic `MenuUpdateRejected(reason="ttl_expired")` for prior, then proceed. |
| **M2-PM** | ValidationError truncated to first 5 | Include total error count + dropped count in `MenuUpdateProposed` audit (extend schema with `extraction_dropped_count: int = Field(default=0, ge=0)`) |
| **M3-PM** | Counter race | Wrap `_next_update_id` read-modify-write in `FileLock(counter.parent / "counter.lock")` |
| **M4-PM** | `image_path.read_bytes()` OSError uncaught | Add `OSError` to except clause |
| **L3-PM** | Empty-items menu accepted | If `len(items) == 0`, refuse and return EXIT_OK with stdout `{"status": "no_items", "preview": "no items extracted; please re-send a clearer photo"}`. Skip pending write + audit. |

### 2.5 `src/agents/catering/scripts/apply-menu-update`

| ID | Finding | Fix |
|---|---|---|
| **M1** (CRITICAL) | Bare except → silent menu overwrite + lost archive | Narrow to `except RuntimeError` for corrupt-after-quarantine (already renamed by safe_io). For other exceptions → `EXIT_SCHEMA_VIOLATION`, do NOT overwrite menu. Always `mkdir -p` archive dir + `shutil.copy2` raw bytes BEFORE attempting validation. |
| **PM3** (HIGH) | Code-format check duplicates schema regex | Validate via `re.fullmatch(r"^#[A-HJKMNPQR-Z2-9]{5}$", code)`. (Same fix as M1-A.) |
| **M1-AM** | Pending unlink race | Already inside outer PENDING_LOCK — verified safe; add comment documenting the invariant. |

### 2.6 `src/agents/catering/scripts/lookup-prior-leads-by-phone`

| ID | Finding | Fix |
|---|---|---|
| **PM2 / H2** (HIGH) | Phone canonicalization: 10-digit US-local → +9045551234 silently | In `E164Phone.from_any` (schemas.py:48-59): if input is bare 10 digits, raise `ValueError("phone must include country code; got 10 digits without +")`. Add migration: scripts that called `from_any` with potential 10-digit input must now handle the explicit reject. |
| **M6-LL** | `_load_config_now` bare-Exception fallback | Add `tz_warn: bool` to result dict so SKILLs can route accordingly |
| **L4-LL** | `_normalize_aware` warn stderr-only | Add `tz_warn_count: int` to result dict |

### 2.7 Test resurrection (T1)

| ID | Finding | Fix |
|---|---|---|
| **T1** (CRITICAL) | 63 catering tests broken (`spec_from_file_location → None`) | Port `_b1_helpers.py` SourceFileLoader pattern. Two approaches: (a) refactor each test file to use `_b1_helpers.run_create/run_apply/lookup_prior_leads_by_phone_helper`; (b) keep their own helpers but apply the SourceFileLoader fix. Choose **(a)** — eliminates duplication. |
| **T1-extend** | Coverage gaps (S1, S2 invariants, untested audit classes) | Add ~30 new tests to `test_catering_schemas.py` covering: every audit class, status-machine transitions, S1 quote-required validator, S6 regex unification, etc. |

### 2.8 Templates

| File | Change |
|---|---|
| `catering_quote_to_customer.txt` | Add `{off_menu_items_section}` substitution slot |
| `catering_decline_to_customer.txt` | NEW — for A6 reject-with-reason path |

---

## 3. Total scope estimate

- **Production code changes**: ~14 files modified, ~1200 LOC (script changes are dense; schema additions are concentrated)
- **Test changes**: ~3 files modified + ~30 new tests added, ~1500 LOC
- **Templates**: 1 new + 1 modified
- **Audit-class additions**: 3 new classes (`CateringOwnerEdited`, `CateringOwnerApprovalCardFailed`, `CateringOwnerApprovalCardSkipped`, `CateringQuoteAttempted`)
- **Schema additions**: 1 new field on `CateringLeadStore` (`schema_version`), 1 new field on `MenuUpdateProposed` (`extraction_dropped_count`), 1 new validator on `CateringLead`, 2 unified regex constants
- **Total: ~2700 LOC across 18+ files**

This is a LARGE single PR. Justified by user's "fix everything" + autonomous-mode directive.

---

## 4. Build sequence (single branch, ~6 commits for atomicity)

| Commit | Scope | LOC est | Tests est |
|---|---|---|---|
| 1 | **Schema layer**: S1+S2+S3+S4+S6+M1-S+M2-S+M3-S+M4-S+L1-S; new audit classes; helper module `catering_status_machine.py` | ~300 | ~150 |
| 2 | **`create-catering-lead`**: C1+C2+C3+C4+C5+C6+C7+M1-CL through M7-CL+L4-CL | ~250 | ~200 |
| 3 | **`apply-catering-owner-decision`**: A1–A9 + M1-A through M4-A + L1-A + L3-A | ~350 | ~250 |
| 4 | **Menu scripts**: PM1+M1+M2-PM+M3-PM+M4-PM+PM3+L3-PM+M1-AM | ~200 | ~150 |
| 5 | **`lookup` + phone canonicalization**: PM2+M6-LL+L4-LL | ~80 | ~80 |
| 6 | **T1 test resurrection**: rewrite `test_catering_v02_scripts.py` + `test_lookup_prior_leads.py` with SourceFileLoader pattern; extend `test_catering_schemas.py` | ~50 LOC code change, ~700 LOC test additions | (the tests are the fix) |

Each commit independently verifiable: pytest must pass after each.

---

## 5. Risk + mitigation

| Risk | Mitigation |
|---|---|
| **Schema migration**: existing on-disk leads.json + audit log entries may fail new validators (S1 quote_text required, S3 tz-aware ts) | (a) Deploy with `model_config = ConfigDict(extra="allow")` initially. (b) Migration script `tools/catering-state-migrate.py` to backfill. (c) For S3 specifically: don't enforce on read of historic entries — only on write; make ts validator a `field_validator(mode="before")` that auto-converts naive→UTC if naive. |
| **`E164Phone.from_any` rejection of 10-digit US**: any legacy callsite that passes 10-digit input now raises ValueError | grep all callsites; verify all wrap in try/except OR provide country code. Update `lookup-prior-leads-by-phone:104-117` to handle the explicit reject as `lookup_status="invalid_phone"`. |
| **Resurrected v02 tests may surface 5-30 NEW bugs** when they actually run | Spec for build phase: run resurrected tests immediately; if they reveal new bugs, fix in-line in this PR (extend scope). Better to catch now than ship dormant decoration. |
| **9 active leads on VPS** during deploy | Deploy in low-traffic window. Tier-1 schema changes are additive (new validators reject NEW writes; existing reads use `mode="before"` auto-migration). hermes-gateway restart is ~2s. |
| **Test infra change blast radius**: rewriting v02 + lookup test files | Each test must pass on VPS Linux validation before commit. Use the proven `/tmp/catering_e2e/` sandbox pattern. |
| **PR review fatigue**: 2700 LOC is a lot for 5 reviewers | Each commit is independently reviewable. Reviewers tagged with the relevant commit per their lens. |

---

## 6. Operational gates

| Gate | Effect | Check |
|---|---|---|
| Hermes pin | None (extends-Hermes) | Drift-check tag confirms |
| Symlink integrity | None | No script path moves |
| Tarball | tests/ excluded — but production scripts changed | Standard build-deploy-tarball.sh pattern |
| Smoke test | Existing smoke test covers create-catering-lead, apply-catering-owner-decision, parse-menu-photo, apply-menu-update, lookup paths | Must pass post-deploy |
| Live customer | 9 leads in flight: 5 AWAITING (will be subject to NEW status-machine validators on next status change), 4 SENT_TO_CUSTOMER (terminal — unaffected) | Migration shim needed for AWAITING leads — they have non-empty quote_text already; verify in pre-deploy audit |
| 20-min soak | Required given schema + script changes | Watch decisions.log for invariant_violation entries |

---

## 7. What this plan is NOT

- Not a refactor (e.g., not splitting catering scripts into a python package — out of scope)
- Not adding new features (no new flows, just hardening existing ones)
- Not changing SKILL prompts (Hermes-side; out of scope)
- Not adding cockpit views (deferred until 2nd customer)
- Not Decimal money migration (deferred; cost > benefit at v0.2)
- Not env-var configuration of paths (cross-cutting; defer to platform pass)

---

## Pipeline status

- 🟡 Plan written (this doc) — awaits 5-parallel-agent plan review
- ⏳ Design (after plan-review synthesis)
- ⏳ 5-parallel-agent design review
- ⏳ Build (6 commits)
- ⏳ PR + 5 code reviews
- ⏳ Apply review fixes
- ⏳ Pre-merge VPS validation (run resurrected v02 + lookup tests on staging-new sandbox)
- ⏳ Merge + deploy + 20-min soak
