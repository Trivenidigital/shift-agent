# Credential-Minimized Hermes Mode Plan

**Drift-check tag:** `extends-Hermes`

**Date:** 2026-05-14

**Goal:** make the "no API key, no bot token where possible" Hermes operating model real for SMB-Agents by turning the market research into deployable guardrails, readiness tooling, and portfolio documentation.

**New primitives introduced:**

- `credential-minimized-readiness` operator/deploy check.
- A machine-readable credential capability matrix for the SMB agent portfolio.
- A smoke-test gate that verifies no-key Hermes foundation skills are installed.
- Updated roadmap/docs that promote MCP/vendor connectors before custom commercial API clients.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp customer/owner channel | yes - Hermes WhatsApp bridge via linked-device session | Use as default no-bot-token channel; treat session files as secrets. |
| Deterministic routing | yes - live `cf-router` plugin | Reuse for high-risk routing; do not add a new router. |
| Skills substrate | yes - project `SKILL.md` deployment is live | Keep custom business logic as Hermes skills where Hermes already owns orchestration. |
| OCR/document intake | yes - `productivity/ocr-and-documents` installed/enabled on `main-vps` | Use before custom PDF/OCR code. |
| Maps/routing/geocoding | yes - `productivity/maps` installed/enabled on `main-vps` | Use for no-key location/routing workflows; respect public OSM/Nominatim rate limits. |
| MCP integration substrate | yes - `mcp/native-mcp` installed/enabled on `main-vps` | Use as the first integration layer for QBO, Stripe, Square, PayPal, Airtable, Notion, DocuSign, etc. |
| Google/Airtable/Notion | yes - bundled skills installed/enabled on `main-vps` | Use only in connected mode; they still require OAuth/PAT. |
| Email fallback | yes - `email/himalaya` installed/enabled on `main-vps` | Optional channel; requires mailbox credentials. |
| QuickBooks | no Hermes-native SMB skill; Intuit QBO MCP exists | Prefer Intuit MCP + owner approval guardrails before custom raw QBO API. |
| Payments/POS | no credential-free Hermes skill; Stripe/Square/PayPal MCP servers exist | Prefer vendor MCP with restricted scopes before custom API; money-moving writes stay owner-gated. |
| Delivery marketplace/tax filing | no credible official skill/MCP found in this pass | Treat as integration-required; do not promise no-key automation. |

Awesome-Hermes-Agent ecosystem check: useful index, but no evidence it removes the SMB commercial API credential boundary. Self-Evolution Kit is useful later for skill/prompt improvement from traces, but it does not eliminate model or target-system credentials.

## Drift And Runtime Evidence

Read/verified before planning:

- `src/agents/shift/scripts/shift-agent-smoke-test.sh` - deploy smoke pattern and current skill/plugin gates.
- `src/agents/shift/scripts/shift-agent-deploy.sh` - tarball deploy and artifact install flow.
- `src/agents/multi_location/scripts/closest-location.py` - existing wrapper around Hermes `productivity/maps`.
- `tasks/skills-roadmap.md` - older May 3 market snapshot that needs updating.
- `docs/portfolio.md` - portfolio agent inventory and integration promises.
- Live `main-vps` Hermes inventory:
  - Builtin skills enabled: 82.
  - Required no-key foundation skills present: `productivity/maps`, `productivity/ocr-and-documents`, `mcp/native-mcp`.
  - Connected-mode skills present: `productivity/google-workspace`, `productivity/airtable`, `productivity/notion`, `email/himalaya`.
  - Plugins present/enabled: `cf-router`.
  - Current env presence by name only: `OPENROUTER_API_KEY=set`; `KIMI_API_KEY`, `AIRTABLE_API_KEY`, `NOTION_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `STRIPE_SECRET_KEY`, `SQUARE_ACCESS_TOKEN`, `QUICKBOOKS_CLIENT_ID` unset.

## Market Research Findings

The accurate target is **credential-minimized**, not credential-free:

- No bot token is realistic for WhatsApp-first deployments because Hermes can use a WhatsApp linked-device session. That session is still a sensitive credential.
- No business API key is realistic for local/manual workflows: WhatsApp intake, uploaded files, local JSON state, OCR, maps, timers, audit, and owner approvals.
- No LLM API key is possible only with local/self-hosted models or OAuth-backed providers. OAuth is still a credential, and current production still uses OpenRouter.
- Fully autonomous writeback to QBO/POS/payment/tax/delivery systems requires OAuth, PAT, API key, or a managed connector.

### High-Signal Connector Landscape

| Domain | Best available candidate | Credential shape | Fit for SMB-Agents |
|---|---|---|---|
| QuickBooks Online | Intuit QuickBooks Online MCP server | QBO OAuth app/client credentials + realm | Strong for Agent #21/#22; writes require approval guardrails. |
| Stripe | Stripe MCP | Remote OAuth or restricted secret key | Strong for Cash/AR and payment links; restrict tools/scopes. |
| Square | Square MCP server | OAuth or access token | Strong for POS/catalog/orders/inventory if customer uses Square. |
| Clover | Clover API / community MCP candidates such as `clovercli` | Clover OAuth app/client credentials | Candidate for EOD/POS workflows when the customer uses Clover; verify source quality before install. |
| Toast/Shopify/WooCommerce | customer-POS-specific MCP/API search required | OAuth/app credentials | Triage by actual customer POS before scoping custom code. |
| PayPal | PayPal MCP server | OAuth or access token | Strong for invoices/orders/refunds where PayPal is used. |
| Airtable | Official Airtable MCP/server + Hermes skill | OAuth or PAT | Good for lightweight SKU/P&L/customer tables. |
| Notion | Official Notion MCP/server + Hermes skill | OAuth preferred; token for headless | Good for docs/checklists; not a system of record for money. |
| Google Workspace | Hermes skill or community MCP | Google OAuth | Good for Sheets/Drive/Calendar; connected mode only. |
| DocuSign | DocuSign MCP connector | DocuSign account/OAuth/client config | Good for e-sign/onboarding once guardrails exist. |
| Infobip | Infobip remote MCP servers | OAuth 2.1 or API key | Strong broad messaging fallback; not no-token. |
| Pipedream | Pipedream MCP | Pipedream-managed OAuth | Broad fallback when no vendor MCP exists; adds platform dependency. |
| Yelp | Yelp MCP | Yelp Fusion AI API key | Useful for review intelligence; not Google Business Profile. |
| Maps/geocoding | Hermes `productivity/maps` using OSM/Nominatim/OSRM | none for low volume public endpoints | Good no-key default; rate-limit and cache. |
| Connected maps/places | Google Maps Grounding Lite MCP | Google Cloud project with billing plus API key or OAuth | Connected-mode option for high-quality places/location research; not no-key. |
| Local LLM | Ollama/llama.cpp/vLLM/LocalAI | none if local endpoint only | Separate reliability project; hardcoded OpenRouter paths must be found first. |
| OCR/doc extraction | Hermes `ocr-and-documents`, Tesseract, Tika, PaddleOCR | none/local | Good no-key intake path before cloud OCR. |
| DoorDash/UberEats/Grubhub | no credible vendor-official MCP found | partner APIs/iPaaS likely | Keep as connected/custom later. |
| Tax filing | no official maintained MCP found | state/provider-specific credentials | Keep as reminder/checklist unless customer authorizes filing integration. |

### Sources To Preserve In Docs

- Hermes bundled skills catalog: https://hermes-agent.nousresearch.com/docs/reference/skills-catalog
- Hermes optional skills catalog: https://hermes-agent.nousresearch.com/docs/reference/optional-skills-catalog/
- Hermes skill creation docs: https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills
- Intuit QBO MCP: https://github.com/intuit/quickbooks-online-mcp-server
- Stripe MCP docs: https://docs.stripe.com/mcp
- Stripe MCP repo registry entry: https://github.com/mcp/com.stripe/mcp
- Square MCP: https://github.com/square/square-mcp-server
- PayPal MCP: https://github.com/paypal/paypal-mcp-server
- Airtable MCP docs: https://support.airtable.com/docs/using-the-airtable-mcp-server
- Notion MCP docs: https://developers.notion.com/docs/get-started-with-mcp
- DocuSign MCP guide: https://www.docusign.com/blog/claude-docusign-mcp-connector-guide
- Pipedream MCP: https://pipedream.com/docs/connect/mcp/
- Infobip MCP: https://www.infobip.com/docs/mcp
- Nominatim usage policy: https://operations.osmfoundation.org/policies/nominatim/
- Ollama OpenAI compatibility: https://docs.ollama.com/api/openai-compatibility
- llama.cpp multimodal docs: https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- vLLM OpenAI-compatible server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server/
- Apache Tika formats/OCR: https://tika.apache.org/3.2.2/formats.html
- PaddleOCR docs: https://www.paddleocr.ai/main/en/index/index.html
- Awesome Hermes Agent: https://github.com/0xNyk/awesome-hermes-agent
- Hermes Self-Evolution Kit: https://github.com/NousResearch/hermes-agent-self-evolution

## Proposed Build Slice

### Phase 1 - Plan/Design Artifacts

- Update this plan with reviewer feedback.
- Write a design spec that nails file layout, CLI contract, deploy integration, and test coverage.
- Update the existing `tasks/hermes-no-key-no-token-analysis-2026-05-14.md` so it no longer claims installed built-in skills are absent.

### Phase 2 - Machine-Readable Capability Matrix

Add a repo-owned data source that maps every portfolio agent to:

- useful no-key mode,
- manual-export mode,
- connected mode,
- Hermes skills/plugins to check first,
- market connector candidates,
- credential class,
- `last_verified`,
- source URL,
- connector maturity (`official`, `vendor`, `community`, `beta`, `unknown`),
- deployment status (`installed`, `available`, `candidate`, `avoid`, `not_found`),
- owner-approval requirements,
- no-go claims.

Preferred implementation after design review: a Python module under `src/platform/` consumed by a CLI. This keeps the matrix in the existing deploy tarball/install path. Avoid schema churn unless reviewers find a strong reason.

### Phase 3 - Readiness CLI

Add `credential-minimized-readiness` with two outputs:

- Human text for operators.
- JSON for tests/deploy tooling.

It must:

- Detect local/live install roots without printing secret values.
- Define canonical skill roots explicitly:
  - `/root/.hermes/skills/<category>/<name>/SKILL.md` for installed/enabled Hermes skills on the live VPS.
  - `/usr/local/lib/hermes-agent/skills/<category>/<name>/SKILL.md` as the bundled fallback root for official Hermes skills.
  - repo-local `src/agents/**/skills/<name>/SKILL.md` only for local/dev reporting.
- Verify no-key foundation skills: `productivity/maps`, `productivity/ocr-and-documents`, `mcp/native-mcp`.
- Report project plugin baseline separately: `cf-router` directory exists, Python modules compile/import read-only, `/root/.hermes/config.yaml` lists it under `plugins.enabled`, and `plugins.disabled` does not include it.
- Report credential presence by class/name/status only: set/unset/muted/placeholder. Never emit value, path, basename, prefix, or sample.
- Report WhatsApp channel readiness separately from skill readiness. A disconnected bridge is not a missing-skill failure, but a WhatsApp-first deployment must not be called green while the bridge is disconnected.
- Summarize per-agent readiness: no-key-ready, manual-export, connected-required.
- Exit non-zero in strict foundation mode only for missing external no-key foundation requirements. `cf-router` failures exit non-zero only when the caller passes `--validate-plugin cf-router`, because deploy installs that repo plugin before the pre-restart check.

This CLI is additive. It must not replace or weaken existing runtime-critical gates such as `vision-auth-smoke`, Pushover/alert checks, config validation, env symlink integrity, or bridge health checks.

### Phase 4 - Deploy Integration

Wire a strict foundation readiness gate into deploy before app artifacts are installed and before `hermes-gateway` is restarted. Missing foundation skills are external Hermes install state; app rollback cannot repair them, so this gate must abort with no state change rather than trip post-restart rollback.

Keep the post-restart smoke test behavior-focused. It may call the readiness CLI in non-strict/report mode, but it must not be the first strict check for external Hermes install state.

Strict foundation failures:

- Missing `productivity/maps`
- Missing `productivity/ocr-and-documents`
- Missing `mcp/native-mcp`
- `cf-router` is handled by the separate post-install/pre-restart plugin gate, including enabled/disabled drift.

Connected-mode missing credentials should be reported as informational, not deploy-blocking. Existing required runtime credentials remain governed by their existing fail-closed checks.

### Phase 5 - Docs/Roadmap Refresh

Update:

- `tasks/skills-roadmap.md`: replace stale "no QBO/Stripe/Square/PayPal/DocuSign path" claims with current MCP/vendor-connector findings.
- `tasks/hermes-no-key-no-token-analysis-2026-05-14.md`: align with live VPS inventory and market research.
- `docs/portfolio.md`: add a concise credential-mode note where agents currently imply full automation against external systems.
- `tasks/todo.md`: track completed/reviewed status.

## What This Plan Will Not Do

- It will not install third-party community skills blindly.
- It will not configure QBO, Stripe, Square, PayPal, Google, Airtable, Notion, DocuSign, Infobip, or Pipedream credentials.
- It will not remove the current OpenRouter key or switch production models.
- It will not downgrade the existing OpenRouter vision-auth deploy gate; local/OAuth model mode is a separate verified project.
- It will not enable external money-moving/legal/tax writes.
- It will not promise "no credentials" where a WhatsApp session or OAuth token is actually required.

## Review Plan

Plan review, 2 parallel agents:

- Hermes/market coverage reviewer: checks whether the plan missed existing Hermes skills/plugins/MCP candidates or overstates net-new work.
- Runtime/deploy/security reviewer: checks secret handling, smoke-gate blast radius, and operational safety.

Design review, 2 parallel agents:

- Code/schema/test reviewer: checks file layout, CLI contract, tests, and deploy integration.
- Product/ops reviewer: checks operator usefulness, claims discipline, and source-backed market research.

Implementation PR review, 3 parallel agents:

- Code/test reviewer.
- Deploy/runtime/security reviewer.
- Hermes-first/market-research reviewer.

## Acceptance Criteria

- Plan and design carry drift tag plus Hermes-first section.
- Market research is captured with links and current May 14, 2026 vendor/MCP status.
- A deterministic CLI reports readiness without leaking secrets.
- Deploy aborts before install/restart if no-key Hermes foundation skills disappear.
- Existing runtime-critical credential gates remain fail-closed.
- Existing tests pass, plus new focused tests for the CLI/matrix.
- PR is reviewed, fixed, merged, and deployed to `main-vps` using the tarball path.
