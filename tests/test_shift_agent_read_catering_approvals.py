"""Focused tests for the `get_pending_catering_approvals` plugin tool.

Scope mirrors tests/test_shift_agent_read_equipment_tool.py: the handler contract
and its authorization, with `gateway.session_context` stubbed so no gateway is
needed. The Hermes bridge itself was proven against the pinned 0.19.1 runtime
before the first of these tools was written and is not recreated here.

Authorization is driven by monkeypatching `identity.resolve_identity`, so the
roles[] checks — including the dual-role principal — run on every host, Windows
included. Tests that reach the store need safe_io/fcntl and stay `linux_only`;
the pending-status derivation reads `schemas` only, so it does not.

Two properties get more than a single case each, because both are ways this tool
could be quietly wrong rather than loudly broken:

* The PENDING SET is generated from `CateringLeadStatus`, one lead per status, so
  a status added to the schema without thought here shows up as a diff in the
  matrix rather than as a lead the owner is never told about.
* `customer_phone` absence is asserted over the whole serialized payload for
  every status, not just on the happy path.
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

OWNER = "19045550100@s.whatsapp.net"
EMPLOYEE = "19045550200@s.whatsapp.net"
DUAL_ROLE = "19045550300@s.whatsapp.net"

# What identify-sender returns per principal. DUAL_ROLE is the B1 bug class: a
# genuine owner who is also on the roster resolves scalar `employee` by LID, so
# a scalar check refuses them while roles[] does not.
IDENTITIES = {
    OWNER: {"role": "owner", "roles": ["owner"]},
    EMPLOYEE: {"role": "employee", "roles": ["employee"]},
    DUAL_ROLE: {"role": "employee", "roles": ["employee", "owner"]},
}

# The two statuses live production actually parks decisions in, and the exact set
# the derivation must produce. Written out longhand so the test suite states the
# expected answer rather than recomputing the implementation's own expression.
EXPECTED_PENDING = {"AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED"}

NOW = "2026-08-18T09:00:00-04:00"


def _install_session_stub(principal: str) -> None:
    """Stand in for the gateway ContextVar with an explicit, exact value."""
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
    """Config + a store path that is deliberately absent to begin with."""
    (tmp_path / "state").mkdir()
    _write_cfg(tmp_path)
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("SHIFT_AGENT_CATERING_LEADS_PATH",
                       str(tmp_path / "state" / "catering-leads.json"))
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", NOW)
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    return tmp_path


# A dedicated alias so this file's package copy — and the identity module it
# monkeypatches — never collides with a sibling test file's copy.
PKG = "shift_agent_read_pkg_catering_approvals"


def _load_package():
    """Import the plugin as a PACKAGE so its relative imports resolve.

    The shipped directory is `shift-agent-read`, which is not a legal module
    name, so it is aliased. Hermes' own loader does the equivalent; the point of
    doing it here is that `from .identity import ...` must work exactly as it
    will at runtime — no sys.path insertion of the plugin directory.
    """
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
    """(Re)load the plugin package with a session stub already in place."""
    _install_session_stub(principal)
    for name in list(sys.modules):
        if name == PKG or name.startswith(PKG + "."):
            del sys.modules[name]
    return _load_package()


def _tool(monkeypatch=None, principal=OWNER):
    """The approvals tool, with identify-sender resolution stubbed in-process."""
    pkg = _pkg(principal)
    if monkeypatch is not None:
        monkeypatch.setattr(pkg.identity, "resolve_identity",
                            lambda p: IDENTITIES.get(p))
    return pkg.catering_approvals_tool


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


def _seed(env, leads):
    (env / "state" / "catering-leads.json").write_text(
        json.dumps({"schema_version": 1, "leads": leads}), encoding="utf-8")


def _lead(lead_id, status="AWAITING_OWNER_APPROVAL", created="2026-06-05T10:00:00-04:00",
          **kw):
    """A schema-valid lead. `quote_text` is always supplied: post-AWAITING
    statuses require it, and letting the legacy sentinel shim fire would put a
    WARN on stderr for every fixture."""
    base = {
        "lead_id": lead_id,
        "status": status,
        "customer_phone": "+19045551234",
        "raw_inquiry": "catering for 60 people, biryani please",
        "original_message_id": f"msg-{lead_id}",
        "created_at": created,
        "updated_at": created,
        "quote_text": "Quote: 60 guests, $1,200.",
        "owner_approval_code": "#4SX94",
    }
    base.update(kw)
    return base


class _Binder:
    """Records bind attempts; can be made to fail."""

    def __init__(self, succeed=True):
        self.succeed = succeed
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return self.succeed


def _unbound(monkeypatch, tool, succeed=True):
    """Replace the real turn binder so store-free paths run off-Linux too."""
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
    assert t.SCHEMA["name"] == "get_pending_catering_approvals"
    assert len(t.SCHEMA["description"]) > 80
    assert "compliance" in t.SCHEMA["description"].lower(), (
        "description should distinguish this capability from the compliance calendar"
    )


def test_the_tool_takes_no_arguments_at_all(env):
    """No parameter is worth letting a model narrow 'what is waiting on me?'."""
    params = _tool().SCHEMA["parameters"]
    assert params["properties"] == {}
    assert params["required"] == []


def test_register_puts_the_tool_in_the_surviving_toolset(env):
    """`agent.disabled_toolsets` suppresses `skills` and `terminal` by name on
    the live gateway; `shift_agent_read` is what makes this path reachable."""
    registered = []

    class FakeCtx:
        def register_tool(self, **kw):
            registered.append(kw)

    _pkg().register(FakeCtx())
    by_name = {r["name"]: r for r in registered}
    assert "get_pending_catering_approvals" in by_name
    captured = by_name["get_pending_catering_approvals"]
    assert captured["toolset"] == "shift_agent_read"
    assert callable(captured["handler"])
    assert captured["schema"]["name"] == "get_pending_catering_approvals"
    assert captured["description"] == captured["schema"]["description"]
    assert {r["toolset"] for r in registered} == {"shift_agent_read"}
    assert len(by_name) == len(registered), "duplicate tool name registered"
    assert str(PLUGIN_DIR) not in sys.path, (
        "importing the plugin must not put its own directory on sys.path"
    )


def test_plugin_manifest_lists_the_tool(env):
    """`provides_tools` is what the operator reads; a tool missing from it is a
    tool nobody knows shipped."""
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    assert "get_pending_catering_approvals" in manifest["provides_tools"]


# ── authorization ──────────────────────────────────────────────────────────

@linux_only
def test_owner_succeeds(env, monkeypatch):
    _seed(env, [_lead("L0001")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is True and out["source_status"] == "populated"


def test_non_owner_refuses(env, monkeypatch):
    t = _tool(monkeypatch, EMPLOYEE)
    assert json.loads(t.handler({})) == {"ok": False, "refused": "not_owner"}


@linux_only
def test_non_owner_is_refused_even_with_pending_leads_on_disk(env, monkeypatch):
    """Falsifiable: the refusal must not be an artefact of an empty store."""
    _seed(env, [_lead("L0001")])
    out = json.loads(_tool(monkeypatch, EMPLOYEE).handler({}))
    assert out == {"ok": False, "refused": "not_owner"}
    assert "L0001" not in json.dumps(out)


def test_dual_role_principal_is_allowed(env, monkeypatch):
    """THE B1 bug class: a principal whose roles[] include owner but whose
    scalar `role` resolves `employee` is a genuine owner and must be served."""
    t = _tool(monkeypatch, DUAL_ROLE)
    _unbound(monkeypatch, t)
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
    """A refusal must never be readable as an authoritative 'nothing pending'."""
    for principal in ("", EMPLOYEE):
        out = json.loads(_tool(monkeypatch, principal).handler({}))
        assert out["ok"] is False
        for absent in ("leads", "pending_total", "leads_total"):
            assert absent not in out


def test_model_supplied_identity_arguments_cannot_grant_owner(env, monkeypatch):
    spoof = {"owner": True, "role": "owner", "phone": "+19045550100"}
    out = json.loads(_tool(monkeypatch, EMPLOYEE).handler(spoof))
    assert out == {"ok": False, "refused": "not_owner"}


# ── the pending-status derivation ──────────────────────────────────────────

def test_pending_statuses_are_derived_from_the_deployed_transition_table(env):
    """Not a hardcoded literal: the set is every status that can reach
    OWNER_APPROVED. Today that is AWAITING_OWNER_APPROVAL + CUSTOMER_FINALIZED."""
    assert _tool().pending_statuses() == EXPECTED_PENDING


def test_pending_statuses_track_the_schema_not_a_copy(env):
    """Recomputed straight from schemas: if the state machine gains a status that
    can reach OWNER_APPROVED, this test fails and someone has to look."""
    from schemas import CATERING_TRANSITIONS
    derived = {s for s, allowed in CATERING_TRANSITIONS.items()
               if "OWNER_APPROVED" in allowed}
    assert derived == EXPECTED_PENDING
    assert _tool().pending_statuses() == derived


def test_pending_statuses_are_all_real_statuses(env):
    from schemas import CateringLeadStatus
    assert _tool().pending_statuses() <= set(get_args(CateringLeadStatus))


@linux_only
def test_unreadable_state_machine_fails_closed(env, monkeypatch):
    """Guessing a narrower literal would silently under-report open money
    decisions, so an unreadable table is a failure, not a fallback."""
    t = _tool(monkeypatch)
    monkeypatch.setattr(t, "pending_statuses", lambda: None)
    _seed(env, [_lead("L0001")])
    out = json.loads(t.handler({}))
    assert out["ok"] is False and out["error"] == "state_machine_unavailable"
    for absent in ("leads", "pending_total", "leads_total", "source_status"):
        assert absent not in out


# ── the config enable gate ─────────────────────────────────────────────────
#
# cfg.catering.enabled defaults False ("default OFF — opt-in" in CateringConfig),
# and an absent block validates to that default. An un-onboarded business must
# hear that catering is off, never "nothing waiting on you".


def test_disabled_agent_reports_disabled_not_nothing_pending(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    _seed(env, [_lead("L0001")])   # would be pending
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert out["coverage_status"] == "not_enabled"
    assert b.calls == [t.TPL_DISABLED]
    for absent in ("leads_total", "pending_total", "leads", "returned", "truncated"):
        assert absent not in out, f"{absent!r} must not appear on a disabled agent"
    assert "L0001" not in json.dumps(out)


def test_disabled_agent_does_not_read_the_store(env, monkeypatch):
    """Falsifiable: the store is corrupt, so any read would surface
    state_unreadable. Getting `disabled` proves the gate returned first."""
    _write_cfg(env, {"enabled": False})
    (env / "state" / "catering-leads.json").write_text("{not json", encoding="utf-8")
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t)
    assert json.loads(t.handler({}))["source_status"] == "disabled"


def test_absent_config_block_is_treated_as_disabled(env, monkeypatch):
    _write_cfg(env, None)
    _seed(env, [_lead("L0001")])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "disabled"
    assert b.calls == [t.TPL_DISABLED]


def test_bind_failure_suppresses_the_disabled_payload(env, monkeypatch):
    _write_cfg(env, {"enabled": False})
    # Seeded pending lead: without the gate the handler would reach the populated
    # path and return ok=True, so this test fails if the gate is gone.
    _seed(env, [_lead("L0001")])
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t, succeed=False)
    out = json.loads(t.handler({}))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    assert "source_status" not in out and "coverage_status" not in out


def test_unreadable_config_fails_closed_rather_than_claiming_disabled(env, monkeypatch):
    """"Not enabled" is a claim about configuration. If the config could not be
    read, that claim is unproven — and so is any answer from the store."""
    (env / "config.yaml").write_text("customer: [unclosed\n", encoding="utf-8")
    _seed(env, [_lead("L0001")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "config_unavailable"
    for absent in ("leads", "leads_total", "pending_total", "source_status"):
        assert absent not in out


# ── the four states ────────────────────────────────────────────────────────

def test_missing_state_is_distinct(env, monkeypatch):
    assert not (env / "state" / "catering-leads.json").exists()
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "missing"
    assert out["coverage_status"] == "not_configured"
    # Coverage is UNKNOWN, not zero: no zero-shaped authoritative fields.
    for absent in ("leads_total", "pending_total", "leads", "returned", "truncated"):
        assert absent not in out, f"{absent!r} must not appear on a missing source"


@linux_only
def test_empty_state_is_distinct(env, monkeypatch):
    _seed(env, [])
    t = _tool(monkeypatch)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty"
    assert out["leads_total"] == 0 and out["pending_total"] == 0
    assert out["leads"] == []


@linux_only
def test_populated_with_zero_pending_is_not_empty(env, monkeypatch):
    """THE distinction: leads exist, none is the owner's to decide. Not 'no
    leads', and definitely not 'no customers'."""
    _seed(env, [_lead("L0001", status="OWNER_REJECTED"),
                _lead("L0002", status="CLOSED"),
                _lead("L0003", status="SENT_TO_CUSTOMER")])
    t = _tool(monkeypatch)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "populated"
    assert out["leads_total"] == 3 and out["pending_total"] == 0
    assert out["leads"] == []


@linux_only
def test_populated_positive_control_returns_real_rows(env, monkeypatch):
    """THE anti-stub test: a handler that always refused, or always reported
    zero, would pass every negative case above and fail here."""
    _seed(env, [_lead("L0017", created="2026-06-09T19:49:15-04:00",
                      owner_approval_code="#4SX94"),
                _lead("L0020", status="CUSTOMER_FINALIZED",
                      created="2026-07-25T21:21:45-04:00",
                      owner_approval_code="#KYHWU", quote_total_usd=76),
                _lead("L0002", status="CLOSED")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is True and out["source_status"] == "populated"
    assert out["leads_total"] == 3 and out["pending_total"] == 2
    assert out["returned"] == 2 and out["truncated"] is False
    codes = {r["lead_id"]: r["owner_approval_code"] for r in out["leads"]}
    assert codes == {"L0017": "#4SX94", "L0020": "#KYHWU"}
    totals = {r["lead_id"]: r["quote_total_usd"] for r in out["leads"]}
    assert totals == {"L0017": None, "L0020": 76}


# ── which leads count as pending ───────────────────────────────────────────

@linux_only
def test_every_status_is_classified_exactly_once(env, monkeypatch):
    """Generated matrix over the whole `CateringLeadStatus` Literal: one lead per
    status, and exactly the derived pending set comes back. A status added to the
    schema shows up here rather than as a lead nobody is told about."""
    from schemas import CateringLeadStatus
    statuses = list(get_args(CateringLeadStatus))
    _seed(env, [_lead(f"L{i:04d}", status=s) for i, s in enumerate(statuses)])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["leads_total"] == len(statuses)
    returned = {r["status"] for r in out["leads"]}
    assert returned == EXPECTED_PENDING
    assert out["pending_total"] == len(EXPECTED_PENDING)


@linux_only
def test_customer_finalized_leads_are_reported(env, monkeypatch):
    """The under-report guard. A customer who has locked in their selection is
    waiting on the owner; live production had 2 such leads against 3 obvious
    ones, so dropping them would have under-reported by 40%."""
    _seed(env, [_lead("L0016", status="CUSTOMER_FINALIZED",
                      owner_approval_code="#GWXSR")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["pending_total"] == 1
    assert out["leads"][0]["status"] == "CUSTOMER_FINALIZED"


@linux_only
def test_owner_edited_is_not_pending_on_the_owner(env, monkeypatch):
    """OWNER_EDITED cannot reach OWNER_APPROVED — the redraft is pending on the
    system, not on the owner. Pins the derivation's near-miss."""
    _seed(env, [_lead("L0001", status="OWNER_EDITED")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["pending_total"] == 0


# ── result contract ────────────────────────────────────────────────────────

@linux_only
def test_customer_phone_is_never_emitted(env, monkeypatch):
    """Asserted over the whole serialized payload, for every status, not just on
    the happy path."""
    from schemas import CateringLeadStatus
    statuses = list(get_args(CateringLeadStatus))
    _seed(env, [_lead(f"L{i:04d}", status=s,
                      customer_phone=f"+1904555{1000 + i:04d}")
                for i, s in enumerate(statuses)])
    blob = json.dumps(json.loads(_tool(monkeypatch).handler({})))
    assert "customer_phone" not in blob
    for i in range(len(statuses)):
        assert f"+1904555{1000 + i:04d}" not in blob


@linux_only
def test_raw_inquiry_and_quote_text_are_withheld(env, monkeypatch):
    """The question is which decisions are outstanding. The customer's message
    body and the drafted quote prose are not part of that answer."""
    _seed(env, [_lead("L0001", raw_inquiry="SECRETINQUIRY",
                      quote_text="SECRETQUOTE")])
    blob = json.dumps(json.loads(_tool(monkeypatch).handler({})))
    assert "SECRETINQUIRY" not in blob and "SECRETQUOTE" not in blob


@linux_only
def test_row_keys_are_exactly_the_contract(env, monkeypatch):
    _seed(env, [_lead("L0001")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert set(out) == {"ok", "source_status", "leads_total", "pending_total",
                        "returned", "truncated", "leads"}
    assert set(out["leads"][0]) == {
        "lead_id", "status", "owner_approval_code", "created_at", "updated_at",
        "age_days", "days_since_update", "quote_total_usd", "customer_name",
        "deposit_status", "on_hold"}


@linux_only
def test_oldest_lead_leads_the_list_with_its_age(env, monkeypatch):
    """"Oldest first" is the owner's triage order, and `age_days` is the number
    they act on. NOW is pinned at 2026-08-18."""
    _seed(env, [_lead("recent", created="2026-08-11T09:00:00-04:00"),
                _lead("ancient", created="2026-06-05T09:00:00-04:00"),
                _lead("middle", created="2026-07-19T09:00:00-04:00")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert [r["lead_id"] for r in out["leads"]] == ["ancient", "middle", "recent"]
    assert out["leads"][0]["age_days"] == 74
    assert out["leads"][-1]["age_days"] == 7


@linux_only
def test_days_since_update_is_separate_from_age(env, monkeypatch):
    _seed(env, [_lead("L0001", created="2026-06-05T09:00:00-04:00",
                      updated_at="2026-08-15T09:00:00-04:00")])
    row = json.loads(_tool(monkeypatch).handler({}))["leads"][0]
    assert row["age_days"] == 74 and row["days_since_update"] == 3


@linux_only
def test_a_lead_without_a_code_is_still_reported(env, monkeypatch):
    """It is still the owner's decision; it just cannot be closed by code. The
    description tells Hermes to say so rather than invent one."""
    _seed(env, [_lead("L0001", owner_approval_code=None)])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["pending_total"] == 1
    assert out["leads"][0]["owner_approval_code"] is None


@linux_only
def test_rows_are_capped_but_counts_stay_exact(env, monkeypatch):
    """A cap that changed the counts would answer 'how many are waiting on me?'
    wrongly, which is the one number the owner acts on."""
    t = _tool(monkeypatch)
    _seed(env, [_lead(f"L{i:04d}", created=f"2026-0{1 + i % 6}-05T09:00:00-04:00")
                for i in range(t.MAX_ROWS + 4)])
    out = json.loads(t.handler({}))
    assert out["pending_total"] == t.MAX_ROWS + 4
    assert out["returned"] == t.MAX_ROWS and len(out["leads"]) == t.MAX_ROWS
    assert out["truncated"] is True


@linux_only
def test_handler_returns_json_text_not_a_dict(env, monkeypatch):
    _seed(env, [_lead("L0001")])
    res = _tool(monkeypatch).handler({})
    assert isinstance(res, str)
    assert isinstance(json.loads(res), dict)


@linux_only
def test_result_contains_no_prose(env, monkeypatch):
    """The handler owns facts; Hermes owns wording."""
    _seed(env, [_lead("L0001")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert not any(isinstance(v, str) and " " in v
                   for v in out.values() if not isinstance(v, (list, dict)))


# ── fail closed: never an authoritative empty ──────────────────────────────

@linux_only
def test_unreadable_state_fails_closed(env, monkeypatch):
    """A corrupt/schema-invalid store is NOT an authoritative empty result."""
    (env / "state" / "catering-leads.json").write_text(
        json.dumps({"schema_version": 1, "leads": [{"lead_id": "no_status"}]}),
        encoding="utf-8")
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "state_unreadable"
    for absent in ("leads", "leads_total", "pending_total", "source_status"):
        assert absent not in out, f"{absent!r} must not appear in a failure result"


@linux_only
def test_unestablishable_today_fails_closed(env, monkeypatch):
    """No silent fallback: age_days must never come from a guessed date."""
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "not-a-timestamp")
    _seed(env, [_lead("L0001")])
    out = json.loads(_tool(monkeypatch).handler({}))
    assert out["ok"] is False
    assert out["error"] == "customer_timezone_unavailable"
    for absent in ("leads", "leads_total", "pending_total", "source_status"):
        assert absent not in out


# ── turn-bound outbound binding (fail-closed) ──────────────────────────────
#
# Every zero state must bind its deterministic reply BEFORE returning the
# authoritative payload. "No catering leads are waiting on you", said to an owner
# with three open quotes, is how a booking is lost.


def test_missing_binds_exact_template_before_returning(env, monkeypatch):
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["ok"] is True and out["source_status"] == "missing"
    assert b.calls == [t.TPL_MISSING]


@linux_only
def test_empty_binds_exact_template(env, monkeypatch):
    _seed(env, [])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["source_status"] == "empty"
    assert b.calls == [t.TPL_EMPTY]


@linux_only
def test_populated_zero_binds_template_with_the_actual_total(env, monkeypatch):
    """The bound sentence has to prove leads exist, or it reads like 'empty'."""
    _seed(env, [_lead("L0001", status="CLOSED"),
                _lead("L0002", status="OWNER_REJECTED")])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["pending_total"] == 0 and out["leads_total"] == 2
    assert b.calls == [t.TPL_POPULATED_ZERO.format(leads_total=2)]
    assert "2 catering leads" in b.calls[0]


@linux_only
def test_positive_rows_bind_nothing(env, monkeypatch):
    """Hermes keeps presentation ownership when there is something real to say."""
    _seed(env, [_lead("L0001")])
    t = _tool(monkeypatch)
    b = _unbound(monkeypatch, t)
    out = json.loads(t.handler({}))
    assert out["pending_total"] == 1
    assert b.calls == []


@linux_only
@pytest.mark.parametrize("state", ["missing", "empty", "populated_zero"])
def test_bind_failure_suppresses_every_zero_payload(env, monkeypatch, state):
    """THE load-bearing rule: no zero evidence without a bound qualification."""
    if state == "empty":
        _seed(env, [])
    elif state == "populated_zero":
        _seed(env, [_lead("L0001", status="CLOSED")])
    t = _tool(monkeypatch)
    _unbound(monkeypatch, t, succeed=False)
    out = json.loads(t.handler({}))
    assert out["ok"] is False
    assert out["refused"] == "outbound_truthfulness_guard_unavailable"
    for absent in ("leads_total", "pending_total", "leads", "returned",
                   "truncated", "source_status", "coverage_status"):
        assert absent not in out, f"{absent!r} leaked on a guard-unavailable refusal"


# ── description pins ───────────────────────────────────────────────────────

def test_description_carries_the_scope_rules(env):
    """These sentences are what keep a recorded-store read from being reported as
    a statement about the business; an edit must not drop them silently."""
    d = _tool().DESCRIPTION
    assert "RECORDED" in d
    assert "This is NOT a statement that nothing is waiting" in d
    assert "does NOT establish that no decisions are outstanding" in d
    assert "NOT that no customer has enquired" in d
    assert "Do not generalize beyond the recorded store" in d


def test_description_carries_the_hard_rules(env):
    d = _tool().DESCRIPTION
    assert "never invent a lead, an approval code or a quote total" in d
    assert "#XXXXX approve" in d, "the code is the actionable half of the F8 loop"
    assert "NOT a $0 quote" in d
    assert "READ-ONLY" in d
    assert "Never read out a customer's phone number" in d
