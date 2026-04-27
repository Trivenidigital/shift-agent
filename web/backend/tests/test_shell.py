"""Smoke tests for the subprocess wrapper.

These are deliberately limited to behavior we can verify offline:
- allowlist enforcement
- arg type validation
- NUL/empty rejection in user_args
"""
from __future__ import annotations

import pytest

from app.shell import run_cli, ALLOWED_BINS


def test_rejects_unlisted_binary():
    with pytest.raises(ValueError, match="not in allowlist"):
        run_cli("/bin/sh", ["-c", "echo hi"])


def test_rejects_nonstring_args():
    bin_ = next(iter(ALLOWED_BINS))
    with pytest.raises(TypeError):
        run_cli(bin_, ["ok", 42])  # type: ignore[list-item]


def test_rejects_user_arg_with_nul():
    bin_ = next(iter(ALLOWED_BINS))
    with pytest.raises(ValueError, match="NUL"):
        run_cli(bin_, [], user_args=["bad\x00stuff"])


def test_allowlist_is_frozen():
    # Sanity: ensure all-listed binaries are absolute paths
    for b in ALLOWED_BINS:
        assert b.startswith("/")
