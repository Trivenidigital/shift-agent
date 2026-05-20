import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "hermes-fleet-upgrade.py"
NORMALIZATION_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "hermes_fleet_normalization"


def load_module():
    spec = importlib.util.spec_from_file_location("hermes_fleet_upgrade", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def good_snapshot(module, *, label="Main", alias="main-vps", commit=None):
    return module.HostSnapshot(
        alias=alias,
        label=label,
        role="production",
        promotion_order=2,
        hermes_commit=commit or "a" * 40,
        hermes_branch="main",
        gateway_status="active",
        cockpit_status="active",
        bridge_status="listening",
        env_symlink_status="ok",
        latest_shift_agent_deploy="deploy-20260520-022938-7ba6b4d3",
        skills_count=681,
        plugins_count=8,
        patch_gate_status="ok",
        checked_at="2026-05-20T12:00:00Z",
        expects_whatsapp=True,
    )


def test_default_fleet_order_is_srilu_main_vpin():
    module = load_module()

    hosts = module.default_fleet_hosts()

    assert [host.label for host in hosts] == ["Srilu", "Main", "VPIN"]
    assert [host.promotion_order for host in hosts] == [1, 2, 3]


def test_classifies_green_host_when_runtime_and_commit_are_healthy():
    module = load_module()
    snapshot = good_snapshot(module, commit="b" * 40)

    health = module.classify_snapshot(snapshot, upstream_commit="b" * 40)

    assert health.status == "green"
    assert health.summary == "ready"
    assert health.blockers == []


@pytest.mark.parametrize(
    "field,value,expected_blocker",
    [
        ("hermes_commit", "", "unknown Hermes commit"),
        ("gateway_status", "inactive", "hermes-gateway inactive"),
        ("bridge_status", "not_listening", "WhatsApp bridge not listening"),
        ("env_symlink_status", "missing", "env symlink not ok"),
        ("patch_gate_status", "failed", "Hermes patch gate failed"),
    ],
)
def test_classifies_blocking_runtime_gaps_as_red(field, value, expected_blocker):
    module = load_module()
    snapshot = good_snapshot(module)
    snapshot = snapshot.replace(**{field: value})

    health = module.classify_snapshot(snapshot, upstream_commit="a" * 40)

    assert health.status == "red"
    assert expected_blocker in health.blockers


def test_non_whatsapp_host_does_not_block_on_missing_bridge():
    module = load_module()
    snapshot = good_snapshot(module).replace(
        bridge_status="not_listening",
        expects_whatsapp=False,
    )

    health = module.classify_snapshot(snapshot, upstream_commit="a" * 40)

    assert health.status == "green"
    assert "WhatsApp bridge not listening" not in health.blockers


def test_missing_optional_cockpit_is_yellow_not_red():
    module = load_module()
    snapshot = good_snapshot(module).replace(cockpit_status="missing")

    health = module.classify_snapshot(snapshot, upstream_commit="a" * 40)

    assert health.status == "yellow"
    assert "shift-agent-cockpit missing" in health.warnings
    assert health.blockers == []


def test_newer_upstream_commit_is_yellow_upgrade_available():
    module = load_module()
    snapshot = good_snapshot(module, commit="a" * 40)

    health = module.classify_snapshot(snapshot, upstream_commit="b" * 40)

    assert health.status == "yellow"
    assert "Hermes upgrade available" in health.warnings


def test_markdown_report_includes_all_hosts_and_stop_conditions():
    module = load_module()
    snapshots = [
        good_snapshot(module, label="Srilu", alias="srilu-vps"),
        good_snapshot(module, label="Main", alias="main-vps"),
        good_snapshot(module, label="VPIN", alias="vpin-vps"),
    ]

    report = module.render_markdown_report(
        snapshots,
        upstream_commit="a" * 40,
        generated_at="2026-05-20T12:00:00Z",
    )

    assert "Hermes Fleet Daily Check" in report
    assert "Srilu" in report
    assert "Main" in report
    assert "VPIN" in report
    assert "Stop Conditions" in report
    assert "gateway inactive" in report


def test_json_report_is_machine_readable_and_redacts_secret_like_fields():
    module = load_module()
    snapshot = good_snapshot(module)

    payload = module.render_json_report(
        [snapshot],
        upstream_commit="a" * 40,
        generated_at="2026-05-20T12:00:00Z",
    )
    parsed = json.loads(payload)

    assert parsed["upstream_commit"] == "a" * 40
    assert parsed["hosts"][0]["label"] == "Main"
    assert "OPENROUTER_API_KEY" not in payload
    assert "OPENAI_API_KEY" not in payload
    assert "secret" not in payload.lower()


def test_high_risk_path_diff_marks_gateway_bridge_and_provider_changes():
    module = load_module()

    risk = module.classify_upstream_changes(
        [
            "README.md",
            "gateway/run.py",
            "scripts/whatsapp-bridge/bridge.js",
            "providers/openrouter.py",
            "skills/productivity/ocr-and-documents/SKILL.md",
        ]
    )

    assert risk.level == "high"
    assert "gateway/run.py" in risk.high_risk_paths
    assert "scripts/whatsapp-bridge/bridge.js" in risk.high_risk_paths
    assert "providers/openrouter.py" in risk.high_risk_paths
    assert "skills/productivity/ocr-and-documents/SKILL.md" in risk.skill_paths
    assert "README.md" in risk.low_risk_paths


def test_skill_only_diff_is_medium_and_not_core_blocking():
    module = load_module()

    risk = module.classify_upstream_changes(
        [
            "skills/creative/image-edit/SKILL.md",
            "skills/productivity/google-workspace/SKILL.md",
        ]
    )

    assert risk.level == "medium"
    assert risk.high_risk_paths == []
    assert len(risk.skill_paths) == 2


def test_render_skill_sync_report_surfaces_relevant_domains():
    module = load_module()
    snapshots = [
        good_snapshot(module, label="Srilu", alias="srilu-vps").replace(skills_count=0, plugins_count=0),
        good_snapshot(module, label="Main", alias="main-vps").replace(skills_count=56, plugins_count=1),
    ]
    risk = module.classify_upstream_changes(
        [
            "skills/productivity/ocr-and-documents/SKILL.md",
            "skills/creative/comfyui/SKILL.md",
            "skills/software-development/github-pr-workflow/SKILL.md",
        ]
    )

    report = module.render_skill_sync_report(
        snapshots,
        risk,
        generated_at="2026-05-20T12:00:00Z",
    )

    assert "Hermes Skill Sync Report" in report
    assert "Srilu" in report
    assert "Main" in report
    assert "ocr-and-documents" in report
    assert "creative/comfyui" in report
    assert "review-before-install" in report


def test_normalization_report_uses_main_as_reference():
    module = load_module()
    main = good_snapshot(module, label="Main", alias="main-vps")
    srilu = good_snapshot(module, label="Srilu", alias="srilu-vps").replace(
        env_symlink_status="missing",
        bridge_status="not_listening",
        patch_gate_status="missing",
        latest_shift_agent_deploy="",
    )
    vpin = good_snapshot(module, label="VPIN", alias="vpin-vps").replace(
        env_symlink_status="missing",
        cockpit_status="missing",
    )

    report = module.render_normalization_report(
        [srilu, main, vpin],
        generated_at="2026-05-20T12:00:00Z",
    )

    assert "Main reference shape" in report
    assert "Srilu" in report
    assert "VPIN" in report
    assert "env symlink" in report
    assert "WhatsApp bridge" in report
    assert "patch gate" in report


def test_normalization_report_accepts_offline_snapshots_and_renders_roles():
    module = load_module()

    snapshots = module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "blocked_snapshots.json")
    report = module.render_normalization_markdown(snapshots)

    assert "Srilu" in report
    assert "Main" in report
    assert "VPIN" in report
    assert "canary" in report


def test_promotion_readiness_requires_srilu_green_before_main_or_vpin():
    module = load_module()

    payload = module.normalization_payload(
        module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "blocked_snapshots.json")
    )

    assert payload["promotion_readiness"]["srilu_to_main"]["ready"] is False
    assert "Srilu must be green before Main promotion" in payload["promotion_readiness"]["srilu_to_main"]["reasons"]


def test_promotion_readiness_turns_green_when_contract_is_green():
    module = load_module()

    payload = module.normalization_payload(
        module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "green_snapshots.json")
    )

    assert payload["promotion_readiness"]["srilu_to_main"]["ready"] is True
    assert payload["promotion_readiness"]["main_to_vpin"]["ready"] is True
    assert payload["promotion_readiness"]["docker_decision"]["status"] == "deferred"


def test_promotion_readiness_requires_green_not_yellow_hosts():
    module = load_module()
    snapshots = module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "green_snapshots.json")
    snapshots["hosts"][0]["patch_gate_status"] = "missing"

    payload = module.normalization_payload(snapshots)

    srilu = next(host for host in payload["hosts"] if host["label"] == "Srilu")
    assert srilu["health"]["status"] == "yellow"
    assert payload["promotion_readiness"]["srilu_to_main"]["ready"] is False
    assert "Srilu must be green before Main promotion" in payload["promotion_readiness"]["srilu_to_main"]["reasons"]


def test_normalization_report_does_not_include_mutation_commands():
    module = load_module()

    snapshots = module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "blocked_snapshots.json")
    report = module.render_normalization_markdown(snapshots)
    forbidden = ["systemctl restart", "scp ", "rsync", "deploy.sh", "git checkout", "hermes update"]

    assert not any(token in report for token in forbidden)


def test_normalization_report_cli_requires_snapshots_json(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "probe_host", lambda *_args, **_kwargs: pytest.fail("must not SSH in normalization-report v0.1"))

    assert module.main(["normalization-report", "--format", "json"]) == 2


def test_normalization_report_cli_uses_snapshot_fixture_without_ssh(monkeypatch, tmp_path):
    module = load_module()
    monkeypatch.setattr(module, "probe_host", lambda *_args, **_kwargs: pytest.fail("must not SSH when snapshots are provided"))
    out = tmp_path / "nested" / "fleet.json"

    code = module.main([
        "normalization-report",
        "--format",
        "json",
        "--snapshots-json",
        str(NORMALIZATION_FIXTURES / "blocked_snapshots.json"),
        "--out",
        str(out),
    ])

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "hosts" in payload
    assert "promotion_readiness" in payload
    assert payload["hosts"][0]["health"]["status"] == "red"


def test_normalization_payload_blocks_stale_snapshot():
    module = load_module()
    snapshots = module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "blocked_snapshots.json")

    payload = module.normalization_payload(snapshots)

    vpin = next(host for host in payload["hosts"] if host["label"] == "VPIN")
    assert any("snapshot stale" in blocker for blocker in vpin["health"]["blockers"])
    assert payload["promotion_readiness"]["main_to_vpin"]["ready"] is False


def test_normalization_json_redacts_secret_like_fields():
    module = load_module()
    snapshots = module.load_normalization_snapshot_payload(NORMALIZATION_FIXTURES / "green_snapshots.json")
    snapshots["hosts"][0]["OPENROUTER_API_KEY"] = "sk-secret"

    payload = module.render_normalization_json(snapshots)

    assert "OPENROUTER_API_KEY" not in payload
    assert "sk-secret" not in payload
    assert "secret" not in payload.lower()


def test_remote_probe_script_is_lf_only_for_linux_bash():
    module = load_module()

    script = module.remote_probe_script()

    assert "\r" not in script
    assert script.startswith("\nset +e\n")


def test_remote_probe_only_runs_installed_patch_gate_when_baseline_exists():
    module = load_module()

    script = module.remote_probe_script()

    assert "/usr/local/bin/hermes-patch-baseline.txt" in script
    assert "[ -f /usr/local/bin/hermes-patch-baseline.txt ]" in script
    assert "rm -f /tmp/hermes_fetch.out /tmp/hermes_patch_gate.out" in script


def test_probe_host_sends_lf_only_bytes_to_ssh(monkeypatch):
    module = load_module()
    captured = {}

    class FakeProcess:
        returncode = 0
        stdout = "hermes_commit=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured["args"] = args[0]
        captured["input"] = kwargs["input"]
        captured["text"] = kwargs.get("text")
        return FakeProcess()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.probe_host(module.default_fleet_hosts()[0])

    assert isinstance(captured["input"], bytes)
    assert b"\r" not in captured["input"]
    assert captured["text"] is False
    assert "-o" in captured["args"]
    assert "BatchMode=yes" in captured["args"]
    assert "ConnectTimeout=10" in captured["args"]


def test_probe_timeout_returns_probe_error(monkeypatch):
    module = load_module()

    def fake_run(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="ssh", timeout=45)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    snapshot = module.probe_host(module.default_fleet_hosts()[0])

    assert snapshot.probe_error
    assert snapshot.alias == "srilu-vps"


def test_validate_candidate_rejects_wrong_length_and_non_hex():
    module = load_module()

    with pytest.raises(ValueError):
        module.validate_candidate("abc")
    with pytest.raises(ValueError):
        module.validate_candidate("g" * 40)


def test_promotion_plan_requires_candidate_sha():
    module = load_module()

    with pytest.raises(ValueError, match="candidate"):
        module.build_promotion_plan("", module.default_fleet_hosts())


def test_promotion_plan_uses_fixed_order_and_review_gates():
    module = load_module()
    candidate = "c" * 40

    plan = module.build_promotion_plan(
        candidate,
        module.default_fleet_hosts(),
        generated_at="2026-05-20T12:00:00Z",
    )

    assert plan.index("Wave 1") < plan.index("Wave 2") < plan.index("Wave 3")
    assert candidate in plan
    assert "tools/patch-hermes.py" in plan
    assert "tools/hermes-patch-baseline.txt" in plan
    assert "shift-agent-deploy.sh" in plan
    assert "Stop immediately" in plan
