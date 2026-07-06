# Flyer-Studio — Rollback Runbook

Last updated: 2026-06-29

Rollback is fast and layered. Use the lightest sufficient lever first.

## Levers (lightest → heaviest)
1. **Feature flag off / kill-switch** — flip the change's flag (`FLYER_*_KILLSWITCH` or its scoped flag) off. Fastest; no deploy; restores prior behavior immediately. Prefer this whenever the change shipped behind a flag.
2. **Revert the PR** — `git revert` the merge, rebuild + redeploy the tarball. Use when the change is not behind a flag or the flag is insufficient.
3. **Restore previous config** — revert `config.yaml` / flag values to the last-known-good and re-run the deploy smoke.
4. **Stop the customer-facing release** — demote the release mode (production → canary → internal → off) and halt onboarding new customers onto the changed path. See `docs/runbooks/release.md`.

## Mandatory post-rollback verification
- [ ] Deterministic fallback is intact and serving.
- [ ] **No fabricated facts shipped** — spot-check recent flyers: no invented price / offer / business name / date / location.
- [ ] **No QR change shipped** — customer-supplied QR codes preserved (not regenerated) + decode to the supplied target on the correct channel.
- [ ] Locked-fact enforcement + the visual-QA fabrication gate are active.
- [ ] Hermes version unchanged (pinned 0.14); WhatsApp not migrated; no community skill installed.
- [ ] Operator notified; incident + root cause recorded.

## Notes
- Prefer **flag-off over revert** when the change shipped behind a flag (reversible in seconds, no redeploy).
- The **deterministic fallback** and the **locked-fact / fabrication gate** are the safety floor — a rollback must never leave them disabled.
- A customer-facing rollback is not "done" until the no-fabricated-facts and no-QR-change checks above are confirmed on real recent output.
