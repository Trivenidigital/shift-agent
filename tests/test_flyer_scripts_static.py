"""Static production-readiness contracts for Flyer Studio scripts."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "src" / "agents" / "flyer" / "scripts"


def test_scripts_use_atomic_writes_and_locks():
    for name in [
        "create-flyer-project",
        "update-flyer-project",
        "generate-flyer-concepts",
        "finalize-flyer-assets",
        "handle-flyer-onboarding",
        "store-flyer-brand-asset",
        "manage-flyer-account",
    ]:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "FileLock" in text
        assert "atomic_write_text" in text


def test_delivery_script_can_send_by_project_id():
    text = (SCRIPTS / "send-flyer-package").read_text(encoding="utf-8")
    finalize = (SCRIPTS / "finalize-flyer-assets").read_text(encoding="utf-8")
    assert "--project-id" in text
    assert "validate_text_manifest_file" in text
    assert "--allow-unverified-asset" in text
    assert "--dry-run-bridge" in text
    assert "FLYER_TEXT_QA_BREAK_GLASS" in text
    assert "project.status != \"finalizing_assets\"" in text
    assert "FINAL_KIND_TO_FORMAT" in text
    assert "output_format=expected_formats.get(str(asset)) or None" in text
    assert "project_changed_during_delivery" in text
    assert "_record_asset_delivery" in text
    assert "_pending_project_assets" in text
    assert "delivery_status == \"uncertain\"" in text
    assert 'status="sent"' in text
    assert "FlyerAssetsDelivered" in text
    assert "FlyerDeliveryFailed" in text
    assert ".model_dump_json()" in text
    assert '"status": "delivered"' in text
    assert '"status": "delivered"' not in finalize
    assert "audit_uncertain_delivery_block" in text


def test_delivery_report_installed_and_smoked_for_operator_visibility():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    report = (SCRIPTS / "flyer-delivery-report").read_text(encoding="utf-8")

    assert "flyer-delivery-report" in deploy
    assert "flyer-delivery-report" in smoke
    assert "build_delivery_report" in report
    assert "--json" in report
    assert "uncertain_asset_ids" in report


def test_update_script_supports_selection_revision_and_approval():
    text = (SCRIPTS / "update-flyer-project").read_text(encoding="utf-8")
    assert "--select-concept" in text
    assert "--revision-text" in text
    assert "--approve-message-id" in text


def test_deploy_repairs_flyer_state_ownership():
    text = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    assert "chown -R shift-agent:shift-agent /opt/shift-agent/state/flyer" in text


def test_generation_defaults_to_one_selected_concept_for_credit_control():
    script = (SCRIPTS / "generate-flyer-concepts").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    assert '"status": "awaiting_final_approval" if one_shot else "awaiting_concept_selection"' in script
    assert '"selected_concept_id": "C1" if one_shot else None' in script
    assert "Reply APPROVE or reply with changes." in actions
    assert "Reply 1, 2, or 3 to choose" not in actions
    assert "validate_text_manifest_file" in actions


def test_flyer_complete_requests_send_processing_ack_before_generation():
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    assert "def send_flyer_processing_ack" in actions
    assert "Request processing." in actions
    assert hooks.index("send_flyer_processing_ack(chat_id, project_id)") < hooks.index("trigger_generate_flyer_concepts(project_id)")


def test_intake_script_handles_menu_fliers_with_location_phone_and_address():
    script = (SCRIPTS / "create-flyer-project").read_text(encoding="utf-8")
    assert "menu_match" in script
    assert "location_match" in script
    assert "address_match" in script
    assert "phone_match" in script
    assert "professional local food menu flyer" in script
    assert "--reference-media-path" in script
    assert "_copy_reference_asset" in script
    assert "reference_image" in script
    assert "offer_match" in script


def test_router_starts_new_work_over_active_state_for_explicit_or_media_template_requests():
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    assert "def should_start_new_flyer_over_active" in actions
    assert "def flyer_project_has_required_fields" in actions
    assert "_NEW_FLYER_REQUEST" in actions
    assert "_MEDIA_TEMPLATE_EDIT" in actions
    assert "_WRONG_FLYER_CORRECTION" in actions
    assert "force_new=True" in hooks
    assert hooks.index("should_start_new_flyer_over_active(text, has_media=bool(media_path))") < hooks.index("_try_flyer_brand_asset_intercept")


def test_onboarding_is_whatsapp_native_and_plan_config_driven():
    script = (SCRIPTS / "handle-flyer-onboarding").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    schemas = (REPO / "src" / "platform" / "schemas.py").read_text(encoding="utf-8")
    assert "FlyerPlanTier" in schemas
    assert "starter" in schemas and "growth" in schemas and "unlimited" in schemas
    assert "handle_onboarding_message" in script
    assert "store_brand_asset" in (SCRIPTS / "store-flyer-brand-asset").read_text(encoding="utf-8")
    assert "trigger_flyer_onboarding" in actions
    assert "trigger_store_flyer_brand_asset" in actions
    assert "_try_flyer_brand_asset_intercept" in hooks
    assert "_try_flyer_onboarding_intercept" in hooks
    assert hooks.index("_try_flyer_active_project_intercept") < hooks.index("_try_flyer_onboarding_intercept")


def test_account_script_supports_activation_quota_and_commands():
    script = (SCRIPTS / "manage-flyer-account").read_text(encoding="utf-8")
    assert "--command-text" in script
    assert "--activate-customer" in script
    assert "--reserve-quota" in script
    assert "--finalize-usage" in script
    assert "--release-quota" in script


def test_revision_clears_selected_design_and_blocks_unapplied_approval():
    script = (SCRIPTS / "update-flyer-project").read_text(encoding="utf-8")
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    render = (REPO / "src" / "agents" / "flyer" / "render.py").read_text(encoding="utf-8")
    assert '"concepts": []' in script
    assert '"selected_concept_id": None' in script
    assert '"final_asset_ids": []' in script
    assert "cannot approve with unapplied revisions" in script
    assert "cannot approve while revised design has not been regenerated" in script
    assert "approve_regenerated=true" in hooks
    assert "if path.exists() and path.stat().st_size > 1000" not in render


def test_phase2_quality_smoke_and_workflow_deploy_contracts():
    smoke_cli = (SCRIPTS / "smoke-flyer-quality").read_text(encoding="utf-8")
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    update = (SCRIPTS / "update-flyer-project").read_text(encoding="utf-8")
    generate = (SCRIPTS / "generate-flyer-concepts").read_text(encoding="utf-8")

    assert "--real-model" in smoke_cli
    assert "--allow-spend" in smoke_cli
    assert "--final-package" in smoke_cli
    assert "--dry-run-bridge" in smoke_cli
    assert "send_dry_run" in smoke_cli
    assert "validate_text_manifest_file" in smoke_cli
    assert "FLYER_STATE_ROOT" in smoke_cli
    assert "json.dumps" in smoke_cli
    assert "flyer_workflow" in update
    assert "src/agents/flyer/workflow.py /opt/shift-agent/flyer_workflow.py" in deploy
    assert "/usr/local/bin/smoke-flyer-quality" in smoke
    assert "sudo -u shift-agent" in smoke and "smoke-flyer-quality --final-package" in smoke
    assert "rm -f /usr/local/bin/smoke-flyer-quality" in deploy
    assert "rm -f /opt/shift-agent/flyer_workflow.py" in deploy
    assert "import flyer_workflow" in smoke
    assert f".{{args.project_id}}.generate.lock" in generate


def test_generation_does_not_hold_file_lock_during_render():
    generate = (SCRIPTS / "generate-flyer-concepts").read_text(encoding="utf-8")
    first_lock_start = generate.index("with FileLock")
    first_lock_end = generate.index("specs = render_concept_previews")
    assert first_lock_end > first_lock_start
    lock_block = generate[first_lock_start:first_lock_end]
    assert "render_concept_previews(" not in lock_block


def test_active_revision_failure_gets_clarification_not_false_noted_message():
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    update = (SCRIPTS / "update-flyer-project").read_text(encoding="utf-8")
    assert "revision_requires_clarification" in hooks
    assert "I need one clarification before regenerating" in hooks
    assert '"revision_requires_clarification": revision_requires_clarification' in update
    assert '"project_id": updated.project_id' in update
    assert "Do not persist an unapplied no-op revision" in update
