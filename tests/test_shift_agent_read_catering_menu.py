"""Focused tests for the `get_catering_menu_items` plugin tool.

Scope mirrors tests/test_shift_agent_read_equipment_tool.py, with one structural
difference: this tool is PUBLIC, so most of what the owner-only files spend their
authorization tests on becomes a single inverted assertion — the handler must
serve a caller it never identified, and must not reach `identify-sender` at all.

What replaces the authorization surface here is PRICE DISCIPLINE. A price read
out to a customer is a commitment, so the tests assert on the exact bound
outbound string, not only on the JSON: the rendered `$12.99` has to be the
store's own number, the menu's own date has to appear on every rendered reply,
and a withdrawn dish must be unreachable through every filter combination.

Tests that reach the store need safe_io/fcntl and stay `linux_only`.
"""
from __future__ import annotations

import json
import platform
import sys
import types
from pathlib import Path
from typing import get_args

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "shift-agent-read"
PLATFORM_DIR = REPO / "src" / "platform"

linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="needs POSIX: loading the store goes through safe_io, which uses fcntl",
)

# A customer, an employee and nobody at all. None of them should matter.
CUSTOMER = "19045557777@s.whatsapp.net"
EMPLOYEE = "19045550200@s.whatsapp.net"

MENU_UPDATED = "2026-05-05T20:36:25-04:00"
MENU_DATE = "2026-05-05"
NOW = "2026-08-18T09:00:00-04:00"   # 105 days after the menu was published


def _install_session_stub(principal: str) -> None:
    mod = types.ModuleType("gateway.session_context")
    values = {"HERMES_SESSION_USER_ID": principal,
              "HERMES_SESSION_ID": "sess-test",
              "HERMES_SESSION_MESSAGE_ID": "msg-test"}
    mod.get_session_env = lambda name, default="": values.get(name, default)
    pkg = sys.modules.get("gateway") or types.ModuleType("gateway")
    pkg.session_context = mod
    sys.modules["gateway"] = pkg
    sys.modules["gateway.session_context"] = mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Config + a menu path that is deliberately absent to begin with."""
    (tmp_path / "state").mkdir()
    _write_cfg(tmp_path)
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("SHIFT_AGENT_CATERING_MENU_PATH",
                       str(tmp_path / "state" / "catering-menu.json"))
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", NOW)
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    return tmp_path


PKG = "shift_agent_read_pkg_catering_menu"


def _load_package():
    """Import the plugin as a PACKAGE so its relative imports resolve."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        PKG, PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = mod
    spec.loader.exec_module(mod)
    return mod


def _pkg(principal=CUSTOMER):
    _install_session_stub(principal)
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    return _load_package()


def _tool(principal=CUSTOMER):
    return _pkg(principal).catering_menu_tool


def _write_cfg(env, catering_block=...):
    """Rewrite config.yaml. Pass catering_block=None to omit the block entirely."""
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    if catering_block is ...:
        catering_block = {"enabled": True}
    if catering_block is not None:
        cfg["catering"] = catering_block
    (env / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _seed(env, items, updated_at=MENU_UPDATED, version=2):
    (env / "state" / "catering-menu.json").write_text(
        json.dumps({"version": version, "updated_at": updated_at,
                    "updated_by": "photo-ocr", "items": items}),
        encoding="utf-8")


def _item(name, price=5.99, category="appetizer", tags=("veg",), **kw):
    base = {"name": name, "price_usd": price, "category": category,
            "dietary_tags": list(tags), "available": True, "notes": "",
            "serves": None}
    base.update(kw)
    return base


# Shaped after the real store: 78 items, appetizer-heavy, `serves` mostly unset.
MENU = [
    _item("Idly (3 PCS)", 5.99),
    _item("Veg Samosa", 4.50),
    _item("Chicken 65", 9.99, tags=("non-veg", "spicy")),
    _item("Hyderabadi Chicken Biryani", 14.99, category="main",
          tags=("non-veg", "spicy"), serves=2),
    _item("Veg Biryani Tray", 89.00, category="main", serves=10),
    _item("Gulab Jamun", 6.00, category="dessert"),
    _item("Discontinued Paneer Roll", 7.25, available=False),
]


class _Binder:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return self.succeed


def _binder(monkeypatch, tool, succeed=True):
    b = _Binder(succeed=succeed)
    monkeypatch.setattr(tool, "_bind_outbound", b)
    return b


# ── registration contract ──────────────────────────────────────────────────

def test_schema_is_the_inner_function_object_with_a_description(env):
    """Double-wrapping empties function.description and removes the tool's line
    from the deferred catalog, making it undiscoverable. Pin the shape."""
    t = _tool()
    assert set(t.SCHEMA) == {"name", "description", "parameters"}
    assert "type" not in t.SCHEMA and "function" not in t.SCHEMA
    assert t.SCHEMA["name"] == "get_catering_menu_items"
    assert len(t.SCHEMA["description"]) > 80


def test_arguments_carry_no_identity_field(env):
    props = _tool().SCHEMA["parameters"]["properties"]
    assert set(props) == {"name_contains", "category", "dietary_tag", "max_items"}
    forbidden = {"owner", "role", "phone", "user", "user_id", "sender", "identity"}
    assert not (set(props) & forbidden)
    assert _tool().SCHEMA["parameters"]["required"] == []


def test_schema_enums_match_the_deployed_literals(env):
    """The JSON-Schema enum has to be a literal list at registration time, before
    the platform modules are importable — so it is a copy, and a copy drifts.
    Pin both against the schema they were copied from."""
    from schemas import DietaryTag, MenuCategory
    t = _tool()
    assert t.CATEGORIES == list(get_args(MenuCategory))
    assert t.DIETARY_TAGS == list(get_args(DietaryTag))
    props = t.SCHEMA["parameters"]["properties"]
    assert props["category"]["enum"] == list(get_args(MenuCategory))
    assert props["dietary_tag"]["enum"] == list(get_args(DietaryTag))


def test_menu_path_default_matches_catering_paths(env, monkeypatch):
    """`catering_paths.CATERING_MENU_PATH` is the canonical write-side location.
    This tool cannot import it (it is not env-overridable), so pin the copy."""
    from catering_paths import CATERING_MENU_PATH
    monkeypatch.delenv("SHIFT_AGENT_CATERING_MENU_PATH", raising=False)
    assert _tool().MENU_PATH == CATERING_MENU_PATH


def test_register_puts_the_tool_in_the_surviving_toolset(env):
    registered = []

    class FakeCtx:
        def register_tool(self, **kw):
            registered.append(kw)

    _pkg().register(FakeCtx())
    by_name = {r["name"]: r for r in registered}
    assert "get_catering_menu_items" in by_name
    captured = by_name["get_catering_menu_items"]
    assert captured["toolset"] == "shift_agent_read"
    assert callable(captured["handler"])
    assert captured["description"] == captured["schema"]["description"]
    assert len(by_name) == len(registered), "duplicate tool name registered"


def test_plugin_manifest_lists_the_tool(env):
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert "get_catering_menu_items" in manifest["provides_tools"]


# ── public by design ───────────────────────────────────────────────────────

@linux_only
@pytest.mark.parametrize("principal", [CUSTOMER, EMPLOYEE, ""])
def test_any_caller_including_an_unbound_session_is_served(env, principal):
    """No owner gate. A menu and its prices are information a business
    publishes; an unbound session is a customer, not an intruder."""
    _seed(env, MENU)
    out = json.loads(_tool(principal).handler({"name_contains": "biryani"}))
    assert out["ok"] is True and out["source_status"] == "populated"
    assert out["matched"] == 2


@linux_only
def test_identify_sender_is_never_called(env, monkeypatch):
    """Falsifiable: resolution is made to explode. A public read that touched it
    would fail this test instead of quietly adding a subprocess to every menu
    question."""
    pkg = _pkg()

    def _explode(_principal):
        raise AssertionError("public tool must not resolve identity")

    monkeypatch.setattr(pkg.identity, "resolve_identity", _explode)
    _seed(env, MENU)
    out = json.loads(pkg.catering_menu_tool.handler({}))
    assert out["ok"] is True


# ── argument validation ────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [
    {"category": "entree"},                 # not a MenuCategory
    {"dietary_tag": "vegetarian"},          # the tag is "veg"
    {"max_items": 0},
    {"max_items": 99},
    {"max_items": "many"},
    {"name_contains": "x" * 81},
])
def test_out_of_schema_arguments_are_refused_not_clamped(env, args):
    """A hidden correction answers a question the customer did not ask —
    `location_tool.top_n` makes the same call."""
    assert json.loads(_tool().handler(args)) == {
        "ok": False, "refused": "invalid_arguments"}


@linux_only
def test_valid_boundary_arguments_are_accepted(env):
    """The falsifier for the test above: the boundary values themselves work."""
    _seed(env, MENU)
    t = _tool()
    for args in ({"max_items": 1}, {"max_items": 25}, {"category": "main"},
                 {"dietary_tag": "veg"}, {"name_contains": "x" * 80}):
        assert json.loads(t.handler(args))["ok"] is True, args


# ── the config enable gate ─────────────────────────────────────────────────

def test_disabled_agent_reports_disabled_not_an_empty_menu(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert out["coverage_status"] == "not_enabled"
    assert b.calls == [t.TPL_DISABLED]
    for absent in ("items", "matched", "items_total", "menu_updated_at"):
        assert absent not in out
    assert "Biryani" not in json.dumps(out)


def test_disabled_agent_does_not_read_the_store(env, monkeypatch):
    """Falsifiable: the store is corrupt, so any read would surface
    state_unreadable. Getting `disabled` proves the gate returned first."""
    _write_cfg(env, {"enabled": False})
    (env / "state" / "catering-menu.json").write_text("{not json", encoding="utf-8")
    t = _tool()
    _binder(monkeypatch, t)
    assert json.loads(t.handler({}))["source_status"] == "disabled"


def test_absent_config_block_is_treated_as_disabled(env, monkeypatch):
    _write_cfg(env, None)
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    assert json.loads(t.handler({}))["source_status"] == "disabled"
    assert b.calls == [t.TPL_DISABLED]


def test_unreadable_config_fails_closed_but_still_binds_a_safe_reply(env, monkeypatch):
    """On a PUBLIC surface an unbound failure hands the turn back to a model that
    was just asked what the biryani costs. Report ok=false AND bind the safe
    sentence."""
    (env / "config.yaml").write_text("customer: [unclosed\n", encoding="utf-8")
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out == {"ok": False, "error": "config_unavailable"}
    assert b.calls == [t.TPL_UNAVAILABLE]


# ── the four states ────────────────────────────────────────────────────────

def test_missing_state_is_distinct(env, monkeypatch):
    assert not (env / "state" / "catering-menu.json").exists()
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "missing"
    assert out["coverage_status"] == "not_configured"
    for absent in ("items", "matched", "items_total", "menu_updated_at"):
        assert absent not in out
    assert b.calls == [t.TPL_UNAVAILABLE]


@linux_only
def test_empty_menu_is_distinct_from_missing(env, monkeypatch):
    """The customer reply is shared on purpose — they cannot act on the
    difference — but the structured result keeps them apart, because the
    operator can. Same split location_tool documents."""
    _seed(env, [])
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty"
    assert out["items_total"] == 0 and out["matched"] == 0 and out["items"] == []
    assert out["menu_updated_at"] == MENU_DATE
    assert b.calls == [t.TPL_UNAVAILABLE]


@linux_only
def test_populated_but_no_match_is_distinct_from_empty(env, monkeypatch):
    """THE distinction: the menu is fine, this dish is not on it. A model that
    collapses this into 'we have no menu' — or helpfully offers a substitute —
    is the failure this state exists to prevent."""
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"name_contains": "sushi"}))
    assert out["source_status"] == "populated"
    assert out["items_total"] == len(MENU) and out["matched"] == 0
    assert out["items"] == [] and out["returned"] == 0
    assert b.calls == [t.TPL_NO_MATCH.format(menu_date=MENU_DATE)]
    assert MENU_DATE in b.calls[0]


@linux_only
def test_populated_positive_control_returns_real_rows(env, monkeypatch):
    """THE anti-stub test: a handler that always refused, or always reported
    zero, would pass every negative case above and fail here."""
    _seed(env, MENU)
    out = json.loads(_tool().handler({"name_contains": "idly"}))
    assert out["ok"] is True and out["source_status"] == "populated"
    assert out["matched"] == 1 and out["returned"] == 1
    assert out["items"] == [{"name": "Idly (3 PCS)", "price_usd": 5.99,
                             "category": "appetizer", "dietary_tags": ["veg"],
                             "serves": None}]


# ── filtering ──────────────────────────────────────────────────────────────

@linux_only
def test_unavailable_items_are_filtered_before_anything_else(env):
    """A withdrawn dish must be unreachable through every route: the unfiltered
    listing, its own name, its category and its tag."""
    _seed(env, MENU)
    t = _tool()
    for args in ({}, {"name_contains": "Paneer"}, {"category": "appetizer"},
                 {"dietary_tag": "veg"}, {"max_items": 25}):
        blob = json.dumps(json.loads(t.handler(args)))
        assert "Paneer" not in blob, args
        assert "7.25" not in blob, args


@linux_only
def test_available_total_excludes_withdrawn_items(env):
    _seed(env, MENU)
    out = json.loads(_tool().handler({}))
    assert out["items_total"] == len(MENU)
    assert out["available_total"] == len(MENU) - 1


@linux_only
@pytest.mark.parametrize("args,expected", [
    ({"name_contains": "biryani"}, {"Hyderabadi Chicken Biryani", "Veg Biryani Tray"}),
    ({"name_contains": "BIRYANI"}, {"Hyderabadi Chicken Biryani", "Veg Biryani Tray"}),
    ({"category": "dessert"}, {"Gulab Jamun"}),
    ({"dietary_tag": "non-veg"}, {"Chicken 65", "Hyderabadi Chicken Biryani"}),
    ({"dietary_tag": "spicy"}, {"Chicken 65", "Hyderabadi Chicken Biryani"}),
    ({"category": "main", "dietary_tag": "veg"}, {"Veg Biryani Tray"}),
    ({"name_contains": "biryani", "category": "main", "dietary_tag": "non-veg"},
     {"Hyderabadi Chicken Biryani"}),
])
def test_filters_are_conjunctive_and_case_insensitive(env, args, expected):
    _seed(env, MENU)
    out = json.loads(_tool().handler(args))
    assert {r["name"] for r in out["items"]} == expected
    assert out["matched"] == len(expected)


@linux_only
def test_no_filters_lists_the_menu_up_to_the_default_cap(env):
    _seed(env, MENU)
    t = _tool()
    out = json.loads(t.handler({}))
    assert out["matched"] == len(MENU) - 1
    assert out["returned"] == min(t.DEFAULT_MAX_ITEMS, len(MENU) - 1)


@linux_only
def test_max_items_caps_rows_without_changing_the_match_count(env, monkeypatch):
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"max_items": 2}))
    assert out["matched"] == len(MENU) - 1 and out["returned"] == 2
    assert len(out["items"]) == 2
    assert t.TPL_MORE.format(returned=2, matched=len(MENU) - 1) in b.calls[0]


# ── price + staleness discipline (the bound outbound text) ─────────────────

@linux_only
def test_the_rendered_reply_carries_the_exact_stored_price(env, monkeypatch):
    """Not rounded, not re-expressed, not totalled. `$14.99` or nothing."""
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    t.handler({"name_contains": "Hyderabadi"})
    text = b.calls[0]
    assert "$14.99" in text
    assert "Hyderabadi Chicken Biryani" in text
    assert "serves ~2" in text


@linux_only
def test_every_rendered_reply_carries_the_menu_date(env, monkeypatch):
    """Unconditional, not threshold-driven: a staleness cutoff is a number
    someone has to keep true, and the day it drifts the customer is quoted a
    stale price with no qualification at all."""
    _seed(env, MENU)
    t = _tool()
    for args in ({}, {"name_contains": "idly"}, {"category": "dessert"},
                 {"name_contains": "sushi"}):
        b = _binder(monkeypatch, t)
        t.handler(args)
        assert MENU_DATE in b.calls[0], args


@linux_only
def test_a_fresh_menu_is_qualified_exactly_like_a_stale_one(env, monkeypatch):
    """The qualification does not depend on age, so it cannot silently lapse."""
    _seed(env, MENU, updated_at="2026-08-18T08:00:00-04:00")
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"name_contains": "idly"}))
    assert out["menu_age_days"] == 0
    assert "2026-08-18" in b.calls[0] and "please confirm" in b.calls[0]


@linux_only
def test_menu_age_days_is_reported_for_the_operator(env):
    _seed(env, MENU)
    out = json.loads(_tool().handler({"name_contains": "idly"}))
    assert out["menu_updated_at"] == MENU_DATE
    assert out["menu_age_days"] == 105
    assert out["menu_version"] == 2


@linux_only
def test_an_unpriced_item_is_marked_not_guessed(env, monkeypatch):
    """`price_usd` is Optional on MenuItem. Null must never render as $0.00."""
    _seed(env, [_item("Chef's Table Tasting", price=None, category="special")])
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["items"][0]["price_usd"] is None
    assert "price on request" in b.calls[0]
    assert "$0" not in b.calls[0]


@linux_only
def test_owner_notes_are_never_shown_to_a_customer(env):
    """`notes` is the owner's own free text — allergen memos, supplier reminders
    — and is not written for customers to read."""
    _seed(env, [_item("Idly (3 PCS)", notes="SECRETNOTE reheat 4 min")])
    out = json.loads(_tool().handler({}))
    assert "notes" not in out["items"][0]
    assert "SECRETNOTE" not in json.dumps(out)


@linux_only
def test_row_keys_are_exactly_the_contract(env):
    _seed(env, MENU)
    out = json.loads(_tool().handler({"name_contains": "idly"}))
    assert set(out) == {"ok", "source_status", "menu_version", "menu_updated_at",
                        "menu_age_days", "items_total", "available_total",
                        "matched", "returned", "items"}
    assert set(out["items"][0]) == {"name", "price_usd", "category",
                                    "dietary_tags", "serves"}


@linux_only
def test_handler_returns_json_text_not_a_dict(env):
    _seed(env, MENU)
    res = _tool().handler({})
    assert isinstance(res, str)
    assert isinstance(json.loads(res), dict)


# ── fail closed ────────────────────────────────────────────────────────────

@linux_only
def test_unreadable_store_fails_closed_with_a_bound_reply(env, monkeypatch):
    """A corrupt/schema-invalid menu is NOT an empty menu."""
    (env / "state" / "catering-menu.json").write_text(
        json.dumps({"version": 2, "updated_at": MENU_UPDATED,
                    "items": [{"name": "no price field", "price_usd": "free"}]}),
        encoding="utf-8")
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out == {"ok": False, "error": "state_unreadable"}
    assert b.calls == [t.TPL_UNAVAILABLE]


@linux_only
def test_an_unresolvable_today_degrades_only_the_operator_field(env, monkeypatch):
    """`menu_age_days` is the operator's number; the customer's date comes from
    the menu itself, so the answer survives."""
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "not-a-timestamp")
    _seed(env, MENU)
    t = _tool()
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"name_contains": "idly"}))
    assert out["ok"] is True and out["menu_age_days"] is None
    assert MENU_DATE in b.calls[0]


@linux_only
@pytest.mark.parametrize("state", ["disabled", "missing", "empty", "no_match",
                                   "populated", "unreadable"])
def test_bind_failure_suppresses_every_payload(env, monkeypatch, state):
    """THE load-bearing rule, and here it covers the SUCCESS path too: no price
    reaches a customer unless its exact wording was bound first."""
    args = {}
    if state == "disabled":
        _write_cfg(env, {"enabled": False})
        _seed(env, MENU)
    elif state == "empty":
        _seed(env, [])
    elif state == "no_match":
        _seed(env, MENU)
        args = {"name_contains": "sushi"}
    elif state == "populated":
        _seed(env, MENU)
    elif state == "unreadable":
        (env / "state" / "catering-menu.json").write_text("{not json",
                                                          encoding="utf-8")
    t = _tool()
    _binder(monkeypatch, t, succeed=False)
    out = json.loads(t.handler(args))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    for absent in ("items", "matched", "items_total", "source_status",
                   "coverage_status", "menu_updated_at", "error"):
        assert absent not in out, f"{absent!r} leaked on a guard-unavailable refusal"


# ── description pins ───────────────────────────────────────────────────────

def test_description_carries_the_scope_rules(env):
    d = _tool().DESCRIPTION
    assert "PUBLISHED catering menu" in d
    assert "NOT a statement about what the kitchen cooks" in d
    assert "establishes that the business does not cater" in d
    assert "never that it does not exist" in d
    assert "never offer a substitute the tool did not return" in d


def test_description_carries_the_price_rules(env):
    """A price stated to a customer is a commitment; these are the sentences
    that say so. An edit must not drop them silently."""
    d = _tool().DESCRIPTION
    assert "never quote a price this tool did not return" in d
    assert "never round or re-express a price" in d
    assert "a commitment" in d
    assert "last-updated date" in d and "never present it" in d
    assert "READ-ONLY" in d and "cannot take an order" in d
