# Hermes No-Key / No-Bot-Token Portfolio Analysis

**Drift-check tag:** `Hermes-native`

**Date:** 2026-05-14

## Question

Can SMB-Agents reach the level of:

> No API key, no bot token, just running with the skills and plugins adapted to your needs.

## Short Answer

Partially, and the distinction matters:

- **No bot token:** yes for a WhatsApp-first deployment, because Hermes uses the Baileys WhatsApp Web bridge rather than a Telegram/Slack/Discord bot token. This still creates a sensitive WhatsApp session credential under `~/.hermes/platforms/whatsapp/session`; it is not "no credential."
- **No business API keys:** yes for local-file, WhatsApp-media, OCR/document, cron, audit-log, owner-approval, and public-map workflows. This covers a large MVP surface.
- **No LLM API key:** possible only by switching from OpenRouter to OAuth-based providers or a local/self-hosted model endpoint. Current `main-vps` uses `OPENROUTER_API_KEY`.
- **No credentials at all:** no. Any durable remote messaging account, OAuth SaaS integration, payment rail, POS system, QuickBooks, tax portal, or delivery marketplace needs credentials or a browser/session equivalent.

The practical target should be **credential-minimized Hermes**, not credential-free Hermes.

## Current Live VPS Evidence

Live inventory from `main-vps`:

- Installed project skills: 31, including `dispatch_shift_agent`, `handle_sick_call`, catering skills, `multi_location_query`, `compliance_owner_query`, `expense_bookkeeper_dispatcher`, `pnl_anomaly_dispatcher`, and Tier-2 stubs.
- Installed plugins: `cf-router` only.
- Enabled plugins: `cf-router`.
- Secrets currently visible by name in `/root/.hermes/.env`: `OPENROUTER_API_KEY` set, `KIMI_API_KEY` unset.
- Official/builtin Hermes skills are installed and enabled on `main-vps`, including `productivity/maps`, `productivity/google-workspace`, `productivity/airtable`, `productivity/notion`, `productivity/ocr-and-documents`, `mcp/native-mcp`, and `email/himalaya`.
- Connected-mode credential checks by name only: `AIRTABLE_API_KEY`, `NOTION_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `STRIPE_SECRET_KEY`, `SQUARE_ACCESS_TOKEN`, and `QUICKBOOKS_CLIENT_ID` are unset today. This is fine for no-key/manual-export mode and expected to change only per customer authorization.

## Hermes-First Analysis

| Domain | Hermes capability found? | Decision |
|---|---|---|
| WhatsApp messaging | Yes — Hermes WhatsApp uses Baileys Web session, not official Meta Business API | Use for no-bot-token customer/owner channel; protect session as credential |
| Telegram/Discord/Slack | Yes, but each requires platform bot/app tokens | Avoid if the goal is no bot token |
| Skills substrate | Yes — portable `SKILL.md` workflow logic and repo-local project skills | Use heavily; custom skills are low-risk when grounded in deployed patterns |
| Plugins | Yes — `cf-router` live plugin proves deterministic interception works | Use plugins for deterministic high-risk routing, not general business logic sprawl |
| OCR/docs | Yes — Hermes vision plus official `productivity/ocr-and-documents` | Use for receipt/menu/invoice/image/PDF ingestion |
| Maps/routes | Yes — official `productivity/maps` uses OSM/OSRM | Use for no-key location/routing workflows |
| Google/Airtable/Notion | Yes, official skills exist | Use only when customer accepts OAuth/PAT |
| MCP | Yes — official `mcp/native-mcp` exists | Use as integration layer; does not remove target-system OAuth |
| QuickBooks | No Hermes-native skill; Intuit MCP server exists | Prefer MCP over custom raw API, but OAuth/client secrets still required |
| Payments/POS/tax/delivery/e-sign | No suitable credential-free Hermes coverage | Treat as integration-required |

Awesome-Hermes-Agent ecosystem check: useful for awareness, but no evidence it removes SMB commercial API credential needs. Self-Evolution Kit is useful later for optimizing skills/prompts from traces, but it operates via API calls and PR review; it is not a credential-elimination mechanism.

## Market Research Addendum (2026-05-14)

Research update: the May 3 `tasks/skills-roadmap.md` conclusion that QBO/Stripe/Square/PayPal/DocuSign had no useful connector path is now stale. Hermes still does not ship SMB-commercial write skills for these systems, but the broader MCP/vendor ecosystem now provides credible connector candidates. The rule should be:

> vendor MCP or vetted MCP first; custom raw API only after connector review fails.

| Domain | Candidate | Credential shape | Portfolio impact |
|---|---|---|---|
| QuickBooks Online | Intuit QuickBooks Online MCP | QBO OAuth app/client credentials + realm | Strong candidate for #21 Expense Bookkeeper and #22 P&L Anomaly; owner approval guardrails still required before writes. |
| Stripe | Stripe MCP | OAuth or restricted secret key | Strong candidate for #15 Cash & AR/payment links; must restrict write tools and keep owner approval. |
| Square | Square MCP | OAuth or access token | Strong candidate if a customer uses Square POS/orders/catalog/inventory. |
| PayPal | PayPal MCP | OAuth or access token | Strong candidate for invoices/orders/refunds where PayPal is used. |
| Airtable | Official Airtable MCP + Hermes `productivity/airtable` | OAuth or PAT | Good for lightweight SKU/P&L/customer tables; permissions mirror Airtable permissions. |
| Notion | Official Notion MCP + Hermes `productivity/notion` | OAuth preferred; token for headless | Good for docs/checklists; not a money system of record. |
| DocuSign | DocuSign MCP connector | DocuSign account/OAuth/client config | Strong candidate for e-sign/onboarding once approval guards exist. |
| Infobip | Infobip remote MCP servers | OAuth 2.1 or API key | Strong multi-channel fallback; not no-token. |
| Pipedream | Pipedream MCP | Pipedream-managed OAuth | Broad fallback when no vendor MCP exists; adds platform dependency. |
| DoorDash/UberEats/Grubhub | no no-key Hermes skill found; vendor/partner API surfaces exist | DoorDash developer API, Uber Eats Marketplace API, Grubhub partner integrations, or iPaaS | Keep as integration-required and allowlist/credential-gated. |
| Tax filing | no credible official MCP found | state/provider credentials | Keep as reminder/checklist unless authorized integration exists. |

No-key/no-bot-token alternatives researched:

- WhatsApp Web linked-device bridges (Baileys/whatsapp-web.js) remove bot tokens but create session secrets.
- Local LLM options (Ollama, llama.cpp, vLLM, LocalAI) can reduce model API keys, but current repo paths still include OpenRouter-specific usage and need a separate quality/reliability project.
- Local OCR/doc options (`productivity/ocr-and-documents`, Tesseract, Tika, PaddleOCR) cover many receipt/menu/PDF workflows without cloud OCR credentials.
- Public OSM/Nominatim/OSRM can support low-volume map workflows without keys, but Nominatim requires a valid User-Agent/Referer and caps heavy use at 1 request per second.
- Manual CSV/PDF/photo export workflows remain the honest no-business-API mode for QBO/POS/supplier data.

## Credential Classes

| Class | Meaning | Examples | Can avoid? |
|---|---|---|---|
| `none/local` | Local files, timers, JSON state, uploaded media, public data | roster JSON, catering menu, decisions.log, OSM/OSRM | Yes |
| `session` | Login/session credential, no API key string | WhatsApp Baileys session, browser login, OAuth token cache | API-key-free, not credential-free |
| `oauth/pat` | User-authorized SaaS account access | Google, Notion, Airtable, QBO, Slack | Not for production automation |
| `api_key` | Direct platform or model key | OpenRouter, Exa, Stripe, Twilio | Sometimes replace with OAuth/local, often not |
| `write_rail` | Money/legal/customer-impacting writes | payments, tax filing, e-sign, delivery marketplace | No |

## All-Agent Matrix

Legend:

- **A:** Can run useful production MVP with no business API key and no bot token, assuming WhatsApp session + current LLM access.
- **B:** Can run no-key/manual-export MVP, but production improves with OAuth/API.
- **C:** Production automation requires external credentials/API/OAuth.
- **D:** Retired/folded/backlog or not worth standalone implementation.

| Agent | Credential-minimized feasibility | Hermes-first path | Hard credential boundary |
|---|---:|---|---|
| #1 Shift Agent | A | WhatsApp + local roster/schedule + cf-router/skills | Calendar/Sheets only if customer wants Google source of truth |
| #2 Catering Lead | A/B | WhatsApp media/text, local menu, JSON state, owner approval | Deposits, payment links, POS capacity, email/web-form integrations |
| #3 Multi-Location | A/B | Local location config + `productivity/maps` | Live inventory/POS across stores |
| #4 Daily Brief | A | Read decisions.log/local state and send WhatsApp | Email/Google backup channel |
| #5 EOD Reconciliation | B/C | Local audit/day summary + manual register input | POS sales/register API |
| #6 Inventory Tracker | B/C | WhatsApp staff counts, OCR supplier sheets, local SKU state | POS decrement, supplier reorder, Airtable/ERP |
| #7 Supplier Coordination | B/C | Local supplier roster + WhatsApp/manual order drafts | Supplier portals, email OAuth/SMTP, EDI/API ordering |
| #8 Receiving & QA | B/C | OCR/photo/PDF intake and manual PO matching | Inventory/POS writeback |
| #9 VIP Customer | B/C | Local catering/customer history and WhatsApp tone handling | POS/loyalty history |
| #10 Catering Follow-up | A/B | Agent #2 state + WhatsApp templates | Email/CRM |
| #11 Festival & Peak Prep | B | Local festival calendar JSON + daily brief signals | External event/calendar APIs if dynamic |
| #12 Hiring & Onboarding | B/C | WhatsApp intake + local docs/checklists | Google Drive, job boards, e-sign, background checks |
| #13 Compliance Calendar | A/B | Local compliance-items JSON + timers + WhatsApp owner actions | Agency portals / filing APIs |
| #14 Employee Document Tracker | B/C | Local folders + OCR + reminders | Google Drive, I-9/e-verify, e-sign |
| #15 Cash & AR | C | Manual ledger summaries can be local | Stripe/Square/PayPal, rail-specific Venmo/Zelle/Cash App/Razorpay, and bank-feed/Open Banking credentials |
| #16 Sales Tax Filing | C | Reminder/checklist only can be local | State filing portals/APIs and POS tax data |
| #17 Unit Economics | D | Retired; use #22 | Deep recipe/POS/COGS integration if revived |
| #18 Customer Complaint | D/A | Folded into #9 + #4; WhatsApp triage works | Review-site APIs if external |
| #19 Equipment Maintenance | B | Local equipment list, reminders, issue intake | Vendor/IoT APIs |
| #20 Owner Wellbeing | D/A | Folded into #4 + quiet-hours | Calendar optional |
| #21 Expense Bookkeeper | B/C | Receipt/image/PDF extraction + owner approval local | QBO write OAuth; Intuit MCP still needs OAuth |
| #22 P&L Anomaly | B/C | CSV/manual-export anomaly checks | POS/QBO live data |
| #23 Order Status & Pickup | C | Manual board only | POS/KDS/order-system live status |
| #24 Upsell & Menu Personalizer | B/C | Local menu + customer notes | POS/loyalty/order-history integration |
| #25 Third-Party Delivery Coordinator | C | Manual escalation only | DoorDash/Uber Eats/Grubhub partner credentials/webhooks or iPaaS |
| #26 Performance & Training Coach | B/C | Audit/log based coaching | POS/LMS/time-clock integrations |
| #27 Catering Equipment & Packaging Tracker | A/B | Local packaging inventory + catering event state | Supplier ordering APIs |
| #28 Perishable Priority & Waste Reducer | B/C | Manual expiry/photo counts | POS/inventory velocity data |
| #29 Slow-Mover Liquidation | B/C | Local inventory + owner-approved WhatsApp suggestions | POS/promo channel writeback |
| #30 Order Accuracy Guardian | C | Manual photo checks only | KDS/POS/order-state integrations |
| #31 Kitchen Load Balancer & ETA | C | Manual queue is weak | KDS/POS real-time kitchen timing |
| #32 Special Request Memory | A | Local customer/lead notes and soft priors | None unless synced to CRM/POS |
| #33 Loyalty & Punch-Card | B/C | Local phone ledger + WhatsApp reminders | POS/loyalty platform |
| #34 Menu Suggestion & Upsell | B/C | Local menu + current chat context | POS/cart/order channel |
| #35 Referral & Review Responder | B/C | Manual pasted review/referral handling | Google/Yelp/Facebook review APIs |
| #36 Credit Customer & Temple Account | B/C | Local ledger + WhatsApp statements | QBO/bank/payment reconciliation |
| #37 New Location Feasibility Scout | B/C | Public web + maps + local notes | Paid demographic/real-estate datasets |
| #38 Local Community Broadcast | C | Small owner-approved WhatsApp sends only | Bulk WhatsApp/SMS/email compliance and provider tokens |
| #39 Photo Menu Curator | A/B | WhatsApp photos + Hermes vision/local menu state | Cloud vision only if not using local/OAuth vision |
| #40 Competitor Price Watcher | B/C | Public web/manual crawl; low-volume | Search/scraping APIs, anti-bot handling |
| #41 Owner Wellbeing & Burnout Guardian | A/B | Quiet-hours + weekly owner-load summary | Calendar optional; avoid therapy framing |

## Portfolio Grouping

### Good no-key/no-bot-token candidates

These can be valuable with WhatsApp session, local state, skills/plugins, and current model access:

`#1`, `#2`, `#3`, `#4`, `#10`, `#13`, `#19`, `#27`, `#32`, `#39`, `#41`.

### Manual-export first candidates

These avoid API keys if the owner/staff uploads files/CSVs/photos, but will not be 99% autonomous until integrations are added:

`#5`, `#6`, `#7`, `#8`, `#9`, `#11`, `#12`, `#14`, `#21`, `#22`, `#24`, `#26`, `#28`, `#29`, `#33`, `#36`, `#37`, `#40`.

### Integration-required candidates

These should not be promised as no-key full automation:

`#15`, `#16`, `#23`, `#25`, `#30`, `#31`, `#38`, plus production modes of `#5`, `#6`, `#21`, `#22`, and `#36`.

## Recommended Architecture

1. **WhatsApp-only production channel by default.** Avoid Telegram/Slack/Discord bot tokens unless a customer explicitly asks for those channels.
2. **Treat WhatsApp session as a secret.** It is not a bot token, but it grants account access.
3. **Keep the current Hermes project-skill pattern.** This repo already works by installing project `SKILL.md` files into `/root/.hermes/skills` and `cf-router` into `/root/.hermes/plugins`.
4. **Install no-key official skills first:** `productivity/maps`, `productivity/ocr-and-documents`, and `mcp/native-mcp`.
5. **Install OAuth/PAT skills only per customer need:** `google-workspace`, `airtable`, `notion`.
6. **For QBO, use MCP before raw custom API.** Intuit now has a QBO MCP server with broad QBO coverage, but it still requires OAuth/client credentials.
7. **Offer two modes for integration-heavy agents:**
   - `manual-export mode`: owner uploads CSV/PDF/photo; Hermes analyzes and drafts.
   - `connected mode`: customer authorizes POS/QBO/payment system; Hermes automates.
8. **Model credential strategy:** current OpenRouter key is simplest. To reduce API keys, evaluate Codex OAuth, MiniMax OAuth, or local/self-hosted models, but do this as a separate reliability project because vision and routing quality matter.
9. **Use Self-Evolution Kit later.** It can optimize skills and prompts from traces, but it uses API calls and human-reviewed PRs; it is not a no-credential shortcut.

## Bottom Line

We can truthfully market a large part of this system as:

> WhatsApp-first Hermes agents, no platform bot token, no per-agent SaaS key for local/manual workflows.

We should not market the whole portfolio as:

> No API keys or credentials.

The honest product posture is:

> Start no-key with WhatsApp + local files. Add OAuth/API only when an agent must read or write a customer’s live business system.

## Sources

- Hermes WhatsApp docs: Baileys Web bridge, no Meta Business verification, session persistence/security.
- Hermes Telegram/Slack/Discord docs: Telegram requires BotFather token; Slack requires `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`; Discord requires `DISCORD_BOT_TOKEN`.
- Hermes configuration docs: secrets live in `.env`; OpenRouter requires `OPENROUTER_API_KEY`; OAuth providers can avoid API keys but still require login credentials/tokens; local/self-hosted model endpoints are supported.
- Hermes bundled skills catalog: `mcp/native-mcp`, `productivity/maps`, `productivity/google-workspace`, `productivity/airtable`, `productivity/notion`, `productivity/ocr-and-documents`.
- Intuit QuickBooks Online MCP server: broad QBO tool coverage, but OAuth/client credentials required.
- Hermes Agent Self-Evolution Kit: useful for skill/prompt optimization with guardrails, but API-call based.
- Awesome Hermes Agent: useful ecosystem index, not evidence of credential-free SMB integrations.
