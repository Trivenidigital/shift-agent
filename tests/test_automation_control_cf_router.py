"""PR-5 automation-control — cf-router INBOUND enforcement point (§5.4/§5.5).

Exercises the deterministic kernel wired into pre_gateway_dispatch: customer
STOP → opt_out + exactly one ack + subsequent inbound suppressed; pause; no
silent re-enable on re-entry; explicit resume; owner takeover/release via the
#XXXXX owner surface; idempotency; and the INDEPENDENT STOP-only / takeover-only
activation. A source scan proves the kernel is wired BETWEEN the F8 owner block
and F9 in _pre_gateway_dispatch_impl. Windows-runnable via the fcntl stub.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import automation_control as ac  # noqa: E402

CUSTOMER = "15550100001@lid"
OWNER = "19045550999@s.whatsapp.net"
LEAD_PHONE = "+15550100001"
CODE = "#A3F9K"


def _load_plugin():
    pkg = "cf_router_ac_pkg"
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

    actions_mod = _load("actions")
    hooks_mod = _load("hooks")
    return hooks_mod, actions_mod


class _Spy:
    def __init__(self):
        self.sends = []      # (chat_id, text)
        self.emits = []      # (entry_type, fields)


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    spy = _Spy()
    # Capture kernel sends (ack / owner confirmation) instead of hitting bridge.
    monkeypatch.setattr(hooks_mod, "_automation_control_send",
                        lambda cid, txt, nid: spy.sends.append((cid, txt)))
    # Capture audit emits (both variants share ac._emit).
    monkeypatch.setattr(ac, "_emit", lambda t, f: spy.emits.append((t, f)))
    # Route kernel state to a tmp file; clear the in-process last-known cache.
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH",
                       str(tmp_path / "catering-automation-control.json"))
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
    ac._LAST_KNOWN_MODE.clear()
    # Leads store (for owner takeover code resolution).
    leads = tmp_path / "catering-leads.json"
    leads.write_text(json.dumps({"leads": [
        {"lead_id": "L0001", "status": "SENT_TO_CUSTOMER",
         "owner_approval_code": CODE, "customer_phone": LEAD_PHONE},
    ]}), encoding="utf-8")
    monkeypatch.setattr(actions_mod, "LEADS_PATH", leads)
    # lid-cache so the customer's LID converges to the lead's phone (the learned-
    # mapping pilot case): a takeover set on the lead's E.164 customer_phone must
    # suppress the customer's LID-keyed inbound (LID<->phone convergence, #615).
    lidcache = tmp_path / "lid-cache.json"
    lidcache.write_text(json.dumps({"schema_version": 1, "pairs": [
        {"phone": LEAD_PHONE, "lid": CUSTOMER},
    ]}), encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(lidcache))
    # Deterministic owner/customer identity.
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: cid == OWNER)
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: None)
    return SimpleNamespace(hooks=hooks_mod, actions=actions_mod, spy=spy)


def _call(plugin, text, chat_id):
    return plugin.hooks._try_automation_control(text, chat_id, "wamid.M", "wamid.M")


def _changed(spy):
    return [f for t, f in spy.emits if t == "catering_automation_control_changed"]


def _suppressed(spy):
    return [f for t, f in spy.emits if t == "catering_automated_send_suppressed"]


# ── STOP / opt-out (stop sub-flag on) ────────────────────────────────────────

class TestCustomerStop:
    @pytest.fixture(autouse=True)
    def _stop_on(self, monkeypatch):
        monkeypatch.setenv("CATERING_STOP_ENABLED", "1")
        monkeypatch.delenv("CATERING_TAKEOVER_ENABLED", raising=False)

    def test_stop_opts_out_with_exactly_one_ack(self, plugin):
        res = _call(plugin, "STOP", CUSTOMER)
        assert res["action"] == "skip" and "customer_stop" in res["reason"]
        assert ac.get_mode(CUSTOMER) == "opted_out"
        assert len(plugin.spy.sends) == 1  # exactly one deterministic ack
        assert "further automated messages" in plugin.spy.sends[0][1]
        assert _changed(plugin.spy)[-1]["to_mode"] == "opted_out"

    def test_subsequent_inbound_suppressed_no_second_ack(self, plugin):
        _call(plugin, "STOP", CUSTOMER)
        plugin.spy.sends.clear()
        res = _call(plugin, "are you there?", CUSTOMER)
        assert res == {"action": "skip", "reason": "automation_suppressed:opted_out"}
        assert plugin.spy.sends == []            # no second ack
        assert _suppressed(plugin.spy)[-1]["suppressed_send_kind"] == "inbound_dispatch"

    def test_repeat_stop_does_not_re_ack(self, plugin):
        _call(plugin, "STOP", CUSTOMER)
        plugin.spy.sends.clear()
        _call(plugin, "unsubscribe", CUSTOMER)
        assert plugin.spy.sends == []            # ack_sent guard holds

    def test_reentry_does_not_silently_re_enable(self, plugin):
        _call(plugin, "STOP", CUSTOMER)
        _call(plugin, "hi again, new question", CUSTOMER)
        assert ac.get_mode(CUSTOMER) == "opted_out"   # still suppressed

    def test_explicit_resume_re_enables(self, plugin):
        _call(plugin, "STOP", CUSTOMER)
        plugin.spy.sends.clear()
        res = _call(plugin, "RESUME", CUSTOMER)
        assert res["action"] == "skip" and "resume" in res["reason"]
        assert ac.get_mode(CUSTOMER) == "active"
        assert _changed(plugin.spy)[-1]["to_mode"] == "active"
        assert len(plugin.spy.sends) == 1        # resume confirmation

    def test_resume_token_on_active_convo_is_noop(self, plugin):
        """BLOCKER-1a: an ACTIVE customer typing a resume/catering-close word is
        NOT intercepted (routes normally), sends no ack, and stays active."""
        for word in ("RESUME", "confirm", "continue"):
            assert _call(plugin, word, CUSTOMER) is None
            assert ac.get_mode(CUSTOMER) == "active"
        assert plugin.spy.sends == []

    def test_pause_then_expiry(self, plugin, tmp_path):
        res = _call(plugin, "pause", CUSTOMER)
        assert ac.get_mode(CUSTOMER) == "paused"
        assert res["action"] == "skip"
        # Expire → a fresh inbound is no longer suppressed.
        p = Path(__import__("os").environ["CATERING_AUTOMATION_CONTROL_STATE_PATH"])
        doc = json.loads(p.read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "2000-01-01T00:00:00+00:00"
        p.write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()
        assert _call(plugin, "hello", CUSTOMER) is None


# ── takeover (takeover sub-flag on) ──────────────────────────────────────────

class TestOwnerTakeover:
    @pytest.fixture(autouse=True)
    def _takeover_on(self, monkeypatch):
        monkeypatch.setenv("CATERING_TAKEOVER_ENABLED", "1")
        monkeypatch.setenv("CATERING_STOP_ENABLED", "1")

    def test_owner_takeover_sets_customer_takeover(self, plugin):
        res = _call(plugin, f"{CODE} takeover", OWNER)
        assert res["action"] == "skip" and "owner_takeover" in res["reason"]
        # The CUSTOMER conversation (resolved from the code) is now in takeover.
        assert ac.get_mode(LEAD_PHONE) == "takeover"
        assert len(plugin.spy.sends) == 1        # owner confirmation
        assert plugin.spy.sends[0][0] == OWNER
        chg = _changed(plugin.spy)[-1]
        assert chg["to_mode"] == "takeover" and chg["actor"] == "owner"

    def test_takeover_suppresses_customer_inbound_and_queued_followup(self, plugin):
        _call(plugin, f"{CODE} takeover", OWNER)
        # Customer's later message → suppressed at the inbound point.
        res = _call(plugin, "any update?", CUSTOMER)
        assert res == {"action": "skip", "reason": "automation_suppressed:takeover"}

    def test_release_returns_to_active(self, plugin):
        _call(plugin, f"{CODE} takeover", OWNER)
        plugin.spy.emits.clear()
        res = _call(plugin, f"release {CODE}", OWNER)
        assert res["action"] == "skip" and "owner_release" in res["reason"]
        assert ac.get_mode(LEAD_PHONE) == "active"
        chg = _changed(plugin.spy)[-1]
        assert chg["from_mode"] == "takeover" and chg["to_mode"] == "active"

    def test_idempotent_engage_release(self, plugin):
        _call(plugin, f"{CODE} takeover", OWNER)
        _call(plugin, f"{CODE} takeover", OWNER)   # idempotent re-engage
        assert ac.get_mode(LEAD_PHONE) == "takeover"
        _call(plugin, f"release {CODE}", OWNER)
        _call(plugin, f"release {CODE}", OWNER)     # idempotent re-release
        assert ac.get_mode(LEAD_PHONE) == "active"

    def test_owner_manual_message_not_suppressed(self, plugin):
        """The owner's own self-chat is never a suppressed customer key."""
        _call(plugin, f"{CODE} takeover", OWNER)
        assert _call(plugin, "just checking the dashboard", OWNER) is None

    def test_state_survives_restart(self, plugin):
        _call(plugin, f"{CODE} takeover", OWNER)
        ac._LAST_KNOWN_MODE.clear()               # simulate process restart
        assert ac.get_mode(LEAD_PHONE) == "takeover"

    def test_unknown_code_falls_through(self, plugin):
        assert _call(plugin, "#ZZZZZ takeover", OWNER) is None


# ── independence: STOP-only vs takeover-only ─────────────────────────────────

class TestIndependentActivation:
    def test_stop_only_ignores_owner_takeover(self, plugin, monkeypatch):
        monkeypatch.setenv("CATERING_STOP_ENABLED", "1")
        monkeypatch.delenv("CATERING_TAKEOVER_ENABLED", raising=False)
        # Owner takeover command is NOT processed (takeover disabled) → owner chat
        # is unsuppressed → None.
        assert _call(plugin, f"{CODE} takeover", OWNER) is None
        assert ac.get_mode(LEAD_PHONE) == "active"
        # Customer STOP still works.
        assert _call(plugin, "STOP", CUSTOMER)["action"] == "skip"
        assert ac.get_mode(CUSTOMER) == "opted_out"

    def test_takeover_only_ignores_customer_stop(self, plugin, monkeypatch):
        monkeypatch.setenv("CATERING_TAKEOVER_ENABLED", "1")
        monkeypatch.delenv("CATERING_STOP_ENABLED", raising=False)
        # Customer STOP is NOT processed (stop disabled) → active → None.
        assert _call(plugin, "STOP", CUSTOMER) is None
        assert ac.get_mode(CUSTOMER) == "active"
        # Owner takeover still works.
        assert _call(plugin, f"{CODE} takeover", OWNER)["action"] == "skip"
        assert ac.get_mode(LEAD_PHONE) == "takeover"


class TestMasterFlagOff:
    def test_master_off_is_byte_identical_none(self, plugin, monkeypatch):
        monkeypatch.delenv("CATERING_AUTOMATION_CONTROL_ENABLED", raising=False)
        monkeypatch.setenv("CATERING_STOP_ENABLED", "1")
        monkeypatch.setenv("CATERING_TAKEOVER_ENABLED", "1")
        assert _call(plugin, "STOP", CUSTOMER) is None
        assert _call(plugin, f"{CODE} takeover", OWNER) is None
        assert plugin.spy.sends == []


# ── wiring proof ─────────────────────────────────────────────────────────────

class TestDispatchWiring:
    def test_kernel_called_between_f8_and_f9(self):
        hooks_mod, _ = _load_plugin()
        src = inspect.getsource(hooks_mod._pre_gateway_dispatch_impl)
        i_f8 = src.index("_try_f8_intercept")
        i_ac = src.index("_try_automation_control")
        i_f9 = src.index("_is_sick_call")
        assert i_f8 < i_ac < i_f9, "automation-control must run after F8, before F9"
