"""Tests for the deterministic Shift sick-call CLI.

The script is extensionless because deploy installs it as /usr/local/bin. Use
SourceFileLoader explicitly, matching the repo pattern for script tests.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "handle-shift-sick-call"
PLATFORM = REPO / "src" / "platform"


def _load_script():
    sys.path.insert(0, str(PLATFORM))
    loader = importlib.machinery.SourceFileLoader("handle_shift_sick_call_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def env_dir(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test Store",
            "location_id": "loc_test",
            "timezone": "America/New_York",
        },
        "owner": {
            "name": "Owner",
            "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (tmp_path / "roster.json").write_text(json.dumps({
        "location": {"id": "loc_test"},
        "employees": [{
            "id": "e008",
            "name": "Srini Bangaru",
            "role": "floor",
            "phone": "+17329837841",
            "languages": ["en"],
            "can_cover_roles": ["floor", "cashier"],
            "status": "active",
            "phone_history": [],
            "restrictions": None,
            "lid": "201975216009469@lid",
        }],
        "schedule": {},
    }), encoding="utf-8")
    (state / "pending.json").write_text(json.dumps({"next_proposal_seq": 1, "proposals": {}}))
    return tmp_path


def test_employee_sick_call_without_schedule_acknowledges_and_alerts_owner(env_dir, monkeypatch):
    mod = _load_script()
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.ROSTER_PATH = env_dir / "roster.json"
    mod.PENDING_PATH = env_dir / "state" / "pending.json"

    sent = []
    monkeypatch.setattr(
        mod,
        "_identify_sender",
        lambda chat_id: {
            "role": "employee",
            "employee_id": "e008",
            "name": "Srini Bangaru",
            "phone_normalized": "+17329837841",
            "lid": "201975216009469@lid",
        },
    )
    monkeypatch.setattr(
        mod,
        "_send_text",
        lambda jid, message, action_id: sent.append((jid, message, action_id)) or (True, "mid", ""),
    )
    monkeypatch.setattr(mod, "_notify_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "_create_proposal", lambda **_kwargs: pytest.fail("no schedule should not create proposal"))

    rc = mod.process_absence(
        chat_id="201975216009469@lid",
        text="Hey Boss! I am down with fever, I can't come for shift today.",
        message_id="wa-live-1414",
    )

    assert rc == 0
    assert sent[0][0] == "201975216009469@lid"
    assert "no scheduled shift" in sent[0][1].lower()
    assert sent[1][0] == "19045550100@s.whatsapp.net"
    assert "reported an absence" in sent[1][1].lower()
    assert "no scheduled shift" in sent[1][1].lower()


def test_employee_sick_call_with_schedule_creates_owner_proposal(env_dir, monkeypatch):
    mod = _load_script()
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.ROSTER_PATH = env_dir / "roster.json"
    mod.PENDING_PATH = env_dir / "state" / "pending.json"
    roster = {
        "location": {"id": "loc_test"},
        "employees": [
            {
                "id": "e008",
                "name": "Srini Bangaru",
                "role": "floor",
                "phone": "+17329837841",
                "languages": ["en", "te"],
                "can_cover_roles": ["floor", "cashier"],
                "status": "active",
                "phone_history": [],
                "restrictions": None,
                "lid": "201975216009469@lid",
            },
            {
                "id": "e009",
                "name": "Anjali Rao",
                "role": "floor",
                "phone": "+19045550109",
                "languages": ["en", "te"],
                "can_cover_roles": ["floor"],
                "status": "active",
                "phone_history": [],
                "restrictions": None,
                "lid": None,
            },
        ],
        "schedule": {
            "2026-06-07": [
                {"employee_id": "e008", "shift": "10:00-18:00", "role": "floor"},
            ],
        },
    }
    (env_dir / "roster.json").write_text(json.dumps(roster), encoding="utf-8")

    sent = []
    create_calls = []
    monkeypatch.setattr(
        mod,
        "_identify_sender",
        lambda chat_id: {
            "role": "employee",
            "employee_id": "e008",
            "name": "Srini Bangaru",
            "phone_normalized": "+17329837841",
            "lid": "201975216009469@lid",
        },
    )
    monkeypatch.setattr(
        mod,
        "_send_text",
        lambda jid, message, action_id: sent.append((jid, message, action_id)) or (True, "mid", ""),
    )
    monkeypatch.setattr(mod, "_notify_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mod,
        "_render_template",
        lambda template, fields: (
            True,
            (
                f"candidate msg for {fields['candidate_name']}"
                if template == "coverage_message_to_candidate"
                else f"owner proposal {fields['candidate_name']} {fields['code']}"
            ),
        ),
    )

    def fake_create(**kwargs):
        create_calls.append(kwargs)
        return 0, {"proposal_id": "P0001", "code": "#ABCDE"}, "ok"

    monkeypatch.setattr(mod, "_create_proposal", fake_create)

    rc = mod.process_absence(
        chat_id="201975216009469@lid",
        text="I have fever, cannot come for shift on 2026-06-07",
        message_id="wa-live-scheduled",
    )

    assert rc == 0
    assert create_calls
    assert create_calls[0]["candidate"].id == "e009"
    assert create_calls[0]["absent_shift"] == "10:00-18:00"
    assert create_calls[0]["absent_role"] == "floor"
    assert create_calls[0]["absent_reason"] == "health: fever"
    assert sent[0][0] == "201975216009469@lid"
    assert "checking coverage" in sent[0][1].lower()
    assert sent[1][0] == "19045550100@s.whatsapp.net"
    assert "owner proposal Anjali Rao #ABCDE" in sent[1][1]
