"""M7 — production-equivalent deterministic E2E: the WHOLE catering lifecycle.

One customer, one sandbox, one ordered transcript. Every step drives the REAL
production code — the real cf-router dispatch functions, the real catering
scripts (loaded in-process via `fixtures_fleet.load_script`), the real platform
kernels (pricing / quote-ledger / qualification / automation-control /
follow-ups) — against a sandboxed state dir. Deterministic inbound messages
stand in for live WhatsApp; NOTHING is mocked inside the business logic.

WHY THIS FILE EXISTS: M1..M6 each proved their own slice. M7 proves they
COMPOSE — that a lead qualified by M1, priced by M2, quoted by M3, controlled by
M4 and chased by M5 is the SAME lead, with one continuous audit timeline and no
duplicate sends. The per-milestone suites cannot catch a composition break; this
one can.

WHAT IS AND IS NOT STUBBED
  * NOT stubbed: every catering script, every kernel, the cf-router F7 +
    automation-control interceptors, the safe_io `bridge_post` chokepoint with
    its kill-switch / automation-control / action-context gates, the audit
    chokepoint, the quote ledger, the R2A amendment sidecar.
  * Stubbed (three seams, all OUTSIDE the business logic):
      1. `urllib.request.urlopen` — the WhatsApp transport. Every send that
         survives all the gates lands in `sb.sent`; a dropped send never gets
         there. This is the ONLY send seam, so kill-switch / suppression
         assertions hold uniformly for scripts and cf-router alike.
      2. `actions.lid_to_phone_via_identify_sender` — the identity subprocess.
      3. `actions.trigger_create_catering_lead` / `invoke_*` — cf-router's
         subprocess boundary, replaced by in-process runs of the SAME scripts
         against the sandbox (Windows has no fork/venv; the script code paths
         are identical either way).

DETERMINISM
  No LLM, no network, no sleeps. The clock is controlled the way the M5 suites
  control it — by writing `due_at` into the past rather than faking `now` (the
  sweep reads wall-clock `customer_now`), and quiet hours are disabled in the
  sandbox config (`quiet_hours_start == quiet_hours_end`) so the sweep's verdict
  does not depend on what time CI runs.

ORDERING IS LOAD-BEARING. The tests share ONE module-scoped sandbox and run as a
transcript: `test_02` opens the lead `test_04` answers, `test_08` sends the quote
`test_10` chases. Run them as a file, not individually. The numeric prefixes are
the execution order.

CROSS-PLATFORM: runs on Linux AND Windows, skipping nothing, via the
`fixtures_fleet` fcntl stub (advisory locking is irrelevant in a single test
process). The only platform difference is the quote-ledger / amendment-sidecar
POSIX owner:group check, which is inert off POSIX and is pointed at the CI
runner's own ids on Linux (see `_FS_OWNER`).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLATFORM = SRC / "platform"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
TEMPLATES_SRC = SRC / "agents" / "catering" / "templates"
MENU_FIXTURE = Path(__file__).resolve().parent / "e2e" / "fixtures" / "catering-menu-e2e.json"
PRICEBOOK_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catering_pricebook_valid.json"
for _p in (str(PLATFORM), str(SRC), str(REPO / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

import automation_control as ac  # noqa: E402
import catering_amendments  # noqa: E402
import catering_pricing  # noqa: E402
import catering_quote_ledger  # noqa: E402
import safe_io  # noqa: E402


def _fs_owner_group() -> tuple[str, str]:
    """The ids the ledger / amendment-sidecar POSIX fs check must expect. On the
    deployed VPS that is shift-agent:shift-agent; in a sandbox it is whoever owns
    the tmp dir (the CI runner). Off POSIX the check is inert."""
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


_FS_OWNER, _FS_GROUP = _fs_owner_group()

# ── the cast ─────────────────────────────────────────────────────────────────
CUSTOMER_PHONE = "+15550001111"
CUSTOMER_LID = "15550001111@lid"
CUSTOMER_JID = "15550001111@s.whatsapp.net"
OWNER_JID = "19045550100@s.whatsapp.net"
# A second, deliberately-seeded lead: the ambiguous-acceptance and per-lead-hold
# cells need a lead that is NOT the transcript's lead, so those assertions cannot
# be satisfied by accident from the main lifecycle's state.
OTHER_PHONE = "+15550002222"
OTHER_JID = "15550002222@s.whatsapp.net"
OTHER_LEAD_ID = "L9001"
OTHER_CODE = "#QRSTV"
# A third seeded lead, parked at CUSTOMER_FINALIZED: the per-lead-hold cell needs
# a lead the approve path would otherwise ACCEPT, so that the refusal it asserts
# is attributable to the hold and not to a status guard firing first.
HELD_PHONE = "+15550003333"
HELD_LEAD_ID = "L9002"
HELD_CODE = "#WXYZ2"
SEEDED_LEAD_IDS = (OTHER_LEAD_ID, HELD_LEAD_ID)

# The package the owner finalizes on. Chosen from the fixture pricebook so the
# expected total can be recomputed INDEPENDENTLY below (see test_07).
PACKAGE_ID = "veg-premium"
FINAL_HEADCOUNT = 160


# ── sandbox ──────────────────────────────────────────────────────────────────
class _Sandbox:
    """One state dir shared by the whole transcript, plus the accumulating
    transport sink. Module-scoped: this IS the running business."""

    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.logs = root / "logs"
        self.templates = root / "templates"
        self.config = root / "config.yaml"
        self.leads = self.state / "catering-leads.json"
        self.proposals = self.state / "catering-proposals.json"
        self.menu = self.state / "catering-menu.json"
        self.pricebook = self.state / "catering-pricebook.json"
        self.ledger = self.state / "catering-quote-ledger.json"
        self.followups = self.state / "catering-followups.json"
        self.amendments = self.state / "catering-amendments.json"
        self.control = self.state / "catering-automation-control.json"
        self.lid_cache = self.state / "lid-cache.json"
        self.dedupe = self.state / "cf-router-inbound-dedupe.json"
        self.disabled_flag = self.state / "disabled.flag"
        self.log = self.logs / "decisions.log"
        # Transport sink: every send that survives every gate.
        self.sent: list[dict] = []
        # Carried between ordered steps (ids minted by the real scripts).
        self.lead_id: str = ""
        self.approval_code: str = ""
        self.proposal_set_id: str = ""
        self.expected_total_cents: int = 0
        self.followup_code: str = ""


def _build_sandbox(root: Path) -> _Sandbox:
    sb = _Sandbox(root)
    for d in (sb.state, sb.logs, sb.templates):
        d.mkdir(parents=True, exist_ok=True)
    for f in TEMPLATES_SRC.iterdir():
        if f.is_file():
            (sb.templates / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Triveni Studio E2E", "location_id": "loc_m7",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": OWNER_JID},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
        # quiet_hours_start == quiet_hours_end disables the window entirely, so
        # the follow-up sweep's verdict does not depend on the hour CI runs.
        "catering_followup": {"enabled": True, "quiet_hours_start": 0, "quiet_hours_end": 0},
    }
    sb.config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    sb.leads.write_text(
        json.dumps({"schema_version": 1, "leads": [_other_lead(), _held_lead()]}, indent=2),
        encoding="utf-8")
    sb.proposals.write_text(json.dumps({"schema_version": 1, "next_sequence": 1, "sets": []}),
                            encoding="utf-8")
    sb.log.write_text("", encoding="utf-8")
    sb.lid_cache.write_text(
        json.dumps({"schema_version": 1,
                    "pairs": [{"phone": CUSTOMER_PHONE, "lid": CUSTOMER_LID}]}),
        encoding="utf-8")
    return sb


def _other_lead() -> dict:
    """The deliberately-seeded second lead (a different customer, already quoted)."""
    return {
        "lead_id": OTHER_LEAD_ID, "status": "SENT_TO_CUSTOMER",
        "customer_phone": OTHER_PHONE, "customer_name": "Second Customer",
        "raw_inquiry": "(seed) corporate lunch for 40",
        "original_message_id": "wamid.SEED.OTHER",
        "created_at": "2026-07-01T00:00:00+00:00", "updated_at": "2026-07-01T00:00:00+00:00",
        "extracted": {"headcount": 40, "event_date": "2026-09-30", "event_time": None,
                      "menu_preferences": [], "dietary_restrictions": [],
                      "delivery_or_pickup": "unknown", "budget_hint_usd": None,
                      "notes": "seed", "off_menu_items": []},
        "selected_items": [{"name": "Idly (3 PCS)", "qty": 40, "price_usd": 6}],
        "quote_total_usd": 240,
        "quote_text": "Seed quote already sent to the second customer.",
        "owner_approval_code": OTHER_CODE,
    }


def _held_lead() -> dict:
    """The third seeded lead: finalized and genuinely approvable, so the hold cell
    proves the HOLD refused it rather than a status guard."""
    return {
        "lead_id": HELD_LEAD_ID, "status": "CUSTOMER_FINALIZED",
        "customer_phone": HELD_PHONE, "customer_name": "Third Customer",
        "raw_inquiry": "(seed) office party for 30 on 2026-10-15",
        "original_message_id": "wamid.SEED.HELD",
        "created_at": "2026-07-02T00:00:00+00:00", "updated_at": "2026-07-02T00:00:00+00:00",
        "customer_finalized_at": "2026-07-02T00:05:00+00:00",
        "extracted": {"headcount": 30, "event_date": "2026-10-15", "event_time": None,
                      "menu_preferences": [], "dietary_restrictions": [],
                      "delivery_or_pickup": "unknown", "budget_hint_usd": None,
                      "notes": "seed", "off_menu_items": []},
        "selected_items": [{"name": "Masala Dosa", "qty": 30, "price_usd": 10}],
        "quote_total_usd": 300,
        "quote_text": "<v0.3-pre-quote-draft>",
        "owner_approval_code": HELD_CODE,
    }


# ── env: every store, flag and chokepoint pointed at the sandbox ─────────────
def _env_map(sb: _Sandbox) -> dict[str, str]:
    """The full production env surface, re-applied per test.

    Re-applied (not set once) because conftest's autouse isolations re-point
    SHIFT_AGENT_DECISIONS_LOG_PATH / notify paths at a fresh per-test tmp dir.
    Module-level autouse fixtures run AFTER conftest's, so this wins.
    """
    return {
        # paths
        "SHIFT_AGENT_CONFIG_PATH": str(sb.config),
        "SHIFT_AGENT_STATE_DIR": str(sb.state),
        "SHIFT_AGENT_LEADS_PATH": str(sb.leads),
        "SHIFT_AGENT_LEADS_LOCK": str(sb.leads) + ".lock",
        "SHIFT_AGENT_LOG_PATH": str(sb.log),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(sb.log),
        "SHIFT_AGENT_TEMPLATE_DIR": str(sb.templates),
        "SHIFT_AGENT_FOLLOWUPS_PATH": str(sb.followups),
        "SHIFT_AGENT_CATERING_AMENDMENTS_PATH": str(sb.amendments),
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_PATH": str(sb.ledger),
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER": _FS_OWNER,
        "SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP": _FS_GROUP,
        "SHIFT_AGENT_LID_CACHE_PATH": str(sb.lid_cache),
        "SHIFT_AGENT_DISABLED_FLAG": str(sb.disabled_flag),
        "CATERING_AUTOMATION_CONTROL_STATE_PATH": str(sb.control),
        # M4 controls — armed, wildcard-graduated (the deployed rollout target)
        "CATERING_AUTOMATION_CONTROL_ENABLED": "1",
        "CATERING_AUTOMATION_CONTROL_ALLOWLIST": "*",
        "CATERING_STOP_ENABLED": "1",
        "CATERING_TAKEOVER_ENABLED": "1",
        # M5 follow-ups — sweep armed, wildcard-graduated, owner-approval gated
        # (CATERING_FOLLOWUP_AUTOSEND deliberately left OFF: the owner card is
        # the shipped default and this transcript proves that path).
        "CATERING_FOLLOWUP_ENABLED": "1",
        "CATERING_FOLLOWUP_ALLOWLIST": "*",
        # M3 proposal sweep
        "CATERING_PROPOSAL_SWEEP_ENABLED": "1",
        # The transport is stubbed at urlopen, so the send path must be allowed to
        # reach it — otherwise bridge_post short-circuits before the kill-switch
        # and automation-control gates this file exists to prove.
        "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1",
    }


@pytest.fixture(scope="module")
def sb(tmp_path_factory) -> _Sandbox:
    """ONE sandbox for the whole transcript (see the module docstring on order)."""
    return _build_sandbox(tmp_path_factory.mktemp("catering_studio_e2e"))


@pytest.fixture(autouse=True)
def _e2e_env(sb: _Sandbox, monkeypatch):
    """Re-point every env-resolved path/flag at the shared sandbox, install the
    transport sink, and clear the module-global caches that would otherwise leak
    a previous step's answer into this one."""
    for key, value in _env_map(sb).items():
        monkeypatch.setenv(key, value)
    # The amendment sidecar has no env override for its POSIX owner check (the
    # ledger does). On Linux the sandbox is runner-owned, so point the constant at
    # the runner; monkeypatch restores it (catering_amendments is a SHARED module).
    monkeypatch.setattr(catering_amendments, "DEFAULT_OWNER", _FS_OWNER)
    monkeypatch.setattr(catering_amendments, "DEFAULT_GROUP", _FS_GROUP)
    # In-process caches that must not carry across steps.
    ac._LAST_KNOWN_MODE.clear()
    monkeypatch.setattr(ac, "_last_state_alert_monotonic", 0.0, raising=False)
    monkeypatch.setattr(safe_io, "_last_disabled_alert_monotonic", 0.0, raising=False)

    def _fake_urlopen(req, timeout=None):  # noqa: ARG001
        body = json.loads(req.data.decode("utf-8"))
        sb.sent.append({"jid": body["chatId"], "message": body["message"]})

        class _Resp:
            def read(self_inner):  # noqa: N805
                return json.dumps({"id": f"wamid.OUT.{len(sb.sent):04d}"}).encode("utf-8")

            def __enter__(self_inner):  # noqa: N805
                return self_inner

            def __exit__(self_inner, *a):  # noqa: N805
                return False

        return _Resp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        yield
    ac._LAST_KNOWN_MODE.clear()


# ── in-process script runner ─────────────────────────────────────────────────
_SANDBOX_ATTRS = (
    "CONFIG_PATH", "LEADS_PATH", "LEADS_LOCK", "LOG_PATH", "LOG_LOCK",
    "MENU_PATH", "MENU_LOCK", "PROPOSALS_PATH", "PROPOSALS_LOCK",
    "PRICEBOOK_PATH", "PRICEBOOK_LOCK", "LEDGER_PATH", "AMENDMENTS_PATH",
    "TEMPLATE_DIR", "ARCHIVE_DIR", "NOTIFY_OWNER_BIN",
)


def _bind_paths(mod, sb: _Sandbox):
    """Re-point a freshly-loaded script's module-level path constants at the
    sandbox. Only attributes the script actually declares are touched, so this
    one binder serves every script without asserting a shape it does not have."""
    values = {
        "CONFIG_PATH": sb.config,
        "LEADS_PATH": sb.leads, "LEADS_LOCK": Path(str(sb.leads) + ".lock"),
        "LOG_PATH": sb.log, "LOG_LOCK": Path(str(sb.log) + ".lock"),
        "MENU_PATH": sb.menu, "MENU_LOCK": Path(str(sb.menu) + ".lock"),
        "PROPOSALS_PATH": sb.proposals,
        "PROPOSALS_LOCK": Path(str(sb.proposals) + ".lock"),
        "PRICEBOOK_PATH": sb.pricebook,
        "PRICEBOOK_LOCK": Path(str(sb.pricebook) + ".lock"),
        "LEDGER_PATH": sb.ledger,
        "AMENDMENTS_PATH": sb.amendments,
        "TEMPLATE_DIR": sb.templates,
        "ARCHIVE_DIR": sb.state / "archive",
        # Deliberately non-existent: the Pushover bin is not on a test runner, and
        # every caller treats its absence as a best-effort miss.
        "NOTIFY_OWNER_BIN": sb.state / "no-such-notify-owner",
    }
    for name in _SANDBOX_ATTRS:
        if hasattr(mod, name):
            setattr(mod, name, values[name])
    return mod


_run_seq = [0]


def _run_script(sb: _Sandbox, script_name: str, argv: list[str], *, stdin: str = ""):
    """Load a catering script fresh, bind it to the sandbox, run its main().

    Returns (rc, stdout, stderr). A FRESH module per call on purpose: each
    invocation is its own process-of-record in production, and anything that only
    works because a previous run left module state behind is a bug this harness
    must not hide.
    """
    _run_seq[0] += 1
    mod = load_script(f"m7_{script_name.replace('-', '_')}_{_run_seq[0]}", SCRIPTS / script_name)
    _bind_paths(mod, sb)
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = [script_name, *argv]
    if stdin:
        sys.stdin = io.StringIO(stdin)
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin
    return rc, out.getvalue(), err.getvalue()


def _stdout_json(out: str) -> dict:
    """Last JSON object a script printed (scripts may emit INFO lines first)."""
    for line in reversed([ln for ln in out.splitlines() if ln.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


# ── cf-router plugin (real hooks + actions) ──────────────────────────────────
def _load_plugin():
    """Load hooks + actions as submodules of a synthetic package so `from .
    import actions` resolves (the plugin dir name has a hyphen).

    NON-EVICTING (mirrors tests/test_cf_router_qualification_loop.py, NOT
    test_cf_router_plugin.py): nothing is removed from sys.modules except this
    file's own package, so co-resident suites keep their `schemas` / `safe_io`
    bindings and this file can run everywhere instead of being Windows-skipped.
    """
    pkg = "cf_router_studio_e2e_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(spec)

    def _load(name):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        sp = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(sp)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    return _load("actions"), _load("hooks")


def _shim_create_lead(sb: _Sandbox):
    def _t(customer_phone, customer_name, raw_inquiry, message_id,
           extracted_fields=None, suppress_customer_ack=False, qualification_gate=False):
        fields = {"headcount": None, "event_date": None, "event_time": None,
                  "menu_preferences": [], "off_menu_items": [], "dietary_restrictions": [],
                  "delivery_or_pickup": "unknown", "budget_hint_usd": None, "notes": ""}
        if extracted_fields:
            fields.update(extracted_fields)
        argv = ["--customer-phone", customer_phone, "--customer-name", customer_name or "",
                "--raw-inquiry", raw_inquiry[:1000], "--message-id", message_id,
                "--fields-json", json.dumps(fields)]
        if suppress_customer_ack:
            argv.append("--suppress-customer-ack")
        if qualification_gate:
            argv.append("--qualification-gate")
        rc, out, err = _run_script(sb, "create-catering-lead", argv)
        if rc == 0:
            return True, (out.strip().splitlines()[-1] if out.strip() else "")
        return False, f"exit={rc} stderr={err[:500]}"
    return _t


def _shim_amend(sb: _Sandbox):
    def _a(lead_id, *, mode="amendment", answer_text="", message_id=""):
        argv = ["--lead-id", lead_id, "--mode", mode]
        if answer_text:
            argv += ["--answer-text", answer_text[:4000]]
        if message_id:
            argv += ["--message-id", message_id]
        return _run_script(sb, "amend-catering-lead", argv)[0]
    return _a


def _shim_create_proposals(sb: _Sandbox):
    def _i(lead_id, chat_id, message_id, text):
        return _run_script(sb, "create-catering-proposal-options", [
            "--lead-id", lead_id, "--customer-jid", chat_id,
            "--source-message-id", message_id, "--logical-turn-id", message_id,
            "--request-text", text, "--auto-generate-from-menu",
        ])[0]
    return _i


def _shim_select(sb: _Sandbox, finalize_calls: list):
    def _s(lead_id, chat_id, message_id, text):
        # select-catering-proposal shells out to finalize-catering-menu. Capture
        # that ONE subprocess boundary (finalize is driven for real, with the
        # owner's explicit --package-id, in test_07) and report success so the
        # REAL selection state machine advances.
        import subprocess as _sp
        real_run = _sp.run

        def _fake_run(argv, **kwargs):  # noqa: ARG001
            finalize_calls.append([str(p) for p in argv])
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        _sp.run = _fake_run
        try:
            return _run_script(sb, "select-catering-proposal", [
                "--lead-id", lead_id, "--customer-jid", chat_id,
                "--customer-message-id", message_id, "--selection-text", text,
            ])[0]
        finally:
            _sp.run = real_run
    return _s


@pytest.fixture(scope="module")
def cf(sb: _Sandbox):
    """The real cf-router, wired so its subprocess boundary runs the REAL scripts
    in-process against the sandbox. Module-scoped: one router for the transcript."""
    actions, hooks = _load_plugin()
    hooks.F7_PROPOSAL_BRANCH_ENABLED = True
    hooks.F7_PRIMARY_FOLLOWUP_REPLY = True
    hooks.F7_QUALIFICATION_GATE_ENABLED = True

    actions.CONFIG_PATH = sb.config
    actions.LEADS_PATH = sb.leads
    actions.PROPOSALS_PATH = sb.proposals
    actions.MENU_PATH = sb.menu
    actions.LOG_PATH = sb.log
    actions.PLATFORM_DIR = PLATFORM
    actions.CF_ROUTER_INBOUND_DEDUPE_PATH = sb.dedupe

    # Identity subprocess → deterministic. The ONLY identity seam.
    actions.lid_to_phone_via_identify_sender = (
        lambda cid: (OTHER_PHONE, "customer") if cid.startswith("15550002222")
        else (CUSTOMER_PHONE, "customer")
    )
    # Subprocess boundary → REAL scripts, in-process, same sandbox.
    finalize_calls: list = []
    actions.trigger_create_catering_lead = _shim_create_lead(sb)
    actions.invoke_amend_catering_lead = _shim_amend(sb)
    actions.invoke_create_catering_proposals = _shim_create_proposals(sb)
    actions.invoke_select_catering_proposal = _shim_select(sb, finalize_calls)
    return SimpleNamespace(hooks=hooks, actions=actions, finalize_calls=finalize_calls)


def _event(mid: str, chat_id: str = CUSTOMER_LID):
    return SimpleNamespace(message_id=mid, chat_id=chat_id,
                           timestamp="1756684800", transport="whatsapp")


def _inbound(cf, sb: _Sandbox, text: str, mid: str, chat_id: str = CUSTOMER_LID):
    """One customer inbound, through the production ordering.

    `_pre_gateway_dispatch_impl` runs the durable inbound-dedupe guard BEFORE any
    interceptor, so a harness that jumped straight to F7 would be modelling a
    dispatch that cannot happen. The two lines below are that guard, verbatim
    from hooks.py; everything after is the REAL F7 primary intercept.
    """
    if cf.actions.mark_cf_router_inbound_seen(chat_id, mid, text):
        return {"action": "skip", "reason": "cf-router duplicate inbound"}
    allow_new, signals = cf.actions.classify_catering(text)
    return cf.hooks._try_f7_primary_intercept(
        text, chat_id, _event(mid, chat_id), signals=signals, allow_new_lead=allow_new,
    )


# ── audit helpers ────────────────────────────────────────────────────────────
def _rows(sb: _Sandbox, type_: str) -> list[dict]:
    return [r for r in read_log_rows(sb.log) if r.get("type") == type_]


def _row_types(sb: _Sandbox) -> list[str]:
    return [r.get("type") for r in read_log_rows(sb.log)]


def _leads(sb: _Sandbox) -> list[dict]:
    return json.loads(sb.leads.read_text(encoding="utf-8"))["leads"]


def _lead(sb: _Sandbox, lead_id: str) -> dict:
    match = [le for le in _leads(sb) if le["lead_id"] == lead_id]
    assert len(match) == 1, f"expected exactly one lead {lead_id}, got {[le['lead_id'] for le in _leads(sb)]}"
    return match[0]


def _followups(sb: _Sandbox) -> list[dict]:
    if not sb.followups.exists():
        return []
    return json.loads(sb.followups.read_text(encoding="utf-8"))["followups"]


def _to_customer(sb: _Sandbox, since: int) -> list[dict]:
    return [s for s in sb.sent[since:] if str(s["jid"]).startswith("15550001111")]


def _to_owner(sb: _Sandbox, since: int) -> list[dict]:
    return [s for s in sb.sent[since:] if str(s["jid"]) == OWNER_JID]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Seed: menu + a NON-placeholder pricebook (the real import script).
# ═════════════════════════════════════════════════════════════════════════════
def test_01_seed_menu_and_pricebook(sb: _Sandbox):
    sb.menu.write_text(MENU_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    rc, out, err = _run_script(sb, "import-catering-pricebook", [
        "--file", str(PRICEBOOK_FIXTURE), "--updated-by", "import",
        "--sender-role", "owner",
    ])
    assert rc == 0, f"import-catering-pricebook exit={rc}; stderr={err[:600]}"

    book = catering_pricing.load_pricebook(sb.pricebook)
    assert book is not None, f"no pricebook written at {sb.pricebook}"
    assert book.is_placeholder() is False, (
        "the E2E must run on a REAL pricebook — a placeholder forces every quote to "
        "price_status=pending_owner_review and the 'exact' assertions become vacuous")
    assert {p.id for p in book.per_person_packages} >= {PACKAGE_ID}, (
        f"fixture pricebook is missing package {PACKAGE_ID}: "
        f"{[p.id for p in book.per_person_packages]}")
    assert _rows(sb, "catering_pricebook_updated"), (
        f"pricebook import must leave an audit row; saw {sorted(set(_row_types(sb)))}")

    menu = json.loads(sb.menu.read_text(encoding="utf-8"))
    assert len(menu["items"]) > 0 and menu["version"] >= 1, "menu fixture did not land"


# ═════════════════════════════════════════════════════════════════════════════
# 2. First customer inbound → QUALIFYING lead + a bounded question batch.
# ═════════════════════════════════════════════════════════════════════════════
MSG_INQUIRY = (
    "I need catering for a wedding reception for about 150 guests next month. "
    "Mostly vegetarian but some chicken dishes."
)


def test_02_first_inbound_opens_a_qualifying_lead_and_asks_questions(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    result = _inbound(cf, sb, MSG_INQUIRY, "wamid.M7.01")
    assert result is not None and result["action"] == "skip", (
        f"F7 must own a fresh catering inquiry; got {result!r}")

    new = [le for le in _leads(sb) if le["lead_id"] not in SEEDED_LEAD_IDS]
    assert len(new) == 1, f"exactly one new lead expected, got {[le['lead_id'] for le in new]}"
    lead = new[0]
    sb.lead_id, sb.approval_code = lead["lead_id"], lead["owner_approval_code"]

    assert lead["status"] == "QUALIFYING", (
        f"the M1 gate must park an under-specified inquiry, not send it to the owner; "
        f"status={lead['status']} extracted={lead['extracted']}")
    assert lead["extracted"]["headcount"] == 150, (
        f"headcount must be extracted from the inbound: {lead['extracted']}")
    assert lead["extracted"]["dietary_restrictions"] == ["veg"], (
        f"the veg/non-veg signal must be extracted: {lead['extracted']}")
    assert lead["extracted"]["event_date"] is None, (
        "'next month' is NOT a date — extracting one would fabricate a commitment; "
        f"got {lead['extracted']['event_date']!r}")

    asked = lead["pending_questions"]
    assert 2 <= len(asked) <= 4, f"question batch must be 2..4 fields, got {asked}"
    assert set(asked) == {"event_date", "venue", "service_style"}, (
        f"the batch must be exactly the unsatisfied required fields, got {asked}")

    # ONE bounded customer send: the question batch. No owner card yet — there is
    # nothing to approve.
    cust = _to_customer(sb, n0)
    assert len(cust) == 1, f"exactly one customer send on the intake turn, got {cust}"
    for field in asked:
        import catering_qualification as cq
        assert cq.QUESTION_TEMPLATES[field] in cust[0]["message"], (
            f"question for {field!r} missing from the reply:\n{cust[0]['message']}")
    assert _to_owner(sb, n0) == [], (
        "a QUALIFYING lead must not fire an owner approval card")

    types = _row_types(sb)
    assert "catering_lead_created" in types
    assert "catering_lead_qualification_updated" in types
    assert "cf_router_intercepted" in types, (
        f"the routing decision must be auditable; saw {sorted(set(types))}")
    assert "catering_owner_approval_requested" not in types, (
        "no owner approval is requested while the lead is still QUALIFYING")
    status_rows = [r for r in _rows(sb, "catering_lead_status_change")
                   if r["lead_id"] == sb.lead_id]
    assert [(r["from_status"], r["to_status"]) for r in status_rows] == [("NEW", "QUALIFYING")]


# ═════════════════════════════════════════════════════════════════════════════
# 3. Exact duplicate inbound → no second lead, no second reply.
# ═════════════════════════════════════════════════════════════════════════════
def test_03_duplicate_inbound_is_idempotent(cf, sb: _Sandbox):
    """Two independent layers must each refuse the replay — the router-level
    dedupe, and the create script's own (customer_phone, message_id) key. Both
    are asserted because either one alone is a single point of failure."""
    n0 = len(sb.sent)
    leads_before = json.dumps(_leads(sb), sort_keys=True)

    # (a) the durable router dedupe recognises the replayed event.
    replay = _inbound(cf, sb, MSG_INQUIRY, "wamid.M7.01")
    assert replay == {"action": "skip", "reason": "cf-router duplicate inbound"}, (
        f"the replay must be stopped at the dedupe guard, got {replay!r}")

    # (b) and the script layer refuses it independently, even if (a) were bypassed.
    ok, detail = cf.actions.trigger_create_catering_lead(
        customer_phone=CUSTOMER_PHONE, customer_name="", raw_inquiry=MSG_INQUIRY,
        message_id="wamid.M7.01", extracted_fields={"headcount": 150},
        qualification_gate=True,
    )
    assert ok, f"the replayed create must succeed as a no-op, got {detail!r}"
    assert json.loads(detail).get("idempotent_replay") is True, (
        f"create-catering-lead must report the replay, stdout={detail!r}")

    assert json.dumps(_leads(sb), sort_keys=True) == leads_before, (
        "a replayed inbound must not mutate the lead store")
    assert len(_leads(sb)) == 3, (
        f"still exactly the transcript lead + the two seeded ones, got "
        f"{[le['lead_id'] for le in _leads(sb)]}")
    assert _to_customer(sb, n0) == [], (
        f"a replay must not produce a second customer reply, got {_to_customer(sb, n0)}")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Customer answers → same lead updated, intake closes, owner card sent.
# ═════════════════════════════════════════════════════════════════════════════
MSG_ANSWER = "September 12 at the Grand Ballroom in Edison, buffet style, serving at 6pm"


def _next_september_12() -> str:
    """The date a bare "September 12" means: the next one that has not passed.

    Stating the contract instead of hard-coding a year keeps this transcript from
    rotting on 2026-09-13 — and keeps the lead's event date in the FUTURE, which
    the event-anchored follow-ups depend on to stay not-yet-due in test_10."""
    from datetime import date
    today = datetime.now(timezone.utc).date()
    year = today.year if date(today.year, 9, 12) >= today else today.year + 1
    return f"{year}-09-12"


def test_04_answer_advances_the_same_lead_to_owner_approval(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    result = _inbound(cf, sb, MSG_ANSWER, "wamid.M7.02")
    assert result is not None and result["action"] == "skip", (
        f"the slot-filling answer arm must own this turn; got {result!r}")

    assert len(_leads(sb)) == 3, (
        f"an answer must never mint a second lead: {[le['lead_id'] for le in _leads(sb)]}")
    lead = _lead(sb, sb.lead_id)
    assert lead["extracted"]["event_date"] == _next_september_12(), (
        f"the date answer must land on the SAME lead: {lead['extracted']}")
    assert lead["extracted"]["service_style"] == "buffet", (
        f"the service-style answer must land on the SAME lead: {lead['extracted']}")
    assert lead["extracted"]["headcount"] == 150, "an answer must not overwrite a filled slot"
    assert lead["status"] == "AWAITING_OWNER_APPROVAL", (
        f"intake must hand off once nothing further is worth asking; status={lead['status']} "
        f"pending={lead['pending_questions']}")
    assert lead["pending_questions"] == [], "handoff clears the open questions"

    # COMPOSITION NOTE (not a bug): `venue` stays unfilled. It is a free-text field
    # and the deterministic parser only reads a bare reply as a venue when venue is
    # the SOLE outstanding question — here three were asked at once. The loop then
    # hands off WITH the gap flagged rather than re-asking, which is the M1 ruling.
    qual = [r for r in _rows(sb, "catering_lead_qualification_updated")
            if r["lead_id"] == sb.lead_id]
    assert qual[-1]["outcome"] == "round_cap_reached", (
        f"handoff-with-gaps is the expected outcome here, got {qual[-1]['outcome']}")
    assert "venue" in qual[-1]["still_missing"], (
        f"the unfilled gap must be flagged to the owner: {qual[-1]}")

    assert any(r["lead_id"] == sb.lead_id and r["to_status"] == "AWAITING_OWNER_APPROVAL"
               for r in _rows(sb, "catering_lead_status_change")), (
        "the handoff transition must be audited")
    approval = [r for r in _rows(sb, "catering_owner_approval_requested")
                if r["lead_id"] == sb.lead_id]
    assert len(approval) == 1 and approval[0]["approval_code"] == sb.approval_code

    owner = _to_owner(sb, n0)
    assert len(owner) == 1, f"exactly one owner card on the handoff turn, got {len(owner)}"
    assert sb.approval_code in owner[0]["message"], (
        f"the owner card must carry the #code:\n{owner[0]['message'][:400]}")
    card = [r for r in _rows(sb, "catering_owner_approval_card_sent")
            if r["lead_id"] == sb.lead_id]
    assert len(card) == 1 and card[0]["owner_card_outbound_id"], (
        "the owner card send must be traceable with its provider id")


# ═════════════════════════════════════════════════════════════════════════════
# 5. Amendment: captured durably AND applied to the same lead.
# ═════════════════════════════════════════════════════════════════════════════
def test_05_amendment_is_captured_and_applied(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    result = _inbound(cf, sb, f"actually make it {FINAL_HEADCOUNT} guests", "wamid.M7.03")
    assert result is not None and result["action"] == "skip"

    # Durable capture (the R2A sidecar) — real store, real fs validation.
    assert sb.amendments.exists(), (
        "the amendment must be captured durably before the customer is answered")
    records = json.loads(sb.amendments.read_text(encoding="utf-8"))["records"]
    assert len(records) == 1, f"exactly one amendment captured, got {records}"
    assert records[0]["lead_id"] == sb.lead_id

    # ...and APPLIED (the M1 half that closed the "owner approves stale numbers" gap).
    lead = _lead(sb, sb.lead_id)
    assert lead["extracted"]["headcount"] == FINAL_HEADCOUNT, (
        f"the amendment must be applied to the SAME lead, not just stored; "
        f"headcount={lead['extracted']['headcount']}")
    assert len(_leads(sb)) == 3, "an amendment must never mint a new lead"

    applied = [r for r in _rows(sb, "catering_amendment_applied") if r["lead_id"] == sb.lead_id]
    assert len(applied) == 1, f"exactly one amendment-applied row, got {applied}"
    assert "headcount" in applied[0]["fields_changed"], (
        f"the applied row must name the field it changed: {applied[0]}")
    assert applied[0]["amendment_id"] == records[0]["amendment_id"], (
        "the applied row must reference the captured record")
    assert records[0].get("applied_at"), "the sidecar record must be stamped applied"

    assert len(_to_owner(sb, n0)) == 1, (
        "the owner is re-carded with the amended numbers exactly once")
    assert str(FINAL_HEADCOUNT) in _to_owner(sb, n0)[0]["message"], (
        f"the re-card must show the AMENDED headcount:\n{_to_owner(sb, n0)[0]['message'][:400]}")


# ═════════════════════════════════════════════════════════════════════════════
# 6. Proposal options generated → DRAFT→SENT + the initial_draft ledger version.
# ═════════════════════════════════════════════════════════════════════════════
def test_06_proposal_options_sent_with_initial_draft_ledger_version(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    rc = cf.actions.invoke_create_catering_proposals(
        sb.lead_id, CUSTOMER_LID, "wamid.M7.04", "please send me two sample menus")
    assert rc == 0, f"create-catering-proposal-options exit={rc}"

    sets = [s for s in json.loads(sb.proposals.read_text(encoding="utf-8"))["sets"]
            if s["lead_id"] == sb.lead_id]
    assert len(sets) == 1, f"exactly one proposal set for the lead, got {len(sets)}"
    pset = sets[0]
    sb.proposal_set_id = pset["proposal_set_id"]
    assert pset["status"] == "SENT", f"the set must reach SENT, got {pset['status']}"
    assert len(pset["options"]) == 2, f"two options expected, got {len(pset['options'])}"
    assert pset["outbound_message_id"], "a SENT set must record the provider id it went out as"

    gen = [r for r in _rows(sb, "catering_proposals_generated") if r["lead_id"] == sb.lead_id]
    assert len(gen) == 1 and gen[0]["outcome"] == "sent"
    assert gen[0]["logical_turn_id"] == "wamid.M7.04" and gen[0]["send_attempt_id"]

    # The initial_draft ledger version — owner-side, NOT sent, stamped with the
    # exact menu + pricebook it was priced against.
    history = catering_quote_ledger.history(sb.lead_id, data_path=sb.ledger)
    drafts = [r for r in history if r["source"] == "initial_draft"]
    assert len(drafts) == 1, (
        f"one initial_draft version expected; ledger holds "
        f"{[(r['version'], r['source']) for r in history]}")
    draft = drafts[0]
    assert draft["version"] == 1, f"the draft is version 1, got {draft['version']}"
    assert draft["source_message_id"] == sb.proposal_set_id
    menu_version = json.loads(sb.menu.read_text(encoding="utf-8"))["version"]
    book = catering_pricing.load_pricebook(sb.pricebook)
    assert draft["menu_version"] == menu_version, (
        f"draft must stamp the menu it priced against: {draft['menu_version']} != {menu_version}")
    assert draft["pricebook_version"] == book.version, (
        f"draft must stamp the pricebook version: {draft['pricebook_version']} != {book.version}")
    assert draft["price_status"] in ("exact", "estimated", "pending_owner_review")
    assert any(r["lead_id"] == sb.lead_id and r["version"] == 1
               for r in _rows(sb, "catering_quote_version_committed")), (
        "the ledger commit must be audited")

    assert len(_to_customer(sb, n0)) == 1, (
        "the tiered option set is the ONE bounded customer send for this turn")


# ═════════════════════════════════════════════════════════════════════════════
# 7. Customer selects; owner finalizes with --package-id + explicit quantities.
#    The total is recomputed INDEPENDENTLY here and must match to the cent.
# ═════════════════════════════════════════════════════════════════════════════
SELECTED_ITEMS = [
    {"name": "Idly (3 PCS)", "qty": 12, "price_usd": 6},
    {"name": "Masala Dosa", "qty": 8, "price_usd": 10},
]


def _expected_total_cents(sb: _Sandbox) -> tuple[int, int]:
    """Recompute the quote from the FIXTURE, independent of the kernel.

    Deliberately duplicated arithmetic: if the kernel's order of operations or
    rounding drifts, this disagrees and the test fails. Returns
    (total_cents, guard_basis_dollars) where the guard basis is the
    package+items subtotal the truth guard compares the caller's number against.
    """
    from decimal import ROUND_HALF_UP, Decimal
    book = json.loads(sb.pricebook.read_text(encoding="utf-8"))
    book = book.get("pricebook", book)
    pkg = next(p for p in book["per_person_packages"] if p["id"] == PACKAGE_ID)

    # A package basket prices FROM the package; its materialised item rows are
    # NOT also charged as a la carte lines.
    per_person = pkg["price_per_person_cents"] * FINAL_HEADCOUNT
    items = 0
    # fee_ids=None applies every ACTIVE fee; a missing per_unit means "flat", and
    # units for a flat fee is 1. The one per_staff fee in the fixture is inactive.
    fees = sum(f["amount_cents"] for f in book["fixed_fees"]
               if f.get("active", True) and (f.get("per_unit") or "flat") == "flat")
    subtotal = per_person + items + fees
    tax = int((Decimal(subtotal) * Decimal(book["tax_rate_bps"]) / Decimal(10000))
              .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    total = subtotal + tax
    guard = int((Decimal(per_person + items) / 100).quantize(Decimal("1"),
                                                             rounding=ROUND_HALF_UP))
    return total, guard


def _finalize(sb: _Sandbox, message_id: str):
    return _run_script(sb, "finalize-catering-menu", [
        "--code", sb.approval_code,
        "--customer-message-id", message_id,
        "--logical-turn-id", message_id,
        "--package-id", PACKAGE_ID,
        "--selected-items-json", json.dumps(SELECTED_ITEMS),
        "--quote-total-usd", str(_expected_total_cents(sb)[1]),
    ])


def test_07_selection_then_finalize_produces_a_deterministic_exact_total(cf, sb: _Sandbox):
    rc = cf.actions.invoke_select_catering_proposal(
        sb.lead_id, CUSTOMER_LID, "wamid.M7.05", "I like Option 2. Please send prices.")
    assert rc == 0, f"select-catering-proposal exit={rc}"
    pset = next(s for s in json.loads(sb.proposals.read_text(encoding="utf-8"))["sets"]
                if s["lead_id"] == sb.lead_id)
    assert pset["status"] == "SELECTED" and pset["selected_option_id"] == "2", (
        f"the real resolver must record option 2: status={pset['status']} "
        f"selected={pset['selected_option_id']}")
    sel = [r for r in _rows(sb, "catering_proposal_selected") if r["lead_id"] == sb.lead_id]
    assert len(sel) == 1, f"exactly one selection recorded, got {len(sel)}"
    assert len(cf.finalize_calls) == 1, "selection advances owner review exactly once"

    expected_total_cents, guard_dollars = _expected_total_cents(sb)
    sb.expected_total_cents = expected_total_cents

    rc, out, err = _finalize(sb, "wamid.M7.06")
    assert rc == 0, f"finalize-catering-menu exit={rc}; stderr={err[:800]}"
    body = _stdout_json(out)

    expected_dollars = round(expected_total_cents / 100)
    assert body["quote_total_usd"] == expected_dollars, (
        f"kernel total {body['quote_total_usd']} != independently recomputed "
        f"{expected_dollars} (cents={expected_total_cents}, guard basis={guard_dollars})")
    assert body["price_status"] == "exact", (
        f"a real pricebook + a package basket must price EXACT, got "
        f"{body['price_status']} flags={body.get('pricing_flags')}")
    assert body["pricebook_version"] == catering_pricing.load_pricebook(sb.pricebook).version

    lead = _lead(sb, sb.lead_id)
    assert lead["status"] == "CUSTOMER_FINALIZED", f"status={lead['status']}"
    assert lead["quote_total_usd"] == expected_dollars
    assert [(i["name"], i["qty"]) for i in lead["selected_items"]] == [
        (i["name"], i["qty"]) for i in SELECTED_ITEMS], (
        f"the owner's explicit quantities must persist verbatim: {lead['selected_items']}")

    # Reproducibility: the same inputs, run again, produce the same number.
    rc2, out2, err2 = _finalize(sb, "wamid.M7.06")
    assert rc2 == 0, f"finalize replay exit={rc2}; stderr={err2[:600]}"
    body2 = _stdout_json(out2)
    assert body2["quote_total_usd"] == body["quote_total_usd"], (
        f"finalize is not reproducible: {body['quote_total_usd']} then "
        f"{body2['quote_total_usd']}")
    assert body2["price_status"] == body["price_status"]
    assert _lead(sb, sb.lead_id)["quote_total_usd"] == expected_dollars


# ═════════════════════════════════════════════════════════════════════════════
# 8. Owner revision → a NEW immutable ledger version; the old one is untouched.
#
#    ORDERING NOTE (a real composition constraint, not a test convenience): the
#    revision has to happen HERE, before the approval, because the owner-decision
#    edit path only accepts {AWAITING_OWNER_APPROVAL, CUSTOMER_FINALIZED}. Once a
#    quote has gone to the customer the lead is deliberately frozen — "that would
#    rewrite a quote the customer has already been given". And an OWNER_EDITED
#    lead is deliberately NOT approvable either (a retry-approve would ship the
#    un-edited text), so a revision must be re-finalized before it can be sent.
#    The transcript below walks that exact route.
# ═════════════════════════════════════════════════════════════════════════════
REVISION_TEXT = "Revised: buffet upgraded to include two extra desserts at no charge."


def test_08_owner_revision_appends_an_immutable_ledger_version(sb: _Sandbox):
    assert _lead(sb, sb.lead_id)["status"] == "CUSTOMER_FINALIZED", (
        "the revision path starts from the finalized quote")
    before = catering_quote_ledger.history(sb.lead_id, data_path=sb.ledger)
    assert before, "the ledger must already hold the initial draft"
    v1_bytes = json.dumps(before[0], sort_keys=True)
    highest_before = max(r["version"] for r in before)

    n0 = len(sb.sent)
    rc, _out, err = _run_script(sb, "apply-catering-owner-decision", [
        "--code", sb.approval_code, "--decision", "edit", "--sender-role", "owner",
        "--edit-text", REVISION_TEXT, "--logical-turn-id", "wamid.M7.07",
    ])
    assert rc == 0, f"owner edit exit={rc}; stderr={err[:800]}"
    assert _lead(sb, sb.lead_id)["status"] == "OWNER_EDITED"
    assert _to_customer(sb, n0) == [], (
        f"a revision is owner-side only — nothing goes to the customer yet, got "
        f"{_to_customer(sb, n0)}")

    after = catering_quote_ledger.history(sb.lead_id, data_path=sb.ledger)
    assert len(after) == len(before) + 1, (
        f"the revision must append exactly one version: "
        f"{[(r['version'], r['source']) for r in after]}")
    newest = after[-1]
    assert newest["version"] == highest_before + 1, "versions are monotonic per lead"
    assert newest["source"] == "owner_edit", f"source={newest['source']}"

    # FINDING (pinned as-is, reported to the orchestrator — NOT fixed here):
    # the owner_edit ledger version snapshots `lead.quote_text`, and the edit path
    # never writes `--edit-text` onto the lead (only the APPROVE path writes
    # quote_text). Since an already-approved lead is not editable, an owner_edit
    # version can therefore never carry the revision the owner typed — it carries
    # whatever quote_text the lead had, here still the pre-quote sentinel. The
    # revision survives ONLY in the catering_owner_decision audit row. Asserting
    # the real behaviour keeps this test honest; a future fix should thread
    # edit_text into the ledger append and will flip these two assertions.
    assert REVISION_TEXT not in newest["quote_text"], (
        "FINDING CHANGED: the owner_edit ledger version now carries the edit text — "
        "update this cell (and the report) to assert the fixed behaviour")
    decision_rows = [r for r in _rows(sb, "catering_owner_decision")
                     if r["lead_id"] == sb.lead_id and r["decision"] == "edit"]
    assert len(decision_rows) == 1 and decision_rows[0]["edit_text"] == REVISION_TEXT, (
        f"the revision text must at minimum be durable in the audit row: {decision_rows}")

    # IMMUTABILITY: the earlier version is byte-identical after the append.
    assert json.dumps(after[0], sort_keys=True) == v1_bytes, (
        "appending a version must never rewrite an earlier one")
    assert catering_quote_ledger.latest_committed(
        sb.lead_id, data_path=sb.ledger)["version"] == newest["version"], (
        "only the latest version is what renders")
    committed = [r for r in _rows(sb, "catering_quote_version_committed")
                 if r["lead_id"] == sb.lead_id]
    assert {r["version"] for r in committed} == {r["version"] for r in after}, (
        f"every ledger version must have a commit row: rows={committed}")

    # The revision must be re-finalized before it is approvable (see the ordering
    # note above). This is the step that makes the edited quote sendable.
    rc, out, err = _finalize(sb, "wamid.M7.07b")
    assert rc == 0, f"re-finalize after revision exit={rc}; stderr={err[:800]}"
    assert _stdout_json(out)["quote_total_usd"] == round(sb.expected_total_cents / 100), (
        "a text-only revision must not move the price")
    assert _lead(sb, sb.lead_id)["status"] == "CUSTOMER_FINALIZED", (
        "the re-finalize is what makes the revised quote approvable")


# ═════════════════════════════════════════════════════════════════════════════
# 9. Owner approves → the governed quote goes out; replay does not re-send.
# ═════════════════════════════════════════════════════════════════════════════
def _approve(sb: _Sandbox, logical_turn_id: str):
    return _run_script(sb, "apply-catering-owner-decision", [
        "--code", sb.approval_code, "--decision", "approve", "--sender-role", "owner",
        "--quote-from-lead-state", "--logical-turn-id", logical_turn_id,
    ])


def test_09_owner_approval_sends_the_governed_quote_once(sb: _Sandbox):
    n0 = len(sb.sent)
    rc, out, err = _approve(sb, "wamid.M7.07")
    assert rc == 0, f"apply-catering-owner-decision exit={rc}; stderr={err[:800]}"

    cust = _to_customer(sb, n0)
    assert len(cust) == 1, f"exactly one customer quote send, got {len(cust)}"
    quote = cust[0]["message"]
    assert "valid until" in quote, f"the quote must state its validity window:\n{quote}"
    assert str(_lead(sb, sb.lead_id)["quote_total_usd"]) in quote, (
        f"the delivered quote must carry the persisted total:\n{quote}")
    assert str(FINAL_HEADCOUNT) in quote, f"the amended headcount must appear:\n{quote}"

    lead = _lead(sb, sb.lead_id)
    assert lead["status"] == "SENT_TO_CUSTOMER", f"status={lead['status']}"
    assert lead["quote_text"].strip(), "the delivered quote text must be persisted on the lead"

    sent_rows = [r for r in _rows(sb, "catering_quote_sent") if r["lead_id"] == sb.lead_id]
    assert len(sent_rows) == 1, f"exactly one quote-sent row, got {len(sent_rows)}"
    assert sent_rows[0]["logical_turn_id"] == "wamid.M7.07"
    assert sent_rows[0]["send_attempt_id"], "the quote send must carry its identity"
    anchors = [r for r in _rows(sb, "catering_quote_attempted") if r["lead_id"] == sb.lead_id]
    assert {a["send_attempt_id"] for a in anchors} == {sent_rows[0]["send_attempt_id"]}, (
        "every attempt anchor shares the delivered send's identity")

    # Replay defence 1 — the status guard. The quote already went out, so a plain
    # re-approve is refused outright.
    n1 = len(sb.sent)
    rc2, _out2, err2 = _approve(sb, "wamid.M7.07")
    assert rc2 == 4, (
        f"re-approving an already-sent lead must be refused, not re-sent; rc={rc2} "
        f"stderr={err2[:400]}")
    assert _to_customer(sb, n1) == [], f"nothing re-sent, got {_to_customer(sb, n1)}"

    # Replay defence 2 — the ANCHOR short-circuit, the one that matters. Model the
    # crash window the anchor exists for: the bridge POST succeeded but the process
    # died before the state write, so the lead is still OWNER_APPROVED while the
    # customer already holds the quote. Re-running must recover the state WITHOUT a
    # second send.
    doc = json.loads(sb.leads.read_text(encoding="utf-8"))
    for le in doc["leads"]:
        if le["lead_id"] == sb.lead_id:
            le["status"] = "OWNER_APPROVED"
    sb.leads.write_text(json.dumps(doc), encoding="utf-8")

    n2 = len(sb.sent)
    rc3, out3, err3 = _approve(sb, "wamid.M7.07")
    assert rc3 == 0, f"the anchor recovery exit={rc3}; stderr={err3[:600]}"
    body3 = _stdout_json(out3)
    assert body3.get("idempotent_replay") is True, (
        f"the prior delivery must be recognised from the audit anchor, stdout={out3[:300]}")
    assert body3["new_status"] == "SENT_TO_CUSTOMER", "the crashed state is recovered"
    assert _to_customer(sb, n2) == [], (
        f"the recovery must NOT re-send the quote, got {_to_customer(sb, n2)}")
    assert len([r for r in _rows(sb, "catering_quote_sent")
                if r["lead_id"] == sb.lead_id]) == 1, "still exactly one quote-sent row"
    assert _lead(sb, sb.lead_id)["status"] == "SENT_TO_CUSTOMER"


# ═════════════════════════════════════════════════════════════════════════════
# 10. Follow-up: quote-sent scheduled it; sweep cards the owner; owner approves.
# ═════════════════════════════════════════════════════════════════════════════
def _only_due(sb: _Sandbox, followup_id: str) -> None:
    """Make exactly ONE follow-up due, and nothing else.

    The sweep reads wall-clock `customer_now` — there is no clock to inject — so
    the M5 suites control time by writing `due_at` relative to now. This does the
    same, and additionally parks every OTHER scheduled record in the future. That
    second half matters: the quote-sent trigger also schedules event-anchored
    chases (event_approaching is event_date minus 7 days), so on any run inside a
    week of the transcript's event the sweep would card two follow-ups and the
    "exactly one chase" assertions would depend on the calendar. Pinning one
    record at a time makes each sweep assertion date-independent.
    """
    doc = json.loads(sb.followups.read_text(encoding="utf-8"))
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    far = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    for f in doc["followups"]:
        if f["followup_id"] == followup_id:
            f["due_at"] = past
        elif f["status"] == "scheduled":
            f["due_at"] = far
    sb.followups.write_text(json.dumps(doc), encoding="utf-8")


def test_10_followup_is_scheduled_carded_and_approved(sb: _Sandbox):
    scheduled = [f for f in _followups(sb) if f["lead_id"] == sb.lead_id]
    assert scheduled, (
        "sending the quote must schedule the proposal_unanswered chase; "
        f"store={_followups(sb)}")
    unanswered = [f for f in scheduled if f["followup_type"] == "proposal_unanswered"]
    assert len(unanswered) == 1, f"one proposal_unanswered follow-up, got {unanswered}"
    assert unanswered[0]["status"] == "scheduled"
    assert any(r["lead_id"] == sb.lead_id and r["trigger"] == "quote_sent"
               for r in _rows(sb, "catering_followup_scheduled")), (
        "the schedule must name the trigger that caused it")

    _only_due(sb, unanswered[0]["followup_id"])
    n0 = len(sb.sent)
    rc, out, err = _run_script(sb, "catering-followup-sweep", [])
    assert rc == 0, f"sweep exit={rc}; stderr={err[:600]}"
    assert _stdout_json(out).get("carded") == 1, f"sweep stdout={out[:300]}"

    carded = next(f for f in _followups(sb)
                  if f["followup_id"] == unanswered[0]["followup_id"])
    assert carded["status"] == "awaiting_owner_approval", (
        f"the sweep must card the owner, not auto-send; status={carded['status']} "
        f"suppressed_reason={carded.get('suppressed_reason')}")
    assert carded["approval_code"] and carded["approval_code"].startswith("#"), (
        f"the card must mint a #code, got {carded['approval_code']!r}")
    sb.followup_code = carded["approval_code"]
    assert carded["rendered_message"], "the card must freeze the message it will send"

    owner = _to_owner(sb, n0)
    assert len(owner) == 1 and sb.followup_code in owner[0]["message"], (
        f"the owner card must carry its own #code:\n{owner[0]['message'][:400] if owner else None}")
    assert _to_customer(sb, n0) == [], "carding must not message the customer"
    assert [r["approval_code"] for r in _rows(sb, "catering_followup_card_sent")] == [
        sb.followup_code]

    # Owner approves → the customer finally gets the chase.
    n1 = len(sb.sent)
    rc, out, err = _run_script(sb, "approve-catering-followup", [
        "--code", sb.followup_code, "--decision", "approve", "--sender-role", "owner",
    ])
    assert rc == 0, f"approve-catering-followup exit={rc}; stderr={err[:600]}"
    assert next(f for f in _followups(sb)
                if f["approval_code"] == sb.followup_code)["status"] == "approved_sent"
    cust = _to_customer(sb, n1)
    assert len(cust) == 1, f"exactly one customer follow-up send, got {len(cust)}"
    assert carded["rendered_message"] in cust[0]["message"], (
        f"the approved send must be the exact text the owner was shown (modulo the "
        f"bridge template-bypass header):\nsent={cust[0]['message']!r}\n"
        f"carded={carded['rendered_message']!r}")
    assert [r["approval_code"] for r in _rows(sb, "catering_followup_sent")] == [sb.followup_code]


# ═════════════════════════════════════════════════════════════════════════════
# 11. STOP: one deterministic ack, then total suppression.
# ═════════════════════════════════════════════════════════════════════════════
def _control(cf, text: str, chat_id: str, mid: str):
    return cf.hooks._try_automation_control(text, chat_id, mid, mid)


def test_11_customer_stop_acks_once_then_suppresses_everything(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    out = _control(cf, "STOP", CUSTOMER_LID, "wamid.M7.09")
    assert out is not None and out["action"] == "skip" and "stop" in out["reason"], (
        f"STOP must be owned by the control kernel; got {out!r}")
    assert ac.get_mode(CUSTOMER_LID) == "opted_out", (
        f"mode={ac.get_mode(CUSTOMER_LID)}")
    acks = _to_customer(sb, n0)
    assert len(acks) == 1, f"exactly one opt-out ack, got {acks}"
    assert "RESUME" in acks[0]["message"], (
        f"the ack must tell the customer how to come back:\n{acks[0]['message']}")
    changed = _rows(sb, "catering_automation_control_changed")
    assert changed and changed[-1]["to_mode"] == "opted_out"

    # Duplicate STOP → no second ack (the atomic ack-once guard).
    n1 = len(sb.sent)
    _control(cf, "STOP", CUSTOMER_LID, "wamid.M7.10")
    assert _to_customer(sb, n1) == [], (
        f"a repeated STOP must not re-ack, got {_to_customer(sb, n1)}")

    # A due follow-up is now SUPPRESSED, not sent.
    rc, out2, err = _run_script(sb, "create-catering-followup", [
        "--lead-id", sb.lead_id, "--due-at", "+1h", "--note", "post-stop chase",
    ])
    assert rc == 0, f"create-catering-followup exit={rc}; stderr={err[:600]}"
    fid = _stdout_json(out2)["followup_id"]
    _only_due(sb, fid)

    n2 = len(sb.sent)
    rc, out3, err = _run_script(sb, "catering-followup-sweep", [])
    assert rc == 0, f"sweep exit={rc}; stderr={err[:600]}"
    record = next(f for f in _followups(sb) if f["followup_id"] == fid)
    assert record["status"] == "suppressed", (
        f"an opted-out conversation must suppress the chase, got {record['status']}")
    assert record["suppressed_reason"] == "automation_suppressed", (
        f"reason={record['suppressed_reason']}")
    assert sb.sent[n2:] == [], f"a suppressed sweep sends NOTHING, got {sb.sent[n2:]}"
    assert any(r["followup_id"] == fid and r["reason"] == "automation_suppressed"
               for r in _rows(sb, "catering_followup_suppressed"))

    # ...and the outbound chokepoint itself drops a direct send to that customer.
    from schemas import ActionExecutionContext
    ctx = ActionExecutionContext(action_id="m7.e2e.direct", is_regulated_action=False,
                                 verified_action_result=False)
    n3 = len(sb.sent)
    ok, mid, err_s, status = safe_io.bridge_post(CUSTOMER_JID, "an automated nudge",
                                                 action_context=ctx)
    assert (ok, status) == (False, "suppressed"), (
        f"bridge_post must drop toward a suppressed conversation: "
        f"ok={ok} mid={mid!r} err={err_s!r} status={status!r}")
    assert err_s == "automation_suppressed:opted_out"
    assert sb.sent[n3:] == [], "a suppressed send must never reach transport"
    assert any(r["mode"] == "opted_out" and r["suppressed_send_kind"] == "outbound_send"
               for r in _rows(sb, "catering_automated_send_suppressed"))


# ═════════════════════════════════════════════════════════════════════════════
# 12. Owner takeover: a human is handling it; automation stands down.
# ═════════════════════════════════════════════════════════════════════════════
def test_12_owner_takeover_stands_automation_down(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    out = _control(cf, f"take over {sb.approval_code}", OWNER_JID, "wamid.M7.11")
    assert out is not None and out["action"] == "skip" and "takeover" in out["reason"], (
        f"the owner takeover command must be owned by the kernel; got {out!r}")
    assert ac.get_mode(CUSTOMER_LID) == "takeover", (
        f"the takeover must target the customer resolved from {sb.approval_code}; "
        f"mode={ac.get_mode(CUSTOMER_LID)}")

    confirmations = _to_owner(sb, n0)
    assert len(confirmations) == 1, (
        f"the owner gets exactly one deterministic confirmation, got {confirmations}")
    assert sb.approval_code in confirmations[0]["message"], (
        f"the confirmation must name the lead it applies to:\n{confirmations[0]['message']}")
    assert _to_customer(sb, n0) == [], "a takeover must not message the customer"
    assert _rows(sb, "catering_automation_control_changed")[-1]["to_mode"] == "takeover"

    # A customer inbound is no longer auto-handled.
    n1 = len(sb.sent)
    out = _control(cf, "any update on my quote?", CUSTOMER_LID, "wamid.M7.12")
    assert out is not None and out["action"] == "skip", (
        f"a suppressed conversation must be short-circuited before F7; got {out!r}")
    assert out["reason"] == "automation_suppressed:takeover", f"reason={out['reason']}"
    assert sb.sent[n1:] == [], (
        f"nothing is sent while a human holds the conversation, got {sb.sent[n1:]}")
    assert any(r["mode"] == "takeover" and r["suppressed_send_kind"] == "inbound_dispatch"
               for r in _rows(sb, "catering_automated_send_suppressed"))


# ═════════════════════════════════════════════════════════════════════════════
# 13. Release: the owner hands the conversation back.
# ═════════════════════════════════════════════════════════════════════════════
def test_13_owner_release_restores_normal_handling(cf, sb: _Sandbox):
    n0 = len(sb.sent)
    out = _control(cf, f"release {sb.approval_code}", OWNER_JID, "wamid.M7.13")
    assert out is not None and out["action"] == "skip" and "release" in out["reason"], (
        f"got {out!r}")
    assert ac.get_mode(CUSTOMER_LID) == "active", f"mode={ac.get_mode(CUSTOMER_LID)}"
    assert len(_to_owner(sb, n0)) == 1, "one deterministic release confirmation"
    assert _rows(sb, "catering_automation_control_changed")[-1]["to_mode"] == "active"

    # Normal handling resumes: an inbound is no longer short-circuited.
    assert _control(cf, "any update on my quote?", CUSTOMER_LID, "wamid.M7.14") is None, (
        "an active conversation must fall through the control kernel to normal routing")


# ═════════════════════════════════════════════════════════════════════════════
# 14. Acceptance → BOOKED; an ambiguous reply on a DIFFERENT lead clarifies once.
# ═════════════════════════════════════════════════════════════════════════════
def _record_acceptance(sb: _Sandbox, lead_id: str, jid: str, mid: str, text: str):
    return _run_script(sb, "record-catering-acceptance", [
        "--lead-id", lead_id, "--customer-jid", jid,
        "--customer-message-id", mid, "--message-text", text,
        "--sender-role", "customer",
    ])


def test_14_customer_acceptance_books_the_lead(sb: _Sandbox):
    # The lead is OWNER_EDITED after test_09; the customer's acceptance applies to
    # the quote they were sent, so put the lead back in the state that models
    # "quote is with the customer" before recording their answer.
    doc = json.loads(sb.leads.read_text(encoding="utf-8"))
    for le in doc["leads"]:
        if le["lead_id"] == sb.lead_id:
            le["status"] = "SENT_TO_CUSTOMER"
    sb.leads.write_text(json.dumps(doc), encoding="utf-8")

    n0 = len(sb.sent)
    rc, out, err = _record_acceptance(sb, sb.lead_id, CUSTOMER_JID, "wamid.M7.15",
                                      "yes, we accept - let's book it")
    assert rc == 0, f"record-catering-acceptance exit={rc}; stderr={err[:800]}"
    body = _stdout_json(out)
    assert body["outcome"] == "accepted" and body["new_status"] == "BOOKED", f"stdout={body}"

    lead = _lead(sb, sb.lead_id)
    assert lead["status"] == "BOOKED", f"status={lead['status']}"
    assert lead["customer_acceptance"] == "accepted"
    assert lead["acceptance_message_id"] == "wamid.M7.15", (
        f"the acceptance must be anchored to the message that carried it: {lead}")
    row = [r for r in _rows(sb, "catering_lead_acceptance_recorded")
           if r["lead_id"] == sb.lead_id]
    assert len(row) == 1 and row[0]["outcome"] == "accepted"
    assert row[0]["to_status"] == "BOOKED"
    assert len(_to_owner(sb, n0)) == 1, "the owner is told the booking landed"
    assert len(_to_customer(sb, n0)) == 1, "the customer gets one confirmation"

    # A DIFFERENT lead, an ambiguous reply: one clarification, no state change.
    n1 = len(sb.sent)
    before = _lead(sb, OTHER_LEAD_ID)
    rc, out2, err2 = _record_acceptance(sb, OTHER_LEAD_ID, OTHER_JID, "wamid.M7.16", "ok great")
    assert rc == 0, f"ambiguous acceptance exit={rc}; stderr={err2[:600]}"
    after = _lead(sb, OTHER_LEAD_ID)
    assert after["status"] == before["status"] == "SENT_TO_CUSTOMER", (
        "an ambiguous reply must NOT decide the booking")
    assert after.get("customer_acceptance") is None
    assert after["acceptance_clarification_sent_message_id"] == "wamid.M7.16"
    clar = [r for r in _rows(sb, "catering_lead_acceptance_recorded")
            if r["lead_id"] == OTHER_LEAD_ID]
    assert len(clar) == 1 and clar[0]["outcome"] == "clarification_sent"
    other_sends = [s for s in sb.sent[n1:] if str(s["jid"]).startswith("15550002222")]
    assert len(other_sends) == 1, f"exactly one clarification, got {other_sends}"

    # ...and only ONCE: a second ambiguous reply is silent.
    n2 = len(sb.sent)
    rc, out3, _ = _record_acceptance(sb, OTHER_LEAD_ID, OTHER_JID, "wamid.M7.17", "ok great")
    assert rc == 0 and _stdout_json(out3).get("replayed") is True, f"stdout={out3[:300]}"
    assert sb.sent[n2:] == [], (
        f"clarification is asked once, never again, got {sb.sent[n2:]}")


# ═════════════════════════════════════════════════════════════════════════════
# 15. Kill switch: every send drops while the flag is present.
# ═════════════════════════════════════════════════════════════════════════════
def test_15_kill_switch_drops_every_send(sb: _Sandbox):
    from schemas import ActionExecutionContext
    ctx = ActionExecutionContext(action_id="m7.e2e.killswitch", is_regulated_action=False,
                                 verified_action_result=False)
    sb.disabled_flag.write_text("disabled by the operator", encoding="utf-8")
    try:
        n0 = len(sb.sent)
        ok, mid, err, status = safe_io.bridge_post(OTHER_JID, "anything at all",
                                                   action_context=ctx)
        assert (ok, mid, err, status) == (False, "", "agent_disabled", "disabled"), (
            f"the kill switch must drop the send: {(ok, mid, err, status)}")
        assert sb.sent[n0:] == [], "a killed send must never reach transport"
        refused = _rows(sb, "outbound_refused_disabled")
        assert refused and refused[-1]["send_kind"] == "bridge_post", (
            f"the drop must be audited; rows={refused}")

        # Not even an owner-directed script send survives it.
        n1 = len(sb.sent)
        rc, _out, _err = _run_script(sb, "catering-followup-sweep", [])
        assert rc == 0, "the sweep never fails its unit"
        assert sb.sent[n1:] == [], (
            f"no send of any kind while disabled, got {sb.sent[n1:]}")
    finally:
        sb.disabled_flag.unlink()

    # Flag removed → sends flow again.
    n2 = len(sb.sent)
    ok, mid, err, status = safe_io.bridge_post(OTHER_JID, "back online", action_context=ctx)
    assert (ok, status) == (True, "sent"), f"send after re-enable: {(ok, mid, err, status)}"
    assert len(sb.sent[n2:]) == 1, "the send reaches transport once the flag is gone"


# ═════════════════════════════════════════════════════════════════════════════
# 16. Per-lead hold: the owner freezes ONE lead without touching the fleet.
# ═════════════════════════════════════════════════════════════════════════════
def test_16_per_lead_hold_refuses_approval_and_suppresses_followup(sb: _Sandbox):
    rc, out, err = _run_script(sb, "set-catering-lead-hold", [
        "--lead-id", HELD_LEAD_ID, "--on", "--reason", "owner is renegotiating",
        "--actor", "owner",
    ])
    assert rc == 0, f"set-catering-lead-hold exit={rc}; stderr={err[:600]}"
    held = _lead(sb, HELD_LEAD_ID)
    assert held["on_hold"] is True and held["hold_reason"] == "owner is renegotiating"
    assert [r["to_hold"] for r in _rows(sb, "catering_lead_hold_changed")] == [True]
    assert _lead(sb, sb.lead_id)["on_hold"] is False, (
        "a per-lead hold must freeze exactly one lead, not the fleet")

    # The approve path refuses BEFORE any mutation. The lead is CUSTOMER_FINALIZED,
    # i.e. one the approve matcher WOULD accept — so rc=14 is attributable to the
    # hold, not to a status guard firing first.
    n0 = len(sb.sent)
    status_before = held["status"]
    rc, _out, err = _run_script(sb, "apply-catering-owner-decision", [
        "--code", HELD_CODE, "--decision", "approve", "--sender-role", "owner",
        "--quote-from-lead-state", "--logical-turn-id", "wamid.M7.18",
    ])
    assert rc == 14, (
        f"a held lead must refuse approval with EXIT_LEAD_ON_HOLD; rc={rc} err={err[:400]}")
    assert _lead(sb, HELD_LEAD_ID)["status"] == status_before, (
        "the refusal must not mutate the lead")
    assert sb.sent[n0:] == [], f"a held lead sends nothing, got {sb.sent[n0:]}"
    blocked = _rows(sb, "catering_lead_hold_blocked_send")
    assert blocked and blocked[-1]["blocked_send_kind"] == "owner_approved_quote", (
        f"the block must be audited; rows={blocked}")

    # A due follow-up on the held lead is suppressed too.
    rc, out2, err = _run_script(sb, "create-catering-followup", [
        "--lead-id", HELD_LEAD_ID, "--due-at", "+1h", "--note", "held-lead chase",
    ])
    assert rc == 0, f"create-catering-followup exit={rc}; stderr={err[:600]}"
    fid = _stdout_json(out2)["followup_id"]
    _only_due(sb, fid)
    n1 = len(sb.sent)
    rc, _out, err = _run_script(sb, "catering-followup-sweep", [])
    assert rc == 0, f"sweep exit={rc}; stderr={err[:600]}"
    record = next(f for f in _followups(sb) if f["followup_id"] == fid)
    assert record["status"] == "suppressed" and record["suppressed_reason"] == "lead_on_hold", (
        f"status={record['status']} reason={record.get('suppressed_reason')}")
    assert sb.sent[n1:] == [], f"a held lead's chase sends nothing, got {sb.sent[n1:]}"

    # Clear the hold — the freeze is reversible.
    rc, _out, err = _run_script(sb, "set-catering-lead-hold", [
        "--lead-id", HELD_LEAD_ID, "--off", "--actor", "owner",
    ])
    assert rc == 0, f"clearing the hold exit={rc}; stderr={err[:600]}"
    cleared = _lead(sb, HELD_LEAD_ID)
    assert cleared["on_hold"] is False and cleared["hold_reason"] is None
    assert [r["to_hold"] for r in _rows(sb, "catering_lead_hold_changed")] == [True, False]


# ═════════════════════════════════════════════════════════════════════════════
# 17. FINALE: one continuous audit timeline, no duplicates anywhere.
# ═════════════════════════════════════════════════════════════════════════════
# The order these stages FIRST appear in the transcript. Two placements are worth
# stating because they surprise on first read and both are correct:
#   * `catering_followup_scheduled` lands at the QUALIFICATION handoff, long before
#     any quote — amend-catering-lead schedules an incomplete_qualification nudge
#     when intake stops with gaps open. The quote-sent trigger schedules more later.
#   * `catering_quote_version_committed` precedes `catering_proposals_generated`:
#     create-catering-proposal-options commits the owner-side initial_draft version
#     as part of the same successful send.
LIFECYCLE_ORDER = [
    "catering_lead_created",
    "catering_lead_qualification_updated",
    "catering_owner_approval_requested",
    "catering_followup_scheduled",
    "catering_amendment_applied",
    "catering_quote_version_committed",
    "catering_proposals_generated",
    "catering_proposal_selected",
    "catering_menu_finalized",
    "catering_quote_sent",
    "catering_followup_card_sent",
    "catering_followup_sent",
    "catering_automation_control_changed",
    "catering_lead_acceptance_recorded",
    "outbound_refused_disabled",
    "catering_lead_hold_blocked_send",
]


def test_17_audit_timeline_tells_the_whole_story_without_duplicates(sb: _Sandbox):
    rows = read_log_rows(sb.log)
    assert rows, "the transcript must have left an audit trail"
    types = [r.get("type") for r in rows]

    # (a) every lifecycle stage is present, in lifecycle order.
    first_at: dict[str, int] = {}
    for idx, t in enumerate(types):
        first_at.setdefault(t, idx)
    missing = [t for t in LIFECYCLE_ORDER if t not in first_at]
    assert not missing, (
        f"the timeline is missing lifecycle stages {missing}; present types="
        f"{sorted(set(types))}")
    positions = [(t, first_at[t]) for t in LIFECYCLE_ORDER]
    out_of_order = [(positions[i][0], positions[i + 1][0])
                    for i in range(len(positions) - 1)
                    if positions[i][1] > positions[i + 1][1]]
    assert not out_of_order, (
        f"lifecycle stages appeared out of order: {out_of_order}\n"
        f"first-seen offsets: {positions}")

    # (b) exactly ONE lead for the customer (plus the deliberately-seeded second).
    all_leads = _leads(sb)
    assert len(all_leads) == 3, f"lead ids: {[le['lead_id'] for le in all_leads]}"
    assert sorted(le["lead_id"] for le in all_leads) == sorted(
        [sb.lead_id, *SEEDED_LEAD_IDS])
    assert [le["lead_id"] for le in all_leads if le["customer_phone"] == CUSTOMER_PHONE] == [
        sb.lead_id], "the whole transcript must live on ONE lead"
    created = [r["lead_id"] for r in _rows(sb, "catering_lead_created")]
    assert len(created) == len(set(created)) == 1, (
        f"no duplicate lead creations: {created}")

    # (c) no duplicate outbound for the same logical send anchor.
    for row_type, anchor in (("catering_quote_sent", "lead_id"),
                             ("catering_followup_sent", "approval_code"),
                             ("catering_lead_acceptance_recorded", "lead_id")):
        anchors = [r[anchor] for r in _rows(sb, row_type)
                   if r.get("outcome") in (None, "sent", "accepted", "clarification_sent")]
        assert len(anchors) == len(set(anchors)), (
            f"duplicate {row_type} rows for the same {anchor}: {anchors}")

    # (d) every successful catering send row carries its outbound identity.
    identity_types = {"catering_customer_ack_sent", "catering_owner_approval_card_sent",
                      "catering_proposals_generated", "catering_quote_sent",
                      "catering_menu_finalized"}
    for r in rows:
        if r.get("type") not in identity_types:
            continue
        if r["type"] == "catering_menu_finalized" and r.get("outcome") != "finalized":
            continue
        assert r.get("send_attempt_id"), f"{r['type']} row without a send_attempt_id: {r}"
        assert r.get("logical_turn_id"), f"{r['type']} row without a logical_turn_id: {r}"

    # (e) the refusals are recorded as refusals — not as sends.
    assert _rows(sb, "outbound_refused_disabled"), "the kill-switch drop must be on the record"
    assert _rows(sb, "catering_lead_hold_blocked_send"), "the hold block must be on the record"
    assert not [t for t in set(types) if t and t.startswith("flyer_")], (
        f"a catering transcript must not mutate flyer state: {sorted(set(types))}")

    # (f) the lead ends where the story ends.
    final = _lead(sb, sb.lead_id)
    assert final["status"] == "BOOKED", f"final status={final['status']}"
    assert final["quote_text"].strip() and final["quote_total_usd"], (
        "a booked lead carries the quote it was booked on")
    ledger = catering_quote_ledger.history(sb.lead_id, data_path=sb.ledger)
    assert len(ledger) >= 2, f"the lead's quote history: {[r['version'] for r in ledger]}"
    assert [r["version"] for r in ledger] == sorted(r["version"] for r in ledger), (
        "ledger versions are monotonic")

    # FINDING (pinned as-is, reported — NOT fixed here): the LEAD's own
    # `quote_version` field is never written by any catering script, so it stays 0
    # while the ledger holds the real history. The Studio read model surfaces that
    # lead field verbatim (see
    # web/backend/tests/test_catering_studio_e2e_readmodel.py), which is how a
    # 2-version lead ends up displayed as "0 versions". Asserting it here keeps the
    # two suites agreeing on the end state without a cross-suite import.
    assert final.get("quote_version", 0) == 0, (
        "FINDING CHANGED: lead.quote_version is now maintained — update this cell, "
        "the read-model companion, and the report")
