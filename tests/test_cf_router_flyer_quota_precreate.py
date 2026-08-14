"""A quota-blocked flyer request must not leave a project row behind.

THE DEFECT. `_try_flyer_primary_intercept` created the project first and hit
the quota wall a few lines later. Nothing cleans the row up: both quota-block
returns from `_reserve_flyer_access_or_reply` yield `access == ""`, and
`_release_flyer_access` short-circuits on that with `no_access_to_release` —
there is no reservation to give back, so the naive "release on the block path"
fix is a no-op. The row simply stays.

WHY THAT IS A CATERING BUG. `has_non_delivered_flyer_project_by_sender` reads
projects.json with no staleness bound, and the catering-admission gate yields
to the LLM for every catering inquiry from a sender who has one. So a customer
who once hit the flyer paywall silently loses deterministic catering routing —
permanently, with no audit row saying so.

TWO FIXES, PINNED SEPARATELY because they fail independently:
  * the quota check runs BEFORE `trigger_create_flyer_project`, so a blocked
    request creates no row at all;
  * the CATERING-ADMISSION call site alone applies `is_stale_for_new_request`,
    so any row that does survive stops suppressing catering once it is stale.

The other two call sites of the broad predicate are deliberately untouched and
pinned as such — each needs its own review.

Dispatch-level, in the style of tests/test_cf_router_menu_caption_cession.py:
the unit under test is which arm claims the inbound and what state it leaves
behind, not what any arm renders.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PHONE = "+15550100077"
CHAT = "15550100077@lid"
FLYER_REQUEST = "make me a diwali sale flyer for saturday, 20% off all sweets"
CATERING_TEXT = "we need catering for 80 guests on June 20"

# The reply the account module renders for an exhausted trial. Byte-identical
# before and after the reorder is the contract — the customer must not be able
# to tell that the check moved.
QUOTA_REPLY = (
    "Flyer Studio\n------------\n"
    "Your free trial has used 3/3 sample flyers. "
    "Upgrade now to keep creating professional flyers: reply CHANGE PLAN "
    "STARTER, CHANGE PLAN GROWTH, or CHANGE PLAN UNLIMITED."
)


def _load_plugin():
    """Load hooks + actions as submodules of a synthetic package (the plugin dir
    name has a hyphen). Non-evicting: `schemas` / `safe_io` are left alone so
    co-resident flyer suites keep their bindings."""
    pkg = "cf_router_quota_precreate_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg or mod_name.startswith(pkg + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(pkg_spec)

    loaded = {}
    for name in ("actions", "hooks"):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        loaded[name] = mod
    return loaded["hooks"], loaded["actions"]


@pytest.fixture()
def plugin():
    return _load_plugin()


class _Spies:
    def __init__(self):
        self.audits: list[dict] = []
        self.sent: list[tuple] = []
        # Every project row this inbound caused. THE assertion surface: on a
        # quota-blocked request this list must stay empty.
        self.created: list[dict] = []
        self.reserve_calls: list[dict] = []
        self.check_calls: list[dict] = []

    @property
    def reasons(self) -> list[str]:
        return [a["reason"] for a in self.audits]


def _wire_flyer(monkeypatch, hooks_mod, actions_mod, *, quota_allowed: bool):
    """Arm the flyer primary arm for a returning ACTIVE customer whose next
    request either fits the plan or does not."""
    s = _Spies()

    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda _cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "find_flyer_customer_by_sender", lambda _p, _c: {
        "customer_id": "CUST0077", "status": "trial", "business_name": "Triveni",
    })
    monkeypatch.setattr(actions_mod, "is_vague_flyer_start", lambda *_a, **_kw: False)
    monkeypatch.setattr(actions_mod, "find_paid_flyer_guest_order", lambda _p, _c: None)
    monkeypatch.setattr(actions_mod, "find_reserved_flyer_guest_order",
                        lambda _p, _c, _pid: None)
    monkeypatch.setattr(actions_mod, "is_exact_reference_edit_request",
                        lambda *_a, **_kw: False)
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(
        actions_mod, "send_flyer_text",
        lambda cid, txt, **kw: s.sent.append((cid, txt, kw.get("action_context")))
        or (True, "mid-quota", ""))

    def _create(**kw):
        s.created.append(kw)
        project = {"project_id": "F0777", "status": "collecting_required_info"}
        return True, "created", project

    monkeypatch.setattr(actions_mod, "trigger_create_flyer_project", _create)
    monkeypatch.setattr(actions_mod, "flyer_project_has_manual_review_queued",
                        lambda _p: False)
    monkeypatch.setattr(actions_mod, "flyer_project_has_required_fields", lambda _p: True)

    def _reserve(*, customer_phone, project_id, message_id):
        s.reserve_calls.append({"project_id": project_id})
        if quota_allowed:
            return True, "reserved", {"quota_allowed": True}
        return True, "quota_blocked", {"quota_allowed": False, "reply_text": QUOTA_REPLY}

    monkeypatch.setattr(actions_mod, "trigger_flyer_reserve_quota", _reserve)

    # The read-only pre-create probe. Absent on unfixed code, which is what
    # makes the first cell red there rather than silently passing.
    def _check(*, customer_phone, message_id):
        s.check_calls.append({"phone": customer_phone})
        if quota_allowed:
            return True, "allowed", {"quota_allowed": True, "reply_text": ""}
        return True, "quota_blocked", {"quota_allowed": False, "reply_text": QUOTA_REPLY}

    if hasattr(actions_mod, "trigger_flyer_check_quota"):
        monkeypatch.setattr(actions_mod, "trigger_flyer_check_quota", _check)

    monkeypatch.setattr(actions_mod, "trigger_generate_flyer_concepts",
                        lambda _pid: (True, "generated"))
    monkeypatch.setattr(actions_mod, "send_flyer_processing_ack",
                        lambda *_a, **_kw: (True, "mid-proc", ""))
    return s


def _flyer_start(hooks_mod, text=FLYER_REQUEST):
    return hooks_mod._try_flyer_primary_intercept(
        text, CHAT,
        SimpleNamespace(text=text, chat_id=CHAT, message_id="wamid.QUOTA1"),
    )


# ── (a) a quota-blocked start creates NO project row ────────────────────────
def test_quota_blocked_start_creates_no_project_row(monkeypatch, plugin):
    """RED on unfixed code: the row exists because the create ran first.

    The row is the whole defect — nothing releases it, and it is what
    suppresses catering routing from then on.
    """
    hooks_mod, actions_mod = plugin
    s = _wire_flyer(monkeypatch, hooks_mod, actions_mod, quota_allowed=False)

    result = _flyer_start(hooks_mod)

    assert result is not None and result["action"] == "skip"
    assert s.created == [], (
        "a quota-blocked request created a flyer project row; nothing ever "
        "releases it and it silently disables catering routing for this sender")


def test_the_quota_reply_is_byte_identical_after_the_reorder(monkeypatch, plugin):
    """Moving the check earlier must be invisible to the customer."""
    hooks_mod, actions_mod = plugin
    s = _wire_flyer(monkeypatch, hooks_mod, actions_mod, quota_allowed=False)

    _flyer_start(hooks_mod)

    assert len(s.sent) == 1, "exactly one customer-visible reply"
    chat, body, ctx = s.sent[0]
    assert chat == CHAT
    assert body == QUOTA_REPLY, "the quota reply text changed"
    assert ctx.action_id == "flyer.quota.blocked"
    assert "flyer_quota_blocked" in s.reasons


def test_an_allowed_request_still_creates_the_project_and_reserves(monkeypatch, plugin):
    """The happy path is untouched: project created, quota reserved against it."""
    hooks_mod, actions_mod = plugin
    s = _wire_flyer(monkeypatch, hooks_mod, actions_mod, quota_allowed=True)

    result = _flyer_start(hooks_mod)

    assert result is not None
    assert len(s.created) == 1, "an allowed request must still create its project"
    assert [c["project_id"] for c in s.reserve_calls] == ["F0777"], (
        "the authoritative reservation must still bind to the real project id")


# ── the two paths the reorder DOES change, pinned deliberately ──────────────
# Both apply only to a customer who is ALREADY out of quota, and both used to
# end in "no flyer" regardless. The reorder changes WHICH reply they get and
# whether a row is left behind. Measured before/after on this branch:
#
#   manual-review-queued  before: project row + "queued for manual review" ack
#                         after : no row + the quota reply
#   intake-incomplete     before: project row + "I need a few more details..."
#                         after : no row + the quota reply
#
# These are the price of checking quota before the project exists: the two
# post-create branches that never consulted quota can no longer be reached by
# a blocked customer. Pinned so the change is visible and reversible, not
# discovered later from a funnel metric.
@pytest.mark.parametrize("manual_review,has_required,label", [
    (True, True, "manual-review-queued"),
    (False, False, "intake-incomplete"),
])
def test_the_reorder_short_circuits_the_two_post_create_branches(
        monkeypatch, plugin, manual_review, has_required, label):
    hooks_mod, actions_mod = plugin
    s = _wire_flyer(monkeypatch, hooks_mod, actions_mod, quota_allowed=False)
    monkeypatch.setattr(actions_mod, "flyer_project_has_manual_review_queued",
                        lambda _p: manual_review)
    monkeypatch.setattr(actions_mod, "flyer_project_has_required_fields",
                        lambda _p: has_required)
    monkeypatch.setattr(actions_mod, "send_flyer_manual_review_ack",
                        lambda *_a, **_kw: (True, "mid-manual", ""))

    _flyer_start(hooks_mod)

    assert s.created == [], f"{label}: a blocked customer still left a row behind"
    assert [body for _c, body, _x in s.sent] == [QUOTA_REPLY], (
        f"{label}: a blocked customer now gets the quota reply here, not the "
        "branch reply — this is the deliberate consequence of the reorder")


# ── (b)-(d) the catering-admission gate ─────────────────────────────────────
def _project(status: str, age_hours: float) -> dict:
    return {
        "project_id": "F0777",
        "status": status,
        "updated_at": (datetime.now(timezone.utc)
                       - timedelta(hours=age_hours)).isoformat().replace("+00:00", "Z"),
    }


def _wire_catering(monkeypatch, hooks_mod, actions_mod, project):
    """A catering inquiry from a sender who owns `project`."""
    s = _Spies()
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender",
                        lambda _cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender",
                        lambda _p, _c: project)
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: s.audits.append(kw))
    return s


def test_a_stale_orphan_row_no_longer_blocks_catering_admission(monkeypatch, plugin):
    """RED on unfixed code: the broad predicate has no staleness bound, so the
    orphan suppresses catering forever."""
    hooks_mod, actions_mod = plugin
    # 48h past the 2h intake threshold — the shape a quota-blocked orphan has.
    _wire_catering(monkeypatch, hooks_mod, actions_mod,
                   _project("collecting_required_info", age_hours=48))

    assert actions_mod.has_recent_non_delivered_flyer_project_by_sender(PHONE, CHAT) is False, (
        "a stale non-delivered project must stop suppressing catering routing")


def test_a_fresh_active_project_still_blocks_catering_admission(monkeypatch, plugin):
    """The regression guard: a genuinely live flyer conversation must still win
    the inbound, or a mid-flyer message gets read as a catering inquiry."""
    hooks_mod, actions_mod = plugin
    _wire_catering(monkeypatch, hooks_mod, actions_mod,
                   _project("collecting_required_info", age_hours=0.25))

    assert actions_mod.has_recent_non_delivered_flyer_project_by_sender(PHONE, CHAT) is True


def test_a_delivered_project_never_blocks_either_way(monkeypatch, plugin):
    hooks_mod, actions_mod = plugin
    _wire_catering(monkeypatch, hooks_mod, actions_mod,
                   _project("delivered", age_hours=0.25))

    assert actions_mod.has_recent_non_delivered_flyer_project_by_sender(PHONE, CHAT) is False
    assert actions_mod.has_non_delivered_flyer_project_by_sender(PHONE, CHAT) is False


# ── (e) the other two call sites keep the BROAD predicate ───────────────────
def test_the_broad_predicate_is_unchanged_for_its_other_callers(monkeypatch, plugin):
    """Only the catering-admission site is bounded by this PR.

    `has_non_delivered_flyer_project_by_sender` still answers "is there ANY
    non-delivered project", with no staleness bound, because its other two call
    sites were not reviewed here. If someone later bounds the broad predicate
    itself, this cell fails and sends them to review those two sites first.
    """
    hooks_mod, actions_mod = plugin
    _wire_catering(monkeypatch, hooks_mod, actions_mod,
                   _project("collecting_required_info", age_hours=48))

    assert actions_mod.has_non_delivered_flyer_project_by_sender(PHONE, CHAT) is True, (
        "the broad predicate must keep its unbounded meaning for its other callers")


def test_only_the_catering_admission_site_uses_the_bounded_predicate():
    """Pins WHICH call sites moved, by source inspection.

    The two other callers of the broad predicate (the flyer-active-project
    guard and the catering-acceptance arm) each need their own review; a silent
    swap there would change flyer routing, not just catering admission.
    """
    source = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    assert source.count("has_recent_non_delivered_flyer_project_by_sender") == 1, (
        "exactly one call site — the catering-admission gate — may use the "
        "staleness-bounded predicate")
    assert source.count("has_non_delivered_flyer_project_by_sender(") == 2, (
        "the other two broad-predicate call sites must stay untouched")
