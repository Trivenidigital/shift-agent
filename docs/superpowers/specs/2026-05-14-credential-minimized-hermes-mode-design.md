# Credential-Minimized Hermes Mode Design

**Drift-check tag:** `extends-Hermes`

**Status:** Draft after plan review fixes. Awaiting two design reviews.

## Goal

Make credential-minimized Hermes mode an enforceable repo/runtime posture:

- WhatsApp-first operation with no platform bot token.
- No business API credentials required for local/manual workflows.
- Clear connected-mode boundaries for QBO, POS, payments, Google, Airtable,
  Notion, DocuSign, Infobip, and similar systems.
- A deploy-time gate that catches missing no-key Hermes foundation skills
  before app code is installed or services are restarted.
- Operator-facing readiness output that never prints secret values.

This is not a claim that the system is credential-free. WhatsApp sessions,
OAuth refresh tokens, model keys, and provider tokens remain credentials.

## New Primitives Introduced

- `src/platform/credential_readiness.py` - deployable, importable capability
  matrix and readiness helpers.
- `src/platform/scripts/credential-minimized-readiness` - human/JSON CLI.
- Pre-install deploy gate in `shift-agent-deploy.sh` that runs the staging CLI
  in strict foundation mode.
- Staged/pre-restart `cf-router` validation remains separate because
  `cf-router` is repo-installed, not external Hermes install state.
- Optional post-restart smoke/report call in non-strict mode.
- Updated market/docs artifacts:
  - `tasks/hermes-no-key-no-token-analysis-2026-05-14.md`
  - `tasks/skills-roadmap.md`
  - `docs/portfolio.md`
  - `tasks/todo.md`

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp messaging | yes - Hermes WhatsApp linked-device bridge | Default no-bot-token channel; treat session as credential. |
| No-key maps | yes - `productivity/maps` installed/enabled live | Gate as a foundation skill. |
| No-key OCR/docs | yes - `productivity/ocr-and-documents` installed/enabled live | Gate as a foundation skill. |
| Integration substrate | yes - `mcp/native-mcp` installed/enabled live | Gate as a foundation skill; use before raw custom API clients. |
| Deterministic routing | yes - project `cf-router` plugin installed/enabled live | Validate after repo plugin install and before gateway restart. |
| Google/Airtable/Notion | yes - bundled skills installed/enabled live | Report connected-mode readiness only; do not fail deploy when OAuth/PAT is unset. |
| Email fallback | yes - `email/himalaya` installed/enabled live | Report connected-mode readiness only. |
| QBO | no Hermes-native skill; Intuit MCP exists | Matrix must prefer Intuit MCP before custom QBO API. |
| Payments/POS | no credential-free Hermes skill; Stripe/Square/PayPal/Clover candidates exist | Matrix must require customer POS/payment triage and owner approval before writes. |
| Delivery/tax | no credible official credential-free coverage found | Matrix marks as connected/custom; no no-key autonomy claims. |

Awesome-Hermes-Agent ecosystem verdict: useful index, no drop-in SMB credential
elimination. Self-Evolution Kit verdict: useful later for skill optimization,
not a credential-removal mechanism.

## Drift Checks Performed

Read before design:

- `src/agents/shift/scripts/shift-agent-smoke-test.sh`: current post-restart
  smoke behavior, plugin compile check, OpenRouter/Pushover gates.
- `src/agents/shift/scripts/shift-agent-deploy.sh`: tarball deploy flow and
  pre/post restart gates.
- `tools/build-deploy-tarball.sh`: deploy artifact includes `src/`, `tools/`,
  and `.commit-hash`.
- `src/agents/multi_location/scripts/closest-location.py`: current bundled
  `productivity/maps` root.
- `src/platform/scripts/dispatcher-accuracy-report` and tests: local module
  loading style for extensionless Python CLIs.
- `tasks/skills-roadmap.md`: stale May 3 market baseline to refresh.
- `docs/portfolio.md`: current external-integration promises.
- Live `main-vps` inventory: 82 builtin Hermes skills enabled, required
  foundation skills present, `cf-router` enabled, external SaaS credentials
  unset except current `OPENROUTER_API_KEY`.

## Data Model

Implement the matrix as Python constants in `src/platform/credential_readiness.py`
rather than YAML/JSON. Reasons:

- It is included in the existing tarball because `src/` is already packaged.
- It can be installed beside existing platform modules in `/opt/shift-agent`.
- Tests can import it without adding a YAML parser dependency to the strict
  pre-install gate.
- It avoids schema churn in `schemas.py`; this is operator metadata, not
  runtime state.

### Core Types

Use dataclasses or plain typed dictionaries. Keep the module dependency-light:
stdlib only.

Definitions:

```python
FOUNDATION_SKILLS = (
    SkillRequirement(
        skill_id="productivity/maps",
        category="productivity",
        name="maps",
        credential_class="none/local",
        last_verified="2026-05-14",
        source_url="https://hermes-agent.nousresearch.com/docs/reference/skills-catalog",
    ),
    ...
)
```

Connector candidates should include:

- `name`
- `domain`
- `source_url`
- `credential_class`: `none/local`, `session`, `oauth`, `pat`, `api_key`,
  `managed_oauth`, `write_rail`
- `maturity`: `official`, `vendor`, `community`, `beta`, `unknown`
- `market_state`: `stable`, `beta`, `preview`, `requires_allowlist`,
  `tooling_may_change`, `unmaintained`, `unknown`
- `auth_modes`: list such as `remote_oauth`, `local_oauth`,
  `restricted_api_key`, `pat`, `session`, `manual_export`, `none`
- `deployment_status`: `installed`, `available`, `candidate`, `avoid`,
  `not_found`
- `last_verified`
- `freshness_days`: default 30 for connector candidates, 90 for official
  Hermes bundled skills, 14 for beta/preview/write-rail connectors
- `notes`

Agent capability rows should include:

- `agent_id`
- `agent_name`
- `default_mode`: `no_key_ready`, `manual_export`, `connected_required`,
  `retired_or_folded`
- `useful_no_key_mode`
- `manual_export_mode`
- `connected_mode`
- `hermes_first_skills`
- `project_skills`
- `connector_candidates`
- `credential_boundary`
- `owner_approval_required`
- `no_go_claims`

Every connector row must carry `last_verified` and `source_url` so the matrix
does not silently go stale like the older roadmap did.

Freshness rule:

- `fresh` when `today - last_verified <= freshness_days`
- `stale` when beyond the freshness window
- strict foundation mode does not fail on stale market candidates, because they
  do not affect deploy safety
- JSON/text output must show stale connector claims prominently
- tests must fail if any matrix row lacks `last_verified`, `source_url`, or
  `freshness_days`

Before designing/building an agent that depends on a stale connector row, the
operator must refresh that row's market research. The readiness CLI provides
the stale list; it is not a web crawler.

## CLI Contract

Command:

```bash
credential-minimized-readiness [--format text|json] [--strict-foundation]
  [--hermes-home PATH] [--hermes-install-root PATH]
  [--repo-root PATH] [--env PATH ...] [--config PATH]
  [--check-bridge] [--bridge-url URL] [--validate-plugin NAME]
```

Defaults:

- `--format text`
- `--hermes-home /root/.hermes`
- `--hermes-install-root /usr/local/lib/hermes-agent`
- `--repo-root` inferred from current checkout when local, otherwise absent
- `--env /root/.hermes/.env --env /opt/shift-agent/.env`
- `--config /root/.hermes/config.yaml`
- `--bridge-url http://127.0.0.1:3000/health`
- no bridge probe unless `--check-bridge` is passed

Exit codes:

- `0`: report rendered; strict foundation checks passed or strict not enabled.
- `1`: strict foundation failure.
- `2`: invalid arguments or unreadable required input in strict mode.

`--validate-plugin cf-router` validates repo-installed plugin state after
install. It can be combined with `--format json|text`. It is not implied by
`--strict-foundation`.

### Skill Roots

Resolve slash skill IDs such as `productivity/maps` and `mcp/native-mcp` in
this order:

1. Live installed root:
   `/root/.hermes/skills/<category>/<name>/SKILL.md`
2. Bundled root:
   `/usr/local/lib/hermes-agent/skills/<category>/<name>/SKILL.md`
3. Local dev root, report-only:
   `src/agents/**/skills/<name>/SKILL.md` or
   `src/**/skills/<category>/<name>/SKILL.md` if such a tree exists later

Strict foundation passes if either live installed root or bundled root exists.
Local dev roots are never enough for live strict mode.

### Plugin Check

Installed/live `cf-router` is green only when all are true:

- `/root/.hermes/plugins/cf-router` exists
- `actions.py` and `hooks.py` compile
- `/root/.hermes/config.yaml` contains `plugins.enabled` with `cf-router`

If config is unreadable in non-strict mode, return `unknown` with detail. In
strict mode, unreadable config is exit `2`, because enabled/disabled cannot be
determined.

Use a tiny YAML extractor for `plugins.enabled` or PyYAML if available. The
strict path should not require Pydantic. If PyYAML is unavailable, parse the
small `plugins: enabled:` shape conservatively; tests must cover this fallback.

### Credential Redaction

Credential reporting is by name and status only:

```json
{"name":"OPENROUTER_API_KEY","class":"api_key","status":"set"}
```

Allowed statuses:

- `set`
- `unset`
- `muted`
- `placeholder`
- `env_present`
- `oauth_session_present`
- `candidate_only`
- `not_probed`
- `unknown`

Never emit:

- secret values
- file paths from credential values
- basenames
- prefixes
- suffixes
- sample characters

This applies to both text and JSON. The CLI may emit the env file paths it
checked only if they are explicit CLI arguments, not values read from the env.

Connected-mode readiness is deliberately conservative. An env var being set
means only `env_present`; it does not prove the connector works. Hosted OAuth
or MCP sessions are `not_probed` unless the design later adds a connector-
specific probe. Candidate connectors with no configured local credential are
`candidate_only`, not `unset`.

### Runtime Channel Reporting

The CLI should report WhatsApp readiness separately:

- `not_checked` unless `--check-bridge`
- `connected` if bridge `/health` returns status `connected`
- `disconnected` with sanitized reason otherwise

`--strict-foundation` does not fail on a disconnected bridge because that is
runtime service state, not foundation skill/plugin install state. Existing
`shift-agent-health-check.sh` remains the authoritative runtime bridge health
monitor. The readiness report must not print a global green "WhatsApp-first
ready" if the bridge status is `disconnected`.

## Deploy Integration

### Install Contract

In `install_artifacts()`:

- install `src/platform/credential_readiness.py` to
  `/opt/shift-agent/credential_readiness.py` only if the source file exists,
  preserving rollback compatibility with older tarballs
- `src/platform/scripts/*` is already installed to `/usr/local/bin/`, so the
  CLI arrives through the existing script glob

### Pre-Install Gate

In `shift-agent-deploy.sh` deploy action:

1. Define `VENV_PY=/usr/local/lib/hermes-agent/venv/bin/python` before any
   Python gate.
2. After Hermes pin gate and before state-file migration or artifact install,
   run:

```bash
if [ -x "$STAGING/src/platform/scripts/credential-minimized-readiness" ]; then
    "$VENV_PY" "$STAGING/src/platform/scripts/credential-minimized-readiness" \
      --strict-foundation --format text
else
    echo "WARN: credential-minimized-readiness absent from staging - skipping foundation gate (pre-feature rollback compatibility)" >&2
fi
```

This is external Hermes install-state verification. It checks only external
Hermes foundation skills: `productivity/maps`, `productivity/ocr-and-documents`,
and `mcp/native-mcp`. It must happen before app artifacts are installed and
before `hermes-gateway` restart, so missing external foundation skills abort
the deploy rather than triggering rollback.

Do not include repo-installed `cf-router` in the pre-install strict foundation
failure set. A deploy can repair a missing or stale repo plugin because
`install_artifacts()` rsyncs `src/plugins/` to `/root/.hermes/plugins/`.

### Staged/Pre-Restart Plugin Gate

Keep or extend the existing pre-restart plugin compile gate after
`install_artifacts()`, when the staged `cf-router` has been installed but
before `hermes-gateway` restarts:

- compile `/root/.hermes/plugins/cf-router/actions.py`
- compile `/root/.hermes/plugins/cf-router/hooks.py`
- verify `/root/.hermes/config.yaml` lists `cf-router` under
  `plugins.enabled`

This is the correct place for strict `cf-router` validation because it checks
the code/config that will actually be loaded on restart.

The deploy script may use the existing inline compile gate plus the readiness
CLI's `--validate-plugin cf-router`, or fold config-enabled validation into the
existing gate. Either way, plugin validation occurs after `install_artifacts()`.

### Staged CLI Imports

The CLI must work before installation. At script startup, it should insert
candidate module paths in this order:

1. `Path(__file__).resolve().parents[1]` when running from
   `$STAGING/src/platform/scripts/credential-minimized-readiness`
2. `/opt/shift-agent` for installed execution
3. repo `src/platform` when tests run from a checkout

Tests must execute the staging script through `sys.executable` with
`/opt/shift-agent/credential_readiness.py` absent, proving the pre-install gate
uses staged code rather than stale installed code.

### Post-Restart Smoke

Add a non-strict/report call inside `shift-agent-smoke-test.sh` near the
existing smoke checks if the installed CLI exists:

```bash
if [ -x /usr/local/bin/credential-minimized-readiness ]; then
    "$PY" /usr/local/bin/credential-minimized-readiness --format text || true
fi
```

This is informational only. Do not use post-restart smoke rollback as the first
strict check for missing foundation skills.

Existing gates stay authoritative:

- env symlink integrity
- config validation
- `vision-auth-smoke`
- Pushover/alert checks
- bridge runtime health checks
- systemd checks

The readiness gate must not downgrade any current production hard dependency.

## Docs/Roadmap Updates

Update the current analysis and roadmap to say:

- `main-vps` already has the bundled no-key foundation skills installed and
  enabled.
- Connected-mode credentials are currently unset by design.
- QBO/Stripe/Square/PayPal/DocuSign now have credible MCP/vendor connector
  candidates; old "no connector path" language is stale.
- The new default is vendor MCP or vetted MCP first, custom raw API second.
- Clover/POS should be triaged by the customer's actual POS, not assumed.
- Google Maps Grounding Lite MCP is a connected-mode option, not a no-key
  replacement for `productivity/maps`.
- DoorDash/UberEats/Grubhub and tax filing remain connected/custom surfaces.
- Zelle, Cash App, Venmo, Razorpay, bank reconciliation, payroll/time-clock,
  e-verify/background checks, supplier portals/EDI, Google Business Profile,
  Facebook reviews, and delivery marketplace surfaces are tracked as
  candidate/custom integration categories in the matrix even if not fully
  researched in this slice.

Surgically amend stale `docs/portfolio.md` paragraphs that currently state or
imply custom raw API work is the only path. Do not rewrite the whole portfolio,
but do update concrete stale claims for Agent #21 QBO, Agent #15 payments/POS,
DocuSign/e-sign, and review APIs so they say "MCP/vendor connector first,
custom raw API only after connector review fails."

## Error Handling

- Missing foundation skill in strict mode: exit `1` with a clear missing item
  list.
- Missing `cf-router` config enablement in live/plugin validation mode: exit
  `1`; it is not part of external foundation strict mode.
- Unreadable Hermes config in strict mode: exit `2`.
- Missing env files: report credentials as `unknown` or `unset`; do not fail
  unless a future explicit required-runtime credential gate uses the CLI.
- Bridge probe failure: sanitized disconnected status; no strict failure.
- Bad JSON/text output path: exit `2`.

## Test Plan

Focused unit tests:

- slash skill IDs resolve to live installed and bundled roots
- local dev roots do not satisfy strict live foundation mode
- missing foundation skill makes strict result fail
- missing/stale live `cf-router` does not fail external foundation strict mode
  before install
- staged/pre-restart plugin validation catches missing/disabled `cf-router`
- connected-mode credentials unset are informational and do not fail strict
- candidate connector rows report `candidate_only`/`not_probed`, not false
  `unset`
- env parser reports only statuses, never values, paths, basenames, prefixes,
  or suffixes
- muted/placeholder statuses are detected without printing the underlying value
- `cf-router` is green only when directory exists, modules compile, and config
  enables it
- config parser works with PyYAML and with the fallback parser
- JSON output is stable and contains `foundation`, `plugins`, `credentials`,
  `agents`, `connectors`, and `whatsapp`
- text output contains no secret values from fixture env files
- `--check-bridge` reports connected/disconnected without leaking exception
  internals

Subprocess tests:

- `credential-minimized-readiness --format json` exits `0` on complete fixture
- `credential-minimized-readiness --strict-foundation` exits `1` on missing
  `mcp/native-mcp`
- `credential-minimized-readiness --validate-plugin cf-router` exits `2` on
  unreadable config needed for plugin enablement validation

Static/deploy tests:

- deploy script invokes the staging CLI before state-file migration,
  `install_artifacts`, and `systemctl restart hermes-gateway`
- staging CLI subprocess tests use `sys.executable`, not direct execution, so
  they pass on Windows
- deploy install contract is rollback-compatible when
  `src/platform/credential_readiness.py` is absent
- smoke script informational call is non-strict and cannot trigger rollback

Docs tests/invariants:

- `tasks/skills-roadmap.md` no longer claims there is no QBO/Stripe/Square/
  PayPal/DocuSign connector candidate
- no-key analysis does not claim installed foundation skills are absent
- matrix rows carry `last_verified`, source URL, and `freshness_days`
- stale connector rows are surfaced in CLI output

Verification commands:

```bash
python -m pytest tests/test_credential_readiness.py tests/test_repo_invariants.py -q
python -m py_compile src/platform/credential_readiness.py
bash -n src/agents/shift/scripts/shift-agent-deploy.sh
bash -n src/agents/shift/scripts/shift-agent-smoke-test.sh
```

Run broader tests if focused tests pass and before tarball deploy.

## Rollout Plan

1. Implement tests first.
2. Add `src/platform/credential_readiness.py`.
3. Add `src/platform/scripts/credential-minimized-readiness` with staged import
   path.
4. Wire deploy pre-install strict external-foundation gate.
5. Keep/extend pre-restart `cf-router` validation after plugin install.
6. Add optional non-strict smoke/report call inside `shift-agent-smoke-test.sh`.
7. Update docs/roadmap/analysis.
8. Run focused tests.
9. Commit, push branch, create PR.
10. Run three implementation reviewers.
11. Fix findings and rerun tests.
12. Merge and deploy via tarball.
13. On `main-vps`, run:

```bash
/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/credential-minimized-readiness --format json
/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/credential-minimized-readiness --strict-foundation
```

14. Verify gateway active and bridge health remains connected.

## Rollback

If the new readiness gate falsely blocks deploy:

- Do not disable existing runtime-critical gates.
- Inspect missing item output and verify live Hermes install roots.
- If false positive is in path resolution, fix the CLI and redeploy.
- If an emergency rollback to a pre-feature tarball is needed, the deploy script
  warns/skips when the staging CLI is absent for rollback compatibility.

## Non-Goals

- No credential provisioning.
- No third-party skill/plugin installation.
- No QBO/Stripe/Square/PayPal/DocuSign write enablement.
- No local model migration.
- No new runtime state file or DB table.
- No customer-facing behavior change.
