# Runbook — Gateway agent toolset scoping & hardening (documentation only)

**This runbook DOCUMENTS the current hardened posture and the prerequisites,
rollback, and verification for any future change. It does NOT authorize
re-enabling any toolset or re-arming any tool.** Every activation below is
separately operator-gated and must be ruled on its own.

## Context

The production `hermes-gateway` is a **single shared agent** serving flyer,
catering, and shift via the cf-router pre-LLM dispatch layer. Hermes 0.14's
`disabled_toolsets` (in `/root/.hermes/config.yaml` under `agent:`) applies
**globally** to that one agent — there is no per-agent toolset scoping on the
pinned stack. `config.yaml` is operator-managed: the deploy only validates it via
the shape gate (`check-hermes-config-yaml`); it never writes it, so edits persist
across deploys.

## Current hardened posture (2026-07-21)

`disabled_toolsets` = `[delegation, skills, browser, clarify, terminal,
code_execution, file]` — **seven toolsets dark.** Basis (18-day tool-invocation
inventory from `/root/.hermes/logs/agent.log*`):

| Toolset | Tools | Evidence for disabling |
|---|---|---|
| `skills` | `skill_manage`, `skill_view`, `skills_list` | **`skill_manage` self-modification hazard** — edit attempts back to 2026-07-03, one completed transiently. Disarmed. |
| `browser` | `browser_*` | zero invocations across the full May–July window |
| `clarify` | `clarify` | zero invocations across the full window |
| `terminal` | `terminal`, `process` | 132 invocations, ALL in 6 May dev-phase conversations; zero in July production |
| `code_execution` | `execute_code` | 51 invocations, all May dev-phase; zero July production |
| `file` | `read_file`, `write_file`, `patch`, `search_files` | 3 invocations, May; zero July production |
| `delegation` | `delegate_task` | pre-existing disable |

July production used only `send_message`, `memory`, `session_search` (residual
armed surface — benign, shared across all agents).

### `skill_manage` disarmament + drift tripwire

`skill_manage` (in the `skills` toolset) is disarmed because a self-modifying
skills tool on a customer-facing agent is a standing hazard. Compensating control:
the **`shift-agent-skills-audit.timer`** is enabled — a root-hardened watchdog
(gateway-unwritable inputs, per PR #583's trust-boundary design) that hash-diffs
`/root/.hermes/skills` against the pinned manifest every 15 min and fires a §12b
operator alert on any curated-skill drift, unknown flat skill dir, or deletion of
a critical skill. At enablement it reported zero curated drift (confirming a manual
hash-diff). This closes the acute self-modification exposure.

## Why no per-agent scoping is built today

With the hazardous toolsets dark globally, the residual per-agent scoping problem
is small (the remaining armed surface is benign and shared). Neither split-gateways
nor a tool-dispatch policy gate is justified for the current loadout.

**Standing trigger (documented, not a current action):** the moment any future
agent needs a tool re-armed that another agent on the shared gateway should NOT
hold, per-agent scoping becomes necessary. The **default answer is a policy gate at
tool dispatch**, keyed on the cf-router-determined agent context (cheaper than
gateway-splitting, and the chokepoint pattern again — one enforcement point at the
dispatch boundary). Split-gateways (separate processes per agent) is the heavier
fallback, reserved for a hazard that is process-level rather than call-level. This
is a design trigger; it does not authorize re-arming anything.

## Activation prerequisites (for any future re-enable — NOT authorized here)

Should a future ruling ever re-enable a toolset, these are the prerequisites that
ruling must satisfy — listed so the bar is known in advance, not to grant it:

1. A named agent-need justification (which agent, which flow, why the tool is
   required and why a deterministic script cannot serve it).
2. Confirmation the tool is not one of the hazard class (`skill_manage`,
   `terminal`, `execute_code`, `file`-write, `browser`) OR, if it is, an explicit
   risk acceptance plus the per-agent scoping control (policy gate) so only the
   needing agent holds it.
3. A soak/observation plan and a §12b alarm on first use.

## Rollback

- **Restore the pre-hardening loadout** (should any disable prove to break a
  needed flow): remove the toolset name(s) from `disabled_toolsets` in
  `/root/.hermes/config.yaml` and `systemctl restart hermes-gateway`. Backups of
  the pre-change configs are retained on-box:
  `config.yaml.bak-skillmanage-20260721`, `.bak-loadout-20260721`,
  `.bak-loadout2-20260721`. Restoring a backup is the fastest full rollback.
- **The shape gate** (`check-hermes-config-yaml`) must pass after any edit — it
  runs in the deploy and can be run standalone before restart.
- No deploy is required for a config-only change (config is operator-managed), but
  the change should be recorded in the approvals log.

## Verification

After any change to `disabled_toolsets`:

1. `check-hermes-config-yaml` → shape gate passes.
2. `systemctl restart hermes-gateway` → `systemctl is-active hermes-gateway` =
   active; journal error-clean.
3. Read-back: `disabled_toolsets` in `config.yaml` matches intent.
4. Post-change tool-invocation watch: `grep -oE "tool [a-z_]+ (completed|returned
   error)" /root/.hermes/logs/agent.log | sort | uniq -c` — confirm no
   just-disabled tool is being invoked, and no unexpected tool appears.
5. `shift-agent-skills-audit.timer` remains enabled and its last run is clean.

## Durability note

These disables live only in the on-box `config.yaml`, which is operator-managed and
not repo-tracked. They persist across deploys (the deploy never writes the file) but
would be lost on a from-scratch box rebuild from a config template. A follow-up
could have the shape gate assert the hazard-class toolsets stay disabled; that is a
separate proposal, not part of this runbook.
