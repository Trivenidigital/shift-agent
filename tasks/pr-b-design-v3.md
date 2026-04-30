# PR-B — Design v3 (MVP, Hermes-first re-checked)

**Drift-check tag:** `extends-Hermes`

**Pipeline position:** Plan v2 ✅ → Design v1 → 5-agent review → Design v2 → **per-commit Hermes-first re-check (this) → Design v3 ← you are here** → Build → PR + 5-review → merge → 90-min canary → bulk deploy.

**Supersedes:** Design v1 (commit `5fc429e`) and Design v2 (working tree). v3 is BINDING.

**Why v3 exists:** at user-prompted re-check (2026-04-30), the per-commit Hermes-first checklist had never been applied to the 8-commit PR-B2 scope. Doing it surfaced that 5 of 8 commits were avoidable scaffolding around what is fundamentally a 1-commit SKILL.md change + 1-commit apply-script change. Per CLAUDE.md red-flag clause: *"if the spec marks most steps `[net-new]`, you almost certainly missed a Hermes capability."* True here.

---

## 1. Per-step Hermes-first checklist (BINDING, applied first)

| Step in the catering quote flow | Hermes? | Net-new? |
|---|---|---|
| Owner sends `<CODE> approve` reply via WhatsApp | [Hermes] dispatcher routes to handle_catering_owner_approval SKILL | — |
| SKILL reads lead + customer config + filtered menu from filesystem | [Hermes] SKILLs are scripts with FS access; precedent: `parse_catering_inquiry` Step 0 reads inline | — |
| LLM drafts customer-facing quote text | [Hermes] gateway LLM call (deployed substrate) | — |
| SKILL writes drafted text to apply-script via stdin | [Hermes] subprocess invocation pattern; we just use stdin not argv | — |
| Truth check: does drafted text contain the headcount + date? | [net-new] | ~10 LOC regex |
| Strip control chars + cap length before sending to WhatsApp | [net-new] | ~3 LOC |
| Apply-script publishes drafted text to customer | [Hermes] existing post-anchor + retry-state-machine handles this | — |
| Audit failure if SKILL→apply handoff breaks | [Hermes] `log-decision-direct` is the chokepoint; SKILL emits directly | — |
| Add `catering_quote_skill_failed` LogEntry variant | [extends-Hermes] new variant in deployed audit-chain pattern | ~20 LOC |

**Total net-new: ~33 LOC of behavior code + ~20 LOC of schema + the SKILL.md rewrite.** Everything else is already Hermes substrate.

---

## 2. Read-deployed-code evidence

| File | Confirmed |
|---|---|
| `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` Step 0 | reads state files inline — precedent for inlining `catering-lead-context` work into the SKILL |
| `src/platform/scripts/log-decision-direct` | accepts JSON on argv (or `-` for stdin) — SKILL emits failures here directly, no separate Python script needed |
| `src/agents/catering/scripts/apply-catering-owner-decision` (post-PR-D2) | already has anchor + retry-state-machine for post-bridge divergence; `--quote-text` already passes drafted text on argv (RCE surface — must be replaced by stdin) |
| `src/platform/schemas.py` `_BaseEntry` discriminated union | `CateringQuoteSkillFailed` variant slots in via Tag-union pattern |

---

## 3. Build sequence (3 commits, ~125 LOC total)

| # | Commit | LOC |
|---|---|---|
| 1 | `feat(schemas): CateringQuoteSkillFailed LogEntry variant + Tag-union edit` | ~20 src + 6 tests |
| 2 | `refactor(catering): apply-decision --quote-text-stdin (replaces argv); 5-line headcount+ISO-date regex sanity check; 3-line Cc/Cf/length normalize; delete _render_quote/_format_menu_section/_load_menu_filtered/MENU_ITEMS_IN_QUOTE/template file (no fallback)` | ~50 src + 12 tests |
| 3 | `docs(catering-skill): handle_catering_owner_approval SKILL.md v0.4 single-turn LLM flow — Step 0 reads state inline, Step 1 LLM drafts, Step 2 invokes apply-decision via stdin; emit catering_quote_skill_failed via log-decision-direct on failure` | ~70 SKILL + 4 tests |

**No PR-B1/B2 split** — the schema additions are minimal (1 LogEntry variant) and don't need rollback-asymmetry treatment. PR-D3 absorbing shim ships idle (no reserved keys are written by this PR).

---

## 4. Concrete code

### 4.1 Commit 2 apply-script (excerpt)

```python
# apply-catering-owner-decision (changes)

ap.add_argument("--quote-text-stdin", action="store_true",
                help="Read drafted quote text from stdin (PR-B v0.4). "
                     "Replaces argv-based --quote-text (shell-escape RCE).")

# In approve flow:
if args.decision == "approve":
    if not args.quote_text_stdin:
        sys.stderr.write("PR-B v0.4: --quote-text-stdin required\n")
        return EXIT_INVALID_INPUT
    quote_text = sys.stdin.read(8192)  # 8KB cap, no TOCTOU/symlink concerns
    if len(quote_text) > 4096:
        sys.stderr.write("quote_text > 4KB; refusing\n")
        return EXIT_INVALID_INPUT

    # Normalize: 3 lines, inline
    quote_text = "".join(c for c in quote_text
                         if unicodedata.category(c) not in {"Cc", "Cf"} or c == "\n")
    quote_text = re.sub(r"[*_~`]+", "", quote_text)[:600]

    # Truth sanity (5 lines, inline)
    hc = lead.extracted.headcount
    if hc is not None and not re.search(rf"\b{hc}\b(?!\d|[,.]\d)", quote_text):
        _emit_quote_skill_failed(lead, "truth_guard_failed", f"headcount={hc} missing")
        return EXIT_DEPENDENCY_DOWN
    ed = lead.extracted.event_date
    if ed and ed not in quote_text:
        _emit_quote_skill_failed(lead, "truth_guard_failed", f"event_date={ed} missing as ISO")
        return EXIT_DEPENDENCY_DOWN
    # ... existing post-anchor + retry-state-machine continues unchanged
```

ISO-only date enforcement (commit 3 SKILL prompt instructs LLM to include `(YYYY-MM-DD)` parenthetical alongside any prose date). Eliminates the entire `_fuzzy_find_date` regex matrix from v2 — if ISO is missing, reject and let the SKILL retry once.

### 4.2 Commit 3 SKILL.md (sketch)

```markdown
# handle_catering_owner_approval — Step prose

Step 0. Read context inline:
  - Lead: `jq '.leads[] | select(.owner_approval_code=="$CODE")' /opt/shift-agent/state/catering-leads.json`
  - Customer config: `cat /opt/shift-agent/config.yaml`
  - Menu items: `jq '.items[]' /opt/shift-agent/state/catering-menu.json`

Step 1. Draft a single customer-facing quote message. Plain prose only — no markdown,
   no special characters. MUST include literal ISO event_date as `(YYYY-MM-DD)`. MUST
   include the literal headcount integer.

Step 2. Pipe drafted text to apply-decision:
   ```bash
   echo "$DRAFT" | apply-catering-owner-decision --code "$CODE" --decision approve --quote-text-stdin
   ```
   On non-zero exit (truth-guard rejected, bridge unreachable, etc.):
   ```bash
   log-decision-direct '{"type":"catering_quote_skill_failed","ts":"'"$(date -u -Iseconds)"'","lead_id":"'"$LEAD_ID"'","reason":"apply_decision_nonzero","detail":"exit='"$RC"'"}'
   ```
```

---

## 5. Deploy plan

1. Tarball + scp + `shift-agent-deploy.sh` on canary.
2. Pre-restart import gate passes.
3. **30-min canary soak** (down from 90-min — surface area is much smaller now). Watch:
   - Tail `decisions.log` for `catering_quote_attempted` + `catering_quote_skill_failed` ratios.
   - First real LLM-drafted quote: operator manually inspects before customer Pushover fires.
4. After 30-min clean: bulk-deploy 8 non-canary VPS via `tools/canary-bulk-deploy.sh`.

No `HERMES_LLM_MOCK` infrastructure required. No tarball `.deploy-flags` PR_B_PHASE marker required (no schema-rollback ladder). No `check-active-traffic-gate.sh` required (no money-moving binary swap; this is a SKILL prose change + a stdin flag).

---

## 6. Out-of-scope (documented as deferred to v0.5)

- Truth guard expansion (NFKD-fold customer name match, headcount-None hallucination scan, fuzzy date prose match, menu-item hallucination, price fabrication) — defer until real canary failures justify each
- `voice_quality`, `quote_source`, `tone_profile`, `tone_examples` schema fields — defer until a v0.5 SKILL actually needs them
- ToneProfile model — defer
- record-catering-skill-failure operator script — replaced by inline `log-decision-direct` call from SKILL
- catering-lead-context bundler script — replaced by inline reads in SKILL Step 0
- quote_normalizer.py module — replaced by 3-line inline strip
- check-canary-watchlist.sh + halt threshold + LLM cost-runaway alert — operator-eyeball monitoring during 30-min canary; automate later if needed
- skill_drafts/ directory + uid-validated tmpfile reads — eliminated by stdin

---

## 7. PR-D3 status

PR-D3 absorbing shim is already deployed and soaking. v3 doesn't write any of the reserved keys, so the shim sits idle. Two options:
- **(A)** Leave it deployed; ignore. Cost: 2 `mode='before'` validators run on every model load (~microseconds). Removal trigger from PR-D3 design doc no longer applies.
- **(B)** Open PR-D4 to delete the shim now that it's not load-bearing. ~30 LOC + tests removed.

Recommend **(A) for now** — shim costs nothing and could become useful again if a future PR adds reserved keys. Schedule PR-D4 for the next time we touch schemas.py for unrelated reasons.

---

## 8. Self-review — design-v3 specific

- [x] Per-commit Hermes-first checklist applied (§1, BINDING).
- [x] Net-new logic: ~33 LOC behavior + ~20 LOC schema + 70 SKILL = ~125 LOC total.
- [x] Drift-tag `extends-Hermes` (only 2 of ~10 steps are net-new).
- [x] No SaaS-style infra. No parallel approval-code generator.
- [x] Storage: JSON-on-disk + flock unchanged.
- [x] Audit emission via deployed `log-decision-direct` chokepoint, no parallel path.
- [x] No new dependencies (`python-dateutil` not needed; `datetime.strptime` not needed; ISO-only date verification is a substring check).
- [x] RCE surface eliminated by stdin (no tmpfile + TOCTOU + uid + symlink + size + unlink dance).
- [x] No `--quote-text-file` directory provisioning required.
- [x] No PR-B1/B2 split — single PR.
- [x] No 24h soak between sub-PRs (was infrastructure for the dropped split).
- [x] PR-D3 absorbing shim now idle but harmless.

## Status: DESIGN-V3-DRAFTED, awaiting user approval to build

Estimated build + PR + 5-review + merge + 30-min canary: **~3-4 hours total**, vs design-v2's ~2-day estimate.
