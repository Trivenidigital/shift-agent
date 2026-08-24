"""`send-coverage-message` must validate its bridge URL before POSTing.

`BRIDGE_SEND_URL` became env-overridable in c22d687c (P1-A). That change did not
bring the validator that every other reader of `HERMES_BRIDGE_URL` pairs with it
— `safe_io.bridge_post` and `send-daily-brief`. Before c22d687c the URL was a
hardcoded constant and therefore unexfiltratable; after it, an operator-set
`HERMES_BRIDGE_URL` pointed at a remote host would silently redirect a staff
phone number and the rendered coverage message from this one script, while every
other send path refused the same value.

This script keeps its own `bridge_post` (that bypass is under separate review),
so the validator has to be called explicitly here rather than inherited.
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
    reason="send-coverage-message imports safe_io, which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "send-coverage-message"
PLATFORM = REPO / "src" / "platform"


def _load(monkeypatch, bridge_url: str):
    """Import the script as a module with HERMES_BRIDGE_URL set.

    The URL is read at import time, so it must be set before exec_module.
    """
    monkeypatch.setenv("HERMES_BRIDGE_URL", bridge_url)
    monkeypatch.delenv("HERMES_BRIDGE_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("SHIFT_AGENT_REHEARSAL", raising=False)
    if str(PLATFORM) not in sys.path:
        sys.path.insert(0, str(PLATFORM))
    loader = importlib.machinery.SourceFileLoader("scm_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_file_location(
        "scm_under_test", str(SCRIPT), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # name != __main__, so main() does not run
    return mod


@pytest.mark.parametrize("evil", [
    "http://exfil.example/send",
    "https://attacker.test/collect",
    "http://10.0.0.5:3000/send",
])
def test_a_remote_bridge_url_is_refused_before_any_post(monkeypatch, evil):
    """The exfiltration case. Must refuse, and must refuse WITHOUT sending."""
    mod = _load(monkeypatch, evil)

    posted = []
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda *a, **k: posted.append(a) or pytest.fail(
                            "a POST was attempted to a remote bridge URL"))

    ok, detail = mod.bridge_post("15550100001@s.whatsapp.net", "coverage ask")

    assert ok is False, "a remote bridge URL must not be accepted"
    assert "unsafe bridge url" in detail, detail
    assert posted == [], "refusal must happen before the request is issued"


def test_a_non_http_scheme_is_refused(monkeypatch):
    mod = _load(monkeypatch, "file:///etc/passwd")
    ok, detail = mod.bridge_post("15550100001@s.whatsapp.net", "x")
    assert ok is False
    assert "unsafe bridge url" in detail, detail


def test_production_loopback_is_unaffected(monkeypatch):
    """PRODUCTION MUST NOT CHANGE. The deployed value is 127.0.0.1:3000, and it
    must still reach the transport. Without this the guard could 'pass' by
    refusing everything — the failure mode that looks identical to success."""
    mod = _load(monkeypatch, "http://127.0.0.1:3000/send")

    class _Resp:
        status = 200

        def read(self):
            return b'{"id":"msg_ok_1"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen)

    ok, detail = mod.bridge_post("15550100001@s.whatsapp.net", "coverage ask")

    assert ok is True, f"loopback must still send: {detail}"
    assert seen["url"] == "http://127.0.0.1:3000/send"


# NOTE: the HERMES_BRIDGE_ALLOW_REMOTE escape hatch is deliberately NOT tested
# here. safe_io reads it into the module constant ALLOW_REMOTE_BRIDGE at import
# time, so an in-process monkeypatch cannot reach it and a test written that way
# fails identically with and without this fix — which is to say it pins nothing.
# The opt-in is safe_io's behaviour and is covered by safe_io's own tests; this
# file's job is that THIS script consults the validator at all.


def test_the_stale_comment_no_longer_claims_the_validator_is_absent():
    """The source used to carry 'no validate_bridge_url' as a statement of fact.
    A comment asserting the opposite of the code is the defect class this repo
    keeps paying for, so it is pinned rather than trusted."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "no validate_bridge_url" not in src, (
        "the comment still claims the validator is absent, but it is called")
    assert "validate_bridge_url(BRIDGE_SEND_URL)" in src, (
        "the validator must be called on the module-level URL actually POSTed to")
