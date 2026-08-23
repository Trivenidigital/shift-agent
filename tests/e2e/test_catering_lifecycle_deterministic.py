"""Deterministic catering lifecycle E2E — the REAL CLI chain, out of process.

WHY THIS FILE EXISTS
    Two other suites already claim "catering end to end", and neither covers
    what this one covers:

    * ``tests/e2e/test_catering_conversation_e2e.py`` is the FUNDED gate: it
      drives a real OpenRouter conversation and SKIPS without a key, so on CI it
      proves nothing. Untouched by this file — this is an additional layer, not
      a replacement.
    * ``tests/test_catering_studio_e2e.py`` is the in-process transcript. It
      loads the same scripts with ``fixtures_fleet.load_script`` and stubs
      cf-router's subprocess boundary, so the two seams that only exist between
      processes are invisible to it: the deployed ``/opt/shift-agent`` layout
      the scripts hardcode, and ``select-catering-proposal`` shelling out to
      ``finalize-catering-menu`` through the Hermes venv interpreter.

    This file runs the deployed scripts as SUBPROCESSES, from ``/usr/local/bin``,
    against a real ``/opt/shift-agent`` layout, with the 78-item production menu.
    Nothing but the WhatsApp transport is stubbed.

WHAT IS AND IS NOT STUBBED
    * NOT stubbed: every catering script, the platform modules they import, the
      audit chokepoint, the approval-code minting, the proposal state machine,
      the finalize subprocess hop.
    * Stubbed (one seam): the WhatsApp bridge — a loopback HTTP server that
      answers ``POST /send``. ``test_positive_control_stub_is_on_the_send_seam``
      exists so a dead stub cannot masquerade as green.

WHERE THE STATE LIVES
    The scripts hardcode ``/opt/shift-agent/...``. Rather than modify them (a
    rewritten script is not the deployed script), ``/opt/shift-agent`` is made a
    SYMLINK to a per-test ``tmp_path`` sandbox, and torn down afterwards. The
    test refuses to run at all if ``/opt/shift-agent`` is a real directory —
    that is a deployed box, and nothing here may touch one. Building the
    sandbox therefore needs a writable ``/opt`` and ``/usr/local`` (i.e. root):
    the CI gate runs it in a container, and where the sandbox cannot be built
    the module skips — unless ``CATERING_LIFECYCLE_E2E_REQUIRED=1``, which the
    gate sets so a skip there is a FAILURE rather than a silent pass.

    Not parallel-safe: ``/opt/shift-agent`` is a single global name. Run this
    file in a plain pytest process, never under xdist alongside itself.

NO LLM, NO KEY, NO NETWORK beyond loopback. Proposal generation is the
deterministic ``--auto-generate-from-menu`` path; nothing here reads
OPENROUTER_API_KEY, and the transport-evidence harness is never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

# tests/conftest.py puts src/platform on sys.path, so the exit codes under test
# are the SAME constants the scripts return — a renumbering cannot drift past
# this file unnoticed.
from exit_codes import (  # noqa: E402
    EXIT_DEPENDENCY_DOWN,
    EXIT_INVALID_INPUT,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_PRIVILEGE_DENIED,
    EXIT_TRUTH_GUARD_FAILED,
)

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parents[2]
PLATFORM_DIR = REPO / "src" / "platform"
CATERING_SCRIPTS = REPO / "src" / "agents" / "catering" / "scripts"
SHIFT_SCRIPTS = REPO / "src" / "agents" / "shift" / "scripts"
TEMPLATES_SRC = REPO / "src" / "agents" / "catering" / "templates"

# The 78-item production menu, copied off the box. READ-ONLY here:
# `test_zz_menu_fixture_was_not_mutated` proves the run never wrote to it.
MENU_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catering-menu-e2e.json"
MENU_FIXTURE_ITEM_COUNT = 78

# The three absolute locations the deployed scripts hardcode.
DEPLOY_ROOT = Path("/opt/shift-agent")
BIN_DIR = Path("/usr/local/bin")
HERMES_VENV_BIN = Path("/usr/local/lib/hermes-agent/venv/bin")
HERMES_VENV_PY = HERMES_VENV_BIN / "python"

OWNER_JID = "19045550100@s.whatsapp.net"
CUSTOMER_PHONE = "+19045550199"
CUSTOMER_JID = "19045550199@s.whatsapp.net"

_REQUIRED = os.environ.get("CATERING_LIFECYCLE_E2E_REQUIRED") == "1"


def _future_date(days: int) -> str:
    """A relative event date. A literal is a calendar time-bomb — the scripts
    reject past event_dates (see test_catering_v02_scripts.py's note)."""
    return (date.today() + timedelta(days=days)).isoformat()


def _base_fields(**over) -> dict:
    fields = {
        "headcount": 50,
        "event_date": _future_date(45),
        "event_type": "corporate",
        "menu_preferences": ["South Indian"],
        "dietary_restrictions": ["vegetarian"],
        "delivery_or_pickup": "delivery",
        "budget_hint_usd": 3000,
        "notes": "deterministic lifecycle e2e",
    }
    fields.update(over)
    return fields


def _unavailable(reason: str):
    """Skip — or, under the CI gate, FAIL. A skip on Linux proves nothing, so
    the gate sets CATERING_LIFECYCLE_E2E_REQUIRED=1 and an unbuildable sandbox
    becomes a red build instead of a green one with a quiet 's'."""
    message = (
        f"catering lifecycle E2E could not build its sandbox: {reason}. It needs "
        f"a writable /opt and /usr/local (run as root, e.g. the python:3.11-slim "
        f"container the CI gate uses)."
    )
    if _REQUIRED:
        pytest.fail(message + " CATERING_LIFECYCLE_E2E_REQUIRED=1 forbids skipping.")
    pytest.skip(message)


# ── transport stub — the ONE stubbed seam ────────────────────────────────────
class _BridgeStub(BaseHTTPRequestHandler):
    """Answers POST /send (what bridge_post calls) and GET (health probes).
    Every captured body lands in `sink`."""

    sink: list = []

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(raw)
        except Exception:  # noqa: BLE001 — a non-JSON body is still evidence
            doc = {"__unparsed__": raw}
        self.__class__.sink.append(doc)
        self._respond({"id": f"wamid.STUB.{len(self.__class__.sink):04d}"})

    def do_GET(self):  # noqa: N802
        self._respond({"status": "connected"})

    def _respond(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: ARG002
        return


def _closed_port() -> int:
    """An ephemeral port with nothing listening — the positive control's target."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── sandbox ──────────────────────────────────────────────────────────────────
@dataclass
class Sandbox:
    root: Path
    bridge_url: str
    sent: list
    created_bin_links: list = field(default_factory=list)

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def leads_path(self) -> Path:
        return self.state / "catering-leads.json"

    @property
    def proposals_path(self) -> Path:
        return self.state / "catering-proposals.json"

    @property
    def menu_path(self) -> Path:
        return self.state / "catering-menu.json"

    @property
    def log_path(self) -> Path:
        return self.logs / "decisions.log"

    def leads(self) -> list:
        if not self.leads_path.exists():
            return []
        return json.loads(self.leads_path.read_text(encoding="utf-8"))["leads"]

    def lead(self, lead_id: str) -> dict:
        for row in self.leads():
            if row.get("lead_id") == lead_id:
                return row
        raise AssertionError(
            f"lead {lead_id!r} not in {[row.get('lead_id') for row in self.leads()]}")

    def proposal_sets(self) -> list:
        if not self.proposals_path.exists():
            return []
        return json.loads(self.proposals_path.read_text(encoding="utf-8"))["sets"]

    def audit_types(self) -> list:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line).get("type")
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def to(self, jid: str) -> list:
        return [m for m in self.sent if m.get("chatId") == jid]


def _config(root: Path) -> dict:
    return {
        "schema_version": 1,
        "customer": {"name": "Triveni Lifecycle E2E", "location_id": "loc_e2e",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": OWNER_JID},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        # deposit_pct 0 keeps money structurally out of this test: no deposit can
        # be armed, so no scenario here can mint a payment link.
        "catering": {"enabled": True, "deposit_pct": 0,
                     "deposit_threshold_guests": 50, "stale_after_hours": 336},
    }


def _claim_deploy_root(root: Path) -> None:
    """Point /opt/shift-agent at the sandbox. A deployed install is a real
    directory, so a real directory here means a real box: refuse. A leftover
    SYMLINK is a crashed run of this file; replace it."""
    if DEPLOY_ROOT.is_symlink():
        DEPLOY_ROOT.unlink()
    elif DEPLOY_ROOT.exists():
        _unavailable(
            f"{DEPLOY_ROOT} is a real directory — this looks like a deployed "
            f"shift-agent box and this test will not touch one"
        )
    try:
        DEPLOY_ROOT.parent.mkdir(parents=True, exist_ok=True)
        DEPLOY_ROOT.symlink_to(root, target_is_directory=True)
    except OSError as e:
        _unavailable(f"cannot symlink {DEPLOY_ROOT} -> {root} ({type(e).__name__}: {e})")


def _install_scripts(sb: Sandbox) -> None:
    """Symlink the REAL scripts into /usr/local/bin, the way the deploy installs
    them. Symlinks, not copies, so there is no chance of running a mutated
    variant of a script this test claims to exercise unmodified."""
    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _unavailable(f"cannot create {BIN_DIR} ({type(e).__name__}: {e})")
    for source_dir in (CATERING_SCRIPTS, SHIFT_SCRIPTS):
        for script in sorted(source_dir.iterdir()):
            if not script.is_file() or script.suffix in {".pyc", ".md"}:
                continue
            target = BIN_DIR / script.name
            if target.exists() and not target.is_symlink():
                _unavailable(
                    f"{target} already exists as a real file — this looks like a "
                    f"deployed shift-agent box and this test will not overwrite it"
                )
            try:
                if target.is_symlink() or target.exists():
                    target.unlink()
                # COPY + chmod 755, not symlink. The catering scripts are tracked
                # 100644 and the real deploy installs them executable; a symlink
                # inherits the tracked 644 and every exec raises PermissionError.
                # This passed locally only because the Windows bind mount
                # synthesises an exec bit — the same masking that once hid a
                # deploy bug behind `[ -x ]`. Copying mirrors what the deploy
                # actually does, so the test cannot pass for a reason production
                # does not share.
                shutil.copy2(script, target)
                target.chmod(0o755)
            except OSError as e:
                _unavailable(f"cannot install {target} ({type(e).__name__}: {e})")
            sb.created_bin_links.append(target)


def _install_venv_shim() -> None:
    """finalize-catering-menu's shebang IS /usr/local/lib/hermes-agent/venv/bin/python,
    and select-catering-proposal spawns it by that absolute path. Without the shim
    the chain degrades to EXIT_DEPENDENCY_DOWN(6) and the proposal set records
    `finalize_exit_6` — which reads exactly like a product fault."""
    try:
        HERMES_VENV_BIN.mkdir(parents=True, exist_ok=True)
        if HERMES_VENV_PY.is_symlink():
            HERMES_VENV_PY.unlink()
        elif HERMES_VENV_PY.exists():
            _unavailable(
                f"{HERMES_VENV_PY} exists and is not a symlink — this looks like a "
                f"real Hermes install and this test will not overwrite it"
            )
        HERMES_VENV_PY.symlink_to(sys.executable)
    except OSError as e:
        _unavailable(f"cannot create the Hermes venv shim ({type(e).__name__}: {e})")


@pytest.fixture
def sandbox(tmp_path):
    """One pristine deployed-shaped tree per test, rooted in tmp_path."""
    root = tmp_path / "shift-agent"
    for sub in ("state", "logs", "templates"):
        (root / sub).mkdir(parents=True)
    # The deployed tree is FLAT: the scripts do sys.path.insert(0, "/opt/shift-agent").
    for module in PLATFORM_DIR.glob("*.py"):
        shutil.copy(module, root / module.name)
    for template in TEMPLATES_SRC.iterdir():
        if template.is_file():
            shutil.copy(template, root / "templates" / template.name)
    (root / "config.yaml").write_text(yaml.safe_dump(_config(root)), encoding="utf-8")
    (root / "roster.json").write_text(json.dumps({"employees": []}), encoding="utf-8")
    shutil.copy(MENU_FIXTURE, root / "state" / "catering-menu.json")

    _BridgeStub.sink = []
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    sb = Sandbox(
        root=root,
        bridge_url=f"http://127.0.0.1:{server.server_port}/send",
        sent=_BridgeStub.sink,
    )
    _claim_deploy_root(root)
    _install_scripts(sb)
    _install_venv_shim()
    _assert_sandbox_is_not_production(sb, tmp_path)
    _assert_venv_shim()
    try:
        yield sb
    finally:
        server.shutdown()
        for link in sb.created_bin_links:
            # These are real copies now, not symlinks — remove either form, or
            # the next run in the same container trips the "looks like a
            # deployed box" refusal on leftovers this test created itself.
            if link.is_symlink() or link.exists():
                try:
                    link.unlink()
                except OSError:
                    pass
        if HERMES_VENV_PY.is_symlink():
            HERMES_VENV_PY.unlink()
        if DEPLOY_ROOT.is_symlink():
            DEPLOY_ROOT.unlink()


# ── guards the harness itself must pass before any product code runs ─────────
def _assert_sandbox_is_not_production(sb: Sandbox, tmp_path: Path) -> None:
    """RIGOR 7. The scripts write to the literal string '/opt/shift-agent/state';
    this proves that string resolves into tmp_path and not onto a real box."""
    assert DEPLOY_ROOT.is_symlink(), (
        f"{DEPLOY_ROOT} must be the sandbox symlink, not a real tree"
    )
    resolved_state = (DEPLOY_ROOT / "state").resolve()
    assert resolved_state == sb.state.resolve(), (
        f"/opt/shift-agent/state resolves to {resolved_state}, not the sandbox "
        f"{sb.state.resolve()}"
    )
    assert str(resolved_state).startswith(str(tmp_path.resolve()) + os.sep), (
        f"the state dir the scripts will write ({resolved_state}) is not under "
        f"tmp_path ({tmp_path.resolve()}) — refusing to run"
    )


def _assert_venv_shim() -> None:
    """RIGOR 3. A missing Hermes interpreter degrades the whole chain to exit 6
    silently; assert it up front so that can never be mistaken for a product bug."""
    assert HERMES_VENV_PY.exists(), (
        f"{HERMES_VENV_PY} is missing — select-catering-proposal spawns "
        f"finalize-catering-menu through it, and without it the chain returns "
        f"EXIT_DEPENDENCY_DOWN(6) with no product fault involved"
    )
    probe = subprocess.run([str(HERMES_VENV_PY), "-c", "print('ok')"],
                           capture_output=True, text=True, timeout=60)
    assert probe.returncode == 0 and probe.stdout.strip() == "ok", (
        f"the Hermes venv shim is not a working interpreter: rc={probe.returncode} "
        f"stdout={probe.stdout!r} stderr={probe.stderr!r}"
    )


def _child_env(sb: Sandbox, bridge_url=None) -> dict:
    env = dict(os.environ)
    # conftest re-points these at a per-test tmp dir for in-process tests. Here the
    # deployed DEFAULTS are what must be exercised — they land in the sandbox via
    # the /opt/shift-agent symlink — so drop the overrides rather than shadow them.
    for key in ("SHIFT_AGENT_DECISIONS_LOG_PATH", "SHIFT_AGENT_NOTIFY_DEDUP_STATE",
                "SHIFT_AGENT_NOTIFY_FAILED_LOG", "PYTHONPATH"):
        env.pop(key, None)
    env.update({
        "HERMES_BRIDGE_URL": bridge_url or sb.bridge_url,
        "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1",
        # safe_io's prod-write guard compares UNRESOLVED prefixes, so every write
        # through the /opt/shift-agent symlink looks like a production write to it.
        # Cleared only because _assert_sandbox_is_not_production already proved
        # that prefix resolves into tmp_path.
        "SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _fmt(name: str, args, proc: subprocess.CompletedProcess, expect) -> str:
    return (
        f"{name} {' '.join(str(a) for a in args)}\n"
        f"  expected exit {expect}, got {proc.returncode}\n"
        f"  stdout: {(proc.stdout or '').strip()[:2000]}\n"
        f"  stderr: {(proc.stderr or '').strip()[:2000]}"
    )


def run(sb: Sandbox, name: str, *args, expect: int = EXIT_OK, stdin=None,
        bridge_url=None, steps=None):
    """Invoke a REAL deployed script and hold it to an EXACT exit code."""
    proc = subprocess.run(
        [str(BIN_DIR / name), *[str(a) for a in args]],
        input=stdin, capture_output=True, text=True, timeout=180,
        env=_child_env(sb, bridge_url),
    )
    if steps is not None:
        steps.append(name)
    # RIGOR 1: an argparse usage error is a HARNESS bug wearing a refusal's exit
    # code. It must never read as a passing negative test.
    if proc.returncode == 2 and "usage:" in (proc.stderr or ""):
        pytest.fail(
            f"ARGPARSE USAGE ERROR invoking {name} — the harness called the script "
            f"wrong; this is never a product refusal.\n{_fmt(name, args, proc, expect)}"
        )
    # RIGOR 2: any unexpected exit fails, with both streams in the message.
    assert proc.returncode == expect, _fmt(name, args, proc, expect)
    return proc


def create_lead(sb, message_id: str, *, phone: str = CUSTOMER_PHONE, name: str = "Priya",
                raw: str = "Hi! Need catering for 50 people, South Indian please",
                fields=None, expect: int = EXIT_OK, bridge_url=None, steps=None):
    return run(
        sb, "create-catering-lead",
        "--customer-phone", phone, "--customer-name", name,
        "--raw-inquiry", raw, "--message-id", message_id,
        "--fields-json", json.dumps(fields or _base_fields()),
        expect=expect, bridge_url=bridge_url, steps=steps,
    )


def open_lead(sb, message_id: str = "wamid.LC.001", **kw) -> dict:
    """Create one lead and return it, asserting the shape every scenario relies on."""
    before = {row["lead_id"] for row in sb.leads()}
    create_lead(sb, message_id, **kw)
    fresh = [row for row in sb.leads() if row["lead_id"] not in before]
    assert len(fresh) == 1, f"expected exactly one new lead, got {fresh}"
    lead = fresh[0]
    assert lead["status"] == "AWAITING_OWNER_APPROVAL", lead["status"]
    assert lead["owner_approval_code"], "no owner_approval_code minted"
    assert lead["deposit_required"] is False, (
        "deposit_pct=0 must leave the deposit disarmed — no money may move here"
    )
    return lead


def assert_only_stub_saw_traffic(sb: Sandbox) -> None:
    """RIGOR 6. Every captured outbound is addressed and well-formed, and no
    outbound went anywhere but the two JIDs this agent talks to. A send that
    escaped to another seam would leave these counts disagreeing."""
    assert all(m.get("chatId") and m.get("message") for m in sb.sent), (
        f"the stub captured a malformed outbound: {sb.sent}"
    )
    assert len(sb.sent) == len(sb.to(OWNER_JID)) + len(sb.to(CUSTOMER_JID)), (
        f"an outbound went to neither the owner nor the customer: "
        f"{[m.get('chatId') for m in sb.sent]}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# THE GREEN LIFECYCLE
# ═════════════════════════════════════════════════════════════════════════════
HAPPY_PATH_STEPS = [
    "create-catering-lead",
    "create-catering-proposal-options",
    "select-catering-proposal",
    "apply-catering-owner-decision",
]


def test_full_lifecycle_inquiry_to_quote_delivered(sandbox):
    """inquiry -> deterministic options -> customer picks -> CUSTOMER_FINALIZED
    -> owner approves -> SENT_TO_CUSTOMER -> the quote is captured by the stub.

    One test, because the value is that these COMPOSE: a per-step suite cannot
    catch a break BETWEEN the steps."""
    sb = sandbox
    steps = []

    # 1. inquiry
    lead = open_lead(sb, "wamid.LC.001", steps=steps)
    lead_id, code = lead["lead_id"], lead["owner_approval_code"]
    assert len(sb.to(OWNER_JID)) == 1, "owner approval card not sent"
    assert code in sb.to(OWNER_JID)[0]["message"]
    assert len(sb.to(CUSTOMER_JID)) == 1, "customer acknowledgement not sent"

    # 2. deterministic proposal generation from the 78-item menu (no LLM)
    run(sb, "create-catering-proposal-options",
        "--lead-id", lead_id, "--customer-jid", CUSTOMER_JID,
        "--source-message-id", "wamid.LC.001",
        "--request-text", "what can you do for 50 people",
        "--auto-generate-from-menu", steps=steps)
    sets = sb.proposal_sets()
    assert len(sets) == 1, f"expected one proposal set, got {sets}"
    options = sets[0]["options"]
    assert len(options) >= 2, f"auto-generation produced {len(options)} option(s)"
    for option in options:
        # RIGOR 5: the contract field is `item_names`. Reading `items` returns a
        # false 0 and reads as "auto-generation is broken" — make that impossible.
        assert "item_names" in option, (
            f"option {option.get('option_id')} has no `item_names` — the contract "
            f"field; keys present: {sorted(option)}"
        )
        assert option["item_names"], f"option {option.get('option_id')} has no items"
        assert "items" not in option, (
            "the option schema grew an `items` field; every assertion in this file "
            "reads `item_names` and would now be reading the wrong one"
        )

    # 3. the customer picks an option -> CUSTOMER_FINALIZED
    run(sb, "select-catering-proposal",
        "--lead-id", lead_id, "--customer-jid", CUSTOMER_JID,
        "--customer-message-id", "wamid.LC.002",
        "--selection-text", "option 1 please", steps=steps)
    lead = sb.lead(lead_id)
    assert lead["status"] == "CUSTOMER_FINALIZED", lead["status"]
    assert lead["selected_items"], "the selection recorded no items"
    assert sb.proposal_sets()[0]["status"] == "SELECTED", sb.proposal_sets()[0]["status"]
    assert sb.proposal_sets()[0]["selected_option_id"] == "1"

    # 4. the owner approves -> the customer gets the quote
    before = len(sb.to(CUSTOMER_JID))
    run(sb, "apply-catering-owner-decision",
        "--sender-role", "owner", "--code", code,
        "--decision", "approve", "--quote-from-lead-state", steps=steps)
    lead = sb.lead(lead_id)
    assert lead["status"] == "SENT_TO_CUSTOMER", lead["status"]
    assert lead["quote_total_usd"], "the approved lead carries no total"

    # BOTH halves: the state moved AND the customer actually received something.
    quotes = sb.to(CUSTOMER_JID)[before:]
    assert len(quotes) == 1, f"expected exactly one quote send, got {len(quotes)}"
    body = quotes[0]["message"]
    assert str(lead["quote_total_usd"]) in body, (
        f"the quote the customer received does not carry the approved total "
        f"{lead['quote_total_usd']}: {body!r}"
    )
    assert lead["selected_items"][0]["name"] in body, (
        f"the quote does not name the items the customer selected: {body!r}"
    )

    # the audit chain recorded the transition, not just the state file
    assert "catering_lead_status_change" in sb.audit_types()

    # RIGOR 4: a step that silently never ran must not read as a pass.
    assert steps == HAPPY_PATH_STEPS, (
        f"the scenario did not execute the steps it claims to prove.\n"
        f"  ran:      {steps}\n  expected: {HAPPY_PATH_STEPS}"
    )
    assert_only_stub_saw_traffic(sb)


# ═════════════════════════════════════════════════════════════════════════════
# NEGATIVES — each asserts the SPECIFIC refusal, never merely "nonzero"
# ═════════════════════════════════════════════════════════════════════════════
def test_unknown_approval_code_is_refused(sandbox):
    sb = sandbox
    open_lead(sb, "wamid.LC.010")
    before = len(sb.sent)
    proc = run(sb, "apply-catering-owner-decision",
               "--sender-role", "owner", "--code", "#ZZZZZ",
               "--decision", "approve", "--quote-from-lead-state",
               expect=EXIT_NOT_FOUND)
    assert "no recoverable lead with code #ZZZZZ" in proc.stderr, proc.stderr
    assert len(sb.sent) == before, "an unknown code produced an outbound message"


def test_duplicate_inbound_message_id_replays_instead_of_minting(sandbox):
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.020")
    before_leads, before_sent = len(sb.leads()), len(sb.sent)

    proc = create_lead(sb, "wamid.LC.020", raw="same message, delivered twice")
    replay = json.loads(proc.stdout.strip().splitlines()[-1])
    assert replay["idempotent_replay"] is True, replay
    assert replay["lead_id"] == lead["lead_id"], replay
    assert replay["approval_code"] == lead["owner_approval_code"], replay
    assert len(sb.leads()) == before_leads, "a duplicate inbound minted a second lead"
    assert len(sb.sent) == before_sent, "a duplicate inbound re-sent the owner card"


def test_owner_reject_moves_the_lead_and_sends_nothing_to_the_customer(sandbox):
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.030")
    before = len(sb.to(CUSTOMER_JID))
    run(sb, "apply-catering-owner-decision",
        "--sender-role", "owner", "--code", lead["owner_approval_code"],
        "--decision", "reject", "--reason", "fully booked that weekend")
    assert sb.lead(lead["lead_id"])["status"] == "OWNER_REJECTED"
    # Deliberate product behaviour (the apply script sends nothing customer-facing
    # on reject/edit). Pinned so a change to it is a decision, not a drift.
    assert len(sb.to(CUSTOMER_JID)) == before, (
        "reject sent the customer a message; that is a product change, not a bug fix"
    )


def test_owner_edit_moves_the_lead_to_owner_edited(sandbox):
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.040")
    before = len(sb.to(CUSTOMER_JID))
    run(sb, "apply-catering-owner-decision",
        "--sender-role", "owner", "--code", lead["owner_approval_code"],
        "--decision", "edit", "--edit-text", "make it 45 guests, add paneer tikka")
    assert sb.lead(lead["lead_id"])["status"] == "OWNER_EDITED"
    assert len(sb.to(CUSTOMER_JID)) == before


@pytest.mark.parametrize("role", ["customer", "employee", "unknown"])
def test_non_owner_cannot_apply_an_owner_decision(sandbox, role):
    """A screenshot-forwarded #XXXXX must not let a customer approve their own
    quote. EXIT_PRIVILEGE_DENIED(12), refused before any state read."""
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.050")
    before_status = lead["status"]
    before_sent = len(sb.sent)
    proc = run(sb, "apply-catering-owner-decision",
               "--sender-role", role, "--code", lead["owner_approval_code"],
               "--decision", "approve", "--quote-from-lead-state",
               expect=EXIT_PRIVILEGE_DENIED)
    assert "privilege denied" in proc.stderr, proc.stderr
    assert f"sender_role={role!r}" in proc.stderr, proc.stderr
    assert sb.lead(lead["lead_id"])["status"] == before_status, "the state moved anyway"
    assert len(sb.sent) == before_sent, "a denied caller still caused an outbound"


def test_owner_approve_of_a_non_finalized_lead_is_refused_and_reprompts(sandbox):
    """PR-CF1. AWAITING_OWNER_APPROVAL, no selected_items, no quote source:
    EXIT_TRUTH_GUARD_FAILED(11), and the owner is told why over the bridge.

    Asserted against the SCRIPT, not against cf-router routing — the routing
    side of this seam has its own suite."""
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.060")
    assert not lead.get("selected_items"), "precondition: the lead must be un-finalized"
    assert lead.get("customer_finalized_at") is None

    before_customer = len(sb.to(CUSTOMER_JID))
    before_owner = len(sb.to(OWNER_JID))
    proc = run(sb, "apply-catering-owner-decision",
               "--sender-role", "owner", "--code", lead["owner_approval_code"],
               "--decision", "approve",
               expect=EXIT_TRUTH_GUARD_FAILED)
    assert "PR-CF1" in proc.stderr and "not customer-finalized" in proc.stderr, proc.stderr

    reprompts = sb.to(OWNER_JID)[before_owner:]
    assert len(reprompts) == 1, f"expected one owner reprompt, got {reprompts}"
    assert "hasn't finalized" in reprompts[0]["message"], reprompts[0]["message"]
    assert len(sb.to(CUSTOMER_JID)) == before_customer, (
        "the refused approve still sent the customer something"
    )
    assert sb.lead(lead["lead_id"])["status"] == "AWAITING_OWNER_APPROVAL"
    assert "catering_quote_skill_failed" in sb.audit_types()


def test_ambiguous_selection_asks_the_customer_to_clarify(sandbox):
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.070")
    run(sb, "create-catering-proposal-options",
        "--lead-id", lead["lead_id"], "--customer-jid", CUSTOMER_JID,
        "--source-message-id", "wamid.LC.070",
        "--request-text", "what can you do for 50 people",
        "--auto-generate-from-menu")
    before = len(sb.to(CUSTOMER_JID))

    proc = run(sb, "select-catering-proposal",
               "--lead-id", lead["lead_id"], "--customer-jid", CUSTOMER_JID,
               "--customer-message-id", "wamid.LC.071",
               "--selection-text", "hmm, maybe option 1 or option 2, what do you think?",
               expect=EXIT_INVALID_INPUT)
    # EXIT_INVALID_INPUT is also argparse's exit code; `run` already proved this is
    # not a usage error, and the clarification below proves the script really ran.
    assert "usage:" not in proc.stderr, proc.stderr

    clarifications = sb.to(CUSTOMER_JID)[before:]
    assert len(clarifications) == 1, f"expected one clarification, got {clarifications}"
    assert "reply with the option number" in clarifications[0]["message"].lower(), (
        clarifications[0]["message"]
    )
    assert sb.lead(lead["lead_id"])["status"] == "AWAITING_OWNER_APPROVAL", (
        "an ambiguous reply advanced the lead"
    )


def test_repeat_owner_approve_does_not_send_the_quote_twice(sandbox):
    """Idempotency. Once the lead is SENT_TO_CUSTOMER the code no longer matches
    any recoverable state, so a replayed approve is refused by exit code rather
    than by luck — and, critically, sends nothing."""
    sb = sandbox
    lead = open_lead(sb, "wamid.LC.080")
    lead_id, code = lead["lead_id"], lead["owner_approval_code"]
    run(sb, "create-catering-proposal-options",
        "--lead-id", lead_id, "--customer-jid", CUSTOMER_JID,
        "--source-message-id", "wamid.LC.080",
        "--request-text", "options for 50 please", "--auto-generate-from-menu")
    run(sb, "select-catering-proposal",
        "--lead-id", lead_id, "--customer-jid", CUSTOMER_JID,
        "--customer-message-id", "wamid.LC.081", "--selection-text", "option 2")
    run(sb, "apply-catering-owner-decision",
        "--sender-role", "owner", "--code", code,
        "--decision", "approve", "--quote-from-lead-state")
    assert sb.lead(lead_id)["status"] == "SENT_TO_CUSTOMER"
    after_first = len(sb.sent)

    proc = run(sb, "apply-catering-owner-decision",
               "--sender-role", "owner", "--code", code,
               "--decision", "approve", "--quote-from-lead-state",
               expect=EXIT_NOT_FOUND)
    assert f"no recoverable lead with code {code}" in proc.stderr, proc.stderr
    assert "in status SENT_TO_CUSTOMER" in proc.stderr, proc.stderr
    assert len(sb.sent) == after_first, "the replayed approve sent a second quote"
    assert_only_stub_saw_traffic(sb)


# ═════════════════════════════════════════════════════════════════════════════
# HARNESS INTEGRITY
# ═════════════════════════════════════════════════════════════════════════════
def test_positive_control_stub_is_on_the_send_seam(sandbox):
    """THIS TEST EXISTS SO A DEAD STUB CANNOT MASQUERADE AS GREEN.

    Every other test concludes "the customer received X" from the stub's sink.
    That conclusion is worthless unless the stub is genuinely the seam the
    production send path reaches. Point HERMES_BRIDGE_URL at a CLOSED port and
    the same inquiry must FAIL to deliver — EXIT_DEPENDENCY_DOWN(6), nothing in
    the sink. If the stub were dead code the send would "succeed" regardless and
    this test would fail."""
    sb = sandbox
    dead = f"http://127.0.0.1:{_closed_port()}/send"
    create_lead(sb, "wamid.LC.090", bridge_url=dead, expect=EXIT_DEPENDENCY_DOWN)
    assert sb.sent == [], (
        f"the stub captured a send aimed at the closed port {dead} — the sink is "
        f"not measuring the real send seam: {sb.sent}"
    )
    # The lead itself is still written: the failure is delivery, not creation.
    assert len(sb.leads()) == 1, "the inquiry was lost along with the send"


def test_sandbox_never_resolves_to_production(sandbox, tmp_path):
    """RIGOR 7, re-asserted after a real run so it covers the paths the scripts
    ACTUALLY wrote, not just the ones the fixture set up."""
    sb = sandbox
    open_lead(sb, "wamid.LC.100")
    written = sb.leads_path
    assert written.exists(), "the run wrote no leads file at all"
    assert str(written.resolve()).startswith(str(tmp_path.resolve()) + os.sep), written
    assert (DEPLOY_ROOT / "state" / "catering-leads.json").resolve() == written.resolve()
    assert DEPLOY_ROOT.is_symlink(), "/opt/shift-agent must never be a real tree here"
    assert not Path("/opt/shift-agent/state").is_symlink(), (
        "the real state directory must live in tmp_path, reached via the root symlink"
    )
    assert sb.log_path.exists(), "the audit chokepoint wrote nowhere"
    assert str(sb.log_path.resolve()).startswith(str(tmp_path.resolve()) + os.sep)


def test_menu_fixture_is_the_78_item_production_menu(sandbox):
    """The menu under test is the real one, not a toy. A shrunken fixture would
    quietly weaken every auto-generation assertion in this file."""
    menu = json.loads(MENU_FIXTURE.read_text(encoding="utf-8"))
    assert len(menu["items"]) == MENU_FIXTURE_ITEM_COUNT, len(menu["items"])
    copied = json.loads(sandbox.menu_path.read_text(encoding="utf-8"))
    assert copied == menu, "the sandbox copy diverged from the fixture"


def test_zz_menu_fixture_was_not_mutated():
    """The fixture is READ-ONLY input. Anything that writes back to it (a script
    resolving the menu path to the repo, a helper "fixing" it) is a defect."""
    digest = hashlib.sha256(MENU_FIXTURE.read_bytes()).hexdigest()
    assert digest == _MENU_DIGEST_AT_IMPORT, (
        f"{MENU_FIXTURE} changed during the run: {_MENU_DIGEST_AT_IMPORT} -> {digest}"
    )


_MENU_DIGEST_AT_IMPORT = hashlib.sha256(MENU_FIXTURE.read_bytes()).hexdigest()
