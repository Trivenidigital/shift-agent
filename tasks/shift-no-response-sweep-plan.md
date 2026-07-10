# Plan — Shift no-response escalation sweep

**Drift-check tag:** `extends-Hermes` — adds an ops timer + stale-detection on top of the existing
proposal state machine, reusing its transition/alert/audit chokepoints. Fights no convention.

**Authorization:** overnight autonomous build (operator "start the next steps as per this prompt").
First build slice from the owner-experience review (PR #586). Envelope: TDD → PR, **no merge/deploy**;
ships behind a config flag default-OFF (owner enables per-customer after review).

**Origin (owner-experience review, Shift #1 finding):** a `sent` coverage proposal whose candidate
never replies **never transitions or escalates** — the machinery exists (`update-proposal-status`
supports `no_response_timeout` + `--actor timer`; `LEGAL_TRANSITIONS["sent"]` includes it) but nothing
invokes it. Owner approves → candidate never sees WhatsApp → proposal sits `sent` forever → shift
silently uncovered, no alert — while the copy promised *"I'll let you know when they respond."* This is
the §12a/§12b silent-failure class. This slice gives that stale state an owner.

## Hermes-first analysis
| Step | Hermes provides? | Tag |
|---|---|---|
| Read per-VPS `pending.json` state | Yes — `safe_io` / per-VPS JSON | `[Hermes]` |
| Transition `sent`→`no_response_timeout` | Yes — existing `update-proposal-status` chokepoint (tested, `--actor timer`) | `[Hermes]` reuse |
| Owner alert | Yes — existing `shift-agent-notify-owner` | `[Hermes]` reuse |
| Audit row | Yes — `ProposalStatusChange` via the transition chokepoint + structured logs | `[Hermes]` reuse |
| Periodic trigger (systemd timer) | No — our ops infra | `[net-new]` |
| Stale-detection logic | No Hermes primitive | `[net-new]` |
| Config-flag gate | Our `LimitsConfig` | `[net-new]` |

**Ecosystem check:** no Hermes/MCP primitive for "sweep my own pending records for a stale state and
escalate." The transition + alert + audit + the `no_response_timeout` state ALL already exist; only the
scheduled trigger + detection are missing. Receipt: `shift-no-response-sweep.json` (net_new=4, hermes=4).

## Drift-rule self-checks (deployed code Read before drafting)
- ✅ Read `src/agents/shift/scripts/update-proposal-status` (transition chokepoint: `--actor timer`
  allowed line 86; `no_response_timeout` sets `timeout_ts` line 156-157; enforces `is_legal_transition`)
  before designing the sweep's transition call.
- ✅ Read `src/platform/schemas.py` (`LimitsConfig:290` `extra="forbid"` + `pending_proposal_ttl_hours`;
  `SentProposal:3134` has `sent_ts`; `LEGAL_TRANSITIONS:3200` `sent`→`no_response_timeout` is LEGAL;
  `no_response_timeout` terminal) before placing the config fields + the transition.
- ✅ Read `src/agents/shift/scripts/shift-agent-health-watchdog.sh` + `.service`/`.timer` (oneshot
  User=shift-agent + timer pattern) before drafting the sweep's units.

## Design
- `src/platform/proposal_sweep.py` — **stdlib-only** `find_stale_sent_proposals(proposals, now,
  ttl_minutes)` (pure; duck-typed on `.status`/`.sent_ts`; cross-platform unit-testable).
- `src/agents/shift/scripts/shift-agent-proposal-sweep` — python CLI: load config → **gate on
  `no_response_sweep_enabled` (default OFF → exit 0)** → load `pending.json` → find stale → for each,
  `subprocess update-proposal-status <id> no_response_timeout --cause no_response_sweep --actor timer`
  (rc 0 → alert owner + dispatched/delivered logs; rc 9 illegal = candidate already replied → skip;
  else log). TOCTOU-safe: the chokepoint re-checks legality under lock.
- `src/agents/shift/systemd/shift-agent-proposal-sweep.{service,timer}` — oneshot User=shift-agent,
  OnUnitActiveSec=900. Timer ENABLED at deploy (harmless no-op while flag OFF); feature gated by config.
- `LimitsConfig` += `no_response_sweep_enabled: bool = False`, `candidate_response_ttl_minutes: int = 30`
  (additive, defaults → backward-compatible under `extra="forbid"`); document in `config.yaml.template`.
- Deploy: script + units install via existing wildcards; add timer enable; document the one-line flag flip.

**Alert copy (honest — no false promise):** "{candidate} hasn't replied to the coverage request for
{date} {shift} ({role}) in {N} min. The shift for {absent} is still uncovered — please arrange coverage."
Does NOT promise a "reply NEXT" command that doesn't exist yet (that would repeat the sin the review flagged).

## Task checklist
- [ ] T1 — `proposal_sweep.py` + `tests/test_proposal_sweep.py` (TDD red→green)
- [ ] T2 — `LimitsConfig` fields + config.yaml.template + backward-compat test
- [ ] T3 — `shift-agent-proposal-sweep` script (gate → detect → transition → alert)
- [ ] T4 — `.service` + `.timer`; deploy.sh timer-enable + flag-flip doc
- [ ] T5 — invariant tests (config defaults; script references flag + `--actor timer` + no_response_timeout)
- [ ] T6 — full `pytest` green; multi-vector review (state-machine + owner-facing); PR (no merge/deploy)

## Out of scope (documented)
- `awaiting_owner_approval` 4h expiry (the second false promise) — same sweep pattern, fast-follow.
- `NEXT #CODE` next-candidate owner command (medium-term roadmap).
- Auto-messaging a next candidate (never without owner approval).
- Enabling the flag / deploying (operator-gated).
