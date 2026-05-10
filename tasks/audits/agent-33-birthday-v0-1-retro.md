# Post-merge retrospective — Agent #33 v0.1 (option C pivot)

## Task / PR

- Task #: #33 + #34-#39 (this session's task graph)
- PR #: [#80](https://github.com/Trivenidigital/shift-agent/pull/80)
- Agent / feature: Agent #33 Loyalty & Punch-Card v0.1 — birthday reminders in Daily Brief + `record-customer-birthday` CLI script
- Plan version landed: v2 (option C pivot from v1 read-only plan)
- Build commits: 2 (initial + review fixups)
- Reviewer cycles: 2 plan + 2 design + 3 PR
- Total elapsed: ~2 hours of agent execution time
- Final landed commit: `a7db10b` on main
- Deploy: `deploy-20260510-150603-a7db10bd`

---

## Q1 — Hermes-first: which steps moved from `[net-new]` in plan v1 to `[Hermes]` in final code?

**Major scope pivot at plan-review (option A→C pattern, second occurrence in this session).**

**Plan v1** proposed a read-only birthday section in Daily Brief — 4/12 [net-new] tagged at ~165 LOC.

**Plan-review BLOCKERs**:
- R1: read-only without an input path is dead-code-on-arrival; either defer or add minimal write path
- R2 (3 BLOCKERs): `schema_version: int = 1` should be `Literal[1]`; MM-DD regex allows `02-30`; hand-edit race

**User chose option C** (add minimal CLI write path + fix R2 BLOCKERs). Plan v2 grew scope to 5/12 [net-new] / ~330 LOC.

**Net effect (different from #32's option A):**
- v1 plan was UNDER-scoped (would have shipped dead code)
- Option C added the minimal write path + fixed all 4 R2 BLOCKERs
- Plan v2's increased [net-new] count was honest scope addition, not over-engineering — the only alternative was full deferral

**Caught at:** plan-review (R1's BLOCKER + R2's 3 BLOCKERs all caught before any code was written)

**Cost of late catch:** zero LOC churn (caught BEFORE design phase). Cost of catching: ~15 minutes of plan-review agent execution + user-decision pause for the option-C choice.

---

## Q2 — Drift-rule: which deployed-pattern files did I claim to read but didn't?

**1 caught by P-B hook + resolved before write** at design time:
- Initial design doc cited `src/platform/safe_io.py:242` based on `Bash`-grep, not actual `Read`. Hook blocked the write. Resolved by Reading lines 240-254 of safe_io.py to confirm `atomic_write_json` signature accepts pydantic models.

No reviewer surfaced unread material as a gotcha.

---

## Q3 — Skill / helper landscape: what would have saved LOC if checked first?

**Plan-time substrate inventory: properly applied — no further leverage available.**

This is the THIRD retro to flag plan-time substrate inventory as the load-bearing question. Specifically:
- `_aggregate_birthdays` mirrored `_aggregate_yesterday`'s degraded-mode-on-failure pattern → no parallel infrastructure
- `_render_birthdays` mirrored `_render_alerts`/`_render_yesterday`/`_render_today` ordering → consistent
- `record-customer-birthday` mirrored `create-catering-lead`'s lock+atomic_write+audit pattern exactly → not duplicating, mirroring
- BriefSection extension reused the existing Literal extension point
- `LoyaltyConfig` mirrored `EquipmentMaintenanceConfig`/`PnlAnomalyConfig` Tier-2 scaffold pattern
- Schema additions used `Literal[1] = 1` pinning matching `Config.schema_version` deployed convention
- Audit variant `CustomerBirthdayRecorded` mirrored `BriefSent` `_BaseEntry` pattern
- `_BaseEntry` discriminated-union slot via `Annotated[..., Tag(...)]` (lesson from #41 B1)

What WOULD have saved LOC if I'd applied this lens harder at v1 plan time: the v1 plan was honest about reusing all 8 of those patterns; the under-scoping was the BLOCKER, not over-engineering. Plan-time substrate inventory caught everything reusable; option-C scope expansion was about closing the dead-code gap, not parallel infrastructure.

**`tasks/skills-roadmap.md` consulted:** N/A — Hermes ecosystem provides agent skills, not per-customer birthday memory.

---

## Q4 — Reviewer-lens mandate (P-D self-check)

- **Plan review:** **YES** — both reviewers carried the Hermes-first scope-questioning lens. R1's "is read-only enough?" question was the load-bearing finding. R2's 3 BLOCKERs caught fixable schema flaws.
- **Design review:** **YES** — R1 caught the unconditional template render BLOCKER (R1-B1). R2 caught the importlib-pattern third-occurrence flag.
- **PR review:** **YES** — R1 was empty (scope holds). R2 caught canonical-form discipline + section spacing. R3 caught test boundary-coverage gap.

**Findings credited to the lens specifically:**
- Plan-time R1's "is read-only enough?" question prevented shipping dead code (the highest-ROI single finding in the session for #33)
- Design-time R1-B1's unconditional-render catch prevented every existing customer's brief from getting "Birthdays today: None today." noise
- PR-time R2's E164Phone + section-spacing catches were minor but fix code-style drift before merge

The lens continues to pay for itself.

---

## Q5 — Hook firing (P-A self-check)

- **hermes-first-check.py:** 0 blocks
- **read-receipt-check.py (P-B):** **YES, 1 block** — design doc cited `safe_io.py:242` based on `Bash` grep, not `Read`. Hook blocked the write. Resolved by Reading the cited section. Same false-claim pattern caught on #32 + #41 PR cycles.
- **/hermes-check receipt (P-C):** **YES, 4 receipts** in this task graph: v1 plan + v2 plan + v2 design + (option-C-write-path) — all within 30-min freshness windows when used.
- **False positives:** None.
- **False negatives:** None surfaced by reviewers.

---

## Lessons → next-task action items

**Recurring pattern across #41 + #32 + #33 (≥3 retros — ESCALATION TIME):**

1. **Plan-time substrate inventory pays catastrophically.** Three retros now flag this. The specific question: *"could the existing closest-similar substrate absorb this with a one-field / one-call / one-prose-line addition?"* — applied to the plan's [net-new] items.

   - #41: ~40 LOC saved
   - #32: ~315 LOC saved (option-A pivot)
   - #33: prevented dead-code (option-C pivot — different valence: caught UNDER-scoping rather than over-scoping; both are scope-discipline failures the lens catches)

   **ESCALATION RECOMMENDATION**: add the substrate-inventory question as a permanent prompt-template line in CLAUDE.md plan-first section OR in `/hermes-check` Step 1's instructions. Specifically: "For each [net-new] step, name the closest-similar deployed substrate; if extending it covers v0.1, that IS the v0.1 scope. If no substrate covers it, that's the genuine [net-new]."

2. **Option-A and option-C pivots are the SAME class of finding.** Both are "v1 scope is wrong; reviewers force re-scope." Option A was over-scoping (build new parallel infrastructure) → trim. Option C was under-scoping (dead code without input path) → expand. The lens catches BOTH directions. Worth naming them explicitly in the recurring-pattern memory.

**Local lessons (apply to next agent build):**

3. **Third occurrence of the importlib + sys.modules pre-load pattern was hit and DEFERRED.** Per #32 retro lesson #3, factoring up should happen on the third occurrence. I deferred to a backlog item rather than expanding scope mid-PR. **Action: at next agent build that needs this pattern, factor up first** — the marginal cost of doing it on the FOURTH occurrence is even higher than doing it on the THIRD.

4. **Read-receipt hook caught a `Bash`-grep-vs-`Read` confusion AGAIN.** Same pattern as #32 + #41. The fix is mechanical (Read the cited file), but the recurring trigger suggests the design-author (me) defaults to `Bash` grep when "scanning for context" rather than `Read` when "committing to a citation." **Possible escalation:** add a self-check-template line to the design-doc skeleton: "for every file:line citation, did I `Read` it (vs grep / Bash) this session?"

**No new generalizable platform changes** beyond the recurring-pattern escalation candidates above. Two CLAUDE.md candidate additions queued behind one more occurrence.
