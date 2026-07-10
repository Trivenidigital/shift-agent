# Decision — Agent #21 Expense Bookkeeper v0.2 QBO-write: rescope to the Intuit QBO MCP server

**Drift-check tag:** `extends-Hermes` — the `QBOClient` Protocol + money-moving guardrails are our
extension; this rescope routes the WRITE through Hermes's `mcp/native-mcp` bridge to a vendor MCP
server rather than hand-rolling an Intuit SDK OAuth+write client, staying on the substrate path.

**Authorization:** operator "proceed with … C" (2026-07-10). Decision/design doc only — **no
implementation**. Opened for review.

**Status of #21:** v0.1 shipped (scaffolded, DISABLED-default): dispatcher + `parse_receipt_photo`
+ approval handler + `MockQBOClient`. `RealQBOClient` is a stub (raises `NotImplementedError`).
The v0.2 plan of record (`qbo_client.py:8`, `expense-bookkeeper-v02-followups.md:101`) is: "wire the
actual Intuit Developer SDK" — i.e. a hand-rolled OAuth2 + write client (~400 LOC, the last large
net-new surface for #21).

## What changed since that plan: an official write-capable QBO MCP server now exists

Re-check of the ecosystem (2026-07-10; supersedes the `skills-roadmap.md` "no QBO write skill"
finding): **`intuit/quickbooks-online-mcp-server`** — official Intuit org, **Apache-2.0**, ~305★,
last push 2026-06-17. Exposes write tools directly matching #21's need: `create_bill`,
`create_purchase` (QBO's expense-recording entity), `update_bill`, `create_vendor`, `create_payment`
(~55 create/update tools). **OAuth2** to one QBO company; access-token 1h / refresh ~100 days
auto-refreshed. Runs **local, one company per instance** — which maps exactly onto our per-customer
single-tenant VPS model. (Stripe has an equivalent official MCP — relevant to commerce/deposits, out
of scope for #21.)

## Hermes-first analysis

| Step | Hermes provides? | Net-new |
|---|---|---|
| WhatsApp receipt intake → dispatch → vision extract → structured `ExpenseLead` | `[Hermes]` (v0.1 proves it) | 0 |
| Chart-of-accounts mapping (`qbo_account`) | `[net-new]` — per-customer business logic | ~60–100 LOC |
| Approval workflow (`#XXXXX` + amount), role-gating, audit chain, reply | `[Hermes]` (v0.1) | 0 |
| **QBO write API** (OAuth + create_bill/create_purchase) | `[net-new]` — external write; **MCP server now supplies the plumbing** | was ~400, now ~80–120 LOC adapter |

**Verdict:** 2 of 9 steps net-new (unchanged); the rescope makes the QBO-write step *more*
Hermes-aligned — it uses the documented `mcp/native-mcp` escape hatch (CLAUDE.md: "check native-mcp
for community MCP servers before estimating custom LOC") instead of custom SDK code.

## Drift-rule self-checks (deployed code Read before drafting)

- ✅ Read `src/platform/qbo_client.py` (the `QBOClient` Protocol, `QBOPushResult`, `QBOPushError` +
  `QBOErrorClass`, `RETRYABLE_ERROR_CLASSES`, `RealQBOClient` stub) — the adapter target — before
  drafting the mapping.
- ✅ Read `tasks/expense-bookkeeper-v02-followups.md` (V02-4 token-redactor gap for real OAuth
  payloads; "Real RealQBOClient impl" as the open v0.2 item) before drafting the residual net-new.

## Decision

**Implement `RealQBOClient` (v0.2) as a thin adapter over the Intuit QBO MCP server (via
`mcp/native-mcp`), NOT a hand-rolled Intuit SDK client.**

**Preserved (no change):**
- The `QBOClient` **Protocol** stays the seam. `RealQBOClient.push(lead) -> QBOPushResult` maps the
  MCP `create_bill`/`create_purchase` tool response → `QBOPushResult`, and MCP tool errors → the
  existing `QBOPushError` classes (`token_expired`/`rate_limit`/`server`/`network`/`bad_account`/
  `invalid_request`) so `RETRYABLE_ERROR_CLASSES` and all v0.1 guardrails + tests remain real.
- **Money-moving discipline** (code+amount approval, perceptual-hash dedup, per-amount thresholds,
  reversibility window) — already in v0.1; unaffected. This is the layer MCP does NOT provide.

**Net-new that REMAINS (MCP does not eliminate it):**
- The **adapter** (`ExpenseLead` → MCP tool args incl. `VendorRef` + typed line detail; response/
  error mapping): ~80–120 LOC + tests. The `MockQBOClient` stays for tests (adapter is mock-swappable).
- **Chart-of-accounts mapping** (`qbo_account` → QBO account/vendor resolution; likely a
  `create_vendor` upsert + account lookup): ~60–100 LOC — genuinely per-customer, no MCP primitive.
- **V02-4 token-redactor** extension (real OAuth `state=`/`code_verifier=` now reachable) — still needed.
- Per-VPS MCP-server config + OAuth app registration (config, not LOC).

**Eliminated (~280–320 LOC):** custom OAuth2 authorization-code + PKCE flow, token refresh/persist,
Intuit SDK request formatting, HTTP retry plumbing — all now handled by the MCP server.

## Risks / residuals to resolve at v0.2 implementation time (NOT in this doc)
1. **MCP-server maturity** — 26 open issues; an OAuth-refresh bug was patched as recently as
   2026-06-17. **Soak-test in the QBO sandbox before trusting unattended production writes.** Pin a
   reviewed commit (mirror our Hermes-pin discipline) rather than tracking `main`.
2. **Deployment shape** — it's a local/self-hosted server (one company per instance). Decide: run it
   on each customer VPS (fits single-tenant) vs the operator VPS. Adds a process to supervise + a
   deploy-gate (does the MCP server respond? is OAuth valid?) — mirror `check-commerce-*` gates.
3. **Security** — use restricted OAuth scopes; keep the human-confirmation (our `#XXXXX` approval)
   in front of every write; the MCP tool call must be gated behind owner approval, never autonomous.
   This aligns with, not replaces, our money-moving discipline.
4. **Idempotency** — confirm the MCP create tools are safe under our `original_message_id`
   replay/retry (avoid double-posting an expense). May need a client-side idempotency key or a
   read-before-write check.

## Recommended v0.2 sequence (net-new only; each a separate PR)
1. **MCP-server deploy + gate** — vendor server pinned + `check-qbo-mcp` readiness gate (config/OAuth). 
2. **`RealQBOClient` MCP adapter** — Protocol impl + response/error mapping + V02-4 redactor + tests.
3. **Chart-of-accounts mapping** — `qbo_account` → QBO account/vendor resolution + tests.
4. **Sandbox soak** — n=X real sandbox writes across error classes before flipping the flag.
5. **Enable** (operator-gated, per-customer).

## Out of scope (this doc)
- Any code. This is the build-vs-integrate decision + residual scoping.
- Stripe MCP (commerce/deposits) — same ecosystem shift, separate track.
- The other v0.2 followups (V02-1..8) — tracked in `expense-bookkeeper-v02-followups.md`, unaffected.
