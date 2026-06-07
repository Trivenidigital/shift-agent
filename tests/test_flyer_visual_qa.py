from __future__ import annotations

from datetime import datetime, timezone

from schemas import (
    FlyerLockedFact,
    FlyerProject,
    FlyerReferenceExtraction,
    FlyerRequestFields,
    FlyerSourceContract,
    FlyerVisualQAReport,
)


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


def test_visual_qa_blocks_raw_request_instruction_text_even_when_facts_present(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"not really an image but has bytes")
    (tmp_path / "flyer.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99 "
        "Create a flyer for Premium Clean Chicken",
        encoding="utf-8",
    )

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "raw request instruction text is visible in generated flyer" in report.blockers


def test_visual_qa_blocks_stored_contact_instruction_leak(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"not really an image but has bytes")
    (tmp_path / "flyer.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99 "
        "Use Address and phone number stored",
        encoding="utf-8",
    )

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "raw request instruction text is visible in generated flyer" in report.blockers


def test_visual_qa_blocks_requested_edits_instruction_leak(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"not really an image but has bytes")
    (tmp_path / "flyer.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99 "
        "Requested edits: increase price",
        encoding="utf-8",
    )

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "raw request instruction text is visible in generated flyer" in report.blockers


def test_visual_qa_allows_legitimate_flyer_for_copy(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"not really an image but has bytes")
    (tmp_path / "flyer.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99 Flyer for Community Fundraiser",
        encoding="utf-8",
    )

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


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


# ---------- S5 P0-4: 7+ canonical scenarios ----------

def _write_sidecar(tmp_path, text: str, *, filename: str = "flyer.png"):
    artifact = tmp_path / filename
    artifact.write_bytes(b"image bytes")
    (tmp_path / f"{filename}.ocr.txt").write_text(text, encoding="utf-8")
    return artifact


def test_visual_qa_passes_with_matching_locked_facts(tmp_path):
    """Good preview: every required locked fact appears in OCR, no placeholders → status='passed'."""
    from agents.flyer.visual_qa import run_visual_qa

    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Clean bird. Strong life. Kheema Dosa $13.99 +1 732 983 7841",
    )
    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "passed", report.blockers
    assert not report.blockers


def test_visual_qa_fails_when_required_price_missing(tmp_path):
    """Missing-price scenario: locked item:0:price="$13.99" but OCR has no price text → fails."""
    from agents.flyer.visual_qa import run_visual_qa

    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Clean bird. Strong life. Kheema Dosa",
    )
    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("item:0:price" in b for b in report.blockers)


def test_visual_qa_fails_when_item_prices_are_swapped(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Idli", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$6", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa $6\nIdli $5")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)
    assert any("item price mismatch: item:1 expected Idli $6" in b for b in report.blockers)


def test_visual_qa_price_match_does_not_accept_prefix_price(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa $5.99")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: item:0:price" in report.blockers
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)


def test_visual_qa_price_match_requires_currency_for_currency_fact(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa - 5 pieces")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: item:0:price" in report.blockers
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)


def test_visual_qa_accepts_item_price_below_item_name(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa\n$5")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed"
    assert report.blockers == []


def test_visual_qa_accepts_item_price_in_stacked_menu_card(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\n"
        "GAVVALU\n1 Lb\n$8.99\n"
        "CHEKKALU\n1 lb\n$8.99\n"
        "ARISALU\n1 Lb\n$10.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers
    assert report.blockers == []


def test_visual_qa_accepts_rowwise_menu_card_grid_ocr(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\n"
        "GAVVALU CHEKKALU ARISALU\n"
        "1 Lb 1 lb 1 Lb\n"
        "$8.99 $8.99 $10.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers
    assert report.blockers == []


def test_visual_qa_rejects_rowwise_menu_card_grid_swapped_price(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\n"
        "GAVVALU CHEKKALU ARISALU\n"
        "1 Lb 1 lb 1 Lb\n"
        "$10.99 $8.99 $8.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Gavvalu 1 Lb $8.99" in b for b in report.blockers)
    assert any("item price mismatch: item:2 expected Arisalu 1 Lb $10.99" in b for b in report.blockers)


def test_visual_qa_rejects_stacked_menu_price_after_next_item_name(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Idli 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$6", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa\n1 lb\nIdli\n1 lb\n$5\n$6")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Dosa 1 lb $5" in b for b in report.blockers)


def test_visual_qa_rejects_stacked_menu_conflicting_price_before_expected_price(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Idli", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$6", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa\n$6\n$5\nIdli\n$6")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)


def test_visual_qa_rejects_stacked_menu_unknown_item_between_name_and_price(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa\nVada\n$5")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)


def test_visual_qa_rejects_stacked_menu_unknown_item_with_expected_price_on_adjacent_line(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa\nVada $5")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)


def test_visual_qa_rejects_same_line_unknown_item_between_name_and_price(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\nDosa Vada $5")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Dosa $5" in b for b in report.blockers)


def test_visual_qa_rejects_numeric_identity_inserted_before_stacked_price(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Chicken", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$10", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats\nChicken\n65\n$10")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Chicken $10" in b for b in report.blockers)


def test_visual_qa_rejects_rowwise_menu_card_grid_swapped_units(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 500 g", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$4.99", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\n"
        "GAVVALU CHEKKALU\n"
        "500 g 1 Lb\n"
        "$8.99 $4.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Gavvalu 1 Lb $8.99" in b for b in report.blockers)
    assert any("item price mismatch: item:1 expected Chekkalu 500 g $4.99" in b for b in report.blockers)


def test_visual_qa_rejects_rowwise_menu_card_grid_unknown_middle_column(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa 1 pc", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Idli 1 pc", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$6", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats\n"
        "DOSA VADA IDLI\n"
        "1 pc 1 pc 1 pc\n"
        "$5 $6 $9",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:1 expected Idli 1 pc $6" in b for b in report.blockers)


def test_visual_qa_rejects_rowwise_menu_card_grid_missing_numeric_item_identity(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Chicken 65", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$10", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Paneer 65", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$9", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats\n"
        "CHICKEN PANEER\n"
        "1 pc 1 pc\n"
        "$10 $9",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("item price mismatch: item:0 expected Chicken 65 $10" in b for b in report.blockers)
    assert any("item price mismatch: item:1 expected Paneer 65 $9" in b for b in report.blockers)


def test_visual_qa_accepts_same_line_price_before_item_name(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Dosa", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$5", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(tmp_path, "Fresh Meats menu\n$5 Dosa")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed"
    assert report.blockers == []


def test_visual_qa_fails_when_required_schedule_missing(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            *_project().locked_facts,
            FlyerLockedFact(fact_id="schedule", label="Schedule", value="Thursday every week", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Clean bird. Strong life. Kheema Dosa $13.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: schedule" in report.blockers


def test_visual_qa_accepts_every_day_for_weekly_schedule(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            *_project().locked_facts,
            FlyerLockedFact(fact_id="schedule", label="Schedule", value="Thursday every week", source="customer_text", required=True),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Every Thursday. Clean bird. Strong life. Kheema Dosa $13.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_accepts_every_two_days_for_weekly_schedule(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            *_project().locked_facts,
            FlyerLockedFact(
                fact_id="schedule",
                label="Schedule",
                value="Wednesday and Thursday every week",
                source="customer_text",
                required=True,
            ),
        ]
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Every Wednesday and Thursday. Clean bird. Strong life. Kheema Dosa $13.99",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_blocks_unrequested_delivery_claim(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. WhatsApp Delivery. Clean bird. Strong life. Kheema Dosa $13.99",
    )

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "unrequested operational claim visible: delivery" in report.blockers


def test_visual_qa_allows_customer_stated_we_cater_offer(tmp_path):
    """Live graduation re-roll regression (2026-06-07): the customer's OWN offer fact
    "We cater both veg and Non-veg" must NOT be flagged as an unrequested catering claim. The
    detector pattern (\\bwe\\s+cater\\b) matches the locked source fact, so the claim is grounded.
    Before the fix the allow-check looked for the bare keyword "catering", missed "we cater", and
    flagged the customer's own offer."""
    from agents.flyer.visual_qa import run_visual_qa
    base = _project()
    project = base.model_copy(update={
        "locked_facts": list(base.locked_facts) + [
            FlyerLockedFact(fact_id="offer:0", label="Offer", value="We cater both veg and Non-veg",
                            source="customer_text", required=True),
        ],
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Clean bird. Strong life. We cater both veg and Non-veg. Kheema Dosa $13.99",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert "unrequested operational claim visible: catering" not in report.blockers
    assert report.status == "passed", report.blockers


def test_visual_qa_allows_customer_stated_we_deliver_offer(tmp_path):
    """Symmetric to catering: a customer "we deliver" line is grounded in the source, so a rendered
    delivery claim is allowed (the detector pattern matches the source)."""
    from agents.flyer.visual_qa import run_visual_qa
    project = _project().model_copy(update={
        "raw_request": "Create flyer. We deliver to your door.",
        "fields": _project().fields.model_copy(update={"notes": "We deliver to your door."}),
    })
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Clean bird. Strong life. We deliver to your door. Kheema Dosa $13.99",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert "unrequested operational claim visible: delivery" not in report.blockers
    assert report.status == "passed", report.blockers


def test_visual_qa_blocks_unrequested_catering_claim(tmp_path):
    """The grounded-credit must stay strict: with NO catering anywhere in the customer source, a
    rendered catering claim is still blocked (does not broadly allow operational claims)."""
    from agents.flyer.visual_qa import run_visual_qa
    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. Catering Service. Clean bird. Strong life. Kheema Dosa $13.99",
    )
    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert "unrequested operational claim visible: catering" in report.blockers


def test_visual_qa_allows_requested_delivery_claim(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats. Premium Clean Chicken. WhatsApp Delivery. Clean bird. Strong life. Kheema Dosa $13.99",
    )
    project = _project().model_copy(update={
        "raw_request": "Create flyer. Mention delivery available.",
        "fields": _project().fields.model_copy(update={"notes": "Mention delivery available."}),
    })

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_requires_business_campaign_contact_and_profile_location(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0065",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-evening",
        raw_request="evening snacks",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchn", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Evening Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )

    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchn Evening Snacks Call +1 732 983 7841",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert "missing required visible fact: location" in report.blockers

    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchn Call +1 732 983 7841 90 Brybar Dr St Johns FL",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert "missing required visible fact: campaign_title" in report.blockers


def test_visual_qa_allows_campaign_title_with_profile_anchors_without_exact_brand(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0103",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-biryani",
        raw_request=(
            "Create a Special Biryani's Flyer using golden background. "
            "Use address and phone number stored."
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "SPECIAL BIRYANI'S\nChicken Biryani $16.99\nGoat Biryani $18.99\n"
        "90 Brybar Dr, St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers
    assert "missing required visible fact: business_name" not in report.blockers


def test_visual_qa_allows_campaign_titles_that_contain_org_suffix_words(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    campaign_titles = [
        "Restaurant Week Specials",
        "Kitchen Essentials Sale",
        "Cafe Style Biryani",
        "Biryani Bazaar",
    ]
    for index, title in enumerate(campaign_titles, start=1):
        project = FlyerProject(
            project_id=f"F02{index:02d}",
            status="awaiting_final_approval",
            customer_phone="+17329837841",
            created_at=now,
            updated_at=now,
            original_message_id=f"m-campaign-org-word-{index}",
            raw_request=f"Create a {title} flyer. Use saved address and phone.",
            locked_facts=[
                FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
                FlyerLockedFact(fact_id="campaign_title", label="Campaign", value=title, source="customer_text", required=True),
                FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
                FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            ],
        )
        artifact = _write_sidecar(
            tmp_path,
            f"{title}\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
            filename=f"campaign-{index}.png",
        )

        report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

        assert report.status == "passed", (title, report.blockers)


def test_visual_qa_still_requires_campaign_and_profile_anchors_when_brand_absent(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0103",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-biryani",
        raw_request="Create a Special Biryani's Flyer. Use address and phone number stored.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )

    missing_campaign = _write_sidecar(
        tmp_path,
        "Chicken Biryani $16.99\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )
    report = run_visual_qa(project, missing_campaign, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert "missing required visible fact: campaign_title" in report.blockers

    missing_address = _write_sidecar(
        tmp_path,
        "SPECIAL BIRYANI'S\nChicken Biryani $16.99\n+1 732 983 7841",
        filename="flyer2.png",
    )
    report = run_visual_qa(project, missing_address, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert "missing required visible fact: business_name" in report.blockers
    assert "missing required visible fact: location" in report.blockers


def test_visual_qa_does_not_skip_business_name_with_customer_text_anchors(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0115",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-customer-text-anchors",
        raw_request="Create a Special Biryani's Flyer for my event at 1 Event Rd. Phone +19045550104.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+19045550104", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="1 Event Rd", source="customer_text", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "SPECIAL BIRYANI'S\n1 Event Rd\n+1 904 555 0104",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: business_name" in report.blockers


def test_visual_qa_requires_exact_business_name_for_integrated_menu_candidate(tmp_path):
    """Integrated menu posters do not get a deterministic masthead overlay, so
    QA must require the real business name even when campaign/location/contact
    anchors are visible.
    """
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F9100",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="m-integrated-menu",
        raw_request=(
            "Create a flyer for south indian snacks. Include Gavvalu 1 Lb $8.99, "
            "Chekkalu 1 lb $8.99 and Arisalu 1 Lb $10.99"
        ),
        fields=FlyerRequestFields(preferred_language="en"),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="South Indian Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="Chekkalu 1 lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:price", label="Price", value="$8.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="Arisalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:price", label="Price", value="$10.99", source="customer_text", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "South Indian Snacks\nGavvalu 1 Lb $8.99\nChekkalu 1 lb $8.99\n"
        "Arisalu 1 Lb $10.99\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert "missing required visible fact: business_name" in report.blockers


def test_visual_qa_blocks_regional_script_for_integrated_english_menu_candidate(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F9101",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="m-integrated-menu-profile-language",
        raw_request="Create a flyer for south indian snacks. Include Gavvalu 1 Lb $8.99.",
        fields=FlyerRequestFields(preferred_language="te"),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="South Indian Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nSouth Indian Snacks\nGavvalu 1 Lb $8.99\n"
        "90 Brybar Dr St Johns FL\n+1 732 983 7841\nతెలుగు",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert "English-only flyer contains regional/non-English script" in report.blockers


def test_visual_qa_allows_regional_script_for_explicit_localized_menu(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F9102",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="m-localized-menu",
        raw_request="Create a flyer for south indian snacks. Use Telugu language.",
        fields=FlyerRequestFields(preferred_language="te"),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="South Indian Snacks", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Gavvalu 1 Lb", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nSouth Indian Snacks\nతెలుగు\nGavvalu 1 Lb $8.99\n"
        "90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert "English-only flyer contains regional/non-English script" not in report.blockers
    assert report.status == "passed"


def test_visual_qa_requires_exact_business_name_for_saved_brand_requests(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0104",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-brand",
        raw_request="Create a flyer using saved logo and saved business name.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Daily Specials", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "DAILY SPECIALS\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: business_name" in report.blockers


def test_visual_qa_allows_saved_contact_policy_without_exact_brand(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0116",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-saved-contact",
        raw_request="Create a Special Biryani's flyer. Use saved address and phone.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "SPECIAL BIRYANI'S\nChicken Biryani $16.99\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_requires_exact_business_name_for_use_logo_requests(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0114",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-use-logo",
        raw_request="Create a Daily Specials flyer. Use logo.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Daily Specials", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "DAILY SPECIALS\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: business_name" in report.blockers


def test_visual_qa_blocks_explicit_wrong_business_label_even_with_profile_anchors(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0106",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-wrong-brand",
        raw_request="Create a Special Biryani's Flyer. Use address and phone number stored.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Business: Other Restaurant\nSPECIAL BIRYANI'S\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "visible wrong business/brand: Other Restaurant" in report.blockers


def test_visual_qa_blocks_unlabeled_wrong_business_masthead_even_with_profile_anchors(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0107",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-wrong-masthead",
        raw_request="Create a Special Biryani's Flyer. Use address and phone number stored.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "OTHER RESTAURANT\nSPECIAL BIRYANI'S\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "visible wrong business/brand: Other Restaurant" in report.blockers


def test_visual_qa_blocks_titlecase_wrong_business_masthead_even_with_profile_anchors(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0117",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-wrong-titlecase",
        raw_request="Create a Special Biryani's Flyer. Use address and phone number stored.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Other Restaurant\nSPECIAL BIRYANI'S\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "visible wrong business/brand: Other Restaurant" in report.blockers


def test_visual_qa_blocks_mixed_case_org_suffix_wrong_masthead_even_with_profile_anchors(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0112",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-source-edit-recomposed",
        raw_request="Edit uploaded flyer/source artwork. Pick Any 3 Dosa replace with Pick Any 4 Dosa.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Specials", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="offer:0", label="Offer", value="Pick Any 4 Dosa", source="customer_text", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Triveni EXPRESS\n"
        "indian Cafe & Bakery\n"
        "THURSDAY DOSA NIGHT SPECIALS\n"
        "Business: Lakshmi's Kitchen\n"
        "Specials\n"
        "Location: 90 Brybar Dr St Johns FL\n"
        "Contact: +1 732 983 7841\n"
        "Detail: Pick Any 4 Dosa",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "visible wrong business/brand: Triveni Express" in report.blockers


def test_visual_qa_allows_sentence_case_org_word_tagline_with_profile_anchors(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0118",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-tagline",
        raw_request="Create a daily specials flyer.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Daily Specials", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\n"
        "Daily Specials\n"
        "Made fresh in our kitchen\n"
        "90 Brybar Dr St Johns FL\n"
        "Contact: +1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_blocks_source_contract_business_name_without_forbidden_substrings(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0108",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-source-brand",
        raw_request="Use this flyer for Lakshmi's Kitchen, replace branding.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
        reference_extractions=[
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="source_edit_template",
                provider="test",
                status="ok",
                source_contract=FlyerSourceContract(
                    source_business_names=["Other Restaurant"],
                    target_business_name="Lakshmi's Kitchen",
                    forbidden_substrings=[],
                ),
            )
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Other Restaurant\nSPECIAL BIRYANI'S\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "visible wrong business/brand: Other Restaurant" in report.blockers


def test_visual_qa_accepts_saint_johns_address_variant(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0096",
        status="awaiting_final_approval",
        customer_phone="+19045550104",
        created_at=now,
        updated_at=now,
        original_message_id="m-lakshmi",
        raw_request="source edit",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    )

    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\n"
        "Veg Thali Special\n"
        "Moringa Dal\nJeera Rice\n"
        "90 Brybar Dr,\nSaint Johns, FL\n"
        "+17329837841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers
    assert "missing required visible fact: location" not in report.blockers


def test_visual_qa_accepts_digit_heavy_location_split_across_ocr_lines(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0080",
        status="awaiting_final_approval",
        customer_phone="+15713830763",
        created_at=now,
        updated_at=now,
        original_message_id="m-mk-kitchen",
        raw_request="evening snacks",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="MK kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+15713830763", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="23596 prosperity ridge pl Ashburn Va 20148", source="customer_profile", required=True),
        ],
    )

    artifact = _write_sidecar(
        tmp_path,
        "MK kitchen\n"
        "Specials\n"
        "Wednesday To Saturday | 4 PM TO 7 PM\n"
        "23596 prosperity ridge pl\n"
        "Ashburn Va 20148\n"
        "+15713830763",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers
    assert "missing required visible fact: location" not in report.blockers


def test_visual_qa_blocks_regional_script_when_customer_requested_english_only(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0081",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-english-only",
        raw_request=(
            "Create flyer. Language: English only. "
            "Do NOT use Telugu, Hindi, or any regional Indian language."
        ),
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchn", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Ganesh Festival", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchn Ganesh Festival Call +17329837841 \u0c17\u0c23\u0c47\u0c36",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("English-only" in blocker for blocker in report.blockers)


def test_visual_qa_allows_english_text_when_customer_requested_english_only(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0081",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-english-only",
        raw_request="Create flyer. Language: English only. Do NOT use Telugu.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchn", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Ganesh Festival", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(tmp_path, "Lakshmis Kitchn Ganesh Festival Call +17329837841")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_blocks_regional_script_when_request_says_only_english(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0082",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-only-english",
        raw_request="Create poster for weekend offer. Use only English for all text.",
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Lakshmis Kitchn",
                source="customer_profile",
                required=True,
            ),
            FlyerLockedFact(
                fact_id="contact_phone",
                label="Contact",
                value="+17329837841",
                source="customer_profile",
                required=True,
            ),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchn Weekend Offer Call +17329837841 \u0c17\u0c23\u0c47\u0c36",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("English-only" in blocker for blocker in report.blockers)


def test_visual_qa_fails_on_template_placeholder_strings(tmp_path):
    """Generic template leakage ("YOUR LOGO HERE", "CLICK HERE TO ADD TEXT") must fail QA
    even when every locked fact happens to be present, because the customer would receive
    a generic template."""
    from agents.flyer.visual_qa import run_visual_qa

    artifact = _write_sidecar(
        tmp_path,
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99 YOUR LOGO HERE",
    )
    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("placeholder" in b for b in report.blockers)


def test_visual_qa_normalizes_phone_formatting(tmp_path):
    """OCR commonly emits +1 732 983 7841 or (732) 983-7841 while locked-fact has +17329837841.
    The digits-only path must accept the formatted version as a match."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    })

    artifact = _write_sidecar(tmp_path, "Fresh Meats Contact: +1 (732) 983-7841")
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "passed", report.blockers
    assert not any("contact_phone" in b for b in report.blockers)


def test_visual_qa_normalizes_apostrophe_in_business_name(tmp_path):
    """OCR drops curly apostrophes on small/handwritten brands. Locked "Lakshmi's Kitchen"
    must accept OCR "Lakshmis Kitchen" or "Lakshmi’s Kitchen" as a match."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_text", required=True),
        ],
    })

    for ocr_text in [
        "Lakshmis Kitchen Premium Indian Dinner",
        "Lakshmi’s Kitchen Premium Indian Dinner",
        "LAKSHMIS KITCHEN Premium Indian Dinner",
    ]:
        artifact = _write_sidecar(tmp_path, ocr_text)
        report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
        assert report.status == "passed", (ocr_text, report.blockers)


def test_visual_qa_fails_when_phone_completely_wrong(tmp_path):
    """Wrong-phone scenario: locked says one number, OCR shows a different one → fail."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    })

    artifact = _write_sidecar(tmp_path, "Fresh Meats Contact: +1 999 999 9999")
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("contact_phone" in b for b in report.blockers)


def test_validate_visual_qa_report_rejects_project_version_mismatch(tmp_path):
    """Stale QA sidecar (validate against newer project_version) must fail."""
    from agents.flyer.visual_qa import write_visual_qa_report, validate_visual_qa_report

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"image bytes")
    report = FlyerVisualQAReport(
        project_id="F9002",
        asset_id="A0001",
        artifact_path=str(artifact),
        artifact_sha256="0" * 64,
        project_version=1,  # stale
        output_format="concept_preview",
        provider="sidecar",
        qa_source="sidecar_test",
        status="passed",
        checked_at=datetime.now(timezone.utc),
    )
    write_visual_qa_report(report, artifact)

    # Caller now expects version 2; the on-disk QA is stale.
    result = validate_visual_qa_report(
        artifact, project_id="F9002", project_version=2, output_format="concept_preview", allow_sidecar=True,
    )
    assert not result.ok
    assert any("version mismatch" in b for b in result.blockers)


def test_validate_visual_qa_report_rejects_output_format_mismatch(tmp_path):
    """A QA pass for concept_preview must NOT cover an instagram_post send."""
    from agents.flyer.visual_qa import write_visual_qa_report, validate_visual_qa_report

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"image bytes")
    report = FlyerVisualQAReport(
        project_id="F9002",
        asset_id="A0001",
        artifact_path=str(artifact),
        artifact_sha256="0" * 64,
        project_version=1,
        output_format="concept_preview",  # written for preview
        provider="sidecar",
        qa_source="sidecar_test",
        status="passed",
        checked_at=datetime.now(timezone.utc),
    )
    write_visual_qa_report(report, artifact)

    # Caller now wants to send an instagram_post; QA covers preview only.
    result = validate_visual_qa_report(
        artifact, project_id="F9002", project_version=1, output_format="instagram_post", allow_sidecar=True,
    )
    assert not result.ok
    assert any("output format mismatch" in b for b in result.blockers)


def test_validate_visual_qa_report_rejects_sidecar_when_disabled(tmp_path):
    """In production, allow_sidecar=False means a sidecar_test QA report does not
    cover a customer send. Operator must rerun QA against the real OCR provider."""
    from agents.flyer.visual_qa import write_visual_qa_report, validate_visual_qa_report

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"image bytes")
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

    result = validate_visual_qa_report(
        artifact, project_id="F9002", project_version=1, output_format="concept_preview", allow_sidecar=False,
    )
    assert not result.ok
    assert any("sidecar visual QA is disabled" in b for b in result.blockers)


def test_validate_visual_qa_report_rejects_missing_report(tmp_path):
    """No QA report at all on disk → cannot send. The QA gate is fail-closed."""
    from agents.flyer.visual_qa import validate_visual_qa_report

    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"image bytes")

    result = validate_visual_qa_report(
        artifact, project_id="F9002", project_version=1, output_format="concept_preview", allow_sidecar=True,
    )
    assert not result.ok
    assert any("missing" in b for b in result.blockers)


def test_visual_qa_does_not_match_short_item_inside_longer_word(tmp_path):
    """Regression for review HIGH: locked item name 'Idly' must NOT match OCR
    'Idlysugar' / 'Idlywood'. Pre-fix the naive substring check would have
    passed QA. With word-boundary matching the QA correctly fails the missing-
    item-name blocker."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idly", source="customer_text", required=True),
        ],
    })

    artifact = _write_sidecar(tmp_path, "Fresh Meats. Featuring Idlysugar Premium Combo $13.99")
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("item:0:name" in b for b in report.blockers)


def test_visual_qa_does_not_match_business_name_as_prefix_of_unrelated_brand(tmp_path):
    """Regression: locked business_name='Acme' must NOT match OCR mentioning
    'Acme Building Services' as an unrelated brand. Word-boundary is on both
    sides so 'Acme' alone is required."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Acme", source="customer_text", required=True),
        ],
    })

    # OCR mentions a multi-word brand starting with Acme but NOT just "Acme" as a stand-alone token.
    # Word boundary correctly accepts "Acme" + space + "Building" as having word-boundary on both
    # sides of "Acme" — so it IS a match. The actual false-positive class is the SUBSTRING form
    # like "AcmeBuilding" (no space). Pin that.
    artifact = _write_sidecar(tmp_path, "Featured: AcmeBuilding Premium Services $99")
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed", "QA must not match 'Acme' inside 'AcmeBuilding'"


def test_visual_qa_phone_must_be_in_contiguous_run_not_globbed_across_text(tmp_path):
    """Regression for review HIGH: locked phone '+17329837841' must NOT match
    if its digits only appear by concatenating across unrelated text regions
    (e.g. 'Order 17 — discount 32-98-37841'). Phone digits-only checked WITHIN
    a single contiguous digit-bearing run, not against the whole-OCR digit
    stream."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    })

    # Across-region: digit fragments separated by an em-dash (not in the phone-run regex).
    artifact = _write_sidecar(tmp_path, "Fresh Meats Order 17 — discount 32-98-37841 today")
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("contact_phone" in b for b in report.blockers)


def test_visual_qa_short_local_number_is_not_treated_as_phone(tmp_path):
    """Regression: a 7-digit value (legacy local number, or accidental SKU)
    should NOT trigger the phone digits-only path which is too permissive at
    that length. The word-boundary text path applies instead."""
    from agents.flyer.visual_qa import run_visual_qa
    from schemas import FlyerLockedFact

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Fresh Meats", source="customer_text", required=True),
            FlyerLockedFact(fact_id="sku", label="SKU", value="7329837", source="customer_text", required=True),
        ],
    })

    # OCR has '17329837841' as a phone, which contains digits "7329837" as substring.
    # Phone-path is disabled for sub-10-digit values → text path applies → word-boundary check on
    # "7329837" against text "+17329837841" — fails because the digits are inside a longer digit run.
    artifact = _write_sidecar(tmp_path, "Fresh Meats Contact: +17329837841 today")
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("sku" in b for b in report.blockers)


def test_visual_qa_provider_unavailable_when_sidecar_disabled_and_no_openrouter_key(tmp_path, monkeypatch):
    """Provider unavailable scenario: no OPENROUTER_API_KEY and sidecar disabled means
    run_visual_qa returns provider_unavailable. The downstream generate/finalize callers
    queue manual_edit_required with reason_code='visual_qa_failed' (covered by their tests).
    Here we just pin the return shape."""
    from agents.flyer.visual_qa import run_visual_qa

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("FLYER_QA_ALLOW_SIDECAR", "0")
    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"image")
    # Sidecar exists but allow_sidecar=False excludes it; OPENROUTER absent.
    (tmp_path / "flyer.png.ocr.txt").write_text("Fresh Meats $13.99", encoding="utf-8")

    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=False)
    assert report.status == "provider_unavailable"
    assert any("OPENROUTER_API_KEY" in b or "ocr/vision text unavailable" in b for b in report.blockers)


# ─── Task 6: source-contract forbidden-substring QA gate ──────────


def _source_contract_project(forbidden, required_text=None, required_facts=None):
    now = datetime(2026, 5, 20, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9091",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-source-qa",
        raw_request="Edit uploaded flyer.",
        locked_facts=required_facts or [],
        reference_extractions=[
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="source_edit_template",
                provider="test",
                status="ok",
                source_contract=FlyerSourceContract(
                    requested_replacements={"Triveni Express": "Lakshmi's Kitchen"},
                    forbidden_substrings=list(forbidden),
                    required_text=list(required_text or []),
                    preserve_layout=True,
                    preserve_unmentioned_text=True,
                    confidence=0.9,
                ),
            ),
        ],
    )


def _write_sidecar_for_source(tmp_path, content):
    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"img")
    (tmp_path / "flyer.png.ocr.txt").write_text(content, encoding="utf-8")
    return artifact


def test_visual_qa_blocks_when_replaced_brand_still_visible(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _source_contract_project(["Triveni Express"])
    # OCR still has the OLD brand alongside the new — must fail QA.
    artifact = _write_sidecar_for_source(
        tmp_path,
        "Lakshmi's Kitchen Monday Thali Specials. Triveni Express ad bottom.",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("Triveni Express" in b for b in report.blockers)


def test_visual_qa_blocks_source_brand_even_when_campaign_and_profile_anchors_match(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 26, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0105",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-source-brand",
        raw_request="Use this reference only as inspiration for Special Biryani's. Use stored address and phone.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Special Biryani's", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
        reference_extractions=[
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="inspiration",
                provider="test",
                status="ok",
                source_contract=FlyerSourceContract(
                    source_business_names=["Other Restaurant"],
                    target_business_name="Lakshmi's Kitchen",
                    forbidden_substrings=["Other Restaurant"],
                    confidence=0.9,
                ),
            ),
        ],
    )
    artifact = _write_sidecar_for_source(
        tmp_path,
        "SPECIAL BIRYANI'S\nOther Restaurant\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert any("Other Restaurant" in blocker for blocker in report.blockers)


def test_visual_qa_passes_when_forbidden_brand_absent(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _source_contract_project(["Triveni Express"])
    artifact = _write_sidecar_for_source(
        tmp_path,
        "Lakshmi's Kitchen Monday Thali Specials. Jeera Rice.",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert all("Triveni Express" not in b for b in report.blockers)


def test_visual_qa_phone_forbidden_match(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _source_contract_project(["9045550100"])
    artifact = _write_sidecar_for_source(
        tmp_path,
        "Lakshmi's Kitchen Monday Thali Specials. Call (904) 555-0100 today.",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("9045550100" in b for b in report.blockers)


def test_visual_qa_brown_rice_passes_when_only_jeera_rice_is_a_replacement(tmp_path):
    """`Rice -> Jeera Rice` is a menu-item replacement: `Rice` is NOT
    populated into forbidden_substrings, so OCR text containing
    `Brown Rice` (a different rice variant) passes."""
    from agents.flyer.visual_qa import run_visual_qa

    project = _source_contract_project([])  # forbidden_substrings empty
    artifact = _write_sidecar_for_source(
        tmp_path,
        "Lakshmi's Kitchen Brown Rice and Jeera Rice listed.",
    )
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert all("Brown Rice" not in b and "Rice" not in b for b in report.blockers)


def test_visual_qa_semantically_accepts_diwali_campaign_and_offer_facts(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F0106",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        original_message_id="m-diwali",
        raw_request="Create a flyer for Diwali sale, All items 5-10% off. Lucky draw eligible with purchase above $100.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Diwali Sale", source="customer_text", required=True),
            FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="All items 5-10% off", source="customer_text", required=True),
            FlyerLockedFact(fact_id="offer:0", label="Offer", value="Lucky draw eligible with purchase above $100", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchen\nDIWALI SALE\nALL ITEMS 5-10% OFF\nLucky Draw Eligible\nAbove $100 purchase\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_accepts_requested_catering_label_as_non_identity(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F0105",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        original_message_id="m-f0105",
        raw_request="Create a daily thali specials flyer. Include veg, chicken, and goat specials, sides, catering note, address, phone.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:0:name", label="Item", value="veg", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:1:name", label="Item", value="chicken", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="goat specials", source="customer_text", required=True),
            FlyerLockedFact(fact_id="item:3:name", label="Item", value="sides", source="customer_text", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nCATERING\nDAILY THALI SPECIALS\nVEG THALI\nCHICKEN THALI\nGOAT THALI\nSIDES & DESSERTS\n90 BRYBAR DR ST JOHNS FL\nCONTACT: +17329837841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


def test_visual_qa_still_blocks_unrequested_catering_identity(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
        "raw_request": "Create a daily thali specials flyer.",
    })
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nCATERING\nDAILY THALI SPECIALS\n90 Brybar Dr St Johns FL\nCONTACT: +17329837841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert "visible wrong business/brand: Catering" in report.blockers


def test_visual_qa_blocks_source_contract_catering_suffix_masthead(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F0118",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-source-catering",
        raw_request="Use Acme Catering reference only as inspiration for Lakshmi's Kitchen.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
        reference_extractions=[
            FlyerReferenceExtraction(
                asset_id="A0001",
                role="source_edit_template",
                provider="test",
                status="ok",
                source_contract=FlyerSourceContract(
                    source_business_names=["Acme Catering"],
                    target_business_name="Lakshmi's Kitchen",
                    forbidden_substrings=[],
                ),
            )
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nCATERING\nDAILY THALI SPECIALS\n90 Brybar Dr St Johns FL\nCONTACT: +17329837841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert "visible wrong business/brand: Catering" in report.blockers


def test_visual_qa_item_name_semantics_reject_negative_or_note_only_mentions(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F0105",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        original_message_id="m-f0105",
        raw_request="Create a daily thali specials flyer. Include veg, chicken, and goat specials, sides, catering note, address, phone.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="item:2:name", label="Item", value="goat specials", source="customer_text", required=True),
        ],
    )
    negative = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nDAILY THALI SPECIALS\nNO GOAT AVAILABLE TODAY\n90 Brybar Dr St Johns FL\nCONTACT: +17329837841",
        filename="negative.png",
    )
    note_only = _write_sidecar(
        tmp_path,
        "Lakshmi's Kitchen\nDAILY THALI SPECIALS\nCATERING NOTE: ASK ABOUT GOAT OPTIONS\n90 Brybar Dr St Johns FL\nCONTACT: +17329837841",
        filename="note.png",
    )

    negative_report = run_visual_qa(project, negative, output_format="concept_preview", allow_sidecar=True)
    note_report = run_visual_qa(project, note_only, output_format="concept_preview", allow_sidecar=True)

    assert "missing required visible fact: item:2:name" in negative_report.blockers
    assert "missing required visible fact: item:2:name" in note_report.blockers


def test_visual_qa_does_not_accept_bare_event_word_for_campaign_title(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Diwali Sale", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    })
    artifact = _write_sidecar(tmp_path, "Lakshmis Kitchen\nDIWALI\n90 Brybar Dr St Johns FL\n+1 732 983 7841")

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: campaign_title" in report.blockers


def test_visual_qa_requires_campaign_title_phrase_proximity(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = _project().model_copy(update={
        "locked_facts": [
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Diwali Sale", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
        ],
    })
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchen\nDiwali decorations and sweets\nWeekly sale starts soon\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "failed"
    assert "missing required visible fact: campaign_title" in report.blockers


def test_visual_qa_requires_expiry_context_for_promotion_end(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F0107",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        original_message_id="m-snacks",
        raw_request="Create a flyer for evening snacks sale, Wednesday and Thursday, any item $7.99. Free Masala Chai with any purchase above $12. This promotion runs until June 25.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Evening Snacks Sale", source="customer_text", required=True),
            FlyerLockedFact(fact_id="pricing_structure", label="Pricing", value="Any item $7.99", source="customer_text", required=True),
            FlyerLockedFact(fact_id="offer:0", label="Offer", value="Free Masala Chai with any purchase above $12", source="customer_text", required=True),
            FlyerLockedFact(fact_id="schedule", label="Schedule", value="Wednesday and Thursday", source="customer_text", required=True),
            FlyerLockedFact(fact_id="promotion_end", label="Promotion end", value="June 25", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    )
    good_artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchen\nEVENING SNACKS SALE\nWednesday and Thursday\nAny item $7.99\nFree Masala Chai with purchase above $12\nOffer valid until June 25\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
        filename="good.png",
    )
    bad_artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchen\nEVENING SNACKS SALE\nWednesday and Thursday\nAny item $7.99\nFree Masala Chai with purchase above $12\nJune 25\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
        filename="bad.png",
    )

    good = run_visual_qa(project, good_artifact, output_format="concept_preview", allow_sidecar=True)
    bad = run_visual_qa(project, bad_artifact, output_format="concept_preview", allow_sidecar=True)

    assert good.status == "passed", good.blockers
    assert bad.status == "failed"
    assert "missing required visible fact: promotion_end" in bad.blockers


def test_visual_qa_accepts_explicit_promotion_end_label(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa

    project = FlyerProject(
        project_id="F0107",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        original_message_id="m-snacks",
        raw_request="Create a flyer for evening snacks sale. This promotion runs until June 25.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmi's Kitchen", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="campaign_title", label="Campaign", value="Evening Snacks Sale", source="customer_text", required=True),
            FlyerLockedFact(fact_id="promotion_end", label="Promotion end", value="June 25", source="customer_text", required=True),
            FlyerLockedFact(fact_id="location", label="Location", value="90 Brybar Dr St Johns FL", source="customer_profile", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact", value="+17329837841", source="customer_profile", required=True),
        ],
    )
    artifact = _write_sidecar(
        tmp_path,
        "Lakshmis Kitchen\nEVENING SNACKS SALE\nPROMOTION END: JUNE 25\n90 Brybar Dr St Johns FL\n+1 732 983 7841",
    )

    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)

    assert report.status == "passed", report.blockers


# ─────────────────────────────────────────────────────────────────
# P0 #2 — severity classifier tests (Commit 1)
# Pure-function over (blockers, project) -> 'pass' | 'warn' | 'block'.
# DICTIONARY is the policy; classifier evaluates it.
# ─────────────────────────────────────────────────────────────────


def _classifier_project(business_name: str = "Lakshmi's Kitchen") -> FlyerProject:
    """Minimal project for classifier tests — only locked_fact is business_name
    (the brand-typo gate's reference value)."""
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F0108",
        status="generating_concepts",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-test",
        raw_request="Create a flyer for Dosa Special",
        locked_facts=[
            FlyerLockedFact(
                fact_id="business_name", label="Business", value=business_name,
                source="customer_text", required=True,
            ),
        ],
    )


def _project_business_name_for_test(project: FlyerProject) -> str:
    for fact in project.locked_facts:
        if fact.fact_id == "business_name":
            return fact.value
    return ""


def test_classify_qa_severity_empty_blockers_returns_pass():
    from agents.flyer.visual_qa import classify_qa_severity
    assert classify_qa_severity([], project=_classifier_project()) == "pass"


def test_classify_qa_severity_single_placeholder_blocker_returns_block():
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["placeholder text is visible in generated flyer"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_single_missing_location_returns_warn():
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["missing required visible fact: location"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "warn"


def test_classify_qa_severity_unknown_blocker_fails_closed():
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["missing required visible fact: replacement:0:new"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_f0108_brand_typo_returns_warn():
    """F0108 reproduction: 'Laksmi'S Kitchen' (typo) vs 'Lakshmi's Kitchen'
    (project brand). Passes all 3 gates -> warn."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["visible wrong business/brand: Laksmi'S Kitchen"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "warn"


def test_classify_qa_severity_brand_token_overlap_zero_returns_block():
    """Distinct brand (token overlap = 0) -> block."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["visible wrong business/brand: Laxmi Mart"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_short_brand_typo_blocked_when_overlap_fails():
    """Short brands (4 chars): Arla vs Aria -> overlap 0, distance 1.
    Token gate fails -> block. Short-brand-by-default is correct."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["visible wrong business/brand: Arla"]
    assert classify_qa_severity(blockers, project=_classifier_project("Aria")) == "block"


def test_is_brand_typo_boundary_overlap_05_classifies_warn():
    """F0108 sits at overlap = 0.5 exactly (shared {kitchen} of 2 project
    tokens). With >= semantics MUST classify warn. Pinned per plan §5."""
    from agents.flyer.visual_qa import _is_brand_typo
    assert _is_brand_typo("Laksmi'S Kitchen", "Lakshmi's Kitchen")


def test_classify_qa_severity_two_item_warns_returns_block_via_core_promise():
    """2 core-promise warn blockers (item:N:name) -> block via escalation,
    even though count is below the cap."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = [
        "missing required visible fact: item:4:name",
        "missing required visible fact: item:5:name",
    ]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_brand_typo_plus_missing_schedule_returns_block():
    """Reviewer 2 #2 combo escalation: 1 brand-identity warn + 1 event-essential
    warn -> block. Owner getting a draft with misspelled name AND no event
    time is structurally worse than count=2 suggests."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = [
        "visible wrong business/brand: Laksmi'S Kitchen",
        "missing required visible fact: schedule",
    ]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_brand_typo_plus_missing_contact_info_returns_warn():
    """Brand-identity warn + non-event-essential warn -> warn (combo
    escalation only triggers for event-essential warns; contact_info is
    recoverable on revision)."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = [
        "visible wrong business/brand: Laksmi'S Kitchen",
        "missing required visible fact: contact_info",
    ]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "warn"


def test_classify_qa_severity_four_warns_returns_block_via_count_cap():
    """4 warn blockers -> block via count cap."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = [
        "missing required visible fact: location",
        "missing required visible fact: contact_info",
        "missing required visible fact: schedule",
        "missing required visible fact: promotion_end",
    ]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_warn_plus_block_returns_block():
    """Any block-tier blocker forces block."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = [
        "missing required visible fact: location",
        "missing required visible fact: contact_info",
        "placeholder text is visible in generated flyer",
    ]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_f0109_three_missing_facts_returns_block():
    """F0109 reproduction: 1 missing location (warn, event-essential)
    + 2 missing item names (warn, core-promise) -> block via BOTH
    core-promise escalation AND count cap."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = [
        "missing required visible fact: location",
        "missing required visible fact: item:4:name",
        "missing required visible fact: item:5:name",
    ]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_provider_unavailable_returns_block():
    """Substrate failure (OCR unavailable) -> block."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["ocr/vision text unavailable for generated artifact"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_missing_business_name_returns_block():
    """missing business_name is block-tier (identity bleed risk), NOT warn."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["missing required visible fact: business_name"]
    assert classify_qa_severity(blockers, project=_classifier_project()) == "block"


def test_classify_qa_severity_does_not_mutate_inputs():
    """Pure-function invariant: classifier must not modify blockers or project.
    Defensive — if the classifier ever leaks workflow side-effects, the
    Hermes-as-brain invariant has regressed."""
    from agents.flyer.visual_qa import classify_qa_severity
    blockers = ["missing required visible fact: schedule"]
    blockers_before = list(blockers)
    project = _classifier_project()
    brand_before = _project_business_name_for_test(project)
    _ = classify_qa_severity(blockers, project=project)
    assert blockers == blockers_before
    assert _project_business_name_for_test(project) == brand_before


def test_run_visual_qa_sets_severity_field_on_pass_path(tmp_path):
    """run_visual_qa now populates report.severity in both early-return and
    main paths. Pass-path: no blockers -> severity 'pass'."""
    from agents.flyer.visual_qa import run_visual_qa
    artifact = tmp_path / "ok.png"
    artifact.write_bytes(b"img")
    (tmp_path / "ok.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99",
        encoding="utf-8",
    )
    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "passed"
    assert report.severity == "pass"


def test_run_visual_qa_sets_severity_field_on_block_path(tmp_path):
    """When blockers fire, severity reflects the classifier output."""
    from agents.flyer.visual_qa import run_visual_qa
    artifact = tmp_path / "bad.png"
    artifact.write_bytes(b"img")
    (tmp_path / "bad.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. Kheema Dosa [price]",
        encoding="utf-8",
    )
    report = run_visual_qa(_project(), artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    # placeholder blocker is block-tier
    assert report.severity == "block"


# ── intent-aware QA: inferred-item coverage (bounded-creative-planner slice 3) ──

def _project_with_inferred(items):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    facts = [FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchen",
                             source="customer_text", required=True)]
    for i, name in enumerate(items):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=name,
                                     source="hermes_inferred"))
    return FlyerProject(
        project_id="F9003", status="awaiting_final_approval", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-qa",
        raw_request="Flyer for Lakshmis Kitchen, include breakfast items",
        locked_facts=facts,
    )


def _qa_with_ocr(tmp_path, project, ocr_text):
    from agents.flyer.visual_qa import run_visual_qa
    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"bytes for the artifact")
    (tmp_path / "flyer.png.ocr.txt").write_text(ocr_text, encoding="utf-8")
    return run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)


def test_intent_qa_passes_when_all_inferred_items_rendered(tmp_path):
    report = _qa_with_ocr(tmp_path, _project_with_inferred(["Idli", "Masala Dosa"]),
                          "Lakshmis Kitchen Idli Masala Dosa Breakfast Specials")
    assert not any("inferred item not rendered" in b for b in report.blockers)


def test_intent_qa_blocks_when_an_inferred_item_is_missing(tmp_path):
    report = _qa_with_ocr(tmp_path, _project_with_inferred(["Idli", "Masala Dosa"]),
                          "Lakshmis Kitchen Idli Breakfast Specials")  # Masala Dosa absent
    assert any("inferred item not rendered: Masala Dosa" in b for b in report.blockers)


def test_intent_qa_inert_without_inferred_items(tmp_path):
    # No hermes_inferred facts (default/dormant state) → coverage check is a no-op.
    report = _qa_with_ocr(tmp_path, _project(),
                          "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99")
    assert not any("inferred item not rendered" in b for b in report.blockers)


# ── intent-aware QA: requested item count (bounded-creative-planner slice 5b) ──

def _project_inferred_count(items, raw_request):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    facts = [FlyerLockedFact(fact_id="business_name", label="Business", value="Dragon Bowl",
                             source="customer_text", required=True)]
    for i, name in enumerate(items):
        facts.append(FlyerLockedFact(fact_id=f"item:{i}:name", label="Item", value=name,
                                     source="hermes_inferred"))
    return FlyerProject(
        project_id="F9004", status="awaiting_final_approval", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-qa",
        raw_request=raw_request, locked_facts=facts,
    )


def test_requested_item_count_parses_count_or_none():
    from agents.flyer.visual_qa import _requested_item_count
    assert _requested_item_count("include 8 famous South Indian breakfast items") == 8
    assert _requested_item_count("6 items, any item at $8.99") == 6
    assert _requested_item_count("any item at $8.99") is None  # a price, not an item count
    assert _requested_item_count("open 8 AM to 11 AM") is None  # a time, not an item count
    assert _requested_item_count("a great summer sale flyer") is None


def test_intent_count_qa_passes_when_committed_count_matches():
    from agents.flyer.visual_qa import _inferred_intent_count_blockers
    project = _project_inferred_count(["Idli", "Vada", "Dosa"], "include 3 famous south indian items")
    assert _inferred_intent_count_blockers(project) == []


def test_intent_count_qa_blocks_when_short_of_requested():
    from agents.flyer.visual_qa import _inferred_intent_count_blockers
    project = _project_inferred_count(["Idli", "Vada"], "include 3 famous south indian items")
    blockers = _inferred_intent_count_blockers(project)
    assert any("requested item count not satisfied: asked 3, have 2" in b for b in blockers)


def test_intent_count_qa_inert_without_inferred_items():
    """Hard items only (no hermes_inferred) + a stated count ⇒ no assertion. The count
    QA is gated on planner contribution, so dormant flyers are unaffected."""
    from agents.flyer.visual_qa import _inferred_intent_count_blockers
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F9005", status="awaiting_final_approval", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-qa",
        raw_request="include 3 famous south indian items",
        locked_facts=[FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idli",
                                       source="customer_text")],
    )
    assert _inferred_intent_count_blockers(project) == []


def test_intent_count_blocker_is_block_tier():
    from agents.flyer.visual_qa import classify_qa_severity
    project = _project_inferred_count(["Idli", "Vada"], "include 3 famous south indian items")
    sev = classify_qa_severity(["requested item count not satisfied: asked 3, have 2"], project=project)
    assert sev == "block"


def _phone_project(extra_facts=()):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9050",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-qa",
        raw_request="Lunch combo flyer.",
        locked_facts=[
            FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchen", source="customer_text", required=True),
            FlyerLockedFact(fact_id="contact_phone", label="Contact phone", value="+1 732 983 7841", source="customer_profile", required=True),
            *extra_facts,
        ],
    )


def test_unexpected_phone_blocked_when_extra_wrong_number_present():
    # P1-1: the correct phone present AND an extra/corrupted one — flag only the wrong one.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    blockers = _unexpected_phone_blockers(_phone_project(), "Call +1 732 983 7841 or +1 732 983 7899")
    assert blockers and any("7899" in b for b in blockers)
    assert not any("7841" in b for b in blockers)


def test_unexpected_phone_no_false_positive_for_correct_phone_variants():
    # The locked phone rendered in any legitimate format (country code, separators,
    # repeated header/footer) must never be flagged.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    project = _phone_project()
    for ocr in [
        "Call 732-983-7841 today",
        "Call +1 (732) 983-7841",
        "Header +1 732 983 7841 ... Footer 732.983.7841",
        "WhatsApp 17329837841",
    ]:
        assert _unexpected_phone_blockers(project, ocr) == [], ocr


def test_unexpected_phone_not_flagged_for_menu_prices():
    # A price-dense menu must not synthesize a false phone-shaped run.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    ocr = "Idli $2 Vada $3 Dosa $4 Pongal $5 Upma $6 Poori $7 Call +1 732 983 7841"
    assert _unexpected_phone_blockers(_phone_project(), ocr) == []


def test_unexpected_phone_skipped_when_no_locked_phone():
    # No locked phone to compare against -> cannot verify, must not false-alarm.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    project = FlyerProject(
        project_id="F9051", status="awaiting_final_approval", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-qa", raw_request="x",
        locked_facts=[FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchen", source="customer_text", required=True)],
    )
    assert _unexpected_phone_blockers(project, "Call 1 222 333 4444 or 9 888 777 6666") == []


def test_unexpected_phone_blocker_is_block_tier():
    from agents.flyer.visual_qa import classify_qa_severity
    sev = classify_qa_severity(["unverified phone number visible: +1 732 983 7899"], project=_phone_project())
    assert sev == "block"


def test_unexpected_phone_integration_via_run_visual_qa(tmp_path):
    from agents.flyer.visual_qa import run_visual_qa
    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"bytes")
    (tmp_path / "flyer.png.ocr.txt").write_text(
        "Lakshmis Kitchen Lunch Combo Idli $8.99 Call +1 732 983 7841 or +1 732 983 7899",
        encoding="utf-8",
    )
    project = _phone_project([
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idli", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
    ])
    report = run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=True)
    assert report.status == "failed"
    assert any("unverified phone number visible" in b for b in report.blockers)


def test_unexpected_phone_blocked_when_glob_adjacent_to_correct_phone():
    # Codex HIGH-1: two numbers joined by " / " must not glob into one >15-digit run
    # that escapes the length check — the wrong one is still flagged.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    blockers = _unexpected_phone_blockers(_phone_project(), "Call +1 732 983 7841 / +1 732 983 7899")
    assert any("7899" in b for b in blockers)


def test_unexpected_phone_blocked_for_suffix_digit_corruption():
    # Codex HIGH-2: a number that CONTAINS the locked digits plus an extra digit is
    # NOT the registered phone — national-number compare (not substring) flags it.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    blockers = _unexpected_phone_blockers(_phone_project(), "Call +1 732 983 7841 then +1 732 983 78410")
    assert any("78410" in b for b in blockers)


def test_unexpected_phone_not_flagged_for_bare_price_column():
    # Codex MEDIUM: a bare decimal/price column must not be read as a phone.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    ocr = "Weekend specials 12.99 8.99 5.49 6.49 9.99 Call +1 732 983 7841"
    assert _unexpected_phone_blockers(_phone_project(), ocr) == []


def test_unexpected_phone_blocked_for_wrong_country_code():
    # Codex HIGH-3: same national digits under a DIFFERENT country code (+91 vs the
    # registered +1) is a wrong number — calling it reaches a different country.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    blockers = _unexpected_phone_blockers(_phone_project(), "Call +91 732 983 7841")
    assert any("+91" in b for b in blockers)


def test_unexpected_phone_allows_correct_number_with_or_without_plus_one():
    # +1 and bare 10-digit are the same NANP domestic line — never flagged.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    assert _unexpected_phone_blockers(_phone_project(), "Call +1 732 983 7841") == []
    assert _unexpected_phone_blockers(_phone_project(), "Call 732 983 7841") == []


def test_unexpected_phone_blocked_for_country_code_split_from_national_digits():
    # Codex round-3: a wrong country code separated from the national digits by a wide
    # gap, a newline, or a leading paren must still be caught (independent cc scan).
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    for ocr in [
        "Call +91   732 983 7841",
        "Call +91\n732 983 7841",
        "Call +91 (732) 983-7841",
    ]:
        blockers = _unexpected_phone_blockers(_phone_project(), ocr)
        assert any("+91" in b for b in blockers), ocr


def test_unexpected_phone_no_false_positive_for_math_or_promo_plus():
    # A "+N" used for math/promotions (not a country code prefixing a phone) must not flag.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    ocr = "Mix 2 + 3 toppings free, spend $50+ for delivery. Call +1 732 983 7841"
    assert _unexpected_phone_blockers(_phone_project(), ocr) == []


def test_unexpected_phone_no_false_positive_for_address_or_price_adjacent_to_phone():
    # Codex round-4: a ZIP/price next to the phone (same line or the line above) must not
    # glob into it and be misread as a country code — the correct phone still passes.
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    for ocr in [
        "90 Brybar Dr St Johns FL 32259\n(732) 983-7841",
        "90 Brybar Dr St Johns FL 32259 (732) 983-7841",
        "Lunch special $9\n732 983 7841",
    ]:
        assert _unexpected_phone_blockers(_phone_project(), ocr) == [], ocr


def test_unexpected_phone_still_blocked_when_adjacent_to_address_digits():
    # Globbing an adjacent ZIP must NOT hide a genuinely wrong phone (its national differs).
    from agents.flyer.visual_qa import _unexpected_phone_blockers
    assert _unexpected_phone_blockers(_phone_project(), "Visit FL 32259 (999) 888-7777")


def _date_project(event_date, created=datetime(2026, 6, 2, tzinfo=timezone.utc)):
    return FlyerProject(
        project_id="F9052", status="awaiting_final_approval", customer_phone="+10000000000",
        created_at=created, updated_at=created, original_message_id="m-qa", raw_request="flyer",
        locked_facts=[FlyerLockedFact(fact_id="business_name", label="Business", value="Lakshmis Kitchen", source="customer_text", required=True)],
        fields=FlyerRequestFields(event_or_business_name="Lakshmis Kitchen", event_date=event_date),
    )


def test_past_event_date_blocked():
    # P1-2: an event date before the flyer's creation date is stale -> fail closed.
    from agents.flyer.visual_qa import _past_event_date_blockers
    blockers = _past_event_date_blockers(_date_project("2026-05-01"))
    assert blockers and "2026-05-01" in blockers[0]


def test_today_and_future_event_dates_not_blocked():
    from agents.flyer.visual_qa import _past_event_date_blockers
    assert _past_event_date_blockers(_date_project("2026-06-02")) == []  # same day is valid
    assert _past_event_date_blockers(_date_project("2026-12-25")) == []  # future is valid


def test_missing_event_date_not_blocked():
    from agents.flyer.visual_qa import _past_event_date_blockers
    assert _past_event_date_blockers(_date_project(None)) == []


def test_past_event_date_blocker_is_block_tier():
    from agents.flyer.visual_qa import classify_qa_severity
    sev = classify_qa_severity(["event date is in the past: 2026-05-01"], project=_date_project("2026-05-01"))
    assert sev == "block"


def test_text_defect_note_blockers_flag_duplication_and_misspelling():
    # P1-4: vision-QA notes reporting duplicated or misspelled text become blockers.
    from agents.flyer.visual_qa import _text_defect_note_blockers
    notes = ["business name appears duplicated at top and middle", "THURSDAY is misspelled as THURRSDAY", "colors look vibrant"]
    blockers = _text_defect_note_blockers(notes)
    assert len(blockers) == 2 and all(b.startswith("visible text defect reported by QA:") for b in blockers)


def test_text_defect_note_blockers_ignore_clean_notes():
    from agents.flyer.visual_qa import _text_defect_note_blockers
    assert _text_defect_note_blockers(["all text legible", "good contrast"]) == []
    assert _text_defect_note_blockers([]) == []


def test_text_defect_note_blocker_is_block_tier():
    from agents.flyer.visual_qa import classify_qa_severity
    sev = classify_qa_severity(["visible text defect reported by QA: brand duplicated"], project=_date_project(None))
    assert sev == "block"


def test_run_visual_qa_blocks_on_duplicate_text_note(tmp_path, monkeypatch):
    # Integration: a duplicate-text quality note from the vision path fails QA closed.
    from agents.flyer import visual_qa as vq
    monkeypatch.setattr(vq, "_vision_text", lambda artifact: (
        "Lakshmis Kitchen Lakshmis Kitchen Idli $8.99 Call +1 732 983 7841", "openrouter", "ocr_vision",
        ["business name appears duplicated"]))
    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"bytes")
    project = _phone_project([
        FlyerLockedFact(fact_id="item:0:name", label="Item", value="Idli", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:0:price", label="Price", value="$8.99", source="customer_text", required=True),
    ])
    report = vq.run_visual_qa(project, artifact, output_format="concept_preview", allow_sidecar=False)
    assert report.status == "failed"
    assert any("visible text defect reported by QA" in b for b in report.blockers)
