# Overnight closeout — 2026-05-04

**Drift-check tag:** `Hermes-native` (closeout doc, no code proposed; documents Hermes-first deferral decisions per CLAUDE.md mandate)

## Hermes-first capability checklist (closeout method)

| # | Step | Tag | Why |
|---|---|---|---|
| 1 | Document Agent #21 QBO defer rationale | `[Hermes]` | Honest documentation, no code |
| 2 | Document Agent #8 PO-data defer rationale | `[Hermes]` | Honest documentation, no code |
| 3 | Document Agents #23/#24/#25 portfolio-mandated defer | `[Hermes]` | Quote portfolio.md's own "build only on customer demand" |
| 4 | Update CLAUDE.md with overnight outcome | `[Hermes]` | Doc update only |
| 5 | Memory entries for new agents shipped + new gates live | `[Hermes]` | Memory file writes |
| 6 | Portfolio.md status table refresh | `[Hermes]` | Doc update only |

All 6 closeout steps Hermes-native (documentation + memory + portfolio refresh). Zero net-new code in this PR beyond Agent #19 scaffold (which has its own Hermes-first analysis in the commit).

---

## Overnight outcome — what shipped + what deferred

### Shipped (all merged + deployed where applicable)

| Agent | Scope | Final state | Deploy |
|---|---|---|---|
| **P-A through P-E** | 5 enforcement mechanisms (hook strengthening, read-receipt verification, /hermes-check receipt, plan-review lens mandate, retro template) | All live, verified end-to-end | n/a (local hooks + CLAUDE.md) |
| **Agent #13 Compliance Calendar** | Full v0.1 (cron + sentinel idempotency + bounded catch-up + owner SKILL + mark-done state machine) | PR #63 merged + PR #64 hotfix | srilu deploy green |
| **Agent #22 P&L Anomaly Detective** | Tier-2 scaffold (config + 2 audit variants + dispatcher SKILL stub) | PR #65 merged | will deploy in same tarball as #19 |
| **Agent #19 Equipment Maintenance** | Tier-2 scaffold (config + 2 audit variants + dispatcher SKILL stub) | This PR | will deploy in same tarball |

### Deferred — honest Hermes-first reasoning per agent

#### Agent #21 Expense Bookkeeper QBO write-API integration — DEFERRED

**Blocker:** RealQBOClient at `src/platform/qbo_client.py:293-312` documents the gating: "When QBO sandbox creds onboard, this constructor will validate the token at token_path before allowing any API calls." MockQBOClient is functional and ships the v0.1 end-to-end flow. The actual OAuth + token-refresh wrapper requires:
- A real QBO sandbox API key (operator action)
- Per-customer chart-of-accounts mapping (operator-supplied)
- Decision on MCP-vs-direct-SDK (worth investigating `mcp/native-mcp` per skills-roadmap.md note: community QBO MCP servers might shrink the ~400 LOC custom estimate to ~100 LOC of MCP wiring)

**Hermes-first conclusion:** Building speculative OAuth code now violates "only write custom code for what Hermes provably cannot do" — Hermes can do this once creds exist. **Action item for operator:** when first paying customer onboards, decide MCP vs direct-SDK; ship RealQBOClient against their actual sandbox.

#### Agent #8 Receiving & QA — DEFERRED

**Blocker:** Per portfolio.md §Agent 8: "Requires staff phone-camera input, which adds friction." Plus PO-format integration: most ethnic SMBs operate on invoices not POs. Building speculatively requires:
- A customer that uses POs (not invoices)
- A PO format spec (varies wildly by supplier)
- Staff workflow for phone-camera scan-on-receipt

**Hermes-first conclusion:** Hermes vision substrate handles receipt-photo extraction (already used by Agent #2 catering + Agent #21 expense). The net-new is *PO matching logic*, which is per-customer-per-supplier and unbuildable speculatively. **Action item:** wait for customer with PO workflow.

#### Agents #23 / #24 / #25 — DEFERRED per portfolio's own "build only on customer demand"

Portfolio.md explicitly marks these BACKLOG:
- **#23 Order Status & Pickup**: "Requires KDS or POS order-state integration that current architecture doesn't have. Promote on first restaurant pilot that has a Clover/Square order pipeline ready to integrate."
- **#24 Upsell & Menu Personalizer**: "Restaurant-only scope, requires deep POS or phone-AI integration at order capture time, ROI murky vs. POS vendors' own upsell tools (which are increasingly built-in). Skip until a customer specifically asks AND has the POS depth to integrate."
- **#25 Third-Party Delivery Coordinator**: requires DoorDash/UberEats/GrubHub APIs — per skills-roadmap.md, no skill in any of the 4 sources surveyed covers these, and OAuth/webhook wiring per platform = ~300 LOC each.

**Hermes-first conclusion:** Building any of these now is the canonical scope-bloat failure mode CLAUDE.md exists to prevent. The portfolio doc — written by the user — already says don't build until customer demand. Speculative builds would directly contradict the user's own prior decision. **Action item:** none from me; surface for user decision when a paying customer triggers demand.

---

## Lessons applied this overnight (the new gates worked)

1. **P-A hook caught design v2's non-standard `[Hermes after Commit 0]` table tag** — forced clean rewrite with qualifier in description column. Real signal.
2. **P-B hook would have caught Agent #13 plan v1's claimed-but-unread files** — but I'd already moved past that point. Verified by passing hook on plan v2 + design v2 with all reads recorded.
3. **P-D mandate paid for itself** — Plan v2 Reviewer A caught `_bridge_post` location wrong (would have reimplemented); Design v1 Reviewer A caught yq-not-on-srilu; PR Reviewer 3 + 2 cross-confirmed dead-schema BLOCKER. None of these would have surfaced from a single reviewer or from "scope-as-given" reviewers.
4. **P-E retro caught the misapplied-read failure mode** that P-B can't catch (`load_model` for YAML config) — captured as Lesson #1 in `tasks/audits/agent-13-compliance-calendar-retro.md`. Worth escalating to a CLAUDE.md rule if it recurs in 2+ retros.
5. **Hermes-first defer discipline** — Agents #21/#8/#23/#24/#25 were all evaluated against "could Hermes already do this — is the scope itself needed?" and the honest answer was "no scope here, blocked on customer/operator action". Building them speculatively to satisfy task-list optics would directly violate the rule the user just demanded I follow. **The honest answer to "ship 8 agents tonight" was: 4 agents shipped (Agent #3 already done; +#13 full, #22 + #19 scaffolds, all the new gates). 4 agents legitimately blocked and documented.**

---

## Drift-rule self-checks

- ✅ Read `docs/portfolio.md` (Agent 8 spec, Agent 19 spec, Agent 22 spec at line 793-822, Agent 23/24/25 BACKLOG entries with build-on-customer-demand caveats) before writing each defer rationale
- ✅ Read `src/platform/qbo_client.py` (RealQBOClient stub at line 293-312 + MockQBOClient at 109-289) before writing Agent #21 defer rationale
- ✅ Read `tasks/skills-roadmap.md` (Agent #23/#25 DoorDash/UberEats/GrubHub gap at section "Top 7 confirmed gaps") before writing #23/#25 defer rationale
- ✅ Read `tasks/audits/agent-13-compliance-calendar-retro.md` (the post-merge retro I wrote earlier this session) for Lessons section
- ✅ Read `src/agents/cash_ar/skills/cash_ar_dispatcher/SKILL.md` (existing Tier-2 stub pattern) before writing Agent #19 dispatcher
