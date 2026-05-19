"""Retry-safety contracts for Flyer final package delivery."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import importlib.machinery
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from schemas import FlyerAsset, FlyerCustomerStore, FlyerProject, FlyerProjectStore, FlyerRequestFields, FlyerUsageEvent, FlyerVisualQAReport  # noqa: E402


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "send-flyer-package"
REPORT_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "flyer-delivery-report"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("send_flyer_package_script", str(SCRIPT_PATH))
    return loader.load_module()


def _load_report_script():
    loader = importlib.machinery.SourceFileLoader("flyer_delivery_report_script", str(REPORT_SCRIPT_PATH))
    return loader.load_module()


def _asset(asset_id: str, kind: str, path: str, *, status: str = "pending", mid: str = "") -> FlyerAsset:
    return FlyerAsset(
        asset_id=asset_id,
        kind=kind,  # type: ignore[arg-type]
        source="rendered",
        path=path,
        mime_type="application/pdf" if path.endswith(".pdf") else "image/png",
        sha256="a" * 64,
        original_message_id="approve-1",
        received_at=datetime.now(timezone.utc),
        delivery_status=status,  # type: ignore[arg-type]
        outbound_message_id=mid,
        delivered_at=datetime.now(timezone.utc) if status == "sent" else None,
    )


def _project(tmp_path: Path) -> FlyerProject:
    kinds = [
        ("A0001", "final_whatsapp_image", "wa.png"),
        ("A0002", "final_instagram_post", "post.png"),
        ("A0003", "final_instagram_story", "story.png"),
        ("A0004", "final_printable_pdf", "print.pdf"),
    ]
    assets = []
    for asset_id, kind, name in kinds:
        path = tmp_path / name
        path.write_bytes(b"asset")
        assets.append(_asset(asset_id, kind, str(path)))
    return FlyerProject(
        project_id="F0001",
        status="finalizing_assets",
        customer_phone="+19045550123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="request-1",
        raw_request="Need flyer",
        fields=FlyerRequestFields(
            event_or_business_name="Daily Lunch Specials",
            contact_info="+1 704 324 3322",
            notes="Item 1 $1.99",
        ),
        assets=assets,
        final_asset_ids=[asset.asset_id for asset in assets],
    )


def _write_visual_qa(asset_path: Path, *, project_id: str = "F0001", project_version: int = 1, output_format: str = "whatsapp_image") -> None:
    from agents.flyer.visual_qa import write_visual_qa_report

    report = FlyerVisualQAReport(
        project_id=project_id,
        asset_id="A0001",
        artifact_path=str(asset_path),
        artifact_sha256="a" * 64,
        project_version=project_version,
        output_format=output_format,
        provider="test",
        qa_source="ocr_vision",
        status="passed",
        checked_at=datetime.now(timezone.utc),
    )
    write_visual_qa_report(report, asset_path)


def test_flyer_asset_delivery_fields_are_backward_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="final_whatsapp_image",
        source="rendered",
        path=str(tmp_path / "wa.png"),
        mime_type="image/png",
        sha256="a" * 64,
        received_at=datetime.now(timezone.utc),
    )

    assert asset.delivery_status == "pending"
    assert asset.outbound_message_id == ""
    assert asset.delivery_attempt_count == 0


def test_delivery_retry_selects_only_unsent_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    sent_asset = project.assets[0].model_copy(update={
        "delivery_status": "sent",
        "outbound_message_id": "wamid.sent.1",
        "delivered_at": datetime.now(timezone.utc),
        "delivery_attempt_count": 1,
    })
    project = project.model_copy(update={"assets": [sent_asset, *project.assets[1:]]})

    pending = mod._pending_project_assets(project)

    assert [asset.asset_id for asset in pending] == ["A0002", "A0003", "A0004"]


def test_final_asset_captions_label_each_customer_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)

    labels = [mod._caption_for_asset(asset) for asset in project.assets]

    assert labels == [
        "WhatsApp flyer - ready to forward in WhatsApp.",
        "Instagram post - square feed version.",
        "Instagram story - vertical story/status version.",
        "Printable PDF - best for printing or sharing as a document.",
    ]


def test_record_asset_delivery_persists_success_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    state_path = tmp_path / "projects.json"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    updated = mod._record_asset_delivery(
        state_path,
        project.project_id,
        "A0001",
        status="sent",
        outbound_message_id="dry-run:wa.png",
        error="",
    )

    asset = next(a for a in updated.assets if a.asset_id == "A0001")
    assert asset.delivery_status == "sent"
    assert asset.outbound_message_id == "dry-run:wa.png"
    assert asset.delivery_attempt_count == 1
    persisted = FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8")).projects[0]
    assert next(a for a in persisted.assets if a.asset_id == "A0001").delivery_status == "sent"


def test_uncertain_delivery_blocks_blind_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    uncertain_asset = project.assets[0].model_copy(update={
        "delivery_status": "uncertain",
        "delivery_error": "ack_parse_failed",
        "delivery_attempt_count": 1,
    })
    project = project.model_copy(update={"assets": [uncertain_asset, *project.assets[1:]]})

    with pytest.raises(SystemExit, match="uncertain delivery"):
        mod._pending_project_assets(project)


def test_uncertain_delivery_block_is_audited_before_retry_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    project = project.model_copy(update={
        "assets": [
            project.assets[0].model_copy(update={
                "delivery_status": "uncertain",
                "delivery_error": "ack_parse_failed",
                "delivery_attempt_count": 1,
            }),
            *project.assets[1:],
        ]
    })
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    class DummyLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_ndjson_append(path: Path, line: str) -> None:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    monkeypatch.setattr(
        mod,
        "_load_runtime_io",
        lambda: (
            DummyLock,
            lambda path, text: Path(path).write_text(text, encoding="utf-8"),
            None,
            None,
            fake_ndjson_append,
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
    ])

    with pytest.raises(SystemExit, match="uncertain delivery"):
        mod.main()

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{
        "type": "flyer_delivery_failed",
        "ts": rows[0]["ts"],
        "project_id": project.project_id,
        "customer_phone": "+19045550123",
        "asset_id": "A0001",
        "status": "uncertain",
        "error": "blocked retry: ack_parse_failed",
    }]


def test_project_delivery_blocks_without_passing_visual_qa(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
    ])

    assert mod.main() == 2

    out = json.loads(capsys.readouterr().out)
    assert out["visual_qa_failed"].endswith("wa.png")
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["error"].startswith("visual_qa_failed")


def test_delivery_report_summarizes_actionable_project_status(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_report_script()
    project = _project(tmp_path)
    project = project.model_copy(update={
        "assets": [
            project.assets[0].model_copy(update={"delivery_status": "sent", "outbound_message_id": "wamid.1"}),
            project.assets[1].model_copy(update={"delivery_status": "failed", "delivery_error": "bridge down"}),
            project.assets[2].model_copy(update={"delivery_status": "uncertain", "delivery_error": "ack_parse_failed"}),
            project.assets[3],
        ]
    })
    delivered = project.model_copy(update={
        "project_id": "F0002",
        "status": "delivered",
        "assets": [asset.model_copy(update={"delivery_status": "sent", "outbound_message_id": f"wamid.{asset.asset_id}"}) for asset in project.assets],
    })
    store = FlyerProjectStore(projects=[project, delivered])

    report = mod.build_delivery_report(store)

    assert report["projects_total"] == 2
    assert report["blocked_projects"] == 1
    assert report["ready_to_retry_projects"] == 0
    assert report["failed_assets"] == 1
    assert report["uncertain_assets"] == 1
    assert report["pending_assets"] == 1
    assert report["issues"][0]["project_id"] == "F0001"
    assert report["issues"][0]["uncertain_asset_ids"] == ["A0003"]
    assert report["issues"][0]["failed_asset_ids"] == ["A0002"]
    assert report["issues"][0]["pending_asset_ids"] == ["A0004"]


def test_delivery_report_ignores_legacy_delivered_assets_without_delivery_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_report_script()
    legacy_delivered = _project(tmp_path).model_copy(update={
        "status": "delivered",
        "updated_at": datetime.now(timezone.utc),
    })

    report = mod.build_delivery_report(FlyerProjectStore(projects=[legacy_delivered]))

    assert report["ok"] is True
    assert report["issues_total"] == 0
    assert report["pending_assets"] == 0


def test_trial_delivery_upsell_message_tracks_remaining_samples(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    now = datetime.now(timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Lakshmi Kitchen",
        business_address="123 Main St",
        public_phone="+17045550199",
        business_whatsapp_number="+17045550199",
        authorized_request_number="+19045550188",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
    ).model_copy(update={
        "status": "trial",
        "current_period_start": now.replace(day=1),
        "current_period_end": now.replace(month=now.month + 1 if now.month < 12 else 12),
        "usage_events": [FlyerUsageEvent(
            reservation_id="CUST0001:F0001",
            project_id="F0001",
            customer_id="CUST0001",
            kind="used",
            count=1,
            recorded_at=now,
            message_id="m1",
        )],
    })

    first = mod._trial_upsell_message(customer)
    assert "2 free sample flyers left" in first
    assert "https://wa.me/918522041562" in first

    used_events = []
    for index in range(1, 4):
        reservation_id = f"CUST0001:F000{index}"
        used_events.append({
            "reservation_id": reservation_id,
            "project_id": f"F000{index}",
            "customer_id": "CUST0001",
            "kind": "used",
            "count": 1,
            "recorded_at": now.isoformat(),
            "message_id": f"m{index}",
        })
    customer = FlyerCustomerStore.model_validate({
        "customers": [customer.model_dump(mode="json") | {"usage_events": used_events}]
    }).customers[0]
    third = mod._trial_upsell_message(customer)
    assert "3 free sample flyers are complete" in third
    assert "$49.99/month" in third


def test_trial_upsell_does_not_block_delivery_on_bad_customer_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path).model_copy(update={"customer_phone": "+19045550188"})
    customer_state = tmp_path / "customers.json"
    customer_state.write_text("{not valid json", encoding="utf-8")

    assert mod._trial_upsell_for_project(project, customer_state) == ""
