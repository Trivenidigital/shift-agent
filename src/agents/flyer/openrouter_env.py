"""Shared OpenRouter API-key resolution from env files.

Deduplicated from the three byte-identical copies that previously lived in
``reference_extract``, ``semantic_brief`` and ``visual_qa`` (census C9). Each of
those modules now imports :func:`read_key_from_env_file` / :func:`openrouter_key`
via the flat-name try/except convention and re-binds them under their historic
private names so existing call sites and test monkeypatches keep working.

Deployed flat as ``/opt/shift-agent/flyer_openrouter_env.py`` by
``shift-agent-deploy.sh`` (guarded install, rollback-safe).
"""
from __future__ import annotations

import os
from pathlib import Path


def read_key_from_env_file(path: str) -> str:
    """Return the ``OPENROUTER_API_KEY`` value from a ``.env``-style file, or ``""``.

    Any unreadable file yields ``""`` so the caller falls through to the next
    source rather than crashing. Note ``exists()`` itself can raise
    ``PermissionError`` (py3.11: EACCES is not in pathlib's ignored-errno set when
    the parent denies traversal — e.g. a CI runner reading ``/root/...``), so the
    whole read is wrapped and any ``OSError`` is treated as "no key".
    """
    p = Path(path)
    try:
        if not p.exists():
            return ""
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "OPENROUTER_API_KEY":
                return raw.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def openrouter_key() -> str:
    """Resolve the OpenRouter key: process env first, then the two on-box env files.

    P1-A: the two file paths were HARDCODED here, unlike ``render._read_env_value``
    and ``commerce_livemode_gate.default_key_reader`` which already honour
    ``HERMES_ENV_PATH`` / ``SHIFT_AGENT_ENV_PATH``. That inconsistency is a
    credential door: a copied-state rehearsal can scrub ``os.environ`` and still
    authenticate, because this resolver reads the real ``/root/.hermes/.env``
    straight off the mounted filesystem. Honouring the same two overrides lets a
    rehearsal point every resolver at one sterile file. The defaults are the
    previous literals, so on-box behaviour is unchanged.
    """
    return (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or read_key_from_env_file(os.environ.get("HERMES_ENV_PATH", "/root/.hermes/.env"))
        or read_key_from_env_file(
            os.environ.get("SHIFT_AGENT_ENV_PATH", "/opt/shift-agent/.env")
        )
    )
