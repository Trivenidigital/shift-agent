"""Tests for src/scripts/validate-sender-block — deterministic block parser
the dispatch_shift_agent SKILL invokes as its first step."""
from __future__ import annotations
import importlib.util
import importlib.machinery
import json
import subprocess
import sys
from pathlib import Path

# Load the script as a module so we can call parse() directly without subprocess.
# The file has no .py extension, so use SourceFileLoader directly.
SCRIPT = Path(__file__).resolve().parent.parent / "src" / "scripts" / "validate-sender-block"
loader = importlib.machinery.SourceFileLoader("validate_sender_block", str(SCRIPT))
spec = importlib.util.spec_from_loader("validate_sender_block", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
parse = mod.parse


def test_happy_path_all_fields():
    line = (
        '[shift-agent-sender v=1 platform=whatsapp '
        'phone="+17329837841" lid="201975216009469@lid" '
        'fromMe=true chat_id="918522041562@s.whatsapp.net"]'
    )
    out = parse(line)
    assert out["valid"] is True
    assert out["v"] == 1
    assert out["platform"] == "whatsapp"
    assert out["phone"] == "+17329837841"
    assert out["lid"] == "201975216009469@lid"
    assert out["fromMe"] is True
    assert out["chat_id"] == "918522041562@s.whatsapp.net"


def test_phone_null_lid_set():
    line = (
        '[shift-agent-sender v=1 platform=whatsapp '
        'phone=null lid="201975216009469@lid" '
        'fromMe=false chat_id="918522041562@s.whatsapp.net"]'
    )
    out = parse(line)
    assert out["valid"] is True
    assert out["phone"] is None
    assert out["lid"] == "201975216009469@lid"
    assert out["fromMe"] is False


def test_both_phone_and_lid_null():
    line = (
        '[shift-agent-sender v=1 platform=whatsapp '
        'phone=null lid=null fromMe=false chat_id=null]'
    )
    out = parse(line)
    assert out["valid"] is True
    assert out["phone"] is None
    assert out["lid"] is None
    assert out["chat_id"] is None


def test_missing_v_marker_invalid():
    line = (
        '[shift-agent-sender platform=whatsapp '
        'phone="+17329837841" lid=null fromMe=true chat_id=null]'
    )
    assert parse(line)["valid"] is False


def test_wrong_v_invalid():
    line = (
        '[shift-agent-sender v=2 platform=whatsapp '
        'phone="+17329837841" lid=null fromMe=true chat_id=null]'
    )
    assert parse(line)["valid"] is False


def test_unknown_platform_still_parses():
    """We accept any platform identifier — Hermes adds new platforms over time."""
    line = (
        '[shift-agent-sender v=1 platform=signal '
        'phone="+17329837841" lid=null fromMe=false chat_id=null]'
    )
    out = parse(line)
    assert out["valid"] is True
    assert out["platform"] == "signal"


def test_escaped_quotes_in_value():
    line = (
        r'[shift-agent-sender v=1 platform=whatsapp '
        r'phone="+1\"7329837841" lid=null fromMe=true chat_id=null]'
    )
    out = parse(line)
    assert out["valid"] is True
    assert out["phone"] == '+1"7329837841'


def test_empty_input():
    assert parse("")["valid"] is False
    assert parse(None)["valid"] is False


def test_garbage_invalid():
    assert parse("hello world")["valid"] is False
    assert parse("[shift-agent-sender]")["valid"] is False


def test_extra_trailing_content_rejected():
    """Block must end with `]` and optional whitespace — anything else
    means the line was tampered with."""
    line = (
        '[shift-agent-sender v=1 platform=whatsapp '
        'phone="+17329837841" lid=null fromMe=true chat_id=null] EXTRA'
    )
    assert parse(line)["valid"] is False


def test_cli_via_subprocess():
    """Smoke test invoking the script as the SKILL would."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--line",
         '[shift-agent-sender v=1 platform=whatsapp '
         'phone="+17329837841" lid=null fromMe=true chat_id=null]'],
        capture_output=True, text=True, check=True,
    )
    out = json.loads(result.stdout.strip())
    assert out["valid"] is True
    assert out["phone"] == "+17329837841"
