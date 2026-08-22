"""`get_pending_catering_approvals` — owner-facing read over state/catering-leads.json.

Thin adapter, mirroring `compliance_tool` and `equipment_tool`. It owns identity,
authorization, a validated store read and date arithmetic. It owns no language
beyond the deterministic zero-state replies below.

WHY THIS TOOL EXISTS. cf-router's F8 arm already executes `#XXXXX approve` /
`#XXXXX reject` deterministically for the catering-leads pool
(`src/plugins/cf-router/hooks.py::_try_f8_intercept`). The code is the actionable
half of that loop, and until now nothing could tell the owner WHICH codes were
still open — the owner had to still be holding the approval card WhatsApp sent
them, in some cases months ago. This tool surfaces the open codes; F8 executes
them. It does NOT approve anything itself and no catering write path is touched.

WHICH LEADS COUNT AS PENDING is derived from the deployed state machine, not
hardcoded: any `CateringLeadStatus` whose `CATERING_TRANSITIONS` row can reach
`OWNER_APPROVED` is a lead the owner still has to decide on. Today that derives
{AWAITING_OWNER_APPROVAL, CUSTOMER_FINALIZED} — and the second one matters,
because a customer who has locked in their menu selection is waiting on the owner
just as much as a freshly drafted quote is. Hardcoding the one obvious literal
would have under-reported live production by 2 of 5. `test_pending_statuses_*`
pins the derived set so a schema change surfaces as a failing test rather than as
a silently different answer.

THE STATE DISTINCTION IS THE POINT — the same four SUCCESSFUL outcomes the
sibling tools draw, for the same reason, plus the config gate above them:

  disabled   — cfg.catering.enabled is false, or the block is absent and defaults
               to false. The store is never read; the owner is told catering is
               not switched on, which is NOT "nothing waiting on you".
  missing    — no leads file. Nothing is configured, so coverage is UNKNOWN and
               this state carries NO counts and NO leads list.
  empty      — file present, zero leads. Configured, no leads at all.
  populated  — leads exist. `pending_total` may be 0, which means none of the
               RECORDED leads is waiting on the owner — not that no customer is.

Everything else FAILS CLOSED. An unreadable store and an unresolvable customer
timezone are not empty results — they return `ok: false` with no counts.

Each zero state binds a deterministic reply to the exact turn via safe_io, and a
failure to bind suppresses the payload entirely. Zeros here are money-adjacent:
"no catering leads are waiting on you" told to an owner with three open quotes is
how a booking is lost. Positive rows bind nothing; Hermes keeps presentation
ownership there.

Rows omit `customer_phone` — and `raw_inquiry`, `quote_text` and `selected_items`
with it. The question is which decisions are outstanding; the customer's number
is not part of that answer, and a routine "what's waiting?" read is the last place
a phone number should be widened into.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

from .identity import fail, ok, refuse, require_owner

TOOLSET = "shift_agent_read"
TOOL_NAME = "get_pending_catering_approvals"

DESCRIPTION = (
    "Read the authenticated owner's catering leads and return the ones still "
    "waiting on the owner's approve/reject decision, oldest first, each with its "
    "#XXXXX approval code. Use for questions about catering leads, catering "
    "quotes awaiting sign-off, what needs the owner's approval, or which "
    "catering inquiries are outstanding. This is separate from the compliance "
    "calendar and the equipment list.\n"
    "\n"
    "SCOPE. This tool knows only what is in the RECORDED catering lead store. It "
    "is never evidence about catering enquiries outside that store.\n"
    "  - 'disabled': catering is switched off for this business, so nothing was "
    "checked. This is NOT a statement that nothing is waiting.\n"
    "  - 'missing': the catering lead store is not configured and coverage is "
    "unavailable. This does NOT establish that no decisions are outstanding.\n"
    "  - 'empty': the store is configured and holds zero leads. That means no "
    "leads are recorded, NOT that no customer has enquired.\n"
    "  - 'populated' with pending_total 0: no RECORDED lead can be APPROVED. "
    "Check awaiting_redraft_total before saying nothing is waiting on the "
    "owner. Do not generalize beyond the recorded store.\n"
    "\n"
    "TWO LISTS, AND THEY ARE NOT INTERCHANGEABLE. `leads` holds decisions the "
    "owner can close right now by replying with the code. `awaiting_redraft` "
    "holds leads the owner already sent an edit on: still the owner's to "
    "resolve, but the quote must be redrafted before they can be approved, and "
    "those rows deliberately carry NO approval code because the apply-script "
    "refuses both approve and reject from that state. Never offer a code for an "
    "awaiting_redraft lead, never invent one, and do not call it approvable. "
    "Nothing automated moves these along — only a new message from the "
    "customer does — so if the owner is surprised one is sitting there, that "
    "is real.\n"
    "\n"
    "RULES. Report only the leads, codes, ages and totals this tool returned — "
    "never invent a lead, an approval code or a quote total, and say a lead is "
    "not recorded rather than guessing. Give the owner the #XXXXX code with "
    "each lead IN `leads`: replying '#XXXXX approve' or '#XXXXX reject' in "
    "this chat is how the decision is actually applied. A lead with no code — "
    "which is every awaiting_redraft row — cannot be decided that way; say so "
    "rather than inventing one. `quote_total_usd` of null means no "
    "total has been computed yet, NOT a $0 quote. This tool is READ-ONLY: it "
    "cannot approve, reject, edit or send anything — if the owner asks you to "
    "action a lead, tell them to reply with the code rather than implying it was "
    "done. Never read out a customer's phone number; this tool deliberately does "
    "not return one."
)

# Deterministic replies bound to the turn for every zero state. Bounded strings,
# never model-generated. Positive rows get none — Hermes presents real leads.
TPL_DISABLED = (
    "Catering isn't enabled for this business yet, so I can't tell you which "
    "catering leads are waiting on your approval."
)
TPL_MISSING = (
    "The catering lead store isn't configured, so I can't determine which "
    "catering leads are waiting on your approval."
)
TPL_EMPTY = (
    "Your catering lead store is configured, but it currently holds no leads "
    "at all."
)
TPL_POPULATED_ZERO = (
    "None of the {leads_total} catering leads on record are currently waiting "
    "on your approval."
)
# The same zero, when it would be FALSE said plainly: leads you edited are still
# yours to resolve, they just cannot be approved with their code until the quote
# is redrafted, and nothing automated will move them.
TPL_POPULATED_ZERO_REDRAFT = (
    "None of the {leads_total} catering leads on record can be approved right "
    "now, but {redraft_total} of them are still waiting on you after your edit: "
    "each needs its quote redrafted before it can be approved, and the approval "
    "code will not work until then."
)

# registry.register() takes the INNER function object; get_definitions() adds the
# {"type":"function","function":...} wrap. Double-wrapping silently empties
# function.description, which strips the tool's line from the deferred catalog
# and makes it undiscoverable.
#
# No parameters at all. "What is waiting on me?" has no dimension worth letting a
# model express, and every parameter is a way for it to narrow an answer the owner
# did not ask to have narrowed. Volume is handled by MAX_ROWS below, whose
# truncation is reported rather than silent.
SCHEMA = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

# Counts are always exact; only the row list is capped. A cap that changed the
# counts would answer "how many are waiting on me?" wrongly, which is the one
# number the owner acts on.
MAX_ROWS = 25

PLATFORM_DIR = str(Path(__file__).resolve().parent.parent.parent / "platform")
CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH",
                                  "/opt/shift-agent/config.yaml"))
# Env-overridable literal, matching the sibling tools rather than importing
# catering_paths — which has no leads entry and is deliberately not
# env-overridable (it serves the write-side scripts, which monkeypatch the
# consuming module attribute instead). cf-router's actions.LEADS_PATH holds the
# same literal.
LEADS_PATH = Path(os.environ.get("SHIFT_AGENT_CATERING_LEADS_PATH",
                                 "/opt/shift-agent/state/catering-leads.json"))


def _ensure_platform_path() -> None:
    """Idempotently put the platform modules on sys.path — same guarded pattern
    cf-router uses, so repeated calls don't grow sys.path."""
    for p in ("/opt/shift-agent", PLATFORM_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def _config():
    """The validated Config, or None when it cannot be read."""
    try:
        import yaml
        from schemas import Config
        return Config.model_validate(
            yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        return None


def _today(cfg) -> date | None:
    """Today in the CUSTOMER's timezone, or None if that cannot be established.

    No UTC fallback. `age_days` is the number the owner acts on ("this one has
    been sitting 74 days"); deriving it from a guessed timezone would produce an
    authoritative-looking number from an unknown basis. Callers fail closed.
    """
    override = os.environ.get("SHIFT_AGENT_NOW_OVERRIDE", "")
    if override:
        try:
            return datetime.fromisoformat(override).date()
        except ValueError:
            return None
    try:
        from safe_io import customer_now
        return customer_now(cfg.customer.timezone).date()
    except Exception:
        return None


def pending_statuses() -> frozenset[str] | None:
    """Statuses the owner still has to decide on, DERIVED from the deployed
    state machine: every status whose transition row can reach OWNER_APPROVED.

    Public so the test suite can pin the derivation against the schema rather
    than against a copy of it. Returns None when the table cannot be read —
    callers must fail closed rather than fall back to a guessed literal, because
    a guess that is too narrow silently under-reports open money decisions.
    """
    try:
        from schemas import CATERING_TRANSITIONS
        return frozenset(
            status for status, allowed in CATERING_TRANSITIONS.items()
            if "OWNER_APPROVED" in allowed
        )
    except Exception:
        return None


def awaiting_redraft_statuses() -> frozenset[str] | None:
    """Statuses where the owner still owns the outcome but CANNOT approve —
    DERIVED, like the set above, from the same table: a row that can still reach
    OWNER_REJECTED but can NOT reach OWNER_APPROVED. Today that is
    {OWNER_EDITED}, and the two sets are disjoint.

    THIS EXISTS BECAUSE THE FIRST SET ALONE PRODUCED A FALSE STATEMENT. An owner
    who replies `#XXXXX edit make it 150 guests` parks the lead in OWNER_EDITED
    (apply-catering-owner-decision:1002). That status cannot reach OWNER_APPROVED,
    so `pending_statuses()` excludes it — correctly, for its own purpose — and the
    lead became invisible while TPL_POPULATED_ZERO told the owner in bound,
    egress-substituted text that nothing was waiting. The deployed dead-man
    watchdog disagrees with that: catering-owner-action-watchdog:362 treats
    {AWAITING_OWNER_APPROVAL, OWNER_EDITED} as the owner-owes-an-action set.

    IT IS A SEPARATE SET, NOT AN ADDITION TO THE FIRST, because the two are
    operationally different. apply-catering-owner-decision:810-830 accepts
    approve from {AWAITING_OWNER_APPROVAL, CUSTOMER_FINALIZED, OWNER_APPROVED}
    and reject/edit from {AWAITING_OWNER_APPROVAL, CUSTOMER_FINALIZED} —
    OWNER_EDITED is in NEITHER matcher (the R2-H1 fix: retry-approve on the same
    code would ship the un-edited quote). Widening `pending_statuses()` to cover
    it would trade a false negative for a false instruction, telling the owner to
    send a code the executor will refuse.

    OWNER_EDITED is a RESTING state, not a transient one: its only exits are
    customer-driven (finalize-catering-menu:1026, amend-catering-lead:118), and
    nothing in the deployed tree — including the TTL sweep, since the table has no
    OWNER_EDITED -> STALE edge — moves it on its own.
    """
    try:
        from schemas import CATERING_TRANSITIONS
        return frozenset(
            status for status, allowed in CATERING_TRANSITIONS.items()
            if "OWNER_REJECTED" in allowed and "OWNER_APPROVED" not in allowed
        )
    except Exception:
        return None


def _bind_outbound(text: str) -> bool:
    """Bind the deterministic reply to THIS turn. Must run inside the handler.

    `HERMES_SESSION_ID` is reassigned by `AIAgent.run_conversation`, so a key
    read any earlier will not match the one the WhatsApp adapter resolves at
    egress — and that mismatch fails by letting unqualified text through.
    """
    _ensure_platform_path()
    try:
        from safe_io import register_turn_outbound_override
        return bool(register_turn_outbound_override(text))
    except Exception:
        return False


def _age_days(when: datetime, today: date) -> int:
    """Whole days between `when` and today.

    Both `created_at` and `updated_at` are REQUIRED on CateringLead, so there is
    no unset case to defend against here. Stored timestamps already carry the
    customer's UTC offset (the catering scripts write them through
    `customer_now`), so the naive `.date()` is the customer's calendar date,
    matching `today`.
    """
    return (today - when.date()).days


def handler(args=None, **kwargs) -> str:
    """Model arguments arrive positionally and carry no identity — see identity.py."""
    authorized, refusal = require_owner()
    if not authorized:
        return refusal

    _ensure_platform_path()

    cfg = _config()
    if cfg is None:
        # Whether catering is enabled could not be established. Reporting "not
        # enabled" here would be a claim about configuration we failed to read,
        # and reporting anything else would read the store without a gate.
        return fail("config_unavailable")
    if not cfg.catering.enabled:
        # CateringConfig.enabled defaults False and an absent block validates to
        # that default, so an un-onboarded box lands here and the store is never
        # read.
        if not _bind_outbound(TPL_DISABLED):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="disabled", coverage_status="not_enabled")

    # Existence is checked BEFORE loading so `missing` can never be reported as
    # `empty`: CateringLeadStore validates from {} into a zero-lead store, so a
    # loader-first order would turn "no source at all" into "no leads", which is
    # exactly the collapse the owner must never be shown.
    if not LEADS_PATH.exists():
        if not _bind_outbound(TPL_MISSING):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="missing", coverage_status="not_configured")

    from safe_io import load_model
    from schemas import CateringLeadStore
    try:
        store, _ = load_model(LEADS_PATH, CateringLeadStore)
    except Exception:
        # Corrupt or schema-invalid store. NOT an empty result — open decisions
        # may well exist and simply be unreadable right now.
        return fail("state_unreadable")

    if not store.leads:
        if not _bind_outbound(TPL_EMPTY):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="empty", leads_total=0, pending_total=0,
                  awaiting_redraft_total=0, returned=0, truncated=False,
                  leads=[], awaiting_redraft=[])

    statuses = pending_statuses()
    redraft_statuses = awaiting_redraft_statuses()
    if statuses is None or redraft_statuses is None:
        return fail("state_machine_unavailable")

    today = _today(cfg)
    if today is None:
        return fail("customer_timezone_unavailable")

    def _row(lead, with_code: bool) -> dict:
        """One lead. `with_code` is FALSE for the redraft list, and that omission
        is structural rather than an instruction: Hermes cannot offer a code it
        was never given, so a lead the executor would refuse cannot be turned
        into "reply #XXXXX approve" by a model trying to be helpful."""
        row = {
            "lead_id": lead.lead_id,
            "status": lead.status,
            "created_at": lead.created_at.isoformat(),
            "updated_at": lead.updated_at.isoformat(),
            "age_days": _age_days(lead.created_at, today),
            "days_since_update": _age_days(lead.updated_at, today),
            "quote_total_usd": lead.quote_total_usd,
            "customer_name": lead.customer_name or "",
            "deposit_status": lead.deposit_status,
            "on_hold": lead.on_hold,
        }
        if with_code:
            row["owner_approval_code"] = lead.owner_approval_code
        return row

    def _bucket(wanted, with_code: bool) -> list:
        return sorted(
            (_row(lead, with_code) for lead in store.leads
             if lead.status in wanted),
            key=lambda r: r["age_days"],
            reverse=True,   # oldest first — the longest wait leads
        )

    pending = _bucket(statuses, with_code=True)
    # Owner-owed but NOT approvable by code. Carried in its own list, without the
    # code, so it can never be presented as an approvable decision.
    redraft = _bucket(redraft_statuses, with_code=False)

    if not pending:
        # Leads exist but none is the owner's to APPROVE. Which of the two zero
        # sentences applies depends on the redraft bucket, because
        # TPL_POPULATED_ZERO is a false statement whenever that bucket is
        # non-empty — and it is bound text, substituted verbatim at egress, so
        # Hermes cannot soften it.
        if redraft:
            text = TPL_POPULATED_ZERO_REDRAFT.format(
                leads_total=len(store.leads), redraft_total=len(redraft))
        else:
            text = TPL_POPULATED_ZERO.format(leads_total=len(store.leads))
        if not _bind_outbound(text):
            return refuse("outbound_truthfulness_guard_unavailable")
    # Positive rows bind nothing: Hermes owns presenting real pending leads.
    return ok(source_status="populated",
              leads_total=len(store.leads),
              pending_total=len(pending),
              awaiting_redraft_total=len(redraft),
              returned=len(pending[:MAX_ROWS]),
              truncated=len(pending) > MAX_ROWS,
              leads=pending[:MAX_ROWS],
              awaiting_redraft=redraft[:MAX_ROWS])
