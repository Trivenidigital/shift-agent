"""Wave-2 DRAFT tier — owner receipt intake and review-only extraction.

Two surfaces, both pinned here because they are only safe together:

  * the cf-router cession gate (owner + media + explicit receipt caption), and
  * `extract-receipt --review-only`, which must persist a truthful DRAFTED lead
    and must not mint an approval code, request approval, or touch QBO.

The tier exists because there is no reachable approval path: `RealQBOClient`
raises on construction and `qbo_client_mode` defaults to "mock", so an
AWAITING_OWNER_APPROVAL row would make the durable record claim an approval was
requested — and would later make the retention timer tell the owner an approval
they never received had expired.

Linux-only: both surfaces import fcntl-using `safe_io`.
"""
from __future__ import annotations

import os
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import importlib.machinery
import importlib.util
import json
import platform
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="cf-router actions and extract-receipt import fcntl-using safe_io",
)

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "src" / "plugins" / "cf-router"
EXTRACT_SCRIPT = (ROOT / "src" / "agents" / "expense_bookkeeper" / "scripts"
                  / "extract-receipt")


def _load_plugin():
    pkg = "cf_router_expense_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(spec)

    def _load(name):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(
            full, str(PLUGIN_DIR / f"{name}.py"))
        sp = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(sp)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    actions_mod = _load("actions")
    hooks_mod = _load("hooks")
    return hooks_mod, actions_mod


@pytest.fixture(scope="module")
def plugin():
    return _load_plugin()


@pytest.fixture(scope="module")
def extract_mod():
    from importlib.machinery import SourceFileLoader
    sys.path.insert(0, str(ROOT / "src" / "platform"))
    loader = SourceFileLoader("extract_receipt_draft_test", str(EXTRACT_SCRIPT))
    spec = importlib.util.spec_from_loader("extract_receipt_draft_test", loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "extract_receipt_draft_test"
    loader.exec_module(mod)
    return mod


# ── caption predicate ───────────────────────────────────────────────────────

@pytest.mark.parametrize("caption", [
    "Expense receipt — review this",
    "expense receipt",
    "Receipt",
    "receipt.",
    "Expense",
    "log this expense",
    "log receipt",
    "book this receipt",
    "expense this",
])
def test_receipt_caption_accepts_explicit_expense_language(plugin, caption):
    _, actions_mod = plugin
    assert actions_mod.is_receipt_caption(caption) is True


@pytest.mark.parametrize("caption", [
    "",
    "here you go",
    "check this out",
    "update menu",
    "new menu",
    "make me a flyer for diwali",
    "I got the receipts from the party",   # not an explicit ingestion request
    "did you receipt the order",
])
def test_receipt_caption_rejects_everything_else(plugin, caption):
    """Deliberately NOT a general receipt-image recognizer — an explicit
    caption trigger only. Anything broader would be the intent classifier this
    wave forbids."""
    _, actions_mod = plugin
    assert actions_mod.is_receipt_caption(caption) is False


def test_menu_caption_routing_is_unchanged(plugin):
    """The menu trigger must keep its exact behavior, and the two vocabularies
    must not overlap — otherwise one arm would silently steal the other."""
    _, actions_mod = plugin
    assert actions_mod.is_menu_update_caption("update menu") is True
    assert actions_mod.is_menu_update_caption("menu") is True
    assert actions_mod.is_receipt_caption("update menu") is False
    assert actions_mod.is_menu_update_caption("expense receipt") is False


# ── cession gate ────────────────────────────────────────────────────────────

def _gate(hooks_mod, monkeypatch, *, text, media, role, owner_capability=None):
    """Drive the gate by OWNER CAPABILITY, which is what it now authorizes on.

    `role` is retained because the gate still records it in the audit row, but
    it is no longer proof of anything. `owner_capability` defaults to the
    legacy scalar equivalence (`role == "owner"`) so every pre-existing case
    keeps its original meaning; the dual-role cases set it explicitly.
    """
    if owner_capability is None:
        owner_capability = (role == "owner")
    monkeypatch.setattr(hooks_mod.actions, "audit_intercepted",
                        lambda **kw: None, raising=False)
    monkeypatch.setattr(hooks_mod.actions, "has_owner_capability",
                        lambda chat_id: owner_capability, raising=False)
    return hooks_mod._receipt_caption_cedes_to_dispatcher(
        text, "chat@lid", media_path=media, role=role)


def test_gate_claims_owner_with_media_and_receipt_caption(plugin, monkeypatch):
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt — review this",
                 media="/tmp/img_1.jpg", role="owner") is True


@pytest.mark.parametrize("role", ["employee", "customer", "guest", ""])
def test_gate_rejects_non_owner_roles(plugin, monkeypatch, role):
    """An expense is a money record. The menu gate admits verified employees;
    this one must not."""
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt",
                 media="/tmp/img_1.jpg", role=role) is False


def test_gate_claims_dual_role_owner_whose_scalar_says_employee(plugin, monkeypatch):
    """Multi-role: owner membership is accepted even when the scalar says employee.

    A principal who is BOTH owner and an active roster employee resolves scalar
    `employee` by LID (the LID branch checks the roster first), which is exactly
    how a genuine owner's receipt was refused in production on 2026-08-10.
    """
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt — review this",
                 media="/tmp/img_1.jpg", role="employee",
                 owner_capability=True) is True


def test_gate_rejects_employee_without_owner_membership(plugin, monkeypatch):
    """The safety rule: employee membership alone NEVER satisfies the owner gate."""
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt — review this",
                 media="/tmp/img_1.jpg", role="employee",
                 owner_capability=False) is False


def test_gate_ignores_a_scalar_owner_claim_without_capability(plugin, monkeypatch):
    """The scalar is NOT proof: a caller claiming `owner` cannot open a money gate.

    Authorization has exactly one path — membership. This pins that the gate
    resolves it itself rather than trusting the label it was handed, so a stale
    or wrong caller-supplied role cannot admit a receipt.
    """
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt",
                 media="/tmp/img_1.jpg", role="owner",
                 owner_capability=False) is False


def test_gate_consults_membership_exactly_once(plugin, monkeypatch):
    """Authorization is resolved by the gate, on every call, for every role."""
    hooks_mod, _ = plugin
    calls = []
    monkeypatch.setattr(hooks_mod.actions, "audit_intercepted",
                        lambda **kw: None, raising=False)
    monkeypatch.setattr(hooks_mod.actions, "has_owner_capability",
                        lambda chat_id: calls.append(chat_id) or True, raising=False)
    assert hooks_mod._receipt_caption_cedes_to_dispatcher(
        "Expense receipt", "chat@lid", media_path="/tmp/img_1.jpg",
        role="owner") is True
    assert calls == ["chat@lid"]


def test_gate_rejects_owner_image_only(plugin, monkeypatch):
    """Image-only owner media stays unsupported this wave — a bare photo is
    genuinely ambiguous between a menu and a receipt."""
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="",
                 media="/tmp/img_1.jpg", role="owner") is False


def test_gate_rejects_receipt_caption_without_media(plugin, monkeypatch):
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt",
                 media=None, role="owner") is False


def test_gate_rejects_unrelated_owner_caption_with_media(plugin, monkeypatch):
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch, text="here you go",
                 media="/tmp/img_1.jpg", role="owner") is False


def test_explicit_flyer_signal_vetoes_the_cession(plugin, monkeypatch):
    """Flyer work keeps precedence when the owner explicitly says flyer."""
    hooks_mod, _ = plugin
    assert _gate(hooks_mod, monkeypatch,
                 text="make this receipt into a flyer",
                 media="/tmp/img_1.jpg", role="owner") is False


def test_gate_never_raises_into_dispatch(plugin, monkeypatch):
    """A cession decision must fail closed, never claim the inbound."""
    hooks_mod, _ = plugin
    monkeypatch.setattr(hooks_mod.actions, "is_receipt_caption",
                        lambda _t: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _gate(hooks_mod, monkeypatch, text="Expense receipt",
                 media="/tmp/img_1.jpg", role="owner") is False


def test_receipt_arm_precedes_every_flyer_claim(plugin):
    """An unresolved Flyer Studio project must not be able to ingest an owner
    receipt as a reference asset — the live F0226 failure mode. Pinned
    structurally: the cession call must appear before the flyer arms."""
    src = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    # Compare CALL SITES, not identifiers: the same names appear in explanatory
    # comments above the gate, and matching prose would assert nothing.
    receipt_at = src.index("if _receipt_caption_cedes_to_dispatcher(")
    escape_at = src.index("escape_result = _try_flyer_catering_escape_gate(")
    primary_at = src.index(
        "and actions.should_start_new_flyer_over_active(text, has_media=bool(media_path))")
    assert receipt_at < escape_at, "receipt cession must precede the catering escape gate"
    assert receipt_at < primary_at, (
        "receipt cession must precede the flyer primary arm that created F0226"
    )
    # And it must sit in the same tier as the proven menu cession.
    assert src.index("if _menu_caption_cedes_to_dispatcher(") < receipt_at


def test_invoke_extract_receipt_always_passes_review_only(plugin, monkeypatch):
    """`--review-only` is the authority boundary, not an option."""
    _, actions_mod = plugin
    seen = {}

    class _R:
        returncode, stdout, stderr = 0, "{}", ""

    def _fake_run(argv, **kw):
        seen["argv"] = argv
        return _R()

    monkeypatch.setattr(actions_mod.subprocess, "run", _fake_run)
    actions_mod.invoke_extract_receipt(
        image_path="/tmp/i.jpg", source_image_id="wa1", sender_phone="+1904",
        sender_lid="1@lid")
    argv = seen["argv"]
    assert "--review-only" in argv
    assert argv[argv.index("--image-path") + 1] == "/tmp/i.jpg"
    assert argv[argv.index("--sender-lid") + 1] == "1@lid"


# ── review-only extraction ──────────────────────────────────────────────────

def _write_config(tmp_path):
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {"enabled": True, "qbo_client_mode": "mock"},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


_VISION = {
    "vendor_name": "Costco Wholesale",
    "vendor_normalized": "Costco",
    "receipt_date": "2026-08-09",
    "total_cents": 4210,
    "currency": "USD",
    "line_items": [{"description": "Paper goods", "amount_cents": 4210}],
    "extraction_confidence": 0.94,
}


@pytest.fixture
def draft_env(extract_mod, monkeypatch, tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    state = tmp_path / "state" / "expense-bookkeeper"
    receipts = state / "receipts"
    logs = tmp_path / "logs"
    receipts.mkdir(parents=True)
    logs.mkdir()
    image = tmp_path / "receipt.jpg"
    PIL.new("RGB", (12, 12), (200, 200, 200)).save(image, "JPEG")

    extract_mod.CONFIG_PATH = _write_config(tmp_path)
    extract_mod.LEADS_PATH = state / "leads.json"
    extract_mod.LOG_PATH = logs / "decisions.log"
    extract_mod.RECEIPTS_DIR = receipts
    # Render from the repo templates. Deploy installs this whole directory to
    # /opt/shift-agent/templates/ via the existing expense glob, so the new
    # review card ships without a deploy-script change.
    extract_mod.TEMPLATE_DIR = (
        ROOT / "src" / "agents" / "expense_bookkeeper" / "templates")
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", str(receipts) + "/")
    monkeypatch.setattr(extract_mod, "_call_vision", lambda *a, **k: dict(_VISION))
    # Classification is a SECOND model call; stub it too, or the run dies on a
    # missing OPENROUTER_API_KEY instead of exercising the DRAFT path.
    monkeypatch.setattr(extract_mod, "_classify_text", lambda *a, **k: {
        "is_business": True, "qbo_account": "Office Supplies",
        "confidence": 0.91, "rationale": "Warehouse-club supplies for the store",
    })

    def _run(source_id="wa_draft_1", review_only=True):
        sys.argv = [str(EXTRACT_SCRIPT),
                    "--image-path", str(image),
                    "--source-image-id", source_id,
                    "--owner-phone", "+19045550100"]
        if review_only:
            sys.argv.append("--review-only")
        return extract_mod.main()

    return {"run": _run, "leads": extract_mod.LEADS_PATH,
            "log": extract_mod.LOG_PATH, "receipts": receipts}


def _leads(env):
    return json.loads(env["leads"].read_text(encoding="utf-8"))["leads"]


def _audit_types(env):
    if not env["log"].exists():
        return []
    return [json.loads(l)["type"]
            for l in env["log"].read_text(encoding="utf-8").splitlines() if l.strip()]


def test_review_only_persists_drafted_with_no_approval_code(draft_env):
    assert draft_env["run"]() == 0
    leads = _leads(draft_env)
    assert len(leads) == 1
    assert leads[0]["status"] == "DRAFTED"
    assert leads[0].get("owner_approval_code") in (None, "")


def test_review_only_emits_no_approval_request_and_no_push_audit(draft_env):
    draft_env["run"]()
    types = _audit_types(draft_env)
    assert "expense_owner_approval_requested" not in types
    assert "expense_push_attempted" not in types
    assert "expense_lead_status_change" in types


def test_review_only_never_constructs_a_qbo_client(draft_env, monkeypatch):
    """No mock ledger, no transaction state, no client construction."""
    import qbo_client

    def _boom(*a, **k):
        raise AssertionError("DRAFT tier must not construct a QBO client")

    monkeypatch.setattr(qbo_client.MockQBOClient, "__init__", _boom, raising=False)
    monkeypatch.setattr(qbo_client.RealQBOClient, "__init__", _boom, raising=False)
    assert draft_env["run"]() == 0
    assert not (draft_env["receipts"].parent / "mock-qbo-pushed.json").exists()


def test_review_card_states_the_boundary_and_omits_approval_language(draft_env, capsys):
    draft_env["run"]()
    card = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["approval_card_text"]
    assert "Review only — this expense has not been posted to QuickBooks." in card
    for banned in ("To approve", "To reject", "undo", "pushed to QuickBooks", "#"):
        assert banned not in card, f"review card must not contain {banned!r}"


def test_review_card_preserves_the_extracted_facts(draft_env, capsys):
    draft_env["run"]()
    card = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["approval_card_text"]
    for fragment in ("Costco", "2026-08-09", "42.10", "Paper goods"):
        assert fragment in card


def test_review_only_is_idempotent_per_source_message(draft_env):
    assert draft_env["run"](source_id="wa_same") == 0
    rc2 = draft_env["run"](source_id="wa_same")
    assert rc2 == 9, "same source message must not create a second expense"
    assert len(_leads(draft_env)) == 1


def test_idempotency_wording_is_not_approval_shaped(draft_env, capsys):
    draft_env["run"](source_id="wa_same")
    capsys.readouterr()
    draft_env["run"](source_id="wa_same")
    text = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["approval_card_text"]
    assert "review" in text.lower()
    assert "approval" not in text.lower()


def test_drafted_is_retention_eligible_and_approval_closed():
    """DRAFTED receipts must not become immortal, and must never be resolvable
    by an approval code lookup."""
    sys.path.insert(0, str(ROOT / "src" / "platform"))
    from schemas import (EXPENSE_RETENTION_CANDIDATES,
                         EXPENSE_APPROVAL_CLOSED_STATUSES,
                         EXPENSE_TRANSITIONS)
    assert "DRAFTED" in EXPENSE_RETENTION_CANDIDATES
    assert "DRAFTED" in EXPENSE_APPROVAL_CLOSED_STATUSES
    assert EXPENSE_TRANSITIONS["DRAFTED"] == frozenset()
    assert "DRAFTED" in EXPENSE_TRANSITIONS["EXTRACTING"]


def test_non_review_mode_still_requests_approval(draft_env):
    """The existing supervised path is untouched by this tier."""
    assert draft_env["run"](source_id="wa_normal", review_only=False) == 0
    leads = _leads(draft_env)
    assert leads[0]["status"] == "AWAITING_OWNER_APPROVAL"
    assert leads[0]["owner_approval_code"]
    assert "expense_owner_approval_requested" in _audit_types(draft_env)
