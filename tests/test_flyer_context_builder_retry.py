"""Creative-Director gateway retry — bounded retry on FAST transients only.

Root cause (live test 2026-06-05): ``flyer_context_builder._call_gateway`` made
the OpenRouter call exactly ONCE and returned ``None`` on ANY failure, so a single
transient gateway blip fail-closed the whole request — the identical call
succeeded ~4s later. The fix adds a bounded retry (3 total attempts, short
backoff) that retries ONLY *fast* transients — HTTP 5xx + connection-level errors
(DNS / connection reset) that fail in milliseconds.

The retry deliberately does NOT cover everything: a TIMEOUT is terminal (the call
already burned the full 30s timeout, so retrying it would stack the tail to
~91s — the long-tail latency the operator's acceptance criteria forbid), and a
deterministic failure (HTTP 4xx, or a 200-but-unparseable response) is also not
retried (retrying cannot fix it).

These tests prove the retry contract OFFLINE — they monkeypatch
``urllib.request.urlopen`` (the network) and ``time.sleep`` (so backoff is
instant), exactly like the rest of the slice-1 suite never touches real HTTP.
Path setup mirrors test_flyer_creative_director.py (src/platform + src/agents/flyer
on sys.path, the way the flat VPS modules import).
"""
from __future__ import annotations

import io
import socket
import urllib.error
from pathlib import Path
import sys

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from schemas import FlyerLockedFact  # noqa: E402

import flyer_context_builder as fcb  # noqa: E402


# ── fixtures (mirror test_flyer_creative_director.py) ────────────────────────


def _identity_facts() -> list[FlyerLockedFact]:
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact",
                        value="+1 732 555 0104", source="customer_profile", required=True),
    ]


def _combo_facts() -> list[FlyerLockedFact]:
    return _identity_facts() + [
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Non Veg Combo", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:0:price", label="Price",
                        value="$49.99", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:name", label="Item",
                        value="Veg Combo", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:price", label="Price",
                        value="$39.99", source="customer_text", required=True),
    ]


_COMBO_REQUEST = (
    "Make a Memorial Day flyer for our two combos — Non Veg Combo $49.99 and "
    "Veg Combo $39.99."
)


def _valid_brief_body() -> str:
    """An OpenRouter-shaped 200 body whose embedded content is a valid FlyerBrief
    candidate (mirrors test_flyer_creative_director._brief_json)."""
    import json
    brief_json = {
        "request_intent": "combo_offer",
        "offer_structure": "Two combo cards.",
        "visual_direction": {
            "theme_family": "Memorial Day patriotic Americana",
            "palette": ["deep red", "navy blue", "white"],
            "motifs": ["stars", "bunting"],
            "visual_subjects": ["festive cookout spread"],
        },
        "layout_strategy": "Headline band, two cards, footer.",
        "grouping": ["combo 1", "combo 2"],
        "must_not_add": ["no third combo"],
        "background_brief": "Textless patriotic cookout background.",
        "fact_refs": [
            {"fact_id": "business_name", "provenance": "locked"},
            {"fact_id": "contact_phone", "provenance": "locked"},
            {"fact_id": "item:0:name", "provenance": "locked"},
            {"fact_id": "item:0:price", "provenance": "locked"},
            {"fact_id": "item:1:name", "provenance": "locked"},
            {"fact_id": "item:1:price", "provenance": "locked"},
        ],
        "offer_groups": [
            {"kind": "combo", "title_ref": "item:0:name", "price_ref": "item:0:price"},
            {"kind": "combo", "title_ref": "item:1:name", "price_ref": "item:1:price"},
        ],
    }
    return json.dumps({"choices": [{"message": {"content": json.dumps(brief_json)}}]})


class _FakeResp:
    """Minimal context-manager stand-in for an ``http.client.HTTPResponse``."""

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=fcb.OPENROUTER_URL, code=code, msg=f"HTTP {code}",
        hdrs=None, fp=io.BytesIO(b""),
    )


class _Urlopen:
    """A scripted ``urlopen`` replacement: each call pops the next action off
    ``actions`` and either returns a fake 200 response or raises the queued
    exception. Records the call count so tests can assert retry behavior."""

    def __init__(self, actions):
        self._actions = list(actions)
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return _FakeResp(action)


@pytest.fixture(autouse=True)
def _armed_no_sleep(monkeypatch):
    """Every retry test runs the REAL ``_call_gateway`` (not a stub): arm the
    flag, give it a non-placeholder key so it reaches urlopen, and make backoff
    instant so the suite stays fast."""
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setattr(fcb, "_openrouter_key", lambda: "sk-test-not-placeholder")
    monkeypatch.setattr(fcb.time, "sleep", lambda *_a, **_k: None)


# ── fast-transient-then-success: a retry recovers the request ────────────────


def test_transient_urlerror_then_success_recovers(monkeypatch):
    """First attempt raises a FAST connection-level error (non-timeout URLError),
    second returns a valid body → build_flyer_brief reaches the validator and
    returns "ok" (retry worked). A non-timeout URLError fails in ms, so retrying
    stays bounded."""
    fake = _Urlopen([urllib.error.URLError("connection reset"), _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert fake.calls == 2  # one fast transient failure, one success


def test_transient_oserror_then_success_recovers(monkeypatch):
    """A bare connection-level OSError (e.g. ECONNRESET) is a fast transient:
    retried, then succeeds."""
    fake = _Urlopen([ConnectionResetError("reset by peer"), _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert fake.calls == 2


def test_transient_5xx_then_success_recovers(monkeypatch):
    """An HTTP 5xx is a fast transient (server-side blip): retried, and the second
    attempt's valid body yields "ok"."""
    fake = _Urlopen([_http_error(503), _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert fake.calls == 2


# ── persistent fast transient: all attempts fail → unavailable, no leak ──────


def test_persistent_transient_exhausts_retries_then_unavailable(monkeypatch):
    """All 3 attempts raise a FAST transient (non-timeout URLError) → status
    "unavailable" (fail-safe), no exception leaks, and exactly 3 attempts were made
    (no infinite retry)."""
    fake = _Urlopen([
        urllib.error.URLError("down"),
        urllib.error.URLError("down"),
        urllib.error.URLError("down"),
    ])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert result.brief is None
    assert result.errors == []
    assert fake.calls == 3  # total attempts = backoffs(2) + 1


def test_persistent_5xx_exhausts_retries_then_unavailable(monkeypatch):
    """Persistent HTTP 5xx is a fast transient and exhausts the 3 attempts → unavailable."""
    fake = _Urlopen([_http_error(500), _http_error(502), _http_error(503)])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert result.brief is None
    assert fake.calls == 3


def test_backoff_sleeps_between_transient_attempts(monkeypatch):
    """Backoff fires once per inter-attempt gap (len == attempts - 1) and uses the
    configured 0.4s → 1.0s schedule — proves bounded backoff, not a tight loop."""
    fake = _Urlopen([_http_error(500), _http_error(500), _http_error(500)])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)
    slept: list[float] = []
    monkeypatch.setattr(fcb.time, "sleep", lambda s: slept.append(s))

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert fake.calls == 3
    assert slept == list(fcb.CREATIVE_DIRECTOR_RETRY_BACKOFFS_SEC) == [0.4, 1.0]


# ── TIMEOUT is terminal: NO retry (no long-tail latency) ─────────────────────


def test_persistent_timeout_is_terminal_single_attempt(monkeypatch):
    """A socket timeout is TERMINAL: a stuck call already burned the full 30s
    timeout, so retrying it would stack the tail (3 × 30s ≈ 91s). Exactly ONE
    attempt, status "unavailable" — the second scripted action is never consumed.
    This is the operator's "bounded, no long-tail latency" acceptance criterion."""
    fake = _Urlopen([socket.timeout("timed out"), _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)
    slept: list[float] = []
    monkeypatch.setattr(fcb.time, "sleep", lambda s: slept.append(s))

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert result.brief is None
    assert fake.calls == 1   # NOT retried — no stacked 30s tail
    assert slept == []       # no backoff because there was no retry


def test_timeouterror_is_terminal_single_attempt(monkeypatch):
    """``TimeoutError`` (the builtin socket.timeout alias on 3.10+) is terminal too."""
    fake = _Urlopen([TimeoutError("timed out"), _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert fake.calls == 1


def test_urlerror_wrapping_timeout_is_terminal_single_attempt(monkeypatch):
    """A timeout can surface wrapped as ``URLError(reason=timeout)`` (urllib does
    this for socket timeouts during connect). That is ALSO terminal — 1 attempt —
    even though a non-timeout URLError would be retried."""
    fake = _Urlopen([
        urllib.error.URLError(socket.timeout("timed out")),
        _valid_brief_body(),
    ])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert result.brief is None
    assert fake.calls == 1   # timeout-in-URLError is terminal, not retried


# ── deterministic 4xx: NOT retried (call count is 1) ─────────────────────────


def test_deterministic_4xx_is_not_retried(monkeypatch):
    """A 4xx is a deterministic client error: it must NOT be retried (the same
    request will 4xx again — retrying wastes a call + money). Exactly ONE attempt,
    and status is "unavailable" (gateway returned no usable brief)."""
    fake = _Urlopen([_http_error(400), _valid_brief_body()])  # 2nd never consumed
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert result.brief is None
    assert fake.calls == 1  # NOT retried


def test_deterministic_401_is_not_retried(monkeypatch):
    """A 401 (bad/expired key) is deterministic too — no retry."""
    fake = _Urlopen([_http_error(401), _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert fake.calls == 1


def test_deterministic_200_unparseable_is_not_retried(monkeypatch):
    """A successful 200 whose body is garbled/off-schema is a DETERMINISTIC shape
    problem, not transient: return None immediately (no retry — a retry can't fix
    a malformed response and would waste a call + money)."""
    fake = _Urlopen(["this is not json at all", _valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "unavailable"
    assert result.brief is None
    assert fake.calls == 1  # 200-but-unparseable is NOT retried


# ── _call_gateway unit: contract is unchanged (Optional[Mapping]) ────────────


def test_call_gateway_returns_parsed_mapping_on_success(monkeypatch):
    """_call_gateway still returns the parsed JSON Mapping on a clean 200 — the
    external contract is byte-identical to the pre-retry version."""
    fake = _Urlopen([_valid_brief_body()])
    monkeypatch.setattr(fcb.urllib.request, "urlopen", fake)

    out = fcb._call_gateway("system", "user")

    assert isinstance(out, dict)
    assert out["request_intent"] == "combo_offer"
    assert fake.calls == 1  # success on the first attempt → no retry


def test_call_gateway_missing_key_returns_none_without_network(monkeypatch):
    """The key gate is unchanged: a missing/placeholder key short-circuits to None
    and never touches the network (urlopen would raise if called)."""
    monkeypatch.setattr(fcb, "_openrouter_key", lambda: "")

    def _boom(*_a, **_k):
        raise AssertionError("must not hit the network when the key is missing")

    monkeypatch.setattr(fcb.urllib.request, "urlopen", _boom)
    assert fcb._call_gateway("system", "user") is None
