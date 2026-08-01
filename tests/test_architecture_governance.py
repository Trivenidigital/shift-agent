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
        "New subsystem": "none",
        "Evidence existing capabilities were insufficient": "n/a - no new subsystem",
        "Architecture exception": "none",
        "Shared-platform impact": "none",
        "Vertical E2E proof": "inbound WhatsApp menu photo through to priced pricebook",
    }
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
