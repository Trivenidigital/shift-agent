# Post-merge retrospective — Agent #32 v0.1 (option A pivot)

## Task / PR

- Task #: #32-#39 (this session's task graph)
- PR #: [#79](https://github.com/Trivenidigital/shift-agent/pull/79)
- Agent / feature: Agent #32 Special Request Memory v0.1 — extend `lookup-prior-leads-by-phone` with `most_recent_notes` (option A pivot)
- Plan version landed: v2 (after option A pivot from rejected v1)
- Build commits: 2 (initial + review fixups)
- Reviewer cycles: 2 plan + 2 design + 3 PR
- Total elapsed: ~2.5 hours of agent execution time
- Final landed commit: `3196295` on main
- Deploy: `deploy-20260509-201922-3196295f`

---

## Q1 — Hermes-first: which steps moved from `[net-new]` in plan v1 to `[Hermes]` in final code?

**Massive — entire plan v1 was rejected at plan-review time and pivoted to a much smaller scope.**

Plan v1 tagged 4/12 [net-new]: parallel store schema + new lookup script + audit variant + SKILL prose patch. Total ~480 LOC.

Plan v2 (after option A pivot) tagged 3/11 [net-new]: 1-LOC field addition to `_empty_result`, 5-LOC `ok`-branch derivation, 10-LOC SKILL prose patch. Total ~50-140 LOC.

**Net effect of the pivot:**
- `SpecialRequestMemoryStore` schema: [net-new] in v1 → **eliminated** (no new store; reuses `CateringLeadStore`)
- `lookup-special-request` script: [net-new] in v1 → **eliminated** (extends `lookup-prior-leads-by-phone`)
- `SpecialRequestLookup` audit variant: [net-new] in v1 → **eliminated** (matches deployed no-audit convention)
- `SpecialRequestMemoryConfig`: [net-new] in v1 → **eliminated** (no new feature flag needed)

**Caught at:** plan-review (R1's BLOCKER 1 + R2's 3 BLOCKERs all collapsed under R1's pivot proposal)

**Cost of late catch:** zero LOC churn (caught BEFORE design phase). Cost of catching: ~10 minutes of plan-review agent execution. Cost of NOT catching would have been ~430 LOC of dead infrastructure + the 5 schema BLOCKERs reviewers found (updated_at default, phone uniqueness, name collision).

This is the strongest "plan-time substrate inventory pays" data point in the session — see #41 retro lesson #1.

---

## Q2 — Drift-rule: which deployed-pattern files did I claim to read but didn't?

**0 unread CLAIMS in the v2 plan** (read-receipt-check hook caught one in v1 plan and resolved before write).

Specifically:
- v1 plan-write attempt 1: blocked by P-B because I cited `tests/test_lookup_prior_leads.py` but had only `ls`'d it. Resolved by Reading the relevant section and confirming the importlib + `_seed_leads` pattern.
- v2 plan + design: all citations were backed by actual session Reads. No reviewer surfaced unread material as a gotcha.

The hook prevented me from making the same mistake at v2-plan-write time.

---

## Q3 — Skill / helper landscape: what would have saved LOC if checked first?

**The pivot itself was the leverage discovery.**

What would have saved LOC if checked first AT THE PLAN PHASE (not retroactively):

| Capability | What v1 built instead | Counterfactual LOC |
|---|---|---|
| `lookup-prior-leads-by-phone` already exists as the per-customer-history substrate | v1 proposed a parallel `lookup-special-request` script | ~250 LOC saved by extending the existing |
| `CateringLead.extracted.notes` already captures free-form prior context | v1 proposed `CustomerPreference` schema + new state file | ~40 LOC saved on schema |
| `lookup-prior-leads-by-phone` emits no audit (precedent) | v1 proposed `SpecialRequestLookup` variant | ~25 LOC saved on audit-variant + LogEntry union edit |

Net: ~315 LOC of [net-new] infrastructure was avoided by Reading the `lookup-prior-leads-by-phone` script + `parse_catering_inquiry` Step 0 prose at PLAN time and asking "could we extend the existing?" — which the plan author (me) failed to do at v1, and the plan reviewer (R1) caught.

**Was `tasks/skills-roadmap.md` consulted at planning time?** Yes for ecosystem skills — N/A finding (no upstream skill covers this).

**Was the relevant SKILL.md actually read?** Yes for `parse_catering_inquiry/SKILL.md`, but only at the "what's the integration point" level — NOT at the "could the existing lookup field set absorb this" level. That gap is the lesson.

---

## Q4 — Reviewer-lens mandate (P-D self-check)

- **Plan review:** **YES** — both reviewers carried the Hermes-first scope-questioning lens. R1's prompt explicitly asked "could `lookup-prior-leads-by-phone` itself be extended to surface a `most_recent_notes` field, instead of a parallel store?" — which became the BLOCKER that drove the entire pivot. R2 carried it too via the schema-design lens, but R1's reframing was the load-bearing finding.
- **Design review:** **YES** — both reviewers carried the lens (Hermes-substrate-vs-net-new at code-level granularity).
- **PR review:** **YES** — R1 (Hermes-first) explicitly asked "is the SCOPE right? Could this be even smaller?" — verdict was "scope holds."
- **Findings credited to the lens specifically:**
  - **Plan-time R1's pivot proposal** — eliminated ~315 LOC of dead infrastructure. By far the highest-ROI finding in the session.
  - **PR-time R1's call** confirmed scope held end-to-end (`MOST_RECENT_NOTES_MAX_CHARS` extracted to constant for v0.2 grep-discoverability).

The lens is paying for itself catastrophically. **This retro escalates plan-time substrate-inventory to a recurring pattern** — see "Lessons" below.

---

## Q5 — Hook firing (P-A self-check)

- **hermes-first-check.py:** did the hook block any write attempt? **NO new blocks.** v1 plan and v2 plan both passed structural checks.
- **read-receipt-check.py (P-B):** did it block any write? **YES, 1 block.** v1 plan-write blocked because of `tests/test_lookup_prior_leads.py` claim that wasn't backed by an actual Read. Resolved by reading the file. Same pattern as #41 (where it caught a `tests/test_catering_v02_scripts.py` claim).
- **/hermes-check receipt (P-C):** **YES, 3 receipts** in this task graph: v1 plan (`agent-32-special-request-memory-v0-1.json`), v2 plan (`agent-32-extend-lookup-prior-leads-with-notes.json`), v2 design (`...with-notes-design.json`). All within 30-min freshness windows when used.
- **False positives:** None.
- **False negatives:** None surfaced by reviewers.

Receipt management note: when scope pivoted (v1 → v2), I needed a NEW receipt with the new topic name (because the hook derives topic from the plan filename and v2's filename is different). This is correct behavior — the receipt records WHAT was scoped at /hermes-check time, not the task's lifetime intent.

---

## Lessons → next-task action items

**Recurring pattern across #41 + #32 (≥2 retros — escalation candidate):**

1. **Plan-time substrate inventory pays catastrophically.** #41 retro flagged this; #32 had a much bigger payoff (~315 LOC eliminated). The specific question that pays: "could the existing X already do this if I added one field / one path / one prose patch?" — applied to the closest-similar deployed substrate.

   **Action: ESCALATE to a recurring plan-template question** in `tasks/agent-N-plan.md` template (if there is one) or in CLAUDE.md plan-first section. Specifically: "Read the closest-similar deployed substrate. Could it absorb this work with a one-field / one-call / one-prose-line addition? If yes, that IS the v0.1 scope — don't build parallel."

**Local lessons (apply to next agent build):**

2. **Pivot artifacts work best when both versions are preserved.** The original plan-doc was moved to `tasks/audits/agent-32-original-plan-rejected.md` — captures the rejected scope + reviewer findings + reasoning for the pivot. Future similar pivots should follow the same convention.

3. **Incidental `_load_script` SourceFileLoader fix unblocked 27 pre-existing tests.** Same root cause as #41's `sys.modules` pre-load fix — the hyphen-named-script + `spec_from_file_location` interaction. **Action: factor up to a shared helper** if a third occurrence appears.

4. **The `_load_script` fix had been broken on srilu the whole time.** 33-of-34 tests passing locally on Windows (where they're skipped via `pytest.mark.skipif`) but failing on srilu. This is a CI gap — the tests were "green" in someone's view but never actually run on srilu before this session. **Action: backlog item** — survey other catering-test files for the same pattern; many likely have the same bug.

**No new generalizable platform changes** beyond the recurring-pattern escalation candidate. No new CLAUDE.md rules to add yet (waiting for the third data point on plan-time substrate inventory).
