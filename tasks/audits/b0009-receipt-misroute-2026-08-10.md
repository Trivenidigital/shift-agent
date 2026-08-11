# B0009 — expense receipt misrouted into Flyer Studio brand-asset pool

**Drift-check tag:** `Hermes-native` — evidence record only. No new primitives, no
infrastructure proposed, no code changed.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Audit/evidence capture | yes — `decisions.log` NDJSON chokepoint + logrotate archive | use it; this doc only extracts a permanent copy before 30-day expiry |
| Brand-asset state change | yes — `/usr/local/bin/set-flyer-brand-asset-state` (audited activate/deactivate) | use it; do NOT hand-edit `customers.json` |

Ecosystem check: no new capability is being built, so no awesome-hermes-agent
sweep applies. Verdict: nothing net-new.

---

## Why this record exists

The live evidence lives in `/var/log/shift-agent-archive/decisions.log-20260811`,
which is subject to `rotate 30` (`/etc/logrotate.d/shift-agent:7-8`) and therefore
**expires around 2026-09-10**. The binding between asset `B0009` and the
originating WhatsApp message exists in exactly one place — the `customers.json`
brand-asset record — so any removal of that record destroys the reconstruction
chain unless copied out first. This file is that copy.

## What happened

On 2026-08-10 a real business expense receipt was sent over WhatsApp with the
caption `Expense receipt review this`. It was ingested by Flyer Studio as brand
asset `B0009` (a "logo") instead of reaching the expense path, and the owner was
told *"Logo saved and will be used for future flyers."*

## Causal chain (verified against deployed code)

1. `hooks.py:566` calls `_try_flyer_brand_asset_intercept` **before** the receipt
   cession at `hooks.py:631`.
2. In `_try_flyer_brand_asset_intercept` (`hooks.py:4238`):
   - `if role == "owner": return None` — owner is exempt, but this sender
     resolved as **employee `e008`**, so the exemption did not apply.
   - `is_brand_asset = active_project is not None or explicit_asset_words`
   - The caption contained **no** asset words (`logo`/`template`/`sample`/
     `reference`/`brand`/`replace`). It was captured **because the chat had an
     active flyer project**.
3. The receipt cession `_receipt_caption_cedes_to_dispatcher` (`hooks.py:4788`)
   requires `role == "owner"` and so would have refused regardless — but it was
   never reached, because step 2 returned terminally first.
4. The intent classifier that could have disambiguated **timed out**:
   `classifier_status:"timeout"`, `classifier_latency_ms:4001`,
   `decision_source:"none"`, `advisory_intent:"unknown"`. The deterministic
   fallback route won.

The system flagged its own exposure on the same turn:
`active_customer_risk: true`, `risk_scope: "pre_project_customer_visible"`.

## Audit-log evidence (source: `decisions.log-20260811`)

Three entries, all keyed to message id `3AFD221E25CE7DD9E966`:

| ts (UTC) | type | key fields |
|---|---|---|
| `2026-08-10T14:36:57.375734Z` | `cf_router_raw_body` | `chat_id=201975216009469@lid`, `body_head="Expense receipt review this"`, `hasMedia=true`, `mime=image/jpeg`, `mediaUrls=[/root/.hermes/cache/images/img_32956c6af619.jpg]` |
| `2026-08-10T14:37:02.221215Z` | `front_brain_reply_composed` | `reply_text="Flyer Studio\n------------\nLogo saved and will be used for future flyers."`, `verdict=passed` |
| `2026-08-10T14:37:02.620898Z` | `cf_router_intercepted` | `reason=flyer_brand_asset_saved`, `sender_role=employee`, `customer_id=CUST0001`, `subprocess_rc=0` |
| `2026-08-10T14:37:06.626206Z` | `flyer_hermes_intent_decision` | `classifier_status=timeout`, `classifier_latency_ms=4001`, `actual_route=flyer_brand_asset_saved`, `route_terminal=true`, `active_customer_risk=true` |

## The `customers.json` record (verbatim, the only B0009 ↔ message binding)

```json
{
  "asset_id": "B0009",
  "kind": "logo",
  "path": "/opt/shift-agent/state/flyer/brand_assets/CUST0001/B0009-logo.jpg",
  "mime_type": "image/jpeg",
  "sha256": "0a750e0b78a44f0f9d643608a9bc9d5af5a694c4e19f623575dfdbc1395b2e15",
  "original_message_id": "3AFD221E25CE7DD9E966",
  "received_at": "2026-08-10T14:37:02.090640Z",
  "active": true,
  "notes": "Expense receipt review this",
  "derived_style": null
}
```

File on disk at time of record: `B0009-logo.jpg`, 896544 bytes, mtime
`Aug 10 14:37`, under `state/flyer/brand_assets/CUST0001/`.

## Sender identity at the time

```json
{"role": "employee", "employee_id": "e008", "name": "Srini Bangaru",
 "phone_normalized": "+17329837841", "lid": "201975216009469@lid"}
```

## Disposition

Authorized by the operator on 2026-08-11 to remove B0009 only, after the
owner-path rehearsal (Workflow 2) was ruled `BLOCKED_UNREACHABLE_IDENTITY`.
Removal scope explicitly excluded F0226 / F0217 / F0222, receipt routing,
sender-identity semantics, and permissions.

See the companion finding on WhatsApp identity topology: the configured owner
JID is the same account the bridge runs on, which is why the owner-path
rehearsal could not be executed.

## Actions taken — 2026-08-11

Backup before mutation: `state/flyer/customers.json.pre-b0009-removal-20260811T190326Z`

1. **Deactivated** via the sanctioned audited path
   `/usr/local/bin/set-flyer-brand-asset-state --asset-id B0009 --deactivate
   --actor operator`. Result `{"prior_active": true, "new_active": false}`,
   audit row `flyer_brand_asset_state_changed` at
   `2026-08-11T19:03:27.417914Z`. Diff vs backup was exactly two lines: the
   asset's `active` flag and the store's `updated_at`.

2. **Deleted the image** `state/flyer/brand_assets/CUST0001/B0009-logo.jpg`
   (896544 bytes) after confirming its sha256 matched the recorded
   `0a750e0b…95b2e15`. **This deletion has no audit row** — no sanctioned
   file-deletion path exists in Flyer Studio, so this markdown entry is the
   record of it. The operator retains the original receipt in WhatsApp.

3. **Record deliberately retained** (deactivated, not deleted). Removing it
   would require hand-splicing `customers.json`, which is the exact failure
   mode `set-flyer-brand-asset-state` was written to prevent (see its
   docstring re: the 2026-06-17 wrong-brand hand-edit with zero audit rows).
   The inert record preserves the `B0009 ↔ 3AFD221E25CE7DD9E966` binding in
   live state.

Post-state verified: 6 asset files remain (B0003–B0008), no other asset
removed or renumbered, `customers.json` otherwise unchanged, no outbound
message sent, no routing/config/permission change, gateway healthy.

Risk closed: `flyer_render.py:_active_brand_assets` filters
`if asset.active and Path(asset.path).exists()` — B0009 now fails both
conditions and cannot be selected by any generation path.
