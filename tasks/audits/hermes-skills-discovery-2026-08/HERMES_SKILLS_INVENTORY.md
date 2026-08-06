# HERMES_SKILLS_INVENTORY

**Method:** `hermes skills list` executed with each host's **active interpreter** and that
process's **`HERMES_HOME`**. Read-only. `hermes skills audit` deliberately **not run** — it
re-scans and may write a scan cache; deferred as a mutation risk.
**Date:** 2026-08-02. **Raw evidence:** `skills_*.txt`, `rt_skills_parsed.json`,
`srilu_skills_correct.txt` in this directory.

## 0. Methodology correction that changed the numbers

An earlier pass queried srilu-vps without setting `HERMES_HOME`, so it read root's default home
rather than the runtime's (`/home/gecko-agent/.hermes`). That produced "84 builtin, 0 local,
0 official" — **wrong**. Corrected figures below. The same class of error (querying a path
instead of the active runtime) also produced the withdrawn 0.14.0 version claim.

## 1. Totals (corrected)

| Host | Total | builtin | local | hub/official | disabled | quarantined |
|---|---|---|---|---|---|---|
| srilu-vps | **98** | 66 | 22 | 10 | **0** | **0** |
| main-vps | **122** | 66 | 49 | 7 | **0** | **0** |
| vpin-vps | **96** | 66 | 23 | 7 | **0** | **0** |

**Common builtin core across all three: 66 skills.**

### 1.1 Finding S-1 — zero disabled, zero quarantined, fleet-wide

**316 skill entries across three hosts; every one reports `status=enabled`.** There is no
disable or quarantine in use anywhere on this fleet. This is the single most consequential
inventory finding: it means (a) the Gecko `QUARANTINED_REFERENCE_ONLY` ruling has **no
enforcement mechanism at the skill layer**, and (b) routing surface is maximal on every host
regardless of that host's purpose (Part 11).

## 2. Cross-host distribution

- **Common to all three:** 84 skills (66 builtin + the shared local/official set:
  `kanban-*`, `native-mcp`, `dspy`, `linear`, `spotify`, `heartmula`, `godmode`,
  `subagent-driven-development`, `writing-plans`, `debugging-hermes-tui-commands`,
  `webhook-subscriptions`, `jupyter-live-kernel`, `ideation`, `segment-anything-model`,
  `audiocraft-audio-generation`, `obliteratus`, `yuanbao`, `baoyu-*`, `pixel-art`,
  `pokemon-player`, `minecraft-modpack-server`).
- **main-vps only (32):** the SME agent skills — see `HERMES_AGENT_INVENTORY.md` §1.1.
- **vpin-vps only (6):** `vizora-customer-lifecycle`, `vizora-shadow-operations`, `axolotl`,
  `unsloth`, `outlines`, `fine-tuning-with-trl`.
- **srilu-vps only (8):** `coin_resolver`, `crypto_narrative_scanner`, `kol_watcher`,
  `narrative_alert_dispatcher`, `narrative_classifier`, plus **`solana`, `evm`,
  `rest-graphql-debug`**.

### 2.1 Finding S-2 — the three Gecko-precedent skills ARE installed and enabled, on the Gecko host

Superseding an earlier incorrect "absent fleet-wide" statement (an artifact of the wrong
`HERMES_HOME`):

| Skill | Host | Source/Trust | Status | Scripts | Size |
|---|---|---|---|---|---|
| `rest-graphql-debug` | srilu-vps | official | **enabled** | **0** | 20K |
| `solana` | srilu-vps | official | **enabled** | 1 | 44K |
| `evm` | srilu-vps | official | **enabled** | 1 | 76K |

The guarded wrapper `/usr/local/bin/gecko-solana-verify` **is present** (mtime 2026-08-01
22:50). Behavioural assessment in `SKILL_SECURITY_REVIEW.md`. **This is a Gecko-workstream
item, not a Shift/Catering/Flyer blocker.**

## 3. Already-installed and underused — the primary opportunity

Present on main-vps today, requiring **no installation**, mapping to brief Parts 5–7:

| Skill | Src | Maps to |
|---|---|---|
| `subagent-driven-development` | local | Shift — subagent delegation (named in the brief) |
| `writing-plans` | local | Shift — structured decision support |
| `debugging-hermes-tui-commands` | local | Shift — Hermes/gateway diagnostics |
| `architecture-diagram` | builtin | Shift — architecture documentation |
| `claude-code`, `codex`, `opencode`, `hermes-agent` | builtin | Shift — coding-agent workflows |
| `kanban-orchestrator`, `kanban-worker`, `kanban-codex-lane` | local | Shift — delegation/queueing |
| `native-mcp` | local | the MCP escape hatch named in `CLAUDE.md` (QBO/Stripe path) |
| `claude-design`, `design-md`, `excalidraw`, `popular-web-designs`, `baoyu-infographic` | builtin/official | Flyer — layout/typography critique |
| `comfyui`, `segment-anything-model` | builtin/local | Flyer — region-scoped edit + masking |
| `dspy` | official | Flyer/Catering — prompt optimisation |
| `webhook-subscriptions` | local | Ops — event plumbing |
| `himalaya` (email) | builtin | Catering — CRM/email handoff |
| `linear` | local | Ops — issue tracking handoff |

**Assessment:** the highest-value near-term action is **routing and enablement discipline over
what is already installed**, not new installation. Confidence is moderate, not high: `skills
list` does not expose per-agent routing bindings, so "underused" is inferred from the absence of
these skills in the agent SKILL.md chains rather than measured from invocation telemetry.

## 4. Routing-surface risk (Part 11 input)

- 98–122 enabled skills per host, **none disabled**, all competing for routing selection.
- Semantically irrelevant to a business fleet but enabled on production:
  `pokemon-player`, `minecraft-modpack-server`, `heartmula`, `spotify`, `ascii-video`,
  `songwriting-and-ai-music`, `pixel-art`, `manim-video`, `p5js`, `touchdesigner-mcp`.
- `godmode` — description `"Jailbreak LLMs: Parseltongue, GODMODE, ULTRAPLINIAN."` — enabled on
  **all three hosts**. See security review.
- Overlap clusters likely to cause ambiguity: three kanban skills; four coding-agent skills
  (`claude-code`/`codex`/`opencode`/`hermes-agent`); six+ creative/design skills.

## 5. Not captured (explicit gaps)

Per-skill **version**, **last-update**, **dependency list**, **network destinations**,
**required credentials**, and **scan verdict** are **not** in this inventory. `skills list` does
not emit them, and obtaining them requires `hermes skills inspect` per skill (feasible, read-only)
and `hermes skills audit` (deferred as possibly-mutating). Scoped as Wave-0 work in
`SKILLS_ADOPTION_PLAN.md`. **No security conclusion in this document rests on an uninspected
skill.**
