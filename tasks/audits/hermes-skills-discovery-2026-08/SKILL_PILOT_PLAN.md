# SKILL_PILOT_PLAN (revision 3)

**Date:** 2026-08-02. **No pilot run. No skill or script executed. No production configuration
changed. Nothing installed, disabled, renamed, or consolidated.** Method: static source tracing
of the active v0.19.1 install plus read-only config inspection, per host.

---

## 1. What `disabled_toolsets: skills` gates on main-vps — precise statement

### 1.1 The model-routing path is closed

`agent/system_prompt.py:299-327` gates index construction on the skills tools themselves:

```python
has_skills_tools = any(name in agent.valid_tool_names
                       for name in ['skills_list', 'skill_view', 'skill_manage'])
if has_skills_tools:
    skills_prompt = _r.build_skills_system_prompt(...)
else:
    skills_prompt = ""            # no <available_skills> block emitted
```

On main-vps, `disabled_toolsets: skills` therefore removes **both**:
1. the model-facing tools `skills_list` / `skill_view` / `skill_manage`, and
2. the `<available_skills>` prompt index (second-order consequence of the same gate).

**Therefore: installed skills are not autonomously discoverable or selectable through the normal
model-routing path on main-vps.**

That statement is deliberately bounded. It is **not** a claim of universal unreachability — the
other five loading paths are assessed individually below, and **one of them is open**.

### 1.2 Per-path assessment (each checked separately)

| PATH | STATUS ON MAIN | EVIDENCE | EFFECT OF `disabled_toolsets: skills` |
|---|---|---|---|
| **1. Model tools** (`skills_list`/`skill_view`/`skill_manage`) | **CLOSED** | `acp_adapter/tools.py:36-38` | Removed from `valid_tool_names` |
| **2. `<available_skills>` index** | **CLOSED** | `system_prompt.py:299` → `else: skills_prompt = ""` | Suppressed (second-order) |
| **3. Direct `/skill-name` slash** | **OPEN — corrects my prior claim** | `gateway/run.py:14707-14775` — *"Skill slash commands: /skill-name loads the skill and sends to agent"*; imports `get_skill_commands`, `resolve_skill_command_key`, `build_skill_invocation_message`. Also `gateway/platforms/webhook.py:773-776`. Supports **stacked** `/a /b …` up to 5 | **Unaffected.** Independent of the toolset |
| **4. Explicit preload** (`hermes -s`, `HERMES_TUI_SKILLS`) | **OPEN — operator/CLI only** | `skill_commands.py:747`; sole caller `cli.py:17616`; **no `gateway/` caller** | Unaffected; honours the denylist (`skill_commands.py:753-760`) |
| **5. Skill bundles** | **CLOSED — unconfigured** | No `bundles` key in main-vps config; dispatch exists at `gateway/run.py:14711-14745` and would take precedence if configured | Unaffected if configured |
| **6. Cron / dispatcher-bound** | **OPEN — but not library skills** | The SME dispatcher SKILL.md chain is live in production. **No systemd unit or cron entry passes `-s`/`--skills`/`HERMES_TUI_SKILLS`** (checked: 0 matches, 0 hermes cron entries) | Unaffected — a distinct surface |
| **7. Plugin skill injection** | **CLOSED in practice** | `hermes_cli/plugins.py:1228` — plugin skills surface *through* the suppressed index. `cf-router` defines `register(ctx)` with no skill registration; `shift-agent-policy` registers an adapter + hook, no skills | Suppressed with the index |
| **8. Repo-specific preloading** | **NONE** | No preload mechanism in `src/` | n/a |

### 1.3 The correction that matters

**Withdrawn:** my prior statement that slash invocation is "not a gateway path."
`gateway/run.py:14749-14753` proves otherwise — the gateway resolves and loads skills from a
`/skill-name` message, entirely independently of `disabled_toolsets`.

**Consequence:** on main-vps, model-driven selection is closed, but **user-driven slash
invocation is open**, subject only to whoever can address the agent. This is the single most
important reachability fact in this document, and it changes the `godmode` assessment (§3).

**Not verified (stated, not assumed):** whether `cf-router`'s `pre_gateway_dispatch` intercepts
or rewrites `/command` text before it reaches this resolver, and whether the WhatsApp adapter
populates `command` for arbitrary inbound text. `WHATSAPP_ALLOWED_USERS` is set, which bounds who
can address the agent at all. These would narrow practical reachability but do **not** close the
code path.

## 2. Denylist isolation — replacing the "all-or-nothing" claim

**Withdrawn:** "enabling the skills toolset would expose all 122 skills with no per-skill
control."

v0.19.1 provides a genuine per-skill deny mechanism (`agent/skill_utils.py:448-454`):

```python
global_disabled = _normalize_string_set(skills_cfg.get("disabled"))
if resolved_platform:
    platform_disabled = (skills_cfg.get("platform_disabled") or {}).get(resolved_platform)
    if platform_disabled is not None:
        return global_disabled | _normalize_string_set(platform_disabled)
return global_disabled
```

And the gateway slash path **already consults it at the invocation site**
(`gateway/run.py:14757-14767`), returning
*"The **{skill}** skill is disabled for {platform}."* So `skills.platform_disabled.whatsapp` is
not a theoretical control — it is the exact enforcement point for the open path in §1.2.

**Current state on main-vps:** the `skills:` block contains only `creation_nudge_interval: 15`.
**No skill is denied, globally or per-platform.**

### 2.1 Limitations of denylist-based isolation (real, and they matter)

1. **Deny, not allow.** New skills are enabled by default; every future install is exposed until
   someone remembers to deny it. There is no positive per-profile allowlist.
2. **Semantics differ by call site.** `get_skill_commands()` applies only the **global** list at
   scan time; per-platform denial is re-checked at each invocation site — the gateway comment says
   this explicitly ("the cache is process-global across platforms"). Correctness depends on every
   call site remembering to pass `platform=`.
3. **Documented bypasses.** Bundle loading and preload both *bypass* the scan-time disabled
   filter and must re-check explicitly (`skill_commands.py:753-760`, `gateway/run.py:14721-14726`).
   These re-checks exist today, but the pattern is a standing sharp edge.
4. **Scale.** Curating a denylist across 98–122 skills per host is meaningful ongoing work, and
   an omission fails open.

**Net:** denylist isolation is sufficient for a curated, small, deliberate exposure (Stage B) but
is **not** the right instrument for Stage A, where an isolated `HERMES_HOME` gives categorically
stronger isolation at lower effort.

## 3. `godmode` — assessed separately per host, because routing differs

| | **main-vps** | **srilu-vps** | **vpin-vps** |
|---|---|---|---|
| `disabled_toolsets` | `skills` disabled | **absent** | **absent** |
| In `<available_skills>` index | **No** (suppressed) | **Yes** (inferred from the same code path) | **Yes** (same) |
| Model-selectable | **No** | **Yes** | **Yes** |
| **Slash-invocable from the platform** | **YES — open path, not denied** | Yes | Yes |
| In a bundle | No (none configured) | Not checked | Not checked |
| Referenced by cron/dispatcher/plugin | **No** (greps returned nothing) | Not checked | Not checked |
| Denied via `skills.disabled` / `platform_disabled` | **No** | Not checked | Not checked |
| Evidence of ever being selected | **None** (0 log hits) | None | None |
| **Ruling** | **`NEEDS_NARROWING`** | **`LIMITED_USE`** | **`LIMITED_USE`** |

**main-vps ruling upgraded from `BENIGN_AND_UNROUTED` to `NEEDS_NARROWING`.** The earlier ruling
rested on the index being suppressed; §1.3 shows the slash path is open and undenied. Capability
is already established (4 scripts, 7 files with subprocess/exec, reads `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / `OPENROUTER_API_KEY`, outbound to `openrouter.ai`, one package install).

**Still not `ACTIVE_SECURITY_DEFECT`:** no evidence of selection anywhere, and practical
reachability is further bounded by `WHATSAPP_ALLOWED_USERS` and possibly by `cf-router`
interception (§1.3, unverified).

**Recommended narrowing (not executed):** add `godmode` to
`skills.platform_disabled.whatsapp` — the gateway checks exactly that at the slash site. Cheap,
reversible, and it does not disturb srilu/vpin. Decision remains the operator's; no change made.

## 4. Pilot readiness — Stage A and Stage B assessed separately

### Stage A — approved shape
Explicit, sanitized, **operator-controlled** invocation (path 4) against an **isolated
`HERMES_HOME`**. Unaffected by `disabled_toolsets`; never touches the WhatsApp gateway; requires
**no production configuration change**. Each pilot generates its own evidence record, so the
absence of historical invocation telemetry is not an obstacle.

**Per-pilot evidence record** (written to `pilot-runs/<pilot>/<fixture>-<run>.md`):

```
pilot ID · agent/workflow · skill name · skill path + content SHA-256 · fixture ID ·
invocation path · input · output · evaluation result · prohibited-effect result ·
timestamp · operator
```

### Stage B — remains gated
Live-agent routing on main-vps. Blocked on two things, neither attempted here:
1. **Live-agent routing design** — deciding whether skills reach the agent by re-enabling the
   toolset (restores model-driven selection over 122 descriptions) or by the already-open slash
   path (user-driven, explicit).
2. **A curated per-skill deny configuration** — `skills.disabled` +
   `skills.platform_disabled.whatsapp`, authored deliberately, accounting for §2.1's limitations.

**Stage B is not proposed in this phase.**

## 5. The six pilots — reconciled

Count: **six**, one row each. Two Shift (installed skills), two Catering (custom), two Flyer
(custom). All Stage A, all invocation path 4, all isolated `HERMES_HOME`, all sanitized fixtures.
Universal prohibited effects: no outbound send, no state mutation, no service restart, no deploy
or config change, no secret access, no destructive command. Universal rollback: delete the
isolated home and `pilot-runs/<pilot>/`.

| PILOT | TARGET AGENT | SKILL | INSTALLED OR CUSTOM | INVOCATION PATH | BASELINE | SUCCESS METRIC | PROHIBITED EFFECTS | ROLLBACK |
|---|---|---|---|---|---|---|---|---|
| **P1** Runtime-effective diagnosis | Shift | `debugging-hermes-tui-commands` | **Installed** — prose-only: 1 file, 0 scripts, 0 network, 0 subprocess, 0 env reads | Operator preload, isolated home | 3 real incidents from this engagement where a path/file was read instead of the running process — all 3 produced wrong conclusions | ≥2/3 reach the correct conclusion **and** cite process-level evidence (`/proc/<pid>`, active interpreter, resolved `HERMES_HOME`) | No restart, config write, deploy change, or secret access | Delete home + run dir |
| **P2** Architecture documentation | Shift | `architecture-diagram` | **Installed** — prose-only: 2 files, 0 scripts, 0 subprocess | Operator preload, isolated home | Hand-maintained routing description in `dispatch_shift_agent/SKILL.md` | Reviewer confirms the diagram matches deployed routing priority; no invented or omitted branch | **No network fetch** — skill references `fonts.googleapis.com`; must render offline, verified | Delete run dir |
| **P3** Inquiry completeness | Catering | `org/catering-inquiry-completeness` | **Custom, prose-only** | Operator preload, isolated home | Current first-response behaviour on the same fixtures | 8/8 contain **zero** price/availability tokens; ≤4 clarification questions; structured extracted fields emitted; explicit missing-required-field list | **Any** invented price, menu item, availability, tax, delivery charge, staffing commitment, minimum, or discount → immediate fail. No send | Delete skill file + run dir |
| **P4** Approved-data proposal draft | Catering | `org/catering-approved-data-proposal` | **Custom, prose-only** | Operator preload, isolated home | Current proposal output on the same approved fixture | Every commercial claim carries a trace to a named fixture field; unresolved fields listed; the deliberately-omitted-price fixture **fails closed** rather than estimating | No invented, averaged, or interpolated pricing or commitment. Draft only — never sent, never treated as approved | Delete skill file + run dir |
| **P5** Unintended-change detection | Flyer | `org/flyer-exact-edit-review` | **Custom, prose-only** | Operator preload, isolated home; interprets a **deterministic** image diff | Recurring-failure list from the 2026-07-13 flyer audit | ≥5/6 seeded defects identified **and** localized; **0** false "unchanged" on a seeded defect | Never alters an image; never judges logo/QR correctness itself — reports the deterministic diff only | Delete skill file + run dir |
| **P6** Bounded edit-scope specification | Flyer | `org/flyer-edit-scope-spec` | **Custom, prose-only** | Operator preload, isolated home | Current free-form edit requests, whose dominant failure is whole-design regeneration | 6/6 name exactly one edit region and explicitly list logo, QR, footer, prices, addresses, phone, dimensions as must-not-change | **Instruction only** — no image generated or modified | Delete skill file + run dir |

### 5.1 Flyer coverage against the required preservation dimensions

**P5** is the pilot that directly tests preservation. Its six sanitized approved/revision pairs
each seed one known defect, covering the full required set:

| Required dimension | P5 fixture |
|---|---|
| exact-copy preservation | altered approved headline copy |
| logo identity | logo letterform added/removed |
| QR identity | QR substituted with a different code |
| price / offer preservation | promotional price changed |
| address + phone preservation | footer address and phone altered |
| dimension / aspect-ratio preservation | aspect ratio changed |
| requested-region-only modification | edit applied outside the requested region |

**P6** is the preventive counterpart: it produces the edit instruction that should stop those
defects from being introduced.

### 5.2 Why not `claude-design` (or another installed creative skill)

Inspected: `description: "Design one-off HTML artifacts (landing, deck, prototype)"`, 650 lines
oriented to **producing new designs**. It promotes redesign, which is precisely Flyer Studio's
dominant failure mode. **Rejected for both Flyer pilots**, reversing my earlier recommendation.
`architecture-diagram` is likewise a generation skill and is used only in P2, where generation is
the intended outcome. No installed skill tests *controlled revision*, so P5 and P6 use narrowly
scoped custom prose-only skills.

### 5.3 Why the Catering pilots are custom

Neither Catering workflow is served by an installed library skill.
`parse_catering_inquiry` and `creative_catering_proposals` are **production dispatcher-chain
skills** — piloting them would alter live behaviour, which Stage A forbids. P3 and P4 are custom
prose-only skills authored into the isolated home, matching Candidate A and Candidate B.

## 6. Considered and rejected

| Skill | Reason |
|---|---|
| `claude-design` | Promotes redesign over controlled revision (§5.2) |
| `subagent-driven-development` | `delegation` toolset disabled on main-vps; exceeds read-only/draft-producing |
| `writing-plans` | Adequate but generic; a scoped custom skill (P6) tests the actual invariant |
| `segment-anything-model` | Listed name ≠ installed path (`skills/mlops/models/segment-anything`); not inspected; not prose-only |
| `comfyui` | Produces images — state-producing |
| `native-mcp` | External MCP privilege surface not understood |
| `himalaya` | Credentialed email — would gain outbound-send authority |
| all community skills | Out of scope this phase |

## 7. Gecko-sensitive skills — remediation proposal (separate track)

Read-only inspection; nothing executed or changed.

| | `rest-graphql-debug` | `solana` | `evm` |
|---|---|---|---|
| Version / source | 1.2.0 official | 0.2.0 official | 1.0.0 official |
| Files / scripts | 1 / **0** | 2 / `scripts/solana_client.py` | 2 / `scripts/evm_client.py` |
| Network | example.com placeholders | **`api.mainnet-beta.solana.com`**, coingecko, `your-private-rpc.com` | coingecko, avax, arbitrum, arbiscan, basescan, ensideas |
| Refs to `gecko-solana-verify` | **0** | **0** | **0** |
| Model-visible on srilu | **Yes** — no `disabled_toolsets`, index emitted | Yes | Yes |
| Slash-invocable on srilu | **Yes** | **Yes** | **Yes** |

Restrictions are documented but **not technically enforced**: the wrapper exists, nothing
references it, and the skill embeds the public RPC endpoint directly.

**Proposed remediation (Gecko workstream):** (1) verify `SOLANA_RPC_URL` in the **consuming
process environment**, not the `.env` file; (2) make the restriction enforceable — point the
client at the wrapper, or remove the skill and expose only the wrapper; (3) rule on
`rest-graphql-debug` given the 0-script finding; (4) prefer `skills.platform_disabled` over
uninstalling, after checking for breakage. **Not a blocker for these pilots** — different host,
and every pilot uses an isolated home.

## 8. FINAL RECOMMENDATION

**Stage A: `PILOTS_READY`.**
All six run through an explicit, sanitized, operator-controlled invocation path (`skill_commands.py:747`
→ `cli.py:17616`) against an isolated `HERMES_HOME` — unaffected by `disabled_toolsets: skills`,
never touching the WhatsApp gateway, requiring no production change. Each pilot generates its own
evidence record, so missing historical telemetry is not a barrier. No named technical defect must
change first.

**Stage B: `PILOTS_READY_AFTER_SPECIFIC_FIXES`**, gated on (i) live-agent routing design and
(ii) a curated per-skill deny configuration accounting for §2.1.

**Separate from pilot readiness — recommended for operator decision:** `godmode` on main-vps is
`NEEDS_NARROWING` because the slash path is open and undenied (§3). The remedy is one denylist
entry at an enforcement point the gateway already consults. **Not executed; no configuration
changed.**

**Preconditions for Stage A are ordinary setup, not fixes:** build the isolated `HERMES_HOME`,
author the four custom prose-only skills into it, and build the sanitized fixture sets
(P1×3, P3×8, P4×5, P5×6, P6×6).


---

## Stage A execution outcome (2026-08-02) — factual corrections

Two runs executed; see `stage-a/STAGE_A_RESULTS.md` (run 2) and `stage-a/STAGE_A_RESULTS_RUN1.md` (run 1).

- **P1's skill was replaced.** `debugging-hermes-tui-commands` failed 3/3 and is retired from this pilot; the candidate is now the custom prose-only `org-shared-runtime-effective-diagnosis`.
- **`claude-design` remains rejected**; P5/P6 use custom prose-only skills, as planned.
- **P5 is `DEFERRED_TO_FLYER_STUDIO_VISUAL_PIPELINE`** — the one-shot CLI has no verified image input (`P5_DESIGN_NOTE.md`). Not counted as a failed pilot.
- **Harness correction:** run 1 had a `STAGE_A_HARNESS_ISOLATION_DEFECT` (web + file tools available). Run 2 locked the tool surface and proved it (`HARNESS_ISOLATION_VALIDATION.md`).
- **No pilot promoted to Stage B.** P4 did not repeat 4/4 under locked tools (3 PASS / 1 PARTIAL).
- Cumulative provider cost across both runs: **$0.117123** of the $5.00 cap.
