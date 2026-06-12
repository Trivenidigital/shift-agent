from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SELF_EVAL = REPO / "tools" / "flyer-self-evaluation.py"

sys.path.insert(0, str(SRC))

from agents.flyer import customer_copy_policy as policy


def load_self_eval():
    spec = importlib.util.spec_from_file_location("flyer_self_eval_policy_test", SELF_EVAL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_self_eval_and_tests_use_same_customer_copy_policy_constants():
    module = load_self_eval()

    assert module.INTERNAL_COPY_TERMS == policy.BANNED_CUSTOMER_COPY_TERMS
    assert module.STATIC_COPY_SCAN_FUNCTIONS == policy.STATIC_CUSTOMER_COPY_FUNCTIONS


def test_customer_copy_policy_detects_project_ids_internal_terms_and_raw_echo():
    result = policy.scan_customer_text(
        "Project F-0065 was queued with provider=openrouter. Original customer request: make it red",
        raw_request="make it red",
    )

    categories = {hit.category for hit in result.hits}
    assert {"project_id", "internal_term", "raw_request_echo"} <= categories


def test_static_send_literal_scan_catches_hook_local_customer_copy():
    source = """
def helper(actions, chat_id):
    actions.send_flyer_text(
        chat_id,
        "Flyer Studio\\n------------\\nProject F0065 provider reason_code leaked",
    )
"""

    scanned = policy.extract_send_call_literals(source)
    assert "Project F0065 provider reason_code leaked" in scanned
    assert policy.scan_customer_text(scanned).hits


def test_static_send_literal_scan_catches_dynamic_project_id_copy():
    source = """
def helper(actions, chat_id, project_id):
    actions.send_flyer_text(
        chat_id,
        f"Flyer Studio\\n------------\\nProject {project_id} is ready.",
    )
"""

    scanned = policy.extract_send_call_literals(source)
    assert "Project {project_id} is ready" in scanned
    hits = policy.scan_customer_text(scanned).hits
    assert any(hit.category == "project_id" for hit in hits)


# ---- PR-γ 2026-05-26: forbidden completion verbs lint (measure mode) -------
# Pure additive peer to scan_customer_text. Does NOT block any send (no
# chokepoint hookup yet — deferred to PR-ζ). Existing replay tests asserting
# `not scan_customer_text(text).hits` for legitimate Flyer copy continue to
# pass because the new lint function is separate, not a wrapper.


def test_pr_gamma_forbidden_completion_verbs_list_complete():
    """Lock the canonical PR-γ verb list (17 verbs covering completion claims
    for billing/payment/account/schedule/delivery)."""
    expected = {
        "processed", "completed", "upgraded", "downgraded", "changed",
        "confirmed", "sent", "approved", "paid", "posted", "pushed",
        "applied", "scheduled", "booked", "cancelled", "canceled", "refunded",
    }
    assert set(policy.FORBIDDEN_COMPLETION_VERBS) == expected
    # Tuple, not set — preserves declared order for stable test iteration.
    assert isinstance(policy.FORBIDDEN_COMPLETION_VERBS, tuple)


def test_pr_gamma_lint_no_unverified_completion_catches_each_verb():
    """Each forbidden verb in isolation triggers a hit when
    has_verified_action_result=False (the default)."""
    for verb in policy.FORBIDDEN_COMPLETION_VERBS:
        text = f"I have {verb} your request."
        scan = policy.lint_no_unverified_completion(text)
        verbs_found = {hit.value for hit in scan.hits if hit.category == "unverified_completion_verb"}
        assert verb in verbs_found, f"verb {verb!r} must be detected in {text!r}"


def test_pr_gamma_lint_verified_action_result_suppresses_all_hits():
    """When has_verified_action_result=True, lint returns empty scan even if
    the text contains EVERY forbidden verb."""
    text = "I have processed, completed, upgraded, downgraded, changed, confirmed, sent, approved, paid, posted, pushed, applied, scheduled, booked, cancelled, canceled, refunded everything."
    scan = policy.lint_no_unverified_completion(text, has_verified_action_result=True)
    assert scan.hits == ()


def test_pr_gamma_lint_case_insensitive_and_word_boundary():
    """Verb detection is case-insensitive and word-boundary-anchored.
    'approveable' / 'approving' / 'sender' must NOT match because they are
    not the bare verb."""
    # Case-insensitive positive cases
    assert policy.lint_no_unverified_completion("Your request was PROCESSED").hits
    assert policy.lint_no_unverified_completion("plan Confirmed").hits
    assert policy.lint_no_unverified_completion("SENT").hits
    # Word-boundary negative cases — embedded substrings must not trigger
    # because `\b` only matches between word and non-word chars (underscore
    # is a word char in default Python regex).
    assert not policy.lint_no_unverified_completion("This is approveable").hits
    assert not policy.lint_no_unverified_completion("sender").hits
    assert not policy.lint_no_unverified_completion("pushed_to_qa").hits
    assert not policy.lint_no_unverified_completion("approvedbyowner").hits


def test_pr_gamma_lint_deduplicates_per_verb():
    """Repeated verbs in one text return only one hit per distinct verb."""
    text = "We sent your flyer. Your flyer was sent. We sent it again."
    scan = policy.lint_no_unverified_completion(text)
    sent_hits = [hit for hit in scan.hits if hit.value == "sent"]
    assert len(sent_hits) == 1
    assert sent_hits[0].category == "unverified_completion_verb"


def test_pr_gamma_lint_multiple_distinct_verbs():
    """Multiple distinct verbs in one text return one hit per verb."""
    text = "Your plan upgraded and the payment processed successfully."
    scan = policy.lint_no_unverified_completion(text)
    verbs_found = {hit.value for hit in scan.hits if hit.category == "unverified_completion_verb"}
    assert verbs_found == {"upgraded", "processed"}


def test_pr_gamma_lint_empty_or_no_verb_text_returns_empty():
    """Text with no forbidden verbs returns empty CustomerCopyScan."""
    assert policy.lint_no_unverified_completion("").hits == ()
    assert policy.lint_no_unverified_completion(None).hits == ()
    assert policy.lint_no_unverified_completion("Please review the design").hits == ()
    assert policy.lint_no_unverified_completion("Your flyer is ready").hits == ()
    # Word-boundary protection — "approve" alone is in the verb list, but
    # text without the verb in word-boundary form returns no hits
    assert policy.lint_no_unverified_completion("Send your first flyer request now.").hits == ()


def test_pr_gamma_lint_does_not_modify_scan_customer_text_behavior():
    """Regression check: PR-γ adds a NEW function and does NOT change
    `scan_customer_text` behavior. Existing replay tests (in test_flyer_*)
    assert `not scan_customer_text(text).hits` for legitimate Flyer copy
    containing words like 'sent' / 'scheduled' / 'applied'; modifying
    scan_customer_text would break them. This test locks that scan_customer_text
    does NOT flag forbidden completion verbs."""
    text = "Your flyer was sent. Your plan was upgraded. Payment confirmed."
    # scan_customer_text only checks BANNED_CUSTOMER_COPY_TERMS (internal-term
    # leakage), project IDs, and raw_request_echo. It does NOT check forbidden
    # completion verbs.
    scan = policy.scan_customer_text(text)
    verb_hits = [hit for hit in scan.hits if hit.category == "unverified_completion_verb"]
    assert verb_hits == []
    # The new lint function DOES flag them
    lint_scan = policy.lint_no_unverified_completion(text)
    lint_verbs = {hit.value for hit in lint_scan.hits if hit.category == "unverified_completion_verb"}
    assert lint_verbs == {"sent", "upgraded", "confirmed"}


def test_pr_gamma_lint_returns_customer_copy_scan_dataclass():
    """Return type contract: lint_no_unverified_completion returns a
    CustomerCopyScan (same dataclass as scan_customer_text) so future
    callers can polymorphically consume both."""
    scan = policy.lint_no_unverified_completion("Your flyer sent successfully")
    assert isinstance(scan, policy.CustomerCopyScan)
    assert scan.text == "Your flyer sent successfully"
    assert all(isinstance(hit, policy.CustomerCopyHit) for hit in scan.hits)
    # matched_values property works on the returned scan
    assert "sent" in scan.matched_values


# ─────────────────────────────────────────────────────────────────
# P0 #2 — warn-tier customer copy tests (Commit 2)
# Pure-function formatters: blockers + project -> string.
# All output must pass BOTH scan_customer_text AND lint_no_unverified_completion.
# ─────────────────────────────────────────────────────────────────


def _project_with_brand(brand: str = "Lakshmi's Kitchen") -> dict:
    """Dict-shaped project (matches cf-router's runtime call shape from
    _dispatch_concept_preview_send loading projects.json)."""
    return {
        "project_id": "F0108",
        "locked_facts": [{"fact_id": "business_name", "value": brand}],
    }


def _assert_lint_clean(text: str) -> None:
    scan = policy.scan_customer_text(text)
    assert scan.hits == (), f"scan_customer_text hits: {scan.hits}"
    lint = policy.lint_no_unverified_completion(text)
    assert lint.hits == (), f"lint_no_unverified_completion hits: {lint.hits}"


def test_warn_tier_draft_header_constant_lint_clean():
    _assert_lint_clean(policy.WARN_TIER_DRAFT_HEADER)


def test_format_warn_tier_correction_summary_brand_typo_only():
    """F0108 reproduction: single brand-typo blocker."""
    full, short = policy.format_warn_tier_correction_summary(
        ["visible wrong business/brand: Laksmi'S Kitchen"],
        _project_with_brand(),
    )
    assert "Lakshmi's Kitchen" in full
    assert "near the bottom" in full
    assert "near the bottom" in short


def test_format_warn_tier_correction_summary_missing_location_only():
    full, short = policy.format_warn_tier_correction_summary(
        ["missing required visible fact: location"],
        _project_with_brand(),
    )
    assert "location" in full
    assert "missing location" in short


def test_format_warn_tier_correction_summary_brand_plus_contact_info_severity_ordered():
    """Brand-identity outputs first regardless of input order."""
    full, _ = policy.format_warn_tier_correction_summary(
        [
            "missing required visible fact: contact_info",
            "visible wrong business/brand: Laksmi'S Kitchen",
        ],
        _project_with_brand(),
    )
    spelling_idx = full.find("spelling")
    contact_idx = full.find("contact")
    assert 0 <= spelling_idx < contact_idx, full


def test_format_warn_tier_correction_summary_clamps_to_top_2():
    blockers = [
        "missing required visible fact: contact_info",
        "missing required visible fact: location",
        "visible wrong business/brand: Laksmi'S Kitchen",
    ]
    full, _ = policy.format_warn_tier_correction_summary(blockers, _project_with_brand())
    assert "spelling" in full
    assert "location" in full
    assert "contact" not in full


def test_build_warn_tier_customer_text_f0108_lint_clean():
    text = policy.build_warn_tier_customer_text(
        ["visible wrong business/brand: Laksmi'S Kitchen"],
        _project_with_brand(),
    )
    _assert_lint_clean(text)


def test_build_warn_tier_customer_text_contains_short_summary_verbatim_in_ok_clause():
    """Reviewer 2 #3 refinement: the OK-confirm clause echoes the short
    summary verbatim so the customer can't dismiss without acknowledging
    what's wrong."""
    text = policy.build_warn_tier_customer_text(
        ["visible wrong business/brand: Laksmi'S Kitchen"],
        _project_with_brand(),
    )
    _, short = policy.format_warn_tier_correction_summary(
        ["visible wrong business/brand: Laksmi'S Kitchen"],
        _project_with_brand(),
    )
    assert short in text
    assert "Reply OK if you've checked" in text
    ok_line = next(line for line in text.splitlines() if line.startswith("Reply OK"))
    assert short in ok_line


def test_build_warn_tier_customer_text_contains_full_summary_in_body():
    text = policy.build_warn_tier_customer_text(
        ["missing required visible fact: location"],
        _project_with_brand(),
    )
    assert "location address" in text


def test_build_warn_tier_customer_text_degenerate_empty_blockers_lint_clean():
    text = policy.build_warn_tier_customer_text([], _project_with_brand())
    _assert_lint_clean(text)
    assert policy.WARN_TIER_DRAFT_HEADER in text


def test_build_warn_tier_customer_text_brand_missing_uses_placeholder():
    no_brand_project = {"project_id": "F0001", "locked_facts": []}
    text = policy.build_warn_tier_customer_text(
        ["visible wrong business/brand: Some Name"],
        no_brand_project,
    )
    _assert_lint_clean(text)
    assert "the business name" in text


def test_warn_tier_text_lint_clean_for_all_translation_table_entries():
    """Every blocker pattern in _WARN_BLOCKER_TRANSLATIONS must produce
    lint-clean output when rendered alone. Catches a regression where a
    new translation entry introduces a banned word."""
    sample_blockers = [
        "visible wrong business/brand: Laksmi'S Kitchen",
        "missing required visible fact: location",
        "missing required visible fact: schedule",
        "missing required visible fact: promotion_end",
        "missing required visible fact: item:3:name",
        "missing required visible fact: contact_info",
    ]
    for blocker in sample_blockers:
        text = policy.build_warn_tier_customer_text([blocker], _project_with_brand())
        scan = policy.scan_customer_text(text)
        lint = policy.lint_no_unverified_completion(text)
        assert scan.hits == (), f"blocker {blocker!r}: scan hits {scan.hits}"
        assert lint.hits == (), f"blocker {blocker!r}: lint hits {lint.hits}"


def test_format_warn_recovery_revision_ack_lint_clean():
    """Reviewer 2 #7 — warn-recovery revision ack must lint-clean against both."""
    ack = policy.format_warn_recovery_revision_ack(
        ["visible wrong business/brand: Laksmi'S Kitchen"],
        _project_with_brand(),
    )
    _assert_lint_clean(ack)


def test_format_warn_recovery_revision_ack_does_not_use_prior_draft_clean_phrasing():
    """The warn-recovery ack must NOT echo the existing revising_design copy
    ('Your requested changes are saved and the revised design is being
    prepared') — that phrasing presupposes the prior draft was clean."""
    ack = policy.format_warn_recovery_revision_ack([], _project_with_brand())
    lowered = ack.lower()
    assert "update" in lowered or "fix" in lowered
    assert "requested changes are saved" not in lowered


def test_warn_tier_formatters_do_not_mutate_inputs():
    """Pure-function invariant: formatters must not modify blockers or
    project. Defensive — if any formatter leaks state-write side effects,
    Hermes-as-brain invariant regression."""
    blockers = ["visible wrong business/brand: Laksmi'S Kitchen"]
    blockers_before = list(blockers)
    project = _project_with_brand()
    project_before = {
        "project_id": project["project_id"],
        "locked_facts": [dict(f) for f in project["locked_facts"]],
    }
    _ = policy.build_warn_tier_customer_text(blockers, project)
    _ = policy.format_warn_tier_correction_summary(blockers, project)
    _ = policy.format_warn_recovery_revision_ack(blockers, project)
    assert blockers == blockers_before
    assert project["project_id"] == project_before["project_id"]
    assert [dict(f) for f in project["locked_facts"]] == project_before["locked_facts"]


def test_build_preview_approval_checklist_summarizes_customer_facts_lint_clean():
    project = {
        "project_id": "F9001",
        "locked_facts": [
            {"fact_id": "business_name", "value": "Lakshmi's Kitchen"},
            {"fact_id": "campaign_title", "value": "Indo-Chinese Specials on Wednesday"},
            {"fact_id": "offer:0", "value": "Any item $9.99"},
            {"fact_id": "item:0:name", "value": "Veg Fried Rice"},
            {"fact_id": "item:0:price", "value": "$9.99"},
            {"fact_id": "item:1:name", "value": "Gobi Manchurian"},
            {"fact_id": "item:1:price", "value": "$9.99"},
            {"fact_id": "schedule", "value": "Wednesday"},
            {"fact_id": "location", "value": "90 Brybar Dr"},
            {"fact_id": "contact_phone", "value": "+1 980-200-5022"},
        ],
    }

    checklist = policy.build_preview_approval_checklist(project)

    _assert_lint_clean(checklist)
    assert checklist.startswith("Please check these details before approving:")
    assert "Business: Lakshmi's Kitchen" in checklist
    assert "Title: Indo-Chinese Specials on Wednesday" in checklist
    assert "Offer: Any item $9.99" in checklist
    assert "Items: Veg Fried Rice - $9.99; Gobi Manchurian - $9.99" in checklist
    assert "Schedule: Wednesday" in checklist
    assert "Contact: 90 Brybar Dr; +1 980-200-5022" in checklist
    assert "F9001" not in checklist


def test_build_preview_approval_checklist_clamps_items_and_length():
    project = {
        "locked_facts": [
            {"fact_id": "business_name", "value": "Lakshmi's Kitchen"},
            {"fact_id": "campaign_title", "value": "Weekend Menu " + ("with family specials " * 20)},
            {"fact_id": "offer:0", "value": "Grand opening offer " + ("all day savings " * 18)},
            {"fact_id": "promotion_end", "value": "June 30"},
            *[
                {"fact_id": f"item:{idx}:name", "value": f"Item {idx} " + ("special combo " * 8)}
                for idx in range(1, 9)
            ],
        ],
    }

    checklist = policy.build_preview_approval_checklist(project)

    assert "Item 1" in checklist
    assert "Item 4" in checklist
    assert "Item 5" not in checklist
    assert "+4 more" in checklist
    assert "Ends: June 30" in checklist
    assert len(checklist) <= 700


def test_build_preview_approval_checklist_preserves_ends_at_hard_budget():
    project = {
        "locked_facts": [
            {"fact_id": "business_name", "value": "B" * 200},
            {"fact_id": "campaign_title", "value": "T" * 200},
            {"fact_id": "offer:0", "value": "O" * 200},
            {"fact_id": "promotion_end", "value": "June 30"},
            {"fact_id": "location", "value": "L" * 200},
            {"fact_id": "contact_phone", "value": "+1 980-200-5022"},
            *[
                {"fact_id": f"item:{idx}:name", "value": f"Item {idx} " + ("X" * 180)}
                for idx in range(1, 9)
            ],
        ],
    }

    checklist = policy.build_preview_approval_checklist(project)

    assert len(checklist) <= 700
    assert "Items:" in checklist
    assert "Ends: June 30" in checklist


def test_build_preview_approval_checklist_includes_detail_and_offer_price_shapes():
    project = {
        "locked_facts": [
            {"fact_id": "business_name", "value": "Lakshmi's Kitchen"},
            {"fact_id": "campaign_title", "value": "Mid-night Biryani"},
            {"fact_id": "offer_price", "value": "$25.99"},
            {"fact_id": "detail_001", "label": "Item 1", "value": "Chicken biryani - $12.99"},
            {"fact_id": "detail_002", "label": "Item 2", "value": "Goat biryani - $14.99"},
        ],
    }

    checklist = policy.build_preview_approval_checklist(project)

    assert "Offer: $25.99" in checklist
    assert "Items: Chicken biryani - $12.99; Goat biryani - $14.99" in checklist


def test_build_preview_approval_checklist_returns_empty_without_customer_facts():
    assert policy.build_preview_approval_checklist({"project_id": "F9001"}) == ""


def test_format_warn_tier_correction_summary_accepts_pydantic_project_shape():
    """The formatter accepts BOTH dict-shape (cf-router runtime via
    projects.json) AND Pydantic FlyerProject-shape (generate-flyer-concepts
    call site). Smoke test for the Pydantic path."""
    from datetime import datetime, timezone
    from schemas import FlyerLockedFact, FlyerProject

    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    # Use generating_concepts here — Commit 2 branches off origin/main
    # which doesn't yet have Commit 1's delivered_with_warning Literal.
    # Formatter doesn't read project.status; any valid status works.
    project = FlyerProject(
        project_id="F0108",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-1",
        raw_request="Create flyer",
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name", label="Business",
                value="Lakshmi's Kitchen", source="customer_text", required=True,
            ),
        ],
    )
    full, short = policy.format_warn_tier_correction_summary(
        ["visible wrong business/brand: Laksmi'S Kitchen"],
        project,
    )
    assert "Lakshmi's Kitchen" in full
    assert "spelling" in short


def test_assumption_summary_line_surfaces_inferred_item_names():
    """Slice 4: the assumption line lists hermes_inferred item NAMES so the customer
    can revise them in one reply. Non-inferred facts and non-item-name facts are
    ignored; it is read-only (surfaces, never invents)."""
    from schemas import FlyerLockedFact
    facts = [
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Masala Dosa", source="hermes_inferred"),
        FlyerLockedFact(fact_id="item:1:name", label="Item", value="Idli", source="hermes_inferred"),
        FlyerLockedFact(fact_id="title", label="Title", value="Weekend Specials", source="customer_text"),
        FlyerLockedFact(fact_id="item:9:price", label="Price", value="$8.99", source="hermes_inferred"),
    ]
    line = policy.assumption_summary_line(facts)
    assert "Masala Dosa" in line and "Idli" in line
    assert "Weekend Specials" not in line  # not inferred
    assert "8.99" not in line  # inferred but not an item NAME
    assert "Reply to swap" in line


def test_assumption_summary_line_empty_when_no_inferred():
    """Dormant/default state and fully customer-specified or already-confirmed
    flyers ⇒ no line (empty string)."""
    from schemas import FlyerLockedFact
    assert policy.assumption_summary_line([]) == ""
    assert policy.assumption_summary_line([
        FlyerLockedFact(fact_id="title", label="Title", value="Specials", source="customer_text"),
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_confirmed"),
    ]) == ""
