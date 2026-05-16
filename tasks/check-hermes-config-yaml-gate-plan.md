# Plan — `check-hermes-config-yaml.sh` deploy-time gate

**Drift-check tag:** `extends-Hermes`

**Authoritative backlog:** `tasks/todo.md` P3 §"Config.yaml shape gate (NEW 2026-05-05)" — M2 reviewer finding.

**One-line goal:** Add a deploy-time shape gate over `/root/.hermes/config.yaml` that fails closed when shift-agent-load-bearing fields are missing, mistyped, or contain shape-violating values. Pairs with the existing PR #17 Hermes commit-pin gate and PR #18 `.env` symlink-integrity gate.

**Plan revision history:**
- v1 (2026-05-16) — initial draft.
- **v2 (2026-05-16) — applied 13 findings from two parallel plan reviewers (R1 structural / R2 Hermes-first):** corrected gate insertion point post-`VENV_PY` (R1-2), added `None`-guard for empty YAML (R1-1), dropped `_config_version` (R2-1: confirmed absent on live config), adopted PR #17 two-variable override pattern (R1-4 / R2-2), expanded baseline to actual live keys (R2-3), added second-level subkey enumeration (R1-3), documented `safe_io`-bypass rationale (R2-4), flagged `auxiliary.vision` absence as kill-criterion (R2-5), added dangling-symlink + empty-file test cases (R1-6), named smoke-side purposes (R1-7), added Hermes-upgrade runbook hint (R2-6), grounded test-pattern files (R2-8), documented WARN-not-FAIL tradeoff (R1-5).

---

## 1. Hermes-first capability checklist

Per CLAUDE.md mandatory checklist; live VPS probe + 4-source ecosystem audit 2026-05-16. Receipt at `tasks/.hermes-check-receipts/check-hermes-config-yaml-gate.json`.

### Per-step `[Hermes]` / `[net-new]` table

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Operator edits `/root/.hermes/config.yaml` | `[Hermes]` | Hermes owns the config-file format and load. |
| 2 | Tarball build + scp | `[Hermes]` | Existing `tools/build-deploy-tarball.sh` pattern. |
| 3 | `shift-agent-deploy.sh deploy` orchestrator | `[Hermes]` | Existing project orchestrator; new gate inserts into it. |
| 4 | Hermes pin gate (`tools/check-shift-agent-patch.sh`) | `[Hermes]` | PR #17 already in place. |
| 5 | **Bash wrapper `tools/check-hermes-config-yaml.sh`** | `[net-new]` | Hermes has no `--check-yaml-shape` flag. Live probe 2026-05-16: typo'd file → byte-identical output from `hermes config check` AND `hermes doctor`. |
| 6 | Baseline `tools/hermes-config-yaml-baseline.txt` | `[net-new]` | Mirrors `hermes-patch-baseline.txt` (PR #17). |
| 7 | **Python helper `src/platform/scripts/check-hermes-config-yaml`** | `[net-new]` | Hermes substrate has no shape-validation entry point. |
| 8 | Required-field assertions (`model.default`, `model.provider`) | `[net-new]` | Hermes silently defaults missing keys (verified live). |
| 9 | PyYAML load | `[Hermes]` | Already in Hermes venv (`hermes doctor` → `✓ PyYAML`). |
| 10 | Stdout JSON envelope + exit-code matrix | `[net-new]` | Project gate-contract; consumers are bash wrapper + tests. |
| 11 | `HERMES_CONFIG_GATE_OVERRIDE` + dual-channel audit | `[net-new]` | Mirrors PR #17 `HERMES_PIN_OVERRIDE` two-channel approach. |
| 12 | `log-decision-direct` invocation for audit row | `[Hermes]` | Hermes-substrate chokepoint (deployed since 2026-04-27). |
| 13 | Deploy abort + zero state change | `[Hermes]` | Existing `shift-agent-deploy.sh` pattern. |
| 14 | Smoke-side informational pass | `[net-new]` | Small addition to `shift-agent-smoke-test.sh`. |
| 15 | Pytest exit-code matrix | `[net-new]` | New fixtures + assertions; mirrors `test_safe_io_load_status.py` pattern. |

**[Hermes]:** 6 steps. **[net-new]:** 9 steps.

### Awesome-hermes-agent ecosystem check

`https://github.com/NousResearch/awesome-hermes-agent` returned 404 on 2026-05-16. Fell back to memory `feedback_hermes_skills_landscape.md` (4-source audit 2026-05-03). No YAML-validator skill in `productivity/`, no schema-checking community skill, no MCP server bridging YAML schema validation. **Verdict:** no ecosystem skill closes this gap.

### Live installed Hermes skills inventory (main-vps, 2026-05-16)

56 skill directories at `/root/.hermes/skills/` (full list in `.remote_hermes_inventory.txt`). None are config-validator skills. Plugins dir: `cf-router` only.

### Hermes built-in `config` subcommands (live probe 2026-05-16)

| Command | What it does | Catches typo'd YAML keys? |
|---|---|---|
| `hermes config show` | Prints known fields | **No** — silent on unknowns |
| `hermes config check` | Env-var Required/Optional API key matrix | **No** — does not parse YAML shape |
| `hermes config migrate` | Mutating; adds new fields; no `--dry-run` | **No** — adds, doesn't validate |
| `hermes doctor` | Python deps + file presence + auth + connectivity | **No** — config-file check is "exists" + "version up to date" only |

Probe artifact: `.remote_hermes_check_probe.txt` — typo'd file produced byte-identical output from `hermes config check` AND `hermes doctor` vs clean state.

**Verdict:** Net-new gate required. Drift-check tag `extends-Hermes`: layers custom infrastructure on top of Hermes substrate without modifying it. Scope is the shift-agent-load-bearing subset only — not full Hermes-schema coverage.

---

## 2. Drift-rule self-checks (read deployed code before proposing)

Per CLAUDE.md drift rules §Part 3 — every plan must list the deployed files read, with one bullet per file containing the literal word `Read` and a backtick-quoted path:

- ✅ Read `tools/check-shift-agent-patch.sh` (lines 14–237; `fail/warn/info` helpers; `HERMES_PIN_OVERRIDE` two-variable override; dual-channel audit at lines 96–119; "TO MAKE PERMANENT" hint pattern). **THE canonical deploy-gate pattern to mirror.**
- ✅ Read `tools/check-env-drift.sh` (lines 1–95; `_value_of()` helper with CRLF + quote normalization at lines 36–44; sha256-without-leaking-secrets reporting at lines 63–69; exit codes 0/1/2 contract). Sibling gate; same structural template.
- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (lines 476–636 covering Hermes pin gate at line 484–495, credential-minimized foundation gate at 514–524, state-file migration check at 531–570, env symlink integrity gate at 592–635). Confirmed insertion point: **AFTER Hermes pin gate (line 495) / BEFORE credential-minimized foundation gate (line 514)** — config.yaml shape is asserted on Hermes infrastructure before any dependent work begins.
- ✅ Read `src/agents/shift/scripts/shift-agent-smoke-test.sh` (lines 224–235; step 3 already validates the *shift-agent app's* `/opt/shift-agent/config.yaml` against `schemas.Config`). Confirms our gate is a **distinct surface** (`/root/.hermes/config.yaml`, Hermes's own config) and does NOT duplicate smoke step 3.
- ✅ Read `src/platform/schemas.py` lines 2020–2056 (`class Config(BaseModel)` with `model_config = ConfigDict(extra="forbid")`). Confirms this is the *shift-agent app* schema with strict `extra="forbid"` — the OPPOSITE of Hermes config's silent-accept behavior. We do NOT bind to it for the Hermes-config gate; we define minimal shape requirements as a Python helper.

Additional grounding from live VPS state captured during the audit phase:
- ✅ Read `.remote_hermes_inventory.txt` + `.remote_hermes_check_probe.txt` — 56 installed Hermes skills (none are config-validators); `hermes config check` and `hermes doctor` both produced byte-identical output against typo'd YAML vs clean state.
- ✅ Read `tests/test_safe_io_load_status.py` (lines 1–77; pytest.mark.skipif(Windows) decoration, pure-function unit tests, `pytest.raises(LoadStatusError) as exc` + substring match on `str(exc.value)`). Pattern reusable for Python helper's exception-bearing functions.
- ✅ Read `tests/test_catering_v02_scripts.py` (lines 1–100; subprocess + env-overridable paths + bridge stub via `HTTPServer`; `pytestmark = pytest.mark.skipif(Windows...)`; per-test `env_dir` fixture). **THIS is the canonical pattern for subprocess-invoked script tests** — new `test_check_hermes_config_yaml.py` will mirror the fixture shape and the `subprocess.run(...).returncode` + stderr-substring assertion idiom.
- ✅ Read `.remote_config_keys_probe.txt` (live probe 2026-05-16, post-review-round-1) — verified directly against main-vps `/root/.hermes/config.yaml`. Key findings consumed:
  - **`_config_version` is NOT present** on the live config (neither `_config_version` nor `config_version` returned a value via `dict.get()`). **Dropped from required fields AND from advisory checks** — see §3 update.
  - Actual live top-level keys (21): `WHATSAPP_HOME_CHANNEL, agent, auxiliary, browser, code_execution, compression, delegation, display, fallback_providers, group_sessions_per_user, memory, model, onboarding, platform_toolsets, plugins, provider_routing, session_reset, skills, streaming, stt, terminal`. Baseline `KNOWN_TOP_LEVEL_KEYS` updated to include all of these PLUS the upstream cli-config.yaml.example keys (superset, to avoid false-positive WARNs across customer VPSes that may have additional sections enabled). Was missing 5 keys in v1.
  - Live `auxiliary.vision` shape confirmed: `{provider=auto, model=openai/gpt-4o-mini}`. Confirms conditional-check structure in §3.

### Pre-install bypass-of-`safe_io` rationale (per R2-4)

The Python helper at `src/platform/scripts/check-hermes-config-yaml` is **stdlib + PyYAML only**, NOT routed through `safe_io.load_yaml_model`. This matches the pattern used by `src/platform/credential_readiness.py` (PR #86): pre-install gates run BEFORE `/opt/shift-agent/` artifacts are installed, so the project's `safe_io` module is not importable in that context. The gate explicitly does NOT need the rename-on-corrupt-load behavior `safe_io.load_yaml_model` provides — operator-edited config files surface parse errors in place, and the gate emits `exit 2` "could not parse YAML" instead. This is a deliberate, documented divergence from the `hermes-alignment.md` Part 1 §Storage rule (which applies to RUNTIME app code, not deploy-time gates).

---

## 3. Scope

### In scope (v2 — reviewer-applied)

1. **`tools/check-hermes-config-yaml.sh`** (~130 LOC bash, mirrors `check-shift-agent-patch.sh` shape) — fail-closed gate over `/root/.hermes/config.yaml`:
   - **Required (fail-close if missing, `None`, or wrong shape):**
     - `model.default` — non-empty string containing `/` (provider/model shape)
     - `model.provider` — non-empty string
   - **Conditional (fail-close if PRESENT-but-malformed; absence is OK silently):**
     - `auxiliary.vision.provider` — if present, must be one of `auto`, `openai`, `openrouter`, `anthropic`
     - `auxiliary.vision.model` — if present, must be non-empty string
   - **Advisory (informational only, NEVER fail):**
     - `provider_routing.sort` — if present, must be one of `price`, `latency`, `throughput`
   - **Unknown top-level keys (WARN, not FAIL):** detect by diff vs `KNOWN_TOP_LEVEL_KEYS` superset in baseline file; WARN per unknown key.
   - **Unknown second-level keys under `auxiliary` (WARN, not FAIL) — NEW per R1-3:** if `auxiliary` is a mapping, enumerate its subkeys; for `auxiliary.vision`, also enumerate `vision`'s subkeys. WARN on any subkey not in `{provider, model}` under `auxiliary.vision`, and on any subkey other than `vision` under `auxiliary` itself (catches `auxiliary.visoin.provider` and `auxiliary.web_extraction.…` typo classes). Documented limit: this is shallow; we do NOT walk deeper than 2 levels.
   - **`_config_version` — DROPPED per R2-1.** Live probe 2026-05-16 confirmed neither `_config_version` nor `config_version` is present on main-vps `/root/.hermes/config.yaml`. The `hermes doctor` output already prints "Config version up to date (v23)" by inspecting Hermes-internal state; we do not need to mirror that check.

2. **`src/platform/scripts/check-hermes-config-yaml`** (~140 LOC, stdlib + PyYAML only) — Python helper called by the bash gate. Does YAML load + shape assertion + 2-level subkey enumeration; emits JSON envelope to stdout for the bash wrapper to summarize. **Per R1-1:** explicit `isinstance(doc, dict)` check immediately after `yaml.safe_load` — empty file (`yaml.safe_load` returns `None`) or non-mapping root → `exit 2` with `{"ok": false, "error": "empty_or_non_mapping_yaml"}`. **Per R1-6:** pre-load `os.path.exists()` check follows symlinks → `exit 2` "config.yaml missing or unreadable" for dangling-symlink case.

3. **Wire into `shift-agent-deploy.sh`** — new `=== Hermes config.yaml shape gate ===` block. **Per R1-2 (corrected insertion point):** inserts **AFTER** the `VENV_PY` guard block (after line 506, immediately after the `exit 1` for "Hermes venv Python missing") and **BEFORE** the `=== Credential-minimized Hermes foundation gate ===` (line 514). This guarantees `$VENV_PY` is defined and validated before our gate invokes the Python helper. The block uses `$VENV_PY <staging>/src/platform/scripts/check-hermes-config-yaml /root/.hermes/config.yaml` (the helper accepts a path argument so unit tests can target fixtures).

4. **Override mechanism — TWO VARIABLES per R1-4 / R2-2:**
   - `HERMES_CONFIG_GATE_OVERRIDE_FIELD=<exact-field-name>` — operator MUST type the name of the specific field they are knowingly bypassing (e.g. `model.default`, `auxiliary.vision.provider`). Attestation; analogous to PR #17 requiring the operator to re-type the actual current commit hash.
   - `HERMES_CONFIG_GATE_OVERRIDE_REASON=<free-text>` — non-empty rationale, captured in audit.
   - **Both required;** missing either → no override (gate fails normally). Empty-string for either → no override.
   - **Attestation check:** if `HERMES_CONFIG_GATE_OVERRIDE_FIELD` is set, the helper verifies that the named field IS actually one of the fail-closed fields in the current run's findings. If the operator declares they're overriding `model.default` but the actual failure is `auxiliary.vision.provider`, the override is REJECTED with a clear message. This catches stale-shell-variable bypass attempts.
   - **Dual-channel audit:** append plain-text to `/opt/shift-agent/logs/config-gate-overrides.log` (always succeeds) AND emit `log-decision-direct` JSON entry of type `agent_state_change` with `reason` carrying `config_gate_override field=<field> reason=<reason>`.

5. **Tests in `tests/test_check_hermes_config_yaml.py`** (`pytest.mark.skipif(Windows…)` like `test_catering_v02_scripts.py`; subprocess-invoke + `returncode` + stderr-substring assertions). Exit-code matrix (12 cases — expanded per R1-1, R1-6):
   - C1: clean config → exit 0
   - C2: missing `model.default` → exit 1, stderr names missing field
   - C3: typo'd `model.dafault` → exit 1; stderr names BOTH missing-required AND the unknown key `dafault` under `model`
   - C4: `model.default` integer → exit 1, stderr names wrong-shape field
   - C5: `auxiliary.vision.provider: invalid` → exit 1, stderr enumerates allowed values
   - C6: `provider_routing.sort: badvalue` → exit 0 (advisory) with WARN line
   - C7: `auxiliary.vision` absent entirely → exit 0 (conditional; silent — no WARN because absence is conditional-OK)
   - C8: `auxiliary.visoin.provider: openai` (typo in sub-key) → exit 0 with WARN; stderr names the unknown `auxiliary.visoin` subkey
   - C9: YAML parse error → exit 2, stderr says "could not parse YAML" with line number
   - C10: empty file (PyYAML returns None) → exit 2, stderr says "empty or non-mapping YAML" **(NEW per R1-1)**
   - C11: dangling symlink (target missing) → exit 2, stderr says "missing or unreadable" **(NEW per R1-6)**
   - C12: `HERMES_CONFIG_GATE_OVERRIDE_FIELD=model.default` + `HERMES_CONFIG_GATE_OVERRIDE_REASON=valid` + missing `model.default` → exit 0; both audit channels written
   - C13: `HERMES_CONFIG_GATE_OVERRIDE_FIELD=model.default` + REASON empty → exit 1 (rejected: empty reason)
   - C14: `HERMES_CONFIG_GATE_OVERRIDE_FIELD=model.default` + REASON set, but actual failure is `auxiliary.vision.provider` → exit 1 (attestation mismatch; the rejection is the load-bearing protection)

6. **Smoke-test integration** (`shift-agent-smoke-test.sh`) — adds informational call to `$VENV_PY /usr/local/bin/check-hermes-config-yaml --json /root/.hermes/config.yaml` and greps for `"ok": true`. **Per R1-7 — two explicit purposes:**
   1. **Regression guard on the gate binary itself** — confirms the helper survived install_artifacts (catches `install -m 755` failures, packaging-script bugs, file-perm drift).
   2. **Second warning channel** — surfaces WARN-level issues (unknown top-level keys, second-level subkey typos) to the operator at smoke time even if they dismissed the deploy-side WARNs. Failure here triggers the existing smoke→auto-rollback path.

7. **`tools/hermes-config-yaml-baseline.txt`** — KEY=VALUE format mirroring `hermes-patch-baseline.txt`. **Per R2-3 — baseline now grounded in actual live config:**
   - `KNOWN_TOP_LEVEL_KEYS=WHATSAPP_HOME_CHANNEL,agent,auxiliary,browser,code_execution,compression,container_cpu,container_disk,container_memory,container_persistent,delegation,display,fallback_providers,group_sessions_per_user,honcho,memory,model,onboarding,openrouter,platforms,platform_toolsets,plugins,prompt_caching,provider_routing,security,session_reset,skills,streaming,stt,terminal,tool_loop_guardrails,worktree` — union of live main-vps keys (21) + upstream cli-config.yaml.example keys (per WebFetch 2026-05-16). 32 keys total. **Per R2-6:** include a 10-line maintenance header at the TOP of the file documenting the operator runbook for updating after Hermes upgrades: run the inventory script, diff against current baseline, append legitimate new keys, commit + ship a new tarball.

### Out of scope (explicit; per R2-1 — `_config_version` no longer claimed)

- Validating Hermes's full schema (Hermes owns it; we'd drift on upgrade)
- Validating `/opt/shift-agent/config.yaml` (already covered by smoke step 3 against `schemas.Config`)
- Auto-fixing typos (same posture as PR #17 + #18 — operator fixes manually)
- Validating semantic values (model existence at provider — that's `vision-auth-smoke`'s job)
- Multi-tenant / multi-VPS comparison
- **Config-version surfacing** — `hermes doctor` already prints "Config version up to date (v23)"; we don't need to mirror it.
- **Walking deeper than 2 levels of YAML.** WARN-not-FAIL on unknown second-level subkeys catches the common typo class (`auxiliary.visoin.provider`) but stops at depth 2. Deeper typos (e.g. `auxiliary.vision.providre`) are out of scope; would require a full Hermes schema, which we explicitly do not own. The WARN-not-FAIL tradeoff is **a conscious accept of partial closure for non-load-bearing subtrees** (per R1-5).

### Out of scope (explicit)

- Validating Hermes's full schema (Hermes owns it; we'd drift on upgrade)
- Validating `/opt/shift-agent/config.yaml` (already covered by smoke step 3 against `schemas.Config`)
- Auto-fixing typos (same posture as PR #17 + #18 — operator fixes manually)
- Validating semantic values (model existence at provider — that's `vision-auth-smoke`'s job)
- Multi-tenant / multi-VPS comparison

---

## 4. Test plan

### Local (Windows host)

- `python -m pytest tests/test_check_hermes_config_yaml.py -q` — full exit-code matrix (10 fixture YAML files).
- `python -m py_compile src/platform/scripts/check-hermes-config-yaml` — syntax check.
- `bash -n tools/check-hermes-config-yaml.sh` — bash syntax (Git Bash).
- `tests/test_repo_invariants.py` — extend if needed for the new scripts.

### VPS (post-PR; operator manual step before merge)

- `sudo /opt/shift-agent/staging-new/tools/check-hermes-config-yaml.sh` against live config → exit 0.
- Inject `model.dafault: foo` into a copy → exit 1 with informative stderr.

---

## 5. Runtime-state assumptions (per CLAUDE.md §9a)

1. **Current `/root/.hermes/config.yaml` on main-vps validates cleanly.** Verified via direct probe 2026-05-16 (`.remote_config_keys_probe.txt`): `model.default=openai/gpt-4o-mini`, `model.provider=openrouter`, `auxiliary.vision.{provider=auto, model=openai/gpt-4o-mini}`. Gate MUST NOT block routine deploys. **Test C1 fixture mirrors the live shape verbatim.**
2. **`_config_version` is NOT present on main-vps.** Confirmed empty via `dict.get()` probe 2026-05-16. The plan's v1 advisory check on this field was incorrect (R2-1 catch); removed.
3. **PyYAML in Hermes venv.** Verified via `hermes doctor` → `✓ PyYAML`.
4. **`/opt/shift-agent/logs/` exists or can be created.** Same as PR #17 override log path.
5. **`log-decision-direct` at `/usr/local/bin/`.** Verified via `shift-agent-smoke-test.sh:24`.
6. **Top-level keys on live main-vps are a subset of the proposed `KNOWN_TOP_LEVEL_KEYS` baseline.** Probe 2026-05-16: all 21 live keys are included in the v2 baseline (32 keys total — live 21 ∪ upstream 11). Per R2-3 — baseline updates do NOT introduce spurious WARNs on the current live config.
7. **UNVERIFIED ASSUMPTION (per R2-5):** the conditional check on `auxiliary.vision.*` assumes Hermes gracefully falls back when the entire `auxiliary.vision` block is ABSENT (gateway starts; vision routing degrades to default provider). We have NOT tested this on the live VPS (would require removing the block + restarting hermes-gateway under operator supervision). **If the design or build phase surfaces evidence that `auxiliary.vision` absence causes a Hermes startup failure**, promote it from CONDITIONAL to REQUIRED in §3.1 and re-baseline tests C7. Tracked as a §6 kill criterion.

---

## 6. Kill criteria

- Plan review reveals M2 surface is closed by something missed (Hermes flag, community skill, MCP). Re-run Hermes-first audit.
- Live VPS probe reveals a Hermes CLI flag that DOES catch typos. Switch to wiring that in; drop net-new bash.
- Gate as designed false-positives the CURRENT main-vps config. Reduce required-shape until current passes.
- Override mechanism surfaces a no-audit path. Tighten before ship.
- **Per R2-5:** if design or build surfaces evidence that `auxiliary.vision` absence causes a Hermes startup failure (rather than graceful fallback), promote the field from CONDITIONAL to REQUIRED and re-baseline test C7 + the conditional-vs-required logic in §3.

---

## 7. Pipeline cadence

~320 LOC + ~180 LOC tests across 6 files → **Medium pipeline:** Plan → 2-review → Design → 2-review → Build → PR → 3-review. Matches operator's session request.

---

## 8. Reviewer-lens preview

When dispatching plan reviewers, prime them on different attack vectors:

- **Reviewer R1 — Structural / silent-failure:** does this gate actually close the M2 surface, or are there typo classes (case sensitivity? `Default` vs `default`? deeply-nested keys not in the load-bearing subset?) it misses? Could WARN-not-FAIL on unknown keys let a real bug through? Are there shape-violation classes (None values, empty strings, list-where-string-expected) not enumerated in §3.5? What if PyYAML returns `None` for an empty file?
- **Reviewer R2 — Hermes-first / drift / scope:** is this gate needed? Did I miss a Hermes CLI flag in the probe? Is scope too broad (validating things Hermes does for us) or too narrow (leaving load-bearing fields unguarded)? Does the proposed approach violate canonical conventions in `docs/hermes-alignment.md` Parts 1 and 3? Did I correctly identify smoke-side step 3 as a distinct surface (Hermes config vs shift-agent app config)?

---

## 9. Deliverables checklist (for build phase)

- [ ] `tools/check-hermes-config-yaml.sh` — bash wrapper, ~130 LOC, mirrors `check-shift-agent-patch.sh`; two-variable override (`HERMES_CONFIG_GATE_OVERRIDE_FIELD` + `HERMES_CONFIG_GATE_OVERRIDE_REASON`); attestation check rejects field/failure mismatch
- [ ] `src/platform/scripts/check-hermes-config-yaml` — Python helper, ~140 LOC stdlib + PyYAML only; `None`-guard; pre-load `os.path.exists()` symlink check; 2-level subkey enumeration under `auxiliary`; JSON envelope to stdout
- [ ] `tools/hermes-config-yaml-baseline.txt` — KEY=VALUE baseline; 32 keys; operator runbook header
- [ ] `tests/test_check_hermes_config_yaml.py` — 14 exit-code test cases; `pytest.mark.skipif(Windows)` per `test_catering_v02_scripts.py` pattern
- [ ] `src/agents/shift/scripts/shift-agent-deploy.sh` — new `=== Hermes config.yaml shape gate ===` block inserted AFTER `VENV_PY` guard (after line 506) / BEFORE credential-minimized foundation gate (line 514)
- [ ] `src/agents/shift/scripts/shift-agent-smoke-test.sh` — informational post-restart pass with two stated purposes (gate-binary regression guard + second WARN channel)
- [ ] `docs/hermes-alignment.md` Part 2 — mark "Config.yaml shape gate" Resolved with PR link
- [ ] `tasks/todo.md` P3 §"Config.yaml shape gate" — flip to ✅ with date

---

## 10. Reviewer-finding closure summary (v1 → v2)

| Finding | Severity | Reviewer | Resolution |
|---|---|---|---|
| R1-1 — `yaml.safe_load` on empty file returns `None` | HIGH | R1 | §3.2 `None`-guard + new test C10 |
| R1-2 — Insertion point before `VENV_PY` definition | HIGH | R1 | §3.3 corrected to AFTER `VENV_PY` guard (line 506) |
| R2-1 — `_config_version` field name unverified | HIGH | R2 | Probed live config; field absent; **dropped from scope** |
| R2-2 + R1-4 — Single-variable override risks fat-finger bypass | HIGH+MED | R2+R1 | §3.4 adopted PR #17 two-variable + attestation check |
| R2-3 — Baseline derived from upstream docs, not live config | HIGH | R2 | §3.7 baseline updated to live keys (21) ∪ upstream (11) |
| R1-3 — Second-level typos slip through | MED | R1 | §3.1 second-level enumeration under `auxiliary` |
| R1-5 — WARN-not-FAIL tradeoff implicit | MED | R1 | §3.7 "Out of scope" paragraph documents conscious accept |
| R2-4 — `safe_io.load_yaml_model` Part 1 alignment | MED | R2 | §2 new "Pre-install bypass-of-`safe_io` rationale" paragraph |
| R2-5 — `auxiliary.vision` absence behavior unverified | MED | R2 | §5 flagged as unverified assumption + §6 kill criterion added |
| R1-6 — Symlink test case missing | LOW | R1 | §3.5 new test C11 (dangling symlink) + §3.2 pre-load `os.path.exists()` |
| R1-7 — Smoke-side pass purposes vague | LOW | R1 | §3.6 two purposes named explicitly |
| R2-6 — Baseline-update runbook missing | LOW | R2 | §3.7 maintenance header on baseline file |
| R2-8 — Test files not in §2 self-checks | INFO | R2 | §2 added two test files with line-range citations |

**13 findings, all closed.** Two reviewers' verdicts (APPROVE WITH CHANGES) converted to APPROVE upon v2 plan.
