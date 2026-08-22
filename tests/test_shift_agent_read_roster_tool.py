"""Focused tests for the `get_roster_capabilities` plugin tool.

Scope mirrors tests/test_shift_agent_read_equipment_tool.py: the handler contract
and its authorization, with `gateway.session_context` stubbed so no gateway is
needed. Authorization is driven by monkeypatching `identity.resolve_identity`, so
the roles[] checks run on every host, Windows included. Tests that reach the
store need safe_io/fcntl and stay `linux_only`.

Three properties get more than a single case, because each is a way this tool
could be quietly wrong rather than loudly broken:

* NO IDENTITY FIELD ESCAPES. `roster.json` is the file `identify-sender` resolves
  callers against, so `phone` / `lid` / `phone_history` are an authorization
  surface, not just PII. Asserted over the whole serialized payload, through
  every filter route, with values distinctive enough to grep for.
* TERMINATED AND INACTIVE STAFF ARE UNREACHABLE. Asserted through every filter
  that would otherwise select them, because the wrong answer here — offering
  someone who no longer works there as cover — reaches a real person.
* `schedule` IS NEVER READ. A schedule seeded into the fixture must not appear in
  any result, and the tool must answer normally when the key is absent entirely.

The fixture roster is shaped after the live box: 8 employees, 6 active, 2
terminated, ISO language codes, overlapping `can_cover_roles`.
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
    reason="needs POSIX: loading the roster goes through safe_io, which uses fcntl",
)

OWNER = "19045550100@s.whatsapp.net"
EMPLOYEE = "19045550200@s.whatsapp.net"
DUAL_ROLE = "19045550300@s.whatsapp.net"

IDENTITIES = {
    OWNER: {"role": "owner", "roles": ["owner"]},
    EMPLOYEE: {"role": "employee", "roles": ["employee"]},
    DUAL_ROLE: {"role": "employee", "roles": ["employee", "owner"]},
}


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
    """A roster path that is deliberately absent to begin with.

    No config fixture: this tool reads no config at all, because the roster has
    no enable flag to read — see the module docstring in roster_tool.py.
    """
    monkeypatch.setenv("SHIFT_AGENT_ROSTER_PATH", str(tmp_path / "roster.json"))
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    return tmp_path


PKG = "shift_agent_read_pkg_roster"


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


def _pkg(principal=OWNER):
    _install_session_stub(principal)
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    return _load_package()


def _tool(monkeypatch=None, principal=OWNER):
    pkg = _pkg(principal)
    if monkeypatch is not None:
        monkeypatch.setattr(pkg.identity, "resolve_identity",
                            lambda p: IDENTITIES.get(p))
    return pkg.roster_tool


def _emp(eid, name, role, cover, langs, status="active", **kw):
    base = {"id": eid, "name": name, "role": role, "can_cover_roles": list(cover),
            "languages": list(langs), "status": status,
            "phone": f"+1904555{eid[1:]:0>4}"}
    base.update(kw)
    return base


# Shaped after the live box: 8 rows, 6 active, 2 terminated, ISO language codes.
ROSTER_EMPLOYEES = [
    _emp("e001", "Ravi Kumar", "cashier", ["cashier", "floor"], ["en", "te", "hi"]),
    _emp("e002", "Priya Reddy", "bakery", ["bakery", "sweets"], ["en", "te"]),
    _emp("e003", "Suresh Patel", "meat_counter", ["floor", "meat_counter"],
         ["en", "hi", "gu"]),
    _emp("e004", "Anjali Iyer", "cashier", ["bakery", "cashier", "sweets"],
         ["en", "ta"]),
    _emp("e005", "Vikram Sharma", "floor", ["cashier", "floor", "meat_counter"],
         ["en", "hi"], status="terminated"),
    _emp("e006", "Lakshmi", "sweets", ["bakery", "cashier", "sweets"], ["en", "te"],
         nickname="Lucky"),
    _emp("e007", "Test Cover", "floor", ["cashier", "floor"], ["en"],
         status="inactive"),
    _emp("e008", "Srini Bangaru", "floor", ["cashier", "floor"], ["en", "te"]),
]


def _seed(env, employees=None, schedule=..., location=None):
    doc = {"location": location if location is not None else {"id": "loc_t"},
           "employees": ROSTER_EMPLOYEES if employees is None else employees}
    if schedule is ...:
        schedule = {}
    if schedule is not None:
        doc["schedule"] = schedule
    (env / "roster.json").write_text(json.dumps(doc), encoding="utf-8")


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
    assert t.SCHEMA["name"] == "get_roster_capabilities"
    assert len(t.SCHEMA["description"]) > 80


def test_arguments_carry_no_identity_field(env):
    props = _tool().SCHEMA["parameters"]["properties"]
    assert set(props) == {"can_cover_role", "language", "name_contains"}
    forbidden = {"owner", "role", "phone", "user", "user_id", "sender", "identity"}
    assert not (set(props) & forbidden)
    assert _tool().SCHEMA["parameters"]["required"] == []


def test_there_is_no_argument_for_inactive_or_terminated_staff(env):
    """An argument that can surface someone who no longer works here is the one
    wrong answer this tool could give that reaches a real person."""
    blob = json.dumps(_tool().SCHEMA).lower()
    for banned in ("include_inactive", "include_terminated", "status",
                   "all_staff", "include_all"):
        assert banned not in blob


def test_schema_role_enum_matches_the_literal(env):
    """The enum has to be a literal list at registration time, before the
    platform modules are importable — so it is a copy, and a copy drifts."""
    from schemas import Role
    t = _tool()
    assert t.ROLES == list(get_args(Role))
    assert t.SCHEMA["parameters"]["properties"]["can_cover_role"]["enum"] == t.ROLES


def test_roster_path_default_matches_the_deployed_writer(env, monkeypatch):
    """One definition apart from `shift-agent-lid-learn`, not two."""
    monkeypatch.delenv("SHIFT_AGENT_ROSTER_PATH", raising=False)
    # Path equality, not string equality: on Windows the separator normalizes.
    assert _tool().ROSTER_PATH == Path("/opt/shift-agent/roster.json")
    writer = (REPO / "src" / "agents" / "shift" / "scripts"
              / "shift-agent-lid-learn").read_text(encoding="utf-8")
    assert 'SHIFT_AGENT_ROSTER_PATH", "/opt/shift-agent/roster.json"' in writer


def test_register_puts_the_tool_in_the_surviving_toolset(env):
    registered = []

    class FakeCtx:
        def register_tool(self, **kw):
            registered.append(kw)

    _pkg().register(FakeCtx())
    by_name = {r["name"]: r for r in registered}
    assert "get_roster_capabilities" in by_name
    captured = by_name["get_roster_capabilities"]
    assert captured["toolset"] == "shift_agent_read"
    assert callable(captured["handler"])
    assert captured["description"] == captured["schema"]["description"]
    assert {r["toolset"] for r in registered} == {"shift_agent_read"}
    assert len(by_name) == len(registered), "duplicate tool name registered"
    assert str(PLUGIN_DIR) not in sys.path


def test_plugin_manifest_lists_the_tool(env):
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert "get_roster_capabilities" in manifest["provides_tools"]


# ── authorization ──────────────────────────────────────────────────────────

@linux_only
def test_owner_succeeds(env, monkeypatch):
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is True and out["source_status"] == "populated"


def test_non_owner_refuses(env, monkeypatch):
    t = _tool(monkeypatch, EMPLOYEE)
    assert json.loads(t.handler({})) == {"ok": False, "refused": "not_owner"}


@linux_only
def test_non_owner_is_refused_even_with_a_populated_roster(env, monkeypatch):
    """Falsifiable: the refusal must not be an artefact of an empty store."""
    _seed(env)
    out = json.loads(_tool(monkeypatch, EMPLOYEE).handler({}))
    assert out == {"ok": False, "refused": "not_owner"}
    assert "Priya" not in json.dumps(out)


def test_dual_role_principal_is_allowed(env, monkeypatch):
    """THE B1 bug class: roles[] include owner, scalar `role` resolves employee."""
    t = _tool(monkeypatch, DUAL_ROLE)
    _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["ok"] is True, f"dual-role owner was refused: {out}"
    assert out["source_status"] == "missing"


def test_unbound_session_refuses(env, monkeypatch):
    t = _tool(monkeypatch, "")
    assert json.loads(t.handler({})) == {"ok": False, "refused": "unbound_session"}


def test_unresolved_identity_refuses(env, monkeypatch):
    t = _tool(monkeypatch, "19045559999@s.whatsapp.net")
    assert json.loads(t.handler({})) == {"ok": False, "refused": "identity_unresolved"}


def test_refusals_carry_no_counts(env, monkeypatch):
    """A refusal must never be readable as an authoritative 'nobody can'."""
    for principal in ("", EMPLOYEE):
        out = json.loads(_tool(monkeypatch, principal).handler({}))
        assert out["ok"] is False
        for absent in ("staff", "matched", "active_total", "roster_total"):
            assert absent not in out


def test_model_supplied_identity_arguments_cannot_grant_owner(env, monkeypatch):
    spoof = {"owner": True, "role": "owner", "phone": "+19045550100"}
    out = json.loads(_tool(monkeypatch, EMPLOYEE).handler(spoof))
    assert out == {"ok": False, "refused": "not_owner"}


# ── argument validation ────────────────────────────────────────────────────

@pytest.mark.parametrize("args", [
    {"can_cover_role": "barista"},      # not a Role
    {"can_cover_role": "Cashier"},      # the Literal is lowercase
    {"language": "x" * 21},
    {"name_contains": "x" * 81},
])
def test_out_of_schema_arguments_are_refused(env, monkeypatch, args):
    assert json.loads(_tool(monkeypatch).handler(args)) == {
        "ok": False, "refused": "invalid_arguments"}


@linux_only
def test_valid_boundary_arguments_are_accepted(env, monkeypatch):
    """The falsifier for the test above: the boundary values themselves work."""
    _seed(env)
    t = _tool(monkeypatch)
    _binder(monkeypatch, t)
    for args in ({"can_cover_role": "cashier"}, {"language": "x" * 20},
                 {"name_contains": "x" * 80}, {"can_cover_role": "manager"}):
        assert json.loads(t.handler(args))["ok"] is True, args


# ── the four states ────────────────────────────────────────────────────────

def test_missing_state_is_distinct(env, monkeypatch):
    assert not (env / "roster.json").exists()
    t = _tool(monkeypatch)
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "missing"
    assert out["coverage_status"] == "not_configured"
    # Coverage is UNKNOWN, not zero: no zero-shaped authoritative fields.
    for absent in ("roster_total", "active_total", "matched", "staff",
                   "languages_present"):
        assert absent not in out, f"{absent!r} must not appear on a missing source"
    assert b.calls == [t.TPL_MISSING]


@linux_only
def test_empty_state_is_distinct(env, monkeypatch):
    _seed(env, employees=[])
    t = _tool(monkeypatch)
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty"
    assert out["roster_total"] == 0 and out["active_total"] == 0
    assert out["staff"] == []
    assert b.calls == [t.TPL_EMPTY]


@linux_only
def test_no_active_staff_is_distinct_from_empty(env, monkeypatch):
    """THE state that replaces `disabled` here. "Everyone on file is terminated"
    is a different fact from "nobody is on file", and only one of them means the
    owner has no one to call."""
    _seed(env, employees=[
        _emp("e005", "Vikram Sharma", "floor", ["floor"], ["en"], status="terminated"),
        _emp("e007", "Test Cover", "floor", ["floor"], ["en"], status="inactive"),
    ])
    t = _tool(monkeypatch)
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "no_active_staff"
    assert out["roster_total"] == 2 and out["active_total"] == 0
    assert out["staff"] == []
    assert b.calls == [t.TPL_NO_ACTIVE.format(roster_total=2)]
    assert "2 people" in b.calls[0]
    assert "Vikram" not in json.dumps(out)


@linux_only
def test_populated_with_zero_matching_is_distinct(env, monkeypatch):
    """Active staff exist, none match. Not 'no staff', and not 'nobody can'."""
    _seed(env)
    t = _tool(monkeypatch)
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"can_cover_role": "dishwasher"}))
    assert out["source_status"] == "populated"
    assert out["active_total"] == 6 and out["matched"] == 0 and out["staff"] == []
    assert b.calls and "None of your 6 active staff" in b.calls[0]


@linux_only
def test_populated_positive_control_returns_real_people(env, monkeypatch):
    """THE anti-stub test: a handler that always refused, or always reported
    zero, would pass every negative case above and fail here."""
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({"can_cover_role": "meat_counter"}))
    assert out["ok"] is True and out["source_status"] == "populated"
    assert out["roster_total"] == 8 and out["active_total"] == 6
    # Suresh can cover it; Vikram also can but is terminated.
    assert [r["name"] for r in out["staff"]] == ["Suresh Patel"]
    assert out["matched"] == 1 and out["truncated"] is False
    assert out["staff"][0]["languages"] == ["en", "hi", "gu"]


# ── active-only, enforced through every route ──────────────────────────────

@linux_only
@pytest.mark.parametrize("args", [
    {},
    {"can_cover_role": "floor"},
    {"can_cover_role": "cashier"},
    {"can_cover_role": "meat_counter"},
    {"language": "en"},
    {"language": "hi"},
    {"name_contains": "vikram"},
    {"name_contains": "test"},
    {"name_contains": "a"},
])
def test_terminated_and_inactive_staff_are_unreachable(env, monkeypatch, args):
    """Vikram is terminated and Test Cover is inactive. Both would match several
    of these filters; neither may ever appear."""
    _seed(env)
    t = _tool(monkeypatch)
    _binder(monkeypatch, t)
    blob = json.dumps(json.loads(t.handler(args)))
    assert "Vikram" not in blob, args
    assert "Test Cover" not in blob, args
    assert "e005" not in blob and "e007" not in blob, args


@linux_only
def test_counts_distinguish_roster_size_from_active_size(env, monkeypatch):
    """The owner can see that 2 rows are withheld without the rows leaking."""
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["roster_total"] == 8 and out["active_total"] == 6
    assert len(out["staff"]) == 6


@linux_only
def test_derived_lists_are_built_from_active_staff_only(env, monkeypatch):
    """A value in these lists is a promise the owner can act on, so they are
    derived from the ACTIVE set. Nobody active covers `dishwasher`, and the two
    excluded rows contribute nothing."""
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({}))
    assert "dishwasher" not in out["cover_roles_present"]
    assert out["roles_present"] == ["bakery", "cashier", "floor", "meat_counter",
                                    "sweets"]
    assert out["cover_roles_present"] == ["bakery", "cashier", "floor",
                                          "meat_counter", "sweets"]
    assert out["languages_present"] == ["en", "gu", "hi", "ta", "te"]


# ── identity-surface fields never escape ───────────────────────────────────

@linux_only
@pytest.mark.parametrize("args", [
    {}, {"can_cover_role": "cashier"}, {"language": "te"},
    {"name_contains": "priya"}, {"can_cover_role": "dishwasher"},
])
def test_phone_lid_and_phone_history_never_appear(env, monkeypatch, args):
    """roster.json is the file identify-sender resolves callers against, so these
    are an authorization surface, not only PII."""
    _seed(env, employees=[
        _emp("e001", "Ravi Kumar", "cashier", ["cashier"], ["en", "te"],
             phone="+19045551111", lid="123456789012@lid",
             phone_history=[{"phone": "+19045559999",
                             "effective_from": "2026-01-01T00:00:00+00:00"}]),
    ])
    t = _tool(monkeypatch)
    _binder(monkeypatch, t)
    blob = json.dumps(json.loads(t.handler(args)))
    for banned in ("phone", "lid", "phone_history", "+19045551111",
                   "+19045559999", "123456789012"):
        assert banned not in blob, f"{banned!r} leaked for {args}"


@linux_only
def test_row_keys_are_exactly_the_contract(env, monkeypatch):
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({"name_contains": "priya"}))
    assert set(out) == {"ok", "source_status", "roster_total", "active_total",
                        "roles_present", "cover_roles_present",
                        "languages_present", "matched", "returned", "truncated",
                        "staff"}
    assert set(out["staff"][0]) == {"id", "name", "nickname", "role",
                                    "can_cover_roles", "languages", "restrictions"}


# ── the schedule is never read ─────────────────────────────────────────────

@linux_only
def test_a_seeded_schedule_never_reaches_the_result(env, monkeypatch):
    """The sole roster writer never populates `schedule`, so any schedule-derived
    answer would be permanently stale. Nothing here reads it."""
    _seed(env, schedule={"2026-04-25": [{"employee_id": "e001",
                                         "start": "09:00", "end": "17:00",
                                         "role": "cashier"}]})
    blob = json.dumps(json.loads(_tool(monkeypatch).handler({})))
    assert "schedule" not in blob
    assert "2026-04-25" not in blob and "09:00" not in blob


@linux_only
def test_a_roster_with_no_schedule_key_answers_normally(env, monkeypatch):
    """`schedule` defaults to {}; its absence must not degrade the answer."""
    _seed(env, schedule=None)
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["source_status"] == "populated" and out["active_total"] == 6


def test_description_declines_schedule_questions(env):
    d = _tool().DESCRIPTION
    assert "does NOT know the schedule" in d
    assert "Never answer a shift, rota, date or availability question" in d


# ── filtering ──────────────────────────────────────────────────────────────

@linux_only
@pytest.mark.parametrize("args,expected", [
    ({"can_cover_role": "sweets"}, {"Priya Reddy", "Anjali Iyer", "Lakshmi"}),
    ({"can_cover_role": "bakery"}, {"Priya Reddy", "Anjali Iyer", "Lakshmi"}),
    ({"language": "te"}, {"Ravi Kumar", "Priya Reddy", "Lakshmi", "Srini Bangaru"}),
    ({"language": "TE"}, {"Ravi Kumar", "Priya Reddy", "Lakshmi", "Srini Bangaru"}),
    ({"language": "ta"}, {"Anjali Iyer"}),
    ({"language": "gu"}, {"Suresh Patel"}),
    ({"name_contains": "priya"}, {"Priya Reddy"}),
    ({"name_contains": "REDDY"}, {"Priya Reddy"}),
    ({"can_cover_role": "cashier", "language": "te"},
     {"Ravi Kumar", "Lakshmi", "Srini Bangaru"}),
    ({"can_cover_role": "sweets", "language": "ta"}, {"Anjali Iyer"}),
])
def test_filters_are_conjunctive_and_case_insensitive(env, monkeypatch, args, expected):
    _seed(env)
    t = _tool(monkeypatch)
    _binder(monkeypatch, t)
    out = json.loads(t.handler(args))
    assert {r["name"] for r in out["staff"]} == expected
    assert out["matched"] == len(expected)


@linux_only
def test_nickname_is_matched_and_returned(env, monkeypatch):
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({"name_contains": "lucky"}))
    assert [r["name"] for r in out["staff"]] == ["Lakshmi"]
    assert out["staff"][0]["nickname"] == "Lucky"


@linux_only
def test_results_are_sorted_by_name(env, monkeypatch):
    _seed(env)
    out = json.loads(_tool(monkeypatch).handler({}))
    names = [r["name"] for r in out["staff"]]
    assert names == sorted(names, key=str.casefold)


@linux_only
def test_restrictions_are_surfaced_not_withheld(env, monkeypatch):
    """A suggestion that ignores a restriction is worse than one that names it,
    and the owner wrote these."""
    _seed(env, employees=[
        _emp("e001", "Ravi Kumar", "cashier", ["cashier", "meat_counter"],
             ["en"], restrictions={"no_meat_handling": True}),
    ])
    out = json.loads(_tool(monkeypatch).handler({"can_cover_role": "meat_counter"}))
    assert out["staff"][0]["restrictions"] == {"no_meat_handling": True}


@linux_only
def test_rows_are_capped_but_counts_stay_exact(env, monkeypatch):
    t = _tool(monkeypatch)
    _seed(env, employees=[
        _emp(f"e{i:03d}", f"Person {i:03d}", "floor", ["floor"], ["en"])
        for i in range(1, t.MAX_ROWS + 5)
    ])
    out = json.loads(t.handler({}))
    assert out["active_total"] == t.MAX_ROWS + 4
    assert out["matched"] == t.MAX_ROWS + 4
    assert out["returned"] == t.MAX_ROWS and len(out["staff"]) == t.MAX_ROWS
    assert out["truncated"] is True


@linux_only
def test_handler_returns_json_text_not_a_dict(env, monkeypatch):
    _seed(env)
    res = _tool(monkeypatch).handler({})
    assert isinstance(res, str)
    assert isinstance(json.loads(res), dict)


# ── fail closed ────────────────────────────────────────────────────────────

@linux_only
def test_unreadable_roster_fails_closed(env, monkeypatch):
    """A corrupt/schema-invalid roster is NOT an empty roster."""
    (env / "roster.json").write_text("{not json", encoding="utf-8")
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "state_unreadable"
    for absent in ("staff", "roster_total", "active_total", "matched",
                   "source_status"):
        assert absent not in out, f"{absent!r} must not appear in a failure result"


@linux_only
def test_schema_invalid_roster_fails_closed(env, monkeypatch):
    """`Employee` is extra="forbid" and `id` is pattern-bound; a hand-edit that
    breaks either must not read as 'no staff'."""
    _seed(env, employees=[{"id": "not-an-id", "name": "X", "role": "cashier",
                           "phone": "+19045551111"}])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False and out["error"] == "state_unreadable"


# ── turn-bound outbound binding (fail-closed) ──────────────────────────────

@linux_only
def test_positive_rows_bind_nothing(env, monkeypatch):
    """Hermes keeps presentation ownership when there are real people to name."""
    _seed(env)
    t = _tool(monkeypatch)
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"can_cover_role": "meat_counter"}))
    assert out["matched"] == 1
    assert b.calls == []


@linux_only
def test_no_match_reply_names_what_is_available(env, monkeypatch):
    """`languages` holds ISO codes, so 'telugu' finds nobody on a roster where
    three people speak it. The bound reply has to make that recoverable."""
    _seed(env)
    t = _tool(monkeypatch)
    b = _binder(monkeypatch, t)
    out = json.loads(t.handler({"language": "telugu"}))
    assert out["matched"] == 0
    assert "te" in out["languages_present"]
    assert "te" in b.calls[0] and "meat_counter" in b.calls[0]


@linux_only
@pytest.mark.parametrize("state", ["missing", "empty", "no_active", "no_match"])
def test_bind_failure_suppresses_every_zero_payload(env, monkeypatch, state):
    """THE load-bearing rule: no zero evidence without a bound qualification."""
    args = {}
    if state == "empty":
        _seed(env, employees=[])
    elif state == "no_active":
        _seed(env, employees=[_emp("e005", "Vikram Sharma", "floor", ["floor"],
                                   ["en"], status="terminated")])
    elif state == "no_match":
        _seed(env)
        args = {"can_cover_role": "dishwasher"}
    t = _tool(monkeypatch)
    _binder(monkeypatch, t, succeed=False)
    out = json.loads(t.handler(args))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    for absent in ("roster_total", "active_total", "matched", "staff",
                   "source_status", "coverage_status", "languages_present"):
        assert absent not in out, f"{absent!r} leaked on a guard-unavailable refusal"


# ── description pins ───────────────────────────────────────────────────────

def test_description_carries_the_scope_rules(env):
    d = _tool().DESCRIPTION
    assert "RECORDED" in d
    assert "does NOT establish that the business has no staff" in d
    assert "NOT that nobody works here" in d
    assert "it is NOT the same as an empty roster" in d
    assert "check roles_present, cover_roles_present and languages_present" in d


def test_description_carries_the_hard_rules(env):
    d = _tool().DESCRIPTION
    assert "never invent an employee, a capability or a language" in d
    assert "Only ACTIVE staff are ever returned" in d
    assert "Always respect a returned `restrictions` value" in d
    assert "Never read out or ask for a phone number" in d
    assert "READ-ONLY" in d and "cannot message anyone" in d
