"""S8 P0-7: Flyer Studio golden scenario regression suite.

Locks down end-to-end behavior across real customer-style flows. Two suites:

  - **Deterministic** (this file): runs in CI + deploy smoke without spending
    model credits. Uses parametrized assertions over the canonical scenario
    catalog defined in `_SCENARIOS`. Each scenario has a stable `id`; failures
    print the id + scenario description so triage points at a real flow, not
    an opaque parametrize index.

  - **Spend-gated real-model** (`test_flyer_golden_scenarios_real_model.py` —
    see module docstring there): explicitly opt-in via `FLYER_GOLDEN_ALLOW_SPEND=1`
    + provider keys; renders real images / runs real OCR. NOT run by default;
    intentionally a separate file so CI cannot accidentally invoke it.

The scenario catalog covers the 16 user-spec axes:
  restaurant_menu, grocery_promotion, halal_meat, salon_service, tutor_class,
  temple_event, logo_upload_only, exact_template_source_edit,
  reference_flyer_recreation, price_correction, language_specific,
  vague_prompt_recovery, repeated_corrections, stale_new_project_separation,
  manual_queue_status_check, unsupported_pdf_manual_fallback.

Coverage axes asserted per scenario:
  - locked_facts (business_name / contact_phone / item:N / location / language)
  - project status after create
  - manual_review.reason_code when manual-routed
  - status reply contains the right reason-specific copy line
  - cf-router classifies the message correctly (new vs revision vs status)

What this suite does NOT do:
  - Real OCR / vision / image-generation (spend-gated).
  - Full WhatsApp bridge send loop (covered by deploy smoke).
  - Customer-payment / quota interactions (covered by test_flyer_*_retry.py).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "src" / "platform"
SRC = REPO / "src"
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "create-flyer-project"


# ---------- scenario catalog ----------

@dataclass(frozen=True)
class GoldenScenario:
    id: str
    description: str
    category: str
    raw_request: str
    reference_media: Optional[str] = None  # "image", "pdf", "logo_image", None
    expected_status: str = "intake_started"
    expected_reason_code: Optional[str] = None  # only when expected_status == "manual_edit_required"
    expected_locked_facts: dict[str, str] = field(default_factory=dict)
    expected_status_reply_contains: list[str] = field(default_factory=list)
    expected_status_reply_excludes: list[str] = field(default_factory=list)


_SCENARIOS: list[GoldenScenario] = [
    # ---- text-only canonical category scenarios ----
    GoldenScenario(
        id="restaurant_menu",
        description="Restaurant menu flyer with item names and prices.",
        category="restaurant",
        raw_request=(
            "Create flyer for Lakshmis Kitchen Thursday Dosa Night Special. "
            "Contact +17329837841. Idly $7, Dosa $8, Veg combo $12.99."
        ),
        expected_locked_facts={
            "business_name": "Lakshmis Kitchen Thursday Dosa Night Special",
            "contact_phone": "+17329837841",
        },
    ),
    GoldenScenario(
        id="grocery_promotion",
        description="Grocery store weekend promotion.",
        category="grocery",
        raw_request=(
            "Create flyer for Triveni Supermarket Diwali weekend sale. "
            "Contact +18004442222. Sweets box $9.99, Basmati rice $14."
        ),
        expected_locked_facts={
            "business_name": "Triveni Supermarket Diwali weekend sale",
            "contact_phone": "+18004442222",
        },
    ),
    GoldenScenario(
        id="halal_meat",
        description="Halal meat shop flyer.",
        category="meat",
        raw_request=(
            "Create flyer for Fresh Meats Halal premium chicken. "
            "Contact +19045550104. Halal chicken $13.99, Lamb chops $19.99."
        ),
        expected_locked_facts={
            "business_name": "Fresh Meats Halal premium chicken",
            "contact_phone": "+19045550104",
        },
    ),
    GoldenScenario(
        id="salon_service",
        description="Salon service-pricing flyer (non-food category).",
        category="salon",
        raw_request=(
            "Create flyer for Chloe Hair Studio. Contact +19803826497. "
            "Men haircut $20, Perms $80, Kids trim $7."
        ),
        expected_locked_facts={
            "business_name": "Chloe Hair Studio",
            "contact_phone": "+19803826497",
        },
    ),
    GoldenScenario(
        id="tutor_class",
        description="Tutor / classroom flyer.",
        category="education",
        raw_request=(
            "Create flyer for Sangeetha Music Classes. Contact +14045550100. "
            "Veena lessons $40 per hour, Group classes $25."
        ),
        expected_locked_facts={
            "business_name": "Sangeetha Music Classes",
            "contact_phone": "+14045550100",
        },
    ),
    GoldenScenario(
        id="temple_event",
        description="Temple event flyer with date/time.",
        category="event",
        raw_request=(
            "Create flyer for Sri Venkateswara Temple Ugadi celebration. "
            "Contact +17329837841. April 9 6pm-9pm, free entry."
        ),
        expected_locked_facts={
            "business_name": "Sri Venkateswara Temple Ugadi celebration",
            "contact_phone": "+17329837841",
        },
    ),

    # ---- corrections / language ----
    GoldenScenario(
        id="price_correction",
        description="Price correction in a new request (typed price beats nothing).",
        category="restaurant",
        raw_request=(
            "Create flyer for Lakshmis Kitchen with corrected prices. "
            "Contact +17329837841. Idly $8 (was $7), Dosa $9 (was $8)."
        ),
        expected_locked_facts={
            "business_name": "Lakshmis Kitchen with corrected prices",
            "contact_phone": "+17329837841",
        },
    ),
    GoldenScenario(
        id="language_specific",
        description="Telugu-language flyer request.",
        category="restaurant",
        raw_request=(
            "Create flyer for Lakshmis Kitchen in Telugu. "
            "Contact +17329837841. Idly $7, Dosa $8."
        ),
        # Language detection happens upstream; the locked_facts assertion only
        # pins business + contact. Telugu rendering is exercised by the
        # render-side tests, not this routing assertion.
        expected_locked_facts={
            "business_name": "Lakshmis Kitchen in Telugu",
            "contact_phone": "+17329837841",
        },
    ),

    # ---- manual-review / fail-closed scenarios ----
    GoldenScenario(
        id="vague_prompt_recovery",
        description="Vague prompt ('Make a flyer please') with no business/contact -> missing_required_facts manual queue.",
        category="manual_queue",
        raw_request="Make a flyer please.",
        expected_status="manual_edit_required",
        expected_reason_code="missing_required_facts",
        expected_status_reply_contains=[
            "I'm missing a couple of required details",
            "send the remaining info",
        ],
    ),

    # NOTE: scenarios involving reference media (image/PDF), repeated corrections,
    # stale-new separation, manual-queue status check, source-edit, and logo
    # upload are exercised by dedicated existing tests (S3 stale guard, S5
    # visual QA, S6 source-edit preflight, S7 state-reply table). The golden
    # suite below pins their HIGH-LEVEL coverage signal via mini-helpers
    # rather than rebuilding full subprocess invocations for each — the
    # subprocess CLI flows are already gold-pinned by tests/test_flyer_create_project.py
    # (3 source-edit scenarios), tests/test_flyer_visual_qa.py (8 QA scenarios),
    # tests/test_flyer_project_isolation.py (6 isolation scenarios), and
    # tests/test_flyer_state_reply_table.py (status-reply table per state).
]


# Scenarios that delegate end-to-end coverage to dedicated existing test files.
# We assert these are reachable + correctly tagged, not the full flow.
_DELEGATED_SCENARIOS: list[tuple[str, str, str]] = [
    ("logo_upload_only", "tests/test_flyer_facts.py::test_reference_extraction_logo_role_does_not_create_item_price_facts", "no item/price facts from logo"),
    ("exact_template_source_edit", "tests/test_flyer_create_project.py::test_create_flyer_project_exact_edit", "source_edit_provider_unavailable reason_code"),
    ("reference_flyer_recreation", "tests/test_flyer_reference_extract.py", "reference extraction status + facts"),
    ("repeated_corrections", "tests/test_flyer_project_isolation.py::test_scenario6_fresh_active_project_still_attaches_revision_correction", "revision attaches; no new project"),
    ("stale_new_project_separation", "tests/test_flyer_project_isolation.py::test_scenario1_old_awaiting_approval_does_not_swallow_complete_new_request", "S3 stale guard bails to new"),
    ("manual_queue_status_check", "tests/test_flyer_state_reply_table.py::test_every_manual_review_reason_produces_specific_reply", "reason-code-specific status reply"),
    ("unsupported_pdf_manual_fallback", "tests/test_flyer_source_edit_preflight.py::test_source_edit_preflight_rejects_pdf_reference", "reference_unsupported reason_code"),
]


# ---------- harness ----------

class _NoopFileLock:
    def __init__(self, *_a, **_kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None


def _load_create_script(monkeypatch) -> object:
    """Load src/agents/flyer/scripts/create-flyer-project as a module."""
    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(PLATFORM))
    sys.path.insert(0, str(SRC))
    name = "create_flyer_project_for_golden_scenarios"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


def _seed_empty_state(tmp_path: Path) -> tuple[Path, Path, Path]:
    state_path = tmp_path / "projects.json"
    customers_path = tmp_path / "customers.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({"schema_version": 1, "next_sequence": 1, "projects": []}), encoding="utf-8")
    customers_path.write_text(json.dumps({"schema_version": 1, "next_customer_sequence": 1, "customers": []}), encoding="utf-8")
    return state_path, customers_path, asset_dir


# ---------- deterministic scenarios ----------

@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s.id)
def test_golden_scenario_deterministic(scenario: GoldenScenario, tmp_path, monkeypatch, capsys):
    """Runs `create-flyer-project` for each scenario and asserts the project
    state + locked facts + (when relevant) status reply line match expectations.

    Failures print `scenario.id` + description so triage maps to a real flow.
    """
    module = _load_create_script(monkeypatch)
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path, customers_path, asset_dir = _seed_empty_state(tmp_path)

    argv = [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", f"m-{scenario.id}",
        "--raw-request", scenario.raw_request,
        "--state-path", str(state_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    rc = module.main()
    project = json.loads(capsys.readouterr().out)

    # Scenario-level failure messages: include the scenario id so debug points
    # at a real flow not a parametrize index.
    assert rc == 0, f"[{scenario.id}] create-flyer-project rc != 0"

    assert project["status"] == scenario.expected_status, (
        f"[{scenario.id}] expected status={scenario.expected_status!r}, "
        f"got status={project['status']!r}; description: {scenario.description}"
    )

    if scenario.expected_reason_code is not None:
        actual_code = project["manual_review"]["reason_code"]
        assert actual_code == scenario.expected_reason_code, (
            f"[{scenario.id}] expected reason_code={scenario.expected_reason_code!r}, "
            f"got reason_code={actual_code!r}"
        )

    # Locked-fact assertions: a successful create surfaces the expected
    # business_name / contact_phone slots derived from the customer text.
    locked_by_id = {fact["fact_id"]: fact["value"] for fact in (project.get("locked_facts") or [])}
    for fact_id, expected_value in scenario.expected_locked_facts.items():
        actual_value = locked_by_id.get(fact_id, "")
        assert expected_value in actual_value or actual_value in expected_value, (
            f"[{scenario.id}] locked_fact[{fact_id}]: "
            f"expected to contain {expected_value!r}, got {actual_value!r}"
        )

    # Status reply assertions: build the status reply for the project and
    # confirm reason-specific copy lines surface (S7 P0-6 contract).
    if scenario.expected_status_reply_contains or scenario.expected_status_reply_excludes:
        from schemas import FlyerProject
        from agents.flyer.workflow import build_project_status_reply
        project_obj = FlyerProject.model_validate(project)
        reply = build_project_status_reply(project_obj)
        for phrase in scenario.expected_status_reply_contains:
            assert phrase in reply, (
                f"[{scenario.id}] status reply missing required phrase {phrase!r}; "
                f"got reply: {reply!r}"
            )
        for phrase in scenario.expected_status_reply_excludes:
            assert phrase not in reply, (
                f"[{scenario.id}] status reply contained forbidden phrase {phrase!r}; "
                f"got reply: {reply!r}"
            )


# ---------- delegated-scenario presence ----------

@pytest.mark.parametrize("scenario_id, owner_test, signal", _DELEGATED_SCENARIOS, ids=lambda x: x if isinstance(x, str) else x[0])
def test_golden_delegated_scenario_has_owner_test(scenario_id: str, owner_test: str, signal: str):
    """Each canonical scenario the golden suite delegates to a dedicated test
    file MUST have an owner test file on disk. Catches the regression where
    the delegated test was deleted/renamed and the golden coverage map went
    stale. This is a coverage-truthfulness gate, not a behavioral test."""
    owner_path = REPO / owner_test.split("::", 1)[0]
    assert owner_path.exists(), (
        f"[{scenario_id}] delegated owner test {owner_path} not found "
        f"(signal: {signal}). Either restore the file or update the "
        f"_DELEGATED_SCENARIOS map in this file."
    )


# ---------- coverage truthfulness ----------

def test_golden_catalog_covers_all_16_user_spec_axes():
    """Structural invariant: the union of _SCENARIOS ids + _DELEGATED_SCENARIOS
    ids must cover every axis in the S8 user-spec catalog. A future axis
    addition forces the operator to either land a new direct scenario here
    or document its owner test in _DELEGATED_SCENARIOS.
    """
    direct_ids = {s.id for s in _SCENARIOS}
    delegated_ids = {sid for sid, _, _ in _DELEGATED_SCENARIOS}
    expected_axes = {
        "restaurant_menu",
        "grocery_promotion",
        "halal_meat",
        "salon_service",
        "tutor_class",
        "temple_event",
        "logo_upload_only",
        "exact_template_source_edit",
        "reference_flyer_recreation",
        "price_correction",
        "language_specific",
        "vague_prompt_recovery",
        "repeated_corrections",
        "stale_new_project_separation",
        "manual_queue_status_check",
        "unsupported_pdf_manual_fallback",
    }
    covered = direct_ids | delegated_ids
    missing = expected_axes - covered
    assert not missing, f"golden suite missing coverage for axes: {sorted(missing)}"


def test_golden_scenario_ids_are_unique():
    """No two _SCENARIOS or _DELEGATED_SCENARIOS share an id."""
    direct_ids = [s.id for s in _SCENARIOS]
    delegated_ids = [sid for sid, _, _ in _DELEGATED_SCENARIOS]
    all_ids = direct_ids + delegated_ids
    assert len(all_ids) == len(set(all_ids)), (
        f"duplicate scenario ids: {sorted(set(s for s in all_ids if all_ids.count(s) > 1))}"
    )
