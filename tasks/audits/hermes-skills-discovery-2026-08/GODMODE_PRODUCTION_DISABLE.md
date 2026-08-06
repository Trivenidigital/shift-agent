# GODMODE_PRODUCTION_DISABLE

**Change:** globally disable the `godmode` skill via `skills.disabled` on all three production
VPSes.
**Authorization:** operator ruling, 2026-08-02 — global disable, explicitly **not** a
WhatsApp-only `platform_disabled` restriction.
**Executed:** 2026-08-02. **No gateway restart. No service restart. No other configuration
changed. Skill source NOT deleted.**

## Mechanism and why no restart was needed

`agent/skill_utils.py:385-403` — `_load_raw_config()` caches on
`(path, st_mtime_ns, st_size)`. Editing the file changes both mtime and size, so the cache key
changes and the next `get_disabled_skill_names()` call re-reads from disk. The function's own
docstring confirms it "reads the config file directly."

**Reload-without-restart was therefore the supported path and was used.** Every host's
`ActiveEnterTimestamp` is unchanged post-change, proving no restart occurred.

## Pre-change safeguards (all satisfied)

| # | Safeguard | Result |
|---|---|---|
| 1 | Record effective config + permissions | Captured per host below |
| 2 | Confirm active-process `HERMES_HOME` | Derived from `/proc/<pid>/environ` on each host — **not** assumed |
| 3 | Back up config | `config.yaml.bak-godmode-20260802` via `cp -p` (ownership + perms preserved) |
| 4 | Verify no dependency on `godmode` | **0 references** in config, bundles, systemd units, crontab, `profiles/`, `plugins/` on all three hosts |
| 5 | Do not delete skill source | Untouched — skill remains on disk, merely denied |
| 6 | Do not modify other skill entries | Existing `creation_nudge_interval: 15` preserved on every host; no other key altered |
| 7 | Do not enable skills toolset on main-vps | `agent.disabled_toolsets` verified **unchanged** post-edit (still contains `skills`) |
| 8 | No other routing/platform change | None made |

**Config files are regular files, not symlinks** on all three hosts — verified before editing, so
the project's known `.env`-symlink hazard did not apply.

**Concurrency:** main-vps artifacts (`policy.py`, `shift-agent-policy-preflight`, `bridge.js`)
were byte-identical to the 2026-08-01T23:02Z snapshot immediately before the change — the box had
been quiescent ~1.5 h. Order of application was ascending blast radius: vpin → srilu → main.

## Per-host record

### vpin-vps
| Field | Value |
|---|---|
| Active `HERMES_HOME` | `/root/.hermes` |
| Config path | `/root/.hermes/config.yaml` |
| Owner / perms | `root:root` / `600` — **unchanged after edit** |
| sha256 before → after | `16816869b9663183…` → `4c548db6529d5f72…` |
| Backup | `/root/.hermes/config.yaml.bak-godmode-20260802` (`root root 7110`) |
| Restart / reload | **No restart.** `ActiveEnterTimestamp` unchanged: `2026-08-01 20:21:34 UTC` |

### srilu-vps
| Field | Value |
|---|---|
| Active `HERMES_HOME` | `/home/gecko-agent/.hermes` |
| Config path | `/home/gecko-agent/.hermes/config.yaml` |
| Owner / perms | `gecko-agent:gecko-agent` / `600` — **unchanged after edit** |
| sha256 before → after | `627e9dc44bf13d6d…` → `7304a2b2eb60e4f9…` |
| Backup | `…/config.yaml.bak-godmode-20260802` (`gecko-agent gecko-agent 6530`) |
| Restart / reload | **No restart.** `ActiveEnterTimestamp` unchanged: `2026-08-01 18:17:19 UTC` |

### main-vps
| Field | Value |
|---|---|
| Active `HERMES_HOME` | `/root/.hermes` |
| Config path | `/root/.hermes/config.yaml` |
| Owner / perms | `shift-agent:shift-agent` / `600` — **unchanged after edit** |
| sha256 before → after | `f1601b65a6537a21…` → `04cf3b935481f19e…` |
| Backup | `/root/.hermes/config.yaml.bak-godmode-20260802` (`shift-agent shift-agent 6689`) |
| Restart / reload | **No restart.** `ActiveEnterTimestamp` unchanged: `2026-08-01 21:51:14 UTC` |

## Configuration fragment — before / after (identical shape on all three hosts)

**Before**
```yaml
skills:
  creation_nudge_interval: 15
```

**After**
```yaml
skills:
  disabled:
    - godmode
  creation_nudge_interval: 15
```

No pre-existing `disabled` entries existed on any host, so none were displaced. The insertion is
idempotent-guarded: the script aborts with `ALREADY_HAS_DISABLED_BLOCK` rather than merging blind.

## Validation (run with each host's **active** interpreter and `HERMES_HOME`)

| Check | vpin-vps | srilu-vps | main-vps |
|---|---|---|---|
| YAML loads | OK, 20 keys | OK, 19 keys | OK, 22 keys |
| `skills` block | `{'disabled': ['godmode'], 'creation_nudge_interval': 15}` | same | same |
| `get_disabled_skill_names()` global | `['godmode']` | `['godmode']` | `['godmode']` |
| `…(platform='whatsapp')` | `['godmode']` | `['godmode']` | `['godmode']` |
| `…(platform='cli')` | `['godmode']` | — | — |
| Skill-commands total (was) | — | **97** (was 98) | **121** (was 122) |
| `godmode` in skill-commands | — | **False** | **False** |
| `resolve_skill_command_key('godmode')` | — | — | **`None`** |
| Unrelated skills still resolvable | — | `solana`, `evm`, `writing-plans`, `dspy` ✓ | `writing-plans`, `architecture-diagram`, `claude-design`, `dspy` ✓ |
| `agent.disabled_toolsets` unchanged | n/a | n/a | ✓ still `[delegation, skills, browser, clarify, terminal, code_execution, file]` |
| Gateway active | ✓ | ✓ | ✓ |
| Policy preflight | n/a | n/a | ✓ `status=0` (screening intact) |

**The decisive check for main-vps** — the host where the slash path was the open one — is
`resolve_skill_command_key('godmode') → None` combined with `godmode in commands: False`. The
previously-open user-invocable path is now closed for this skill specifically, while 121 other
skills remain resolvable.

**Index check:** on srilu-vps (where `<available_skills>` is emitted) the skill-command set
dropped 98 → 97 with `godmode` absent. On main-vps the index is suppressed independently by
`disabled_toolsets`, so no index assertion applies there.

**No secrets or sensitive values appear in this record.** All validation output was limited to
skill names, counts, hashes, ownership, and timestamps.

## Rollback

Restore the exact backup and revalidate health:

```bash
P=$(systemctl show hermes-gateway -p MainPID --value)
HH=$(tr '\0' '\n' < /proc/$P/environ | grep '^HERMES_HOME=' | cut -d= -f2)
cp -p "$HH/config.yaml.bak-godmode-20260802" "$HH/config.yaml"
systemctl is-active hermes-gateway     # expect: active (no restart required)
```

Per instruction: **do not re-enable `godmode` merely because pilot work does not use it.**
Rollback is for restoring a broken configuration, not for reversing the security decision.

## Residual notes

- The skill source remains at `<HERMES_HOME>/skills/red-teaming/godmode` on all three hosts,
  intentionally (safeguard 5). It is denied, not removed.
- `skills.disabled` is a **global** denylist; per §2.1 of `SKILL_PILOT_PLAN.md` it is deny-not-allow,
  so any future skill install is enabled by default until explicitly denied. That structural
  limitation is unchanged by this action.
- The Gecko-sensitive skills (`rest-graphql-debug`, `solana`, `evm`) were **deliberately not
  touched** — they remain resolvable on srilu-vps, as confirmed in the validation table. They are
  a separate remediation track owned by the Gecko workstream.
