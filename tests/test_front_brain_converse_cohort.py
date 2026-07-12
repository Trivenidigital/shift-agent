"""Front-brain Phase-1 conversational cohort admission (item 1 + hard coupling).

actions.front_brain_converse_admits gates the hooks-side yield. It is
canonical-identity-aware (#615 flyer_canonical_identity_key), fail-closed, and
HARD-COUPLED to the outbound enforcement tier: a chat is admitted ONLY when the
CONVERSE flag+allowlist AND the FRONT_BRAIN_OUTBOUND_ENFORCE tier both admit it,
so CONVERSE can never arm without its screen.

The coupling consults safe_io.front_brain_outbound_enforce_enabled (single source
of truth); safe_io imports fcntl -> Docker python:3.11-slim, not Windows.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="converse<->enforce coupling consults safe_io (fcntl only)",
)

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


def _set(monkeypatch, *, converse=None, converse_chats=None, enforce=None, enforce_allow=None):
    for k, v in (
        ("FRONT_BRAIN_CONVERSE", converse),
        ("FRONT_BRAIN_CONVERSE_CHATS", converse_chats),
        ("FRONT_BRAIN_OUTBOUND_ENFORCE", enforce),
        ("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", enforce_allow),
    ):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def _both(monkeypatch, converse_chats=CHAT, enforce_allow=CHAT):
    _set(monkeypatch, converse="1", converse_chats=converse_chats,
         enforce="1", enforce_allow=enforce_allow)


# ── base admission ───────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    _set(monkeypatch)
    assert actions.front_brain_converse_admits(CHAT) is False


def test_converse_flag_off_denies(monkeypatch):
    _set(monkeypatch, converse=None, converse_chats=CHAT, enforce="1", enforce_allow=CHAT)
    assert actions.front_brain_converse_admits(CHAT) is False


def test_empty_converse_allowlist_denies(monkeypatch):
    _both(monkeypatch, converse_chats="")
    assert actions.front_brain_converse_admits(CHAT) is False


def test_both_admit_returns_true(monkeypatch):
    _both(monkeypatch, converse_chats="+1 732 983 7841", enforce_allow="+17329837841")
    assert actions.front_brain_converse_admits(CHAT) is True
    assert actions.front_brain_converse_admits("19999999999@c.us") is False


# ── HARD COUPLING: converse impossible without its screen ────────────────────

def test_converse_admits_but_enforce_off_denies(monkeypatch):
    # CONVERSE would admit, but the outbound screen is OFF -> DENY (never converse
    # without enforcement covering the reply).
    _set(monkeypatch, converse="1", converse_chats=CHAT, enforce=None, enforce_allow=CHAT)
    assert actions.front_brain_converse_admits(CHAT) is False


def test_converse_admits_but_chat_not_in_enforce_allowlist_denies(monkeypatch):
    _both(monkeypatch, converse_chats=CHAT, enforce_allow="15550001111")
    assert actions.front_brain_converse_admits(CHAT) is False


def test_enforce_wildcard_satisfies_coupling(monkeypatch):
    _both(monkeypatch, converse_chats=CHAT, enforce_allow="*")
    assert actions.front_brain_converse_admits(CHAT) is True


def test_converse_wildcard_still_capped_by_enforce_allowlist(monkeypatch):
    # CONVERSE graduates all chats, but enforcement only admits the listed chat,
    # so converse is capped to that chat.
    _both(monkeypatch, converse_chats="*", enforce_allow="+17329837841")
    assert actions.front_brain_converse_admits(CHAT) is True
    assert actions.front_brain_converse_admits("18005551234@c.us") is False


# ── canonical identity (LID<->phone convergence) with enforce=* ──────────────

def test_lid_and_phone_converge_on_converse_side(monkeypatch, tmp_path):
    lid = "111222333444@lid"
    cache = tmp_path / "lid-cache.json"
    cache.write_text(
        '{"schema_version":1,"pairs":[{"phone":"+17329837841","lid":"111222333444@lid"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(cache))
    _both(monkeypatch, converse_chats="+17329837841", enforce_allow="*")
    assert actions.front_brain_converse_admits(lid) is True
    assert actions.front_brain_converse_admits("999888777666@lid") is False


def test_blank_chat_denied(monkeypatch):
    _both(monkeypatch, converse_chats=CHAT, enforce_allow=CHAT)
    assert actions.front_brain_converse_admits("") is False
