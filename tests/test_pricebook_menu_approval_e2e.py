"""OWNER-MENU -> PRICEBOOK — production-equivalent deterministic E2E.

One owner, one sandbox, one ordered transcript. The owner photographs their menu
and ends up with real, cents-exact commercial prices, through ONE approval:

    parse-menu-photo -> (owner correction: `no` + corrected photo)
      -> parse-menu-photo -> apply-menu-update --decision yes
      -> import-catering-pricebook (real subprocess) -> compute_quote

WHAT IS AND IS NOT STUBBED
  * NOT stubbed: the pending-update staging, the approval-code pool, the owner
    card, the menu persistence + archive + audit, the adapter, the pricebook
    import (a REAL subprocess, exactly as apply-menu-update spawns it in
    production) with its validation / version bump / archive / audit, and the
    pricing kernel.
  * Stubbed: exactly TWO seams, both outside the business logic —
      1. `parse-menu-photo._call_vision`, the OpenRouter call, replaced by
         RECORDED extraction payloads. Precedent:
         tests/test_routing_invariants_r1.py does the same to extract-receipt.
         Hermes is extraction-only by design, so this is the right boundary.
      2. `urllib.request.urlopen`, the WhatsApp transport — present ONLY so the
         transcript can assert that NOTHING was sent to a customer.

CORRECTION MECHANISM. v0.2 has no inline edit verb: both SKILLs state that the
owner corrects an extraction by discarding and re-sending a corrected photo
(update_catering_menu "owner re-uploads if a few items are wrong";
apply_catering_menu_decision "NEVER edit items inline in v0.2"). test_02/03 drive
exactly that loop, which is why the ambiguous item resolves with no new code.

ORDERING IS LOAD-BEARING. These share ONE module-scoped sandbox and run as a
transcript. Run them as a file, not individually.

CROSS-PLATFORM: runs on Linux AND Windows. The child process gets a sandbox-local
`fcntl` shim on Windows only (see `_write_windows_fcntl_shim`), so the REAL
subprocess boundary is exercised on both, rather than the import being mocked out.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLATFORM = SRC / "platform"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
PRICEBOOK_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catering_pricebook_valid.json"
for _p in (str(PLATFORM), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

import catering_pricing  # noqa: E402

OWNER_PHONE = "+19045550100"

# ── Recorded vision extractions ─────────────────────────────────────────────
# Take 1: what the camera actually produced. It covers every exclusion rule the
# adapter has, plus the AMBIGUOUS item the owner has to resolve conversationally
# (Paneer Tikka priced twice — nothing in the menu says which is current).
EXTRACTION_TAKE_1 = {
    "items": [
        {"name": "Idly (3 PCS)", "price_usd": 6.00, "category": "appetizer",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Masala Dosa", "price_usd": 10.50, "category": "main",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Goat Curry", "price_usd": 18.00, "category": "main",
         "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": 4},
        {"name": "Gulab Jamun", "price_usd": 5.00, "category": "dessert",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 2},
        {"name": "Market Fish Fry", "price_usd": None, "category": "main",
         "dietary_tags": ["non-veg"], "available": True, "notes": "market price",
         "serves": 2},
        {"name": "Welcome Drink", "price_usd": 0, "category": "beverage",
         "dietary_tags": ["veg"], "available": True, "notes": "complimentary",
         "serves": 1},
        {"name": "Chai", "price_usd": 5.999, "category": "beverage",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        # THE AMBIGUOUS ITEM: two rows, two prices, no way to tell which is live.
        {"name": "Paneer Tikka", "price_usd": 12.00, "category": "appetizer",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 2},
        {"name": "Paneer Tikka", "price_usd": 14.00, "category": "appetizer",
         "dietary_tags": ["veg"], "available": True, "notes": "large tray",
         "serves": 4},
    ],
    "parser_notes": "two Paneer Tikka rows; one item marked market price",
}

# Take 2: the owner answered "$14 is the large tray, $12 is the active one",
# reprinted that line, and re-sent. The duplicate is gone, Chai's OCR artifact is
# corrected. Market Fish Fry and Welcome Drink are UNCHANGED on purpose — the
# owner confirmed those stay unpriced, and they must stay excluded.
EXTRACTION_TAKE_2 = {
    "items": [
        {"name": "Idly (3 PCS)", "price_usd": 6.00, "category": "appetizer",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Masala Dosa", "price_usd": 10.50, "category": "main",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Goat Curry", "price_usd": 18.00, "category": "main",
         "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": 4},
        {"name": "Gulab Jamun", "price_usd": 5.00, "category": "dessert",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 2},
        {"name": "Market Fish Fry", "price_usd": None, "category": "main",
         "dietary_tags": ["non-veg"], "available": True, "notes": "market price",
         "serves": 2},
        {"name": "Welcome Drink", "price_usd": 0, "category": "beverage",
         "dietary_tags": ["veg"], "available": True, "notes": "complimentary",
         "serves": 1},
        {"name": "Chai", "price_usd": 2.50, "category": "beverage",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 1},
        {"name": "Paneer Tikka", "price_usd": 12.00, "category": "appetizer",
         "dietary_tags": ["veg"], "available": True, "notes": "", "serves": 2},
    ],
    "parser_notes": "corrected reprint",
}

# What take 2's clean rows must become, cents-exact.
EXPECTED_OVERRIDES = {
    "Idly (3 PCS)": 600,
    "Masala Dosa": 1050,
    "Goat Curry": 1800,
    "Gulab Jamun": 500,
    "Chai": 250,
    "Paneer Tikka": 1200,
}
# In the ACTIVE pricebook only — not on the menu, so a sync reports it removed.
STALE_OVERRIDE_NAME = "Seasonal Special"

_WINDOWS_FCNTL_SHIM = '''"""Sandbox-local no-op fcntl for the Windows child process.

Written by tests/test_pricebook_menu_approval_e2e.py and reachable only through
the PYTHONPATH that test hands the child. Advisory locking is irrelevant to
correctness in a single-writer test; this exists so the REAL
import-catering-pricebook subprocess can be exercised on Windows instead of
being mocked away."""
LOCK_EX = 2
LOCK_SH = 1
LOCK_UN = 8
LOCK_NB = 4


def flock(*_a, **_k):
    return None


def lockf(*_a, **_k):
    return None
'''


class _Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.logs = root / "logs"
        self.shim = root / "childpath"
        self.config = root / "config.yaml"
        self.menu = self.state / "catering-menu.json"
        self.menu_pending = self.state / "catering-menu-pending.json"
        self.menu_archive = self.state / "catering-menu-archive"
        self.pricebook = self.state / "catering-pricebook.json"
        self.pricebook_archive = self.state / "catering-pricebook-archive"
        self.log = self.logs / "decisions.log"
        self.image = root / "menu-photo.jpg"
        # Every WhatsApp send that survives every gate. Must stay EMPTY.
        self.sent: list[dict] = []
        # Carried between ordered steps (minted by the real scripts).
        self.code_take_1: str = ""
        self.code_take_2: str = ""
        self.update_id_take_2: str = ""
        self.card_take_1: str = ""
        self.card_take_2: str = ""


def _write_windows_fcntl_shim(sb: _Sandbox) -> None:
    """On Windows the child subprocess has no fcntl, and safe_io imports it at
    module top. Drop a no-op shim on a sandbox-local PYTHONPATH entry — written
    ONLY on Windows so it can never shadow the real module on Linux."""
    sb.shim.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (sb.shim / "fcntl.py").write_text(_WINDOWS_FCNTL_SHIM, encoding="utf-8")


@pytest.fixture(scope="module")
def sb(tmp_path_factory) -> _Sandbox:
    """ONE sandbox for the whole transcript (see the module docstring on order)."""
    box = _Sandbox(tmp_path_factory.mktemp("menu_pricebook_e2e"))
    box.state.mkdir(parents=True, exist_ok=True)
    box.logs.mkdir(parents=True, exist_ok=True)
    box.config.write_text(yaml.safe_dump({
        "schema_version": 1,
        "customer": {"name": "Triveni Bridge E2E", "location_id": "loc_bridge",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": OWNER_PHONE,
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }), encoding="utf-8")
    box.log.write_text("", encoding="utf-8")
    box.image.write_bytes(b"\xff\xd8\xff\xe0 not-a-real-jpeg-but-a-real-file")
    _write_windows_fcntl_shim(box)
    return box


@pytest.fixture(autouse=True)
def _e2e_env(sb: _Sandbox, monkeypatch):
    """Re-point every env-resolved path at the shared sandbox and install the
    transport sink. Re-applied per test (not once) because conftest's autouse
    isolations re-point SHIFT_AGENT_DECISIONS_LOG_PATH at a fresh tmp dir, and
    module-level autouse fixtures run after conftest's."""
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(sb.config))
    monkeypatch.setenv("SHIFT_AGENT_STATE_DIR", str(sb.state))
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(sb.log))
    monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(sb.log))
    # The one env var the CHILD import-catering-pricebook needs to reach the
    # sandbox; SHIFT_AGENT_DECISIONS_LOG_PATH above covers its audit sink.
    monkeypatch.setenv("SHIFT_AGENT_PRICEBOOK_PATH", str(sb.pricebook))
    monkeypatch.setenv("PYTHONPATH", str(sb.shim))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-a-placeholder")
    # Never page a real owner from a test: the notify chokepoint shells out to a
    # binary that does not exist on a runner, which is already its miss path.
    monkeypatch.setenv("SHIFT_AGENT_NOTIFY_DEDUP", "0")

    def _fake_urlopen(req, timeout=None):  # noqa: ARG001
        sb.sent.append({"url": getattr(req, "full_url", str(req))})
        raise AssertionError("this transcript must not send anything")

    with patch("urllib.request.urlopen", _fake_urlopen):
        yield


# ── in-process script runner (the real scripts, sandbox-bound) ──────────────
_run_seq = [0]


def _sandbox_values(sb: _Sandbox) -> dict:
    return {
        "CONFIG_PATH": sb.config,
        "MENU_PATH": sb.menu,
        "MENU_LOCK": Path(str(sb.menu) + ".lock"),
        "PENDING_PATH": sb.menu_pending,
        "PENDING_LOCK": Path(str(sb.menu_pending) + ".lock"),
        "UPDATE_COUNTER_PATH": sb.state / "catering-menu-update-counter.txt",
        "ARCHIVE_DIR": sb.menu_archive,
        "PRICEBOOK_PATH": sb.pricebook,
        "LOG_PATH": sb.log,
        # The REAL sibling script, run by the REAL interpreter running this test.
        # apply-menu-update spawns it exactly as production does.
        "IMPORT_PRICEBOOK_BIN": SCRIPTS / "import-catering-pricebook",
        "PYTHON_BIN": Path(sys.executable),
    }


def _run_script(sb: _Sandbox, script_name: str, argv: list[str], *,
                patches: dict | None = None):
    """Load a catering script fresh, bind it to the sandbox, run its main().

    A FRESH module per call on purpose: each invocation is its own
    process-of-record in production, so anything that only works because a
    previous run left module state behind is a bug this harness must not hide.
    """
    _run_seq[0] += 1
    mod = load_script(f"bridge_{script_name.replace('-', '_')}_{_run_seq[0]}",
                      SCRIPTS / script_name)
    for name, value in _sandbox_values(sb).items():
        if hasattr(mod, name):
            setattr(mod, name, value)
    for name, value in (patches or {}).items():
        setattr(mod, name, value)

    old_argv = sys.argv
    sys.argv = [script_name, *argv]
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    return rc, out.getvalue(), err.getvalue()


def _stdout_json(out: str) -> dict:
    for line in reversed([ln for ln in out.splitlines() if ln.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def _extract(sb: _Sandbox, payload: dict, source_image_id: str) -> dict:
    rc, out, err = _run_script(
        sb, "parse-menu-photo",
        ["--image-path", str(sb.image), "--source-image-id", source_image_id,
         "--owner-phone", OWNER_PHONE],
        patches={"_call_vision": lambda *a, **k: payload},
    )
    assert rc == 0, (out, err)
    return _stdout_json(out)


def _rows(sb: _Sandbox, type_: str) -> list[dict]:
    return [r for r in read_log_rows(sb.log) if r.get("type") == type_]


def _active(sb: _Sandbox):
    return catering_pricing.load_pricebook(sb.pricebook)


# ════════════════════════════════════════════════════════════════════════════
# 1-2. The owner photographs the menu; the card shows what approving would do
# ════════════════════════════════════════════════════════════════════════════
def test_01_operator_seeds_the_commercial_pricebook(sb: _Sandbox):
    """Commercial terms still arrive by import — the bridge supplies ITEM prices,
    never the business model."""
    document = json.loads(PRICEBOOK_FIXTURE.read_text(encoding="utf-8"))
    document["item_price_overrides"] = {"Gulab Jamun": 450, STALE_OVERRIDE_NAME: 900}
    source = sb.root / "pricebook-seed.json"
    source.write_text(json.dumps(document), encoding="utf-8")

    rc, out, err = _run_script(sb, "import-catering-pricebook", [
        "--file", str(source), "--sender-role", "owner",
    ])
    assert rc == 0, (out, err)
    assert _stdout_json(out)["version"] == 5           # fixture is v4 -> max(0,4)+1
    assert _active(sb).source_menu_update_id is None, "a hand import sets no anchor"


def test_02_the_owner_card_states_the_pricebook_effect_of_approving(sb: _Sandbox):
    result = _extract(sb, EXTRACTION_TAKE_1, "wamid.MENU.TAKE1")
    sb.code_take_1 = result["confirmation_code"]
    sb.card_take_1 = result["preview_text"]

    # The single approval's scope is stated on the card, before any yes.
    assert "*Pricebook* — approving this also activates it" in sb.card_take_1
    assert "activates pricebook version 6" in sb.card_take_1
    assert "+ Idly (3 PCS) — $6.00" in sb.card_take_1
    assert "~ Gulab Jamun — $4.50 → $5.00" in sb.card_take_1
    assert f"- {STALE_OVERRIDE_NAME} — $9.00 (no longer on the menu)" in sb.card_take_1

    # The ambiguous item is visibly NOT priced — this is what the owner answers.
    assert "Paneer Tikka — listed twice at different prices" in sb.card_take_1
    assert "Market Fish Fry — no price printed" in sb.card_take_1
    assert "Welcome Drink — priced at zero or less" in sb.card_take_1
    assert "Chai — price has fractions of a cent" in sb.card_take_1

    # Staging only: neither store has moved.
    assert not sb.menu.exists()
    assert _active(sb).version == 5


# ════════════════════════════════════════════════════════════════════════════
# 3-4. The owner corrects — through v0.2's ONLY correction mechanism
# ════════════════════════════════════════════════════════════════════════════
def test_03_owner_discards_the_ambiguous_take(sb: _Sandbox):
    """There is no inline edit verb in v0.2 (both SKILLs say so). The owner
    answers the clarifying question by discarding and re-sending."""
    rc, out, err = _run_script(sb, "apply-menu-update", [
        "--code", sb.code_take_1, "--decision", "no", "--sender-role", "owner",
    ])
    assert rc == 0, (out, err)
    assert _stdout_json(out)["reason"] == "owner_no"
    assert not sb.menu_pending.exists()
    assert not sb.menu.exists(), "a discard never publishes a menu"
    assert _active(sb).version == 5, "a discard never touches prices"
    assert _rows(sb, "menu_update_rejected")[-1]["reason"] == "owner_no"


def test_04_the_corrected_photo_resolves_the_ambiguity(sb: _Sandbox):
    result = _extract(sb, EXTRACTION_TAKE_2, "wamid.MENU.TAKE2")
    sb.code_take_2 = result["confirmation_code"]
    sb.update_id_take_2 = result["update_id"]
    sb.card_take_2 = result["preview_text"]

    assert sb.code_take_2 != sb.code_take_1, "a fresh proposal carries a fresh code"
    assert "Paneer Tikka — listed twice" not in sb.card_take_2
    assert "+ Paneer Tikka — $12.00" in sb.card_take_2
    assert "+ Chai — $2.50" in sb.card_take_2
    # The two the owner confirmed stay unpriced are STILL excluded.
    assert "Market Fish Fry — no price printed" in sb.card_take_2
    assert "Welcome Drink — priced at zero or less" in sb.card_take_2


# ════════════════════════════════════════════════════════════════════════════
# 5-9. ONE approval publishes the menu AND activates the pricebook
# ════════════════════════════════════════════════════════════════════════════
def test_05_approval_publishes_the_menu_and_activates_the_pricebook(sb: _Sandbox):
    before_archives = set(sb.pricebook_archive.glob("*.json")) \
        if sb.pricebook_archive.exists() else set()
    prior_bytes = sb.pricebook.read_bytes()

    rc, out, err = _run_script(sb, "apply-menu-update", [
        "--code", sb.code_take_2, "--decision", "yes", "--sender-role", "owner",
    ])
    assert rc == 0, (out, err)
    result = _stdout_json(out)
    assert result["status"] == "applied"
    assert result["new_version"] == 1
    assert result["pricebook_activated"] is True, err

    # Menu: published exactly as before this change.
    menu = json.loads(sb.menu.read_text(encoding="utf-8"))
    assert menu["updated_by"] == "photo-ocr"
    assert menu["source_image_id"] == "wamid.MENU.TAKE2"
    assert len(menu["items"]) == len(EXTRACTION_TAKE_2["items"])
    assert not sb.menu_pending.exists()

    # Pricebook: version N+1, exactly what the card promised.
    book = _active(sb)
    assert book.version == 6
    assert book.updated_by == "menu_approval"
    assert book.source_menu_update_id == sb.update_id_take_2
    assert book.item_price_overrides == EXPECTED_OVERRIDES
    assert STALE_OVERRIDE_NAME not in book.item_price_overrides
    # Excluded items never become prices.
    assert "Market Fish Fry" not in book.item_price_overrides
    assert "Welcome Drink" not in book.item_price_overrides

    # Commercial fields carried forward VERBATIM from v5.
    seed = json.loads(PRICEBOOK_FIXTURE.read_text(encoding="utf-8"))
    assert book.tax_rate_bps == seed["tax_rate_bps"] == 825
    assert [p.id for p in book.per_person_packages] == \
        [p["id"] for p in seed["per_person_packages"]]
    assert [f.id for f in book.fixed_fees] == [f["id"] for f in seed["fixed_fees"]]
    assert [d.id for d in book.approved_discounts] == \
        [d["id"] for d in seed["approved_discounts"]]

    # Prior version archived, byte-identical to what was live before.
    new_archives = set(sb.pricebook_archive.glob("*.json")) - before_archives
    assert len(new_archives) == 1, new_archives
    archived = new_archives.pop()
    assert "pricebook-v5-" in archived.name
    assert archived.read_bytes() == prior_bytes, "the archive is a byte copy of v5"

    # One audit trail, both effects.
    assert _rows(sb, "menu_update_applied")[-1]["new_version"] == 1
    updated = _rows(sb, "catering_pricebook_updated")[-1]
    assert (updated["version"], updated["prev_version"]) == (6, 5)
    assert updated["updated_by"] == "menu_approval"
    assert _rows(sb, "catering_menu_pricebook_sync_failed") == []


def test_06_the_adapter_output_is_exactly_what_landed(sb: _Sandbox):
    """The document the importer received is reproducible from the approved menu
    and the pricebook that was live — nothing was added on the way through."""
    from schemas import Menu

    menu = Menu.model_validate(json.loads(sb.menu.read_text(encoding="utf-8")))
    archived_v5 = next(sb.pricebook_archive.glob("pricebook-v5-*.json"))
    prior = catering_pricing.load_pricebook(archived_v5)
    live = _active(sb)

    sync = catering_pricing.sync_pricebook_from_menu_items(
        menu.items, prior,
        effective_date=live.effective_date, updated_at=live.updated_at,
        source_menu_update_id=sb.update_id_take_2,
    )
    assert sync.next_version == live.version == 6
    # The importer assigns version/updated_at; everything else must match.
    assert sync.pricebook.model_dump(exclude={"version", "updated_at"}) == \
        live.model_dump(exclude={"version", "updated_at"})


def test_07_a_menu_priced_item_now_quotes_exact_to_the_cent(sb: _Sandbox):
    """Hand-computed independently of the kernel. Before this bridge these lines
    priced off the Menu at 'estimated'; the whole point is that they are now
    committed commercial numbers."""
    from schemas import Menu

    book = _active(sb)
    menu = Menu.model_validate(json.loads(sb.menu.read_text(encoding="utf-8")))

    computation = catering_pricing.compute_quote(
        10, None, [("Chai", 4), ("Idly (3 PCS)", 2)], None, book, menu,
    )

    items_subtotal = 4 * 250 + 2 * 600            # 1000 + 1200 = 2200
    fees_subtotal = 2500 + 5000                   # delivery + on-site setup, both flat
    subtotal = items_subtotal + fees_subtotal     # 9700
    tax = 800                                     # 9700 * 825 / 10000 = 800.25 -> 800
    assert (items_subtotal, fees_subtotal, subtotal) == (2200, 7500, 9700)

    assert computation.price_status == "exact"
    assert computation.is_deliverable() is True
    assert computation.flags == []
    assert [(ln.name, ln.unit_cents, ln.source) for ln in computation.lines] == [
        ("Chai", 250, "override"), ("Idly (3 PCS)", 600, "override"),
    ]
    assert computation.items_subtotal_cents == items_subtotal
    assert computation.fees_subtotal_cents == fees_subtotal
    assert computation.subtotal_cents == subtotal
    assert computation.tax_cents == tax
    assert computation.total_cents == subtotal + tax == 10500


def test_08_an_excluded_item_still_quotes_only_as_an_estimate(sb: _Sandbox):
    """The contrast that makes the exclusions safe rather than lossy: Market Fish
    Fry has no committed price, so it is not silently zero — it forces the whole
    quote to pending_owner_review."""
    from schemas import Menu

    book = _active(sb)
    menu = Menu.model_validate(json.loads(sb.menu.read_text(encoding="utf-8")))

    computation = catering_pricing.compute_quote(
        10, None, [("Market Fish Fry", 2)], None, book, menu,
    )
    assert computation.price_status == "pending_owner_review"
    assert computation.is_deliverable() is False
    assert "missing_price:Market Fish Fry" in computation.flags


def test_09_replaying_the_approval_creates_no_duplicate_version(sb: _Sandbox):
    """Two independent guards, both asserted: apply-menu-update consumed the
    pending file, and the importer refuses a document whose source_menu_update_id
    already produced the live pricebook."""
    before_bytes = sb.pricebook.read_bytes()
    before_updates = len(_rows(sb, "catering_pricebook_updated"))

    # (a) Replaying the owner's reply finds nothing to approve.
    rc, _out, err = _run_script(sb, "apply-menu-update", [
        "--code", sb.code_take_2, "--decision", "yes", "--sender-role", "owner",
    ])
    assert rc == 4, err
    assert sb.pricebook.read_bytes() == before_bytes

    # (b) And even a direct re-import of the same approval's document is a no-op.
    from schemas import Menu

    menu = Menu.model_validate(json.loads(sb.menu.read_text(encoding="utf-8")))
    sync = catering_pricing.sync_pricebook_from_menu_items(
        menu.items, _active(sb),
        effective_date=_active(sb).effective_date, updated_at=_active(sb).updated_at,
        source_menu_update_id=sb.update_id_take_2,
    )
    replay = sb.root / "replay.json"
    replay.write_text(sync.pricebook.model_dump_json(), encoding="utf-8")

    rc, out, err = _run_script(sb, "import-catering-pricebook", [
        "--file", str(replay), "--sender-role", "owner", "--updated-by", "menu_approval",
    ])
    assert rc == 0, (out, err)
    assert _stdout_json(out)["status"] == "already_active"
    assert sb.pricebook.read_bytes() == before_bytes
    assert len(_rows(sb, "catering_pricebook_updated")) == before_updates


# ════════════════════════════════════════════════════════════════════════════
# 10-12. Failure isolation, role gate, and the send assertion
# ════════════════════════════════════════════════════════════════════════════
def test_10_a_pricebook_failure_never_rolls_back_the_approved_menu(sb: _Sandbox):
    """The menu is the owner's decision and is final. If activation fails the
    pricebook stays byte-unchanged, the failure is audited, and the owner is
    paged — the one outcome that must never be silent."""
    result = _extract(sb, EXTRACTION_TAKE_2, "wamid.MENU.TAKE3")
    code = result["confirmation_code"]
    pricebook_before = sb.pricebook.read_bytes()
    menu_version_before = _active(sb).version

    rc, out, err = _run_script(
        sb, "apply-menu-update",
        ["--code", code, "--decision", "yes", "--sender-role", "owner"],
        # The importer binary is missing — exactly what a half-deployed box looks
        # like. Nothing else about the run changes.
        patches={"IMPORT_PRICEBOOK_BIN": sb.root / "no-such-importer"},
    )
    assert rc == 0, (out, err)
    payload = _stdout_json(out)
    assert payload["status"] == "applied"
    assert payload["pricebook_activated"] is False
    assert payload["new_version"] == 2, "the menu still advanced"

    assert json.loads(sb.menu.read_text(encoding="utf-8"))["version"] == 2
    assert sb.pricebook.read_bytes() == pricebook_before, "prices are byte-unchanged"
    assert _active(sb).version == menu_version_before

    failed = _rows(sb, "catering_menu_pricebook_sync_failed")[-1]
    assert failed["update_id"] == result["update_id"]
    assert failed["menu_version"] == 2
    # PRIVACY: the importer's stderr can quote a price, so it stays out of audit.
    assert "$" not in json.dumps(failed)
    assert "pricebook activation failed" in err


@pytest.mark.parametrize("role", ["employee", "customer", "unknown"])
def test_11_a_non_owner_can_neither_approve_nor_move_prices(sb: _Sandbox, role):
    """D-013 defense in depth, now guarding a money surface as well as the menu."""
    pricebook_before = sb.pricebook.read_bytes()
    menu_before = sb.menu.read_bytes()
    result = _extract(sb, EXTRACTION_TAKE_2, f"wamid.MENU.ROLE.{role}")

    rc, _out, err = _run_script(sb, "apply-menu-update", [
        "--code", result["confirmation_code"], "--decision", "yes",
        "--sender-role", role,
    ])
    assert rc == 12, err
    assert "privilege denied" in err
    assert sb.pricebook.read_bytes() == pricebook_before
    assert sb.menu.read_bytes() == menu_before


def test_12_the_whole_transcript_sent_nothing_to_a_customer(sb: _Sandbox):
    """A menu approval is an owner-side operation end to end. Any outbound here
    would be a customer being messaged about the owner's bookkeeping."""
    assert sb.sent == []
