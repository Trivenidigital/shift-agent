"""census C1 2026-07-11 — safe_io audit-write isolation guard.

pytest must never write into the deployed audit tree
(/opt/shift-agent/logs/decisions.log). safe_io.ndjson_append raises when a
pytest process targets the production root, unless explicitly opted in. These
tests pin that guard + the SHIFT_AGENT_DECISIONS_LOG_PATH path resolver.

safe_io imports fcntl at module top, so (like test_safe_io_bridge_post) this
suite is Linux-only.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)


@pytest.fixture
def safe_io_module():
    import importlib
    import safe_io
    importlib.reload(safe_io)
    return safe_io


class TestProdAuditWriteGuard:
    def test_blocks_real_prod_path_without_touching_disk(self, safe_io_module, monkeypatch):
        """A pytest write to the real production audit path raises BEFORE any
        filesystem mutation (the guard runs before mkdir/open)."""
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        # PYTEST_CURRENT_TEST is set by the running pytest; assert the premise.
        import os
        assert os.environ.get("PYTEST_CURRENT_TEST")
        prod = Path("/opt/shift-agent/logs/decisions.log")
        with pytest.raises(RuntimeError) as exc:
            safe_io_module.ndjson_append(prod, "{}")
        # Generalized guard (fix/test-prod-path-bleed-class): the message now
        # names the calling chokepoint + the deployed tree instead of the old
        # audit-only phrasing.
        assert "ndjson_append refused" in str(exc.value)
        assert not prod.exists()  # nothing was created

    def test_bypass_env_allows_write(self, safe_io_module, monkeypatch, tmp_path):
        """SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST=1 lets an on-box smoke write."""
        root = tmp_path / "opt" / "shift-agent"
        monkeypatch.setattr(safe_io_module, "_PROD_AUDIT_ROOT", str(root))
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", "1")
        target = root / "logs" / "decisions.log"
        safe_io_module.ndjson_append(target, '{"ok": 1}')
        assert target.read_text(encoding="utf-8").strip() == '{"ok": 1}'

    def test_allows_non_prod_tmp_path(self, safe_io_module, monkeypatch, tmp_path):
        """A tmp path outside the production root writes normally."""
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        target = tmp_path / "logs" / "decisions.log"
        safe_io_module.ndjson_append(target, '{"ok": 2}')
        assert target.read_text(encoding="utf-8").strip() == '{"ok": 2}'

    def test_inert_outside_pytest(self, safe_io_module, monkeypatch, tmp_path):
        """With PYTEST_CURRENT_TEST unset (simulating production), the guard is
        inert even for a path under the (monkeypatched) production root."""
        root = tmp_path / "opt" / "shift-agent"
        monkeypatch.setattr(safe_io_module, "_PROD_AUDIT_ROOT", str(root))
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        target = root / "logs" / "decisions.log"
        safe_io_module.ndjson_append(target, '{"ok": 3}')
        assert target.read_text(encoding="utf-8").strip() == '{"ok": 3}'

    def test_blocks_monkeypatched_root(self, safe_io_module, monkeypatch, tmp_path):
        """The guard keys off _PROD_AUDIT_ROOT — a write under the configured
        root raises regardless of where that root points."""
        root = tmp_path / "opt" / "shift-agent"
        monkeypatch.setattr(safe_io_module, "_PROD_AUDIT_ROOT", str(root))
        monkeypatch.delenv("SHIFT_AGENT_ALLOW_PROD_AUDIT_IN_TEST", raising=False)
        with pytest.raises(RuntimeError):
            safe_io_module.ndjson_append(root / "logs" / "decisions.log", "{}")


class TestDecisionsLogPathResolver:
    def test_default_is_deployed_prod_path(self, safe_io_module, monkeypatch):
        monkeypatch.delenv("SHIFT_AGENT_DECISIONS_LOG_PATH", raising=False)
        assert safe_io_module._decisions_log_path() == safe_io_module._DECISIONS_LOG_PATH
        assert str(safe_io_module._DECISIONS_LOG_PATH) == "/opt/shift-agent/logs/decisions.log"

    def test_env_override_honored(self, safe_io_module, monkeypatch, tmp_path):
        override = tmp_path / "custom" / "decisions.log"
        monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(override))
        assert safe_io_module._decisions_log_path() == override

    def test_prod_audit_root_constant_unchanged(self, safe_io_module):
        assert safe_io_module._PROD_AUDIT_ROOT == "/opt/shift-agent"
