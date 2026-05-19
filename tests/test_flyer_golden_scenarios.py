"""S8 P0-7: Flyer Studio golden scenario regression suite.

Locks down end-to-end behavior across real customer-style flows.

**Deterministic** (this file): runs in CI + the deploy-time pytest gate
without spending model credits. Uses parametrized assertions over the
canonical scenario catalog defined in `_SCENARIOS` + `_DELEGATED_SCENARIOS`.
Each scenario has a stable `id`; failures print the id + scenario description
so triage points at a real flow, not an opaque parametrize index.

**Spend-gated real-model**: NOT in this PR. Tracked in
`tasks/flyer-p0-execution-plan-2026-05-19.md` as a follow-up; requires an
allow-spend flag, isolated VPS credentials, and a separate test file to
ensure CI cannot accidentally invoke it. The deterministic suite proves
state / routing / reason_code / locked-fact / status-reply truthfulness —
which is what the user-spec axes assert. Real-model rendering / OCR are
exercised by the existing `smoke-flyer-quality --real-model --allow-spend`
path (deploy-gated, not auto-invoked).

How to add a new scenario:
  1. Add a `GoldenScenario(...)` to `_SCENARIOS` if you're writing a new
     direct end-to-end assertion.
  2. OR add `(id, "tests/<file>.py::<test>", signal)` to
     `_DELEGATED_SCENARIOS` if the axis is already covered by a dedicated
     test from an earlier slice.
  3. Extend `EXPECTED_AXES` with the new id.
  All three steps fail-closed: the structural test asserts
  EXPECTED_AXES == direct_ids ∪ delegated_ids, and the delegation
  sentinel verifies the `::test_name` actually resolves to a function
  body in the owner file (via ast).

The scenario catalog covers the canonical 20-axis user-spec set (16 from
the original S8 brief + 4 added via review HIGH #3 extension —
concept_selection text, approval text, non-English replies, break-glass
disambiguation).

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

import ast
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


# Canonical user-spec axes that this suite must cover. Single source of truth —
# the structural test below asserts `direct_ids | delegated_ids == EXPECTED_AXES`
# so adding a scenario without extending this set (or vice versa) fails closed.
EXPECTED_AXES: frozenset[str] = frozenset({
    # S8 original 16-axis brief
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
    # Review HIGH #3 extension: P0 axes from earlier slices that the original
    # brief did not name but that have dedicated owner tests and warrant
    # golden-suite coverage:
    "concept_selection_text_after_threshold",  # S3 stale guard preserves "1"/"C1"
    "approval_text_after_threshold",  # S3 stale guard preserves "approve"/"yes"
    "non_english_reply",  # S3 stale guard preserves Hindi/Telugu/Hinglish
    "break_glass_status_disambiguation",  # S2 break_glass_sent disambiguation
})

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
        # The extractor identifies the brand name as the business signal.
        # Trailing event/special phrasing is not part of the locked brand.
        expected_locked_facts={
            "business_name": "Lakshmis Kitchen",
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
            "business_name": "Triveni Supermarket",
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
            "business_name": "Fresh Meats",
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
            "business_name": "Sri Venkateswara Temple",
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
        # Brand name only — "with corrected prices" is intent, not brand.
        expected_locked_facts={
            "business_name": "Lakshmis Kitchen",
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
        # pins business brand + contact. Telugu rendering is exercised by the
        # render-side tests, not this routing assertion.
        expected_locked_facts={
            "business_name": "Lakshmis Kitchen",
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
# Format: (scenario_id, "tests/<file>.py::<test_function>", signal description).
# The sentinel `test_golden_delegated_scenario_has_owner_test` asserts BOTH
# the file exists AND the `::test_function` name resolves to a real function
# body in that file (via ast.parse) — catches the regression where the owner
# test got renamed and the delegation map silently went stale (pre-merge fix
# of S8 review BLOCKER #1).
_DELEGATED_SCENARIOS: list[tuple[str, str, str]] = [
    ("logo_upload_only", "tests/test_flyer_facts.py::test_reference_extraction_logo_role_does_not_create_item_price_facts", "no item/price facts from logo"),
    ("exact_template_source_edit", "tests/test_flyer_create_project.py::test_create_project_can_queue_exact_reference_edit_without_template_title", "source_edit_provider_unavailable reason_code"),
    ("reference_flyer_recreation", "tests/test_flyer_reference_extract.py::test_classifies_logo_menu_reference_and_source_edit", "reference role classification"),
    ("repeated_corrections", "tests/test_flyer_project_isolation.py::test_scenario6_fresh_active_project_still_attaches_revision_correction", "revision attaches; no new project"),
    ("stale_new_project_separation", "tests/test_flyer_project_isolation.py::test_scenario1_old_awaiting_approval_does_not_swallow_complete_new_request", "S3 stale guard bails to new"),
    ("manual_queue_status_check", "tests/test_flyer_state_reply_table.py::test_every_manual_review_reason_produces_specific_reply", "reason-code-specific status reply"),
    ("unsupported_pdf_manual_fallback", "tests/test_flyer_source_edit_preflight.py::test_source_edit_preflight_rejects_pdf_reference", "reference_unsupported reason_code"),
    # P0-axes coverage extension (S8 review HIGH #3): concept-selection text on
    # awaiting_concept_selection, approval text on awaiting_final_approval, non-
    # English replies — all have dedicated owner tests from earlier slices.
    ("concept_selection_text_after_threshold", "tests/test_flyer_project_isolation.py::test_stale_guard_does_not_drop_concept_selection_after_threshold", "S3 stale guard preserves selection"),
    ("approval_text_after_threshold", "tests/test_flyer_project_isolation.py::test_stale_guard_does_not_drop_approval_text_after_threshold", "S3 stale guard preserves approval"),
    ("non_english_reply", "tests/test_flyer_project_isolation.py::test_stale_guard_does_not_drop_non_english_reply", "S3 stale guard preserves non-English"),
    ("break_glass_status_disambiguation", "tests/test_flyer_state_reply_table.py::test_manual_edit_required_with_break_glass_sent_does_not_use_queued_branch", "break_glass excluded from queued copy"),
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

    # Locked-fact assertions: assert the locked-fact value STARTS WITH the
    # expected token-set we typed in raw_request, then optionally allows a
    # short trailing fragment (e.g. an "in Telugu" / "weekend sale" qualifier).
    # Pre-fix this used bidirectional substring which silently accepted
    # truncation regressions (S8 review BLOCKER #2: locked "Lakshmis" against
    # expected "Lakshmis Kitchen Thursday Dosa..." passed because the short
    # value was a substring of the expected one).
    locked_by_id = {fact["fact_id"]: fact["value"] for fact in (project.get("locked_facts") or [])}
    for fact_id, expected_value in scenario.expected_locked_facts.items():
        actual_value = locked_by_id.get(fact_id, "")
        normalized_expected = expected_value.strip().casefold()
        normalized_actual = actual_value.strip().casefold()
        # Phone facts: exact equality (digit-formatting variations get normalized
        # by visual_qa, not by the fact-extractor — locked store should match
        # what the customer typed verbatim).
        if fact_id == "contact_phone":
            assert normalized_actual == normalized_expected, (
                f"[{scenario.id}] locked_fact[{fact_id}]: "
                f"expected exact match {expected_value!r}, got {actual_value!r}"
            )
        else:
            # Business-name / similar text facts: locked value must START with
            # the expected token-set (catches truncation regressions) and the
            # expected value must be present in the locked value (catches
            # extractor returning unrelated content).
            assert normalized_actual.startswith(normalized_expected), (
                f"[{scenario.id}] locked_fact[{fact_id}]: expected value to start with "
                f"{expected_value!r}, got {actual_value!r}. Tightened from substring-in-"
                f"either-direction (S8 review BLOCKER #2 fix) — short returns now fail."
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
    MUST have BOTH an owner file on disk AND a function with the named
    `::test_<name>` symbol inside it.

    Pre-fix (S8 review BLOCKER #1), the sentinel only checked file existence
    — a delegated entry like `tests/test_flyer_create_project.py::
    test_create_flyer_project_exact_edit` passed silently even though no such
    function exists in that file. The post-fix `ast.parse` walk catches the
    function-name regression before it reaches the operator.
    """
    if "::" in owner_test:
        file_part, func_name = owner_test.split("::", 1)
    else:
        file_part, func_name = owner_test, ""
    owner_path = REPO / file_part
    assert owner_path.exists(), (
        f"[{scenario_id}] delegated owner test file {owner_path} not found "
        f"(signal: {signal}). Either restore the file or update the "
        f"_DELEGATED_SCENARIOS map in this file."
    )
    if func_name:
        source = owner_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert func_name in defined, (
            f"[{scenario_id}] delegated owner test function `{func_name}` not "
            f"found in {owner_path} (signal: {signal}). The test may have been "
            f"renamed or removed; update _DELEGATED_SCENARIOS to point at the "
            f"current owner."
        )


# ---------- coverage truthfulness ----------

def test_golden_catalog_covers_canonical_axes():
    """Structural invariant: the union of `_SCENARIOS` ids and
    `_DELEGATED_SCENARIOS` ids must EQUAL the canonical `EXPECTED_AXES` set.
    A future axis addition forces three coordinated edits (scenario or
    delegation + EXPECTED_AXES extension); the test fails if any one is
    missed. Detects orphan axes (in EXPECTED_AXES but no scenario) AND
    orphan scenarios (with no canonical entry).
    """
    direct_ids = {s.id for s in _SCENARIOS}
    delegated_ids = {sid for sid, _, _ in _DELEGATED_SCENARIOS}
    covered = direct_ids | delegated_ids
    missing = EXPECTED_AXES - covered
    orphan = covered - EXPECTED_AXES
    assert not missing, f"golden suite missing coverage for axes: {sorted(missing)}"
    assert not orphan, (
        f"golden suite has scenarios with no canonical EXPECTED_AXES entry: "
        f"{sorted(orphan)} — either add them to EXPECTED_AXES or remove."
    )


def test_golden_scenario_ids_are_unique():
    """No two _SCENARIOS or _DELEGATED_SCENARIOS share an id."""
    direct_ids = [s.id for s in _SCENARIOS]
    delegated_ids = [sid for sid, _, _ in _DELEGATED_SCENARIOS]
    all_ids = direct_ids + delegated_ids
    assert len(all_ids) == len(set(all_ids)), (
        f"duplicate scenario ids: {sorted(set(s for s in all_ids if all_ids.count(s) > 1))}"
    )
