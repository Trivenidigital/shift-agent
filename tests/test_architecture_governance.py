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


# A substantive fill for every enforced label: evidence-bearing fields get real
# prose, genuinely nullable fields get an explicit `none`, and the two
# registry-checked fields get the facts for `catering-studio` (the project the
# template-parity tests route through). The point of these tests is that the
# published LABEL SET is accepted -- not that a content-free body is, which is
# the false green this fixture used to certify.
_SUBSTANTIVE_FILL = {
    "Requested outcome": "verify the published template's label set is accepted",
    "Affected projects": "catering-studio",
    "Applicable directives": "docs/governance/projects/catering-studio.md",
    "Existing platform/model capabilities reused": "Hermes vision extraction via parse-menu-photo",
    "Existing deterministic kernels reused": "catering_pricing.py",
    "Existing stores/workflows reused": "catering-leads.json and the existing approval workflow",
    "Thin adapters": "none",
    "Custom runtime code genuinely unavoidable": "none",
    "New subsystem": "none",
    "Evidence existing capabilities were insufficient": "n/a - no new subsystem is added",
    "Architecture exception": "none",
    "Shared-platform impact": "none",
    "Other agents affected": "none",
    "Vertical E2E proof": "inbound WhatsApp menu photo through to a priced pricebook",
}


def _body_from_labels(labels: list[str], fill=None) -> str:
    """Build a PR body carrying exactly `labels`, substantively filled."""
    lines = ["## Summary", "", "schema conformance check", "", gov.REUSE_MAP_HEADING, ""]
    lines += [f"- {lbl}: {(fill or _SUBSTANTIVE_FILL).get(lbl, 'none')}" for lbl in labels]
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


# ── 18. blank Reuse Map fields ─────────────────────────────────────────────
#
# A field an author left BLANK is the shape that reaches CI in practice, and it
# is the one shape the rest of this file never produced: every emptiness test
# above writes the literal string "none", which is non-empty TEXT that merely
# happens to be a placeholder token. `_field_value`'s `\s*` after the colon matched
# newlines, so a blank field captured the NEXT line instead and read as
# populated — which silently disarmed both emptiness gates and mis-attributed
# one field's value to another.


@pytest.mark.parametrize(
    "body,expected",
    [
        ("- New subsystem:\n- Vertical E2E proof: yes\n", ""),
        ("- New subsystem: \n- Vertical E2E proof: yes\n", ""),
        ("- New subsystem:\n\n- Vertical E2E proof: yes\n", ""),
        ("- New subsystem:\n", ""),
        ("- New subsystem:\nWe rewrote the platform.\n", ""),
        ("- New subsystem: none\n- Vertical E2E proof: yes\n", "none"),
        ("- **New subsystem:** none\n", "none"),
    ],
)
def test_blank_field_never_captures_the_following_line(body, expected):
    assert gov.GovernanceChecker._field_value(body, "New subsystem") == expected


def test_blank_field_does_not_steal_a_neighbouring_fields_value():
    body = "- Shared-platform impact:\n- Other agents affected: cockpit\n"
    assert gov.GovernanceChecker._field_value(body, "Shared-platform impact") == ""
    assert gov.GovernanceChecker._field_value(body, "Other agents affected") == "cockpit"


def test_absent_field_is_still_distinguishable_from_blank_field():
    """`None` (label absent) and `""` (label present, no value) must not merge:
    GOV-PR-FIELD keys off the former, GOV-PR-EMPTY off the latter."""
    assert gov.GovernanceChecker._field_value("- Thin adapters: x\n", "New subsystem") is None
    assert gov.GovernanceChecker._field_value("- New subsystem:\n", "New subsystem") == ""


def test_blank_shared_platform_impact_blocks_like_the_literal_none():
    """The false green: shared-platform runtime changed and the impact field is
    blank, yet the gate reported OK because the blank read as the next line."""
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "",
    })
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert code == 1, out
    assert "GOV-PR-SHARED" in out


def test_blank_new_subsystem_reads_as_undeclared_not_declared():
    """Opposite direction of the same bug: a blank `New subsystem:` read as a
    DECLARATION, demanding an exception for a subsystem nobody declared."""
    body = reuse_map(**{
        "Affected projects": "catering-studio",
        "New subsystem": "",
    })
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/brand_new_engine.py"],
        added=["src/agents/catering/brand_new_engine.py"],
        body=body,
    )
    assert "GOV-SUBSYSTEM-NOEXC" not in out, out


# ── 19. Reuse Map field classes ────────────────────────────────────────────
#
# Three layers, tested as CLASSES rather than as the handful of fields a probe
# happened to touch:
#   1. every label carries an explicit value (blank != `none`);
#   2. evidence-bearing fields reject bare placeholders;
#   3. `Affected projects` / `Applicable directives` are checked against
#      registry-resolved fact, in their own field.

CATERING_CHANGE = ["src/agents/catering/deposit.py"]


@pytest.mark.parametrize("field", gov.REUSE_MAP_FIELDS)
def test_every_field_blocks_when_the_label_is_absent(field):
    body = reuse_map()
    body = "\n".join(l for l in body.split("\n") if not l.startswith(f"- {field}:"))
    code, out = run_checker(REPO_ROOT, changed=CATERING_CHANGE, body=body)
    assert code == 1, out
    assert "GOV-PR-FIELD" in out and field in out


@pytest.mark.parametrize("field", gov.REUSE_MAP_FIELDS)
def test_every_field_blocks_when_present_but_blank(field):
    code, out = run_checker(
        REPO_ROOT, changed=CATERING_CHANGE, body=reuse_map(**{field: ""}),
    )
    assert code == 1, out
    assert "GOV-PR-EMPTY" in out and field in out


@pytest.mark.parametrize("field", gov.NARRATIVE_REQUIRED_FIELDS)
@pytest.mark.parametrize("placeholder", ["none", "n/a", "N/A.", "-", "--", "tbd", "todo", "x", "ok", "?"])
def test_narrative_field_rejects_a_bare_placeholder(field, placeholder):
    code, out = run_checker(
        REPO_ROOT, changed=CATERING_CHANGE, body=reuse_map(**{field: placeholder}),
    )
    assert code == 1, out
    assert "GOV-PR-PLACEHOLDER" in out or "GOV-PR-DIRECTIVE-MISSING" in out, out


@pytest.mark.parametrize("field", gov.NARRATIVE_REQUIRED_FIELDS)
def test_narrative_field_accepts_an_explained_absence(field):
    explained = {
        "Applicable directives": "docs/governance/projects/catering-studio.md",
    }.get(field, "n/a - CI-only change, no runtime capability is added here")
    code, out = run_checker(
        REPO_ROOT, changed=CATERING_CHANGE, body=reuse_map(**{field: explained}),
    )
    assert code == 0, out


NULLABLE_FIELDS = tuple(
    f for f in gov.REUSE_MAP_FIELDS if f not in gov.NARRATIVE_REQUIRED_FIELDS
    and f != "Affected projects"
)


@pytest.mark.parametrize("field", NULLABLE_FIELDS)
def test_nullable_field_accepts_an_explicit_none(field):
    """`none` is a real answer for these; forcing prose would only teach padding."""
    code, out = run_checker(
        REPO_ROOT, changed=CATERING_CHANGE, body=reuse_map(**{field: "none"}),
    )
    assert code == 0, out


def test_affected_project_named_elsewhere_but_not_in_its_field_blocks():
    """The old check scanned the WHOLE body, so a pasted path or a passing
    remark could satisfy it. `compliance` and `flyer-studio` are substrings of
    their own registered paths, so for those two a diff listing alone did it."""
    body = reuse_map(**{
        "Affected projects": "catering-studio",
        "Requested outcome": "incidentally I also touched src/agents/compliance/rules.py",
    })
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py", "src/agents/compliance/rules.py"],
        body=body,
    )
    assert code == 1, out
    assert "GOV-PR-PROJECT" in out and "compliance" in out


def test_applicable_directives_must_name_the_registered_directive():
    code, out = run_checker(
        REPO_ROOT,
        changed=CATERING_CHANGE,
        body=reuse_map(**{"Applicable directives": "docs/governance/projects/flyer-studio.md"}),
    )
    assert code == 1, out
    assert "GOV-PR-DIRECTIVE-MISSING" in out


def test_shared_runtime_impact_rejects_a_single_junk_character():
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "x",
        "Other agents affected": "catering-studio, flyer-studio",
    })
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert code == 1, out
    assert "GOV-PR-SHARED" in out


def test_shared_runtime_passes_with_substantive_impact_and_named_agents():
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "adds an atomic-write retry; every agent writing "
                                 "through safe_io inherits the new retry ceiling",
        "Other agents affected": "catering-studio, flyer-studio, cockpit",
    })
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert code == 0, out


# ── 20. mutation proof ─────────────────────────────────────────────────────

FALSE_GREEN_BODY = """## Capability Reuse Map

- Requested outcome:
- Affected projects: shift-platform, catering-studio, cockpit, commerce-platform, compliance, daily-brief, eod-reconcile, expense-bookkeeper, flyer-studio, multi-location, phase0-agents, shift-agent
- Applicable directives:
- Existing platform/model capabilities reused:
- Existing deterministic kernels reused:
- Existing stores/workflows reused:
- Thin adapters:
- Custom runtime code genuinely unavoidable:
- New subsystem:
- Evidence existing capabilities were insufficient:
- Architecture exception:
- Shared-platform impact: x
- Other agents affected:
- Vertical E2E proof:
"""


def test_the_recorded_false_green_body_is_now_rejected():
    """Verbatim the body that made the gate print `architecture governance: OK`
    (exit 0) on 40064b1a while changing shared-platform runtime and asserting
    nothing. This test is the closure artifact: if it ever passes again, the
    gate has regressed to certifying an empty claim."""
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/plugins/cf-router/hooks.py", "src/platform/schemas.py"],
        body=FALSE_GREEN_BODY,
    )
    assert code == 1, f"the recorded false-green body passed again:\n{out}"
    # Every narrative field in this body is BLANK rather than a placeholder, so
    # GOV-PR-EMPTY is the code that answers for them; `Shared-platform impact: x`
    # is the one bare placeholder and GOV-PR-SHARED owns it.
    for expected in ("GOV-PR-EMPTY", "GOV-PR-DIRECTIVE-MISSING", "GOV-PR-SHARED"):
        assert expected in out, f"{expected} missing:\n{out}"


@pytest.mark.parametrize("name", list(PUBLISHED_TEMPLATES))
def test_published_template_is_a_form_not_a_passing_answer(name):
    """The inverse of the schema-parity test above. A template ships BLANK
    fields; copying it without answering must fail, or the template itself
    becomes the false green."""
    labels = _bullet_labels(PUBLISHED_TEMPLATES[name]())
    blank = _body_from_labels(labels, fill={lbl: "" for lbl in labels})
    code, out = run_checker(REPO_ROOT, changed=CATERING_CHANGE, body=blank)
    assert code == 1, f"an unfilled {name} passed CI:\n{out}"
    assert "GOV-PR-EMPTY" in out


# ── 21. repeated per-project sections ──────────────────────────────────────
#
# The directive and the PR template both tell authors to repeat the whole Reuse
# Map block under a `### <project-id>` heading for a multi-project change, so a
# label legitimately appears more than once. Scoping the registry checks to the
# label's own field must not break that: reading only the FIRST occurrence would
# reject every documented multi-project PR.


def _two_section_body(second_overrides=None):
    first = reuse_map(**{
        "Affected projects": "catering-studio",
        "Applicable directives": "docs/governance/projects/catering-studio.md",
    })
    second_values = {
        "Affected projects": "flyer-studio",
        "Applicable directives": "docs/governance/projects/flyer-studio.md",
    }
    second_values.update(second_overrides or {})
    second = reuse_map(**second_values)
    # Keep one heading; the second block is a `### <project-id>` subsection.
    second_block = second.split(gov.REUSE_MAP_HEADING, 1)[1]
    return first + "\n### flyer-studio\n" + second_block


def test_multi_project_body_with_repeated_sections_passes():
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py", "src/agents/flyer/onboarding.py"],
        body=_two_section_body(),
    )
    assert code == 0, out


def test_blank_field_in_a_LATER_section_still_blocks():
    """Reading only the first occurrence would let a second section be hollow."""
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py", "src/agents/flyer/onboarding.py"],
        body=_two_section_body({"Vertical E2E proof": ""}),
    )
    assert code == 1, out
    assert "GOV-PR-EMPTY" in out and "Vertical E2E proof" in out


def test_placeholder_in_a_LATER_section_still_blocks():
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/agents/catering/deposit.py", "src/agents/flyer/onboarding.py"],
        body=_two_section_body({"Existing deterministic kernels reused": "n/a"}),
    )
    assert code == 1, out
    assert "GOV-PR-PLACEHOLDER" in out


# ── 22. structure-aware parsing ────────────────────────────────────────────
#
# Every case below was found by independent review of the first cut of this
# fix. They share one root cause: the parser read a value as exactly one
# physical line and parsed the WHOLE body, including regions that are not the
# author speaking. That cut both ways -- a quoted blank label poisoned a field
# the author DID answer, and a quoted populated label supplied a value the
# visible map contradicted.


def test_sub_bullet_value_is_not_read_as_blank():
    """Listing reused capabilities as sub-bullets is the natural answer here,
    and it was reported as `blank` -- the opposite of the truth."""
    body = reuse_map().replace(
        "- Existing platform/model capabilities reused: Hermes vision extraction via parse-menu-photo",
        "- Existing platform/model capabilities reused:\n"
        "  - `safe_io.atomic_write_json` for the state write\n"
        "  - `identify-sender` for sender identity",
    )
    code, out = run_checker(REPO_ROOT, changed=CATERING_CHANGE, body=body)
    assert code == 0, out


def test_wrapped_directive_list_is_not_truncated_to_its_first_line():
    """Three directive paths exceed a comfortable line, so authors wrap. The
    old reading emitted a FALSE STATEMENT: `not named in Applicable
    directives:` about a directive named two lines below."""
    body = reuse_map(**{
        "Affected projects": "shift-platform, catering-studio",
        "Applicable directives": "docs/governance/shared-platform-directive.md,\n"
                                 "  docs/governance/projects/catering-studio.md",
        "Shared-platform impact": "reorders the fsync in the shared atomic-write helper",
        "Other agents affected": "catering-studio",
    })
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/platform/safe_io.py", "src/agents/catering/deposit.py"],
        body=body,
    )
    assert "GOV-PR-DIRECTIVE-MISSING" not in out, out
    assert code == 0, out


def test_a_fenced_copy_of_the_blank_schema_does_not_poison_real_answers():
    """The PR class that MUST quote the blank schema is governance/template
    work -- this very PR's class."""
    quoted = "\n".join(f"- {f}:" for f in gov.REUSE_MAP_FIELDS)
    body = ("## Summary\n\nBefore this change the schema below went green with every value "
            "empty:\n\n```\n" + quoted + "\n```\n\n" + reuse_map())
    code, out = run_checker(REPO_ROOT, changed=CATERING_CHANGE, body=body)
    assert "GOV-PR-EMPTY" not in out, out
    assert code == 0, out


def test_a_fenced_decoy_cannot_supply_a_value_the_visible_map_contradicts():
    """Last-writer-wins is what a human reader assumes. A quoted earlier map
    must not answer for a visible map that says `none`."""
    decoy = "\n".join([
        "- Shared-platform impact: rewrites fsync ordering for every agent",
        "- Other agents affected: catering-studio, flyer-studio",
    ])
    body = ("## Summary\n\nsuperseded map:\n\n```\n" + decoy + "\n```\n\n" + reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
        "Shared-platform impact": "none",
        "Other agents affected": "none",
    }))
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert code == 1, out
    assert "GOV-PR-SHARED" in out


def test_a_reuse_map_hidden_in_an_html_comment_does_not_satisfy_the_gate():
    """It renders on GitHub as a heading followed by whitespace: a green check
    on content no reviewer can see defeats both halves of the design."""
    filled = reuse_map().split(gov.REUSE_MAP_HEADING, 1)[1]
    body = "## Summary\n\nHardens safe_io.\n\n" + gov.REUSE_MAP_HEADING + "\n\n<!--" + filled + "-->\n"
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert code == 1, out


def test_an_extends_declaration_in_a_comment_does_not_waive_the_subsystem_gate():
    """`extends` is the only remaining escape from the subsystem gate, so a
    reviewer note phrased the way the finding text instructs must not trip it."""
    body = ("## Summary\n\nAdds a receipt store.\n\n"
            "<!-- reviewer note: confirm this extends the existing subsystem "
            "rather than forking a new one -->\n\n" + reuse_map(**{
                "Affected projects": "shift-platform",
                "Applicable directives": "docs/governance/shared-platform-directive.md",
                "New subsystem": "yes, a new durable receipt store",
                "Shared-platform impact": "adds a durable store every agent can write to",
                "Other agents affected": "expense-bookkeeper",
            }))
    code, out = run_checker(
        REPO_ROOT,
        changed=["src/platform/receipt_store.py"],
        added=["src/platform/receipt_store.py"],
        body=body,
    )
    assert code == 1, out
    assert "GOV-SUBSYSTEM" in out


@pytest.mark.parametrize("value", [
    "Not applicable", "not applicable", "nope", "none yet", "None needed",
    "not required", "not needed", "nothing to add", "to be determined",
    "tbd after review", "pending review", "see summary", "same as above",
    "done", "Done.", "green", "CI green", "passing", "0", "1", "unknown",
])
def test_expanded_non_answers_are_rejected(value):
    """An author told to stop writing `n/a` writes `Not applicable`; told to
    prove an E2E they write `green`. Catching only the abbreviation made this
    gate theatre."""
    assert gov.is_bare_placeholder(value), f"{value!r} accepted as a real answer"


@pytest.mark.parametrize("value", [
    "n/a - no new subsystem", "safe_io.atomic_write_json",
    "todo-list store reused", "pending-order workflow reused",
    "unknown-sender path reuses identify-sender",
    "N/A - CI-only change, no runtime capability added",
])
def test_real_answers_are_not_mistaken_for_placeholders(value):
    assert not gov.is_bare_placeholder(value), f"{value!r} rejected as a placeholder"


def test_a_non_latin_answer_is_an_answer():
    """`[^0-9a-z]` normalisation collapsed any CJK/Cyrillic answer to `""` and
    rejected it as a placeholder it does not resemble."""
    assert not gov.is_bare_placeholder("\u65e0\u9700\u8981\u7684\u8fd0\u884c\u65f6\u4ee3\u7801")
    assert not gov.is_bare_placeholder("\u041d\u0435\u0442 \u043d\u043e\u0432\u044b\u0445 \u043f\u043e\u0434\u0441\u0438\u0441\u0442\u0435\u043c")


def test_an_invisible_character_is_not_a_value():
    code, out = run_checker(
        REPO_ROOT, changed=CATERING_CHANGE, body=reuse_map(**{"Thin adapters": "\u200b"}),
    )
    assert code == 1, out
    assert "GOV-PR-EMPTY" in out


# ── 23. indentation cannot silence a check ─────────────────────────────────
#
# Found by re-review of the continuation-absorption rule. Indent alone was not
# a safe boundary: CommonMark treats 0-3 spaces before `- ` as the SAME list
# level, so a row written one space deeper than its predecessor renders as a
# flat sibling but measured as a child — which let a blank field capture the
# next row's text again (the original defect, in a new form) and silenced the
# shared-platform gate on a diff no reviewer could see. The fix is the sibling
# guard: a line that is itself a Reuse Map row is never absorbed, at any indent.


def _map_lines(values, indents=None):
    lines = ["## Capability Reuse Map", ""]
    for i, f in enumerate(gov.REUSE_MAP_FIELDS):
        pad = " " * ((indents or {}).get(f, 0))
        lines.append(f"{pad}- {f}: {values[f]}")
    return "\n".join(lines) + "\n"


_SHARED_VALUES = {f: "real substantive answer" for f in gov.REUSE_MAP_FIELDS}
_SHARED_VALUES.update({
    "Affected projects": "shift-platform",
    "Applicable directives": "docs/governance/shared-platform-directive.md",
    "Thin adapters": "none", "Custom runtime code genuinely unavoidable": "none",
    "New subsystem": "none", "Architecture exception": "none",
    "Shared-platform impact": "none",
    "Other agents affected": "catering-studio, flyer-studio, cockpit, commerce-platform, "
                             "compliance, daily-brief, eod-reconcile, expense-bookkeeper, "
                             "multi-location, phase0-agents, shift-agent",
})


def test_one_extra_space_cannot_silence_the_shared_platform_gate():
    body = _map_lines(_SHARED_VALUES, {"Other agents affected": 1})
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert "GOV-PR-SHARED:" in out, f"one space silenced the shared-impact gate:\n{out}"
    assert code == 1, out


@pytest.mark.parametrize("pad", [1, 2, 3, 4])
def test_a_blank_field_never_absorbs_a_sibling_row_at_any_indent(pad):
    values = dict(_SHARED_VALUES)
    values["Thin adapters"] = ""
    body = _map_lines(values, {"Custom runtime code genuinely unavoidable": pad})
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert "GOV-PR-EMPTY" in out, f"blank field absorbed the next row at indent {pad}:\n{out}"
    assert code == 1, out


def test_a_staircase_indented_map_does_not_go_green():
    """Row n indented n spaces — every field a child of the one above it."""
    values = {f: "" for f in gov.REUSE_MAP_FIELDS}
    values["Affected projects"] = "catering-studio"
    values["Applicable directives"] = "docs/governance/projects/catering-studio.md"
    indents = {f: i for i, f in enumerate(gov.REUSE_MAP_FIELDS)}
    code, out = run_checker(
        REPO_ROOT, changed=CATERING_CHANGE, body=_map_lines(values, indents) + "              - (end)\n",
    )
    assert code == 1, out
    assert "GOV-PR-EMPTY" in out


def test_tab_indentation_does_not_confuse_the_boundary():
    """A raw `len` counts a tab as one column; indents are tab-expanded."""
    values = dict(_SHARED_VALUES)
    values["Thin adapters"] = ""
    body = _map_lines(values).replace(
        "- Custom runtime code genuinely unavoidable:", "\t- Custom runtime code genuinely unavoidable:")
    code, out = run_checker(REPO_ROOT, changed=["src/platform/safe_io.py"], body=body)
    assert "GOV-PR-EMPTY" in out, out


@pytest.mark.parametrize("prefix", [
    "```\nsome pasted log\n",                       # unterminated fence
    "````\n- Requested outcome:\n- Vertical E2E proof:\n````\n",  # 4-backtick fence
    "<!-- an unterminated reviewer note\n",          # unterminated comment
])
def test_an_unterminated_or_wide_fence_does_not_delete_the_map(prefix):
    """Stripping to end-of-body reported `no Capability Reuse Map section`
    about a body that visibly contains one. Both patterns fail OPEN."""
    body = "## Summary\n\n" + prefix + "\n" + reuse_map()
    code, out = run_checker(REPO_ROOT, changed=CATERING_CHANGE, body=body)
    assert "GOV-PR-NOMAP" not in out, out


@pytest.mark.parametrize("value", [
    "pending approvals store", "pending order webhook", "unknown vendor SKU parser",
    "todo queue reused", "wip branch protection rules",
])
def test_an_answer_naming_a_real_thing_is_not_a_deferral(value):
    """A first-word deferral rule rejected these — all plausible names here —
    while still missing a deferral padded past its bound. Matched exactly now."""
    assert not gov.is_bare_placeholder(value), f"{value!r} rejected as a placeholder"


def test_an_answer_written_on_the_following_line_is_an_answer():
    """The other half of the sibling guard: indented CONTENT under a label is
    the author answering on the next line, and must be read as the value —
    only a sibling ROW is refused."""
    body = reuse_map().replace(
        "- Thin adapters: menu-to-pricebook adapter",
        "- Thin adapters:\n  the menu-to-pricebook adapter, described here",
    )
    assert gov.GovernanceChecker._field_occurrences(body, "Thin adapters") == [
        "the menu-to-pricebook adapter, described here"
    ]
    code, out = run_checker(REPO_ROOT, changed=CATERING_CHANGE, body=body)
    assert code == 0, out


@pytest.mark.parametrize("field", gov.REUSE_MAP_FIELDS[:-1])
@pytest.mark.parametrize("shape", ["flush", "one_space", "three_spaces", "tab", "star_bullet", "bold_label"])
def test_blank_is_detected_whatever_the_next_row_looks_like(field, shape):
    """Swept invariant: a genuinely blank field is caught regardless of how the
    FOLLOWING row is written. Every regression in this branch's review history
    was a shape that made a blank field read as populated."""
    nxt = gov.REUSE_MAP_FIELDS[list(gov.REUSE_MAP_FIELDS).index(field) + 1]
    values = dict(_SHARED_VALUES)
    values[field] = ""
    lines = ["## Capability Reuse Map", ""]
    for f in gov.REUSE_MAP_FIELDS:
        row = f"- {f}: {values[f]}"
        if f == nxt:
            row = {
                "flush": row,
                "one_space": " " + row,
                "three_spaces": "   " + row,
                "tab": "\t" + row,
                "star_bullet": row.replace("- ", "* ", 1),
                "bold_label": row.replace("- ", "- **", 1).replace(":", "**:", 1),
            }[shape]
        lines.append(row)
    code, out = run_checker(
        REPO_ROOT, changed=["src/platform/safe_io.py"], body="\n".join(lines) + "\n",
    )
    assert "GOV-PR-EMPTY" in out, f"blank {field!r} missed with next row {shape}:\n{out}"


# ── 24. the subsystem heuristic must not fire on test files ────────────────


def test_a_new_test_file_is_not_a_new_subsystem():
    """`tests/test_cf_router_candidate_response.py` blocked a PR as "a new
    router" because the indicators match on NAME. Every future
    `test_*router*` / `test_*store*` / `test_*approval*` would have too, and the
    only way past was to assert the file extends a subsystem — which is not
    true either. A test is neither new nor an extension. Nothing under tests/
    is installed by the deploy, so a subsystem cannot hide there."""
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
    })
    for path in (
        "tests/test_cf_router_candidate_response.py",
        "tests/test_flyer_routing_store.py",
        "tests/test_approvals_scheduler.py",
    ):
        code, out = run_checker(REPO_ROOT, changed=[path], added=[path], body=body)
        assert "GOV-SUBSYSTEM" not in out, f"{path} tripped the subsystem heuristic:\n{out}"


def test_the_heuristic_still_fires_on_a_real_added_subsystem():
    """Guard the guard. Exempting tests must not exempt src/."""
    body = reuse_map(**{
        "Affected projects": "shift-platform",
        "Applicable directives": "docs/governance/shared-platform-directive.md",
    })
    path = "src/platform/receipt_store.py"
    code, out = run_checker(REPO_ROOT, changed=[path], added=[path], body=body)
    assert "GOV-SUBSYSTEM-UNDECLARED" in out, f"a real added store no longer trips:\n{out}"


def test_the_exemption_does_not_cover_a_source_file_merely_named_test():
    """`_is_test_only_path` is narrow on purpose — a real module whose name
    contains "test" is still source and must still be judged."""
    assert not gov._is_test_only_path("src/platform/testing_router.py")
    assert not gov._is_test_only_path("src/agents/flyer/latest_store.py")
    assert gov._is_test_only_path("tests/test_cf_router_x.py")
    assert gov._is_test_only_path("src/agents/shift/tests/test_router.py")
