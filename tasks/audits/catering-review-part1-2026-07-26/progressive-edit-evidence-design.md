# Progressive-edit evidence probe — DESIGN ONLY

**Status:** design / reviewer-ready. **No implementation, no probe execution, no production action.**
**Authoritative starting state:** repo/deploy `dc7a81a2b6366f9c09fad86e7e07ee84a74c768d`
(`deploy-20260729-021058-dc7a81a2`); TE harness/control-socket/transport-budget **OFF**;
containment/allowlists unchanged; post-hoc Linux TE closure **GO**; Stage A **HOLD**;
Stage B / live-harness **NO-GO**. This document does not reopen or modify the completed
transport-evidence closure; it designs the *next* evidence step for the distinct `/edit` seam.

**Drift-check tag:** `extends-Hermes` — reuses the deployed, marker-fenced Hermes patch surface
(`edit_message` budget/front-brain hooks) and the existing `safe_io` per-turn budget machinery;
proposes no new Hermes-owned behaviour. Any probe would mirror the existing `/send` transport-evidence
harness pattern, not introduce a parallel transport.

> **Reviewer amendment 1 (2026-07-31) — owner-page/provider-op accounting corrected.** The earlier
> statement that the budget-exhaustion page "is not a WhatsApp send" was only true on the primary
> owner-notification branch. `notify_owner_with_fallback` has TWO branches and this document now preserves
> both (§1d, §6): primary channel (Pushover/Telegram) succeeds → **one internal owner notification, zero
> owner WhatsApp provider operations**; primary channel FAILS → **one reviewed owner self-chat fallback =
> one additional WhatsApp provider operation** on a **separate owner-send/audit identity**, **never** counted
> as an edit or a customer send. Consequently Strategy C may claim **zero customer/provider *edit* visibility**
> only when owner paging is deterministically stubbed to an internal loopback OR the fallback branch is
> separately contained + accounted; it may **not** categorically claim "zero provider operations" while the
> real self-chat fallback remains possible.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp message edit transport (`/edit` boundary) | none — Hermes IS the substrate; `WhatsAppAdapter.edit_message` is the pinned-Hermes adapter method, budget-screened via the deployed `patch-hermes.py` marker fences | Reuse the real `edit_message` → `POST /edit` seam + the deployed `safe_io.turn_send_budget_gate` draft ceiling. No custom transport; evidence only. |
| Evidence recording / at-most-once / durability | already built for `/send` (`transport_evidence_ledger`/`_lease`/`_diagnostic` + `tests/test_transport_evidence_harness.py`) | Mirror the existing harness's fixtures/oracle for the `/edit` path; do not fork a second evidence framework. |

awesome-hermes-agent ecosystem check: no ecosystem skill governs WhatsApp progressive-edit
transport budgeting; this is bespoke egress-safety machinery already deployed in-tree. Verdict:
**extends-Hermes, evidence-only — nothing net-new to build for the design itself.**

---

## 1. Source-path map (exact anchors, read-only)

Two code homes: **(a)** pinned-Hermes adapter, patched by `tools/patch-hermes.py` (marker-fenced,
default-inert); **(b)** shift-agent `src/platform/safe_io.py` budget machinery. Hermes anchors were
read read-only from the live (patched) box (`/root/.hermes/hermes-agent/…`, whatsapp.py post-TE
sha `b152048a…`); repo anchors from the worktree at `dc7a81a2`.

### 1a. `WhatsAppAdapter.edit_message` — the real edit method (live Hermes `whatsapp.py:1125`)

```python
async def edit_message(self, chat_id, message_id, content, *, finalize=False) -> SendResult:
    """Edit a previously sent message via the WhatsApp bridge."""
    if not self._running or not self._http_session:
        return SendResult(success=False, error="Not connected")
    bridge_exit = await self._check_managed_bridge_exit()
    if bridge_exit:
        return SendResult(success=False, error=bridge_exit)
    try:
        import aiohttp
        # BEGIN shift-agent-turn-budget-edit-drop        (whatsapp.py:1143-1147)
        content = _shift_turn_send_budget_screen(chat_id, content, reserve_budget=finalize)
        if content is _SHIFT_DROP_SEND:
            return None                       # <-- PRE-PROVIDER DENIAL (no /edit POST)
        # END shift-agent-turn-budget-edit-drop
        # BEGIN shift-agent-front-brain-edit             (whatsapp.py:1148-1150)
        content = _shift_front_brain_screen_outbound(chat_id, content, reserve_budget=finalize)
        # END shift-agent-front-brain-edit
        async with self._http_session.post(               # whatsapp.py:1151
            f"http://127.0.0.1:{self._bridge_port}/edit",
            json={"chatId": chat_id, "messageId": message_id, "message": content},
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                return SendResult(success=True, message_id=message_id)   # echoes SAME id
            else:
                error = await resp.text()
                return SendResult(success=False, error=error)
    except Exception as e:
        return SendResult(success=False, error=str(e))
```

| Concern | Anchor | Established fact |
|---|---|---|
| Edit method signature | Hermes `whatsapp.py:1125` | `(chat_id, message_id, content, *, finalize=False) -> SendResult`; `message_id` is an **input** — the caller supplies the id of an already-sent message. |
| Caller | Hermes core `stream_consumer.py` (per `patch-hermes.py:62-67`) | delivers streamed drafts (`finalize=False`) AND the finalized answer (`finalize=True`) via `adapter.edit_message`. |
| Bridge `/edit` request formation | Hermes `whatsapp.py:1151-1159` | `POST http://127.0.0.1:{bridge_port}/edit`, JSON `{"chatId","messageId","message"}`, 15 s timeout. |
| Provider message-ID handling | `whatsapp.py:1160-1161` | On 200 → `SendResult(success=True, message_id=message_id)` — **echoes the same input id, mints no new id** ⇒ an edit modifies **one existing message**, does not create additional messages. |
| Provider acknowledgment semantics | `whatsapp.py:1160-1165` | Ack = **bridge HTTP status** (200 = accepted). Non-200 → `SendResult(success=False, error=<resp text>)`; exception → `success=False`. This is a *bridge* accept, not a WhatsApp delivery/edit receipt (the provider-side edit happens downstream inside `bridge.js`). |
| Pre-provider denial boundary | drop block `whatsapp.py:1143-1147` precedes the POST at `:1151` | A budget-denied edit returns `None` with **no `/edit` POST**. Denial is strictly pre-provider. |
| Retry behaviour | no retry loop in `edit_message` | single POST; no automatic re-send (parity with the `/send` `max_retries==0` faithful behaviour asserted in the harness). |

### 1b. The injected screens (`tools/patch-hermes.py`, marker-fenced)

| Element | Anchor | Note |
|---|---|---|
| `_SHIFT_DROP_SEND` sentinel + `_ShiftDropSend` | `patch-hermes.py:283-296` | identity-compared not-send singleton; caller relays nothing. |
| `_shift_turn_send_budget_screen(chat_id, content, reserve_budget)` | `patch-hermes.py:299-354` | calls `safe_io.turn_send_budget_gate(...)` via `getattr`; `False` → returns `_SHIFT_DROP_SEND`. Fail-closed when **armed** and the gate is unavailable; byte-identical passthrough when disabled. |
| `_shift_front_brain_screen_outbound(chat_id, content, reserve_budget)` | `patch-hermes.py:368-393` | content screen (`front_brain_screen_gateway_send`); contractually `-> str` (may substitute, never suppress). |
| edit_message budget-drop inject | `WHATSAPP_TURN_BUDGET_EDIT_DROP` `patch-hermes.py:432-437`; applier `_apply_wa_turn_budget_adapter` `:1012-1062` (edit drop inserted at the front-brain-edit marker, `:1050-1060`, so the drop precedes the front-brain-edit block) | marker `shift-agent-turn-budget-edit-drop`. |
| edit_message front-brain inject | `WHATSAPP_FB_EDIT_INJECT` `patch-hermes.py:422-425`; applier `_apply_wa_front_brain` `:950-1009` (requires `import aiohttp` inside `edit_message`, `:989-1006`) | marker `shift-agent-front-brain-edit`. |
| `reserve_budget=finalize` threading | `patch-hermes.py:423, 433` | draft edits (`finalize=False`) → `reserve_budget=False`; the finalized edit (`finalize=True`) → `reserve_budget=True`. |

### 1c. The transport budget — the progressive-edit counter + effective cap (`src/platform/safe_io.py`)

| Element | Anchor | Fact |
|---|---|---|
| Feature flag (default OFF) | `turn_send_budget_enabled` `safe_io.py:2054-2062` | `GATEWAY_TURN_SEND_BUDGET_ENABLED == "1"`; default OFF ⇒ gate returns `None` ⇒ byte-identical passthrough. |
| Finalized cap | `turn_send_budget_limit` `:2082-2100`; `DEFAULT_TURN_SEND_BUDGET_LIMIT=5` `:2026`; `MAX=20` `:2035` | finalized sends per inbound turn (send() + finalized edits). |
| **Progressive-draft ceiling ("cap 50")** | `turn_send_budget_draft_limit` `:2103-2124`; `DEFAULT_TURN_SEND_BUDGET_DRAFT_FACTOR=10` `:2042`; `MAX_TURN_SEND_BUDGET_DRAFT_LIMIT=500` `:2045` | `draft_limit = min(limit × 10, 500)` = **5 × 10 = 50** by default; overridable absolute via `GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT`. **This is the "cap 50."** |
| The counter | `_TurnSendBudget` `:2127-2179`; `reserve(consume)` `:2157-2179` | Two bounds. `consume=True` (finalized): drop when `count >= limit`, else `count += 1`. `consume=False` (draft): drop when `count >= limit` **OR** `draft_count >= draft_limit`, else `draft_count += 1` (never `count`). Synchronous ⇒ atomic under the single event loop. |
| Turn-boundary freeze | `begin_inbound_turn_send_budget` `:2187-2225` | config frozen ONCE per inbound turn; config-read exception ⇒ config-failed budget ⇒ every send suppressed (fail-closed). |
| The gate | `turn_send_budget_gate(jid, message, *, reserve_budget=True)` `:2228-2324` | `None`=OFF passthrough; `True`=admitted; `False`=suppress (fail-closed). `reserve_budget=False` for drafts. `message` never recorded (metadata-only telemetry). |
| Denial audit + page | `_emit_turn_send_budget_suppressed` (per drop) + `_page_turn_send_budget_exhausted` (once/turn, guarded by `budget.paged`) `:2255-2308` | denial reason `"exhausted"` vs `"draft_exhausted"` (`:2300`). |
| Content screen | `front_brain_screen_gateway_send` `:1337`; reserve_budget threaded `:1359-1367, 1427-1453` | every edit (incl. drafts) is screened; only the finalized edit reserves a finalized slot. |

**Effective edit-turn behaviour (default config).** Within one inbound turn: draft edits are admitted
while `draft_count < 50` **and** `count < 5`; the **51st draft** → `draft_count >= 50` → gate `False`
→ `_SHIFT_DROP_SEND` → `edit_message` returns `None` → **no `/edit` POST** (denial reason
`draft_exhausted`; one operator page). The finalized edit (`finalize=True`) then consumes one of the 5
finalized slots. So "50 accepted edits + first denial" = 50 admitted draft edits then the 51st draft
denied pre-provider, assuming the 5-finalized cap is not reached first.

### 1d. Paging / audit / state (from `safe_io` + the `/send` evidence stack)

- Denials are audited to the decisions log via `_emit_turn_send_budget_suppressed` (metadata-only:
  turn-id / count / limit / reason — never message content) and paged once per turn.
- **Owner-page accounting is two-branch (load-bearing for the provider-op count).** The once-per-turn page
  goes through `_page_turn_send_budget_exhausted` → `notify_owner_with_fallback` (`safe_io.py:2353-2395`):
  - **Primary channel succeeds** (Pushover via `shift-agent-notify-owner`, or Telegram) → **one internal
    owner notification, ZERO owner WhatsApp provider operations.**
  - **Primary channel FAILS** → **one reviewed owner self-chat fallback = one additional WhatsApp provider
    operation**, on a **separate owner-send/audit identity** (the owner self-chat, not the customer edit
    chat), and it is **never** counted as an edit or a customer send.
  A probe must therefore either (a) deterministically stub owner paging to an internal loopback (so the page
  cannot reach any provider), or (b) separately contain + account the fallback branch (assert its one owner
  self-chat op lands on the owner identity, is separately audited, and never touches the customer edit chat
  or the `loopback_edit_posts` count). It may not assume the fallback never fires.
- The `/edit` seam is **NOT** wired to the transport-evidence ledger (see §2). Its evidence surface today
  is the budget gate's audit rows + the pre-provider `return None`, not a `transport_evidence_ledger` record.

---

## 2. Central finding — the existing TE harness proves `/send`, NOT `/edit`

- **Provenance:** the transport-evidence patch injects the provider-entry observer at the bridge
  **`POST /send`** boundary only (`transport-evidence-source-closure-provenance.md` §"Anchor counts",
  whatsapp.py anchor = `async with self._http_session.post( … /send",`). There is **no `/edit`
  observer**.
- **Harness:** `tests/test_transport_evidence_harness.py` is 100 % finalized-send. A whole-file grep for
  `edit|draft|progressive|reserve_budget|draft_limit` returns **0 matches** (verified). Its `WA_FIXTURE`
  `send()` POSTs `/send` only (`:1346`); the cap it proves is the finalized `cap=5`.
- **Consequence:** proving the **cap-50 draft ceiling at `/edit`** is genuinely net-new evidence. It
  **cannot** reuse the `/send` provider-entry ledger record; the honest evidence for the edit cap is
  (i) the budget gate's audit rows, (ii) the pre-provider `return None` (no `/edit` POST for the 51st),
  and (iii) — only for real-edit fidelity — a real-provider positive control.
- The runner's isolation is asserted (`test_diagnostic_runner_has_no_business_state_seam`, harness `:617`);
  any edit path added to the runner must enter through the existing `dispatch_segment`/`plan` seam, not a
  new business handle — a constraint on any future implementation, noted here for the design.

**Partial edit-awareness that already exists (do not re-derive):**
- The TE **control-plane PREPARE already asserts the draft ceiling value**: `EXPECTED_DRAFT_CAP = 50`
  (`transport_evidence.py:69`), checked in `verify_prerequisites` — `caps_unexpected` if
  `finalized_cap != 5 or draft_cap != 50` (`transport_evidence.py:351-355`), and unit-covered by
  `test_caps_not_exactly_5_50_refused` (harness `:360-366`). So the **configuration/default proof** that the
  draft ceiling equals 50 is **already machine-closed**; the net-new work is a *runtime* exercise of the
  `/edit` draft path, not re-proving the cap value.
- But the **evidence ledger + lease are send-only** (`transport_evidence_ledger.py` /
  `transport_evidence_lease.py`: 0 matches for `edit`/`draft`/`progressive`). Every `AttemptLedger` row, the
  `RunLedger`, the committed-nonce ledger, the lease, and the 28-segment plan model **finalized sends**
  against the `finalized_cap=5` gate. The `draft_cap=50` flows through `budget_state` only as a static
  precondition. ⇒ A `/edit` probe has **two honest evidence surfaces**: (i) reuse the budget gate's audit
  rows + the pre-provider `return None` (no new schema), or (ii) add a net-new `/edit`-unit ledger record
  (implementation, explicitly out of scope for this design). This design recommends (i).

---

## 3. Facts that still require a controlled runtime observation

These cannot be closed by source reading alone and must be predeclared as runtime-observation items:

1. **Real WhatsApp edit semantics** — that a `POST /edit` with an existing `messageId` edits **one**
   message in place on the real provider (source shows the *adapter* echoes the same id; the *provider*
   behaviour lives in `bridge.js` + WhatsApp and is not established from the adapter alone).
2. **User-visible edit churn** — whether N rapid edits produce N notifications or a single quietly-updating
   message on the recipient device. Not derivable from code; provider/client behaviour.
3. **Bridge `/edit` acknowledgment fidelity** — that a bridge 200 corresponds to an accepted provider edit
   (vs an enqueue) — `bridge.js` `/edit` handler + provider response.
4. **End-to-end cap under real streaming** — that Hermes `stream_consumer.py` actually drives ≥ 51 draft
   `edit_message` calls in one turn so the ceiling is reached in production (fire-rate reality, not just the
   gate's arithmetic).
5. **Interaction with the finalized cap** — that a real streamed reply reaches the 50-draft ceiling before
   the 5-finalized cap (else the observed first-denial reason is `exhausted`, not `draft_exhausted`).

---

## 4. Three evidence strategies (assessment)

Ratings: what it proves / does not prove; provider-visible ops; operator+customer notification risk;
duplicate/retry risk; state mutation; cleanup; exact-release requirement; and whether it can honestly
establish **"cap 50 live-proven."**

### Strategy A — Full live boundary test: 50 accepted edits + first denial through the real provider
- **Proves:** the cap-50 draft ceiling end-to-end at the *real* `/edit` provider boundary; real in-place
  edit; real bridge ack; that production streaming can reach the ceiling.
- **Does not prove (extra):** nothing more than the others combined — it is the fullest — but it cannot
  *independently observe* the pre-provider block except as the provider's absence of a 51st edit.
- **Provider-visible ops:** **1 real `/send` + 50 real `/edit` calls** to a real chat. (An edit targets an
  *already-sent* message — `edit_message` takes `message_id` as input, §1a — so a real base message must be
  sent first; that initial send is its own recipient notification.)
- **Notification risk:** **HIGH** — the initial send **plus** 50 rapid edits on a real recipient; potential
  heavy churn on one message; consumes shared outbound cap (100/day) headroom.
- **Duplicate/retry risk:** LOW-MED — no adapter retry, but a 15 s timeout under real network can leave an
  edit's provider outcome uncertain.
- **State mutation:** real WhatsApp traffic + real decisions-log audit rows.
- **Cleanup:** **impossible** — neither the base sent message nor the edits can be un-sent; the message
  stays edited.
- **Exact-release requirement:** the deployed RC **plus** a `/edit` provider-entry observer that **does not
  exist** today ⇒ cannot yield provenance-grade observer evidence; relies on provider-side + audit-row
  evidence only.
- **"cap 50 live-proven":** **YES — the only strategy that can honestly claim it.** But it needs a real
  destination (an admitted allowlisted identity), a separate authorization, and accepts high churn.

### Strategy B — Small real-provider positive control + production-faithful pre-provider boundary proof
- **Proves:** (positive control, 2-3 real edits) that `edit_message` reaches the real `/edit` boundary and
  edits **one** message in place with a real 200 ack; (boundary proof, in-process) that the cap denial is
  **pre-provider** and lands at the draft ceiling.
- **Does not prove:** that 50 *real* sequential edits all succeed at the provider — the cap itself is proven
  against a loopback, not the real provider. ⇒ **not** "cap 50 live."
- **Provider-visible ops:** **1 real `/send` + the 2-3 control edits** (the base message must exist before
  it can be edited, §1a).
- **Notification risk:** **LOW** (one base send + a small control).
- **Duplicate/retry risk:** LOW.
- **State mutation:** small real traffic + in-process budget/audit state.
- **Cleanup:** control leaves 2-3 edits on one message; the boundary proof is in-process (tmp, cleaned).
- **Exact-release requirement:** the pre-provider proof drives the exact-release `safe_io` gate + the real
  patched `edit_message` logic; the control needs the deployed box + an admitted destination.
- **"cap 50 live-proven":** **NO** — establishes pre-provider gate + real-edit-in-place (small N) + config,
  not a live 50.

### Strategy C — Deterministic provider emulator / local bridge fixture exercising the exact `/edit` gate
- **Proves:** the exact cap-50 draft ceiling, the pre-provider denial (51st → `edit_message` returns
  `None`, zero POSTs to the loopback `/edit`), at-most-once per admitted draft, one-page-per-turn, and the
  `draft_exhausted` audit reason — deterministically, cross-platform, **no real provider**. Mirrors exactly
  how the `/send` harness proves `cap=5` via `_IntgHttpSession.post_count` (a `/edit` variant swaps the URL
  check).
- **Does not prove:** real provider in-place edit; real ack; real notification churn (the loopback does not
  model WhatsApp edit semantics).
- **Provider-visible ops:** **NONE on the customer/edit path** (all `/edit` traffic is loopback). The one
  budget-exhaustion owner page is the only thing that could reach a real provider, and only on its FAILURE
  branch (one owner self-chat op). Strategy C therefore **stubs owner paging to an internal loopback**
  (asserting `page_count==1` against the spy, `owner_page_provider_ops==0`) so it can honestly claim **zero
  provider operations of any kind**; if paging is left un-stubbed, C must instead account the fallback branch
  per §6 (`owner_page_provider_ops` ≤ 1 owner op, separately audited, never on the customer edit chat) and
  claim only "zero customer/provider *edit* operations."
- **Notification risk:** **NONE** on the customer path (owner paging stubbed to loopback; if un-stubbed, at
  most one owner self-chat on primary-channel failure — never a customer notification).
- **Duplicate/retry risk:** **NONE.**
- **State mutation:** in-process tmp only.
- **Cleanup:** full (tmp dirs; `_TURN_SEND_BUDGET` ContextVar reset — the existing
  `test_integration_wired_diagnostic_path` scaffold, harness `:1402-1411`).
- **Exact-release requirement:** loads the **real** `patch-hermes.py` transform (`_load_patch_hermes`) +
  real `safe_io` at `dc7a81a2`; strongest exact-release fidelity **for the gate**.
- **"cap 50 live-proven":** **NO** — proves "cap 50 enforced at the gate (emulated transport)."

---

## 5. Recommendation — lowest-risk valid method

**Primary: Strategy C (deterministic `/edit` emulator), executed as a repository-only, default-OFF,
never-in-production test that mirrors the existing `/send` harness.** It closes everything that source +
arithmetic leave open about the *gate* — the 50-draft ceiling, pre-provider denial, at-most-once, audit
row, single page — with **zero customer/edit provider visibility** (owner paging stubbed to loopback →
`owner_page_provider_ops==0`), zero customer notification risk, and the strongest
exact-release fidelity (it runs the real patched `edit_message` logic + real `safe_io`). This is the
faithful analogue of how `cap=5` was proven for `/send`, and it needs **no** Stage-A admission, no real
destination, and no live customer traffic. (Its one owner-page path is stubbed to an internal loopback so
`owner_page_provider_ops==0` — see §6; run un-stubbed only with the fallback branch separately accounted.)

**Only if the operator separately authorizes real-edit fidelity: add Strategy B's minimal positive
control (2-3 real edits) under an admitted Stage-A identity** to establish that a real `/edit` edits one
message in place with a real ack — the one fact C cannot supply. Keep it to the smallest N that
demonstrates in-place edit (≤ 3).

**Do NOT run Strategy A (50 live edits) as the default.** It is the *only* way to honestly claim "cap 50
live-proven," but it requires a real destination, a separate authorization, and accepts high churn on the
recipient with no cleanup. Recommend it only if the operator explicitly requires the words "cap 50
live-proven" and accepts that cost — and even then, guard it behind Stage A + a dedicated authorization,
and note the missing `/edit` provider-entry observer limits its provenance grade.

**Do not assume 50 visible edits will be approved.** The recommendation deliberately does not depend on it.

---

## 6. Predeclared evidence oracle

A probe (Strategy C, or C+B) must assert exactly this set; any deviation is an abort, not a soft pass.

Field names are deliberately labelled by evidence class (§7). Fields prefixed `loopback_` are **emulated
transport** counts (Strategy C) — they are NOT live-provider evidence and must never be reported without the
class tag.

| Field | Expected (default config, one turn) | Source of truth |
|---|---|---|
| `accepted_edit_count` | 50 admitted draft edits (`reserve_budget=False`) | loopback `/edit` POST count |
| `first_denied_attempt` | 51 | index of first `edit_message` returning `None` |
| `denial_count` | ≥ 1 (all further drafts this turn) | budget audit rows |
| `loopback_edit_posts` | **exactly 50** (the 51st makes **no** POST) — *emulated transport, not a live provider call* | loopback `/edit` `post_count` |
| `loopback_acks` | 50 × HTTP 200 from the loopback — *emulated, not a live provider ack* | loopback responses |
| `message_ids` | **exactly one distinct id** across all edits (*live-source-asserted*: the one-id echo is a Hermes `edit_message` property, §1a, not repo-verifiable; a C run controls the id it feeds) | `SendResult.message_id` echo |
| `audit_rows` | one decisions-log row of type **`send_budget_exhausted`** per denial, `reason=draft_exhausted` | decisions-log delta (`_emit_turn_send_budget_suppressed` → `_try_emit_audit_row("send_budget_exhausted", …)`, `safe_io.py:2334-2343`) |
| `page_count` | **exactly 1** (once per turn, `budget.paged`), via the §12b **owner-alert** path (`notify_owner_with_fallback` — Pushover/Telegram primary; owner self-chat only if the primary FAILS) | `_page_turn_send_budget_exhausted` spy |
| `owner_page_provider_ops` | **0** when owner paging is stubbed to an internal loopback OR the primary owner channel succeeds; **1** owner self-chat WhatsApp op **iff** the primary FAILS — on the **owner** identity, separately audited, **never** on the customer edit chat and **never** counted in `loopback_edit_posts` | owner-alert spy branch + owner-send audit row |
| `before_state` | `count=0, draft_count=0` at turn freeze | `_TurnSendBudget` |
| `after_state` | `draft_count=50` (+ finalized `count` only if a finalize edit ran) | `_TurnSendBudget` |
| `cleanup` | tmp dirs removed; `_TURN_SEND_BUDGET` reset to `None`; no residual audit outside the tmp log | fixture teardown |
| **abort conditions** | any `/edit` POST for a denied attempt · > 1 distinct `message_id` · `page_count != 1` · a denied draft reaching the loopback · (pre-provider tests) any provider-visible **edit/customer** send · denial reason `exhausted` when `draft_exhausted` was expected · an owner self-chat fallback that lands on the customer edit chat or inflates `loopback_edit_posts` · owner paging reaching a real provider when the probe required it stubbed to loopback | assertion failure ⇒ abort |

**Page-vs-customer separation (mirror the existing R6 guard).** The one budget-exhaustion page routes
through the §12b `notify_owner_with_fallback` owner-alert path (`safe_io.py:2353-2395`) to the **owner**, not
the customer edit chat. On its **primary** branch (Pushover/Telegram) it makes **no WhatsApp provider call**;
only if the primary FAILS does it fall back to **one** owner self-chat WhatsApp op — on the owner identity,
separately audited, never on the customer edit chat and never counted as an edit. So the page is insulated
from the `loopback_edit_posts`/`exactly 50` count by both a different mechanism (primary channel) **and** a
different destination (owner, not customer) — mirroring the `/send` harness's `expected_chat_id` guard
(`transport_evidence_diagnostic.py:228-238`), which excludes an owner page from the provider canary precisely
because it targets a different identity. The page can never inflate the `exactly 50` **edit** count; the
separate `owner_page_provider_ops` field (above) accounts for the fallback branch's single owner op so the
probe never has to pretend the fallback is impossible. To claim **zero provider operations of any kind**, the
probe must stub owner paging to an internal loopback (recommended for Strategy C).

---

## 7. Evidence-class labelling (never claim equivalence without establishing it)

Every claim a probe emits MUST carry its class. The four classes are **not** interchangeable:

| Class | What it means | Which strategy supplies it | Honest claim |
|---|---|---|---|
| **Real-provider edit proof** | a real WhatsApp message was edited in place via the real `/edit` boundary | A (full), B (small positive control) | "real edits reach the provider and modify one message" (A: at 50; B: at ≤ 3) |
| **Pre-provider gate proof** | the cap denial occurs before any provider call (`edit_message` returns `None`, no POST) | C, B (boundary portion) | "cap denial is pre-provider and lands at the draft ceiling" |
| **Configuration / default proof** | the feature is OFF by default; caps are 5 / 50 / 500 with fail-closed arming | C (asserts the frozen config) + source (§1c) | "default-OFF; draft ceiling = 50; fail-closed when armed" |
| **Simulated / emulated evidence** | behaviour observed against a loopback `/edit`, not a live provider | C | "cap 50 enforced **at the gate** (emulated transport)" — **not** "cap 50 live-proven" |

**Honesty boundary (load-bearing):** **"cap 50 live-proven" is establishable ONLY by Strategy A** (50 real
edits + a real 51st denial through the real provider). Strategy C proves "cap 50 enforced at the gate";
Strategy B's control proves "real edits reach the provider + edit in place (small N)". Neither C, nor B,
nor C+B, equals "cap 50 live-proven." A probe/report must not state or imply that equivalence.

---

## 8. Scope reminder (design only)

Producing or reviewing this document authorizes nothing. Implementing the probe, executing any edit
(real or emulated) against production, enabling the TE control socket / transport budget / harness, using
a real customer identity, or beginning Stage A/B all remain **out of scope** and require separate
operator authorization.
