from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import credential_readiness as cr


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "src" / "platform" / "scripts" / "credential-minimized-readiness"


def _touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_foundation(tmp_path: Path) -> tuple[Path, Path]:
    hermes_home = tmp_path / "home" / ".hermes"
    install_root = tmp_path / "hermes-agent"
    for skill_id in ("productivity/maps", "productivity/ocr-and-documents", "mcp/native-mcp"):
        _touch(hermes_home / "skills" / skill_id / "SKILL.md", f"# {skill_id}\n")
    return hermes_home, install_root


def _make_cf_router(tmp_path: Path, enabled: bool = True, disabled: bool = False) -> tuple[Path, Path]:
    hermes_home = tmp_path / "home" / ".hermes"
    plugin = hermes_home / "plugins" / "cf-router"
    _touch(plugin / "actions.py", "def classify_catering(text):\n    return False, []\n")
    _touch(plugin / "hooks.py", "def pre_gateway_dispatch(event, gateway, session_store):\n    return None\n")
    enabled_block = "    - cf-router\n" if enabled else ""
    disabled_block = "  disabled:\n    - cf-router\n" if disabled else ""
    config = hermes_home / "config.yaml"
    _touch(config, "plugins:\n  enabled:\n" + enabled_block + disabled_block)
    return hermes_home, config


def test_foundation_skills_resolve_from_live_and_bundled_roots(tmp_path: Path):
    hermes_home = tmp_path / "home" / ".hermes"
    install_root = tmp_path / "hermes-agent"
    _touch(hermes_home / "skills" / "productivity/maps" / "SKILL.md")
    _touch(install_root / "skills" / "productivity/ocr-and-documents" / "SKILL.md")
    _touch(install_root / "skills" / "mcp/native-mcp" / "SKILL.md")

    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=hermes_home,
            hermes_install_root=install_root,
            strict_foundation=True,
            today=cr.parse_date("2026-05-14"),
        )
    )

    statuses = {row["id"]: row["status"] for row in report["foundation"]}
    assert statuses == {
        "productivity/maps": "present",
        "productivity/ocr-and-documents": "present",
        "mcp/native-mcp": "present",
    }
    assert report["strict_foundation_ok"] is True


def test_bundled_only_foundation_skill_warns_but_still_passes(tmp_path: Path):
    """BL-HERMES-12 hardening #1: a foundation skill present only in the bundled install root
    (not live) still PASSES the gate (install-state semantics — bundled counts as present), but the
    text report emits a non-blocking WARN so the load-state gap (the live-only loader won't load a
    bundled-only skill) isn't silent. require-live was rejected as harmful (it would invert the
    pre-install install-state gate and false-fail legitimate bundled-will-sync states)."""
    hermes_home = tmp_path / "home" / ".hermes"
    install_root = tmp_path / "hermes-agent"
    _touch(hermes_home / "skills" / "productivity/maps" / "SKILL.md")           # LIVE
    _touch(install_root / "skills" / "productivity/ocr-and-documents" / "SKILL.md")  # bundled-only
    _touch(install_root / "skills" / "mcp/native-mcp" / "SKILL.md")            # bundled-only

    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=hermes_home,
            hermes_install_root=install_root,
            strict_foundation=True,
            today=cr.parse_date("2026-05-14"),
        )
    )

    assert report["strict_foundation_ok"] is True  # non-blocking: bundled-only still passes
    text = cr.format_text_report(report)
    assert "WARN: productivity/ocr-and-documents present in bundled" in text
    assert "WARN: mcp/native-mcp present in bundled" in text
    assert "WARN: productivity/maps" not in text    # a LIVE skill gets no WARN


def test_local_dev_skill_root_does_not_satisfy_live_strict_mode(tmp_path: Path):
    repo_root = tmp_path / "repo"
    _touch(repo_root / "src" / "agents" / "demo" / "skills" / "maps" / "SKILL.md")

    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=tmp_path / "missing-hermes-home",
            hermes_install_root=tmp_path / "missing-install-root",
            repo_root=repo_root,
            strict_foundation=True,
            today=cr.parse_date("2026-05-14"),
        )
    )

    assert report["strict_foundation_ok"] is False
    missing = {row["id"] for row in report["foundation"] if row["status"] == "missing"}
    assert {"productivity/maps", "productivity/ocr-and-documents", "mcp/native-mcp"} <= missing


def test_local_dev_present_reflects_actual_repo_match(tmp_path: Path):
    """resolve_skill.local_dev_present must be True ONLY for a foundation skill actually present
    under repo_root — not blanket-True whenever repo_root is set. Guards a generator-truthiness
    bug where `any(repo_root.glob(p) for p in ...)` was always True (glob yields a truthy
    generator), so local_dev_present never reflected a real match."""
    repo_root = tmp_path / "repo"
    # Only 'maps' exists locally; ocr-and-documents + native-mcp do NOT.
    _touch(repo_root / "src" / "agents" / "demo" / "skills" / "maps" / "SKILL.md")

    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=tmp_path / "missing-hermes-home",
            hermes_install_root=tmp_path / "missing-install-root",
            repo_root=repo_root,
            strict_foundation=True,
            today=cr.parse_date("2026-05-14"),
        )
    )

    local = {row["id"]: row["local_dev_present"] for row in report["foundation"]}
    assert local["productivity/maps"] is True                # actually present under repo_root
    assert local["productivity/ocr-and-documents"] is False  # NOT in repo — bug would report True
    assert local["mcp/native-mcp"] is False


def test_strict_foundation_ignores_missing_repo_installed_cf_router(tmp_path: Path):
    hermes_home, install_root = _make_foundation(tmp_path)

    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=hermes_home,
            hermes_install_root=install_root,
            strict_foundation=True,
            config_path=hermes_home / "missing-config.yaml",
            today=cr.parse_date("2026-05-14"),
        )
    )

    assert report["strict_foundation_ok"] is True
    assert report["plugin"]["status"] in {"missing", "unknown"}


def test_strict_foundation_report_does_not_import_live_cf_router(tmp_path: Path):
    hermes_home, install_root = _make_foundation(tmp_path)
    plugin = hermes_home / "plugins" / "cf-router"
    marker = tmp_path / "import-side-effect"
    _touch(plugin / "actions.py", "def classify_catering(text):\n    return False, []\n")
    _touch(
        plugin / "hooks.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
        "def pre_gateway_dispatch(event, gateway, session_store):\n    return None\n",
    )
    config = hermes_home / "config.yaml"
    _touch(config, "plugins:\n  enabled:\n    - cf-router\n")

    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=hermes_home,
            hermes_install_root=install_root,
            strict_foundation=True,
            config_path=config,
            today=cr.parse_date("2026-05-14"),
        )
    )

    assert report["strict_foundation_ok"] is True
    assert report["plugin"]["status"] == "present"
    assert report["plugin"]["imports_ok"] is False
    assert not marker.exists()


def test_validate_cf_router_requires_directory_compile_and_config_enablement(tmp_path: Path):
    hermes_home, config = _make_cf_router(tmp_path, enabled=True)
    result = cr.validate_cf_router(
        hermes_home=hermes_home,
        config_path=config,
        strict=True,
    )
    assert result["status"] == "present"
    assert result["enabled"] is True
    assert result["disabled"] is False
    assert result["imports_ok"] is True
    assert not (hermes_home / "plugins" / "cf-router" / "__pycache__").exists()

    hermes_home_disabled, config_disabled = _make_cf_router(tmp_path / "disabled", enabled=False)
    result_disabled = cr.validate_cf_router(
        hermes_home=hermes_home_disabled,
        config_path=config_disabled,
        strict=True,
    )
    assert result_disabled["status"] == "disabled"
    assert result_disabled["enabled"] is False

    hermes_home_deny, config_deny = _make_cf_router(tmp_path / "deny", enabled=True, disabled=True)
    result_deny = cr.validate_cf_router(
        hermes_home=hermes_home_deny,
        config_path=config_deny,
        strict=True,
    )
    assert result_deny["status"] == "disabled"
    assert result_deny["enabled"] is True
    assert result_deny["disabled"] is True

    hermes_home_import, config_import = _make_cf_router(tmp_path / "import-fail", enabled=True)
    (hermes_home_import / "plugins" / "cf-router" / "hooks.py").write_text(
        "from . import missing_module\n",
        encoding="utf-8",
    )
    result_import = cr.validate_cf_router(
        hermes_home=hermes_home_import,
        config_path=config_import,
        strict=True,
    )
    assert result_import["status"] == "import_failed"
    assert result_import["imports_ok"] is False


def test_credential_report_never_leaks_values_paths_or_prefixes(tmp_path: Path):
    secret_path = tmp_path / "very-secret" / "google-service-account.json"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENROUTER_API_KEY=sk-or-v1-super-secret-value",
                "OPENAI_API_KEY=sk-openai-secret-value",
                "AIRTABLE_API_KEY=PLACEHOLDER_fill_me_in",
                "NOTION_API_KEY=MUTED_NOTION_TOKEN",
                f"GOOGLE_APPLICATION_CREDENTIALS={secret_path}",
            ]
        ),
        encoding="utf-8",
    )

    creds = cr.inspect_credentials([env_file])
    rendered_json = json.dumps(creds)
    rendered_text = cr.format_text_report({"credentials": creds, "foundation": [], "plugin": {}, "agents": [], "connectors": [], "whatsapp": {"status": "not_checked"}, "stale_connectors": []})

    forbidden = [
        "sk-or-v1-super-secret-value",
        "sk-openai-secret-value",
        "super-secret",
        "very-secret",
        "google-service-account.json",
        "MUTED_NOTION_TOKEN",
        "PLACEHOLDER_fill_me_in",
    ]
    for needle in forbidden:
        assert needle not in rendered_json
        assert needle not in rendered_text

    statuses = {row["name"]: row["status"] for row in creds}
    assert statuses["OPENROUTER_API_KEY"] == "env_present"
    assert statuses["OPENAI_API_KEY"] == "env_present"
    assert statuses["AIRTABLE_API_KEY"] == "placeholder"
    assert statuses["NOTION_API_KEY"] == "muted"
    assert statuses["GOOGLE_APPLICATION_CREDENTIALS"] == "env_present"


def test_connector_rows_have_freshness_metadata_and_stale_status():
    for row in cr.CONNECTOR_CANDIDATES:
        assert row.last_verified
        assert row.source_url
        assert row.freshness_days > 0

    stale = cr.ConnectorCandidate(
        name="Old Candidate",
        domain="test",
        source_url="https://example.com",
        credential_class="oauth",
        maturity="community",
        market_state="unknown",
        auth_modes=("remote_oauth",),
        deployment_status="candidate",
        last_verified="2026-01-01",
        freshness_days=30,
        notes="fixture",
    )
    assert cr.connector_freshness(stale, today=cr.parse_date("2026-05-14")) == "stale"


def test_connected_candidates_are_candidate_only_not_false_unset(tmp_path: Path):
    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=tmp_path / ".hermes",
            hermes_install_root=tmp_path / "install",
            env_paths=[],
            today=cr.parse_date("2026-05-14"),
        )
    )
    qbo = next(row for row in report["connectors"] if row["name"] == "Intuit QuickBooks Online MCP")
    assert qbo["configured_status"] == "candidate_only"


def test_payment_mcp_candidates_include_stripe_and_razorpay(tmp_path: Path):
    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=tmp_path / ".hermes",
            hermes_install_root=tmp_path / "install",
            env_paths=[],
            today=cr.parse_date("2026-05-27"),
        )
    )
    names = {row["name"] for row in report["connectors"] if row["domain"] == "payments"}
    assert "Stripe MCP" in names
    assert "Razorpay MCP" in names


def test_connector_status_distinguishes_partial_and_complete_env_sets(tmp_path: Path):
    partial_env = tmp_path / ".partial.env"
    partial_env.write_text("QUICKBOOKS_CLIENT_ID=id-only\nPAYPAL_ACCESS_TOKEN=token-only\n", encoding="utf-8")
    partial_report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=tmp_path / ".hermes",
            hermes_install_root=tmp_path / "install",
            env_paths=(partial_env,),
            today=cr.parse_date("2026-05-14"),
        )
    )
    partial = {row["name"]: row["configured_status"] for row in partial_report["connectors"]}
    assert partial["Intuit QuickBooks Online MCP"] == "partial_env"
    assert partial["PayPal MCP Server"] == "partial_env"

    complete_env = tmp_path / ".complete.env"
    complete_env.write_text(
        "\n".join(
            [
                "QUICKBOOKS_CLIENT_ID=id",
                "QUICKBOOKS_CLIENT_SECRET=secret",
                "QUICKBOOKS_REFRESH_TOKEN=refresh",
                "QUICKBOOKS_REALM_ID=realm",
                "QUICKBOOKS_ENVIRONMENT=sandbox",
                "PAYPAL_ACCESS_TOKEN=token",
                "PAYPAL_CLIENT_ID=client",
            ]
        ),
        encoding="utf-8",
    )
    complete_report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=tmp_path / ".hermes",
            hermes_install_root=tmp_path / "install",
            env_paths=(complete_env,),
            today=cr.parse_date("2026-05-14"),
        )
    )
    complete = {row["name"]: row["configured_status"] for row in complete_report["connectors"]}
    assert complete["Intuit QuickBooks Online MCP"] == "env_present"
    assert complete["PayPal MCP Server"] == "env_present"


def test_json_output_shape_is_stable(tmp_path: Path):
    hermes_home, install_root = _make_foundation(tmp_path)
    report = cr.build_report(
        cr.ReadinessOptions(
            hermes_home=hermes_home,
            hermes_install_root=install_root,
            today=cr.parse_date("2026-05-14"),
        )
    )
    parsed = json.loads(cr.format_json_report(report))
    assert set(parsed) >= {
        "foundation",
        "plugin",
        "credentials",
        "agents",
        "connectors",
        "whatsapp",
        "stale_connectors",
    }


def test_staging_script_runs_with_staged_module_before_install(tmp_path: Path):
    if not SCRIPT.exists():
        pytest.fail(f"script missing at {SCRIPT}")
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "sys.path[:0] = roots" in script_text
    assert "sys.path.insert(0" not in script_text

    stage = tmp_path / "staging-new"
    staged_script = stage / "src" / "platform" / "scripts" / SCRIPT.name
    staged_module = stage / "src" / "platform" / "credential_readiness.py"
    staged_script.parent.mkdir(parents=True)
    staged_script.write_text(script_text, encoding="utf-8")
    staged_module.write_text(
        "def main(argv=None):\n"
        "    print('STAGED_MODULE_USED')\n"
        "    return 0\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [sys.executable, str(staged_script), "--format", "json"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "STAGED_MODULE_USED" in proc.stdout


def test_script_subprocess_json_and_strict_exit_codes(tmp_path: Path):
    hermes_home, install_root = _make_foundation(tmp_path)

    proc_ok = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--format",
            "json",
            "--hermes-home",
            str(hermes_home),
            "--hermes-install-root",
            str(install_root),
            "--config",
            str(hermes_home / "missing-config.yaml"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc_ok.returncode == 0, proc_ok.stderr
    assert json.loads(proc_ok.stdout)["strict_foundation_ok"] is True

    (hermes_home / "skills" / "mcp" / "native-mcp" / "SKILL.md").unlink()
    proc_strict = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--strict-foundation",
            "--hermes-home",
            str(hermes_home),
            "--hermes-install-root",
            str(install_root),
            "--config",
            str(hermes_home / "missing-config.yaml"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc_strict.returncode == 1
    assert "mcp/native-mcp" in proc_strict.stdout


def test_plugin_validation_unreadable_config_exits_2(tmp_path: Path):
    hermes_home, _config = _make_cf_router(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--validate-plugin",
            "cf-router",
            "--hermes-home",
            str(hermes_home),
            "--config",
            str(hermes_home / "missing-config.yaml"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2


def test_extensionless_script_loadable_like_existing_clis():
    loader = importlib.machinery.SourceFileLoader("credential_minimized_readiness_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader("credential_minimized_readiness_script", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert hasattr(mod, "main")


def test_deploy_installs_credential_readiness_module_and_runs_staging_gate_before_install():
    deploy = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
    text = deploy.read_text(encoding="utf-8")
    assert "src/platform/credential_readiness.py" in text
    assert "rm -f /usr/local/bin/credential-minimized-readiness" in text
    assert "rm -f /opt/shift-agent/credential_readiness.py" in text
    assert '[ -f "$STAGING/src/platform/scripts/credential-minimized-readiness" ]' in text
    gate = '"$VENV_PY" "$STAGING/src/platform/scripts/credential-minimized-readiness"'
    assert gate in text
    assert text.index(gate) < text.index('install_artifacts "$STAGING"')
    assert text.index(gate) < text.index("state-file migration check")


def test_deploy_foundation_gate_fails_closed_on_missing_script():
    """BL-HERMES-12 hardening: a missing credential-minimized-readiness script must FAIL the
    deploy (mirroring the config-yaml shape gate above it), not silently skip — a missing forward
    gate means a malformed artifact. A deliberate rollback to a pre-gate artifact is allowed ONLY
    via the explicit ALLOW_MISSING_FOUNDATION_GATE override."""
    deploy = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
    text = deploy.read_text(encoding="utf-8")
    start = text.index("=== Credential-minimized Hermes foundation gate ===")
    block = text[start:text.index("state-file migration check", start)]
    # Explicit rollback override present...
    assert "ALLOW_MISSING_FOUNDATION_GATE" in block
    # ...and the missing-script path fails closed (not a bare WARN-skip).
    assert "refusing to deploy without the foundation gate" in block
    # The old silent-skip must be gone.
    assert "skipping foundation gate (rollback compatibility)" not in block


def test_deploy_validates_cf_router_after_install_not_in_preinstall_foundation_gate():
    deploy = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
    text = deploy.read_text(encoding="utf-8")
    post_install = text[text.index('install_artifacts "$STAGING"'):]
    assert "--validate-plugin" in post_install
    assert "cf-router" in post_install

    pre_install = text[: text.index('install_artifacts "$STAGING"')]
    gate_start = pre_install.index("credential-minimized-readiness")
    gate_block = pre_install[gate_start : gate_start + 500]
    assert "--validate-plugin" not in gate_block


def test_deploy_compliance_timezone_uses_hermes_venv_and_warns_on_fallback():
    deploy = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
    text = deploy.read_text(encoding="utf-8")
    assert 'customer_tz=$("${VENV_PY:-/usr/local/lib/hermes-agent/venv/bin/python}"' in text
    assert "python3 -c \"import yaml" not in text
    assert "WARN: unable to read customer.timezone" in text


def test_deploy_install_artifacts_failure_uses_rollback_path():
    deploy = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
    text = deploy.read_text(encoding="utf-8")
    assert 'if ! install_artifacts "$STAGING"; then' in text
    block = text[text.index('if ! install_artifacts "$STAGING"; then') : text.index("# Pre-restart cf-router compile gate")]
    assert '"$0" rollback "$PREV_TAG"' in block
    assert 'rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"' in block
    assert "shift-agent-notify-owner" in block


def test_smoke_runs_readiness_report_non_strict_only():
    smoke = REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"
    text = smoke.read_text(encoding="utf-8")
    assert "/usr/local/bin/credential-minimized-readiness --format text" in text
    snippet_start = text.index("/usr/local/bin/credential-minimized-readiness")
    snippet = text[snippet_start : snippet_start + 200]
    assert "--strict-foundation" not in snippet
    assert "|| true" in snippet


def test_docs_do_not_keep_stale_custom_only_connector_claims():
    roadmap = (REPO_ROOT / "tasks" / "skills-roadmap.md").read_text(encoding="utf-8")
    portfolio = (REPO_ROOT / "docs" / "portfolio.md").read_text(encoding="utf-8")
    analysis = (REPO_ROOT / "tasks" / "hermes-no-key-no-token-analysis-2026-05-14.md").read_text(encoding="utf-8")

    stale_phrases = [
        "No QuickBooks Online skill in ANY source",
        "no QBO skill exists anywhere",
        "No standalone Stripe/Square/PayPal/Venmo/Zelle skill",
        "No DocuSign/HelloSign/PandaDoc/Adobe Sign skill anywhere",
        "Official productivity skills such as `productivity/maps`",
        "are documented in the Hermes catalog but are **not installed",
    ]
    combined = "\n".join([roadmap, portfolio, analysis])
    for phrase in stale_phrases:
        assert phrase not in combined

    required_claims = [
        "vendor MCP or vetted MCP first",
        "Intuit QuickBooks Online MCP",
        "Stripe MCP",
        "Square MCP",
        "DocuSign MCP",
    ]
    for phrase in required_claims:
        assert phrase in roadmap or phrase in analysis or phrase in portfolio
