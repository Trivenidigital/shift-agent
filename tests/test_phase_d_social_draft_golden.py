"""Phase D groundwork — golden pin for the offline GBP/IG draft prototype.

Pins tools/phase-d-prototype/generate_social_drafts.py output byte-for-byte
against the committed golden files for the checked-in real-row fixtures
(fixture COPIES — never the live store). Any template or vocabulary change
must regenerate the goldens deliberately, which is the review moment the copy
contract requires (leak law: vocabulary and forbidden-substrings change
together).

Fixture set (all real rows, pulled read-only 2026-07-06):
  DELIVERED (generator composes; golden = subprocess output):
    F0209  Morning Tiffin Deal — uniform price, campaign title present
    F0210  Lunch Thali Special — uniform price, campaign title present
    F0212  Idli Vada Morning Special — uniform price, campaign title present
    F0213  Late Night Biryani — non-uniform shape: NO campaign_title fact,
           bare-price pricing_structure ("$12.99"), a per-item price on the
           hero item. Stresses the two composer shapes the first goldens did
           not exercise (see the D3-gap assertions below).
  AWAITING (generator REFUSES by the delivered-gate; golden = direct compose):
    F0214  Diwali Sweets Box — occasion-bearing (occasion="diwali") + order-by
           schedule form. Real awaiting_final_approval row, so its golden is
           pinned via the pure compose_* functions (status-independent) and the
           delivered gate is asserted separately on the same real row.

Regenerate goldens (from the prototype dir):
  delivered: python generate_social_drafts.py --fixture fixtures/<ID>.json --out golden
  F0214:     compose_gbp_post(row) / compose_ig_caption(row) -> golden/F0214-*.txt

Spec: tasks/phase-d-flyer-to-gbp-spec.md
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROTOTYPE_DIR = Path(__file__).resolve().parents[1] / "tools" / "phase-d-prototype"
GENERATOR = PROTOTYPE_DIR / "generate_social_drafts.py"
FIXTURES_DIR = PROTOTYPE_DIR / "fixtures"
GOLDEN_DIR = PROTOTYPE_DIR / "golden"
DRAFT_SUFFIXES = ("gbp-post.txt", "ig-caption.txt")

# Real rows whose status == "delivered": the generator composes for these, so
# the golden is the generator's own byte output.
DELIVERED_IDS = ("F0209", "F0210", "F0212", "F0213")
# Real rows in a pre-delivery status: the generator's delivered-gate refuses
# them, so the golden is pinned from the pure composer functions and the gate
# refusal is asserted directly on the same row.
AWAITING_IDS = ("F0214",)
ALL_IDS = DELIVERED_IDS + AWAITING_IDS


def _load_module():
    spec = importlib.util.spec_from_file_location("phase_d_social_drafts", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_row(project_id):
    return json.loads((FIXTURES_DIR / f"{project_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("project_id", DELIVERED_IDS)
def test_generator_output_matches_golden(tmp_path, project_id):
    result = subprocess.run(
        [sys.executable, str(GENERATOR),
         "--fixture", str(FIXTURES_DIR / f"{project_id}.json"),
         "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for suffix in DRAFT_SUFFIXES:
        name = f"{project_id}-{suffix}"
        golden = (GOLDEN_DIR / name).read_bytes()
        assert (tmp_path / name).read_bytes() == golden, f"{name} drifted from golden"


@pytest.mark.parametrize("project_id", AWAITING_IDS)
def test_awaiting_row_golden_matches_direct_compose(project_id):
    """A pre-delivery row's golden is the pure composer output (the generator
    would refuse it). Pins the composer for occasion/order-by shapes without
    fabricating a delivered status on the real row."""
    module = _load_module()
    row = _fixture_row(project_id)
    composers = {
        "gbp-post.txt": module.compose_gbp_post,
        "ig-caption.txt": module.compose_ig_caption,
    }
    for suffix, compose in composers.items():
        golden = (GOLDEN_DIR / f"{project_id}-{suffix}").read_bytes()
        assert compose(row).encode("utf-8") == golden, f"{project_id}-{suffix} drifted"


@pytest.mark.parametrize("project_id", AWAITING_IDS)
def test_generator_refuses_real_awaiting_row(tmp_path, project_id):
    """The delivered-gate must refuse a real pre-delivery row — social drafts
    are only offered after finals are delivered (spec §3)."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR),
         "--fixture", str(FIXTURES_DIR / f"{project_id}.json"),
         "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "delivered" in (result.stdout + result.stderr).lower()
    for suffix in DRAFT_SUFFIXES:
        assert not (tmp_path / f"{project_id}-{suffix}").exists()


@pytest.mark.parametrize("project_id", ALL_IDS)
def test_golden_drafts_pass_copy_contract_screen(project_id):
    module = _load_module()
    row = _fixture_row(project_id)
    for suffix in DRAFT_SUFFIXES:
        text = (GOLDEN_DIR / f"{project_id}-{suffix}").read_text(encoding="utf-8")
        assert module.screen_draft(text, row) == []
        assert len(text) <= (
            module.GBP_POST_MAX_CHARS if suffix == "gbp-post.txt" else module.IG_CAPTION_MAX_CHARS
        )


def test_f0214_occasion_field_never_reaches_the_draft():
    """occasion is mood-only and parity-exempt (spec §2): it must contribute
    ZERO words to the copy. "Diwali" legitimately appears in F0214's drafts,
    but only because it is inside the locked campaign_title fact — not because
    the composer read the occasion field. Proven by clearing the field and
    getting byte-identical output."""
    module = _load_module()
    row = _fixture_row("F0214")
    assert row["occasion"] == "diwali"
    neutralized = dict(row)
    neutralized["occasion"] = "none"
    assert module.compose_gbp_post(neutralized) == module.compose_gbp_post(row)
    assert module.compose_ig_caption(neutralized) == module.compose_ig_caption(row)
    # And the only reason "diwali" appears at all is the campaign_title fact.
    assert "Diwali" in row_fact(row, "campaign_title")


def test_f0213_non_uniform_price_shape_is_lossy_but_contract_clean():
    """F0213 stresses two shapes the uniform-price goldens do not, and pins the
    CURRENT (lossy) composer behavior so a future fix regenerates the golden as
    a deliberate review moment. Documented D3 work items (do not hack the
    prototype here):
      D3-gap-1: no campaign_title fact -> the headline degrades to the bare
                business name (no offer headline in the post).
      D3-gap-2: per-item prices are dropped (the Menu line is names-only), so a
                non-uniform menu loses its per-item pricing; only the single
                pricing_structure value survives, and here it is a bare "$12.99"
                with no noun.
    Both are fact-safe by omission — screen_draft stays clean — but a v0 upgrade
    should surface item prices and a nounless price gracefully."""
    module = _load_module()
    row = _fixture_row("F0213")
    post = module.compose_gbp_post(row)
    # Contract holds despite the lossy shape.
    assert module.screen_draft(post, row) == []
    # D3-gap-1: headline is the bare business name (campaign_title absent).
    assert row_fact(row, "campaign_title") == ""
    assert post.splitlines()[0] == "Lakshmi's Kitchen"
    # D3-gap-2: the Menu line carries item names only — no per-item price column.
    menu_line = next(line for line in post.splitlines() if line.startswith("Menu:"))
    assert "Chicken Biryani" in menu_line and "$" not in menu_line
    # The hero's own per-item price is present in the facts but not the draft
    # beyond the single pricing_structure line.
    assert row_fact(row, "item:0:price") == "$12.99"
    assert post.count("$12.99") == 1


def test_screen_catches_jargon_claims_and_non_fact_words():
    module = _load_module()
    row = _fixture_row("F0210")
    clean = module.compose_gbp_post(row)
    assert module.screen_draft(clean, row) == []
    assert any(v.startswith("jargon:") for v in module.screen_draft(clean + "\noperator note", row))
    assert any(v.startswith("claim:") for v in module.screen_draft(clean + "\nbest in town", row))
    assert any(
        v.startswith("non_fact_word:")
        for v in module.screen_draft(clean + "\ncome hungry", row)
    )


def test_generator_refuses_non_delivered_projects(tmp_path):
    module = _load_module()
    row = _fixture_row("F0210")
    row["status"] = "awaiting_final_approval"
    fixture = tmp_path / "not-delivered.json"
    fixture.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(SystemExit):
        module.generate(fixture, tmp_path)


def test_forbidden_lists_are_authored_with_the_vocabulary():
    module = _load_module()
    assert module.ALLOWED_CONNECTIVES
    assert module.FORBIDDEN_SUBSTRINGS_JARGON
    assert module.FORBIDDEN_SUBSTRINGS_CLAIMS
    overlap = {
        term for term in module.FORBIDDEN_SUBSTRINGS_JARGON + module.FORBIDDEN_SUBSTRINGS_CLAIMS
    } & module.ALLOWED_CONNECTIVES
    assert not overlap, f"connective doubles as forbidden term: {overlap}"


def row_fact(row, fact_id):
    for fact in row.get("locked_facts") or []:
        if fact.get("fact_id") == fact_id:
            return " ".join(str(fact.get("value") or "").split())
    return ""
