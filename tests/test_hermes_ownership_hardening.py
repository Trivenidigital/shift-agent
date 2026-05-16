from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SERVICE = REPO / "src" / "platform" / "systemd" / "hermes-gateway.service"
PERMISSIONS_SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-hermes-permissions"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SMOKE = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"


def test_gateway_service_uses_targeted_permissions_preflight_not_recursive_chown():
    text = SERVICE.read_text(encoding="utf-8")

    assert "ExecStartPre=+/usr/local/bin/shift-agent-hermes-permissions" in text
    assert "ExecStartPre=/bin/chown -R shift-agent:shift-agent /root/.hermes" not in text


def test_permissions_preflight_targets_runtime_paths_and_quarantines_stale_backups():
    text = PERMISSIONS_SCRIPT.read_text(encoding="utf-8")

    assert "HERMES_HOME=${HERMES_HOME:-/root/.hermes}" in text
    assert "chown -R \"$SERVICE_USER:$SERVICE_GROUP\" \"$HERMES_HOME\"" not in text
    assert "for runtime_dir in skills plugins logs whatsapp" in text
    assert "hermes-config-backups" in text
    assert "config.yaml.bak-*" in text
    assert "verify_readable_as_service_user \"$HERMES_HOME/config.yaml\"" in text
    assert "verify_executable_as_service_user \"$HERMES_HOME/hermes-agent/venv/bin/python\"" in text
    assert "verify_executable_if_exists \"$HERMES_HOME/node/bin/node\"" in text
    assert "Node is optional" in text


def test_deploy_and_smoke_include_permissions_preflight():
    deploy = DEPLOY.read_text(encoding="utf-8")
    smoke = SMOKE.read_text(encoding="utf-8")

    assert "shift-agent-hermes-permissions" in deploy
    assert "/usr/local/bin/shift-agent-hermes-permissions" in smoke
