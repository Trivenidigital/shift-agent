"""The vision request reads visible spelling; it does not edit flyer copy."""
import json
from types import SimpleNamespace

import pytest

from agents.flyer import visual_qa as qa


@pytest.mark.parametrize("visible", ["MysoorePak", "MysorePak"])
def test_vision_request_demands_literal_spelling_without_expected_copy(tmp_path, monkeypatch, visible):
    artifact = tmp_path / "synthetic.png"
    artifact.write_bytes(b"synthetic-image")
    monkeypatch.setattr(qa, "_openrouter_key", lambda: "test-key")
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": json.dumps({
                "extracted_text": visible, "quality_notes": []})}}]}).encode()

    def urlopen(request, **_):
        captured.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(qa.urllib.request, "urlopen", urlopen)
    text, provider, source, notes = qa._vision_text(artifact)
    assert (text, provider, source, notes) == (visible, "openrouter", "ocr_vision", [])
    content = captured[0]["messages"][0]["content"]
    prompt = content[0]["text"]
    assert "Do not correct spelling" in prompt
    assert "transliterations" in prompt
    assert "unreadable" in prompt
    assert "MysoorePak" not in prompt and "MysorePak" not in prompt
    assert content[1]["image_url"]["detail"] == "high"


def test_literal_ocr_does_not_relax_item_comparison():
    project = SimpleNamespace(locked_facts=[SimpleNamespace(fact_id="item:0:name", value="Mysoore Pak")])
    assert qa._near_duplicate_item_blockers(project, "Mysoore Pak") == []
    assert qa._near_duplicate_item_blockers(project, "Mysore Pak") == [
        "near-duplicate item visible: expected Mysoore Pak but saw Mysore Pak"]
    assert qa._near_duplicate_item_blockers(project, "Mysoore Pak\nMysoore Pak") == [
        "duplicate item visible: Mysoore Pak"]


def _reread_project():
    from datetime import datetime, timezone
    from schemas import FlyerProject, FlyerLockedFact
    now = datetime.now(timezone.utc)
    return FlyerProject(project_id="F9002", status="generating_concepts",
        customer_phone="+15550100001", created_at=now, updated_at=now,
        original_message_id="ocr-test", raw_request="Snack menu",
        locked_facts=[FlyerLockedFact(fact_id=k, label=k, value=v, source="customer_text", required=True)
                      for k, v in [("item:0:name", "Mysoore Pak"), ("item:0:price", "$4.99"),
                                   ("contact_phone", "+15550100001"), ("pricing_structure", "$4.99")]])


@pytest.mark.parametrize("secondary, expected", [
    ("Mysoore Pak $4.99\n+15550100001", None),
    ("Mysore Pak\n$4.99\n+15550100001", "near-duplicate item"),
    ("Mysoore Pak\nMysoore Pak\n$4.99\n+15550100001", "duplicate item"),
    ("Mysoore Pak $9.99\n+15550100001", "price"),
    ("Mysoore Pak $4.99\n+15550100001\n+15550999999", "phone"),
    ("", "near-duplicate item"),
])
def test_real_ocr_reread_runs_all_strict_checks(tmp_path, monkeypatch, secondary, expected):
    artifact = tmp_path / "image.png"
    artifact.write_bytes(b"image")
    calls = []
    primary = "Mysore Pak\n$4.99\n+15550100001"
    def vision(path, *, model):
        calls.append(model)
        text = primary if len(calls) == 1 else secondary
        return text, "openrouter" if text else "unavailable", "ocr_vision", []
    monkeypatch.setattr(qa, "_vision_text", vision)
    report = qa.run_visual_qa(_reread_project(), artifact_path=artifact, output_format="portrait", allow_sidecar=False)
    assert calls == [qa.VISION_QA_MODEL, qa.REGIONAL_QA_MODEL]
    assert report.extracted_text == (secondary or primary)
    assert any(qa.REGIONAL_QA_MODEL in note for note in report.warnings)
    if expected:
        assert report.status == "failed"
        assert any(expected in blocker for blocker in report.blockers), report.blockers
    else:
        assert report.status == "passed", report.blockers


@pytest.mark.parametrize("mode", ["clean", "sidecar", "regional"])
def test_reread_never_runs_for_clean_sidecar_or_regional(tmp_path, monkeypatch, mode):
    artifact = tmp_path / "image.png"
    artifact.write_bytes(b"image")
    calls = []
    text = "Mysoore Pak $4.99\n+15550100001" if mode == "clean" else "Mysore Pak $4.99\n+15550100001"
    def vision(path, *, model):
        calls.append(model)
        return text, "openrouter", "ocr_vision", []
    monkeypatch.setattr(qa, "_vision_text", vision)
    monkeypatch.setattr(qa, "_project_is_regional", lambda _: mode == "regional")
    if mode == "sidecar":
        artifact.with_suffix(".png.ocr.txt").write_text(text)
    qa.run_visual_qa(_reread_project(), artifact_path=artifact, output_format="portrait", allow_sidecar=mode == "sidecar")
    assert calls == ([] if mode == "sidecar" else [qa.REGIONAL_QA_MODEL if mode == "regional" else qa.VISION_QA_MODEL])



@pytest.mark.parametrize("adverse", ["\n$9.99", "\n+15550999999", "\nMysoore Pak\nMysoore Pak", "\nMysore Pak"])
def test_reread_cannot_erase_primary_adverse_evidence(tmp_path, monkeypatch, adverse):
    artifact = tmp_path / "image.png"
    artifact.write_bytes(b"image")
    reads = iter(["Mysore Pak\n$4.99\n+15550100001" + adverse,
                  "Mysoore Pak $4.99\n+15550100001"])
    monkeypatch.setattr(qa, "_vision_text", lambda *a, **kw: (next(reads), "openrouter", "ocr_vision", []))
    report = qa.run_visual_qa(_reread_project(), artifact, output_format="portrait", allow_sidecar=False)
    assert report.status == "failed", report


@pytest.mark.parametrize("row", ["Mysore Pak $4.99", "Mysore Pak ........ $4.99"])
def test_inline_price_does_not_hide_wrong_item_spelling(row):
    assert any("near-duplicate item" in b for b in qa._near_duplicate_item_blockers(_reread_project(), row))


def test_nonuniform_item_price_mismatch_cannot_be_cleared_by_reread(tmp_path, monkeypatch):
    artifact = tmp_path / "image.png"
    artifact.write_bytes(b"image")
    project = _reread_project()
    project.locked_facts = [f for f in project.locked_facts if f.fact_id != "pricing_structure"]
    calls = []
    def vision(*args, **kwargs):
        calls.append(kwargs["model"])
        return "Mysore Pak\n$4.99\n+15550100001", "openrouter", "ocr_vision", []
    monkeypatch.setattr(qa, "_vision_text", vision)
    report = qa.run_visual_qa(project, artifact, output_format="portrait", allow_sidecar=False)
    assert len(calls) == 1
    assert any("item price mismatch" in b for b in report.blockers)


@pytest.mark.parametrize("row", ["Mysoore Pak $4.99", "Mysoore Pak ........ $4.99"])
def test_inline_price_correct_item_is_not_blocked(row):
    assert qa._near_duplicate_item_blockers(_reread_project(), row) == []

