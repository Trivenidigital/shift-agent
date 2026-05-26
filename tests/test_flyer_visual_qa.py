from __future__ import annotations

from datetime import datetime, timezone

from schemas import (
    FlyerLockedFact,
    FlyerProject,
    FlyerReferenceExtraction,
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

def _write_sidecar(tmp_path, text: str, *, filename: str = "flyer.png") -> "Path":
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
