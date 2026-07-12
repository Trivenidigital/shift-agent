"""Front-brain Phase-1 conversational cohort admission (item 1).

actions.front_brain_converse_admits gates the hooks-side yield. It is
canonical-identity-aware (#615 flyer_canonical_identity_key) and fail-closed.
Pure env + identity logic (no fcntl), so this runs on Windows AND Docker.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
sys.path.insert(0, str(REPO / "src" / "platform"))


def _load_actions():
    name = "cf_router_converse_actions_under_test"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, str(PLUGIN_DIR / "actions.py"))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


actions = _load_actions()

CHAT = "17329837841@c.us"


def _enable(monkeypatch, chats):
    monkeypatch.setenv("FRONT_BRAIN_CONVERSE", "1")
    monkeypatch.setenv("FRONT_BRAIN_CONVERSE_CHATS", chats)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_CONVERSE", raising=False)
    monkeypatch.delenv("FRONT_BRAIN_CONVERSE_CHATS", raising=False)
    assert actions.front_brain_converse_admits(CHAT) is False


def test_flag_on_empty_allowlist_disables(monkeypatch):
    _enable(monkeypatch, "")
    assert actions.front_brain_converse_admits(CHAT) is False


def test_flag_off_with_allowlist_still_disabled(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_CONVERSE", raising=False)
    monkeypatch.setenv("FRONT_BRAIN_CONVERSE_CHATS", CHAT)
    assert actions.front_brain_converse_admits(CHAT) is False


def test_membership_admits_only_listed_chat(monkeypatch):
    _enable(monkeypatch, "+1 732 983 7841")
    # canonical key collapses JID/punctuation/+ so the listed number matches
    assert actions.front_brain_converse_admits(CHAT) is True
    assert actions.front_brain_converse_admits("19999999999@c.us") is False


def test_wildcard_graduates_every_chat(monkeypatch):
    _enable(monkeypatch, "*")
    assert actions.front_brain_converse_admits("anyone@c.us") is True
    assert actions.front_brain_converse_admits("74290284261595@lid") is True


def test_lid_and_phone_converge_when_cache_maps(monkeypatch, tmp_path):
    # A LID whose phone the lid-cache knows resolves to the SAME cohort decision
    # as the phone-JID (the #615 LID<->phone convergence): allowlist the phone,
    # a mapped LID is admitted too.
    lid = "111222333444@lid"
    cache = tmp_path / "lid-cache.json"
    cache.write_text(
        '{"schema_version":1,"pairs":[{"phone":"+17329837841","lid":"111222333444@lid"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(cache))
    _enable(monkeypatch, "+17329837841")
    assert actions.front_brain_converse_admits(lid) is True
    # an unmapped LID with the same allowlist is NOT admitted
    assert actions.front_brain_converse_admits("999888777666@lid") is False


def test_blank_chat_id_not_admitted(monkeypatch):
    _enable(monkeypatch, "*")
    # `*` graduates every chat by design; a blank id under a specific allowlist
    # fails closed.
    _enable(monkeypatch, CHAT)
    assert actions.front_brain_converse_admits("") is False
