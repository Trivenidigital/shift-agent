"""Unit tests for skills_manifest — deploy-time SKILL.md content-integrity manifest.

Pure stdlib logic (hashlib/pathlib) → these run cross-platform, including Windows
(unlike the fcntl-gated subprocess suites). Import works because tests/conftest.py
puts src/platform on sys.path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skills_manifest import (
    DuplicateSkillError,
    InvalidSkillNameError,
    audit,
    build_manifest,
    format_manifest,
    main,
    parse_manifest,
    scan_live_skills,
    skill_sha256,
    verify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mk_shipped_skill(agents_root: Path, agent: str, skill: str, content: str) -> Path:
    """Create src-tree layout: <agents_root>/<agent>/skills/<skill>/SKILL.md."""
    d = agents_root / agent / "skills" / skill
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return p


def _mk_live_skill(skills_root: Path, name: str, content: str) -> Path:
    """Create flat on-box layout: <skills_root>/<name>/SKILL.md."""
    d = skills_root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return p


# ── skill_sha256 ────────────────────────────────────────────────────────────

def test_skill_sha256_matches_hashlib(tmp_path: Path):
    p = tmp_path / "SKILL.md"
    p.write_text("hello world", encoding="utf-8")
    assert skill_sha256(p) == _sha("hello world")


# ── build_manifest ──────────────────────────────────────────────────────────

def test_build_manifest_keys_by_skill_dir_name(tmp_path: Path):
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "dispatch_shift_agent", "A")
    _mk_shipped_skill(agents, "shift", "roster_lookup", "B")
    _mk_shipped_skill(agents, "catering", "catering_dispatcher", "C")

    m = build_manifest(agents)
    assert set(m) == {"dispatch_shift_agent", "roster_lookup", "catering_dispatcher"}
    assert m["dispatch_shift_agent"] == _sha("A")
    assert m["catering_dispatcher"] == _sha("C")


def test_build_manifest_dedups_identical_same_name(tmp_path: Path):
    # Same skill name shipped by two agents with IDENTICAL content is fine (dedup).
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "shared_skill", "SAME")
    _mk_shipped_skill(agents, "catering", "shared_skill", "SAME")
    m = build_manifest(agents)
    assert m["shared_skill"] == _sha("SAME")


def test_build_manifest_raises_on_conflicting_duplicate(tmp_path: Path):
    # Same skill name, DIFFERENT content across agents → ambiguous (last-rsync-wins bug).
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "dup", "ONE")
    _mk_shipped_skill(agents, "catering", "dup", "TWO")
    with pytest.raises(DuplicateSkillError):
        build_manifest(agents)


def test_build_manifest_empty_tree_is_empty(tmp_path: Path):
    agents = tmp_path / "agents"
    agents.mkdir()
    assert build_manifest(agents) == {}


# ── format / parse roundtrip ──────────────────────────────────────────────────

def test_format_parse_roundtrip(tmp_path: Path):
    m = {"b_skill": _sha("x"), "a_skill": _sha("y")}
    text = format_manifest(m)
    assert parse_manifest(text) == m


def test_format_is_sorted_and_commented(tmp_path: Path):
    m = {"zzz": _sha("1"), "aaa": _sha("2")}
    text = format_manifest(m)
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    names = [ln.split()[1] for ln in lines]
    assert names == ["aaa", "zzz"]  # deterministic sort by name
    assert text.startswith("#")  # has a comment header


def test_parse_ignores_comments_and_blanks():
    text = "# header\n\n" + _sha("q") + "  only_skill\n\n"
    assert parse_manifest(text) == {"only_skill": _sha("q")}


# ── scan_live_skills ──────────────────────────────────────────────────────────

def test_scan_live_skills_flat_only(tmp_path: Path):
    skills = tmp_path / "skills"
    _mk_live_skill(skills, "dispatch_shift_agent", "A")
    _mk_live_skill(skills, "catering_dispatcher", "C")
    # namespaced foundation skill: productivity/maps/SKILL.md (no SKILL.md at productivity/)
    ns = skills / "productivity" / "maps"
    ns.mkdir(parents=True)
    (ns / "SKILL.md").write_text("MAPS", encoding="utf-8")

    live = scan_live_skills(skills)
    assert set(live) == {"dispatch_shift_agent", "catering_dispatcher"}
    assert "productivity" not in live  # namespaced foundation excluded


def test_scan_live_skills_ignores_dir_without_skillmd(tmp_path: Path):
    skills = tmp_path / "skills"
    _mk_live_skill(skills, "real_skill", "A")
    (skills / "not_a_skill").mkdir(parents=True)  # no SKILL.md
    live = scan_live_skills(skills)
    assert set(live) == {"real_skill"}


def test_scan_live_skills_missing_root_is_empty(tmp_path: Path):
    assert scan_live_skills(tmp_path / "does_not_exist") == {}


# ── verify (D1 deploy gate) ───────────────────────────────────────────────────

def test_verify_clean_when_present_skills_match(tmp_path: Path):
    manifest = {"a": _sha("A"), "b": _sha("B")}
    live = {"a": _sha("A"), "b": _sha("B")}
    r = verify(manifest, live)
    assert r.ok and r.changed == []


def test_verify_detects_content_change(tmp_path: Path):
    manifest = {"a": _sha("A")}
    live = {"a": _sha("A-TAMPERED")}
    r = verify(manifest, live)
    assert not r.ok and r.changed == ["a"]


def test_verify_ignores_manifest_entry_absent_on_box(tmp_path: Path):
    # 'b' not deployed (disabled agent) → NOT a failure (presence gate owns required-set).
    manifest = {"a": _sha("A"), "b": _sha("B")}
    live = {"a": _sha("A")}
    r = verify(manifest, live)
    assert r.ok and r.changed == []


def test_verify_ignores_extra_live_skill(tmp_path: Path):
    # extra skill not in manifest is the WATCHDOG's concern, not the fail-closed deploy gate.
    manifest = {"a": _sha("A")}
    live = {"a": _sha("A"), "rogue": _sha("EVIL")}
    r = verify(manifest, live)
    assert r.ok


# ── audit (D2 watchdog) ───────────────────────────────────────────────────────

def test_audit_clean(tmp_path: Path):
    manifest = {"a": _sha("A")}
    live = {"a": _sha("A")}
    r = audit(manifest, live)
    assert r.clean and r.changed == [] and r.extra == []


def test_audit_detects_changed(tmp_path: Path):
    manifest = {"a": _sha("A")}
    live = {"a": _sha("MUTATED")}
    r = audit(manifest, live)
    assert not r.clean and r.changed == ["a"]


def test_audit_detects_extra_flat_skill(tmp_path: Path):
    # A flat skill not in manifest = the curator-umbrella / autonomous-write failure mode.
    manifest = {"a": _sha("A")}
    live = {"a": _sha("A"), "shift-agent-core": _sha("UMBRELLA")}
    r = audit(manifest, live)
    assert not r.clean and r.extra == ["shift-agent-core"]


def test_audit_foundation_allowlisted_not_extra(tmp_path: Path):
    manifest = {"a": _sha("A")}
    live = {"a": _sha("A"), "some_bundled_flat": _sha("X")}
    r = audit(manifest, live, foundation={"some_bundled_flat"})
    assert r.clean and r.extra == []


def test_audit_missing_is_informational_not_dirty(tmp_path: Path):
    # manifest entry absent on-box (disabled agent) → reported in missing, but NOT unclean.
    manifest = {"a": _sha("A"), "b": _sha("B")}
    live = {"a": _sha("A")}
    r = audit(manifest, live)
    assert r.clean
    assert r.missing == ["b"]


# ── integration with the real repo tree (lockfile invariant) ──────────────────

def test_real_repo_manifest_builds_without_conflict():
    agents_root = REPO_ROOT / "src" / "agents"
    m = build_manifest(agents_root)
    # Sanity: the canonical dispatcher SKILL is always shipped.
    assert "dispatch_shift_agent" in m
    assert all(len(h) == 64 for h in m.values())


def test_committed_baseline_matches_source():
    """Lockfile invariant: tools/skills-manifest.txt MUST equal a fresh build of
    src/agents. If this fails, run `tools/check-skills-manifest.sh build` and commit.
    (Same invariant the build-deploy-tarball build-check enforces at ship time.)"""
    baseline = REPO_ROOT / "tools" / "skills-manifest.txt"
    assert baseline.is_file(), "tools/skills-manifest.txt missing — run the build mode"
    committed = parse_manifest(baseline.read_text(encoding="utf-8"))
    fresh = build_manifest(REPO_ROOT / "src" / "agents")
    assert committed == fresh


# ── CLI (in-process, cross-platform — exercises the JSON+exit-code contract) ───

def _write_manifest(tmp_path: Path, mapping: dict[str, str]) -> Path:
    mf = tmp_path / "skills-manifest.txt"
    mf.write_text(format_manifest(mapping), encoding="utf-8")
    return mf


def _last_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_verify_clean(tmp_path: Path, capsys):
    live = tmp_path / "skills"
    _mk_live_skill(live, "a", "AAA")
    mf = _write_manifest(tmp_path, {"a": _sha("AAA")})
    rc = main(["verify", "--manifest", str(mf), "--skills-root", str(live)])
    payload = _last_json(capsys)
    assert rc == 0 and payload["exit_code"] == 0 and payload["changed"] == []


def test_cli_verify_detects_drift(tmp_path: Path, capsys):
    live = tmp_path / "skills"
    _mk_live_skill(live, "a", "TAMPERED")
    mf = _write_manifest(tmp_path, {"a": _sha("AAA")})
    rc = main(["verify", "--manifest", str(mf), "--skills-root", str(live)])
    payload = _last_json(capsys)
    assert rc == 1 and payload["changed"] == ["a"]


def test_cli_verify_missing_manifest_is_error(tmp_path: Path, capsys):
    rc = main(["verify", "--manifest", str(tmp_path / "nope.txt"), "--skills-root", str(tmp_path)])
    assert rc == 2


def test_cli_audit_reports_extra(tmp_path: Path, capsys):
    live = tmp_path / "skills"
    _mk_live_skill(live, "a", "AAA")
    _mk_live_skill(live, "shift-agent-core", "UMBRELLA")
    mf = _write_manifest(tmp_path, {"a": _sha("AAA")})
    rc = main(["audit", "--manifest", str(mf), "--skills-root", str(live)])
    payload = _last_json(capsys)
    assert rc == 1 and payload["extra"] == ["shift-agent-core"]


def test_cli_build_check_detects_stale(tmp_path: Path, capsys):
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "only_skill", "X")
    stale = _write_manifest(tmp_path, {})  # empty baseline → stale vs source
    rc = main(["build", "--agents-root", str(agents), "--check", str(stale)])
    assert rc == 1


def test_cli_build_check_passes_when_current(tmp_path: Path, capsys):
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "only_skill", "X")
    fresh = _write_manifest(tmp_path, {"only_skill": _sha("X")})
    rc = main(["build", "--agents-root", str(agents), "--check", str(fresh)])
    assert rc == 0


# ── review-hardening: name validation, missing-critical, coverage gaps ────────

def test_build_manifest_rejects_whitespace_name(tmp_path: Path):
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "bad name", "X")  # space in dir name
    with pytest.raises(InvalidSkillNameError):
        build_manifest(agents)


def test_audit_missing_required_makes_unclean(tmp_path: Path):
    manifest = {"dispatch_shift_agent": _sha("A"), "flyer_generation": _sha("B")}
    live = {"flyer_generation": _sha("B")}  # dispatcher deleted between deploys
    r = audit(manifest, live, required={"dispatch_shift_agent"})
    assert not r.clean and r.missing_required == ["dispatch_shift_agent"]


def test_audit_missing_non_required_stays_clean(tmp_path: Path):
    manifest = {"dispatch_shift_agent": _sha("A"), "flyer_generation": _sha("B")}
    live = {"dispatch_shift_agent": _sha("A")}  # flyer disabled/absent = normal
    r = audit(manifest, live, required={"dispatch_shift_agent"})
    assert r.clean and r.missing == ["flyer_generation"] and r.missing_required == []


def test_verify_vacuous_when_live_root_missing(tmp_path: Path):
    # Documented intent: absent skills root -> verify clean (presence gate owns absence).
    manifest = {"a": _sha("A")}
    assert verify(manifest, scan_live_skills(tmp_path / "nonexistent")).ok


def test_build_manifest_ignores_nested_and_stray_skillmd(tmp_path: Path):
    # depth-1 contract: only <agent>/skills/<skill>/SKILL.md is pinned.
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "real", "R")
    nested = agents / "shift" / "skills" / "real" / "sub"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "SKILL.md").write_text("NESTED", encoding="utf-8")
    (agents / "shift" / "SKILL.md").write_text("STRAY", encoding="utf-8")
    assert set(build_manifest(agents)) == {"real"}


def test_cli_audit_required_file_flags_missing_critical(tmp_path: Path, capsys):
    live = tmp_path / "skills"
    _mk_live_skill(live, "keep", "K")  # dispatch_shift_agent absent
    mf = _write_manifest(tmp_path, {"keep": _sha("K"), "dispatch_shift_agent": _sha("D")})
    req = tmp_path / "crit.txt"
    req.write_text("# crit\ndispatch_shift_agent\n", encoding="utf-8")
    rc = main(["audit", "--manifest", str(mf), "--skills-root", str(live), "--required", str(req)])
    assert rc == 1 and _last_json(capsys)["missing_required"] == ["dispatch_shift_agent"]


def test_cli_audit_foundation_file_suppresses_extra(tmp_path: Path, capsys):
    live = tmp_path / "skills"
    _mk_live_skill(live, "a", "A")
    _mk_live_skill(live, "bundled_flat", "B")
    mf = _write_manifest(tmp_path, {"a": _sha("A")})
    found = tmp_path / "found.txt"
    found.write_text("bundled_flat\n", encoding="utf-8")
    rc = main(["audit", "--manifest", str(mf), "--skills-root", str(live), "--foundation", str(found)])
    assert rc == 0 and _last_json(capsys)["extra"] == []


def test_cli_audit_missing_manifest_is_error(tmp_path: Path, capsys):
    rc = main(["audit", "--manifest", str(tmp_path / "nope.txt"), "--skills-root", str(tmp_path)])
    assert rc == 2


def test_cli_build_duplicate_conflict_clean_fail(tmp_path: Path, capsys):
    agents = tmp_path / "agents"
    _mk_shipped_skill(agents, "shift", "dup", "ONE")
    _mk_shipped_skill(agents, "catering", "dup", "TWO")
    out = tmp_path / "m.txt"
    rc = main(["build", "--agents-root", str(agents), "--out", str(out)])
    assert rc == 1 and "FAIL" in capsys.readouterr().err and not out.exists()


def test_cli_build_refuses_empty_write(tmp_path: Path, capsys):
    empty_agents = tmp_path / "agents"
    empty_agents.mkdir()
    out = tmp_path / "m.txt"
    rc = main(["build", "--agents-root", str(empty_agents), "--out", str(out)])
    assert rc == 1 and not out.exists()


def test_committed_critical_list_subset_of_manifest():
    # The critical-skill list must be a subset of the shipped manifest, or a typo would make
    # the watchdog alert on a skill that was never shipped (permanent false missing-critical).
    manifest = parse_manifest((REPO_ROOT / "tools" / "skills-manifest.txt").read_text(encoding="utf-8"))
    crit = {ln.strip() for ln in (REPO_ROOT / "tools" / "skills-critical.txt").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}
    assert not (crit - set(manifest)), f"critical skills not in manifest: {crit - set(manifest)}"
