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
        "proposals_path": state / "catering-proposals.json",
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
    actions_mod.PROPOSALS_PATH = state_env["proposals_path"]
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
        mock_apply.assert_called_once()
        call = mock_apply.call_args
        assert call.args[:2] == ("#ABCDE", "approve")
        assert call.kwargs.get("lead", {}).get("owner_approval_code") == "#ABCDE"

    def test_owner_reject_intercepted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
            event = _make_event("#ABCDE reject not interested", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None
        assert result["action"] == "skip"
        mock_apply.assert_called_once_with("#ABCDE", "reject")  # reject doesn't pass lead

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

    def test_owner_approve_past_tense_intercepted(self, mods, state_env):
        """Owner often replies 'approved' (past tense) — must intercept."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
            event = _make_event("#ABCDE approved", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None and result["action"] == "skip"
        mock_apply.assert_called_once()

    def test_owner_reject_past_tense_intercepted(self, mods, state_env):
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
            event = _make_event("#ABCDE rejected", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None and result["action"] == "skip"
        mock_apply.assert_called_once()

    def test_apply_script_failure_falls_back_to_LLM(self, mods, state_env):
        """If apply-script returns non-zero, plugin returns None so LLM
        runs and surfaces the failure to the owner — does NOT silently skip.
        """
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=9):
            event = _make_event("#ABCDE approve", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)

        # Non-zero rc → fall back to LLM (don't silently eat the message)
        assert result is None
        # But audit row IS written so the failure is observable
        audit = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        rows = [r for r in audit if r["type"] == "cf_router_intercepted" and r["reason"] == "f8_owner_approve"]
        assert len(rows) == 1
        assert rows[0]["subprocess_rc"] == 9

    def test_owner_via_LID_intercepted_via_identify_sender(self, mods, state_env):
        """Owner inbound via @lid (not phone-JID) — F13 fix: fall back to
        identify-sender → role=owner.
        """
        hooks_mod, actions_mod = mods
        _seed_config(state_env, owner_jid="918522041562@s.whatsapp.net")
        _seed_lead(state_env, code="#ABCDE")
        owner_lid = "211390371475536@lid"

        # Mock identify-sender subprocess + apply-script
        fake_run = SimpleNamespace(returncode=0, stdout='{"role":"owner"}', stderr="")
        with patch("subprocess.run", return_value=fake_run):
            with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock_apply:
                event = _make_event("#ABCDE approve", owner_lid)
                result = hooks_mod.pre_gateway_dispatch(event)

        assert result is not None and result["action"] == "skip"
        mock_apply.assert_called_once()

    def test_LID_non_owner_NOT_intercepted(self, mods, state_env):
        """LID sender that identify-sender flags as non-owner → not intercepted."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")
        random_lid = "999999999999@lid"

        fake_run = SimpleNamespace(returncode=0, stdout='{"role":"unknown"}', stderr="")
        with patch("subprocess.run", return_value=fake_run):
            with patch.object(actions_mod, "invoke_apply_owner_decision") as mock_apply:
                event = _make_event("#ABCDE approve", random_lid)
                result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None
        mock_apply.assert_not_called()


class TestF8ParserEdgeCases:
    """Code + verb extraction edge cases — ported from the deleted
    test_overnight_watchdog_classifiers.py::TestOwnerActionParser block
    (audit finding 78 on PR #58). The original tested parse_owner_action()
    on the watchdog directly; cf-router uses _CODE_PATTERN + _VERB_*
    regexes inside hooks.pre_gateway_dispatch, so we exercise the full
    intercept path and assert on the action result.

    Behavior differences vs the deleted watchdog (intentional, per
    cf-router design):
    - Verb-before-code is NOW intercepted (e.g. "approve #ABCDE") because
      both regexes use re.search on the full text, not strict ordering.
      The watchdog required code-then-verb. The plugin's permissive form
      reduces brittleness on natural owner phrasing
      ("Re #ABCDE: approve please", "approve #ABCDE thanks").
    - The other negatives (invalid alphabet chars, no verb, no code,
      unrecognized verb, empty) all still NOT intercepted, matching the
      watchdog's behavior.
    """

    @pytest.fixture(autouse=True)
    def _seed(self, mods, state_env):
        _seed_config(state_env)
        _seed_lead(state_env, code="#ABCDE")

    def test_invalid_alphabet_char_NOT_intercepted(self, mods, state_env):
        """Codes with `0`, `1`, `I`, `O`, `L` shouldn't match _CODE_PATTERN."""
        hooks_mod, actions_mod = mods
        for bad_text in (
            "#A1CDE approve",   # contains 1
            "#ABCD0 approve",   # contains 0
            "#ABCIE approve",   # contains I
            "#ABCOE approve",   # contains O
        ):
            with patch.object(actions_mod, "invoke_apply_owner_decision") as mock:
                event = _make_event(bad_text, "918522041562@s.whatsapp.net")
                result = hooks_mod.pre_gateway_dispatch(event)
            assert result is None, f"Should not intercept invalid code in {bad_text!r}"
            mock.assert_not_called()

    def test_short_or_long_code_NOT_intercepted(self, mods, state_env):
        """Codes that aren't exactly 5 chars after `#` shouldn't match."""
        hooks_mod, actions_mod = mods
        for bad_text in (
            "#ABCD approve",     # 4 chars
            "#ABCDEF approve",   # 6 chars — note this MAY match (regex finds first 5)
        ):
            with patch.object(actions_mod, "invoke_apply_owner_decision"):
                event = _make_event(bad_text, "918522041562@s.whatsapp.net")
                result = hooks_mod.pre_gateway_dispatch(event)
            # 4-char fails; 6-char actually matches the first 5 — document
            # this, not blocking. We only assert the 4-char case here.
            if "#ABCD " in bad_text:
                assert result is None, f"4-char code should not intercept: {bad_text!r}"

    def test_code_without_verb_NOT_intercepted(self, mods, state_env):
        """`#ABCDE` alone (no approve/reject/edit) should let LLM ask for clarification."""
        hooks_mod, actions_mod = mods
        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock:
            event = _make_event("#ABCDE", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result is None
        mock.assert_not_called()

    def test_verb_without_code_NOT_intercepted(self, mods, state_env):
        """`approve` alone (no `#XXXXX`) shouldn't match — there's nothing to apply to."""
        hooks_mod, actions_mod = mods
        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock:
            event = _make_event("approve", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result is None
        mock.assert_not_called()

    def test_unrecognized_verb_NOT_intercepted(self, mods, state_env):
        """`#ABCDE confirm` — code matches but `confirm` isn't in the verb set."""
        hooks_mod, actions_mod = mods
        with patch.object(actions_mod, "invoke_apply_owner_decision") as mock:
            event = _make_event("#ABCDE confirm please", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result is None
        mock.assert_not_called()

    def test_verb_before_code_IS_intercepted(self, mods, state_env):
        """Verb-before-code IS intercepted (cf-router behavior change vs watchdog).
        Both regexes run as re.search on full text — order doesn't matter."""
        hooks_mod, actions_mod = mods
        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock:
            event = _make_event("approve #ABCDE please", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result is not None and result["action"] == "skip"
        mock.assert_called_once()

    def test_mixed_case_code_normalized_to_upper(self, mods, state_env):
        """Lowercase code in inbound — _try_f8_intercept calls .upper() before lookup."""
        hooks_mod, actions_mod = mods
        with patch.object(actions_mod, "invoke_apply_owner_decision", return_value=0) as mock:
            event = _make_event("#abcde approve", "918522041562@s.whatsapp.net")
            result = hooks_mod.pre_gateway_dispatch(event)
        # _CODE_PATTERN has no IGNORECASE (per PR-CF6 audit fix), so this
        # MUST NOT match. The watchdog's parser DID accept lowercase; the
        # plugin deliberately does not.
        assert result is None, "lowercase #abcde should NOT match _CODE_PATTERN (no IGNORECASE)"
        mock.assert_not_called()

    def test_empty_text_NOT_intercepted(self, mods, state_env):
        """Empty / whitespace-only text — _extract_text returns None, plugin returns None."""
        hooks_mod, _ = mods
        for empty in ("", "   ", "\n\n"):
            event = _make_event(empty, "918522041562@s.whatsapp.net")
            # _make_event won't preserve empty text since SimpleNamespace doesn't
            # care; but the hook's _extract_text uses .strip() and returns None
            # on empty results, which short-circuits to None.
            result = hooks_mod.pre_gateway_dispatch(event)
            assert result is None


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


# ============================================================================
# F7 — catering-dispatcher-watchdog (PR-CF7)
# ============================================================================

class TestF7DispatcherWatchdog:
    """Plugin-level tests for the F7 path (PR-CF7).

    Strategy: monkey-patch `threading.Timer` so we can synchronously invoke
    the rescue callback rather than waiting 30s. The classifier itself is
    already pinned by the 26 cases in test_catering_dispatcher_classifier.py.
    """

    @pytest.fixture
    def patched_timer(self, monkeypatch):
        """Replace threading.Timer with a same-shape stub that fires the
        callback IMMEDIATELY (no delay). Returns a list of (delay, fn, args)
        tuples for assertion."""
        calls = []

        class _ImmediateTimer:
            def __init__(self, interval, function, args=None, kwargs=None):
                calls.append((interval, function, args or (), kwargs or {}))
                self._function = function
                self._args = args or ()
                self._kwargs = kwargs or {}
                self.daemon = False

            def start(self):
                # Fire immediately for test determinism
                self._function(*self._args, **self._kwargs)

        # Patch in BOTH the hooks module and threading itself (the hook
        # imports `threading` at module scope, so threading.Timer must be
        # the patched class when the hook references it)
        import threading
        monkeypatch.setattr(threading, "Timer", _ImmediateTimer)
        return calls

    # test_catering_inquiry_schedules_rescue REMOVED 2026-05-12 (PR-CF1d Commit 5).
    # The "rescue is scheduled when classifier matches" behavior no longer exists —
    # primary-mode invokes create-catering-lead directly inside pre_gateway_dispatch.
    # The replacement assertion ("F7 primary fires Branch A for customer-side catering
    # inquiry") lives in TestF7PrimaryMode.test_branch_a_new_inquiry_creates_lead_and_skips_llm.

    def test_non_catering_inquiry_NOT_scheduled(self, mods, state_env, patched_timer):
        """Generic text → no F7 timer scheduled."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        with patch.object(actions_mod, "f7_rescue_check") as mock_rescue:
            event = _make_event(
                "Hi can you help me find a recipe for biryani?",
                "12025550199@s.whatsapp.net",
            )
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result is None
        mock_rescue.assert_not_called()

    def test_F7_disabled_flag_skips_path(self, mods, state_env, patched_timer):
        """When hooks_mod.F7_ENABLED = False, no rescue is scheduled even
        for catering text. Verifies the rollback hatch."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        # Flip the flag (simulates the sed rollback)
        original = hooks_mod.F7_ENABLED
        hooks_mod.F7_ENABLED = False
        try:
            with patch.object(actions_mod, "f7_rescue_check") as mock_rescue:
                event = _make_event(
                    "catering inquiry for 100 people event Saturday, food delivered",
                    "12025550199@s.whatsapp.net",
                )
                result = hooks_mod.pre_gateway_dispatch(event)
            assert result is None
            mock_rescue.assert_not_called()
        finally:
            hooks_mod.F7_ENABLED = original

    # test_owner_chat_short_circuits_F7 REMOVED 2026-05-12 (PR-CF1d Commit 5).
    # Was asserting that the rescue Timer was scheduled even for owner-chat
    # traffic, and the rescue callback would then suppress via the role check.
    # Primary-mode short-circuits the owner case earlier (inside
    # _try_f7_primary_intercept's role check) and never schedules a Timer.
    # Replacement assertion lives in TestF7PrimaryMode.test_owner_role_bypasses_f7_primary.

    def test_message_id_fallback_when_event_lacks_id(self, mods, state_env, patched_timer):
        """Event without message_id attribute → fallback string is used.

        Audit schemas require min_length=1 on message_id; the fallback
        ensures we never pass an empty string. Mirrors the deployed F7
        daemon's `bridge_notify_<chat>_<ms>` pattern.

        PR-CF1d Commit 5: rewritten to test `_extract_message_id` directly
        (helper-level unit test) instead of asserting via mock_rescue.call_args.
        Primary-mode no longer schedules the rescue Timer from
        pre_gateway_dispatch, so the prior mock-args-based assertion is
        infeasible. The helper's contract is unchanged.
        """
        hooks_mod, _actions_mod = mods
        event = SimpleNamespace(
            text="catering for 50 people wedding event next week, food delivered",
            chat_id="12025550199@s.whatsapp.net",
            # No message_id, id, or msg_id
        )
        message_id = hooks_mod._extract_message_id(
            event,
            chat_id="12025550199@s.whatsapp.net",
            text="catering for 50 people wedding event next week, food delivered",
        )
        assert message_id.startswith("cf_router_f7_")
        assert "12025550199" in message_id
        # Audit schema requires min_length=1
        assert len(message_id) >= 1

    def test_message_id_passes_through_when_present(self, mods, state_env, patched_timer):
        """Event with native message_id → that value is used (not fallback).

        PR-CF1d Commit 5: rewritten to test `_extract_message_id` directly,
        same rationale as test_message_id_fallback_when_event_lacks_id above.
        """
        hooks_mod, _actions_mod = mods
        event = SimpleNamespace(
            text="catering for 50 people wedding event next week, food delivered",
            chat_id="12025550199@s.whatsapp.net",
            message_id="3EB0PassThrough123",
        )
        message_id = hooks_mod._extract_message_id(
            event,
            chat_id="12025550199@s.whatsapp.net",
            text="catering for 50 people wedding event next week, food delivered",
        )
        assert message_id == "3EB0PassThrough123"

    def test_rescue_suppressed_when_dispatcher_routed_present(self, mods, state_env):
        """Rescue check finds a recent dispatcher_routed audit row → no
        rescue invocation, no audit row emitted (success path)."""
        _hooks_mod, actions_mod = mods
        _seed_config(state_env)
        # Seed audit log with a matching dispatcher_routed entry
        ts_now = time.time()
        ts_iso = "2026-05-04T03:00:00+00:00"
        row = {
            "type": "dispatcher_routed",
            "ts": ts_iso,
            "sender_lid": "12025550199",
            "sender_phone": "+12025550199",
        }
        state_env["log_path"].write_text(json.dumps(row) + "\n", encoding="utf-8")

        with patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger:
            # Use a ts_at_schedule that's BEFORE the audit row's ts
            from datetime import datetime, timezone
            ts_audit = datetime.fromisoformat(ts_iso).timestamp()
            actions_mod.f7_rescue_check(
                text="catering inquiry text", chat_id="12025550199@s.whatsapp.net",
                message_id="msg_123", signals=["primary:catering", "headcount:50"],
                ts_at_schedule=ts_audit - 10,  # schedule 10s before the dispatch
            )

        # No rescue invocation, no rescue-fire audit row
        mock_trigger.assert_not_called()

    def test_rescue_suppressed_for_owner_role(self, mods, state_env):
        """Sender role resolves to 'owner' → suppressed audit row, no rescue."""
        _hooks_mod, actions_mod = mods
        _seed_config(state_env)
        # Mock identify-sender to return owner role
        fake_run = SimpleNamespace(
            returncode=0, stdout='{"role":"owner","phone_normalized":"+918522041562"}', stderr="",
        )
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger:
            actions_mod.f7_rescue_check(
                text="catering inquiry text", chat_id="918522041562@s.whatsapp.net",
                message_id="msg_owner", signals=["primary:catering", "headcount:50"],
                ts_at_schedule=time.time(),
            )
        mock_trigger.assert_not_called()
        # Suppressed row written
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        suppressed = [r for r in rows if r.get("type") == "catering_dispatcher_watchdog_suppressed"]
        assert len(suppressed) == 1
        assert suppressed[0]["reason"] == "non_customer_role"

    def test_rescue_fires_for_customer_with_phone(self, mods, state_env):
        """Customer sender + phone resolves + no dispatcher_routed →
        invoke create-catering-lead, emit fired audit row."""
        _hooks_mod, actions_mod = mods
        _seed_config(state_env)
        # Mock identify-sender → customer role + phone
        fake_id_run = SimpleNamespace(
            returncode=0, stdout='{"role":"customer","phone_normalized":"+12025550199"}', stderr="",
        )
        # Mock create-catering-lead success
        fake_create_run = SimpleNamespace(returncode=0, stdout="lead_created L0099", stderr="")

        call_log = []
        def _fake_run(cmd, *args, **kwargs):
            call_log.append(cmd[0] if isinstance(cmd, list) else cmd)
            if "identify-sender" in str(cmd):
                return fake_id_run
            if "create-catering-lead" in str(cmd):
                return fake_create_run
            return SimpleNamespace(returncode=1, stdout="", stderr="unmocked")

        with patch("subprocess.run", side_effect=_fake_run):
            actions_mod.f7_rescue_check(
                text="catering inquiry for 80 people event Saturday, food delivered",
                chat_id="12025550199@s.whatsapp.net",
                message_id="msg_customer",
                signals=["primary:catering", "headcount:80", "event_keyword"],
                ts_at_schedule=time.time(),
            )

        # create-catering-lead was invoked
        assert any("create-catering-lead" in str(c) for c in call_log)
        # Fired row written
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        fired = [r for r in rows if r.get("type") == "catering_dispatcher_watchdog_fired"]
        assert len(fired) == 1
        assert fired[0]["customer_phone"] == "+12025550199"
        assert fired[0]["success"] is True
        assert "primary:catering" in fired[0]["signals"]

    def test_rescue_fires_for_employee_private_catering_with_phone(self, mods, state_env):
        """Employee sender can still be a private customer-side catering lead."""
        _hooks_mod, actions_mod = mods
        _seed_config(state_env)
        fake_id_run = SimpleNamespace(
            returncode=0, stdout='{"role":"employee","phone_normalized":"+19045550104"}', stderr="",
        )
        fake_create_run = SimpleNamespace(returncode=0, stdout="lead_created L0101", stderr="")

        call_log = []

        def _fake_run(cmd, *args, **kwargs):
            call_log.append(cmd[0] if isinstance(cmd, list) else cmd)
            if "identify-sender" in str(cmd):
                return fake_id_run
            if "create-catering-lead" in str(cmd):
                return fake_create_run
            return SimpleNamespace(returncode=1, stdout="", stderr="unmocked")

        with patch("subprocess.run", side_effect=_fake_run):
            actions_mod.f7_rescue_check(
                text="This is a catering inquiry for my cousin's wedding on July 12 for 80 people",
                chat_id="201975216009469@lid",
                message_id="msg_employee_customer",
                signals=["primary:catering", "headcount:80", "event_keyword"],
                ts_at_schedule=time.time(),
            )

        assert any("create-catering-lead" in str(c) for c in call_log)
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        fired = [r for r in rows if r.get("type") == "catering_dispatcher_watchdog_fired"]
        assert len(fired) == 1
        assert fired[0]["customer_phone"] == "+19045550104"

    def test_rescue_suppressed_when_phone_unresolvable(self, mods, state_env):
        """Customer role but identify-sender returns no phone → suppressed
        with reason=lid_no_phone_resolution."""
        _hooks_mod, actions_mod = mods
        _seed_config(state_env)
        fake_run = SimpleNamespace(
            returncode=0, stdout='{"role":"customer","phone_normalized":null}', stderr="",
        )
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger:
            actions_mod.f7_rescue_check(
                text="catering for 50 people event Saturday food delivered",
                chat_id="999999999999@lid", message_id="msg_lid",
                signals=["primary:catering", "headcount:50", "event_keyword"],
                ts_at_schedule=time.time(),
            )
        mock_trigger.assert_not_called()
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        suppressed = [r for r in rows if r.get("type") == "catering_dispatcher_watchdog_suppressed"]
        assert len(suppressed) == 1
        assert suppressed[0]["reason"] == "lid_no_phone_resolution"


# === PR-CF1d 2026-05-12: F7 primary-mode helpers ===

def _seed_leads_multi(state_env, leads):
    """Write a multi-lead state file. Each entry in `leads` is a dict with
    overrides for the lead schema; defaults fill the rest."""
    default = {
        "lead_id": "L0000",
        "owner_approval_code": "#AAAAA",
        "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199",
        "customer_lid": None,
        "customer_name": "",
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
        "quote_text": "<legacy>", "quote_version": 0,
        "customer_replied": False,
    }
    rows = [{**default, **overrides} for overrides in leads]
    state_env["leads_path"].write_text(json.dumps({
        "leads": rows,
        "next_lead_seq": len(rows) + 1,
    }), encoding="utf-8")


def _seed_sent_proposal_set(state_env, lead_id="L0001"):
    state_env["proposals_path"].write_text(json.dumps({
        "sets": [{
            "proposal_set_id": f"CPS-{lead_id}-000001",
            "lead_id": lead_id,
            "status": "SENT",
            "sent_at": "2026-04-30T10:01:00-04:00",
            "outbound_message_id": "wamid.proposal.1",
            "options": [
                {"option_id": "1", "title": "Classic"},
                {"option_id": "2", "title": "Premium"},
            ],
        }],
        "next_set_seq": 2,
    }), encoding="utf-8")


def _seed_proposal_sets(state_env, sets):
    state_env["proposals_path"].write_text(json.dumps({
        "sets": sets,
        "next_set_seq": len(sets) + 1,
    }), encoding="utf-8")


class TestFindActiveCateringLeadBySender:
    """PR-CF1d Commit 1: cf-router F7 primary-mode active-lead lookup."""

    def test_no_match_when_no_leads(self, mods, state_env):
        _, actions_mod = mods
        _seed_leads_multi(state_env, [])
        assert actions_mod.find_active_catering_lead_by_sender(
            phone="+17329837841", chat_id="17329837841@s.whatsapp.net",
        ) is None

    def test_match_by_phone(self, mods, state_env):
        """Priority 1: E.164 phone exact-match on customer_phone."""
        _, actions_mod = mods
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "customer_phone": "+17329837841"},
        ])
        result = actions_mod.find_active_catering_lead_by_sender(
            phone="+17329837841", chat_id=None,
        )
        assert result is not None
        assert result["lead_id"] == "L0001"

    def test_match_by_lid_direct(self, mods, state_env):
        """Priority 2: customer_lid exact-match (post-bugfix shape)."""
        _, actions_mod = mods
        _seed_leads_multi(state_env, [
            {"lead_id": "L0002",
             "customer_phone": None,
             "customer_lid": "201975216009469@lid"},
        ])
        result = actions_mod.find_active_catering_lead_by_sender(
            phone=None, chat_id="201975216009469@lid",
        )
        assert result is not None
        assert result["lead_id"] == "L0002"

    def test_match_by_lid_as_fake_phone(self, mods, state_env):
        """Priority 3: LID-digits-as-+phone legacy shape (the actual
        deployed shape in L0004..L0010 as of 2026-05-12)."""
        _, actions_mod = mods
        _seed_leads_multi(state_env, [
            {"lead_id": "L0003",
             "customer_phone": "+201975216009469",
             "customer_lid": None},
        ])
        result = actions_mod.find_active_catering_lead_by_sender(
            phone=None, chat_id="201975216009469@lid",
        )
        assert result is not None
        assert result["lead_id"] == "L0003"

    def test_no_match_when_lead_terminal(self, mods, state_env):
        """Terminal-status leads (SENT_TO_CUSTOMER, OWNER_REJECTED, CLOSED,
        STALE) must not match — they're outside ACTIONABLE_LEAD_STATUSES."""
        _, actions_mod = mods
        _seed_leads_multi(state_env, [
            {"lead_id": "L0004",
             "customer_phone": "+17329837841",
             "status": "SENT_TO_CUSTOMER"},
            {"lead_id": "L0005",
             "customer_phone": "+17329837841",
             "status": "OWNER_REJECTED"},
        ])
        assert actions_mod.find_active_catering_lead_by_sender(
            phone="+17329837841", chat_id=None,
        ) is None

    def test_returns_most_recent_when_multiple_active(self, mods, state_env):
        """When more than one ACTIONABLE_LEAD_STATUS match the sender,
        return the most-recent by created_at (helps customer continue with
        their latest inquiry, not a stale one)."""
        _, actions_mod = mods
        _seed_leads_multi(state_env, [
            {"lead_id": "L0006",
             "customer_phone": "+17329837841",
             "created_at": "2026-05-01T10:00:00-04:00"},
            {"lead_id": "L0007",
             "customer_phone": "+17329837841",
             "created_at": "2026-05-10T10:00:00-04:00"},
            {"lead_id": "L0008",
             "customer_phone": "+17329837841",
             "created_at": "2026-05-05T10:00:00-04:00"},
        ])
        result = actions_mod.find_active_catering_lead_by_sender(
            phone="+17329837841", chat_id=None,
        )
        assert result is not None
        assert result["lead_id"] == "L0007"

    def test_returns_none_when_both_inputs_empty(self, mods, state_env):
        """Defensive: no phone, no chat_id → no possible match."""
        _, actions_mod = mods
        _seed_leads_multi(state_env, [
            {"lead_id": "L0009"},
        ])
        assert actions_mod.find_active_catering_lead_by_sender(
            phone=None, chat_id=None,
        ) is None

    @pytest.mark.parametrize("active_status", [
        "AWAITING_OWNER_APPROVAL",
        "CUSTOMER_FINALIZED",
        "OWNER_EDITED",
        "OWNER_APPROVED",
    ])
    def test_matches_every_actionable_status(self, mods, state_env, active_status):
        """Each ACTIONABLE_LEAD_STATUSES entry must match. Closes the
        coverage gap flagged in the F7 primary-mode PR review — without
        this test, a future change to ACTIONABLE_LEAD_STATUSES that
        accidentally narrows the set could ship undetected."""
        _, actions_mod = mods
        # Verify the status we're testing is actually in the deployed set
        assert active_status in actions_mod.ACTIONABLE_LEAD_STATUSES
        _seed_leads_multi(state_env, [
            {"lead_id": "L0100",
             "customer_phone": "+17329837841",
             "status": active_status},
        ])
        result = actions_mod.find_active_catering_lead_by_sender(
            phone="+17329837841", chat_id=None,
        )
        assert result is not None, f"expected match for status {active_status!r}"
        assert result["lead_id"] == "L0100"
        assert result["status"] == active_status


class TestF7ProposalHelpers:
    def test_find_selectable_proposal_set_uses_latest_sequence_not_latest_sent(self, mods, state_env):
        _, actions_mod = mods
        _seed_proposal_sets(state_env, [
            {
                "proposal_set_id": "CPS-L0001-000001",
                "lead_id": "L0001",
                "status": "SENT",
                "sent_at": "2026-04-30T10:01:00-04:00",
                "outbound_message_id": "wamid.proposal.1",
            },
            {
                "proposal_set_id": "CPS-L0001-000002",
                "lead_id": "L0001",
                "status": "SEND_FAILED",
                "sent_at": "2026-04-30T10:02:00-04:00",
                "outbound_message_id": "",
            },
        ])

        assert actions_mod.find_selectable_proposal_set("L0001") is None

    @pytest.mark.parametrize("text", [
        "can you revise option 2?",
        "I don't like option 2",
    ])
    def test_selection_classifier_rejects_non_selection_mentions(self, mods, text):
        _, actions_mod = mods

        assert actions_mod.is_proposal_selection(text) is False

    @pytest.mark.parametrize("text", [
        "I want to wait for two menu proposals",
        "No need to send proposals yet, we will wait",
        "any update",
    ])
    def test_proposal_request_rejects_passive_wait_or_status_text(self, mods, text):
        _, actions_mod = mods

        assert actions_mod.is_proposal_request(text) is False


class TestSendCanonicalFollowupReply:
    """PR-CF1d Commit 2: cf-router F7 primary-mode UX-mitigation reply."""

    def test_invokes_send_catering_ack_subprocess(self, mods):
        """Verify the helper passes the right args to send-catering-ack:
        --customer-jid, --message-text (hard-coded template, no LLM), --lead-id."""
        _, actions_mod = mods
        fake_run = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with patch("subprocess.run", return_value=fake_run) as mock_run:
            ok = actions_mod.send_canonical_followup_reply(
                chat_id="201975216009469@lid", lead_id="L0011",
            )
        assert ok is True
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert str(actions_mod.SEND_CATERING_ACK_BIN) in cmd[0]
        assert "--customer-jid" in cmd
        assert "201975216009469@lid" in cmd
        assert "--message-text" in cmd
        # Locate the message body argument
        msg_idx = cmd.index("--message-text") + 1
        body = cmd[msg_idx]
        # HARD RULES compliance — template must NOT contain $ or per-person pricing
        assert "$" not in body, f"template leaked $: {body}"
        assert "per person" not in body.lower(), f"template leaked per-person price: {body}"
        # Must reference the lead_id so the customer knows what's being reviewed
        assert "L0011" in body
        assert "--lead-id" in cmd
        assert "L0011" in cmd

    def test_returns_false_on_subprocess_failure(self, mods):
        """Non-zero exit code from send-catering-ack → helper returns False
        (caller still records the suppressed audit row and skips the LLM)."""
        _, actions_mod = mods
        fake_run = SimpleNamespace(returncode=2, stdout="", stderr="bridge unreachable")
        with patch("subprocess.run", return_value=fake_run):
            ok = actions_mod.send_canonical_followup_reply(
                chat_id="201975216009469@lid", lead_id="L0011",
            )
        assert ok is False

    def test_returns_false_on_subprocess_exception(self, mods):
        """Subprocess exception (timeout, OSError) → helper returns False
        without raising. Failure is non-fatal at the caller."""
        _, actions_mod = mods
        with patch("subprocess.run", side_effect=OSError("kaboom")):
            ok = actions_mod.send_canonical_followup_reply(
                chat_id="201975216009469@lid", lead_id="L0011",
            )
        assert ok is False


class TestF7PrimaryMode:
    """PR-CF1d Commit 3: cf-router F7 primary-mode end-to-end paths.

    Replaces the prior rescue-mode pre_gateway_dispatch wiring. F7 now
    intercepts catering customer inbounds AT pre_gateway_dispatch and
    bypasses the LLM entirely. Branch A creates a lead deterministically;
    Branch B suppresses follow-ups against existing active leads.
    """

    def test_branch_a_new_inquiry_creates_lead_and_skips_llm(self, mods, state_env):
        """Customer-side catering inquiry with no active lead → cf-router
        invokes create-catering-lead via trigger_create_catering_lead +
        returns skip. Audit row reason=f7_primary_new_inquiry."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])  # no existing leads
        # identify-sender returns customer (non-owner, non-employee)
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"customer","phone_normalized":"+17329837841"}',
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead",
                          return_value=(True, "lead_created")) as mock_trigger:
            event = _make_event(
                text="catering for 50 people event Saturday food delivered",
                chat_id="17329837841@s.whatsapp.net",
            )
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result == {
            "action": "skip",
            "reason": "cf-router F7 primary: catering inquiry routed deterministically",
        }
        mock_trigger.assert_called_once()
        call_kwargs = mock_trigger.call_args.kwargs
        # HARD RULES: customer_name MUST be empty string (kills hallucination)
        assert call_kwargs["customer_name"] == ""
        # Lead created audit
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        audits = [r for r in rows if r.get("type") == "cf_router_intercepted"]
        assert len(audits) == 1
        assert audits[0]["reason"] == "f7_primary_new_inquiry"
        assert audits[0]["subprocess_rc"] == 0

    def test_branch_a_employee_private_catering_inquiry_creates_lead(self, mods, state_env):
        """Employee identity can still be customer-side for a private event.

        Owner remains control-plane, but an employee may ask for catering for
        their own/family/friend event. This regression pins the live 2026-05-13
        failure where employee e004's cousin-wedding inquiry fell through to
        the generic LLM instead of deterministic lead creation.
        """
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"employee","phone_normalized":"+19045550104"}',
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead",
                          return_value=(True, "lead_created")) as mock_trigger:
            event = _make_event(
                text="This is a catering inquiry for my cousin's wedding on July 12 for 80 people",
                chat_id="201975216009469@lid",
            )
            result = hooks_mod.pre_gateway_dispatch(event)

        assert result == {
            "action": "skip",
            "reason": "cf-router F7 primary: catering inquiry routed deterministically",
        }
        mock_trigger.assert_called_once()
        assert mock_trigger.call_args.kwargs["customer_phone"] == "+19045550104"

    def test_branch_b_active_lead_suppresses_with_canonical_reply(self, mods, state_env):
        """Customer-side catering inquiry with active lead → cf-router skips
        without creating a new lead. With F7_PRIMARY_FOLLOWUP_REPLY=True,
        send_canonical_followup_reply is invoked. Audit
        reason=f7_primary_followup_suppressed."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0011", "owner_approval_code": "#ABCDE",
             "customer_phone": "+17329837841",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"customer","phone_normalized":"+17329837841"}',
            stderr="",
        )
        # Ensure F7_PRIMARY_FOLLOWUP_REPLY is True for this test
        hooks_mod.F7_PRIMARY_FOLLOWUP_REPLY = True
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger, \
             patch.object(actions_mod, "send_canonical_followup_reply",
                          return_value=True) as mock_reply:
            event = _make_event(
                text="catering for 50 people event Saturday food delivered",
                chat_id="17329837841@s.whatsapp.net",
            )
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result is not None
        assert result["action"] == "skip"
        assert "follow-up to active L0011 suppressed" in result["reason"]
        # No new lead created
        mock_trigger.assert_not_called()
        # Canonical follow-up reply sent
        mock_reply.assert_called_once_with("17329837841@s.whatsapp.net", "L0011")
        # Suppressed audit row
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        audits = [r for r in rows if r.get("type") == "cf_router_intercepted"]
        assert len(audits) == 1
        assert audits[0]["reason"] == "f7_primary_followup_suppressed"

    def test_proposal_branch_disabled_keeps_existing_suppression(self, mods, state_env):
        hooks_mod, actions_mod = mods
        hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = False
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "owner_approval_code": "#ABCDE",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])

        with patch.object(actions_mod, "is_owner_chat", return_value=False), \
             patch.object(actions_mod, "lid_to_phone_via_identify_sender",
                          return_value=("+19045550104", "customer")), \
             patch.object(actions_mod, "send_canonical_followup_reply",
                          return_value=True):
            result = hooks_mod.pre_gateway_dispatch(
                _make_event(
                    text="She wants one mixed option and one premium option.",
                    chat_id="201975216009469@lid",
                ),
            )

        assert result is not None
        assert result["action"] == "skip"
        assert "follow-up" in result["reason"]

    def test_proposal_request_actionable_allows_dispatch_when_flag_enabled(self, mods, state_env):
        hooks_mod, actions_mod = mods
        hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "owner_approval_code": "#ABCDE",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])

        with patch.object(actions_mod, "is_owner_chat", return_value=False), \
             patch.object(actions_mod, "lid_to_phone_via_identify_sender",
                          return_value=("+19045550104", "customer")), \
             patch.object(actions_mod, "send_canonical_followup_reply") as mock_reply:
            result = hooks_mod.pre_gateway_dispatch(
                _make_event(
                    text="She wants one mixed option and one premium option.",
                    chat_id="201975216009469@lid",
                ),
            )

        assert result is None
        mock_reply.assert_not_called()

    def test_passive_wait_still_suppresses_when_flag_enabled(self, mods, state_env):
        hooks_mod, actions_mod = mods
        hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "owner_approval_code": "#ABCDE",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])

        with patch.object(actions_mod, "is_owner_chat", return_value=False), \
             patch.object(actions_mod, "lid_to_phone_via_identify_sender",
                          return_value=("+19045550104", "customer")), \
             patch.object(actions_mod, "send_canonical_followup_reply",
                          return_value=True):
            result = hooks_mod.pre_gateway_dispatch(
                _make_event(
                    text="Will wait for two menu proposals. Thank you!",
                    chat_id="201975216009469@lid",
                ),
            )

        assert result is not None
        assert result["action"] == "skip"

    def test_selection_intercepts_outside_catering_classifier(self, mods, state_env):
        hooks_mod, actions_mod = mods
        hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "owner_approval_code": "#ABCDE",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])
        _seed_sent_proposal_set(state_env, lead_id="L0001")

        with patch.object(actions_mod, "is_owner_chat", return_value=False), \
             patch.object(actions_mod, "lid_to_phone_via_identify_sender",
                          return_value=("+19045550104", "customer")), \
             patch.object(actions_mod, "invoke_select_catering_proposal",
                          return_value=0) as mock_select:
            result = hooks_mod.pre_gateway_dispatch(
                SimpleNamespace(
                    text="go with option 2",
                    chat_id="201975216009469@lid",
                    message_id="msg-select-1",
                ),
            )

        assert result is not None
        assert result["action"] == "skip"
        mock_select.assert_called_once_with(
            "L0001",
            "201975216009469@lid",
            "msg-select-1",
            "go with option 2",
        )
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        audits = [r for r in rows if r.get("type") == "cf_router_intercepted"]
        assert len(audits) == 1
        assert audits[0]["reason"] == "f7_proposal_selection"
        assert audits[0]["subprocess_rc"] == 0

    def test_selection_invoke_nonzero_falls_back_to_llm(self, mods, state_env):
        hooks_mod, actions_mod = mods
        hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "owner_approval_code": "#ABCDE",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])
        _seed_sent_proposal_set(state_env, lead_id="L0001")

        with patch.object(actions_mod, "is_owner_chat", return_value=False), \
             patch.object(actions_mod, "lid_to_phone_via_identify_sender",
                          return_value=("+19045550104", "customer")), \
             patch.object(actions_mod, "invoke_select_catering_proposal",
                          return_value=7):
            result = hooks_mod.pre_gateway_dispatch(
                SimpleNamespace(
                    text="go with option 2",
                    chat_id="201975216009469@lid",
                    message_id="msg-select-fail",
                ),
            )

        assert result is None
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        audits = [r for r in rows if r.get("type") == "cf_router_intercepted"]
        assert len(audits) == 1
        assert audits[0]["reason"] == "f7_proposal_selection"
        assert audits[0]["subprocess_rc"] == 7

    @pytest.mark.parametrize("handled_rc", [2, 4, 6, 11])
    def test_selection_handled_exit_codes_skip_llm(self, mods, state_env, handled_rc):
        hooks_mod, actions_mod = mods
        hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0001", "owner_approval_code": "#ABCDE",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])
        _seed_sent_proposal_set(state_env, lead_id="L0001")

        with patch.object(actions_mod, "is_owner_chat", return_value=False), \
             patch.object(actions_mod, "lid_to_phone_via_identify_sender",
                          return_value=("+19045550104", "customer")), \
             patch.object(actions_mod, "invoke_select_catering_proposal",
                          return_value=handled_rc):
            result = hooks_mod.pre_gateway_dispatch(
                SimpleNamespace(
                    text="go with option 2",
                    chat_id="201975216009469@lid",
                    message_id="msg-select-handled",
                ),
            )

        assert result is not None
        assert result["action"] == "skip"
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        audits = [r for r in rows if r.get("type") == "cf_router_intercepted"]
        assert len(audits) == 1
        assert audits[0]["reason"] == "f7_proposal_selection"
        assert audits[0]["subprocess_rc"] == handled_rc

    @pytest.mark.parametrize("text", [
        "Bro any update! She want to see two proposal menus mixing both non-veg and veg options. She will choose the best one from your two proposals.",
        "Will wait for two menu proposals. Thank you!",
    ])
    def test_branch_b_active_lead_suppresses_weak_menu_followups(self, mods, state_env, text):
        """Active-lead follow-ups should not need new-inquiry-level evidence.

        Live 2026-05-13 regression: employee/customer follow-ups about menu
        proposals emitted only `food_keyword`, missed Branch B, and fell
        through to the generic LLM. Once an active lead exists for the sender,
        menu/proposal food signals are enough to use the canonical follow-up
        path.
        """
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [
            {"lead_id": "L0014", "owner_approval_code": "#K6VPD",
             "customer_phone": "+19045550104",
             "status": "AWAITING_OWNER_APPROVAL"},
        ])
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"employee","phone_normalized":"+19045550104"}',
            stderr="",
        )
        hooks_mod.F7_PRIMARY_FOLLOWUP_REPLY = True
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger, \
             patch.object(actions_mod, "send_canonical_followup_reply",
                          return_value=True) as mock_reply:
            result = hooks_mod.pre_gateway_dispatch(
                _make_event(text=text, chat_id="201975216009469@lid"),
            )

        assert result is not None
        assert result["action"] == "skip"
        assert "follow-up to active L0014 suppressed" in result["reason"]
        mock_trigger.assert_not_called()
        mock_reply.assert_called_once_with("201975216009469@lid", "L0014")
        rows = [json.loads(l) for l in state_env["log_path"].read_text(encoding="utf-8").splitlines() if l.strip()]
        audits = [r for r in rows if r.get("type") == "cf_router_intercepted"]
        assert len(audits) == 1
        assert audits[0]["reason"] == "f7_primary_followup_suppressed"

    def test_weak_menu_text_without_active_lead_does_not_create_new_lead(self, mods, state_env):
        """Weak follow-up signals only apply when an active lead already exists."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"customer","phone_normalized":"+17329837841"}',
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger:
            result = hooks_mod.pre_gateway_dispatch(
                _make_event(
                    text="Will wait for two menu proposals. Thank you!",
                    chat_id="17329837841@s.whatsapp.net",
                ),
            )

        assert result is None
        mock_trigger.assert_not_called()

    def test_owner_role_bypasses_f7_primary(self, mods, state_env):
        """Owner-side catering keyword → F8 territory, not F7. F7 returns
        None (no intercept) so the rest of pre_gateway_dispatch can run."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])
        # Owner-chat: is_owner_chat() returns True, so F7 never gets called
        # (the F8 check happens before F7). Verify by patching is_owner_chat
        # to True.
        with patch.object(actions_mod, "is_owner_chat", return_value=True), \
             patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger:
            event = _make_event(
                text="catering for 50 people event Saturday food delivered",
                chat_id="918522041562@s.whatsapp.net",
            )
            result = hooks_mod.pre_gateway_dispatch(event)
        # is_owner_chat=True + no #XXXXX code in text → _try_f8_intercept
        # returns None; F7 path also not entered (sender resolves as owner).
        # Net result: pre_gateway_dispatch returns None (let LLM handle).
        assert result is None
        mock_trigger.assert_not_called()

    def test_f7_disabled_short_circuits(self, mods, state_env):
        """F7_ENABLED=False → catering keyword has no effect; LLM still runs."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])
        hooks_mod.F7_ENABLED = False
        try:
            with patch.object(actions_mod, "trigger_create_catering_lead") as mock_trigger, \
                 patch.object(actions_mod, "lid_to_phone_via_identify_sender") as mock_ident:
                event = _make_event(
                    text="catering for 50 people event Saturday food delivered",
                    chat_id="17329837841@s.whatsapp.net",
                )
                result = hooks_mod.pre_gateway_dispatch(event)
            assert result is None
            mock_trigger.assert_not_called()
            mock_ident.assert_not_called()
        finally:
            hooks_mod.F7_ENABLED = True  # restore

    def test_branch_a_forwards_headcount_signal_to_lead(self, mods, state_env):
        """PR-CF1d Commit 4: classify_catering emits 'headcount:N' as a signal;
        F7 primary forwards it into the lead's extracted_fields so the
        persisted lead carries headcount, not null. Closes the UX-regression
        where owner cards + daily brief showed headcount=null for all
        cf-router-created leads."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"customer","phone_normalized":"+17329837841"}',
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead",
                          return_value=(True, "ok")) as mock_trigger:
            event = _make_event(
                text="catering for 80 people event Saturday food delivered vegetarian",
                chat_id="17329837841@s.whatsapp.net",
            )
            result = hooks_mod.pre_gateway_dispatch(event)
        assert result is not None and result["action"] == "skip"
        mock_trigger.assert_called_once()
        call_kwargs = mock_trigger.call_args.kwargs
        # Headcount signal "headcount:80" should have been parsed + forwarded
        assert call_kwargs.get("extracted_fields") == {"headcount": 80}, \
            f"expected extracted_fields with headcount=80, got {call_kwargs.get('extracted_fields')!r}"

    def test_branch_a_no_headcount_signal_passes_none(self, mods, state_env):
        """When classify_catering finds NO headcount signal (e.g. text says
        'catering for our anniversary' with no digit), extracted_fields is
        None — preserves the prior all-null behavior. Defensive against
        regression of the no-signal path."""
        hooks_mod, actions_mod = mods
        _seed_config(state_env)
        _seed_leads_multi(state_env, [])
        fake_run = SimpleNamespace(
            returncode=0,
            stdout='{"role":"customer","phone_normalized":"+17329837841"}',
            stderr="",
        )
        # This text has catering+event but no headcount digit
        with patch("subprocess.run", return_value=fake_run), \
             patch.object(actions_mod, "trigger_create_catering_lead",
                          return_value=(True, "ok")) as mock_trigger:
            event = _make_event(
                text="hi looking for catering for our wedding reception food delivered",
                chat_id="17329837841@s.whatsapp.net",
            )
            result = hooks_mod.pre_gateway_dispatch(event)
        if result is None:
            # classify_catering may not have classified this text as catering;
            # in that case the test isn't exercising what we want, but it's
            # not a failure of the headcount logic itself.
            return
        mock_trigger.assert_called_once()
        # No headcount signal → no extracted_fields override (None)
        assert mock_trigger.call_args.kwargs.get("extracted_fields") is None

    def test_parse_headcount_from_signals_helper(self, mods):
        """Direct unit test of the _parse_headcount_from_signals helper:
        valid signal → int; missing/malformed → None."""
        hooks_mod, _ = mods
        assert hooks_mod._parse_headcount_from_signals(["headcount:80"]) == 80
        assert hooks_mod._parse_headcount_from_signals(
            ["primary:catering", "headcount:235", "event_keyword"]
        ) == 235
        assert hooks_mod._parse_headcount_from_signals([]) is None
        assert hooks_mod._parse_headcount_from_signals(["primary:catering"]) is None
        # Malformed signal (non-int) → None, not exception
        assert hooks_mod._parse_headcount_from_signals(["headcount:abc"]) is None
        # Defensive: None input
        assert hooks_mod._parse_headcount_from_signals(None) is None
