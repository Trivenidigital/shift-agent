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
        "handle-flyer-intake",
        "store-flyer-brand-asset",
        "manage-flyer-account",
        "manage-flyer-guest-order",
    ]:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "FileLock" in text
        if name == "manage-flyer-guest-order":
            assert "start_guest_order" in text
        elif name == "handle-flyer-intake":
            assert "handle_intake_message" in text
        else:
            assert "atomic_write_text" in text


def test_manual_queue_exposes_triage_and_backfill():
    queue_cli = (SCRIPTS / "flyer-manual-queue").read_text(encoding="utf-8")
    assert "--triage" in queue_cli
    assert "triage_summary" in queue_cli

    backfill = SCRIPTS / "backfill-flyer-manual-reasons"
    assert backfill.is_file(), "backfill-flyer-manual-reasons script missing"
    body = backfill.read_text(encoding="utf-8")
    assert "backfill_manual_reasons" in body
    assert "--apply" in body
    assert "FileLock" in body
    assert "atomic_write_text" in body


def test_manual_transition_sites_use_helper():
    """Every code-path transition into manual_edit_required goes through make_manual_review()."""
    for name in ["create-flyer-project", "generate-flyer-concepts", "finalize-flyer-assets", "update-flyer-project"]:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "make_manual_review" in text, f"{name} should call make_manual_review"

    update_text = (SCRIPTS / "update-flyer-project").read_text(encoding="utf-8")
    assert "--manual-reason-code" in update_text
    assert "reason_code" in update_text


def test_create_flyer_project_manual_edit_path_populates_reason_code():
    """The forward-path bug that produced the 6 prod dead-letter projects:
    --manual-edit-required without a reference failure must still populate manual_review.reason_code,
    not leave it at the default 'unclassified'."""
    text = (SCRIPTS / "create-flyer-project").read_text(encoding="utf-8")
    # The fix: when args.manual_edit_required is set but no reference failure,
    # build a manual_review via the helper with a concrete reason_code.
    assert "args.manual_edit_required and not reference_manual_required" in text
    assert "source_edit_provider_unavailable" in text


def test_delivery_script_can_send_by_project_id():
    text = (SCRIPTS / "send-flyer-package").read_text(encoding="utf-8")
    finalize = (SCRIPTS / "finalize-flyer-assets").read_text(encoding="utf-8")
    assert "--project-id" in text
    assert "validate_text_manifest_file" in text
    assert "--allow-unverified-asset" in text
    assert "--allow-sidecar-visual-qa" in text
    assert "--dry-run-bridge" in text
    assert "FLYER_TEXT_QA_BREAK_GLASS" in text
    assert "project.status != \"finalizing_assets\"" in text
    assert "FINAL_KIND_TO_FORMAT" in text
    assert "validate_visual_qa_report" in text
    assert "output_format=expected_output_format" in text
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
    smoke = (SCRIPTS / "smoke-flyer-quality").read_text(encoding="utf-8")
    assert "write_visual_qa_report" in smoke
    assert "--allow-sidecar-visual-qa" in smoke


def test_delivery_report_installed_and_smoked_for_operator_visibility():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    report = (SCRIPTS / "flyer-delivery-report").read_text(encoding="utf-8")

    assert "flyer-delivery-report" in deploy
    assert "flyer-delivery-report" in smoke
    assert "build_delivery_report" in report
    assert "--json" in report
    assert "uncertain_asset_ids" in report


def test_guest_order_script_installed_for_quick_flyer_path():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    script = (SCRIPTS / "manage-flyer-guest-order").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")

    assert "manage-flyer-guest-order" in deploy
    assert "manage-flyer-guest-order" in smoke
    assert "flyer_guest_order.py" in deploy
    assert "--start" in script
    assert "--activate" in script
    assert "--reserve" in script
    assert "--release" in script
    assert "--consume" in script
    assert "--find-paid" in script
    assert "--find-reserved" in script
    assert "is_quick_flyer_campaign_cta" in actions
    assert "trigger_start_flyer_guest_order" in actions
    assert "trigger_reserve_flyer_guest_order" in actions
    assert "trigger_release_flyer_guest_order" in actions
    assert "trigger_consume_flyer_guest_order" in actions
    assert "find_paid_flyer_guest_order" in actions
    assert "find_reserved_flyer_guest_order" in actions
    assert "quick_flyer_payment" in hooks


def test_flyer_dispatcher_skill_does_not_bypass_cf_router_quota_gate():
    skill = (SCRIPTS.parent / "skills" / "flyer_dispatcher" / "SKILL.md").read_text(encoding="utf-8")

    assert "cf-router" in skill
    assert "do not call project/render/delivery scripts directly" in skill
    assert "account status, quota/guest-order reservation" in skill
    assert "Do not send final" in skill


def test_intake_script_installed_for_language_and_guided_mode():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    script = (SCRIPTS / "handle-flyer-intake").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")

    assert "handle-flyer-intake" in deploy
    assert "handle-flyer-intake" in smoke
    assert "flyer_intake.py" in deploy
    assert "handle_intake_message" in script
    assert "trigger_flyer_intake" in actions
    assert "_try_flyer_intake_intercept" in hooks
    assert "is_vague_flyer_start" in hooks


def test_starter_briefs_module_installed_and_smoked():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")

    assert "src/agents/flyer/starter_briefs.py" in deploy
    assert "/opt/shift-agent/flyer_starter_briefs.py" in deploy
    assert "flyer_starter_briefs" in smoke


def test_flyer_intent_module_installed_and_smoked_for_cf_router_shadow():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")

    assert "src/agents/flyer/intent.py" in deploy
    assert "/opt/shift-agent/flyer_intent.py" in deploy
    assert "flyer_intent" in smoke
    assert "_import_flyer_intent_contract" in actions
    assert "import flyer_intent" in actions


def test_starter_prompt_claims_use_locked_account_script():
    script = (SCRIPTS / "manage-flyer-account").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")

    assert "--claim-starter-prompt" in script
    assert "--release-starter-prompt" in script
    assert "with FileLock(Path(str(state_path) + \".lock\")):" in script
    assert "_trigger_flyer_account_state" in actions
    assert "--claim-starter-prompt" in actions
    assert "--release-starter-prompt" in actions


def test_flyer_campaign_cta_script_installed_and_smoked():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    symbols = (REPO / "src" / "platform" / "scripts" / "check-safe-io-symbols").read_text(encoding="utf-8")
    patcher = (REPO / "tools" / "patch-hermes.py").read_text(encoding="utf-8")
    script = (SCRIPTS / "send-flyer-campaign").read_text(encoding="utf-8")

    assert "send-flyer-campaign" in deploy
    assert "send-flyer-campaign" in smoke
    assert "bridge_send_cta" in symbols
    assert "app.post('/send-cta'" in smoke
    assert "BEGIN shift-agent-cta-buttons" in patcher
    assert "app.post('/send-cta'" in patcher
    assert "bridge_send_cta" in script
    assert "bridge_send_media" in script
    assert "Start Free Trial" in script
    assert "Create One Flyer - $4" in script
    assert "Act Now! Save Time and Money" in script
    assert "DEFAULT_START_TRIAL_MESSAGE" in script
    assert "DEFAULT_QUICK_FLYER_MESSAGE" in script
    assert "DEFAULT_ACT_NOW_MESSAGE" in script
    assert "Help me create a beautiful flyer for my business" in script
    assert "I want to set up Flyer Studio for my business" in script
    assert "--start-trial-message" in script
    assert "--quick-flyer-message" in script
    assert "--act-now-message" in script
    assert '"message": args.start_trial_message' in script
    assert '"message": args.quick_flyer_message' in script
    assert '"message": args.act_now_message' in script
    assert '"url":' not in script
    assert "URL buttons are intentionally not used" in script
    assert "media_message_id" in script
    assert "cta_message_id" in script
    assert "quick_reply" in patcher
    assert "nativeFlowMessage" in patcher
    assert "interactiveResponseMessage" in patcher
    assert "buttonsResponseMessage" in patcher
    assert "cta_url" not in patcher
    assert "relayMessage" in patcher


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
    assert "I'm creating your flyer now" in actions
    assert "5-6 minutes" in actions
    assert "send a preview here shortly" in actions
    assert hooks.index("send_flyer_processing_ack(chat_id, project_id)") < hooks.index("trigger_generate_flyer_concepts(project_id)")


def test_reference_manual_fallback_copy_reaches_resume_paths_and_releases_source_edit_quota():
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")
    actions = (REPO / "src" / "plugins" / "cf-router" / "actions.py").read_text(encoding="utf-8")

    assert "def send_flyer_manual_review_ack" in actions
    assert hooks.count("flyer_generation_queued_manual_review(gen_detail)") >= 4
    assert hooks.count("send_flyer_manual_review_ack(") >= 4
    assert "source_edit_preflight_failed" in hooks
    assert "release_ok={release_ok}; release_detail={release_detail[:250]}" in hooks


def test_deploy_smoke_exercises_deferred_reference_extraction():
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")

    assert "--defer-reference-extraction" in smoke
    assert "FLYER_REFERENCE_ALLOW_SIDECAR=1" in smoke
    assert "Flyer deferred reference extraction smoke passed" in smoke


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
    assert "def flyer_campaign_cta_text" in actions
    assert "def is_flyer_campaign_cta" in actions
    assert "def find_flyer_onboarding_session_by_sender" in actions
    assert "def _try_flyer_campaign_cta_intercept" in hooks
    assert "def _try_flyer_existing_onboarding_intercept" in hooks
    assert "def flyer_project_has_required_fields" in actions
    assert "_NEW_FLYER_REQUEST" in actions
    assert "_FLYER_CAMPAIGN_CTA" in actions
    assert "_MEDIA_TEMPLATE_EDIT" in actions
    assert "_WRONG_FLYER_CORRECTION" in actions
    assert "force_new=True" in hooks
    assert hooks.index("flyer_campaign_cta_text(text)") < hooks.index("should_start_new_flyer_over_active(text, has_media=bool(media_path))")
    assert hooks.index("_try_flyer_brand_asset_intercept(text, chat_id, event, media_path)") < hooks.index("should_start_new_flyer_over_active(text, has_media=bool(media_path))")
    assert "def _try_flyer_reference_scope_choice_intercept" in hooks
    assert "def _try_flyer_reference_scope_authorization_intercept" in hooks
    assert "save_flyer_reference_scope_pending" in hooks
    assert "save_flyer_reference_authorization_pending" in hooks
    assert "consume_flyer_reference_scope_choice" in actions
    assert "consume_flyer_reference_authorization_reply" in actions
    assert "Please resend the flyer request" not in hooks
    assert "Authorized flyer/source artwork" in hooks
    assert "manual_edit_required=True" in hooks
    assert hooks.index("_try_flyer_reference_scope_choice_intercept(text, chat_id, event)") < hooks.index("_try_flyer_active_project_intercept(text, chat_id, event, media_path)")
    assert hooks.index("_try_flyer_reference_scope_authorization_intercept(text, chat_id, event)") < hooks.index("_try_flyer_active_project_intercept(text, chat_id, event, media_path)")


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
    assert "_try_flyer_existing_onboarding_intercept" in hooks
    assert hooks.index("_try_flyer_brand_asset_intercept(text, chat_id, event, media_path)") < hooks.index("_try_flyer_existing_onboarding_intercept(text, chat_id, event)")
    assert hooks.index("_try_flyer_existing_onboarding_intercept(text, chat_id, event)") < hooks.index("should_start_new_flyer_over_active(text, has_media=bool(media_path))")
    assert hooks.index("_try_flyer_existing_onboarding_intercept(text, chat_id, event)") < hooks.index("_try_flyer_active_project_intercept")
    assert hooks.index("_try_flyer_active_project_intercept") < hooks.index("_try_flyer_onboarding_intercept")


def test_manual_queue_cli_has_no_send_close_action():
    script = (SCRIPTS / "flyer-manual-queue").read_text(encoding="utf-8")
    assert "close_manual_project" in script
    assert 'parser.add_argument("--close"' in script
    assert '"status": "closed_no_send"' in script


def test_flyer_launch_marketing_pack_exists_and_has_trial_ctas():
    pack = (REPO / "docs" / "marketing" / "flyer-studio-launch-funnel.md").read_text(encoding="utf-8")
    sales_flyer = (REPO / "docs" / "marketing" / "flyer-studio-sales-flyer.html").read_text(encoding="utf-8")
    gallery = (REPO / "docs" / "marketing" / "flyer-studio-sample-gallery.html").read_text(encoding="utf-8")
    qr = REPO / "docs" / "marketing" / "flyer-studio-trial-qr.png"
    required = [
        "Send a WhatsApp message. Get a professional flyer back.",
        "3 free sample flyers",
        "Try 3 flyers free on WhatsApp",
        "https://wa.me/918522041562?text=START%20FREE%20TRIAL%20-%20I%20want%20to%20try%20Flyer%20Studio",
        "$49.99",
        "$69.99",
        "$199",
        "restaurants",
        "temples",
        "salons",
        "tutors",
        "realtors",
        "opt-in",
        "STOP",
    ]
    for phrase in required:
        assert phrase in pack
    assert "<AGENT_NUMBER>" not in pack
    assert "<FREE_TRIAL_WA_LINK>" not in pack
    assert qr.exists()
    assert qr.stat().st_size > 1000
    for phrase in [
        "Hermes Flyer Studio",
        "Try 3 flyers free",
        "Restaurant Special",
        "Temple Event",
        "Salon Promo",
        "Tutor Class",
        "Realtor Open House",
        "$49.99",
        "$69.99",
        "$199",
    ]:
        assert phrase in sales_flyer
        assert phrase in gallery


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
    assert "/usr/local/bin/check-flyer-reference-scope" in smoke
    assert "sudo -u shift-agent" in smoke and "smoke-flyer-quality --final-package" in smoke
    assert "rm -f /usr/local/bin/smoke-flyer-quality" in deploy
    assert "rm -f /opt/shift-agent/flyer_workflow.py" in deploy
    assert "import flyer_workflow" in smoke
    assert f".{{args.project_id}}.generate.lock" in generate
    assert "source_edit_requested" in generate
    assert "authorized flyer/source artwork update" in generate


def test_flyer_generation_scripts_resolve_draft_and_final_provider_policy():
    generate = (SCRIPTS / "generate-flyer-concepts").read_text(encoding="utf-8")
    finalize = (SCRIPTS / "finalize-flyer-assets").read_text(encoding="utf-8")

    assert "source_edit_provider = cfg.flyer.resolve_source_edit_render_provider()" in generate
    assert "provider=source_edit_provider.provider" in generate
    assert "model=source_edit_provider.model" in generate
    assert "quality=source_edit_provider.quality" in generate
    assert "cfg.flyer.edit_image_model" not in generate

    assert "draft_provider = cfg.flyer.resolve_draft_render_provider()" in generate
    assert "model=draft_provider.model" in generate
    assert "quality=draft_provider.quality" in generate
    assert "cfg.flyer.draft_image_model" not in generate

    assert "final_provider = cfg.flyer.resolve_final_render_provider()" in finalize
    assert "model=final_provider.model" in finalize
    assert "quality=final_provider.quality" in finalize
    assert "cfg.flyer.final_image_model" not in finalize


def test_source_edit_generation_failure_does_not_overwrite_script_manual_review_state():
    hooks = (REPO / "src" / "plugins" / "cf-router" / "hooks.py").read_text(encoding="utf-8")

    assert "if not actions.flyer_generation_queued_manual_review(gen_detail):" in hooks
    assert '"--manual-reason", "source_edit_generation_failed"' in hooks


def test_production_readiness_modules_installed_and_smoked():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")

    for module in [
        "flyer_facts",
        "flyer_reference_extract",
        "flyer_visual_qa",
        "flyer_manual_queue",
    ]:
        assert f"/opt/shift-agent/{module}.py" in deploy
        assert f"import {module}" in smoke

    assert "flyer-manual-queue" in deploy
    assert "flyer-manual-queue" in smoke


def test_cockpit_deploy_restart_uses_health_probe_without_systemctl_wait():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")

    assert "systemctl restart --wait shift-agent-cockpit.service" not in deploy
    assert "systemctl restart shift-agent-cockpit.service" in deploy
    assert "http://127.0.0.1:8081/health" in deploy


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
    assert "_send_flyer_regeneration_failed_ack" in hooks
    assert "approve_regeneration_failed=true" in hooks
    assert "failure_ack_sent=true" in hooks
    assert '"revision_requires_clarification": revision_requires_clarification' in update
    assert '"project_id": updated.project_id' in update
    assert "if revision_requires_clarification:" in update
    assert "return 0" in update
