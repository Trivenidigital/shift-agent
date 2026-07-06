"""Phase D groundwork — golden pin for the offline GBP/IG draft prototype.

Pins tools/phase-d-prototype/generate_social_drafts.py output byte-for-byte
against the committed golden files for the two real-row fixtures (F0210,
F0212, fixture copies — never the live store). Any template or vocabulary
change must regenerate the goldens deliberately, which is the review moment
the copy contract requires (leak law: vocabulary and forbidden-substrings
change together).

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
PROJECT_IDS = ("F0210", "F0212")


def _load_module():
    spec = importlib.util.spec_from_file_location("phase_d_social_drafts", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("project_id", PROJECT_IDS)
def test_generator_output_matches_golden(tmp_path, project_id):
    result = subprocess.run(
        [sys.executable, str(GENERATOR),
         "--fixture", str(PROTOTYPE_DIR / "fixtures" / f"{project_id}.json"),
         "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for suffix in ("gbp-post.txt", "ig-caption.txt"):
        name = f"{project_id}-{suffix}"
        golden = (PROTOTYPE_DIR / "golden" / name).read_bytes()
        assert (tmp_path / name).read_bytes() == golden, f"{name} drifted from golden"


@pytest.mark.parametrize("project_id", PROJECT_IDS)
def test_golden_drafts_pass_copy_contract_screen(project_id):
    module = _load_module()
    row = json.loads(
        (PROTOTYPE_DIR / "fixtures" / f"{project_id}.json").read_text(encoding="utf-8")
    )
    for suffix in ("gbp-post.txt", "ig-caption.txt"):
        text = (PROTOTYPE_DIR / "golden" / f"{project_id}-{suffix}").read_text(encoding="utf-8")
        assert module.screen_draft(text, row) == []
        assert len(text) <= (
            module.GBP_POST_MAX_CHARS if suffix == "gbp-post.txt" else module.IG_CAPTION_MAX_CHARS
        )


def test_screen_catches_jargon_claims_and_non_fact_words():
    module = _load_module()
    row = json.loads((PROTOTYPE_DIR / "fixtures" / "F0210.json").read_text(encoding="utf-8"))
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
    row = json.loads((PROTOTYPE_DIR / "fixtures" / "F0210.json").read_text(encoding="utf-8"))
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
