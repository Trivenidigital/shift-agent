"""Owner token `mark <id> done` must reach the compliance kernel, or nothing does.

`mark-compliance-item-done.py` is documented only by the compliance_owner_query
SKILL, and the SKILL dispatcher cannot run on the box (`skills` toolset
disabled), so the kernel had no caller. These tests pin the deterministic
cf-router arm that gives it one, against the REAL kernel run as a subprocess.

Dormant behind COMPLIANCE_MARK_DONE_ENABLED: unset, the arm returns None before
any I/O and routing is byte-identical.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="cf-router actions + the compliance kernel import safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"
MARK_SCRIPT = REPO / "src" / "agents" / "compliance" / "scripts" / "mark-compliance-item-done.py"

OWNER_JID = "19045550100@s.whatsapp.net"
OTHER_JID = "15550100077@s.whatsapp.net"

R_UNCERTAIN = ("I couldn't confirm whether {item_id} was marked done. Please don't "
               "rely on it yet — ask for your compliance deadlines to check, then "
               "try again.")


def _load_plugin_modules():
    """Load hooks + actions under a synthetic package (the dir name is hyphenated).

    Mirrors tests/test_cf_router_plugin.py, including leaving `schemas`/`safe_io`
    in sys.modules so co-resident test modules keep their bindings.
    """
    if str(PLATFORM_DIR) not in sys.path:
        sys.path.insert(0, str(PLATFORM_DIR))
    pkg_name = "cf_router_compliance_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]
    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg
    mods = {}
    for name in ("actions", "hooks"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", PLUGIN_DIR / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{name}"] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["hooks"], mods["actions"]


def _write_config(path: Path, *, compliance_enabled: bool = True) -> None:
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": OWNER_JID},
        "limits": {},
        "alerting": {"pushover_user_key": "test_k", "pushover_app_token": "test_t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "compliance": {"enabled": compliance_enabled},
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """Armed arm, owner chat, real kernel paths in tmp, captured sends."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    config = tmp_path / "config.yaml"
    _write_config(config)
    items = state / "compliance-items.json"
    items.write_text(json.dumps({"schema_version": 1, "items": [
        {"id": "health_permit", "name": "Health Permit", "category": "inspection",
         "renewal_date": "2026-09-01", "recurrence_days": 365},
        {"id": "fire_cert", "name": "Fire Cert", "category": "other",
         "renewal_date": "2026-09-15", "recurrence_days": 0},
    ]}), encoding="utf-8")
    log = logs / "decisions.log"
    log.write_text("", encoding="utf-8")

    # The kernel subprocess inherits os.environ (invoke passes no env=).
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(config))
    monkeypatch.setenv("SHIFT_AGENT_COMPLIANCE_ITEMS_PATH", str(items))
    monkeypatch.setenv("SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH",
                       str(state / "compliance-last-sent.json"))
    monkeypatch.setenv("SHIFT_AGENT_COMPLIANCE_LOCK_PATH",
                       str(state / "compliance-check.json.lock"))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(log))
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "2026-08-08T09:00:00-04:00")
    monkeypatch.setenv("PYTHONPATH", str(PLATFORM_DIR))
    monkeypatch.setenv("COMPLIANCE_MARK_DONE_ENABLED", "1")

    hooks_mod, actions_mod = _load_plugin_modules()
    monkeypatch.setattr(actions_mod, "CONFIG_PATH", config)
    monkeypatch.setattr(actions_mod, "LOG_PATH", log)
    monkeypatch.setattr(actions_mod, "PYTHON_BIN", Path(sys.executable))
    monkeypatch.setattr(actions_mod, "MARK_COMPLIANCE_ITEM_DONE_BIN", MARK_SCRIPT)
    monkeypatch.setattr(actions_mod, "identify_sender_metadata",
                        lambda ident: {"role": "owner"})
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: cid == OWNER_JID)

    import safe_io
    sends = []

    def _fake_bridge_post(jid, message, *, action_context=None, **kw):
        sends.append({"jid": jid, "text": message, "ctx": action_context, "kw": kw})
        return True, "mid", "", "sent"

    monkeypatch.setattr(safe_io, "bridge_post", _fake_bridge_post)
    return SimpleNamespace(hooks=hooks_mod, actions=actions_mod, items=items,
                           log=log, config=config, sends=sends)


def _rows(log: Path) -> list[dict]:
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _renewal(items: Path, item_id: str):
    doc = json.loads(items.read_text(encoding="utf-8"))
    return next((i["renewal_date"] for i in doc["items"] if i["id"] == item_id), None)


def _dispatch(p, text, message_id, chat_id=OWNER_JID):
    event = SimpleNamespace(text=text, chat_id=chat_id, message_id=message_id)
    return p.hooks.pre_gateway_dispatch(event)


def _stub_invoke(monkeypatch, p, result):
    calls = []

    def _fake(item_id):
        calls.append(item_id)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(p.actions, "invoke_mark_compliance_item_done", _fake)
    return calls


# ─── vertical, through the real dispatch entry point and the real kernel ────


def test_vertical_owner_token_advances_recurring_item(plugin, monkeypatch):
    p = plugin
    before = p.items.read_bytes()

    # MUTANT first: the kernel "succeeds" with no evidence. The store must be
    # untouched and the owner must NOT be told it was marked.
    with monkeypatch.context() as m:
        _stub_invoke(m, p, (0, "{}", ""))
        result = _dispatch(p, "mark health_permit done", "wamid.cm-mutant")
    assert result is not None and result["action"] == "skip"
    assert p.items.read_bytes() == before
    assert p.sends[-1]["text"] == R_UNCERTAIN.format(item_id="health_permit")
    assert not [r for r in _rows(p.log) if r.get("type") == "compliance_item_marked_done"]

    # TARGET: the real kernel advances renewal_date by recurrence_days.
    result = _dispatch(p, "mark health_permit done", "wamid.cm-real")
    assert result == {"action": "skip",
                      "reason": "cf-router: invoked mark-compliance-item-done "
                                "health_permit (rc=0)"}
    assert _renewal(p.items, "health_permit") == "2027-09-01"
    assert p.sends[-1]["text"] == (
        "Marked health_permit done (it was due 2026-09-01). Next due date: 2027-09-01.")

    # WITNESS: the kernel's own row exists, and the route row precedes it.
    rows = _rows(p.log)
    marked = [i for i, r in enumerate(rows)
              if r.get("type") == "compliance_item_marked_done"
              and r.get("item_id") == "health_permit"]
    routed = [i for i, r in enumerate(rows)
              if r.get("type") == "dispatcher_routed"
              and r.get("routed_to_skill") == "mark_compliance_item_done"]
    assert len(marked) == 1
    assert routed and min(routed) < marked[0]
    assert rows[marked[0]]["actor"] == "owner"


def test_vertical_oneshot_item_deleted_reply_oneshot(plugin):
    p = plugin
    result = _dispatch(p, "mark fire_cert done", "wamid.cm-oneshot")
    assert result is not None and result["action"] == "skip"
    assert _renewal(p.items, "fire_cert") is None
    assert _renewal(p.items, "health_permit") == "2026-09-01"
    assert p.sends[-1]["text"] == (
        "Marked fire_cert done (it was due 2026-09-15). It was a one-time item, "
        "so it is no longer tracked.")


def test_recent_double_mark_rc3_reply(plugin):
    p = plugin
    _dispatch(p, "mark health_permit done", "wamid.cm-1")
    result = _dispatch(p, "Mark health_permit as done!", "wamid.cm-2")
    assert result["reason"].endswith("(rc=3)")
    assert _renewal(p.items, "health_permit") == "2027-09-01"   # advanced once
    assert p.sends[-1]["text"] == (
        "health_permit was already marked done in the last 15 minutes, so nothing changed.")
    assert p.sends[-1]["ctx"].claims_action_completed is False
    assert len([r for r in _rows(p.log)
                if r.get("type") == "compliance_item_marked_done"]) == 1


def test_item_not_found_rc1_reply_and_skip(plugin):
    """The operator's arming witness: a probe id reaches the kernel, is routed,
    and mutates nothing."""
    p = plugin
    before = p.items.read_bytes()
    result = _dispatch(p, "mark zz_probe_nonexistent done", "wamid.cm-probe")
    assert result == {"action": "skip",
                      "reason": "cf-router: invoked mark-compliance-item-done "
                                "zz_probe_nonexistent (rc=1)"}
    assert p.items.read_bytes() == before
    assert p.sends[-1]["text"] == (
        "I couldn't find a compliance item called zz_probe_nonexistent, so nothing "
        "was marked done. Ask \"what compliance deadlines do I have?\" to see the "
        "item names.")
    rows = _rows(p.log)
    assert any(r.get("type") == "dispatcher_routed"
               and r.get("routed_to_skill") == "mark_compliance_item_done" for r in rows)
    assert not any(r.get("type") == "compliance_item_marked_done" for r in rows)


# ─── gates: every miss is a byte-identical fall-through ─────────────────────


def test_flag_unset_is_byte_identical(plugin, monkeypatch):
    p = plugin
    monkeypatch.delenv("COMPLIANCE_MARK_DONE_ENABLED")
    owner_calls, invoke_calls, run_calls = [], [], []
    monkeypatch.setattr(p.actions, "is_owner_chat",
                        lambda cid: owner_calls.append(cid) or True)
    monkeypatch.setattr(p.actions, "invoke_mark_compliance_item_done",
                        lambda item_id: invoke_calls.append(item_id) or (0, "", ""))
    monkeypatch.setattr(p.actions.subprocess, "run",
                        lambda *a, **kw: run_calls.append(a) or None)
    before = p.items.read_bytes()
    for value in (None, "0", "true", "yes"):
        if value is not None:
            monkeypatch.setenv("COMPLIANCE_MARK_DONE_ENABLED", value)
        assert p.hooks._try_compliance_mark_done(
            "mark health_permit done", OWNER_JID, "wamid.off") is None
    assert owner_calls == [] and invoke_calls == [] and run_calls == []
    assert p.sends == []
    assert p.items.read_bytes() == before
    assert p.log.read_text(encoding="utf-8") == ""


def test_non_owner_token_falls_through_no_mutation_no_reply(plugin, monkeypatch):
    p = plugin
    calls = _stub_invoke(monkeypatch, p, (0, "{}", ""))
    before = p.items.read_bytes()
    assert p.hooks._try_compliance_mark_done(
        "mark health_permit done", OTHER_JID, "wamid.other") is None
    assert calls == [] and p.sends == []
    assert p.items.read_bytes() == before
    assert p.log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("text", [
    "I think I did the inspection",
    "mark health permit done",
    "please mark x done",
    "mark " + "a" * 41 + " done",
    "mark x done tomorrow",
])
def test_near_miss_texts_do_not_match(plugin, monkeypatch, text):
    p = plugin
    calls = _stub_invoke(monkeypatch, p, (0, "{}", ""))
    assert p.hooks._try_compliance_mark_done(text, OWNER_JID, "wamid.near") is None
    assert calls == [] and p.sends == []


@pytest.mark.parametrize("text", [
    "mark it done",
    "mark as done",
    "mark this done",
    "Mark that done!",
    "mark them as done",
])
def test_pronoun_ids_fall_through_before_route(plugin, monkeypatch, text):
    """Ordinary owner speech is not a compliance token: no route row, no reply."""
    p = plugin
    owner_calls = []
    monkeypatch.setattr(p.actions, "is_owner_chat",
                        lambda cid: owner_calls.append(cid) or True)
    calls = _stub_invoke(monkeypatch, p, (0, "{}", ""))
    assert p.hooks._try_compliance_mark_done(text, OWNER_JID, "wamid.pronoun") is None
    assert calls == [] and p.sends == [] and owner_calls == []
    assert p.log.read_text(encoding="utf-8") == ""


def test_real_id_starting_with_stopword_still_matches(plugin, monkeypatch):
    p = plugin
    calls = _stub_invoke(monkeypatch, p, (1, json.dumps({"error": "item_not_found"}), ""))
    result = p.hooks._try_compliance_mark_done("mark as_built done", OWNER_JID, "wamid.asb")
    assert calls == ["as_built"]
    assert result is not None and result["action"] == "skip"


def test_uppercase_id_lowercased(plugin, monkeypatch):
    p = plugin
    calls = _stub_invoke(monkeypatch, p, (0, json.dumps({
        "item_id": "health_permit", "completed": "2026-09-01", "next": "2027-09-01",
        "deleted": False, "sentinel_keys_pruned": 0}), ""))
    sender_block = ('[shift-agent-sender v=1 platform=whatsapp phone="+19045550100" '
                    'lid=null fromMe=false chat_id="19045550100@s.whatsapp.net"]')
    result = p.hooks._try_compliance_mark_done(
        f"{sender_block}\nMark HEALTH_PERMIT Done.", OWNER_JID, "wamid.upper")
    assert calls == ["health_permit"]
    assert result["action"] == "skip"


def test_compliance_disabled_replies_disabled_no_invoke(plugin, monkeypatch):
    p = plugin
    _write_config(p.config, compliance_enabled=False)
    calls = _stub_invoke(monkeypatch, p, (0, "{}", ""))
    result = p.hooks._try_compliance_mark_done(
        "mark health_permit done", OWNER_JID, "wamid.disabled")
    assert result is not None and result["action"] == "skip"
    assert calls == []
    assert p.sends[-1]["text"] == (
        "Compliance deadline tracking isn't enabled for this business, so nothing "
        "was marked done.")
    assert not any(r.get("type") == "dispatcher_routed" for r in _rows(p.log))


def test_config_unreadable_replies_uncertain_no_invoke(plugin, monkeypatch):
    p = plugin
    p.config.write_text("compliance: [unclosed\n", encoding="utf-8")
    calls = _stub_invoke(monkeypatch, p, (0, "{}", ""))
    result = p.hooks._try_compliance_mark_done(
        "mark health_permit done", OWNER_JID, "wamid.badcfg")
    assert result is not None and result["action"] == "skip"
    assert calls == []
    assert p.sends[-1]["text"] == R_UNCERTAIN.format(item_id="health_permit")


def test_kernel_timeout_is_uncertain_not_failure(plugin, monkeypatch):
    p = plugin
    _stub_invoke(monkeypatch, p, (124, "", "timeout"))
    result = p.hooks._try_compliance_mark_done(
        "mark health_permit done", OWNER_JID, "wamid.timeout")
    assert result["reason"].endswith("(rc=124)")
    assert p.sends[-1]["text"] == R_UNCERTAIN.format(item_id="health_permit")
    errors = [r for r in _rows(p.log)
              if r.get("type") == "cf_router_intercepted" and r.get("reason") == "error"]
    assert len(errors) == 1
    assert errors[0]["subprocess_rc"] == 124
    assert errors[0]["detail"].startswith("compliance_mark_done_uncertain")


# ─── send context and terminality ───────────────────────────────────────────


def test_success_send_context_verified(plugin):
    p = plugin
    _dispatch(p, "mark health_permit done", "wamid.ctx-ok")
    ctx = p.sends[-1]["ctx"]
    assert ctx.action_id == "compliance.mark_done"
    assert ctx.is_regulated_action is False
    assert ctx.claims_action_completed is True
    assert ctx.verified_action_result is True
    assert "automation_control_ack" not in p.sends[-1]["kw"]


@pytest.mark.parametrize("result", [
    (1, json.dumps({"error": "item_not_found", "item_id": "health_permit"}), ""),
    (2, "", "bad input"),
    (3, json.dumps({"error": "recently_marked_done"}), ""),
    (127, "", "no such file"),
    (0, "not json", ""),
    (0, "{}", ""),
])
def test_failure_send_context_not_claiming(plugin, monkeypatch, result):
    p = plugin
    _stub_invoke(monkeypatch, p, result)
    p.hooks._try_compliance_mark_done("mark health_permit done", OWNER_JID, "wamid.ctx")
    ctx = p.sends[-1]["ctx"]
    assert ctx.claims_action_completed is False
    assert ctx.verified_action_result is False


def test_post_route_never_returns_none(plugin, monkeypatch):
    """Past the route row the kernel may have mutated state; the LLM must not
    also answer the turn, even when the invoker and the send both blow up."""
    p = plugin
    _stub_invoke(monkeypatch, p, RuntimeError("boom"))
    import safe_io

    def _raising_post(*a, **kw):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(safe_io, "bridge_post", _raising_post)
    result = p.hooks._try_compliance_mark_done(
        "mark health_permit done", OWNER_JID, "wamid.boom")
    assert result is not None and result["action"] == "skip"
    result = _dispatch(p, "mark health_permit done", "wamid.boom2")
    assert result is not None and result["action"] == "skip"
