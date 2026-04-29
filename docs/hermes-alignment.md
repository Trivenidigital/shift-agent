# SMB-Agents — Hermes Alignment & Operational Hygiene

**Project:** SMB-Agents (per-customer agent suite running on Hermes runtime)
**Status:** v1 — captures patterns deployed as of 2026-04-28
**Audience:** anyone proposing schema, test, or architecture work on this codebase, including future Claude/Copilot/human contributors

---

## How to read this document

Four parts:

1. **Deployed patterns** — what `src/platform/` and the agent layer actually do today. Descriptive, not prescriptive.
2. **Operational drift checklist** — silent-failure surfaces ranked by stakes, not by abstract importance, with realistic costs.
3. **Working agreement** — the structural fix for "drift via imported priors": read deployed code before proposing.
4. **What this doc is NOT** — explicit limits so the doc doesn't creep toward doctrine.

This is a working document, not a constitution. When deployed patterns change, update Part 1. When the checklist gets resolved or accumulates new items, update Part 2. The drift-check workflow in Part 3 is a habit to enforce, not a process gate.

---

## Part 1 — Deployed patterns

What `src/platform/` provides today and how agents are expected to use it.

### Storage

- **JSON-on-disk + `fcntl.flock` + atomic writes.** All state lives at `/opt/shift-agent/state/*.json` on the VPS, written through `safe_io.atomic_write_json` and read with `safe_io.load_model` (Pydantic-validating). NDJSON for append-only logs (`decisions.log`).
- **YAML files (`config.yaml`) use `safe_io.load_yaml_model`, NOT `load_model`.** `load_model` calls `safe_load_json` which calls `json.loads` and rename-quarantines on `JSONDecodeError`. Calling it on YAML content silently moves the customer's actual config aside as `config.yaml.corrupt-<epoch>`. PR #34 added `load_yaml_model` (yaml-aware, no auto-rename, raises explicitly). Operator-edited files surface parse errors in place; auto-quarantine is wrong policy for them.
- **No database engine in the request path.** No SQLite, no Postgres. If you need concurrent multi-writer or query patterns this can't handle, propose alternative explicitly — don't introduce silently.
- **Per-customer-VPS isolation.** Each VPS is single-tenant. State files are not shared across customers. This is the whole reason JSON-on-disk is sufficient.

### LLM call pattern

- **Dispatcher SKILL routes; handler SKILL processes; Python script does data work.** Each inbound WhatsApp message → `dispatch_shift_agent` SKILL classifies sender + shape → routes to one downstream handler SKILL → handler invokes deterministic Python scripts via Hermes' `terminal` tool.
- **Each SKILL has its own prompt scope.** The dispatcher reads SKILL `description:` fields to make routing decisions; handlers read their own SKILL.md plus the message body. SKILLs do not share prompt context.
- **LLM never sees prices, IDs, or sensitive state.** Catering quote rendering is the canonical example: Python loads `catering-menu.json`, filters by dietary tags from the lead, substitutes into a template. The LLM extracts dietary tags into the lead struct; everything downstream is deterministic.

### Audit pattern

- **NDJSON audit log at `/opt/shift-agent/logs/decisions.log`.** Append-only via `safe_io.ndjson_append` (flock + atomic write + fsync). File perms `0640 shift-agent:shift-agent`. Multiple writers append directly: `log-decision-direct` (used by SKILLs via Hermes' terminal tool) plus per-agent scripts (`apply-catering-owner-decision`, `apply-menu-update`, `create-catering-lead`, `create-proposal`, `update-proposal-status`, etc.). Every writer uses the same chokepoint.
- **30-variant discriminated union (`LogEntry`)** in `src/platform/schemas.py`. Add new variants to the union when shipping a new agent or new state transition. The pattern: subclass `_BaseEntry`, set `type: Literal["..."]`, list new fields with explicit types.
- **Rotation:** daily via logrotate (`/etc/logrotate.d/shift-agent`), 30-day retention, archived to `/var/log/shift-agent-archive/`.
- **No cryptographic tamper-evidence.** The integrity story is `0640` perms + Linux file ACLs + off-server backups + the deploy-time gates (Hermes pin, env symlink) which fail-close on state drift. If a future compliance requirement needs cryptographic chain (regulator audit, customer dispute defense), add `_append_sha_chain` at the `safe_io.ndjson_append` chokepoint, add a `verify-decisions-log` script, add daily-cron verification, run one-time backfill. Architecture is straightforward; we just don't have the requirement today. See backlog.

### Approval-code pattern

- **5-char `#XXXXX` codes** drawn from a 28.6M-entry alphabet (excluding ambiguous chars 0/O/1/I/L). Generated via `generate_unique_code` which check-and-rejects against the active per-VPS pool of issued codes.
- **Shared namespace across agents.** Catering leads, menu pending updates, and Shift proposals all draw from the same code space; the dispatcher disambiguates by checking each state file in priority order.
- **TTL via state-file expiry.** Codes are not first-class entities; they live as fields on the proposal/lead/menu-pending records that own them. When the parent record reaches a terminal status, the code becomes inert.

### Schema pattern

- **Pydantic v2 models with explicit `model_config`.** `extra="forbid"` on most state schemas (catches LLM-emitted typos). `extra="ignore"` on `CateringLeadExtractedFields` and similar LLM-output shapes (extractor may emit extras we don't model).
- **Type when downstream code requires it OR when safety/correctness depends on structure that free-text can't reliably preserve.** Example of the first: `headcount: Optional[int]` is typed because quote-rendering needs it as an int. Example of the second: `MenuItem.dietary_tags: list[DietaryTag]` is a constrained Literal because the menu filter can't reliably exclude peanut items if "severe peanut allergy" lives in `notes: str`.
- **Loose for everything else.** `dietary_restrictions: list[str]` (free text) is fine because Python normalizes at filter time. Don't type fields just because typing feels more rigorous.

### Sender identity

- **Phone OR LID against owner config → roster → else `unknown`.** Resolved by `identify-sender` (deterministic helper). Never use message content or WhatsApp profile name for identity.
- **`[shift-agent-sender v=1 ...]` block** is prepended to every inbound by Hermes' message hook. Parsed by `validate-sender-block` (also deterministic). The `fromMe` flag is informational only; owner routing is gated by `identify-sender role=owner`, not by `fromMe`.
- **`lid-cache.json`** maps phones to LIDs as Hermes resolves them; lid-learn cron applies new mappings to roster/config nightly.

### Testing pattern

- **Deterministic Python scripts get pytest.** Extend `tests/test_catering_v02_scripts.py` and similar. Subprocess-invoke the script with prepared state, assert on file mutations and stdout. Fast, free, deterministic.
- **SKILL.md interpretation gets observability + manual smoke.** The dispatcher SKILL is interpreted by Kimi at runtime; deterministic unit tests don't apply. Today's coverage: `dispatcher_routed` audit entry + `dispatcher-accuracy-report` Layer 0 monitor against `decisions.log`. A recorded-replay harness (Layer C) is a future addition once the corpus self-populates.
- **No real-LLM E2E suite today.** Cost ($0.10–0.50/run), non-determinism, and infrastructure (containerized Hermes) make it deferred. Layer 0 monitor + manual smoke is the current floor.

---

## Part 2 — Operational drift checklist

**Organizing principle: silent-failure first, loud-failure later.**

A bug that breaks loudly is a 4am page; you fix it and move on. A bug that breaks silently — bridge.js patch silently no-oping after a Hermes upgrade because a marker comment moved by one character — hurts customers for days before anyone knows to look. Critical-tier items below are ranked by silent-failure risk.

### Critical (silent-failure surface)

| Item | Failure mode | Cost | State | Target |
|---|---|---|---|---|
| Hermes commit hash pinned in `tools/hermes-patch-baseline.txt` + verified by `tools/check-shift-agent-patch.sh` as the first gate in `shift-agent-deploy.sh` | Hermes upgrade breaks our patches; first sign is broken customer behavior | done — `HERMES_COMMIT` + `HERMES_VERSION` + `BRIDGE_POST_PATCH_SHA256` pin, fail-closed gate, `HERMES_PIN_OVERRIDE=<new_hash> HERMES_PIN_OVERRIDE_REASON="..."` escape hatch | done | done — pin updates intentionally frequent (every reviewed Hermes commit produces a git diff = audit trail of due diligence) |
| `bridge.js` patch inventory with version markers + sha256 fingerprint | Upstream rename moves a marker comment by one character; patch silently no-ops; outbound chatter filter stops applying | done — covered by the same `check-shift-agent-patch.sh` gate (sha256 of post-patch bridge.js + marker presence + anchor proximity for both `shift-agent-sender-id` and `shift-agent-template-bypass` patches) | done | done |
| `deploy.sh` reconcile with actual VPS pattern | Script expects `/opt/shift-agent/working` to be a git checkout; VPS uses tarball/staging-new pattern. Today's deploys run by hand, leaving `deploy.sh` documentation-only | 1 day (decide: convert VPS to git checkout, OR rewrite deploy.sh for tarball pattern) | gap | before agent #6 build starts |
| PR-D1→PR-D2 rollback chain integrity | `shift-agent-deploy.sh` selects `PREV_TAG` via `ls -t` (most-recent mtime), NOT by soak duration. An intermediate deploy or `KEEP_TARBALLS=5` rotation between PR-D1 and PR-D2 would displace the shim-bearing tarball, leaving PR-D2 rollback to restore a pre-shim binary that ImportErrors on the new variants | done — `tools/check-pr-d2-rollback-target.sh` operator preflight refuses PR-D2 deploy unless PREV_SHA matches expected PR-D1 SHA; smoke-test rollback path now `rm -f`s the broken NEW_TAG tarball mirroring the pre-restart-gate eviction (R4-H2 fix) | done — gate is operator-driven defense-in-depth; making it non-bypassable would require deploy.sh refactor (see R4-H1 follow-up) |

### High (active gotcha source)

| Item | Failure mode | Cost | State | Target |
|---|---|---|---|---|
| Single canonical `.env` (or sync mechanism) | Two `.env` files (`/root/.hermes/.env` and `/opt/shift-agent/.env`) drift; placeholder API key in one cost hours of debugging on 2026-04-28 | 1–2h (decide: symlink, edit one loader, OR `make sync-env` step) | gap | this week |
| Audit log rotation policy | `decisions.log` was thought to need chain-preserving rotation; investigation 2026-04-28 revealed the chain was decoration (~3% coverage, no verifier). Logrotate already configured (daily, 30-day retention, archive to `/var/log/shift-agent-archive/`) — no chain to preserve | done — chain removed (Option B); deployed integrity = append-only via flock + `0640` perms + off-server backups; chokepoint pattern documented for future re-introduction | done | done — `safe_io.ndjson_append` is the chokepoint if/when compliance need emerges |

### Medium (technical debt)

| Item | Failure mode | Cost | State | Target |
|---|---|---|---|---|
| `docs/platform-contract.md` with semver | Each new agent author re-discovers the platform surface by reading existing code; no breaking-change discipline | 1 day (enumerate `src/platform/*.py` public functions + log-entry types + script exit codes; tag v0.1) | gap | before agent #6 |
| SKILL routing-rule conflict review pre-deploy | Two SKILLs with overlapping `description:` keywords cause Kimi to thrash between them; today caught only by post-mortem JSONL analysis | 2h (script that lists all SKILL descriptions + greps for routing-keyword overlap) | gap | nice-to-have |

### Low (already mitigated by recent work)

| Item | Failure mode | Mitigation | State |
|---|---|---|---|
| `dispatcher_routed` audit coverage | Routing reliability invisible | PR #14 schema + PR #15 reporter | done 2026-04-28 |
| Approval-code TTL per agent | Codes never expire if parent record stuck non-terminal | Acceptable as global config; per-agent TTL deferred until evidence it's needed | acceptable as-is |
| LF line endings on VPS-deployed scripts | CRLF shebang breaks `#!/usr/bin/env python3\r` | `.gitattributes` enforcement | done 2026-04-28 |

### Discipline for items added later

Each new item gets: description, **silent-vs-loud failure mode**, realistic time estimate (not "5-minute fix" unless it really is), current state, target date or explicit "deferred indefinitely, here's why."

The defer-with-reason cases are honest in a way that pure "pending" isn't. If an item sits in "pending" for >3 months without a target, that's a signal to either commit to it or move it to "deferred indefinitely."

---

## Part 3 — Working agreement

The structural fix for "drift via imported priors" is not the drift-check tag I proposed earlier. It's reading deployed code before proposing.

### The rule

**Before proposing schema, test, or architecture work, read the relevant deployed code.**

- Schema work → read `src/platform/schemas.py` (or grep for the specific model)
- Test work → read 1–2 existing test files, e.g. `tests/test_catering_v02_scripts.py` or `tests/test_schemas.py`
- Routing/dispatcher work → read `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` and at least one handler SKILL
- New script proposal → grep `src/platform/scripts/` and `src/agents/*/scripts/` for similar patterns first

This eliminates ~80% of corrections at zero new infrastructure cost. Most "drift" in this project's history has been a contributor (human or AI) importing a SaaS-style frame before grounding in this codebase's specific shape.

### Asymmetric workflow

The rule is the same in both directions, but operationalization differs:

- **Contributor with repo access (Claude Code, human dev):** read directly. ~1 second per `Read`/grep call. No excuse to skip.
- **External reviewer or remote agent (chat reviewer, ChatGPT, etc.):** ask the user to share relevant deployed code before drafting. ~30s round-trip; user can pre-emptively share files when starting an architectural thread to skip the round-trip.

In both cases the principle is "frame from deployed code, not from priors."

### Drift-check tag (secondary mechanism)

For proposals that genuinely extend or deviate from existing patterns, tag at the top:

- `Hermes-native` — uses Hermes primitives without modification
- `extends-Hermes` — adds custom infrastructure on top (most platform work falls here)
- `drifts-from-Hermes` — explicitly fights Hermes conventions; must explain operationally what compensating infrastructure exists

This is a self-disclosure mechanism, not a gate. It surfaces deviation at proposal time so reviewers can engage with the deviation explicitly. It does not replace the read-deployed-code rule.

---

## Part 4 — What this doc is NOT

- **Not Hermes philosophy.** Hermes is a runtime; it doesn't have a philosophy. This doc captures patterns *we* deployed on top of it. Don't quote bullets here as "Hermes prefers X" in future arguments.
- **Not prescriptive about future patterns.** When a new requirement doesn't fit cleanly into Part 1, the answer is to update Part 1 with the new pattern, not to refuse the requirement because it doesn't match.
- **Not a substitute for reading code.** Part 1 summarizes; deployed code is authoritative. If they conflict, deployed code wins and Part 1 is stale.
- **Not a replacement for code review.** Individual PRs still need review against actual changes. This doc raises the floor; review keeps the ceiling.
- **Not a dictate from outside.** The patterns here emerged from how this project actually evolved. Treat the doc as documentation, not policy.
- **Not done.** v1 captures 2026-04-28 state. Update as state changes.
