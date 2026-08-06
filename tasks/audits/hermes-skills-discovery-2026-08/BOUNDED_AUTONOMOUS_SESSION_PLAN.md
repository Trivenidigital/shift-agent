# Bounded Autonomous Session — Plan Artifact

**Drift-check tag:** `extends-Hermes` — all work layers custom adjudication, scoring and freeze
infrastructure above an unmodified Hermes plugin LLM lane. No Hermes core, skill, gateway or
production config is touched.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Structured JSON output from an LLM | yes — Hermes plugin LLM lane | use it unmodified (P3, P6) |
| Vision extraction from an image | yes — Hermes native vision | use it for P5 candidate findings only, never authoritative |
| Clause-level intent adjudication for an edit firewall | none found | build (P6 host layer) |
| Entity-role segmentation for inquiry intake | none found | build (P3 host layer) |
| Deterministic QR / layout / canvas verification | none found | build (P5 deterministic layer) |
| Freeze / provenance / preflight harness | none found | build (`hostlib/preflight.py`) |

Ecosystem check: no official-bundled or awesome-hermes-agent skill performs holdout freezing,
provenance preflight or adjudication of an edit contract. Verdict: model lanes stay Hermes-native;
the deterministic adjudication and evidence layers are necessarily net-new.

---

## 1. Corrected starting state (verified against preserved artifacts, not summaries)

The authorization names `P6 — P6_V6_FREEZE_2 AWAITING INDEPENDENT HOLDOUT`. That is stale by three
supersessions and one completed round. Verified state:

| Claimed | Verified | Evidence |
|---|---|---|
| P6 at freeze-2, holdout pending | P6 at **freeze-4**, holdout **executed and FAILED** | `holdout-v6/FREEZE_MANIFEST_V6.json` = `p6-v6-freeze-4` sha `8aa3581a2ce5d2e2…`; result `holdout-v6/evidence/holdout_v6_results.json` sha `1266abf9b69c753d…`, 43 cases, `PASS: false` |

Cause of the divergence: freeze-3 and freeze-4 were minted **on the execution host** (freeze-3 to
correct a dependency-tuple mismatch, freeze-4 to correct a runner `NameError`), and only the
execution host carried them. The local repo still held freeze-2. Corrected at session start: the
freeze-4 manifest is now installed locally and freeze-2 preserved as
`FREEZE_MANIFEST_V6_freeze2_superseded.json`. Local `hostlib/`, `tests/` and `holdout-v6/` were
otherwise byte-identical to the execution host.

**P6 supersession chain:** `freeze-1` → `freeze-2` (b8d25e3a) → `freeze-3` (dependency tuple re-minted
in execution environment) → `freeze-4` (8aa3581a, runner fix) → **round executed, FAILED** → `v7`
(this session).

Other workstreams accepted as stated pending artifact verification now in progress:
P1 `CLOSED_MODEL_RETIRED` · P3 remediation + fresh holdout required · P4 deterministic ready, real
operator review required · P5 QR/layout/canvas remediation + fresh visual holdout required.

## 2. P6 v6 round outcome — the defects v7 must correct

Full report: `structured-stage-a/stage-b/P6_FRESH_INDEPENDENT_HOLDOUT_RESULTS.md`.

- **D1 — no overt-subject test.** `_grammatical_role` infers PREDICATE by elimination and models no
  subject. Two clauses with explicit subjects were classified `IMPERATIVE`, producing **2 executable
  unsafe contracts**. Round fails outright.
- **D1b — target attachment crosses conjuncts.** Because the boundary rule requires the right side to
  reach an operation verb, observation conjuncts merge into the command clause, making the
  clause-bounded target search vacuous over the whole sentence.
- **D2 — clause-initial exemption defeated by a retained coordinator.** `"and export it portrait."`
  had `before.strip() == "and"`, so the imperative exemption never fired; a requested
  target-independent operation was silently dropped.
- **D3 — case-ID space collides across rounds.** All v6 IDs carry the `hv4-` prefix; 40 of 43 collide
  with v5 IDs but only 1 shares text. Cross-round comparison by ID is unsound. Next case set must be
  round-namespaced.

## 3. Autonomous loop applied per workstream

```
investigate → plan artifact → isolated correction → deterministic tests
→ adversarial review → resolve findings → freeze components + scorer
→ mandatory preflight → independent holdout authoring → seal and hash
→ execute → independent evaluation → ruling
```

Defect found by a holdout ⇒ preserve the failed round unchanged, do **not** patch the frozen system,
mint a new component version and manifest, replay historical cases as regression evidence, commission
a new author, repeat.

## 4. Role separation and how it is enforced

| Role | Agent | Separation mechanism |
|---|---|---|
| Implementation | `coder` | Sees dev tree, defect reports, fixtures. Forbidden from authoring or grading the fresh holdout. |
| Adversarial reviewer | `deep-reasoner` | Sees implementation + tests; must challenge before freeze. |
| Scorer auditor | `deep-reasoner` | Runs before scorer freeze; verifies enum coverage, positive+negative self-tests, null-contract semantics, separated score axes. |
| Independent holdout author | `Agent` with `isolation: "worktree"` | **Physically enforceable.** The entire `structured-stage-a` tree is untracked in git (`git ls-files` returns 0 files), so a fresh worktree contains none of the implementation source, fixtures, prior cases, scorers, briefs or results. The author receives only a sealed package directory. |
| Independent evaluator | `deep-reasoner` | Reviews results; may not modify frozen system or scorer. |
| Final ruling agent | `deep-reasoner` | Treats all prior conclusions as claims; rules on SAFETY / UTILITY / HOLDOUT VALIDITY / OPERATIONAL READINESS separately. |

**Residual limitation, stated honestly:** a worktree removes the evidence from the agent's working
directory but does not block an absolute path back into the main checkout. Separation is therefore
enforced by worktree + sealed package + explicit prohibition, and **verified after the fact** by
`VERIFY_AUTHORING_SEPARATION` (below). Where that verification cannot be satisfied the round is
reported `HOLDOUT_BLOCKED — INDEPENDENCE NOT ENFORCEABLE`.

## 5. Preflight — extended to nine checks

Existing eight, plus two new ones required by this authorization:

- `VERIFY_HOLDOUT_PACKAGE_SEALED` — the cases file and answer keys were hashed into a seal record
  **before** the runner was invoked; the seal is re-verified at run time and must match.
- `VERIFY_AUTHORING_SEPARATION` — an authoring receipt records the isolated directory and its file
  listing; the listing must contain no excluded artifact, and **no authored case may share request
  text with any case from any prior round** (a checkable property, not a promise).

`PreflightAbort` is never caught by a runner. A failure requires a new version and manifest; a frozen
round is never patched.

## 6. Cost governance

Ceiling: **$5.00 cumulative theoretical**. Spent to date on the P6 v6 round:
`MAXIMUM_THEORETICAL_COST` $0.1008, `MINIMUM_OBSERVED_COST` $0.00887925. Each holdout round is
budgeted and ledger-capped before execution; the ledger refuses calls past its cap.

## 7. Boundaries observed for the whole session

No deployment, no merge to a production branch, no production routing, no skill installation into
production Hermes, no WhatsApp or customer send, no automated proposal delivery, no automated flyer
edit, no publishing, no mutation of real pricing or contact data, no unapproved customer data.
Executable contracts are derived inside harnesses and never executed.

P4 remains deterministic; **no model port will be constructed or invoked**. Operator-judgment fields
stay null. Final status `P4_ENGINEERING_READY_AWAITING_OPERATOR_REVIEW`.

## 8. Hard stops that would suspend a workstream

Production mutation · real customer data or outbound send · a flyer needing actual modification ·
independence not enforceable · secret exposure · frozen evidence unpreservable · a deterministic
safety boundary unenforceable · model able to bypass deterministic validation · $5 ceiling ·
repository instructions prohibiting the change · a decision inherently requiring a real operator.
Every unaffected workstream continues.
