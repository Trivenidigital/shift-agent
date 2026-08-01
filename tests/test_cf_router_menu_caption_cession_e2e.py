"""Menu-caption cession, end to end: the ceded inbound reaches menu ingestion,
and the cession itself changes NO approved state.

Companion to tests/test_cf_router_menu_caption_cession.py, which pins the
routing decision. This file follows the ceded inbound one step further — into
`parse-menu-photo`, the script `update_catering_menu` is mandated to call — and
pins what the reviewer asked for: a real pending proposal with a real approval
code comes out the far end, a duplicate delivery does not mint a second one, and
the approved menu / active pricebook are untouched until the owner approves.

WHAT IS AND IS NOT EXERCISED
  * NOT stubbed: the cf-router dispatch decision, the cf-router inbound replay
    guard (pointed at the sandbox so it is LIVE rather than pytest-bypassed),
    the pending-update staging, the approval-code pool + its lock, the
    update-id counter, the pricebook fingerprint read, and the audit emission.
  * Stubbed: `parse-menu-photo._call_vision`, the OpenRouter call, replaced by a
    RECORDED extraction payload — the same seam and the same reason as
    tests/test_pricebook_menu_approval_e2e.py (Hermes is extraction-only by
    design, so that is the right boundary).
  * NOT under test: the SKILL's own LLM interpretation. `update_catering_menu`
    is a SKILL.md, and per the repo's testing convention SKILL interpretation is
    observability + manual smoke, not pytest. These cells therefore prove
    "cf-router releases the inbound AND the script the SKILL must call produces
    the expected pending proposal" — the LLM step between them is asserted by
    the deployed fail-closed rule in the SKILL, not here.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
PRICEBOOK_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catering_pricebook_valid.json"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import yaml  # noqa: E402

# The routing harness is the unit suite's — one definition of "the flyer path is
# armed exactly as the live box had it", so the two files cannot drift apart.
from test_cf_router_menu_caption_cession import (  # noqa: E402
    CHAT,
    INCIDENT_CAPTION,
    MEDIA,
    _load_plugin,
    _wire,
)

OWNER_PHONE = "+15550100001"
MESSAGE_ID = "wamid.MENU0801"
CODE_RE = re.compile(r"^#[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}$")

# Recorded vision extraction — a small, unambiguous menu. The extraction content
# is not what these cells are about (tests/test_pricebook_menu_approval_e2e.py
# owns the adapter's exclusion rules); what matters is that a real pending
# proposal with a real code is staged.
EXTRACTION = {
    "items": [
        {"name": "Idly (3 PCS)", "price_usd": 6.00, "category": "appetizer",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Masala Dosa", "price_usd": 10.50, "category": "main",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Goat Curry", "price_usd": 18.00, "category": "main",
         "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": 4},
    ],
    "parser_notes": "",
}

# The menu the owner already approved, and which must stay live throughout.
APPROVED_MENU = {
    "version": 3,
    "updated_at": "2026-07-01T12:00:00+00:00",
    "updated_by": "photo-ocr",
    "source_image_id": "wamid.OLDMENU",
    "items": [
        {"name": "Veg Biryani", "price_usd": 12.00, "category": "main",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 2},
    ],
    "notes": "delivery within 15 miles",
}


class _Box:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.logs = root / "logs"
        self.config = root / "config.yaml"
        self.menu = self.state / "catering-menu.json"
        self.pending = self.state / "catering-menu-pending.json"
        self.pricebook = self.state / "catering-pricebook.json"
        self.dedupe = self.state / "cf-router-inbound-dedupe.json"
        self.log = self.logs / "decisions.log"
        self.image = root / "menu-photo.jpg"
        # Every outbound that survives every gate. Must stay EMPTY.
        self.sent: list[str] = []


@pytest.fixture
def box(tmp_path, monkeypatch) -> _Box:
    """A sandbox holding an ALREADY-APPROVED menu and an ACTIVE pricebook, so
    "nothing was mutated" is a claim about real prior state, not about absence."""
    b = _Box(tmp_path)
    b.state.mkdir(parents=True, exist_ok=True)
    b.logs.mkdir(parents=True, exist_ok=True)
    b.config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "customer": {"name": "Triveni Menu Cession", "location_id": "loc_cession",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": OWNER_PHONE,
                  "self_chat_jid": "15550100001@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }), encoding="utf-8")
    b.menu.write_text(json.dumps(APPROVED_MENU, indent=2), encoding="utf-8")
    b.pricebook.write_bytes(PRICEBOOK_FIXTURE.read_bytes())
    b.log.write_text("", encoding="utf-8")
    b.image.write_bytes(b"\xff\xd8\xff\xe0 not-a-real-jpeg-but-a-real-file")

    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(b.config))
    monkeypatch.setenv("SHIFT_AGENT_STATE_DIR", str(b.state))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(b.log))
    monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(b.log))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-a-placeholder")

    # Transport sink: nothing in this transcript may reach a real endpoint. Any
    # outbound at all is a hard failure, so "zero customer sends" is enforced by
    # the harness rather than asserted by inspection.
    def _no_transport(req, timeout=None):  # noqa: ARG001
        b.sent.append(getattr(req, "full_url", str(req)))
        raise AssertionError("this transcript must not send anything")

    with patch("urllib.request.urlopen", _no_transport):
        yield b


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_run_seq = [0]


def _parse_menu_photo(box: _Box, *, source_image_id: str = MESSAGE_ID) -> dict:
    """Run the REAL parse-menu-photo — the script the ceded SKILL must call —
    bound to the sandbox, with only the vision call recorded. A FRESH module per
    call: each invocation is its own process-of-record in production."""
    _run_seq[0] += 1
    mod = load_script(f"cession_parse_menu_photo_{_run_seq[0]}", SCRIPTS / "parse-menu-photo")
    for name, value in {
        "CONFIG_PATH": box.config,
        "PENDING_PATH": box.pending,
        "PENDING_LOCK": Path(str(box.pending) + ".lock"),
        "UPDATE_COUNTER_PATH": box.state / "catering-menu-update-counter.txt",
        "PRICEBOOK_PATH": box.pricebook,
        "LOG_PATH": box.log,
    }.items():
        assert hasattr(mod, name), f"parse-menu-photo lost its {name} global"
        setattr(mod, name, value)
    mod._call_vision = lambda *a, **k: EXTRACTION

    old_argv = sys.argv
    sys.argv = ["parse-menu-photo", "--image-path", str(box.image),
                "--source-image-id", source_image_id, "--owner-phone", OWNER_PHONE]
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    assert rc == 0, (out.getvalue(), err.getvalue())
    for line in reversed([ln for ln in out.getvalue().splitlines() if ln.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"parse-menu-photo emitted no JSON: {out.getvalue()!r}")


def _cede(monkeypatch, box: _Box, *, live_replay_guard: bool = False,
          message_id: str = MESSAGE_ID, role: str = "owner"):
    """Drive one inbound through the real dispatch. With `live_replay_guard` the
    cf-router inbound dedupe file is pointed at the sandbox, which also lifts the
    pytest bypass in `mark_cf_router_inbound_seen` — so the guard is LIVE."""
    hooks_mod, actions_mod = _load_plugin()
    real_seen = actions_mod.mark_cf_router_inbound_seen
    s = _wire(monkeypatch, hooks_mod, actions_mod, role=role)
    if live_replay_guard:
        monkeypatch.setattr(actions_mod, "CF_ROUTER_INBOUND_DEDUPE_PATH", box.dedupe)
        monkeypatch.setattr(actions_mod, "mark_cf_router_inbound_seen", real_seen)
    result = hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=INCIDENT_CAPTION, chat_id=CHAT, message_id=message_id, media_path=MEDIA,
    ))
    return result, s


def _proposed_rows(box: _Box) -> list[dict]:
    return [r for r in read_log_rows(box.log) if r.get("type") == "menu_update_proposed"]


# ── Proof 10: the ceded route produces the approval #code ───────────────────
def test_ceded_route_stages_a_pending_proposal_with_an_approval_code(monkeypatch, box):
    """cf-router releases the inbound, then the script the SKILL is mandated to
    call stages a real pending update carrying a real `#XXXXX` code from the
    deployed alphabet — the code the owner replies with to approve.

    This is guarantee (c): the ceded message lands in the EXISTING ingestion, not
    a parallel one. Evidenced three ways — the script executed is the deployed
    file on disk, the preview card is the one that script renders, and the code is
    visible to the EXISTING cross-pool approval registry (`all_live_codes`), which
    is what makes it collide-safe against catering / expense / shift codes and
    resolvable by the deployed dispatcher."""
    import approval_code_pools

    result, s = _cede(monkeypatch, box)
    assert result is None and s.primary_calls == []
    assert s.reasons == ["menu_caption_ceded_to_dispatcher"]
    assert not box.pending.exists(), "routing alone must stage nothing"

    out = _parse_menu_photo(box)

    assert CODE_RE.match(out["confirmation_code"]), out["confirmation_code"]
    assert out["item_count"] == 3
    assert out["preview_text"].strip(), "the existing extraction-preview card"
    pending = json.loads(box.pending.read_text(encoding="utf-8"))
    assert pending["confirmation_code"] == out["confirmation_code"]
    assert pending["source_image_id"] == MESSAGE_ID
    assert pending["update_id"] == out["update_id"]
    assert len(pending["extracted_items"]) == 3
    rows = _proposed_rows(box)
    assert len(rows) == 1
    assert rows[0]["confirmation_code"] == out["confirmation_code"]
    # The EXISTING approval mechanism now owns this code — no parallel registry.
    assert out["confirmation_code"] in approval_code_pools.all_live_codes()
    assert box.sent == [], "no outbound from routing or extraction"


def test_the_ceded_route_runs_the_deployed_ingestion_script(box):
    """Non-vacuity for guarantee (c): the harness executes the file that ships to
    the box, so "the existing flow" cannot quietly become a test-local copy."""
    script = SCRIPTS / "parse-menu-photo"
    assert script.is_file()
    assert script.read_text(encoding="utf-8").lstrip().startswith("#!")


# ── Proofs 8 + 9: no approved state moves until the owner approves ──────────
def test_routing_leaves_the_approved_menu_and_active_pricebook_byte_unchanged(
        monkeypatch, box):
    """The cession only YIELDS to the dispatcher. It must not activate a
    pricebook or mutate the approved menu — and neither must the extraction step
    that follows it. Byte-level, against real pre-existing approved state."""
    menu_before = _sha256(box.menu)
    pricebook_before = _sha256(box.pricebook)

    result, s = _cede(monkeypatch, box)

    assert result is None and s.reasons == ["menu_caption_ceded_to_dispatcher"]
    assert _sha256(box.menu) == menu_before, "the cession mutated the approved menu"
    assert _sha256(box.pricebook) == pricebook_before, "the cession touched the pricebook"

    out = _parse_menu_photo(box)

    # The preview EXISTS — staging is expected and fine — and both COMMERCIAL
    # stores are still byte-identical. Mutation only past the #code approval.
    assert box.pending.exists() and CODE_RE.match(out["confirmation_code"])
    assert _sha256(box.menu) == menu_before, "extraction mutated the approved menu"
    assert _sha256(box.pricebook) == pricebook_before, "extraction activated a pricebook"
    assert box.sent == []


def test_the_previously_approved_menu_stays_active_through_preview(monkeypatch, box):
    """Proof 9 stated as the invariant it is: after the ceded route has staged a
    preview, the menu the owner approved EARLIER is still the live one — same
    version, same items. Nothing goes live until the `#code` + yes arrives."""
    _cede(monkeypatch, box)
    _parse_menu_photo(box)

    live = json.loads(box.menu.read_text(encoding="utf-8"))
    assert live == APPROVED_MENU
    assert live["version"] == 3
    assert [i["name"] for i in live["items"]] == ["Veg Biryani"]
    assert box.pending.exists(), "the new extraction is staged as PENDING, not live"
    assert json.loads(box.pending.read_text(encoding="utf-8"))["update_id"] != live["version"]
    assert box.sent == []


# ── Proof 7: a replayed delivery does not mint a second pending update ──────
def test_duplicate_delivery_yields_exactly_one_pending_update_and_one_code(
        monkeypatch, box):
    """WhatsApp/Hermes replays an already-handled upsert after a restart. The
    cf-router inbound replay guard catches the second copy BEFORE the cession, so
    the dispatcher is handed the menu photo exactly once — one extraction, one
    pending update, one approval code. Without this the owner would get two codes
    for one photo and could approve the stale one."""
    first, s1 = _cede(monkeypatch, box, live_replay_guard=True)
    assert first is None
    assert s1.reasons == ["menu_caption_ceded_to_dispatcher"]

    out = _parse_menu_photo(box)  # the hand-off the first delivery earned

    second, s2 = _cede(monkeypatch, box, live_replay_guard=True)

    assert second == {"action": "skip", "reason": "cf-router duplicate inbound"}
    assert "menu_caption_ceded_to_dispatcher" not in s2.reasons, (
        "a replayed delivery must not cede a second time")
    assert s2.primary_calls == [], "and must not fall through to flyer either"
    # One hand-off ⇒ one staged proposal ⇒ one code, unchanged by the replay.
    pending = json.loads(box.pending.read_text(encoding="utf-8"))
    assert pending["confirmation_code"] == out["confirmation_code"]
    assert len(_proposed_rows(box)) == 1, "ONE logical processing outcome"
    assert box.sent == [], "and no duplicate outbound — no outbound at all"


def test_the_pending_store_is_single_slot_by_construction(monkeypatch, box):
    """Defence in depth behind the replay guard: `parse-menu-photo` writes the
    pending update to ONE atomic path, so even two extractions can never leave
    two pending menu updates for the owner to approve. Pinned because the replay
    guard is TTL-bounded — after the TTL this structural property is what stops a
    second pending proposal from existing alongside the first."""
    menu_before = box.menu.read_bytes()
    _cede(monkeypatch, box)
    first = _parse_menu_photo(box)
    second = _parse_menu_photo(box)

    pending = json.loads(box.pending.read_text(encoding="utf-8"))
    assert pending["confirmation_code"] == second["confirmation_code"]
    assert first["confirmation_code"] != second["confirmation_code"], (
        "each extraction mints its own code — which is why the replay guard, not "
        "this property, is what makes a duplicate DELIVERY produce one code")
    assert box.menu.read_bytes() == menu_before
