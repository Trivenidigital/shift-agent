"""PR-CF6 — cf-router Hermes plugin tests.

Linux-only — actions.audit_intercepted imports safe_io which uses fcntl.
Plugin pure-Python paths (regex, dispatch logic) are tested via the
register/hooks layer with subprocess + state-file lookups mocked.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="actions.audit_intercepted imports safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"


def _load_plugin_modules():
    """Load the cf-router plugin's hooks + actions as submodules of a
    synthetic parent package, so the relative import `from . import actions`
    in hooks.py resolves correctly. The plugin dir name `cf-router` contains
    a hyphen so it can't be imported by name — hence the synthetic package.
    """
    sys.path.insert(0, str(PLATFORM_DIR))

    pkg_name = "cf_router_pkg_under_test"
    if pkg_name in sys.modules:
        # Re-loaded across tests — drop and rebuild
        for mod_name in list(sys.modules):
            if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
                del sys.modules[mod_name]

    # Synthetic parent package — points at the plugin directory
    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    # Load actions submodule
    actions_full = f"{pkg_name}.actions"
    actions_loader = importlib.machinery.SourceFileLoader(
        actions_full, str(PLUGIN_DIR / "actions.py"),
    )
    actions_spec = importlib.util.spec_from_loader(actions_full, actions_loader)
    actions_mod = importlib.util.module_from_spec(actions_spec)
    sys.modules[actions_full] = actions_mod
    actions_loader.exec_module(actions_mod)

    # Load hooks submodule — `from . import actions` now resolves
    hooks_full = f"{pkg_name}.hooks"
    hooks_loader = importlib.machinery.SourceFileLoader(
        hooks_full, str(PLUGIN_DIR / "hooks.py"),
    )
    hooks_spec = importlib.util.spec_from_loader(hooks_full, hooks_loader)
    hooks_mod = importlib.util.module_from_spec(hooks_spec)
    sys.modules[hooks_full] = hooks_mod
    hooks_loader.exec_module(hooks_mod)

    return hooks_mod, actions_mod


@pytest.fixture
def state_env(tmp_path):
    """Per-test state directory + config + log paths."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    return {
        "tmp": tmp_path,
        "state_dir": state,
        "log_path": logs / "decisions.log",
        "config_path": tmp_path / "config.yaml",
        "leads_path": state / "catering-leads.json",
        "menu_pending_path": state / "catering-menu-pending.json",
        "roster_path": tmp_path / "roster.json",
        "throttle_path": state / "cf-router-throttle.json",
    }


@pytest.fixture
def mods(state_env):
    """Load plugin + override paths to test fixtures."""
    hooks_mod, actions_mod = _load_plugin_modules()
    actions_mod.CONFIG_PATH = state_env["config_path"]
    actions_mod.LEADS_PATH = state_env["leads_path"]
    actions_mod.MENU_PENDING_PATH = state_env["menu_pending_path"]
    actions_mod.ROSTER_PATH = state_env["roster_path"]
    actions_mod.LOG_PATH = state_env["log_path"]
    actions_mod.THROTTLE_PATH = state_env["throttle_path"]
    # Override PLATFORM_DIR so audit_intercepted picks up the in-repo
    # schemas.py (which has CfRouterIntercepted), not the deployed one.
    actions_mod.PLATFORM_DIR = PLATFORM_DIR
    return hooks_mod, actions_mod


def _seed_config(state_env, owner_jid="918522041562@s.whatsapp.net"):
    state_env["config_path"].write_text(
        f"owner:\n  self_chat_jid: {owner_jid}\n", encoding="utf-8",
    )


def _seed_lead(state_env, code="#ABCDE", status="AWAITING_OWNER_APPROVAL",
               quote_text="Hi customer, your quote details. (Ref: L0001)"):
    state_env["leads_path"].write_text(json.dumps({
        "leads": [{
            "lead_id": "L0001",
            "owner_approval_code": code,
            "status": status,
            "customer_phone": "+19045550199",
            "customer_name": "Test",
            "raw_inquiry": "x",
            "original_message_id": "x",
            "created_at": "2026-05-03T10:00:00-04:00",
            "updated_at": "2026-05-03T10:00:00-04:00",
            "extracted": {
                "headcount": 50, "event_date": "2026-06-15", "event_time": None,
                "menu_preferences": [], "off_menu_items": [],
                "dietary_restrictions": [], "delivery_or_pickup": "delivery",
                "budget_hint_usd": None, "notes": "",
            },
            "quote_text": quote_text, "quote_version": 0,
            "customer_replied": False,
        }],
        "next_lead_seq": 2,
    }), encoding="utf-8")


def _seed_menu_pending(state_env, code="#YDW6J"):
    state_env["menu_pending_path"].write_text(json.dumps({
        "confirmation_code": code,
        "update_id": "MU0001",
        "proposed_at": "2026-05-03T10:00:00-04:00",
        "source_image_id": "img_test",
        "extracted_items": [{"name": "Test", "price_usd": 5.0, "category": "main",
                              "dietary_tags": ["veg"], "available": True,
                              "notes": "", "serves": None}],
        "parser_notes": "",
    }), encoding="utf-8")


def _seed_roster(state_env, employee_phone="+19045550101"):
    state_env["roster_path"].write_text(json.dumps({
        "employees": [{
            "id": "e001", "name": "Ravi", "phone": employee_phone,
            "role": "cashier", "status": "active",
            "can_cover_roles": ["cashier"], "languages": ["en"],
            "phone_history": [], "restrictions": None, "lid": None,
        }],
    }), encoding="utf-8")


def _make_event(text, chat_id):
    return SimpleNamespace(text=text, chat_id=chat_id)


# ============================================================================
# F8 — owner approval interception
# ============================================================================

class TestF8OwnerApprove:
    def test_owner_approve_intercepted_invokes_apply_script(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE", status="AWAITING_OWNER_APPROVAL")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
            event = _make_event("#ABCDE approve", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None
        assert result["action"] == "skip"
        assert "F8" in result["reason"]
        mock_apply.assert_called_once_with("#ABCDE", "approve")

    def test_owner_reject_intercepted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
            event = _make_event("#ABCDE reject not interested", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None
        assert result["action"] == "skip"
        mock_apply.assert_called_once_with("#ABCDE", "reject")

    def test_owner_edit_NOT_intercepted_lets_LLM_handle(self, mods, state_env):
        """Edit needs LLM extraction — plugin should let LLM handle."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock_apply:
            event = _make_event("#ABCDE edit change to 100 guests", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None  # LLM handles
        mock_apply.assert_not_called()

    def test_non_owner_chat_NOT_intercepted(self, mods, state_env):
        """Code in text but sender is NOT owner → let LLM handle."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env, owner_jid="918522041562@s.whatsapp.net")
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock_apply:
            event = _make_event("#ABCDE approve", "9999999999@s.whatsapp.net")  # not owner
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None
        mock_apply.assert_not_called()

    def test_owner_text_without_code_NOT_intercepted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)

        event = _make_event("Hi can you help me", "918522041562@s.whatsapp.net")
        result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None

    def test_owner_code_no_lead_match_NOT_intercepted(self, mods, state_env):
        """Code is well-formed but doesn't match any open lead → let LLM handle."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ZZZZZ")  # different code from what owner sent

        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock_apply:
            event = _make_event("#ABCDE approve", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None
        mock_apply.assert_not_called()

    def test_terminal_lead_NOT_intercepted(self, mods, state_env):
        """Lead in CLOSED status → not actionable, let LLM handle (will tell owner)."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE", status="CLOSED")

        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock_apply:
            event = _make_event("#ABCDE approve", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None
        mock_apply.assert_not_called()

    def test_owner_approve_writes_audit_row(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0):
            event = _make_event("#ABCDE approve", "918522041562@s.whatsapp.net")
            hooks_mod.pre_gateway_dispatch(event)

        # Audit row written
        audit = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        f8_rows = [r for r in audit if r["type"] == "cf_router_intercepted" and r["reason"] == "f8_owner_approve"]
        assert len(f8_rows) == 1
        assert f8_rows[0]["code"] == "#ABCDE"
        assert f8_rows[0]["chat_id"] == "918522041562@s.whatsapp.net"
        assert f8_rows[0]["subprocess_rc"] == 0


class TestF8MenuYesNo:
    def test_owner_menu_yes_intercepted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_menu_pending(state_env, code="#YDW6J")

        with patch.object(actions_mod, "invoke_apply_menu_update", return_value=0) as mock_apply:
            event = _make_event("#YDW6J yes", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None
        assert result["action"] == "skip"
        mock_apply.assert_called_once_with("#YDW6J", "yes")

    def test_owner_menu_no_intercepted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_menu_pending(state_env, code="#YDW6J")

        with patch.object(actions_mod, "invoke_apply_menu_update", return_value=0) as mock_apply:
            event = _make_event("#YDW6J no looks wrong", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None
        assert result["action"] == "skip"
        mock_apply.assert_called_once_with("#YDW6J", "no")


# ============================================================================
# F9 — sick-call alert
# ============================================================================

class TestF9SickCallAlert:
    def test_employee_sick_call_fires_pushover(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_roster(state_env, employee_phone="+19045550101")

        with patch.object(actions_mod, "fire_pushover_alert") as mock_pushover:
            event = _make_event("Boss I have fever, can't come tomorrow",
                                 "19045550101@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        # Plugin returns None — LLM still handles
        assert result is None
        mock_pushover.assert_called_once()
        _args, kwargs = mock_pushover.call_args
        title_text = (kwargs.get("title") or (_args[0] if _args else "")).lower()
        assert "sick-call" in title_text

    def test_non_employee_sick_text_NOT_alerted(self, mods, state_env):
        """A random sender saying 'sick' shouldn't trigger F9 alert."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_roster(state_env)

        with patch.object(actions_mod, "fire_pushover_alert") as mock_pushover:
            event = _make_event("I'm sick of waiting for catering",
                                 "5555555555@s.whatsapp.net")
            hooks_mod.pre_gateway_dispatch(event)

        mock_pushover.assert_not_called()

    def test_throttle_suppresses_duplicate_alerts(self, mods, state_env):
        """Same chat_id, multiple sick-call messages within 5min → 1 alert only."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_roster(state_env, employee_phone="+19045550101")

        with patch.object(actions_mod, "fire_pushover_alert") as mock_pushover:
            event = _make_event("can't come tomorrow, fever",
                                 "19045550101@s.whatsapp.net")
            hooks_mod.pre_gateway_dispatch(event)
            hooks_mod.pre_gateway_dispatch(event)
            hooks_mod.pre_gateway_dispatch(event)

        assert mock_pushover.call_count == 1

    def test_employee_normal_text_NOT_alerted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_roster(state_env, employee_phone="+19045550101")

        with patch.object(actions_mod, "fire_pushover_alert") as mock_pushover:
            event = _make_event("Hi boss, all good for tomorrow",
                                 "19045550101@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None
        mock_pushover.assert_not_called()


# ============================================================================
# Robustness
# ============================================================================

class TestRobustness:
    def test_missing_config_does_not_crash(self, mods, state_env):
        """No config.yaml → plugin returns None, doesn't raise."""
        hooks_mod, actions_mod = mods
        # Don't seed config
        event = _make_event("#ABCDE approve", "918522041562@s.whatsapp.net")
        result = hooks_mod.pre_gateway_dispatch(event)
        assert result is None  # graceful degradation

    def test_missing_text_returns_none(self, mods, state_env):
        hooks_mod, _ = mods
        event = SimpleNamespace(chat_id="x@y")  # no text
        result = hooks_mod.pre_gateway_dispatch(event)
        assert result is None

    def test_missing_chat_id_returns_none(self, mods, state_env):
        hooks_mod, _ = mods
        event = SimpleNamespace(text="hi")  # no chat_id
        result = hooks_mod.pre_gateway_dispatch(event)
        assert result is None

    def test_event_via_source_attribute(self, mods, state_env):
        """Some Hermes adapters expose chat_id via .source.chat_id, not directly."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
            event = SimpleNamespace(
                text="#ABCDE approve",
                source=SimpleNamespace(chat_id="918522041562@s.whatsapp.net"),
            )
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None
        mock_apply.assert_called_once()
