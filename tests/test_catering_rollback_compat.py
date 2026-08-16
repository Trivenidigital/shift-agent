"""P0-2 — the Catering Studio release must be rollback-able after it has written state.

THE BUG THIS FILE PINS
----------------------
The Studio MVP and P17 added 20 fields and 2 statuses (``QUALIFYING``, ``BOOKED``) to
``CateringLead``, and ``expires_at`` to ``CateringProposalSet``. Both models are
``extra="forbid"`` in the PREVIOUS release (``dc7a81a2``), and its
``CateringLeadStatus`` Literal names neither new status. So ONE lead written by
the new release makes ``catering-leads.json`` unreadable by the old code after a
tarball rollback — the old ``load_model`` raises, and cf-router's tolerant
raw-JSON lookups silently degrade to "no lead" for a live customer.

``catering-state-downgrade`` is the reverse migration. This file proves it works
by materialising the OLD release's schema module out of git and validating
against IT — not against a hand-written approximation of it, which would pass
happily while the real rollback still broke.

THE OLD MODULE IS LOADED IN A SUBPROCESS, ALWAYS. ``git show
dc7a81a2:src/platform/schemas.py`` defines classes with the SAME names as the
live ``schemas`` module this test session has already imported. Loading it
in-process — even under an isolated ``sys.modules`` name — puts two
``CateringLead`` classes in one interpreter, and any Pydantic rebuild or
``TypeAdapter`` cache that resolves by name can pick the wrong one. A subprocess
gets a clean interpreter and sidesteps that class of failure entirely; the cost
is one process launch per validation.

ORDERING IS LOAD-BEARING, as in tests/test_catering_studio_e2e.py. The cells share
ONE module-scoped sandbox and run as a transcript: an old-format store (test_01) is
written into by the new release (test_03..05), shown to be unreadable by the old one
(test_06), downgraded (test_08) and validated again (test_09). Run the FILE, not a
single cell. The numeric prefixes are the execution order.

CROSS-PLATFORM: runs on Linux AND Windows. The in-process layer uses the
``fixtures_fleet`` fcntl stub (advisory locking is irrelevant in a single test
process); the subprocess layer only ever loads the old ``schemas`` module, which
has no ``fcntl`` dependency of its own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, posix_fs_identity, read_log_rows

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLATFORM = SRC / "platform"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
TEMPLATES_SRC = SRC / "agents" / "catering" / "templates"
for _p in (str(PLATFORM), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DOWNGRADE_SCRIPT = SCRIPTS / "catering-state-downgrade"

# The release the downgrade targets. Kept as a literal (not imported from the
# script) so a careless edit to the script's constant fails a test instead of
# silently re-pointing the whole suite at a different baseline.
OLD_RELEASE = "dc7a81a2"

CUSTOMER_PHONE = "+19045550199"
SEED_PHONE = "+19045550177"
SEED_LEAD_ID = "L9001"
SEED_CODE = "#AB2CD"


# ── the OLD release's schema module, materialised out of git ─────────────────
@pytest.fixture(scope="module")
def old_schemas(tmp_path_factory) -> Path:
    """`git show dc7a81a2:src/platform/schemas.py` on disk. Skips (never fails)
    when the object is unreachable — a shallow clone is an environment problem,
    not a regression in the code under test."""
    out = tmp_path_factory.mktemp("old_release") / "old_schemas.py"
    try:
        proc = subprocess.run(
            ["git", "show", f"{OLD_RELEASE}:src/platform/schemas.py"],
            cwd=str(REPO), capture_output=True, text=True,
        )
    except FileNotFoundError:  # no git binary — same class as a shallow clone
        pytest.skip("git is not available; cannot materialise the old release")
    if proc.returncode != 0:
        pytest.skip(f"{OLD_RELEASE}:src/platform/schemas.py unreachable: {proc.stderr[:200]}")
    out.write_text(proc.stdout, encoding="utf-8")
    return out


_VALIDATOR = '''
import importlib.machinery, importlib.util, json, sys

old_path, model_name, store_path = sys.argv[1], sys.argv[2], sys.argv[3]
loader = importlib.machinery.SourceFileLoader("old_release_schemas", old_path)
spec = importlib.util.spec_from_loader("old_release_schemas", loader)
mod = importlib.util.module_from_spec(spec)
sys.modules["old_release_schemas"] = mod
loader.exec_module(mod)

payload = json.loads(open(store_path, encoding="utf-8").read())
try:
    getattr(mod, model_name).model_validate(payload)
except Exception as e:
    print("INVALID: %s" % e)
    sys.exit(1)
print("VALID")
'''


def validate_under_old_release(old_schemas: Path, model_name: str, store_path: Path):
    """Validate a store file against the OLD release's model, in a clean
    interpreter. Returns (ok, output)."""
    script = old_schemas.parent / "validate_under_old.py"
    if not script.exists():
        script.write_text(_VALIDATOR, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), str(old_schemas), model_name, str(store_path)],
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def old_release_symbol(old_schemas: Path, expr: str) -> object:
    """Evaluate `expr` against the OLD schema module in a clean interpreter and
    return the JSON-decoded result. Used by the drift pins so they compare the
    script's constants with the REAL old release, not with a copy of themselves."""
    script = old_schemas.parent / f"probe_{abs(hash(expr))}.py"
    script.write_text(
        "import importlib.machinery, importlib.util, json, sys\n"
        "from typing import get_args\n"
        f"loader = importlib.machinery.SourceFileLoader('old_release_schemas', {str(old_schemas)!r})\n"
        "spec = importlib.util.spec_from_loader('old_release_schemas', loader)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['old_release_schemas'] = mod\n"
        "loader.exec_module(mod)\n"
        f"print(json.dumps({expr}))\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, f"probe {expr!r} failed: {proc.stderr[:500]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── sandbox ─────────────────────────────────────────────────────────────────
class _Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.logs = root / "logs"
        self.templates = root / "templates"
        self.config = root / "config.yaml"
        self.leads = self.state / "catering-leads.json"
        self.proposals = self.state / "catering-proposals.json"
        self.ledger = self.state / "catering-quote-ledger.json"
        self.log = self.logs / "decisions.log"
        self.lead_id = ""


def _old_format_lead(lead_id: str, status: str, phone: str) -> dict:
    """A lead written the way dc7a81a2 wrote them — old fields ONLY.

    Validated against the old models before the new release ever touches it (see
    test_01), so the round trip starts from a genuinely old fixture rather than
    from something merely asserted to be one.
    """
    return {
        "lead_id": lead_id, "status": status,
        "customer_phone": phone, "customer_name": "Seed Customer",
        "raw_inquiry": "(seed) reception dinner for 60",
        "original_message_id": f"wamid.SEED.{lead_id}",
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "extracted": {
            "headcount": 60, "event_date": "2026-09-30", "event_time": None,
            "menu_preferences": [], "dietary_restrictions": [],
            "delivery_or_pickup": "unknown", "budget_hint_usd": None,
            "notes": "seed", "off_menu_items": [],
        },
        "selected_items": [{"name": "Idly (3 PCS)", "qty": 60, "price_usd": 6}],
        "quote_total_usd": 360,
        "quote_text": "Seed quote already sent to the seed customer.",
        "quote_version": 1,
        "owner_approval_code": SEED_CODE,
        "deposit_required": True,
        "deposit_amount_cents": 9000,
        "deposit_status": "awaiting_payment",
        "deposit_payment_reference": "SEEDREF-1",
    }


def _build_sandbox(root: Path) -> _Sandbox:
    import yaml

    sb = _Sandbox(root)
    for d in (sb.state, sb.logs, sb.templates):
        d.mkdir(parents=True, exist_ok=True)
    for f in TEMPLATES_SRC.iterdir():
        if f.is_file():
            (sb.templates / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    sb.config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "customer": {"name": "Rollback Compat", "location_id": "loc_p02",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {}, "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }), encoding="utf-8")
    # The store starts in OLD format: one SENT_TO_CUSTOMER lead the new release
    # will book, exactly as a real rollback window starts.
    sb.leads.write_text(json.dumps({
        "schema_version": 1,
        "leads": [_old_format_lead(SEED_LEAD_ID, "SENT_TO_CUSTOMER", SEED_PHONE)],
    }, indent=2), encoding="utf-8")
    sb.proposals.write_text(json.dumps(
        {"schema_version": 1, "next_sequence": 1, "sets": []}), encoding="utf-8")
    sb.log.write_text("", encoding="utf-8")
    return sb


@pytest.fixture(scope="module")
def sb(tmp_path_factory) -> _Sandbox:
    """ONE sandbox for the whole file — the tests are an ordered transcript
    (old fixture -> new release writes -> downgrade -> validate)."""
    return _build_sandbox(tmp_path_factory.mktemp("catering_rollback_compat"))


@pytest.fixture(autouse=True)
def _env(sb: _Sandbox, monkeypatch):
    for key, value in {
        "SHIFT_AGENT_CONFIG_PATH": str(sb.config),
        "SHIFT_AGENT_STATE_DIR": str(sb.state),
        "SHIFT_AGENT_LEADS_PATH": str(sb.leads),
        "SHIFT_AGENT_CATERING_LEADS_PATH": str(sb.leads),
        "SHIFT_AGENT_CATERING_PROPOSALS_PATH": str(sb.proposals),
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_PATH": str(sb.ledger),
        "SHIFT_AGENT_LOG_PATH": str(sb.log),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(sb.log),
        "SHIFT_AGENT_TEMPLATE_DIR": str(sb.templates),
    }.items():
        monkeypatch.setenv(key, value)
    # POSIX: the ledger enforces an owner:group contract defaulting to the
    # deployed `shift-agent` identity; the sandbox is owned by the pytest user,
    # so pass the runner's identity through (inert off-POSIX). Same pattern as
    # every other suite that drives the ledger writers on Linux CI.
    owner, group = posix_fs_identity()
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER", owner)
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP", group)


_SANDBOX_ATTRS = ("CONFIG_PATH", "LEADS_PATH", "LEADS_LOCK", "LOG_PATH", "LOG_LOCK",
                  "PROPOSALS_PATH", "PROPOSALS_LOCK", "LEDGER_PATH", "TEMPLATE_DIR",
                  "NOTIFY_OWNER_BIN", "NOTIFY_OWNER")
_run_seq = [0]


def _run_script(sb: _Sandbox, script_name: str, argv: list[str]):
    """Load a catering script fresh, bind its paths to the sandbox, run main().

    Mirrors tests/test_catering_studio_e2e.py::_run_script — the same in-process
    pattern that lets the REAL scripts run on Windows.
    """
    import io
    from contextlib import redirect_stderr, redirect_stdout

    _run_seq[0] += 1
    mod = load_script(f"p02_{script_name.replace('-', '_')}_{_run_seq[0]}",
                      SCRIPTS / script_name)
    values = {
        "CONFIG_PATH": sb.config,
        "LEADS_PATH": sb.leads, "LEADS_LOCK": Path(str(sb.leads) + ".lock"),
        "LOG_PATH": sb.log, "LOG_LOCK": Path(str(sb.log) + ".lock"),
        "PROPOSALS_PATH": sb.proposals,
        "PROPOSALS_LOCK": Path(str(sb.proposals) + ".lock"),
        "LEDGER_PATH": sb.ledger,
        "TEMPLATE_DIR": sb.templates,
        "NOTIFY_OWNER_BIN": sb.state / "no-such-notify-owner",
        "NOTIFY_OWNER": str(sb.state / "no-such-notify-owner"),
    }
    for name in _SANDBOX_ATTRS:
        if hasattr(mod, name):
            setattr(mod, name, values[name])
    old_argv = sys.argv
    sys.argv = [script_name, *argv]
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    return rc, out.getvalue(), err.getvalue()


def _leads(sb: _Sandbox) -> list[dict]:
    return json.loads(sb.leads.read_text(encoding="utf-8"))["leads"]


def _lead(sb: _Sandbox, lead_id: str) -> dict:
    return next(le for le in _leads(sb) if le["lead_id"] == lead_id)


def _sidecars(sb: _Sandbox, stem: str) -> list[Path]:
    return sorted(sb.state.glob(f"{stem}.downgrade-sidecar-*.json"))


# ═════════════════════════════════════════════════════════════════════════════
# 1. The fixture is genuinely OLD-format (proves the round trip starts honest).
# ═════════════════════════════════════════════════════════════════════════════
def test_01_seed_store_validates_under_the_old_release(sb: _Sandbox, old_schemas: Path):
    ok, output = validate_under_old_release(old_schemas, "CateringLeadStore", sb.leads)
    assert ok, f"the seed fixture is not genuinely old-format: {output}"


# ═════════════════════════════════════════════════════════════════════════════
# 2. Drift pins — the script's constants must match the REAL old/new delta.
# ═════════════════════════════════════════════════════════════════════════════
def test_02_stripped_field_list_matches_the_real_schema_delta(old_schemas: Path):
    """If a later PR adds a 13th field to CateringLead and forgets the downgrade
    script, this fails — which is the whole point. A hand-maintained list that
    nothing checks is exactly how the rollback breaks again."""
    from schemas import CateringLead

    script = load_script("p02_downgrade_constants", DOWNGRADE_SCRIPT)
    old_fields = set(old_release_symbol(
        old_schemas, "sorted(mod.CateringLead.model_fields.keys())"))
    new_fields = set(CateringLead.model_fields.keys())

    assert set(script.LEAD_FIELDS_UNKNOWN_TO_OLD) == new_fields - old_fields, (
        "LEAD_FIELDS_UNKNOWN_TO_OLD has drifted from the real new-minus-old delta; "
        f"missing={sorted((new_fields - old_fields) - set(script.LEAD_FIELDS_UNKNOWN_TO_OLD))} "
        f"stale={sorted(set(script.LEAD_FIELDS_UNKNOWN_TO_OLD) - (new_fields - old_fields))}"
    )
    assert not (old_fields - new_fields), (
        "the old release has a field the new one dropped — the downgrade would "
        "have to RESTORE it, which this script does not do")


def test_02b_proposal_stripped_field_list_matches_the_real_delta(old_schemas: Path):
    from schemas import CateringProposalSet

    script = load_script("p02_downgrade_constants_b", DOWNGRADE_SCRIPT)
    old_fields = set(old_release_symbol(
        old_schemas, "sorted(mod.CateringProposalSet.model_fields.keys())"))
    new_fields = set(CateringProposalSet.model_fields.keys())
    assert set(script.PROPOSAL_FIELDS_UNKNOWN_TO_OLD) == new_fields - old_fields


def test_02c_status_map_covers_every_new_status_and_targets_a_legal_old_one(
    old_schemas: Path,
):
    from typing import get_args

    from schemas import CateringLeadStatus

    script = load_script("p02_downgrade_constants_c", DOWNGRADE_SCRIPT)
    old_statuses = set(old_release_symbol(
        old_schemas, "sorted(get_args(mod.CateringLeadStatus))"))
    new_statuses = set(get_args(CateringLeadStatus))

    assert set(script.LEAD_STATUS_DOWNGRADE) == new_statuses - old_statuses, (
        "every status the old release lacks needs a mapping, and no mapping may "
        "name a status the new release no longer has")
    for source, target in script.LEAD_STATUS_DOWNGRADE.items():
        assert target in old_statuses, (
            f"{source} maps to {target}, which the old release does not define")

    # The two semantic choices, pinned so a later edit is a deliberate one.
    assert script.LEAD_STATUS_DOWNGRADE["QUALIFYING"] == "NEW", (
        "QUALIFYING is pre-quote and pre-approval; NEW is the old release's only "
        "pre-quote workable status. AWAITING_OWNER_APPROVAL would trip the old "
        "quote_text sentinel backfill and queue a quote-less lead for approval.")
    assert script.LEAD_STATUS_DOWNGRADE["BOOKED"] == "CLOSED"


def test_02d_proposal_status_map_covers_every_new_status_and_targets_a_legal_old_one(
    old_schemas: Path,
):
    """The pin that was missing. ``PROPOSAL_FIELDS_UNKNOWN_TO_OLD`` has been checked
    against the real delta since P0-2, but the proposal STATUS delta was checked by
    nothing at all — the proposals store ran with ``status_map={}``. M3 then added
    ``EXPIRED``, which ``select-catering-proposal`` writes UNGATED whenever a customer
    replies after the validity window, and a live set carries ``expires_at`` today. So
    one late reply was enough to make ``catering-proposals.json`` unreadable by the
    rollback target, and no test said so.

    This is the same shape as test_02c, one store over. Its value is not the mapping
    it asserts today — it is that the NEXT proposal status cannot be added without
    either a mapping or a deliberate, visible failure here.
    """
    from typing import get_args

    from schemas import CateringProposalStatus

    script = load_script("p02_downgrade_constants_d", DOWNGRADE_SCRIPT)
    old_statuses = set(old_release_symbol(
        old_schemas, "sorted(get_args(mod.CateringProposalStatus))"))
    new_statuses = set(get_args(CateringProposalStatus))
    new_only = new_statuses - old_statuses

    assert set(script.PROPOSAL_STATUS_DOWNGRADE) == new_only, (
        "every proposal status the old release lacks needs a mapping, and no mapping "
        "may name a status the new release no longer has; "
        f"missing={sorted(new_only - set(script.PROPOSAL_STATUS_DOWNGRADE))} "
        f"stale={sorted(set(script.PROPOSAL_STATUS_DOWNGRADE) - new_only)}"
    )
    for source, target in script.PROPOSAL_STATUS_DOWNGRADE.items():
        assert target in old_statuses, (
            f"{source} maps to {target}, which the old release does not define")
        # dc7a81a2's select-catering-proposal claims a set only when its status is
        # exactly "SENT" (both `_latest_proposal_for_lead` and `_claim_selection`),
        # so anything else is non-selectable there. A downgrade target that WERE
        # selectable would hand the old release a set it must not act on.
        assert target != "SENT", (
            f"{source} maps to {target}, which the old release treats as selectable")

    # The two semantic choices, pinned so a later edit is a deliberate one.
    assert script.PROPOSAL_STATUS_DOWNGRADE["EXPIRED"] == "SUPERSEDED", (
        "an EXPIRED set really was sent and is now dead; SUPERSEDED is the old "
        "release's terminal for exactly that, and it does not claim the send failed")
    assert script.PROPOSAL_STATUS_DOWNGRADE["SEND_UNCERTAIN"] == "SEND_FAILED", (
        "the old release has no way to express uncertainty, and its only terminal "
        "for a set that never confirmed is SEND_FAILED. The sidecar keeps the real "
        "status, so the downgrade is lossy only to the OLD reader, never on disk")


# ═════════════════════════════════════════════════════════════════════════════
# 3. The NEW release reads the old store and writes new-format state into it.
# ═════════════════════════════════════════════════════════════════════════════
# The state write and the WhatsApp send are separate concerns in these scripts: the
# lead is durable before anything is sent. safe_io refuses every bridge send from a
# pytest context (EXIT_DEPENDENCY_DOWN), which is exactly the protection this suite
# wants — so the send-failure exit is accepted and the assertions are made against
# the STATE the script wrote, which is all this file is about.
_WROTE_STATE = (0, 6)


def test_03_new_release_creates_a_lead_with_new_fields(sb: _Sandbox):
    """The REAL create-catering-lead — so the M1 fields are serialized by
    production code, not by this test's idea of what production writes."""
    rc, out, err = _run_script(sb, "create-catering-lead", [
        "--customer-phone", CUSTOMER_PHONE, "--customer-name", "Priya",
        "--raw-inquiry", "Need catering for a party",
        "--message-id", "wamid.P02.1",
        "--fields-json", json.dumps({"headcount": None, "event_date": None}),
        "--suppress-customer-ack", "--qualification-gate",
    ])
    assert rc in _WROTE_STATE, (
        f"create-catering-lead exit={rc}\nstdout={out[:800]}\nstderr={err[:800]}")

    created = [le for le in _leads(sb) if le["lead_id"] != SEED_LEAD_ID]
    assert len(created) == 1, f"expected exactly one new lead, got {created}"
    lead = created[0]
    sb.lead_id = lead["lead_id"]
    assert lead["status"] == "QUALIFYING", (
        f"an under-qualified lead must park in QUALIFYING; got {lead['status']}")
    # The M1 fields the old release forbids are really on disk now.
    assert "qualification_rounds" in lead and "questions_asked" in lead


def test_04_new_release_books_the_seed_lead(sb: _Sandbox):
    """The REAL record-catering-acceptance — BOOKED plus the M3 acceptance fields."""
    rc, out, err = _run_script(sb, "record-catering-acceptance", [
        "--lead-id", SEED_LEAD_ID,
        "--customer-jid", f"{SEED_PHONE.lstrip('+')}@s.whatsapp.net",
        "--customer-message-id", "wamid.P02.ACCEPT",
        "--message-text", "yes, we accept - let's book it",
    ])
    assert rc in _WROTE_STATE, (
        f"record-catering-acceptance exit={rc}\nstdout={out[:800]}\nstderr={err[:800]}")

    lead = _lead(sb, SEED_LEAD_ID)
    assert lead["status"] == "BOOKED", f"status={lead['status']}"
    assert lead["customer_acceptance"] == "accepted"
    assert lead["acceptance_message_id"] == "wamid.P02.ACCEPT"


def test_05_seed_the_remaining_new_only_surfaces(sb: _Sandbox):
    """M2 pricing provenance + the M3 quote-validity stamp + a proposal set with
    ``expires_at`` + a ledger record carrying the M2 stamps.

    The pricing fields land through a direct write rather than the finalize
    pipeline: this file is about the DOWNGRADE, and reproducing the whole
    quote-composition path here would test M2 a second time while adding a large
    surface that can fail for reasons unrelated to rollback. What matters is that
    every field in ``LEAD_FIELDS_UNKNOWN_TO_OLD`` is genuinely present on disk
    before the downgrade runs — asserted at the end of this test.
    """
    import catering_quote_ledger

    doc = json.loads(sb.leads.read_text(encoding="utf-8"))
    for lead in doc["leads"]:
        if lead["lead_id"] != SEED_LEAD_ID:
            continue
        lead["quote_valid_until"] = "2026-09-15T00:00:00+00:00"
        lead["on_hold"] = True
        lead["hold_reason"] = "owner is renegotiating"
        lead["hold_set_at"] = "2026-07-05T00:00:00+00:00"
        lead["pricing_inputs"] = {
            "guest_count": 60, "package_id": None, "package_name": None,
            "package_price_per_person_cents": None, "package_min_guests": None,
            "line_items": [], "fees": [], "staff_count": None, "miles": None,
            "tax_rate_bps": 0, "currency": "USD",
            "pricebook_version": 3, "menu_version": 2,
            "subtotal_cents": 36000, "total_cents": 36000,
            "price_status": "exact", "flags": [],
        }
    sb.leads.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    # Three proposal sets, one per hazard the old CateringProposalSet presents:
    #   000001  the M3 `expires_at` field it forbids
    #   000002  EXPIRED  — a status its Literal does not name (M3)
    #   000003  SEND_UNCERTAIN — likewise (P1), and with NO forbidden field, so the
    #           remap-only path through plan_downgrade is exercised too
    sb.proposals.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 4,
        "sets": [{
            "proposal_set_id": f"CPS-{SEED_LEAD_ID}-000001",
            "lead_id": SEED_LEAD_ID, "status": "SENT",
            "created_at": "2026-07-03T00:00:00+00:00",
            "sent_at": "2026-07-03T00:05:00+00:00",
            "expires_at": "2026-07-17T00:05:00+00:00",
            "outbound_message_id": "wamid.P02.SET",
            "source_message_id": "wamid.P02.REQ",
            "request_text": "send me some options",
            "options": [
                {"option_id": "1", "style_key": "classic", "tier": "classic",
                 "item_names": ["Idly (3 PCS)"]},
                {"option_id": "2", "style_key": "balanced", "tier": "balanced",
                 "item_names": ["Masala Dosa"]},
            ],
            "selected_option_id": None, "failure_reason": "",
        }, {
            "proposal_set_id": f"CPS-{SEED_LEAD_ID}-000002",
            "lead_id": SEED_LEAD_ID, "status": "EXPIRED",
            "created_at": "2026-06-01T00:00:00+00:00",
            "sent_at": "2026-06-01T00:05:00+00:00",
            "expires_at": "2026-06-15T00:05:00+00:00",
            "outbound_message_id": "wamid.P02.OLDSET",
            "source_message_id": "wamid.P02.OLDREQ",
            "request_text": "options for the June party",
            "options": [
                {"option_id": "1", "style_key": "classic", "tier": "classic",
                 "item_names": ["Idly (3 PCS)"]},
                {"option_id": "2", "style_key": "balanced", "tier": "balanced",
                 "item_names": ["Masala Dosa"]},
            ],
            "selected_option_id": None,
            "failure_reason": "validity window elapsed before selection",
        }, {
            "proposal_set_id": f"CPS-{SEED_LEAD_ID}-000003",
            "lead_id": SEED_LEAD_ID, "status": "SEND_UNCERTAIN",
            "created_at": "2026-07-04T00:00:00+00:00",
            "sent_at": None, "outbound_message_id": "",
            "source_message_id": "wamid.P02.UNCREQ",
            "request_text": "one more set of options",
            "options": [
                {"option_id": "1", "style_key": "classic", "tier": "classic",
                 "item_names": ["Idly (3 PCS)"]},
                {"option_id": "2", "style_key": "balanced", "tier": "balanced",
                 "item_names": ["Masala Dosa"]},
            ],
            "selected_option_id": None,
            "failure_reason": 'empty_message_id: {"accepted": true}',
        }],
    }, indent=2), encoding="utf-8")

    # A ledger record with the M2 stamps — the store the audit says is SAFE, so the
    # downgrade must leave it alone AND the old release must still read it.
    result = catering_quote_ledger.append_version(
        lead_id=SEED_LEAD_ID, quote_text="Seed quote already sent to the seed customer.",
        quote_total_usd=360,
        selected_items=[{"name": "Idly (3 PCS)", "qty": 60, "price_usd": 6}],
        source="customer_finalize", source_message_id="wamid.P02.LEDGER",
        approval_code=SEED_CODE, data_path=sb.ledger,
        menu_version=2, pricebook_version=3, price_status="exact",
    )
    # append_version reports failure in its RESULT rather than raising, so an
    # unasserted call is a fixture that silently builds nothing.
    assert result.ok, f"ledger append refused: {result.reason}"
    ledger = json.loads(sb.ledger.read_text(encoding="utf-8"))
    assert ledger["records"], "the ledger fixture did not append"
    assert ledger["records"][0]["price_status"] == "exact", (
        "the M2 stamp must really be on the ledger record for the safety claim to mean anything")

    # Every field the old release forbids is genuinely present somewhere on disk.
    script = load_script("p02_downgrade_precheck", DOWNGRADE_SCRIPT)
    present = set().union(*(set(le) for le in _leads(sb)))
    assert set(script.LEAD_FIELDS_UNKNOWN_TO_OLD) <= present, (
        "the fixture does not exercise every forbidden field: missing "
        f"{sorted(set(script.LEAD_FIELDS_UNKNOWN_TO_OLD) - present)}")


def test_06_the_new_state_is_unreadable_by_the_old_release(sb: _Sandbox, old_schemas: Path):
    """The bug itself, pinned. If this ever passes, P0-2 has been fixed some
    other way and the downgrade may be removable — but until then, THIS is the
    rollback blocker."""
    ok, output = validate_under_old_release(old_schemas, "CateringLeadStore", sb.leads)
    assert not ok, "expected the new-format lead store to be REJECTED by dc7a81a2"
    assert "extra_forbidden" in output or "literal_error" in output or "Extra inputs" in output, (
        f"rejected, but not for the reason P0-2 describes: {output[:600]}")

    ok, output = validate_under_old_release(old_schemas, "CateringProposalStore", sb.proposals)
    assert not ok, "expected the expires_at proposal set to be REJECTED by dc7a81a2"

    # The STATUS alone breaks the old reader, independently of expires_at — the
    # gap the proposals store's `status_map={}` left open. Probed on a store built
    # from the SEND_UNCERTAIN set, which carries no field the old model forbids.
    status_only = old_schemas.parent / "status_only_probe.json"
    sets = json.loads(sb.proposals.read_text(encoding="utf-8"))["sets"]
    uncertain = next(s for s in sets if s["status"] == "SEND_UNCERTAIN")
    assert not (set(uncertain) & set(("expires_at",))), (
        "this probe only proves anything while the set carries no forbidden field")
    status_only.write_text(json.dumps(
        {"schema_version": 1, "next_sequence": 2, "sets": [uncertain]}), encoding="utf-8")
    ok, output = validate_under_old_release(old_schemas, "CateringProposalStore", status_only)
    assert not ok, "expected SEND_UNCERTAIN alone to be REJECTED by dc7a81a2"
    assert "literal_error" in output or "Input should be" in output, (
        f"rejected, but not by the status Literal: {output[:600]}")


# ═════════════════════════════════════════════════════════════════════════════
# 4. --dry-run mutates nothing.
# ═════════════════════════════════════════════════════════════════════════════
def test_07_dry_run_mutates_nothing(sb: _Sandbox):
    before_leads = sb.leads.read_text(encoding="utf-8")
    before_proposals = sb.proposals.read_text(encoding="utf-8")
    before_log = sb.log.read_text(encoding="utf-8")

    rc, out, err = _run_script(sb, "catering-state-downgrade", ["--dry-run"])
    assert rc == 0, f"dry-run exit={rc}; stderr={err[:600]}"
    assert "would_downgrade" in out, f"dry-run said nothing about what it would do: {out}"

    assert sb.leads.read_text(encoding="utf-8") == before_leads
    assert sb.proposals.read_text(encoding="utf-8") == before_proposals
    assert sb.log.read_text(encoding="utf-8") == before_log, (
        "a dry run must not write an audit row either")
    assert not _sidecars(sb, "catering-leads"), "a dry run must not write a sidecar"


# ═════════════════════════════════════════════════════════════════════════════
# 5. The migration itself, and the proof it restores old-release readability.
# ═════════════════════════════════════════════════════════════════════════════
def test_08_downgrade_runs(sb: _Sandbox):
    rc, out, err = _run_script(sb, "catering-state-downgrade", [])
    assert rc == 0, f"downgrade exit={rc}\nstdout={out[:800]}\nstderr={err[:800]}"
    assert "downgraded catering-leads.json" in out
    assert "downgraded catering-proposals.json" in out


def test_09_downgraded_state_validates_under_the_old_release(sb: _Sandbox, old_schemas: Path):
    """THE test. Only the materialised dc7a81a2 models are used."""
    ok, output = validate_under_old_release(old_schemas, "CateringLeadStore", sb.leads)
    assert ok, f"downgraded lead store still unreadable by {OLD_RELEASE}: {output}"

    ok, output = validate_under_old_release(old_schemas, "CateringProposalStore", sb.proposals)
    assert ok, f"downgraded proposal store still unreadable by {OLD_RELEASE}: {output}"

    assert sb.ledger.exists(), (
        "the ledger fixture from test_05 is missing — run this file as a whole, "
        "not cell by cell (see the module docstring on ordering)")
    ok, output = validate_under_old_release(old_schemas, "CateringQuoteLedgerStore", sb.ledger)
    assert ok, (
        "the quote ledger was audited as rollback-SAFE (tolerant list[dict] records); "
        f"if this fails the audit was wrong and the ledger needs downgrading too: {output}")


def test_10_no_data_was_lost(sb: _Sandbox):
    leads = _leads(sb)
    assert {le["lead_id"] for le in leads} == {SEED_LEAD_ID, sb.lead_id}, (
        "every lead must survive the downgrade")

    booked = _lead(sb, SEED_LEAD_ID)
    qualifying = _lead(sb, sb.lead_id)
    assert booked["status"] == "CLOSED", "BOOKED must map to the old terminal"
    assert qualifying["status"] == "NEW", "QUALIFYING must map to the old pre-quote status"

    # The money-bearing old-schema fields are byte-identical through the round trip.
    seed = _old_format_lead(SEED_LEAD_ID, "SENT_TO_CUSTOMER", SEED_PHONE)
    for field in ("quote_text", "quote_version", "owner_approval_code", "customer_phone",
                  "raw_inquiry", "original_message_id", "selected_items",
                  "quote_total_usd", "deposit_required", "deposit_amount_cents",
                  "deposit_status", "deposit_payment_reference"):
        assert booked[field] == seed[field], (
            f"{field} changed through the downgrade: {booked[field]!r} != {seed[field]!r}")

    # `extracted` is compared sub-key by sub-key, because M1 added five fields to
    # CateringLeadExtractedFields (event_type, venue, service_style,
    # veg_guest_count, nonveg_guest_count) and the downgrade deliberately LEAVES
    # THEM. It can: that nested model is extra="ignore" in BOTH releases, so the old
    # code reads the store cleanly with them present (test_09 proves it) — they are
    # not part of the P0-2 blocker and stripping live intake data to fix a problem
    # that does not exist would be the wrong trade.
    # KNOWN CONSEQUENCE, documented in docs/runbooks/catering-rollback.md: extra=
    # "ignore" drops on READ, so once the old release rewrites a lead those five
    # sub-fields are gone from disk for good. That is post-rollback erosion by the
    # OLD binary, not loss caused by this migration.
    for sub_key, expected in seed["extracted"].items():
        assert booked["extracted"][sub_key] == expected, (
            f"extracted.{sub_key} changed through the downgrade: "
            f"{booked['extracted'][sub_key]!r} != {expected!r}")

    # Timestamps are compared as INSTANTS, not as strings. `created_at` leaves the
    # sandbox as "...+00:00" and comes back as "...Z" — that re-spelling is done by
    # the NEW release's Pydantic serializer when record-catering-acceptance rewrites
    # the store, BEFORE the downgrade runs, and both spellings parse to the same
    # instant under the old models (test_09 validates the result against them).
    # Asserting string equality here would pin a serializer detail this migration
    # does not own and cannot control.
    for field in ("created_at",):
        assert datetime.fromisoformat(booked[field]) == \
            datetime.fromisoformat(seed[field]), (
                f"{field} moved in time through the downgrade: "
                f"{booked[field]!r} != {seed[field]!r}")

    # Everything stripped is recoverable from the sidecar.
    sidecars = _sidecars(sb, "catering-leads")
    assert len(sidecars) == 1, f"expected exactly one lead sidecar, got {sidecars}"
    payload = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert payload["target_release"] == OLD_RELEASE
    recovered = {row["id"]: row for row in payload["records"]}
    assert set(recovered) == {SEED_LEAD_ID, sb.lead_id}

    assert recovered[SEED_LEAD_ID]["original_status"] == "BOOKED"
    assert recovered[sb.lead_id]["original_status"] == "QUALIFYING"
    stripped = recovered[SEED_LEAD_ID]["stripped_fields"]
    assert stripped["customer_acceptance"] == "accepted"
    assert stripped["acceptance_message_id"] == "wamid.P02.ACCEPT"
    assert stripped["hold_reason"] == "owner is renegotiating"
    assert stripped["pricing_inputs"]["total_cents"] == 36000, (
        "the cents-exact pricing provenance must be recoverable in full")

    # No forbidden field is left on any lead on disk.
    script = load_script("p02_downgrade_postcheck", DOWNGRADE_SCRIPT)
    for lead in leads:
        assert not (set(script.LEAD_FIELDS_UNKNOWN_TO_OLD) & set(lead)), (
            f"lead {lead['lead_id']} still carries forbidden fields")

    # The proposal sidecar keeps the validity window the customer was told, and the
    # real status of every set the old release cannot name.
    prop_sidecars = _sidecars(sb, "catering-proposals")
    assert len(prop_sidecars) == 1
    prop_payload = json.loads(prop_sidecars[0].read_text(encoding="utf-8"))
    prop_recovered = {row["id"]: row for row in prop_payload["records"]}
    assert prop_recovered[f"CPS-{SEED_LEAD_ID}-000001"]["stripped_fields"]["expires_at"] == \
        "2026-07-17T00:05:00+00:00"
    assert prop_recovered[f"CPS-{SEED_LEAD_ID}-000002"]["original_status"] == "EXPIRED"
    assert prop_recovered[f"CPS-{SEED_LEAD_ID}-000003"]["original_status"] == "SEND_UNCERTAIN", (
        "an uncertain send is recoverable as uncertain; the downgrade must not be "
        "the thing that turns it into a definite failure on disk")

    # ...and on disk the sets now carry statuses the old release names.
    prop_sets = {row["proposal_set_id"]: row
                 for row in json.loads(sb.proposals.read_text(encoding="utf-8"))["sets"]}
    assert prop_sets[f"CPS-{SEED_LEAD_ID}-000002"]["status"] == "SUPERSEDED"
    assert prop_sets[f"CPS-{SEED_LEAD_ID}-000003"]["status"] == "SEND_FAILED"
    assert prop_sets[f"CPS-{SEED_LEAD_ID}-000003"]["failure_reason"] == \
        'empty_message_id: {"accepted": true}', (
            "the ack evidence is old-release-legal and must survive the downgrade")


def test_11_audit_rows_record_the_downgrade(sb: _Sandbox):
    rows = [r for r in read_log_rows(sb.log) if r.get("type") == "catering_state_downgraded"]
    assert len(rows) == 2, f"one row per downgraded store expected, got {len(rows)}"
    by_store = {r["store"]: r for r in rows}
    assert set(by_store) == {"catering-leads.json", "catering-proposals.json"}

    leads_row = by_store["catering-leads.json"]
    assert leads_row["target_release"] == OLD_RELEASE
    assert leads_row["records_total"] == 2
    assert leads_row["records_remapped"] == 2, "both BOOKED and QUALIFYING were remapped"
    assert leads_row["records_stripped"] >= 1
    assert leads_row["sidecar"] == _sidecars(sb, "catering-leads")[0].name, (
        "the row must name the sidecar that holds what it stripped")


def test_12_old_release_tolerates_the_new_audit_row_type(sb: _Sandbox, old_schemas: Path):
    """The audit row is written by the NEW release and then read by the OLD one.
    dc7a81a2 routes unknown `type` tags to _UnknownLogEntry (extra="allow"), so the
    row is preserved rather than failing the old reader's parse. If that were not
    true, writing the row before the rollback would poison the old release's log
    reads — the opposite of an audit trail."""
    row = next(r for r in read_log_rows(sb.log) if r.get("type") == "catering_state_downgraded")
    probe = old_schemas.parent / "row_probe.json"
    probe.write_text(json.dumps(row), encoding="utf-8")

    script = old_schemas.parent / "validate_row_under_old.py"
    script.write_text(
        "import importlib.machinery, importlib.util, json, sys\n"
        "from pydantic import TypeAdapter\n"
        f"loader = importlib.machinery.SourceFileLoader('old_release_schemas', {str(old_schemas)!r})\n"
        "spec = importlib.util.spec_from_loader('old_release_schemas', loader)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['old_release_schemas'] = mod\n"
        "loader.exec_module(mod)\n"
        "row = json.loads(open(sys.argv[1], encoding='utf-8').read())\n"
        "entry = TypeAdapter(mod.LogEntry).validate_python(row)\n"
        "print(type(entry).__name__)\n"
        "print(entry.type)\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script), str(probe)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        f"{OLD_RELEASE} could not parse the downgrade audit row: {proc.stderr[:600]}")
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "_UnknownLogEntry", f"expected the forward-compat variant, got {lines}"
    assert lines[1] == "catering_state_downgraded", (
        "the original tag must round-trip so audit tooling can still find the row")


def test_12b_every_tag_the_new_release_adds_degrades_under_the_old_union(old_schemas: Path):
    """The same guarantee as test_12, for the CLASS instead of one instance.

    test_12 pins one tag that someone remembered to pin. Every audit tag added
    since dc7a81a2 has the same rollback contract — write it before the
    rollback, and the old reader must absorb it rather than fail the parse — and
    hand-verification that never becomes a test is how a gap survives for
    months. So the delta is computed rather than listed: every tag the new
    union knows and the old one does not is probed against the OLD union.

    A minimal `{type, ts}` payload is the right probe: the old release has no
    model for these tags, so it routes them by tag alone to `_UnknownLogEntry`
    (`type: str`, extra="allow") and never sees the new model's required fields.
    That is exactly the property under test — if a tag ever failed to route, it
    would raise here regardless of payload.
    """
    from schemas import _KNOWN_LOG_ENTRY_TYPES as new_tags

    probe = old_schemas.parent / "new_tags_probe.json"
    probe.write_text(json.dumps(sorted(new_tags)), encoding="utf-8")

    script = old_schemas.parent / "validate_new_tags_under_old.py"
    script.write_text(
        "import importlib.machinery, importlib.util, json, sys\n"
        "from pydantic import TypeAdapter\n"
        f"loader = importlib.machinery.SourceFileLoader('old_release_schemas', {str(old_schemas)!r})\n"
        "spec = importlib.util.spec_from_loader('old_release_schemas', loader)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['old_release_schemas'] = mod\n"
        "loader.exec_module(mod)\n"
        "new_tags = json.loads(open(sys.argv[1], encoding='utf-8').read())\n"
        "new_only = sorted(set(new_tags) - set(mod._KNOWN_LOG_ENTRY_TYPES))\n"
        "adapter = TypeAdapter(mod.LogEntry)\n"
        "out = {}\n"
        "for tag in new_only:\n"
        "    try:\n"
        "        entry = adapter.validate_python(\n"
        "            {'type': tag, 'ts': '2026-08-15T12:00:00+00:00'})\n"
        "        out[tag] = [type(entry).__name__, entry.type]\n"
        "    except Exception as e:\n"
        "        out[tag] = ['RAISED', f'{type(e).__name__}: {e}'[:200]]\n"
        "print(json.dumps(out))\n",
        encoding="utf-8",
    )
    proc = subprocess.run([sys.executable, str(script), str(probe)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        f"the {OLD_RELEASE} tag-delta probe failed: {proc.stderr[:600]}")
    results = json.loads(proc.stdout.strip().splitlines()[-1])

    # A pin that covers nothing passes silently, so anchor it on tags this
    # branch added. If a later release removes them, this list is the reminder
    # to re-anchor rather than to delete the cell.
    for anchor in ("catering_deposit_link_send_unconfirmed",
                   "catering_deposit_reinvoke_refused",
                   "catering_deposit_delivery_reconciled"):
        assert anchor in results, (
            f"{anchor} is not in the new-vs-{OLD_RELEASE} tag delta; the probe "
            f"is covering nothing. Delta was: {sorted(results)}")

    bad = {tag: got for tag, got in results.items()
           if got[0] != "_UnknownLogEntry" or got[1] != tag}
    assert not bad, (
        f"tags added since {OLD_RELEASE} that the old union does not absorb "
        f"cleanly: {bad}. Writing one of these before a rollback poisons the "
        f"old release's log reads.")


def test_13_the_new_release_can_read_its_own_downgrade_output(sb: _Sandbox):
    """Re-upgrade sanity. Every stripped field is Optional/defaulted on the new
    models, so the downgraded store loads clean under the NEW schemas too — an
    operator who runs the migration and then does NOT roll back is not stranded."""
    from safe_io import load_model
    from schemas import CateringLeadStore, CateringProposalStore

    store, status = load_model(sb.leads, CateringLeadStore, default=None)
    assert status == "ok", f"new release cannot read the downgraded store: {status}"
    assert {le.lead_id for le in store.leads} == {SEED_LEAD_ID, sb.lead_id}

    proposals, status = load_model(sb.proposals, CateringProposalStore, default=None)
    assert status == "ok"
    assert proposals.sets[0].expires_at is None, (
        "the stripped window must read back as None, not as a stale value")


# ═════════════════════════════════════════════════════════════════════════════
# 6. Idempotency + the crash window.
# ═════════════════════════════════════════════════════════════════════════════
def test_14_rerun_is_a_no_op(sb: _Sandbox):
    before_leads = sb.leads.read_text(encoding="utf-8")
    before_proposals = sb.proposals.read_text(encoding="utf-8")
    rows_before = len(read_log_rows(sb.log))
    sidecars_before = _sidecars(sb, "catering-leads") + _sidecars(sb, "catering-proposals")

    rc, out, err = _run_script(sb, "catering-state-downgrade", [])
    assert rc == 0, f"re-run exit={rc}; stderr={err[:600]}"
    assert "already downgraded" in out, f"a re-run must say so plainly: {out}"
    assert "nothing to do" in out

    assert sb.leads.read_text(encoding="utf-8") == before_leads
    assert sb.proposals.read_text(encoding="utf-8") == before_proposals
    assert len(read_log_rows(sb.log)) == rows_before, "a no-op must not write an audit row"
    assert _sidecars(sb, "catering-leads") + _sidecars(sb, "catering-proposals") == \
        sidecars_before, "a no-op must not write another sidecar"


def test_15_crash_between_sidecar_and_store_is_recoverable(tmp_path, monkeypatch):
    """Simulate the crash window: the sidecar is durable but the store was never
    rewritten. A re-run must complete against the untouched store — the orphaned
    sidecar is harmless because the re-run recomputes from the original data.

    A fresh sandbox, because this cell needs a store that is still new-format.
    """
    sb2 = _build_sandbox(tmp_path / "crash")
    for key, value in {
        "SHIFT_AGENT_CATERING_LEADS_PATH": str(sb2.leads),
        "SHIFT_AGENT_CATERING_PROPOSALS_PATH": str(sb2.proposals),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(sb2.log),
    }.items():
        monkeypatch.setenv(key, value)

    doc = json.loads(sb2.leads.read_text(encoding="utf-8"))
    doc["leads"][0]["status"] = "BOOKED"
    doc["leads"][0]["customer_acceptance"] = "accepted"
    doc["leads"][0]["on_hold"] = False
    sb2.leads.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    new_format_store = sb2.leads.read_text(encoding="utf-8")

    # The crash: a sidecar from an interrupted run exists; the store is untouched.
    orphan = sb2.state / "catering-leads.downgrade-sidecar-1750000000.json"
    orphan.write_text(json.dumps({
        "schema_version": 1, "created_at": "2026-07-30T00:00:00+00:00",
        "target_release": OLD_RELEASE, "source_store": "catering-leads.json",
        "records": [{"id": SEED_LEAD_ID, "stripped_fields": {}, "original_status": "BOOKED"}],
    }), encoding="utf-8")
    assert sb2.leads.read_text(encoding="utf-8") == new_format_store

    rc, out, err = _run_script(sb2, "catering-state-downgrade", [])
    assert rc == 0, f"re-run after a crash exit={rc}; stderr={err[:600]}"
    assert _lead(sb2, SEED_LEAD_ID)["status"] == "CLOSED", (
        "the re-run must complete the downgrade the crashed run did not finish")

    # The orphan is left alone; the completed run wrote its own.
    assert orphan.exists(), "an orphaned sidecar must not be deleted — it is evidence"
    fresh = [p for p in _sidecars(sb2, "catering-leads") if p != orphan]
    assert len(fresh) == 1, f"the completed run must write its own sidecar: {fresh}"
    assert json.loads(fresh[0].read_text(encoding="utf-8"))["records"][0][
        "original_status"] == "BOOKED"


def test_16_missing_store_is_a_clean_no_op(tmp_path, monkeypatch):
    """A box that never ran catering has no store at all. The migration must say
    so and exit 0 rather than creating an empty one."""
    state = tmp_path / "empty" / "state"
    state.mkdir(parents=True)
    logs = tmp_path / "empty" / "logs"
    logs.mkdir(parents=True)
    sb3 = _Sandbox(tmp_path / "empty")
    sb3.log.write_text("", encoding="utf-8")
    for key, value in {
        "SHIFT_AGENT_CATERING_LEADS_PATH": str(sb3.leads),
        "SHIFT_AGENT_CATERING_PROPOSALS_PATH": str(sb3.proposals),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(sb3.log),
    }.items():
        monkeypatch.setenv(key, value)

    rc, out, err = _run_script(sb3, "catering-state-downgrade", [])
    assert rc == 0, f"exit={rc}; stderr={err[:600]}"
    assert "missing" in out and "nothing to do" in out
    assert not sb3.leads.exists(), "a missing store must not be created by the migration"


# ═════════════════════════════════════════════════════════════════════════════
# 7. Static invariants.
# ═════════════════════════════════════════════════════════════════════════════
def test_17_script_compiles_and_is_executable():
    import py_compile

    py_compile.compile(str(DOWNGRADE_SCRIPT), doraise=True)
    if os.name == "posix":
        assert os.access(DOWNGRADE_SCRIPT, os.X_OK), "the operator runs this directly"


def test_18_the_stores_audited_as_safe_are_never_rewritten():
    """The followups / pricebook / amendments / ledger stores were audited as
    rollback-safe or never-read-by-the-old-release. Rewriting one would be churn
    on live money-adjacent data for no rollback benefit, so the script must not
    name them at all."""
    source = DOWNGRADE_SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]  # everything after the module docstring
    for store in ("catering-followups.json", "catering-pricebook.json",
                  "catering-amendments.json", "catering-quote-ledger.json"):
        assert store not in body, (
            f"{store} was audited as rollback-safe; the migration must not touch it")
