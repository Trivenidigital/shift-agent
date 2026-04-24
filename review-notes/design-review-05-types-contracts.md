# Design Review 5/5 — Types / Contracts (pr-review-toolkit:type-design-analyzer)

**Verdict:** No explicit BLOCKERs, but data models rate 2-3/5 on invariant expression/enforcement. ONE top recommendation that would lift the entire system.

## Top 5 concrete changes (ordered by ROI)

### 1. Create `/opt/shift-agent/schemas.py` with Pydantic models
Single source of truth for:
- `Config`, `Roster`, `Employee`, `ScheduleEntry`
- `Proposal` as discriminated union on `status` (each variant has different required fields)
- `PendingStore`, `SendCounter`, `SeenIds`
- `LogEntry` as discriminated union on `type`

Every script imports + validates on read, writes through `.model_dump()`. Lifts Encapsulation / InvariantExpression / Enforcement from 2 → 4 in one pass. ~1 day work.

### 2. Add `active: bool` / `status` + `phone_history` to Employee
Current schema has no way to mark an employee as terminated while keeping audit history. Roster drift = invisible. Add:
```python
class Employee(BaseModel):
    id: constr(regex=r"^e\d{3,}$")
    phone: E164Phone                  # custom type
    role: Role                         # enum
    can_cover_roles: frozenset[Role]  # enum set, not free list
    status: Literal["active","inactive","terminated"] = "active"
    phone_history: list[PhoneAssignment] = []
```

### 3. Discriminated union on `pending.proposal.status`
Biggest type smell. Currently `status` is a plain string; `sent` coexists with missing `sent_ts`; `send_failed` has no `last_error` requirement; `accepted` has no `response_ts`. Fix:
```python
class AwaitingProposal(BaseModel): status: Literal["awaiting_owner_approval"]; ...
class SentProposal(BaseModel): status: Literal["sent"]; sent_ts: datetime; rendered: str
class SendFailedProposal(BaseModel): status: Literal["send_failed"]; last_error: str; retry_count: int
Proposal = Annotated[Union[...], Field(discriminator="status")]
```

### 4. Canonicalize phone at one chokepoint
Bug farm: config.yaml owner is `+1-904-555-0100` (dashed), roster.json is `+19045550101` (E.164), identify-sender accepts JID variants. Any comparison requires normalize step; forgetting it = silent misidentification.
**Fix:** newtype `E164Phone` with `from_any(raw: str) -> E164Phone` constructor (handles +, 00, dashes, spaces, @s.whatsapp.net, @lid). Forbid raw `str` in cross-boundary function signatures. Store only canonical form in JSON.

Also WhatsApp migrating `@lid` — store BOTH PN-JID and LID-JID per employee.

### 5. Nightly `shift-agent-fsck` asserting cross-file invariants
Invariants that currently exist only in prose:
- Every `proposal_id` in decisions.log exists in pending.json (at some point)
- Every `proposal_created` eventually has a terminal `proposal_status_change`
- `send-counter.day` count == `outbound_sent` count in decisions.log for that day
- `raw_inbound` with `employee_id` → that id exists in roster at event time
- `code` unique among awaiting_owner_approval
- `seen-ids.last_offset_bytes` ≤ stat(agent.log).st_size

Dedicated script invoked nightly pre-backup; dumps failures to decisions.log as `type: invariant_violation`.

## Key findings detail

**`roster.json`**: missing `active`/`status`, `phone_history`, typed roles. `role` as free string breaks cross-reference with `can_cover_roles`.

**`pending.json`**: status is plain string not discriminated union. `status_history` lacks causality fields (should be `{from, to, ts, cause, actor, event_ref}` where event_ref → decisions.log).

**`decisions.log` entries**: Every line has `type` discriminator (good), but the union of `type` values is implicit — scripts add types ad-hoc without central registry. Need `decisions_schema.py` defining `LogEntry = Annotated[Union[RawInbound, ProposalCreated, ...], discriminator="type"]`. No `schema_version` field → no migration story.

**State machine (§9)**: Terminal states labeled in prose, not schema. Transitions not validated at write-time. `send_failed` drawn as endpoint but prose says "retry once" → ambiguous.

**Proposal codes (`#A3F2`)**: 4-char × 31 symbols = 923k codes. At 1000 distinct historical codes, birthday collision probability ~54%. **Fix:** 5 chars (28.6M, negligible collision) OR document "codes ephemeral, proposal_id is canonical in decisions.log."

**input_message**: no max_length, no encoding declaration. A 65k-char WhatsApp message breaks 4KB NDJSON line rule. Add `constr(max_length=4000)`, `truncated: bool` flag.

**config.yaml**: no schema validator. "Fails loudly if required missing" is per-script, inconsistent. Need `ConfigModel(BaseModel)` validated once.

## Ratings table

| Model | Encaps | Usefulness | InvExpr | Enforcement |
|---|---|---|---|---|
| roster.json | 3 | 4 | 2 | 2 |
| pending.json | 2 | 4 | 2 | 3 |
| send-counter.json | 4 | 4 | 3 | 3 |
| seen-ids.json | 4 | 4 | 3 | 3 |
| decisions.log NDJSON | 3 | 4 | 2 | 2 |
| config.yaml | 3 | 4 | 2 | 2 |

**Schemas.py is the single change that lifts every model from ~2.5 avg to ~4 avg on invariant/enforcement columns.**
