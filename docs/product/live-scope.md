# Flyer-Studio — First Commercial Slice (live scope)

Last updated: 2026-06-29

## Product
WhatsApp-first AI marketing assistant for Indian restaurant / grocery / bakery / meat-store owners.

## Customer-visible promise
A store owner sends an offer / menu / photo / logo / QR code on WhatsApp and receives a high-quality flyer plus a social / WhatsApp-ready creative package, **with locked facts preserved**.

## In scope (first commercial slice)
1. **WhatsApp intake** — owner sends text + media (offer / menu / photo / logo / QR) on WhatsApp.
2. **Customer / store profile** — registered business name, contact, location, logo, QR (the locked-fact identity set).
3. **Flyer generation** — integrated generation with the deterministic-overlay fallback; locked facts rendered correctly.
4. **Human approval** — owner reviews the preview and approves (or requests a change) on WhatsApp.
5. **Download / shareable output** — a high-quality flyer + WhatsApp/social-ready package the owner can download/share.
6. **Basic job history** — the owner's prior flyer jobs are listed/retrievable.
7. **Manual regenerate** — owner can request a bounded regenerate, preserving locked facts.
8. **QR preservation** — a customer-supplied QR is preserved (never regenerated) and placed correctly per channel.
9. **Fact locking** — price, offer, business name, date, and location are locked and never fabricated; visual QA + the fabrication firewall enforce.

## Out of scope (explicitly, for this slice)
- Full autonomous **Creative Director Loop**.
- **Community / untrusted skill** installation.
- **WhatsApp migration** (stay on the pinned Hermes 0.14 bridge).
- **Multi-tenant billing**.
- **Large dashboard** / analytics suite.
- **New Hermes upgrade** (pinned 0.14; 0.17 blocked).

## Guardrails (always on)
- Deterministic fallback never removed; locked-fact enforcement never weakened.
- No fabricated price / offer / business name / date / location / QR target.
- Customer-supplied QR codes are never regenerated.
- Every customer-facing change ships flag-gated, allowlist-scoped first (`+17329837841`), kill-switchable — see `docs/runbooks/release.md` and `docs/runbooks/rollback.md`.
- No change to the Hermes version, no WhatsApp migration, no community-skill install.
