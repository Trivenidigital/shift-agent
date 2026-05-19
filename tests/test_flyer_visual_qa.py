from __future__ import annotations

from datetime import datetime, timezone

from schemas import FlyerLockedFact, FlyerProject, FlyerVisualQAReport


def _project():
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9002",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-qa",
        raw_request="Create flyer. Headline: Premium Clean Chicken.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="headline", label="Headline", value="Premium Clean Chicken", source="customer_text", required=True),
            FlyerLockedFact(fact_id="tagline", label="Tagline", value="Clean bird. Strong life.", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$13.99", source="customer_text", required=True),
        ],
    )


def test_visual_qa_blocks_placeholders_even_when_facts_present(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"not really an image but has bytes")
    (tmp_path / "flyer.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. Kheema Dosa [price]",
        encoding="utf-8",
    )

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("placeholder" in blocker for blocker in report.blockers)


def test_visual_qa_requires_real_ocr_or_explicit_test_sidecar(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"image")
    (tmp_path / "flyer.png.ocr.txt").write_text("Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99", encoding="utf-8")

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=False)

    assert report.status == "provider_unavailable"
    assert report.qa_source == "ocr_vision"


def test_qa_report_rejects_artifact_mutation(tmp_path):
    from agents.flyer.visual_qa import write_visual_qa_report, validate_visual_qa_report

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"version one")
    report = FlyerVisualQAReport(
        project_id="F9002",
        asset_id="A0001",
        artifact_path=str(artifact),
        artifact_sha256="0" * 64,
        project_version=1,
        output_format="concept_preview",
        provider="sidecar",
        qa_source="sidecar_test",
        status="passed",
        checked_at=datetime.now(timezone.utc),
    )
    write_visual_qa_report(report, artifact)
    artifact.write_bytes(b"version two")

    result = validate_visual_qa_report(artifact, project_id="F9002", project_version=1, output_format="concept_preview", allow_sidecar=True)

    assert not result.ok
    assert any("artifact hash mismatch" in blocker for blocker in result.blockers)
