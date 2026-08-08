"""Governance checker contract tests.

Exercises tools/check-architecture-governance.py against the REAL registry in
this repository (positive cases) and against synthetic registries written to
tmp_path (negative cases). Governance is CI-only, so these tests never touch
agent state, never import runtime modules, and never shell out to a VPS.

Case list mirrors the governance spec:
  1. Catering-only change loads the Catering directive
  2. Flyer-only change does not load the Catering directive
  3. Shared-platform change requires affected-agent declarations
  4. Multi-project PR requires multiple Reuse Map sections
  5. Unknown path fails / requests classification
  6. Overlapping path without declaration fails
  7. Valid approved exception passes
  8. Catering exception cannot cover a Flyer path
  9. Expired exception fails
 10. Missing Reuse Map fails
 11. New declared subsystem without exception fails
 12. Thin-adapter change using existing kernels passes
 13. Broken nested AGENTS pointer fails
 14. Cursor path scopes do not overlap accidentally
 15. Registry supports at least the discovered number of agents
 16. Adding a new registered agent requires a directive
 17. Governance-only changes do not alter runtime behavior
"""
from __future__ import annotations

import importlib.util
import shutil
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "tools" / "check-architecture-governance.py"
REGISTRY_REL = "docs/governance/project-registry.yaml"
EXCEPTIONS_REL = "docs/governance/architecture-exceptions.yaml"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_architecture_governance", CHECKER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gov = _load_module()


# ── helpers ────────────────────────────────────────────────────────────────


def run_checker(root: Path, *, changed=None, added=None, body=None, registry_only=False,
                today="2026-08-01") -> tuple[int, str]:
    """Invoke the checker as a subprocess, matching how CI calls it."""
    args = [sys.executable, str(CHECKER), "--repo-root", str(root), "--today", today]
    if registry_only:
        args.append("--registry-only")
    # Scratch lives outside the repository: a governance test must never write
    # into the tree it is auditing.
    tmp = Path(tempfile.mkdtemp(prefix="governance-check-"))
    if changed is not None:
        cf = tmp / "changed.txt"
        cf.write_text("\n".join(changed) + "\n", encoding="utf-8")
        args += ["--changed-files", str(cf)]
        af = tmp / "added.txt"
        af.write_text("\n".join(added or []) + "\n", encoding="utf-8")
        args += ["--added-files", str(af)]
    if body is not None:
        bf = tmp / "body.md"
        bf.write_text(body, encoding="utf-8")
        args += ["--pr-body", str(bf)]
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def reuse_map(**overrides) -> str:
    """A complete, passing Capability Reuse Map. Override any field."""
    values = {
        "Requested outcome": "deliver the requested vertical slice",
        "Affected projects": "catering-studio",
        "Applicable directives": "docs/governance/projects/catering-studio.md",
        "Existing platform/model capabilities reused": "Hermes vision extraction via parse-menu-photo",
        "Existing deterministic kernels reused": "catering_pricing.py, import-catering-pricebook",
        "Existing stores/workflows reused": "catering-leads.json, existing approval workflow",
        "Thin adapters": "menu-to-pricebook adapter",
        "Custom runtime code genuinely unavoidable": "none",
        "New subsystem": "none",
        "Evidence existing capabilities were insufficient": "n/a - no new subsystem",
        "Architecture exception": "none",
        "Shared-platform impact": "none",
        "Other agents affected": "none",
        "Vertical E2E proof": "inbound WhatsApp menu photo through to priced pricebook",
    }
    assert set(values) == set(gov.REUSE_MAP_FIELDS), (
        "test fixture drifted from the enforced schema: "
        f"{set(values) ^ set(gov.REUSE_MAP_FIELDS)}"
    )
    values.update(overrides)
    lines = ["## Summary", "", "test body", "", "## Capability Reuse Map", ""]
    lines += [f"- {k}: {v}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A minimal but structurally complete governance tree we can corrupt."""
    root = tmp_path / "repo"
    for rel in gov.REQUIRED_FILES:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, dst)
    shutil.copytree(REPO_ROOT / "docs" / "governance" / "projects",
                    root / "docs" / "governance" / "projects")
    for rule in (REPO_ROOT / ".cursor" / "rules").glob("*.mdc"):
        shutil.copy(rule, root / ".cursor" / "rules" / rule.name)
    for proj in _registry(REPO_ROOT)["projects"]:
        nested = proj.get("nested_agents_file")
        if nested:
            dst = root / nested
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO_ROOT / nested, dst)
    return root


def _registry(root: Path) -> dict:
    return yaml.safe_load((root / REGISTRY_REL).read_text(encoding="utf-8"))


def _write_registry(root: Path, data: dict) -> None:
    (root / REGISTRY_REL).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_exceptions(root: Path, entries: list[dict]) -> None:
    (root / EXCEPTIONS_REL).write_text(
        yaml.safe_dump(
            {"version": "1.0.0", "statuses": list(gov.EXCEPTION_STATUSES), "exceptions": entries},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def approved_exception(**overrides) -> dict:
    entry = {
        "id": "HERMES-EX-001",
        "status": "approved",
        "affected_project": "catering-studio",
        "affected_paths": ["src/agents/catering/store_v2.py"],
        "subsystem_type": "store",
        "scope": "one new catering-local store",
        "reason": "test fixture",
        "existing_capability_found_insufficient": "documented",
        "evidence": "documented",
        "safety_justification": "documented",
        "approver": "product-owner",
        "approval_date": "2026-07-01",
        "expiration_or_review_condition": "review 2027-01-01",
    }
    entry.update(overrides)
    return entry


def classify(path: str) -> str | None:
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry(), checker.findings
    owner, _, _ = checker.matcher.classify(path)
    return owner


# ── 0. baseline ────────────────────────────────────────────────────────────


def test_real_registry_passes_integrity_checks():
    code, out = run_checker(REPO_ROOT, registry_only=True)
    assert code == 0, out


def test_every_tracked_file_classifies():
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry(), checker.findings
    unknown = [
        p for p in tracked
        if not checker.matcher.is_excluded(p) and checker.matcher.classify(p)[0] is None
    ]
    assert unknown == [], f"unclassified tracked files: {unknown[:10]}"


# ── 1 & 2. path-scoped directive loading ───────────────────────────────────


def test_case1_catering_change_loads_catering_directive():
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry()
    affected = checker.classify_changes(
        ["src/agents/catering/scripts/import-catering-pricebook", "src/platform/catering_pricing.py"]
    )
    assert affected == ["catering-studio"]
    assert checker.by_id["catering-studio"].directive == "docs/governance/projects/catering-studio.md"


def test_case2_flyer_change_does_not_load_catering_directive():
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry()
    affected = checker.classify_changes(
        ["src/agents/flyer/visual_qa.py", "src/platform/flyer_identity.py",
         "web/frontend/src/sections/flyer/ManualQueueActions.tsx"]
    )
    assert affected == ["flyer-studio"]
    assert "catering-studio" not in affected


@pytest.mark.parametrize(
    "path,owner",
    [
        ("src/platform/catering_pricing.py", "catering-studio"),
        ("src/platform/flyer_identity.py", "flyer-studio"),
        ("src/platform/qbo_client.py", "expense-bookkeeper"),
        ("src/platform/commerce/cart.py", "commerce-platform"),
        ("src/platform/safe_io.py", "shift-platform"),
        ("src/agents/shift/skills/dispatch_shift_agent/SKILL.md", "shift-platform"),
        ("src/agents/shift/skills/roster_lookup/SKILL.md", "shift-agent"),
        ("web/backend/app/routers/catering.py", "catering-studio"),
        ("web/backend/app/routers/auth.py", "cockpit"),
        ("src/agents/vip/skills/vip_dispatcher/SKILL.md", "phase0-agents"),
    ],
)
def test_ownership_follows_owner_not_directory(path, owner):
    assert classify(path) == owner


# ── 3. shared-platform impact ──────────────────────────────────────────────


def test_case3_shared_platform_change_requires_affected_agents():
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "none",
    })
    code, out = run_checker(
        REPO_ROOT, changed=["src/platform/safe_io.py"], body=body,
    )
    assert code == 1
    assert "GOV-PR-SHARED" in out
    assert "GOV-PR-SHARED-AGENTS" in out


def test_case3_shared_platform_change_passes_when_agents_named():
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "locking helper used by catering-studio and flyer-studio",
        "Existing deterministic kernels reused": "safe_io.atomic_write_json",
    })
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert code == 0, out


def test_shared_platform_test_only_change_does_not_demand_impact():
    """Registry marks impact_analysis_paths; a tests/ change is not runtime."""
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "none",
    })
    code, out = run_checker(REPO_ROOT, changed=["tests/test_schemas.py"], body=body)
    assert code == 0, out


# ── 4. multi-project ───────────────────────────────────────────────────────


def test_case4_multi_project_pr_requires_every_project_named():
    body = reuse_map(**{"Affected projects": "catering-studio"})
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py", "src/agents/flyer/render.py"],
        body=body,
    )
    assert code == 1
    assert "GOV-PR-PROJECT" in out and "flyer-studio" in out


def test_case4_multi_project_pr_passes_when_all_named():
    body = reuse_map(**{
        "Affected projects": "catering-studio, flyer-studio",
        "Applicable directives": (
            "docs/governance/projects/catering-studio.md, "
            "docs/governance/projects/flyer-studio.md"
        ),
    })
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py", "src/agents/flyer/render.py"],
        body=body,
    )
    assert code == 0, out


# ── 5. unknown paths ───────────────────────────────────────────────────────


def test_case5_unknown_path_fails_closed():
    code, out = run_checker(
        REPO_ROOT, changed=["src/agents/brand_new_agent/thing.py"], body=reuse_map(),
    )
    assert code == 1
    assert "GOV-UNCLASSIFIED" in out
    assert "classify it in" in out


def test_case5_excluded_path_is_not_an_unknown():
    code, out = run_checker(
        REPO_ROOT, changed=["web/frontend/package-lock.json"], body=reuse_map(),
    )
    assert "GOV-UNCLASSIFIED" not in out


# ── 6. overlaps ────────────────────────────────────────────────────────────


def test_case6_undeclared_overlap_fails(sandbox: Path):
    data = _registry(sandbox)
    for proj in data["projects"]:
        if proj["id"] == "flyer-studio":
            proj["paths"]["source"].append("src/agents/catering/**")
    _write_registry(sandbox, data)
    code, out = run_checker(
        sandbox, changed=["src/agents/catering/deposit.py"], body=reuse_map(),
    )
    assert code == 1
    assert "GOV-OVERLAP" in out


def test_case6_declared_overlap_passes(sandbox: Path):
    data = _registry(sandbox)
    for proj in data["projects"]:
        if proj["id"] == "flyer-studio":
            proj["paths"]["source"].append("src/agents/catering/**")
    data["overlaps"].append({
        "id": "OV-TEST",
        "projects": ["catering-studio", "flyer-studio"],
        "paths": ["src/agents/catering/**"],
        "resolution": "longest-literal-prefix-wins",
        "reason": "test fixture",
    })
    _write_registry(sandbox, data)
    body = reuse_map(**{"Affected projects": "catering-studio, flyer-studio"})
    code, out = run_checker(sandbox, changed=["src/agents/catering/deposit.py"], body=body)
    assert "GOV-OVERLAP" not in out, out


# ── 7-9. exceptions ────────────────────────────────────────────────────────


def test_case7_approved_exception_in_scope_passes(sandbox: Path):
    _write_exceptions(sandbox, [approved_exception()])
    body = reuse_map(**{
        "New subsystem": "catering-local store",
        "Architecture exception": "HERMES-EX-001",
        "Evidence existing capabilities were insufficient": "documented in the exception",
    })
    code, out = run_checker(
        sandbox,
        changed=["src/agents/catering/store_v2.py"],
        added=["src/agents/catering/store_v2.py"],
        body=body,
    )
    assert code == 0, out


def test_case8_catering_exception_cannot_cover_flyer_path(sandbox: Path):
    _write_exceptions(sandbox, [approved_exception(
        affected_paths=["src/agents/catering/store_v2.py", "src/agents/flyer/store_v2.py"],
    )])
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-EXC-SCOPE" in out
    assert "flyer-studio" in out


def test_case8_cited_exception_does_not_cover_changed_flyer_file(sandbox: Path):
    _write_exceptions(sandbox, [approved_exception()])
    body = reuse_map(**{
        "Affected projects": "catering-studio, flyer-studio",
        "New subsystem": "a store",
        "Architecture exception": "HERMES-EX-001",
    })
    code, out = run_checker(
        sandbox,
        changed=["src/agents/catering/store_v2.py", "src/agents/flyer/store_v2.py"],
        added=["src/agents/flyer/store_v2.py"],
        body=body,
    )
    assert code == 1
    assert "GOV-EXC-UNCOVERED" in out or "GOV-SUBSYSTEM-NOEXC" in out


def test_case9_expired_exception_fails(sandbox: Path):
    _write_exceptions(sandbox, [approved_exception(
        expiration_or_review_condition="expires 2026-01-01",
    )])
    body = reuse_map(**{
        "New subsystem": "catering-local store",
        "Architecture exception": "HERMES-EX-001",
    })
    code, out = run_checker(
        sandbox,
        changed=["src/agents/catering/store_v2.py"],
        added=["src/agents/catering/store_v2.py"],
        body=body,
        today="2026-08-01",
    )
    assert code == 1
    assert "GOV-EXC-EXPIRED" in out


def test_rejected_exception_authorizes_nothing(sandbox: Path):
    _write_exceptions(sandbox, [approved_exception(status="rejected")])
    body = reuse_map(**{
        "New subsystem": "catering-local store",
        "Architecture exception": "HERMES-EX-001",
    })
    code, out = run_checker(
        sandbox,
        changed=["src/agents/catering/store_v2.py"],
        added=["src/agents/catering/store_v2.py"],
        body=body,
    )
    assert code == 1
    assert "GOV-EXC-NOTAPPROVED" in out


def test_shipped_exception_fixture_is_not_approved():
    """The in-tree example must never authorize anything."""
    data = yaml.safe_load((REPO_ROOT / EXCEPTIONS_REL).read_text(encoding="utf-8"))
    for entry in data.get("exceptions") or []:
        assert entry["status"] != "approved", f"{entry['id']} must not ship as approved"
        for fieldname in gov.EXCEPTION_REQUIRED_FIELDS:
            assert fieldname in entry, f"{entry['id']} missing {fieldname}"


# ── 10. reuse map ──────────────────────────────────────────────────────────


def test_case10_missing_reuse_map_fails():
    code, out = run_checker(
        REPO_ROOT, changed=["src/agents/catering/deposit.py"], body="## Summary\n\nlgtm\n",
    )
    assert code == 1
    assert "GOV-PR-NOMAP" in out


def test_case10_verbal_claim_is_not_a_reuse_map():
    body = "## Summary\n\nThis change is Hermes-first and reuses everything.\n"
    code, out = run_checker(REPO_ROOT, changed=["src/agents/catering/deposit.py"], body=body)
    assert code == 1
    assert "GOV-PR-NOMAP" in out


def test_incomplete_reuse_map_fails():
    body = reuse_map()
    body = body.replace("- Vertical E2E proof: inbound WhatsApp menu photo through to priced pricebook\n", "")
    code, out = run_checker(REPO_ROOT, changed=["src/agents/catering/deposit.py"], body=body)
    assert code == 1
    assert "GOV-PR-FIELD" in out and "Vertical E2E proof" in out


def test_bold_markdown_labels_are_accepted():
    """Authors bold their Reuse Map labels; the gate must not depend on that."""
    body = reuse_map()
    bolded = "\n".join(
        line.replace("- ", "- **", 1).replace(":", ":**", 1) if line.startswith("- ") else line
        for line in body.splitlines()
    )
    assert "- **Requested outcome:**" in bolded
    code, out = run_checker(REPO_ROOT, changed=["src/agents/catering/deposit.py"], body=bolded)
    assert code == 0, out


def test_nested_agents_pointer_does_not_trigger_shared_impact_analysis():
    """An AGENTS.md inside a shared runtime dir is a pointer, not shared runtime."""
    body = reuse_map(**{
        "Affected projects": "commerce-platform",
        "Applicable directives": "docs/governance/projects/commerce-platform.md",
        "Shared-platform impact": "none",
    })
    code, out = run_checker(REPO_ROOT, changed=["src/platform/commerce/AGENTS.md"], body=body)
    assert code == 0, out


def test_real_shared_runtime_change_still_triggers_impact_analysis():
    """Guard the guard: an actual commerce module must still demand the analysis."""
    body = reuse_map(**{
        "Affected projects": "commerce-platform",
        "Applicable directives": "docs/governance/projects/commerce-platform.md",
        "Shared-platform impact": "none",
    })
    code, out = run_checker(REPO_ROOT, changed=["src/platform/commerce/cart.py"], body=body)
    assert code == 1
    assert "GOV-PR-SHARED" in out


# ── 11 & 12. subsystem heuristic ───────────────────────────────────────────


def test_case11_new_subsystem_without_exception_fails():
    body = reuse_map(**{"New subsystem": "a catering scheduler"})
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/scheduler.py"],
        added=["src/agents/catering/scheduler.py"],
        body=body,
    )
    assert code == 1
    assert "GOV-SUBSYSTEM-NOEXC" in out


def test_case11_undeclared_new_subsystem_fails():
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/lead_store.py"],
        added=["src/agents/catering/lead_store.py"],
        body=reuse_map(),
    )
    assert code == 1
    assert "GOV-SUBSYSTEM-UNDECLARED" in out


def test_case12_thin_adapter_using_existing_kernels_passes():
    body = reuse_map(**{
        "Thin adapters": "menu_to_pricebook_adapter.py mapping extracted menu items to pricebook rows",
        "New subsystem": "none",
    })
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/menu_to_pricebook_adapter.py"],
        added=["src/agents/catering/menu_to_pricebook_adapter.py"],
        body=body,
    )
    assert code == 0, out


def test_heuristic_ignores_edits_to_existing_modules():
    """A modified file named like a subsystem must not be flagged."""
    body = reuse_map(**{"Affected projects": "catering-studio"})
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/platform/catering_quote_ledger.py", "src/agents/catering/scripts/import-catering-pricebook"],
        added=[],
        body=body,
    )
    assert code == 0, out
    assert "GOV-SUBSYSTEM" not in out


def test_extends_declaration_satisfies_heuristic():
    body = reuse_map(**{
        "Existing stores/workflows reused": (
            "extends the existing subsystem: catering-leads.json, no parallel store"
        ),
    })
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/lead_store.py"],
        added=["src/agents/catering/lead_store.py"],
        body=body,
    )
    assert code == 0, out


# ── 13. nested AGENTS pointers ─────────────────────────────────────────────


def test_case13_broken_nested_agents_pointer_fails(sandbox: Path):
    (sandbox / "src/agents/catering/AGENTS.md").write_text(
        "# Catering\n\nJust do whatever seems right.\n", encoding="utf-8"
    )
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-NESTED-REF" in out


def test_case13_missing_nested_agents_file_fails(sandbox: Path):
    (sandbox / "src/agents/flyer/AGENTS.md").unlink()
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-NESTED-MISSING" in out


def test_all_declared_nested_pointers_resolve():
    data = _registry(REPO_ROOT)
    declared = [p["nested_agents_file"] for p in data["projects"] if p.get("nested_agents_file")]
    assert len(declared) >= 6
    for nested in declared:
        assert (REPO_ROOT / nested).is_file(), nested
        text = (REPO_ROOT / nested).read_text(encoding="utf-8")
        assert "AGENTS.md" in text and "engineering-directive.md" in text
        # a pointer, not a fork
        assert len(text.splitlines()) < 40, f"{nested} looks like a forked policy copy"


# ── 14. cursor scoping ─────────────────────────────────────────────────────


def test_case14_cursor_scopes_do_not_overlap_accidentally():
    code, out = run_checker(REPO_ROOT, registry_only=True)
    assert code == 0, out
    assert "GOV-CURSOR-CROSS" not in out


def test_case14_catering_cursor_rule_never_matches_flyer_paths():
    rules = REPO_ROOT / ".cursor" / "rules"
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry()
    catering_globs = gov.GovernanceChecker._cursor_globs(
        (rules / "catering-studio.mdc").read_text(encoding="utf-8")
    )
    flyer_globs = gov.GovernanceChecker._cursor_globs(
        (rules / "flyer-studio.mdc").read_text(encoding="utf-8")
    )
    assert catering_globs and flyer_globs
    for glob, foreign in ((catering_globs, "flyer-studio"), (flyer_globs, "catering-studio")):
        for g in glob:
            rx = gov.pattern_to_regex(g)
            for path in checker.tracked_files():
                if rx.match(path):
                    assert checker.matcher.classify(path)[0] != foreign, (g, path)


def test_case14_cursor_glob_outside_registry_fails(sandbox: Path):
    rule = sandbox / ".cursor" / "rules" / "catering-studio.mdc"
    rule.write_text(
        rule.read_text(encoding="utf-8").replace(
            "  - src/agents/catering/**", "  - src/agents/flyer/**"
        ),
        encoding="utf-8",
    )
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-CURSOR-SCOPE" in out or "GOV-CURSOR-CROSS" in out


def test_universal_cursor_rule_always_applies():
    text = (REPO_ROOT / ".cursor" / "rules" / "engineering-directive.mdc").read_text(encoding="utf-8")
    assert "alwaysApply: true" in text
    for rule in (REPO_ROOT / ".cursor" / "rules").glob("*.mdc"):
        if rule.stem == gov.CURSOR_UNIVERSAL_RULE:
            continue
        assert "alwaysApply: true" not in rule.read_text(encoding="utf-8"), rule.name


# ── 15 & 16. registry scale and new-agent discipline ───────────────────────


def test_case15_registry_covers_every_discovered_agent():
    """Every directory under src/agents/ must be owned by some project."""
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry()
    agent_dirs = sorted(p.name for p in (REPO_ROOT / "src" / "agents").iterdir() if p.is_dir())
    assert len(agent_dirs) >= 18, agent_dirs
    tracked = checker.tracked_files()
    for name in agent_dirs:
        files = [f for f in tracked if f.startswith(f"src/agents/{name}/")]
        assert files, f"agent `{name}` has no tracked files"
        for probe in files:
            owner, _, _ = checker.matcher.classify(probe)
            assert owner is not None, f"`{probe}` (agent `{name}`) is not classified"


def test_case15_registry_scales_without_checker_changes(sandbox: Path):
    """Adding ~25 more agents must need no code change in the checker."""
    data = _registry(sandbox)
    for i in range(25):
        pid = f"synthetic-agent-{i:02d}"
        directive = f"docs/governance/projects/{pid}.md"
        (sandbox / directive).write_text(f"# {pid}\n\nVersion: 1.0.0\n", encoding="utf-8")
        data["projects"].append({
            "id": pid,
            "name": pid,
            "directive": directive,
            "shared_platform": False,
            "impact_analysis_required": False,
            "lifecycle": "scaffold",
            "runtime": "per-customer VPS",
            "paths": {
                "source": [f"src/agents/{pid}/**"],
                "tests": [], "ops": [], "ui": [], "docs": [],
            },
            "shared_dependencies": ["shift-platform"],
            "nested_agents_file": None,
        })
    _write_registry(sandbox, data)
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 0, out
    checker = gov.GovernanceChecker(sandbox)
    assert checker.load_registry()
    assert len(checker.projects) >= 39
    assert checker.matcher.classify("src/agents/synthetic-agent-07/x.py")[0] == "synthetic-agent-07"


def test_case16_new_registered_agent_requires_a_directive(sandbox: Path):
    data = _registry(sandbox)
    data["projects"].append({
        "id": "ghost-agent",
        "name": "Ghost Agent",
        "directive": "docs/governance/projects/ghost-agent.md",
        "shared_platform": False,
        "impact_analysis_required": False,
        "lifecycle": "scaffold",
        "runtime": "per-customer VPS",
        "paths": {"source": ["src/agents/ghost/**"], "tests": [], "ops": [], "ui": [], "docs": []},
        "shared_dependencies": [],
        "nested_agents_file": None,
    })
    _write_registry(sandbox, data)
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-REG-DIRECTIVE" in out


def test_duplicate_project_id_fails(sandbox: Path):
    data = _registry(sandbox)
    data["projects"].append(dict(data["projects"][0]))
    _write_registry(sandbox, data)
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-REG-DUPID" in out


@pytest.mark.parametrize(
    "bad", ["/abs/path/**", "src/../etc/**", "./src/**", "**", "src\\windows\\**"],
)
def test_malformed_path_patterns_fail(sandbox: Path, bad):
    data = _registry(sandbox)
    data["projects"][0]["paths"]["source"].append(bad)
    _write_registry(sandbox, data)
    code, out = run_checker(sandbox, registry_only=True)
    assert code == 1
    assert "GOV-REG-PATTERN" in out


def test_every_registered_directive_exists_and_is_versioned():
    data = _registry(REPO_ROOT)
    for proj in data["projects"]:
        path = REPO_ROOT / proj["directive"]
        assert path.is_file(), proj["directive"]
        assert "Version:" in path.read_text(encoding="utf-8"), proj["directive"]


# ── 17. governance is not a runtime dependency ─────────────────────────────


def test_case17_no_runtime_code_references_governance():
    """Governance files must not be imported or read by runtime code.

    Scoped to executable/runtime file types on purpose: the nested AGENTS.md
    pointers live under src/ and web/ and are SUPPOSED to reference the
    directives — they are instructions to contributors, not runtime inputs.
    Their content is asserted separately by test_all_declared_nested_pointers_resolve.
    """
    runtime_globs = [
        "src/**/*.py", "src/**/*.sh", "src/**/SKILL.md",
        "src/**/*.service", "src/**/*.timer", "src/**/*.yaml", "src/**/*.yml",
        "web/backend/app/**", "web/frontend/src/**",
    ]
    hits = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "grep", "-rln", "docs/governance", "--", *runtime_globs],
        capture_output=True, text=True,
    ).stdout.split()
    assert hits == [], f"runtime code references governance files: {hits}"


def test_case17_nested_pointers_are_the_only_governance_refs_under_src():
    """Anything under src/ or web/ that mentions governance must be an AGENTS.md."""
    hits = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "grep", "-rln", "docs/governance", "--", "src/", "web/"],
        capture_output=True, text=True,
    ).stdout.split()
    assert hits, "expected the nested AGENTS.md pointers to be found"
    for path in hits:
        assert Path(path).name == "AGENTS.md", (
            f"`{path}` references governance but is not an instruction pointer"
        )


def test_case17_checker_imports_nothing_from_runtime():
    source = CHECKER.read_text(encoding="utf-8")
    for forbidden in ("safe_io", "schemas", "sender_context", "/opt/shift-agent"):
        assert forbidden not in source, f"checker must not touch runtime surface `{forbidden}`"


def test_case17_governance_workflow_is_not_a_deploy_dependency():
    wf = REPO_ROOT / ".github" / "workflows" / "architecture-governance.yml"
    assert wf.is_file()
    text = wf.read_text(encoding="utf-8")
    for forbidden in ("ssh ", "scp ", "systemctl", "deploy.sh", "shift-agent-deploy"):
        assert forbidden not in text, f"governance workflow must not perform deploy actions ({forbidden})"


def test_pointer_files_do_not_fork_policy():
    """CLAUDE.md and copilot-instructions.md must point, not restate."""
    for rel in ("CLAUDE.md", ".github/copilot-instructions.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert len(text.splitlines()) < 60, f"{rel} looks like a forked policy copy"
        assert "docs/governance/engineering-directive.md" in text


# ══════════════════════════════════════════════════════════════════════════
# Reviewer blocker 1 — legacy policy from the pre-governance instruction
# files must survive. Each contract below existed in the pre-PR AGENTS.md /
# CLAUDE.md; a later edit must not silently delete it.
# ══════════════════════════════════════════════════════════════════════════

GOVERNANCE_CORPUS_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "docs/governance/engineering-directive.md",
    "docs/governance/shared-platform-directive.md",
]


def _governance_corpus() -> str:
    parts = [(REPO_ROOT / f).read_text(encoding="utf-8") for f in GOVERNANCE_CORPUS_FILES]
    parts += [p.read_text(encoding="utf-8")
              for p in sorted((REPO_ROOT / "docs" / "governance" / "projects").glob("*.md"))]
    return "\n".join(parts)


# (legacy rule, canonical destination, marker strings that must all survive)
PRESERVED_LEGACY_CONTRACTS = [
    ("hermes-capability-inventory", "shared-platform-directive.md §1a",
     ["Source ingestion across formats", "Vision extraction", "Skill chaining",
      "LLM gateway", "Per-VPS state"]),
    ("canonical-menu-image-reference", "shared-platform-directive.md §1a",
     ["Owner sends a menu\nimage"]),
    ("bundled-ecosystem-skills", "shared-platform-directive.md §1a",
     ["productivity/google-workspace", "productivity/maps", "productivity/airtable",
      "productivity/ocr-and-documents", "productivity/notion"]),
    ("native-mcp-escape-hatch", "shared-platform-directive.md §1a",
     ["mcp/native-mcp", "8,600"]),
    ("genuinely-net-new-categories", "engineering-directive.md §2a",
     ["External write APIs", "Money-moving UX discipline",
      "Per-customer business logic", "Specialised classifiers"]),
    ("trap-skills-do-not-investigate", "engineering-directive.md §2a",
     ["bookkeeper", "sentiment-priority-scorer", "cognify-skills", "farmos-equipment"]),
    ("read-deployed-code-table", "engineering-directive.md §6",
     ["src/platform/schemas.py", "src/platform/safe_io.py",
      "dispatch_shift_agent", "shift-agent-deploy.sh"]),
    ("deployed-pattern-checklist", "engineering-directive.md §7",
     ["atomic_write_json", "generate_unique_code", 'extra="forbid"',
      "fromMe", "single-tenant"]),
    ("drift-check-tag", "projects/repo-meta.md",
     ["Hermes-native", "extends-Hermes", "drifts-from-Hermes"]),
    ("author-side-hook-and-command", "projects/repo-meta.md",
     ["hermes-first-check.py", "/hermes-check"]),
    ("reviewer-standing-rule", "engineering-directive.md §11",
     ["NO-GO", "could an existing capability", "is the scope itself needed"]),
    ("review-economics", "engineering-directive.md §8",
     ["BLOCKER", "two reviewer/fix cycles", "new evidence"]),
    ("pilot-readiness-gate", "AGENTS.md workflow reminders",
     ["pilot-readiness-check --text", "blocking for onboarding"]),
    ("self-learning-boundary", "AGENTS.md workflow reminders",
     ["Self-learning boundary", "state/memory only"]),
    ("catering-menu-authority", "AGENTS.md + projects/catering-studio.md",
     ["only the owner may apply the extracted menu", "confirmation code"]),
    ("tarball-deploy-discipline", "AGENTS.md workflow reminders",
     ["no git checkout on the VPS"]),
    ("plan-first", "AGENTS.md workflow reminders",
     ["tasks/<feature>-plan.md"]),
    ("project-context", "AGENTS.md project context",
     ["Triveni Supermarket", "Hetzner", "docs/portfolio.md"]),
]


@pytest.mark.parametrize(
    "rule,destination,markers",
    PRESERVED_LEGACY_CONTRACTS,
    ids=[c[0] for c in PRESERVED_LEGACY_CONTRACTS],
)
def test_legacy_policy_contract_preserved(rule, destination, markers):
    corpus = _governance_corpus()
    missing = [m for m in markers if m not in corpus]
    assert not missing, (
        f"legacy rule `{rule}` (canonical destination: {destination}) lost these "
        f"markers from the pre-governance instruction files: {missing}"
    )


# The governance consolidation commit and the immutable document it replaced.
# The historical AGENTS.md is its PARENT — resolved once, pinned here as a
# concrete SHA so nothing about it can drift.
GOVERNANCE_CONSOLIDATION_COMMIT = "8685b2530cc4a65c9aa27797cb29c9d8b135aff6"
PRE_CONSOLIDATION_COMMIT = "b8652ab568b5b218648cf4089d7558221e9c1f0d"

# The historical document is VENDORED rather than read through git.
#
# It used to be read as `git show main:AGENTS.md`. That ref moves: on a push to
# main it resolves to the POST-consolidation document, so the test compared the
# current file against a map describing the old one and could never pass. It had
# been red on every main push since consolidation.
#
# Pinning PRE_CONSOLIDATION_COMMIT alone does not fix it either — send-path-ci
# checks out with the default shallow depth, so that object is unreachable there
# and the test would silently skip. Skipping is the same failure wearing a green
# tick. A vendored byte-exact copy is reachable in any clone, on any platform,
# on both PR and main-push CI, and needs no git at all.
PRE_CONSOLIDATION_AGENTS_MD = (
    REPO_ROOT / "tests" / "fixtures" / "governance" / "AGENTS.pre-consolidation.md"
)
# sha256 of that file's exact bytes == `git show <PRE_CONSOLIDATION_COMMIT>:AGENTS.md`.
# .gitattributes marks the fixture `-text` so no platform rewrites its endings.
PRE_CONSOLIDATION_AGENTS_MD_SHA256 = (
    "6677ec8ec8328fd67b6038fef5470c9cde50421192c90bedd1d6af36248ed06e"
)


def test_pre_consolidation_fixture_is_immutable():
    """The historical fixture must be byte-stable and independent of any ref.

    Never skips: the hash half needs no git, so a shallow clone still proves the
    document under test has not drifted. If git history IS reachable, the second
    half additionally proves the vendored bytes equal the pinned commit's blob.
    """
    assert PRE_CONSOLIDATION_AGENTS_MD.exists(), (
        f"vendored governance fixture is missing: {PRE_CONSOLIDATION_AGENTS_MD}"
    )
    raw = PRE_CONSOLIDATION_AGENTS_MD.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == PRE_CONSOLIDATION_AGENTS_MD_SHA256, (
        "the historical AGENTS.md fixture changed. It is a frozen record of the "
        "pre-consolidation document, not a living file — if you meant to change "
        "what is preserved, change ACCOUNTED_PRE_PR_HEADINGS instead.\n"
        f"  expected {PRE_CONSOLIDATION_AGENTS_MD_SHA256}\n  actual   {actual}"
    )

    try:
        from_git = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show",
             f"{PRE_CONSOLIDATION_COMMIT}:AGENTS.md"],
            capture_output=True,
        )
    except OSError:
        return  # no git binary at all (e.g. a minimal container) — hash half ran
    if from_git.returncode != 0 or not from_git.stdout:
        return  # shallow clone: the hash assertion above already ran
    assert hashlib.sha256(from_git.stdout).hexdigest() == actual, (
        f"vendored fixture no longer matches {PRE_CONSOLIDATION_COMMIT}:AGENTS.md"
    )


def test_historical_fixture_is_not_the_live_document():
    """Regression for the actual defect.

    The old test read `git show main:AGENTS.md`, so on a main push the
    "historical" document WAS the live one and preservation was compared against
    itself. Contrast against the live working-tree AGENTS.md rather than any ref:
    no git, no skip, and no dependence on where a local branch happens to point
    (an early draft of this test compared against `main` and tripped on a stale
    local ref — the very fragility being removed).
    """
    live = (REPO_ROOT / "AGENTS.md").read_bytes()
    assert hashlib.sha256(live).hexdigest() != PRE_CONSOLIDATION_AGENTS_MD_SHA256, (
        "the historical fixture is byte-identical to the live AGENTS.md, so the "
        "preservation test would be comparing the document against itself. "
        "Either the fixture started tracking the live file again, or the "
        "governance consolidation was reverted."
    )

    live_headings = {
        h.strip("# ").strip() for h in live.decode("utf-8").splitlines()
        if h.startswith(("## ", "### "))
    }
    fixture_headings = {
        h.strip("# ").strip()
        for h in PRE_CONSOLIDATION_AGENTS_MD.read_text(encoding="utf-8").splitlines()
        if h.startswith(("## ", "### "))
    }
    assert live_headings != fixture_headings, (
        "live and historical heading sets are identical — the fixture is not "
        "capturing the pre-consolidation document"
    )


def test_every_pre_pr_section_heading_is_accounted_for():
    """Each substantive heading in the pre-consolidation AGENTS.md is preserved
    or listed as intentionally superseded. Nothing may be dropped silently.

    Reads the vendored fixture, so it behaves identically on PR CI and on a push
    to main, and never skips.
    """
    pre = PRE_CONSOLIDATION_AGENTS_MD.read_text(encoding="utf-8")
    headings = [h.strip("# ").strip() for h in pre.splitlines() if h.startswith(("## ", "### "))]
    # Point-in-time status sections are intentionally superseded — they record a
    # moment, not a rule, and were already stale at consolidation time.
    intentionally_superseded = {"Active scope this session"}
    unaccounted = [
        h for h in headings
        if h not in intentionally_superseded and h not in ACCOUNTED_PRE_PR_HEADINGS
    ]
    assert not unaccounted, (
        "pre-PR AGENTS.md headings not accounted for in the preservation map: "
        f"{unaccounted}"
    )


ACCOUNTED_PRE_PR_HEADINGS = {
    "⚠️ CRITICAL RULE — Check Hermes capabilities BEFORE writing code",
    "How to apply (mandatory checklist before any code/spec)",
    "What Hermes natively handles (verified in production for Catering Agent as of 2026-04-29)",
    "Install-now ecosystem skills (verified 2026-05-03; cover 6 of 17 prioritized agents)",
    "What is genuine net-new engineering (NOT Hermes substrate)",
    "Why this rule exists",
    "How this rule is enforced (mechanical, not discipline-based)",
    "⚠️ DRIFT RULES — Read deployed code BEFORE proposing",
    "The rule (Part 3 working agreement)",
    "Drift-check tag (mandatory at top of every plan/spec/design doc)",
    "Deployed pattern checklist (Part 1 — verify, do NOT silently import alternatives)",
    "Operational drift checklist (Part 2)",
    "How to apply (mandatory checklist before any plan/spec/code)",
    "Why this rule exists (separate from Hermes-first)",
    "Project context",
    "Key paths",
    "Workflow reminders",
}


def test_claude_md_still_points_and_does_not_refork():
    """Preservation must not be achieved by pasting policy back into CLAUDE.md."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) < 60
    for forked in ("What Hermes natively handles", "Deployed pattern checklist",
                   "productivity/google-workspace", "atomic_write_json"):
        assert forked not in text, f"CLAUDE.md re-forked policy text: {forked!r}"


# ══════════════════════════════════════════════════════════════════════════
# Reviewer blocker 2 — the registry must be precise, not merely comprehensive.
# ══════════════════════════════════════════════════════════════════════════

# (path, expected project, expected rule kind)
OWNERSHIP_TABLE = [
    ("src/agents/catering/deposit.py", "catering-studio", "product-specific"),
    ("tests/test_catering_pricing_kernel.py", "catering-studio", "product-specific"),
    ("tests/e2e/test_catering_conversation_e2e.py", "catering-studio", "product-specific"),
    ("tests/fixtures/catering_pricebook_valid.json", "catering-studio", "product-specific"),
    ("src/agents/catering/scripts/import-catering-pricebook", "catering-studio", "product-specific"),
    ("src/platform/catering_pricing.py", "catering-studio", "product-specific"),
    ("src/agents/flyer/visual_qa.py", "flyer-studio", "product-specific"),
    ("tests/test_flyer_visual_qa.py", "flyer-studio", "product-specific"),
    ("tests/fixtures/flyer_rollout_readiness/green.json", "flyer-studio", "product-specific"),
    ("tools/flyer-self-evaluation.py", "flyer-studio", "product-specific"),
    ("src/platform/flyer_identity.py", "flyer-studio", "product-specific"),
    ("src/platform/commerce/cart.py", "commerce-platform", "product-specific"),
    ("web/backend/app/routers/catering.py", "catering-studio", "product-specific"),
    ("web/frontend/src/sections/catering/CateringStudio.tsx", "catering-studio", "product-specific"),
    ("web/frontend/src/sections/FlyerAdmin.tsx", "flyer-studio", "product-specific"),
    ("web/frontend/src/sections/flyer/ManualQueueActions.tsx", "flyer-studio", "product-specific"),
    ("src/platform/safe_io.py", "shift-platform", "container"),
    ("src/agents/shift/skills/dispatch_shift_agent/SKILL.md", "shift-platform", "product-specific"),
    ("tests/test_schemas.py", "shift-platform", "container"),
    ("tools/patch-hermes.py", "shift-platform", "container"),
    ("src/agents/vip/skills/vip_dispatcher/SKILL.md", "phase0-agents", "product-specific"),
    ("docs/governance/engineering-directive.md", "repo-governance", "product-specific"),
    ("web/backend/app/routers/auth.py", "cockpit", "container"),
    ("src/platform/qbo_client.py", "expense-bookkeeper", "product-specific"),
]


@pytest.mark.parametrize("path,project,kind", OWNERSHIP_TABLE,
                         ids=[p for p, _, _ in OWNERSHIP_TABLE])
def test_ownership_resolution_table(path, project, kind):
    checker = gov.GovernanceChecker(REPO_ROOT)
    assert checker.load_registry(), checker.findings
    r = checker.resolve(path)
    assert r["project"] == project, f"{path} -> {r['project']} (rule {r['winning_rule']})"
    assert r["rule_kind"] == kind, f"{path} matched via {r['winning_rule']} ({r['rule_kind']})"
    assert gov.UNIVERSAL_DIRECTIVE in r["directives"]
    proj = checker.by_id[project]
    assert proj.directive in r["directives"]
    if proj.shared_platform:
        assert gov.SHARED_DIRECTIVE in r["directives"]


def test_no_product_file_is_absorbed_by_a_container_rule():
    """The core blocker-2 guarantee, enforced across the whole repository."""
    code, out = run_checker(REPO_ROOT, registry_only=True)
    assert "GOV-REG-ABSORBED" not in out, out
    assert code == 0, out


def test_catering_test_is_not_classified_only_as_shared_platform():
    for path in ("tests/test_catering_pricing_kernel.py",
                 "tests/e2e/test_catering_conversation_e2e.py",
                 "tests/test_send_catering_ack.py"):
        assert classify(path) == "catering-studio", path


def test_flyer_tool_is_not_classified_only_as_shared_platform():
    for path in ("tools/flyer-self-evaluation.py",
                 "tools/flyer-acceptance-baseline.py",
                 "tools/backfill-flyer-pdf-qa.py"):
        assert classify(path) == "flyer-studio", path


def test_absorption_guard_fires_when_a_product_pattern_is_removed(sandbox, monkeypatch):
    """Deleting a product's test pattern must be caught, not silently absorbed.

    Runs entirely against the sandbox registry; the real repository's file list
    is injected rather than the sandbox registry being copied into the repo — a
    governance test must never write into the tree it audits.
    """
    real = gov.GovernanceChecker(REPO_ROOT)
    assert real.load_registry()
    repo_files = real.tracked_files()

    data = _registry(sandbox)
    for proj in data["projects"]:
        if proj["id"] == "flyer-studio":
            proj["paths"]["tests"] = [p for p in proj["paths"]["tests"]
                                      if p != "tests/test_flyer*.py"]
    _write_registry(sandbox, data)

    checker = gov.GovernanceChecker(sandbox)
    monkeypatch.setattr(checker, "tracked_files", lambda: repo_files)
    assert checker.load_registry(), checker.findings
    checker.check_container_absorption()
    findings = [f for f in checker.findings if f.code == "GOV-REG-ABSORBED"]
    assert findings, "removing tests/test_flyer*.py must be caught by the guard"
    assert any("flyer-studio" in f.message for f in findings)
    assert any("tests/test_flyer" in f.message for f in findings)


def test_cross_project_path_maps_to_all_declared_owners(sandbox: Path):
    """A genuinely shared path may map to multiple owners when declared."""
    data = _registry(sandbox)
    for proj in data["projects"]:
        if proj["id"] == "flyer-studio":
            proj["paths"]["tests"].append("tests/test_catering*.py")
    data["overlaps"].append({
        "id": "OV-TEST-XPROJ",
        "projects": ["catering-studio", "flyer-studio"],
        "paths": ["tests/test_catering*.py"],
        "resolution": "longest-literal-prefix-wins",
        "reason": "genuinely cross-project suite",
    })
    _write_registry(sandbox, data)
    checker = gov.GovernanceChecker(sandbox)
    assert checker.load_registry(), checker.findings
    affected = checker.classify_changes(["tests/test_catering_pricing_kernel.py"])
    assert set(affected) == {"catering-studio", "flyer-studio"}, affected
    assert not [f for f in checker.findings if f.code == "GOV-OVERLAP"]


def test_longest_prefix_cannot_conceal_an_undeclared_overlap(sandbox: Path):
    """Equal-specificity collision must fail even though classification 'works'."""
    data = _registry(sandbox)
    for proj in data["projects"]:
        if proj["id"] == "flyer-studio":
            proj["paths"]["tests"].append("tests/test_catering*.py")
    _write_registry(sandbox, data)
    checker = gov.GovernanceChecker(sandbox)
    assert checker.load_registry()
    checker.classify_changes(["tests/test_catering_pricing_kernel.py"])
    codes = [f.code for f in checker.findings]
    assert "GOV-OVERLAP" in codes, codes


def test_broad_fallback_cannot_validate_a_wrongly_owned_path(sandbox: Path):
    """Widening a container rule must not launder a misowned product file."""
    data = _registry(sandbox)
    for proj in data["projects"]:
        if proj["id"] == "shift-platform":
            proj["paths"]["ops"].append("src/agents/flyer/**")
    _write_registry(sandbox, data)
    checker = gov.GovernanceChecker(sandbox)
    assert checker.load_registry()
    checker.classify_changes(["src/agents/flyer/visual_qa.py"])
    codes = [f.code for f in checker.findings]
    assert "GOV-OVERLAP" in codes, codes


def test_resolve_cli_emits_the_ownership_record():
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(REPO_ROOT), "--format", "json",
         "--resolve", "tests/test_flyer_visual_qa.py", "src/platform/safe_io.py"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    import json as _json
    records = _json.loads(proc.stdout)
    assert records[0]["project"] == "flyer-studio"
    assert records[0]["rule_kind"] == "product-specific"
    assert records[1]["project"] == "shift-platform"
    assert records[1]["rule_kind"] == "container"
    assert gov.SHARED_DIRECTIVE in records[1]["directives"]


# ══════════════════════════════════════════════════════════════════════════
# Capability Reuse Map schema — single-source enforcement.
#
# REUSE_MAP_FIELDS in tools/check-architecture-governance.py is the
# authoritative machine-enforced schema. The three published templates
# necessarily REPEAT those labels; these tests prove each repetition is exact
# and carries no divergent replacement, which is what stops them forking.
# The extractors below are deliberately narrow and literal — no general
# Markdown framework, no schema generator.
# ══════════════════════════════════════════════════════════════════════════

DIRECTIVE_REL = "docs/governance/engineering-directive.md"
PR_TEMPLATE_REL = ".github/pull_request_template.md"

# Labels that previously diverged. If any reappears in a published template,
# that template has forked from the enforced schema again.
RETIRED_LABELS = (
    "Affected project(s)",
    "Applicable directive(s)",
    "Existing platform/model capability reused",
    "Existing deterministic kernel reused",
    "Existing store/workflow reused",
    "Thin adapter introduced",
    "New subsystem introduced",
    "Architecture exception ID or none",
)


def _bullet_labels(text: str) -> list[str]:
    """Labels from `- Label:` bullet lines. Literal and deterministic."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and ":" in stripped:
            out.append(stripped[2:].split(":", 1)[0].strip())
    return out


def _directive_reuse_map_block() -> str:
    """The fenced block under §9 of the universal directive."""
    text = (REPO_ROOT / DIRECTIVE_REL).read_text(encoding="utf-8")
    start = text.index("## 9. Required output")
    fence = text.index("```", start) + 3
    end = text.index("```", fence)
    return text[fence:end]


def _agents_bootstrap_block() -> str:
    """The Capability Reuse Map skeleton inside the AGENTS.md bootstrap fence."""
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    start = text.index("Capability Reuse Map — <project id>")
    end = text.index("```", start)
    return text[start:end]


def _pr_template_block() -> str:
    text = (REPO_ROOT / PR_TEMPLATE_REL).read_text(encoding="utf-8")
    start = text.index(gov.REUSE_MAP_HEADING)
    end = text.index("## Architecture drift check", start)
    return text[start:end]


PUBLISHED_TEMPLATES = {
    "engineering-directive §9": _directive_reuse_map_block,
    "AGENTS.md bootstrap skeleton": _agents_bootstrap_block,
    "pull_request_template.md": _pr_template_block,
}


@pytest.mark.parametrize("name", list(PUBLISHED_TEMPLATES))
def test_published_template_contains_every_enforced_field(name):
    labels = _bullet_labels(PUBLISHED_TEMPLATES[name]())
    missing = [f for f in gov.REUSE_MAP_FIELDS if f not in labels]
    assert not missing, f"{name} is missing enforced field(s): {missing}"


@pytest.mark.parametrize("name", list(PUBLISHED_TEMPLATES))
def test_published_template_has_no_divergent_label(name):
    labels = _bullet_labels(PUBLISHED_TEMPLATES[name]())
    retired = [lbl for lbl in labels if lbl in RETIRED_LABELS]
    assert not retired, f"{name} reintroduced retired label(s): {retired}"
    extra = [lbl for lbl in labels if lbl not in gov.REUSE_MAP_FIELDS]
    assert not extra, f"{name} publishes label(s) the checker does not enforce: {extra}"


def _body_from_labels(labels: list[str]) -> str:
    lines = ["## Summary", "", "schema conformance check", "", gov.REUSE_MAP_HEADING, ""]
    lines += [f"- {lbl}: catering-studio" if lbl == "Affected projects" else f"- {lbl}: none"
              for lbl in labels]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("name", list(PUBLISHED_TEMPLATES))
def test_pr_body_copied_from_published_template_passes(name):
    """The headline guarantee: following any published template passes CI."""
    labels = _bullet_labels(PUBLISHED_TEMPLATES[name]())
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py"],
        body=_body_from_labels(labels),
    )
    assert code == 0, f"a body copied verbatim from {name} was rejected:\n{out}"


@pytest.mark.parametrize(
    "omitted",
    ["Custom runtime code genuinely unavoidable", "Other agents affected"],
)
def test_omitting_a_newly_enforced_field_fails(omitted):
    labels = [f for f in gov.REUSE_MAP_FIELDS if f != omitted]
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py"],
        body=_body_from_labels(labels),
    )
    assert code == 1, out
    assert "GOV-PR-FIELD" in out
    assert f"`{omitted}:`" in out, out


def test_enforced_schema_is_the_expected_fourteen_fields():
    """Pins the agreed schema so a silent addition or removal is visible."""
    assert gov.REUSE_MAP_FIELDS == (
        "Requested outcome",
        "Affected projects",
        "Applicable directives",
        "Existing platform/model capabilities reused",
        "Existing deterministic kernels reused",
        "Existing stores/workflows reused",
        "Thin adapters",
        "Custom runtime code genuinely unavoidable",
        "New subsystem",
        "Evidence existing capabilities were insufficient",
        "Architecture exception",
        "Shared-platform impact",
        "Other agents affected",
        "Vertical E2E proof",
    )


def test_field_matching_stays_exact_not_fuzzy():
    """Variant spellings must NOT be accepted — no aliases, no fuzzy matching."""
    labels = ["Affected project(s)" if f == "Affected projects" else f
              for f in gov.REUSE_MAP_FIELDS]
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py"],
        body=_body_from_labels(labels),
    )
    assert code == 1, "a parenthetical variant label must not satisfy the gate"
    assert "`Affected projects:`" in out
