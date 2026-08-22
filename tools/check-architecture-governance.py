#!/usr/bin/env python3
"""check-architecture-governance.py — deterministic, path-aware governance gate.

Enforces the mechanical parts of the governance model documented in
docs/governance/. It is CI-only: nothing here is imported by runtime code, and
it never reads or writes agent state.

What it verifies
  1. Canonical directive files exist.
  2. docs/governance/project-registry.yaml parses and is internally consistent.
  3. Every directive referenced by the registry exists.
  4. Every registered path pattern is repository-relative and normalized.
  5. No path is ambiguously owned by two projects (undeclared overlap).
  6. Every changed file classifies to at least one project; unknowns fail closed.
  7. Root and nested AGENTS files reference the correct directives.
  8. Claude / Copilot / Cursor instruction files resolve to the canonical set,
     and a project's Cursor rule does not reach an unrelated product.
  9. The PR body carries a Capability Reuse Map naming every affected project.
 10. Shared-platform runtime changes declare their affected agents.
 11. architecture-exceptions.yaml parses, has the required fields, and a cited
     exception is approved, unexpired and actually covers the changed paths.
 12. A newly ADDED file that looks like a new subsystem is declared as
     extending an existing one, or carries an approved exception. Modifying an
     existing module is never flagged by this heuristic.

Usage
  # registry / structure integrity only (runs on every PR)
  check-architecture-governance.py --registry-only

  # full check against a PR
  check-architecture-governance.py --base origin/main --pr-body body.md

  # explicit file lists (used by the tests)
  check-architecture-governance.py \
      --changed-files changed.txt --added-files added.txt --pr-body body.md

Exit codes
  0  pass
  1  one or more findings
  2  usage / environment error
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - environment error, not a finding
    print("check-architecture-governance: PyYAML is required", file=sys.stderr)
    raise SystemExit(2)

# ── constants ──────────────────────────────────────────────────────────────

GOVERNANCE_DIR = "docs/governance"
UNIVERSAL_DIRECTIVE = f"{GOVERNANCE_DIR}/engineering-directive.md"
SHARED_DIRECTIVE = f"{GOVERNANCE_DIR}/shared-platform-directive.md"
REGISTRY = f"{GOVERNANCE_DIR}/project-registry.yaml"
EXCEPTIONS = f"{GOVERNANCE_DIR}/architecture-exceptions.yaml"

REQUIRED_FILES = (
    UNIVERSAL_DIRECTIVE,
    SHARED_DIRECTIVE,
    REGISTRY,
    EXCEPTIONS,
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    ".github/pull_request_template.md",
    ".cursor/rules/engineering-directive.mdc",
)

PATH_CATEGORIES = ("source", "tests", "ops", "ui", "docs")

PROJECT_REQUIRED_KEYS = (
    "id",
    "name",
    "directive",
    "shared_platform",
    "lifecycle",
    "runtime",
    "paths",
    "shared_dependencies",
    "nested_agents_file",
)

EXCEPTION_REQUIRED_FIELDS = (
    "id",
    "status",
    "affected_project",
    "affected_paths",
    "subsystem_type",
    "scope",
    "reason",
    "existing_capability_found_insufficient",
    "evidence",
    "safety_justification",
    "approver",
    "approval_date",
    "expiration_or_review_condition",
)

EXCEPTION_STATUSES = ("proposed", "approved", "rejected", "expired")

REUSE_MAP_HEADING = "## Capability Reuse Map"

# THE authoritative, machine-enforced Capability Reuse Map schema.
#
# Every published template — engineering-directive.md §9, the AGENTS.md session
# bootstrap skeleton, and .github/pull_request_template.md — must reproduce
# these labels EXACTLY. Those documents necessarily repeat the labels; this
# tuple is what decides. tests/test_architecture_governance.py asserts each
# published template contains every field verbatim and carries no divergent
# replacement label, which is what keeps them from forking again.
#
# Matching is exact by design: no aliases, no singular/plural variants, no
# parenthetical variants, no fuzzy matching. A gate that accepts near-misses
# stops being a gate.
REUSE_MAP_FIELDS = (
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

# Structural signals that an ADDED file may be a new subsystem. These are
# supporting signals only: they gate a declaration requirement, never a verdict.
SUBSYSTEM_INDICATORS = {
    "store": r"(^|[_\-/])stores?([_\-.]|$)",
    "router": r"(^|[_\-/])rout(er|ing)([_\-.]|$)",
    "workflow-engine": r"(^|[_\-/])workflow(_?engine)?([_\-.]|$)",
    "scheduler": r"(^|[_\-/])schedul(er|ing)([_\-.]|$)",
    "approval-subsystem": r"(^|[_\-/])approvals?([_\-.]|$)",
    "importer": r"(^|[_\-/])import(er)?([_\-.]|$)",
    "state-machine": r"(^|[_\-/])state[_\-]?machine([_\-.]|$)",
    "orchestration-framework": r"(^|[_\-/])orchestrat\w*([_\-.]|$)",
}

EXTENDS_DECLARATION = re.compile(
    r"extends?\s+(?:the\s+)?existing\s+(?:sub)?system", re.IGNORECASE
)

# Fields whose ENTIRE purpose is to carry evidence. A bare placeholder in one of
# these answers nothing: the map exists, every label is filled, and the gate goes
# green having been told precisely nothing. Legitimate absence is still allowed,
# but it must be EXPLAINED -- `n/a - CI-only change, no runtime capability added`
# passes where a naked `n/a` does not.
NARRATIVE_REQUIRED_FIELDS = (
    "Requested outcome",
    "Applicable directives",
    "Existing platform/model capabilities reused",
    "Existing deterministic kernels reused",
    "Existing stores/workflows reused",
    "Evidence existing capabilities were insufficient",
    "Vertical E2E proof",
)

# Every REUSE_MAP_FIELD not listed above is genuinely nullable -- `none` is a
# real, checkable answer for "Thin adapters" or "Architecture exception", and
# forcing prose there would only teach authors to pad. `Shared-platform impact`
# and `Other agents affected` are nullable HERE and tightened contextually by
# the shared-runtime checks, which is where the context to judge them exists.

# Answers that assert nothing. Compared after case-folding and stripping every
# separator, so `N/A.`, `n / a` and `-- none --` all normalise onto one answer.
#
# Deliberately includes the EXPANSIONS, not just the abbreviations: an author
# told to stop writing `n/a` writes `not applicable`, and `Vertical E2E proof:
# done` / `green` / `passing` is what the same reflex produces there. Catching
# `na` while accepting `notapplicable` would have made this gate theatre.
_PLACEHOLDER_TOKENS = {
    "", "none", "na", "nil", "null", "nothing", "nothingtoadd", "nope",
    "notapplicable", "notrelevant", "notrequired", "notneeded", "noneneeded",
    "noneyet", "nonerequired", "nonetodeclare",
    "tbd", "tba", "tbc", "todo", "tobedetermined", "tobeconfirmed",
    "pending", "pendingreview", "unknown", "unclear",
    "x", "y", "n", "ok", "okay", "yes", "no", "done", "complete", "green",
    "passing", "passes", "cigreen", "citgreen", "0", "1",
    "same", "sameasabove", "asabove", "seeabove", "seesummary",
    "seethesummary", "seetheabove", "seepr", "seedescription", "seebelow",
    "seethedescription", "seeprdescription", "ditto", "above", "nochanges",
    "nothinghere", "notsure", "noidea", "asdiscussed",
    # Whole-phrase deferrals. Matched EXACTLY, never as a prefix: a first-word
    # rule rejected `pending approvals store` and `todo queue reused`, which are
    # honest answers naming real things, while still missing a deferral padded
    # past its bound. Deterministic syntax cannot separate a padded deferral
    # from prose; reviewer judgement owns that, and a false red here is the
    # worse failure.
    "tbdafterreview", "tbdbeforemerge", "todoafterreview", "todobeforemerge",
    "pendingafterreview", "tobedeterminedlater", "tobeconfirmedlater",
}

# Invisible characters that survive `.strip()` and let a "populated" field carry
# nothing a reader can see.
_INVISIBLE_RX = re.compile(r"[\u00a0\u180e\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


def visible_value(value: str) -> str:
    """The value with zero-width / directional characters removed."""
    return _INVISIBLE_RX.sub("", value).strip()


def is_bare_placeholder(value: str) -> bool:
    """Does this value consist of nothing but a placeholder token?

    Deterministic and deliberately shallow. It cannot tell whether prose is
    TRUE -- reviewer judgement owns that -- only whether the author supplied an
    answer at all. `n/a - no new subsystem` normalises to `nanonewsubsystem`
    and passes; `n/a` normalises to `na` and does not.

    Normalisation keeps unicode word characters rather than `[0-9a-z]`, so a
    non-Latin answer stays an answer instead of collapsing to `""` and being
    rejected as a placeholder it does not resemble.
    """
    visible = visible_value(value)
    normalised = re.sub(r"[^\w]+", "", visible, flags=re.UNICODE).lower()
    return normalised in _PLACEHOLDER_TOKENS

# Cursor rule stem → registry project id. A stem absent here must equal an id.
CURSOR_RULE_ALIASES = {"shared-platform-directive": "shift-platform"}
CURSOR_UNIVERSAL_RULE = "engineering-directive"

# Container rules exist so that no path is ever unclassified. They are NOT a
# claim of ownership over everything beneath them: a product-specific pattern
# is strictly more specific and always wins.
CONTAINER_PATTERNS = {
    "tests/**",
    "tools/**",
    "conftest.py",
    "src/platform/*.py",
    "web/backend/**",
    "web/frontend/**",
    "docs/**",
    "tasks/**",
}

# Code containers only. A product-owned test, fixture, tool, module or cockpit
# panel resolving to one of these is a registry defect (GOV-REG-ABSORBED) —
# comprehensive classification is not the same as correct classification.
#
# `docs/**` and `tasks/**` are deliberately NOT guarded: planning and review
# history is cross-product by nature and carries no runtime behavior, so a plan
# named for a product still belongs to repo-meta (see
# docs/governance/projects/repo-meta.md). Product-owned RUNBOOKS and scope docs
# are assigned explicitly to their products in the registry and therefore never
# reach the container rule.
ABSORPTION_GUARDED_CONTAINERS = {
    "tests/**",
    "tools/**",
    "conftest.py",
    "src/platform/*.py",
    "web/backend/**",
    "web/frontend/**",
}

# Path substrings that unambiguously name a product, used ONLY to detect
# container absorption. This is a supporting signal that raises a registry
# finding — it never decides ownership, which the registry alone does.
PRODUCT_NAME_SIGNALS = {
    "catering": "catering-studio",
    "flyer": "flyer-studio",
    "commerce": "commerce-platform",
    "expense": "expense-bookkeeper",
    "qbo": "expense-bookkeeper",
    "compliance": "compliance",
    "daily_brief": "daily-brief",
    "daily-brief": "daily-brief",
    "multi_location": "multi-location",
    "eod": "eod-reconcile",
}

BLOCKER = "BLOCKER"
HIGH = "HIGH"


@dataclass
class Finding:
    code: str
    severity: str
    message: str

    def render(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass
class Project:
    id: str
    raw: dict
    patterns: list[tuple[str, str]] = field(default_factory=list)  # (category, pattern)

    @property
    def directive(self) -> str:
        return self.raw.get("directive", "")

    @property
    def shared_platform(self) -> bool:
        return bool(self.raw.get("shared_platform"))

    @property
    def nested_agents_file(self) -> Optional[str]:
        return self.raw.get("nested_agents_file") or None

    @property
    def impact_analysis_paths(self) -> list[str]:
        return list(self.raw.get("impact_analysis_paths") or [])

    @property
    def shared_dependencies(self) -> list[str]:
        return list(self.raw.get("shared_dependencies") or [])

    def all_patterns(self) -> list[str]:
        return [p for _, p in self.patterns]


# ── path pattern handling ──────────────────────────────────────────────────



def _is_test_only_path(path: str) -> bool:
    """True for files that exist only to test other code.

    Deliberately narrow: a top-level `tests/` tree, or a `tests/` directory
    nested inside a source package. NOT any path merely containing the word
    "test", which would exempt a real `src/platform/testing_router.py`.
    """
    parts = path.split("/")
    return "tests" in parts or parts[-1].startswith("test_")


def pattern_problems(pattern: str) -> list[str]:
    """Return the reasons `pattern` is not an acceptable registry path pattern."""
    problems: list[str] = []
    if not isinstance(pattern, str) or not pattern.strip():
        return ["empty pattern"]
    if pattern != pattern.strip():
        problems.append("leading/trailing whitespace")
    if pattern.startswith("/"):
        problems.append("absolute path")
    if "\\" in pattern:
        problems.append("backslash separator (use POSIX '/')")
    if pattern.startswith("./"):
        problems.append("leading './'")
    if ".." in pattern.split("/"):
        problems.append("'..' segment")
    if "//" in pattern:
        problems.append("empty path segment")
    if pattern.strip("/") in {"**", "*"}:
        problems.append("bare wildcard catch-all")
    return problems


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a registry glob to an anchored regex.

    `**` matches any characters including `/`; `*` and `?` do not cross `/`.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def specificity(pattern: str) -> int:
    """Length of the literal prefix before the first wildcard."""
    idx = len(pattern)
    for wildcard in ("*", "?"):
        pos = pattern.find(wildcard)
        if pos != -1:
            idx = min(idx, pos)
    return idx


class Matcher:
    """Compiled pattern set with longest-literal-prefix-wins classification."""

    def __init__(self, projects: list[Project], excluded: Iterable[str]) -> None:
        self._entries: list[tuple[re.Pattern[str], int, str, str]] = []
        for proj in projects:
            for category, pattern in proj.patterns:
                self._entries.append(
                    (pattern_to_regex(pattern), specificity(pattern), proj.id, pattern)
                )
            _ = category  # category retained on Project.patterns for reporting
        self._excluded = [pattern_to_regex(p) for p in excluded]

    def is_excluded(self, path: str) -> bool:
        return any(rx.match(path) for rx in self._excluded)

    def matches(self, path: str) -> list[tuple[int, str, str]]:
        """All (specificity, project_id, pattern) entries matching `path`."""
        return [
            (spec, pid, pat)
            for rx, spec, pid, pat in self._entries
            if rx.match(path)
        ]

    def classify(self, path: str) -> tuple[Optional[str], list[str], Optional[str]]:
        """Return (owner_id, tied_ids, winning_pattern) for `path`."""
        hits = self.matches(path)
        if not hits:
            return None, [], None
        best = max(h[0] for h in hits)
        top = [h for h in hits if h[0] == best]
        owners = sorted({h[1] for h in top})
        return owners[0], owners, top[0][2]


# ── the checker ────────────────────────────────────────────────────────────


class GovernanceChecker:
    def __init__(self, repo_root: Path) -> None:
        self.root = repo_root
        self.findings: list[Finding] = []
        self.projects: list[Project] = []
        self.by_id: dict[str, Project] = {}
        self.registry: dict = {}
        self.exceptions: dict[str, dict] = {}
        self.overlaps: list[dict] = []
        self.excluded: list[str] = []
        self.matcher: Optional[Matcher] = None
        self.affected: list[str] = []

    # -- helpers ----------------------------------------------------------

    def add(self, code: str, message: str, severity: str = BLOCKER) -> None:
        self.findings.append(Finding(code, severity, message))

    def read(self, rel: str) -> Optional[str]:
        p = self.root / rel
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    def tracked_files(self) -> list[str]:
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "ls-files"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return []
        return [line for line in out.splitlines() if line]

    # -- 1. structure -----------------------------------------------------

    def check_structure(self) -> None:
        for rel in REQUIRED_FILES:
            if not (self.root / rel).is_file():
                self.add("GOV-STRUCT-MISSING", f"required governance file missing: {rel}")

    # -- 2. registry ------------------------------------------------------

    def load_registry(self) -> bool:
        text = self.read(REGISTRY)
        if text is None:
            self.add("GOV-REG-MISSING", f"{REGISTRY} not found")
            return False
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            self.add("GOV-REG-PARSE", f"{REGISTRY} does not parse: {exc}")
            return False
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
            self.add("GOV-REG-SHAPE", f"{REGISTRY} must be a mapping with a `projects` list")
            return False

        self.registry = data
        if not data.get("version"):
            self.add("GOV-REG-VERSION", f"{REGISTRY} has no `version`")
        self.overlaps = list(data.get("overlaps") or [])
        self.excluded = list(data.get("excluded") or [])

        seen: set[str] = set()
        for raw in data["projects"]:
            if not isinstance(raw, dict):
                self.add("GOV-REG-SHAPE", "each project entry must be a mapping")
                continue
            pid = raw.get("id")
            if not pid:
                self.add("GOV-REG-ID", "project entry has no `id`")
                continue
            if pid in seen:
                self.add("GOV-REG-DUPID", f"duplicate project id: {pid}")
                continue
            seen.add(pid)

            for key in PROJECT_REQUIRED_KEYS:
                if key not in raw:
                    self.add("GOV-REG-FIELD", f"project `{pid}` is missing required key `{key}`")

            proj = Project(id=pid, raw=raw)
            paths = raw.get("paths") or {}
            if not isinstance(paths, dict):
                self.add("GOV-REG-PATHS", f"project `{pid}` has a non-mapping `paths`")
                paths = {}
            for category in PATH_CATEGORIES:
                if category not in paths:
                    self.add(
                        "GOV-REG-PATHCAT",
                        f"project `{pid}` is missing path category `{category}` "
                        "(use an empty list if it has none)",
                    )
                for pattern in paths.get(category) or []:
                    for problem in pattern_problems(pattern):
                        self.add(
                            "GOV-REG-PATTERN",
                            f"project `{pid}` path `{pattern}` ({category}): {problem}",
                        )
                    proj.patterns.append((category, pattern))
            if not proj.patterns:
                self.add("GOV-REG-EMPTY", f"project `{pid}` registers no paths")

            self.projects.append(proj)
            self.by_id[pid] = proj

        for pattern in self.excluded:
            for problem in pattern_problems(pattern):
                if problem == "bare wildcard catch-all":
                    continue  # excluded patterns may be broad by design
                self.add("GOV-REG-PATTERN", f"excluded path `{pattern}`: {problem}")

        for proj in self.projects:
            if proj.directive and not (self.root / proj.directive).is_file():
                self.add(
                    "GOV-REG-DIRECTIVE",
                    f"project `{proj.id}` references a missing directive: {proj.directive}",
                )
            for dep in proj.shared_dependencies:
                if dep not in self.by_id:
                    self.add(
                        "GOV-REG-DEP",
                        f"project `{proj.id}` declares unknown shared dependency `{dep}`",
                    )
            for pattern in proj.impact_analysis_paths:
                if pattern not in proj.all_patterns():
                    self.add(
                        "GOV-REG-IMPACT",
                        f"project `{proj.id}` impact_analysis_path `{pattern}` "
                        "is not one of its registered paths",
                    )

        self.matcher = Matcher(self.projects, self.excluded)
        return True

    def _declared_overlap(self, ids: Iterable[str], path: Optional[str] = None) -> bool:
        """True when `ids` are jointly declared in an overlap entry.

        An overlap is scoped to BOTH a project set and a path set: a declared
        web/** container overlap must not silently authorize an ambiguity
        somewhere under src/agents/. When `path` is given, the entry's `paths`
        must also match it.
        """
        wanted = set(ids)
        for entry in self.overlaps:
            declared = set(entry.get("projects") or [])
            if not wanted <= declared:
                continue
            patterns = entry.get("paths") or []
            if path is None:
                return True
            if any(pattern_to_regex(p).match(path) for p in patterns):
                return True
        return False

    def resolve(self, path: str) -> dict:
        """Full ownership resolution for one path — the audit record."""
        assert self.matcher is not None
        owner, owners, pattern = self.matcher.classify(path)
        proj = self.by_id.get(owner) if owner else None
        directives: list[str] = []
        if proj:
            directives.append(proj.directive)
            if proj.shared_platform and proj.directive != SHARED_DIRECTIVE:
                directives.append(SHARED_DIRECTIVE)
        overlap = None
        if len(owners) > 1:
            for entry in self.overlaps:
                if set(owners) <= set(entry.get("projects") or []) and any(
                    pattern_to_regex(p).match(path) for p in entry.get("paths") or []
                ):
                    overlap = entry.get("id")
                    break
        # De-duplicate while preserving order: repo-governance's own directive
        # IS the universal directive.
        seen: set[str] = set()
        applicable = [
            d for d in [UNIVERSAL_DIRECTIVE] + directives
            if d and not (d in seen or seen.add(d))
        ]
        return {
            "path": path,
            "project": owner,
            "tied_projects": owners,
            "winning_rule": pattern,
            "rule_kind": "container" if pattern in CONTAINER_PATTERNS else "product-specific",
            "specificity": specificity(pattern) if pattern else None,
            "directives": applicable,
            "declared_overlap": overlap,
        }

    def check_container_absorption(self) -> None:
        """A product-named file must not be owned via a broad container rule.

        Comprehensive classification is not the same as correct classification:
        `tests/**` can classify every test while silently assigning Catering
        and Flyer suites to the shared platform. This makes that failure loud.
        """
        assert self.matcher is not None
        for path in self.tracked_files():
            if self.matcher.is_excluded(path):
                continue
            owner, _, pattern = self.matcher.classify(path)
            if owner is None or pattern not in ABSORPTION_GUARDED_CONTAINERS:
                continue
            lowered = path.lower()
            for signal, expected in PRODUCT_NAME_SIGNALS.items():
                if signal not in lowered or expected == owner:
                    continue
                if expected not in self.by_id:
                    continue
                self.add(
                    "GOV-REG-ABSORBED",
                    f"`{path}` resolves to `{owner}` only via the container rule "
                    f"`{pattern}`, but its path names `{expected}`. Container rules must "
                    "not absorb product-owned files — add a specific pattern under "
                    f"`{expected}`, or rename the file if it is genuinely shared",
                )
                break

    def check_overlaps(self) -> None:
        """A tracked file whose top-specificity match is tied across projects is
        ambiguously owned and must be declared in `overlaps`."""
        assert self.matcher is not None
        for path in self.tracked_files():
            if self.matcher.is_excluded(path):
                continue
            _, owners, _ = self.matcher.classify(path)
            if len(owners) > 1 and not self._declared_overlap(owners, path):
                self.add(
                    "GOV-OVERLAP",
                    f"`{path}` is claimed at equal specificity by {', '.join(owners)}; "
                    "declare it under `overlaps:` or make one pattern more specific",
                )

    # -- 3. instruction files --------------------------------------------

    def check_instruction_files(self) -> None:
        root_agents = self.read("AGENTS.md")
        if root_agents is not None:
            for ref in (UNIVERSAL_DIRECTIVE, REGISTRY):
                if ref not in root_agents:
                    self.add("GOV-AGENTS-REF", f"AGENTS.md does not reference {ref}")
            if REUSE_MAP_HEADING.lower().replace("## ", "") not in root_agents.lower():
                self.add("GOV-AGENTS-REF", "AGENTS.md does not require a Capability Reuse Map")

        for rel in ("CLAUDE.md", ".github/copilot-instructions.md"):
            text = self.read(rel)
            if text is None:
                continue
            for ref in ("AGENTS.md", UNIVERSAL_DIRECTIVE, REGISTRY):
                if ref not in text:
                    self.add("GOV-POINTER-REF", f"{rel} does not reference {ref}")
            if "Capability Reuse Map" not in text:
                self.add("GOV-POINTER-REF", f"{rel} does not require a Capability Reuse Map")

        for proj in self.projects:
            nested = proj.nested_agents_file
            if not nested:
                continue
            text = self.read(nested)
            if text is None:
                self.add(
                    "GOV-NESTED-MISSING",
                    f"project `{proj.id}` declares nested_agents_file `{nested}` which does not exist",
                )
                continue
            depth = nested.count("/")
            prefix = "/".join([".."] * depth) if depth else "."
            expected = {
                f"{prefix}/AGENTS.md": "root AGENTS.md",
                f"{prefix}/{UNIVERSAL_DIRECTIVE}": "the universal directive",
                f"{prefix}/{proj.directive}": "its own project directive",
            }
            for ref, label in expected.items():
                if ref not in text:
                    self.add(
                        "GOV-NESTED-REF",
                        f"nested instruction file `{nested}` does not resolve to {label} "
                        f"(expected reference `{ref}`)",
                    )

    def check_cursor_rules(self) -> None:
        rules_dir = self.root / ".cursor" / "rules"
        if not rules_dir.is_dir():
            self.add("GOV-CURSOR-MISSING", ".cursor/rules/ not found")
            return

        universal = rules_dir / f"{CURSOR_UNIVERSAL_RULE}.mdc"
        if universal.is_file():
            head = universal.read_text(encoding="utf-8")
            if not re.search(r"^alwaysApply:\s*true\s*$", head, re.MULTILINE):
                self.add(
                    "GOV-CURSOR-UNIVERSAL",
                    f"{universal.relative_to(self.root)} must set `alwaysApply: true`",
                )
            for ref in ("AGENTS.md", UNIVERSAL_DIRECTIVE, REGISTRY):
                if ref not in head:
                    self.add(
                        "GOV-CURSOR-UNIVERSAL",
                        f"{universal.relative_to(self.root)} does not reference {ref}",
                    )

        assert self.matcher is not None
        tracked = self.tracked_files()
        for rule in sorted(rules_dir.glob("*.mdc")):
            stem = rule.stem
            if stem == CURSOR_UNIVERSAL_RULE:
                continue
            pid = CURSOR_RULE_ALIASES.get(stem, stem)
            proj = self.by_id.get(pid)
            if proj is None:
                self.add(
                    "GOV-CURSOR-PROJECT",
                    f".cursor/rules/{rule.name} does not map to a registry project "
                    f"(resolved id `{pid}`)",
                )
                continue

            globs = self._cursor_globs(rule.read_text(encoding="utf-8"))
            if re.search(r"^alwaysApply:\s*true\s*$", rule.read_text(encoding="utf-8"), re.MULTILINE):
                self.add(
                    "GOV-CURSOR-SCOPE",
                    f".cursor/rules/{rule.name} is project-scoped and must not set "
                    "`alwaysApply: true`",
                )
            if not globs:
                self.add(
                    "GOV-CURSOR-SCOPE",
                    f".cursor/rules/{rule.name} declares no `globs:` — project rules must "
                    "use exact path globs from the registry",
                )
                continue

            registered = set(proj.all_patterns())
            for glob in globs:
                if glob not in registered:
                    self.add(
                        "GOV-CURSOR-SCOPE",
                        f".cursor/rules/{rule.name} glob `{glob}` is not a registered path "
                        f"of project `{pid}`",
                    )
                    continue
                # A project rule must not reach files owned by an unrelated
                # product unless that overlap is declared.
                rx = pattern_to_regex(glob)
                for path in tracked:
                    if not rx.match(path) or self.matcher.is_excluded(path):
                        continue
                    owner, _, _ = self.matcher.classify(path)
                    if owner and owner != pid and not self._declared_overlap({pid, owner}, path):
                        self.add(
                            "GOV-CURSOR-CROSS",
                            f".cursor/rules/{rule.name} (project `{pid}`) matches `{path}`, "
                            f"which is owned by `{owner}` — a project rule must not apply to "
                            "an unrelated product",
                        )
                        break

    @staticmethod
    def _cursor_globs(text: str) -> list[str]:
        blocks = text.split("---")
        if len(blocks) < 3:
            return []
        front = blocks[1]
        globs: list[str] = []
        in_globs = False
        for line in front.splitlines():
            if re.match(r"^globs:\s*$", line):
                in_globs = True
                continue
            if in_globs:
                m = re.match(r"^\s+-\s*(.+?)\s*$", line)
                if m:
                    globs.append(m.group(1).strip("'\""))
                    continue
                in_globs = False
            m = re.match(r"^globs:\s*(.+)$", line)
            if m:
                globs.extend(g.strip().strip("'\"") for g in m.group(1).split(",") if g.strip())
        return globs

    # -- 4. exceptions ----------------------------------------------------

    def load_exceptions(self) -> None:
        text = self.read(EXCEPTIONS)
        if text is None:
            self.add("GOV-EXC-MISSING", f"{EXCEPTIONS} not found")
            return
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            self.add("GOV-EXC-PARSE", f"{EXCEPTIONS} does not parse: {exc}")
            return
        entries = data.get("exceptions")
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            self.add("GOV-EXC-SHAPE", f"{EXCEPTIONS} `exceptions` must be a list")
            return

        assert self.matcher is not None
        for raw in entries:
            if not isinstance(raw, dict):
                self.add("GOV-EXC-SHAPE", "each exception entry must be a mapping")
                continue
            eid = raw.get("id") or "<no id>"
            for fieldname in EXCEPTION_REQUIRED_FIELDS:
                if fieldname not in raw:
                    self.add("GOV-EXC-FIELD", f"exception `{eid}` is missing field `{fieldname}`")
            status = raw.get("status")
            if status not in EXCEPTION_STATUSES:
                self.add(
                    "GOV-EXC-STATUS",
                    f"exception `{eid}` has status `{status}`; allowed: "
                    + ", ".join(EXCEPTION_STATUSES),
                )
            proj_id = raw.get("affected_project")
            if proj_id and proj_id not in self.by_id:
                self.add(
                    "GOV-EXC-PROJECT",
                    f"exception `{eid}` names unknown project `{proj_id}`",
                )
            for pattern in raw.get("affected_paths") or []:
                for problem in pattern_problems(pattern):
                    self.add("GOV-EXC-PATH", f"exception `{eid}` path `{pattern}`: {problem}")
                if proj_id and proj_id in self.by_id:
                    owner, _, _ = self.matcher.classify(pattern)
                    if owner and owner != proj_id:
                        self.add(
                            "GOV-EXC-SCOPE",
                            f"exception `{eid}` is scoped to `{proj_id}` but path "
                            f"`{pattern}` is owned by `{owner}` — an exception cannot "
                            "authorize another product's paths",
                        )
            if eid != "<no id>":
                self.exceptions[str(eid)] = raw

    @staticmethod
    def _is_expired(raw: dict, today: _dt.date) -> bool:
        if raw.get("status") == "expired":
            return True
        cond = raw.get("expiration_or_review_condition")
        if isinstance(cond, _dt.date):
            return cond < today
        if isinstance(cond, str):
            m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", cond)
            if m:
                try:
                    return _dt.date(int(m[1]), int(m[2]), int(m[3])) < today
                except ValueError:
                    return False
        return False

    def exception_covers(self, eid: str, paths: Iterable[str], today: _dt.date) -> bool:
        raw = self.exceptions.get(eid)
        if raw is None:
            self.add("GOV-EXC-UNKNOWN", f"PR cites unknown architecture exception `{eid}`")
            return False
        if raw.get("status") != "approved":
            self.add(
                "GOV-EXC-NOTAPPROVED",
                f"exception `{eid}` has status `{raw.get('status')}` — only `approved` "
                "exceptions authorize otherwise prohibited architecture",
            )
            return False
        if self._is_expired(raw, today):
            self.add("GOV-EXC-EXPIRED", f"exception `{eid}` is expired and authorizes nothing")
            return False
        regexes = [pattern_to_regex(p) for p in raw.get("affected_paths") or []]
        uncovered = [p for p in paths if not any(rx.match(p) for rx in regexes)]
        if uncovered:
            self.add(
                "GOV-EXC-UNCOVERED",
                f"exception `{eid}` does not cover changed path(s): "
                + ", ".join(sorted(uncovered)[:5]),
            )
            return False
        return True

    # -- 5. classification + PR body -------------------------------------

    def classify_changes(self, changed: list[str]) -> list[str]:
        assert self.matcher is not None
        affected: list[str] = []
        for path in changed:
            if self.matcher.is_excluded(path):
                continue
            owner, owners, _ = self.matcher.classify(path)
            if owner is None:
                self.add(
                    "GOV-UNCLASSIFIED",
                    f"changed path `{path}` maps to no project — classify it in "
                    f"{REGISTRY} before merging",
                )
                continue
            if len(owners) > 1 and not self._declared_overlap(owners, path):
                self.add(
                    "GOV-OVERLAP",
                    f"changed path `{path}` is ambiguously owned by {', '.join(owners)}",
                )
            for pid in owners if self._declared_overlap(owners, path) else [owner]:
                if pid not in affected:
                    affected.append(pid)
        self.affected = affected
        return affected

    @staticmethod
    def _deemphasize(text: str) -> str:
        """Drop markdown bold/italic markers so `- **Label:** v` reads like `- Label: v`.

        Authors bold their Reuse Map labels; the gate must not depend on that
        choice. Only the emphasis markers are removed — the values are intact
        for the emptiness and project-name checks that follow.
        """
        return re.sub(r"\*\*|__", "", text)

    # Markdown that is NOT the author's answer. A PR body routinely quotes the
    # blank schema in a fence (every governance/template PR does) and carries
    # `<!-- -->` guidance from the published template. Parsing those cuts both
    # ways: a quoted blank label poisons a field the author DID answer, and a
    # quoted populated label supplies a value the visible map contradicts.
    #
    # Both patterns REQUIRE their terminator and so fail OPEN. An unterminated
    # fence or comment used to swallow everything to end-of-body, deleting the
    # author's real map and reporting `no Capability Reuse Map section` about a
    # body that visibly contains one. Stripping nothing risks a stale value;
    # stripping everything destroys the map. Fences match runs of >= 3 markers
    # so a ````-fenced block (exactly how you quote content containing ```) is
    # handled.
    _FENCE_RX = re.compile(r"^[^\S\n]*(?P<f>`{3,}|~{3,})[^\n]*\n.*?^[^\S\n]*(?P=f)`*~*[^\S\n]*$",
                           re.DOTALL | re.MULTILINE)
    _COMMENT_RX = re.compile(r"<!--.*?-->", re.DOTALL)

    @classmethod
    def _authored_text(cls, body: str) -> str:
        """The body with quoted and commented-out regions removed."""
        return cls._FENCE_RX.sub("", cls._COMMENT_RX.sub("", body))

    @classmethod
    def _any_field_line(cls) -> re.Pattern[str]:
        """Matches ANY enforced Reuse Map label line, at any indent."""
        alt = "|".join(re.escape(f) for f in REUSE_MAP_FIELDS)
        return re.compile(rf"^[^\S\n]*[-*]?[^\S\n]*(?:{alt})[^\S\n]*:", re.IGNORECASE)

    @classmethod
    def _field_occurrences(cls, body: str, label: str) -> list[str]:
        r"""Every value written against this label, in document order.

        A value is the label's own line PLUS any following lines indented
        deeper than it, because listing reused capabilities as sub-bullets, or
        wrapping a long directive list, is the natural way to answer these
        fields -- and reading only the first physical line called the richest
        answer in the map "blank".

        A SIBLING ROW IS NEVER ABSORBED, whatever its indent. That guard, not
        the indent arithmetic, is what keeps this from re-opening the defect
        the parser exists to fix. Indent alone was not enough: CommonMark
        treats 0-3 spaces before `- ` as the same list level, so a row written
        one space deeper than its predecessor renders as a flat sibling but
        measured as a child -- which let a blank field capture the next row's
        text again, and silenced the shared-platform gate on a one-space diff
        no reviewer could see. Indents are tab-expanded for the same reason:
        a raw `len` counts a tab as one column.

        The directive tells authors to repeat the whole block under a
        `### <project-id>` heading for a multi-project change, so one label
        legitimately appears more than once. Checks asking "was this answered
        anywhere" union these; checks asking "is any answer blank" scan all.
        """
        h = r"[^\S\n]*"
        rx = re.compile(rf"^({h})[-*]?{h}{re.escape(label)}{h}:{h}(.*)$", re.IGNORECASE)
        sibling = cls._any_field_line()
        lines = cls._deemphasize(cls._authored_text(body)).split("\n")
        out: list[str] = []
        for i, line in enumerate(lines):
            m = rx.match(line)
            if m is None:
                continue
            indent = len(m.group(1).expandtabs(4))
            parts = [m.group(2).strip()]
            for follower in lines[i + 1:]:
                if not follower.strip():
                    break
                if sibling.match(follower):
                    break
                expanded = follower.expandtabs(4)
                if len(expanded) - len(expanded.lstrip()) <= indent:
                    break
                parts.append(follower.strip().lstrip("-*").strip())
            out.append(" ".join(p for p in parts if p).strip())
        return out

    @classmethod
    def _field_value(cls, body: str, label: str) -> Optional[str]:
        """The first value written against this label.

        `None` means the label is absent; `""` means it is present with nothing
        after the colon. Those are different facts and different checks key off
        each, so they must never collapse into one another.
        """
        found = cls._field_occurrences(body, label)
        return found[0] if found else None

    @classmethod
    def _field_union(cls, body: str, label: str) -> str:
        """All values for this label joined -- for "was it named at all" checks."""
        return " ".join(cls._field_occurrences(body, label))

    def check_pr_body(self, body: str, changed: list[str], today: _dt.date) -> None:
        if REUSE_MAP_HEADING not in self._deemphasize(self._authored_text(body)):
            self.add(
                "GOV-PR-NOMAP",
                f"PR body has no `{REUSE_MAP_HEADING}` section — a verbal reuse claim "
                "is insufficient",
            )
            return

        # Layer 1 -- every label carries an explicit value. A blank field and an
        # absent field are different author acts (forgot the schema vs. skipped
        # the question) and get different codes, but neither is an answer.
        for label in REUSE_MAP_FIELDS:
            occurrences = self._field_occurrences(body, label)
            if not occurrences:
                self.add("GOV-PR-FIELD", f"Capability Reuse Map is missing the `{label}:` field")
            elif not all(visible_value(v) for v in occurrences):
                self.add(
                    "GOV-PR-EMPTY",
                    f"Capability Reuse Map field `{label}:` is blank. Write the answer "
                    "explicitly -- for a genuinely nullable field that means `none`, not "
                    "an empty line.",
                )

        # Layer 2 -- evidence-bearing fields reject bare placeholders.
        for label in NARRATIVE_REQUIRED_FIELDS:
            value = next(
                (v for v in self._field_occurrences(body, label) if v and is_bare_placeholder(v)),
                None,
            )
            if value:
                self.add(
                    "GOV-PR-PLACEHOLDER",
                    f"Capability Reuse Map field `{label}:` answers `{value}`, which asserts "
                    "nothing. This field carries evidence; if the honest answer is absence, "
                    "say why (`n/a - CI-only change, no runtime capability added`).",
                )

        # Layer 3 -- `Affected projects` is checked against registry-resolved
        # fact, IN ITS OWN FIELD. The previous whole-body substring let a project
        # id satisfy this by appearing in a pasted diff path or a passing remark;
        # `compliance` and `flyer-studio` are both substrings of their own
        # registered paths, so for those two the check could be met by a PR that
        # never declared them at all.
        declared_projects = self._field_union(body, "Affected projects")
        for pid in self.affected:
            if pid not in declared_projects:
                self.add(
                    "GOV-PR-PROJECT",
                    f"changed files affect project `{pid}` but the Capability Reuse Map's "
                    "`Affected projects:` field does not name it",
                )

        # Layer 3 -- `Applicable directives` must name each affected project's
        # REGISTERED directive. Naming a directive that governs something else is
        # the same failure as naming none.
        declared_directives = self._field_union(body, "Applicable directives")
        for pid in self.affected:
            proj = self.by_id.get(pid)
            directive = proj.directive if proj else ""
            if directive and directive not in declared_directives:
                self.add(
                    "GOV-PR-DIRECTIVE-MISSING",
                    f"project `{pid}` is affected but its registered directive "
                    f"`{directive}` is not named in `Applicable directives:`",
                )

        # Shared-platform runtime changes must declare affected agents.
        assert self.matcher is not None
        for pid in self.affected:
            proj = self.by_id.get(pid)
            if proj is None or not proj.shared_platform:
                continue
            impact_rx = [pattern_to_regex(p) for p in proj.impact_analysis_paths]
            touched_runtime = any(
                any(rx.match(path) for rx in impact_rx)
                # A nested AGENTS.md sits inside a runtime directory but is an
                # instruction pointer, never shared runtime. Adding one must not
                # demand a fleet-wide impact analysis.
                and Path(path).name != "AGENTS.md"
                for path in changed
            )
            if not touched_runtime:
                continue
            # `Shared-platform impact` is nullable in general -- most PRs touch no
            # shared runtime. THIS one does, so the field stops being nullable and
            # has to carry a description. `none`, blank and `x` are equally silent.
            value = self._field_value(body, "Shared-platform impact") or ""
            if is_bare_placeholder(value):
                self.add(
                    "GOV-PR-SHARED",
                    f"`{pid}` is shared platform and its runtime changed — "
                    "`Shared-platform impact:` must describe the effect; "
                    f"`{value}` describes nothing",
                )
            dependents = [
                p.id for p in self.projects if pid in p.shared_dependencies
            ]
            # Look only in the fields that are supposed to carry the answer —
            # a project id mentioned incidentally elsewhere is not a declaration.
            declaration = " ".join(
                self._field_union(body, label)
                for label in ("Affected projects", "Shared-platform impact", "Other agents affected")
            )
            named = [d for d in dependents if d in declaration]
            if dependents and not named:
                self.add(
                    "GOV-PR-SHARED-AGENTS",
                    f"`{pid}` is shared platform and its runtime changed — the PR must name "
                    "the affected agents (candidates: " + ", ".join(sorted(dependents)) + ")",
                )

        # Cited exception, if any, must be approved, unexpired and in scope.
        cited = self._field_value(body, "Architecture exception") or ""
        for eid in re.findall(r"\b([A-Z][A-Z0-9]*-EX-\d+)\b", cited):
            self.exception_covers(eid, changed, today)

    # -- 6. new-subsystem heuristic --------------------------------------

    def check_new_subsystems(self, added: list[str], body: str, today: _dt.date) -> None:
        assert self.matcher is not None
        suspicious: list[tuple[str, str]] = []
        for path in added:
            if self.matcher.is_excluded(path):
                continue
            if _is_test_only_path(path):
                # A test file is never a new subsystem. The indicators match on
                # NAME, so `tests/test_cf_router_candidate_response.py` reads as a
                # new router purely because it tests one — and every future
                # `test_*router*`, `test_*store*`, `test_*approval*` would too.
                # Asking a test author "does this extend an existing subsystem?"
                # has no true answer: it is neither new nor an extension, it is a
                # test, and the only way past the gate was to assert something
                # false. Nothing under tests/ is installed by the deploy, so a
                # subsystem cannot hide here.
                continue
            for kind, rx in SUBSYSTEM_INDICATORS.items():
                if re.search(rx, path, re.IGNORECASE):
                    suspicious.append((path, kind))
                    break
        if not suspicious:
            return

        declared_new = (self._field_value(body, "New subsystem") or "").lower()
        # The author's own declaration only. Read from the raw body, a reviewer
        # note in an HTML comment -- phrased the way GOV-SUBSYSTEM-UNDECLARED
        # itself instructs -- silently waived the whole subsystem gate.
        extends = bool(EXTENDS_DECLARATION.search(self._authored_text(body)))
        cited = self._field_value(body, "Architecture exception") or ""
        eids = re.findall(r"\b([A-Z][A-Z0-9]*-EX-\d+)\b", cited)

        for path, kind in suspicious:
            if extends:
                continue
            if not is_bare_placeholder(declared_new):
                # Declared as new — must be carried by an approved exception.
                if any(self.exception_covers(eid, [path], today) for eid in eids):
                    continue
                self.add(
                    "GOV-SUBSYSTEM-NOEXC",
                    f"added file `{path}` is declared as a new subsystem ({kind}) but no "
                    "approved architecture exception covers it",
                )
                continue
            self.add(
                "GOV-SUBSYSTEM-UNDECLARED",
                f"added file `{path}` looks like a new {kind}. Declare in the Reuse Map "
                "that it extends an existing subsystem, or record an approved exception. "
                "(Heuristic: it fires only on ADDED files, never on edits to existing "
                "modules.)",
            )

    # -- orchestration ----------------------------------------------------

    def run(
        self,
        changed: Optional[list[str]],
        added: Optional[list[str]],
        body: Optional[str],
        today: _dt.date,
    ) -> None:
        self.check_structure()
        if not self.load_registry():
            return
        self.check_overlaps()
        self.check_container_absorption()
        self.check_instruction_files()
        self.check_cursor_rules()
        self.load_exceptions()

        if changed is None:
            return
        self.classify_changes(changed)
        if body is None:
            return
        self.check_pr_body(body, changed, today)
        self.check_new_subsystems(added or [], body, today)


# ── CLI ────────────────────────────────────────────────────────────────────


def _read_list(spec: str) -> list[str]:
    if spec == "-":
        return [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    p = Path(spec)
    if not p.is_file():
        print(f"check-architecture-governance: no such file: {spec}", file=sys.stderr)
        raise SystemExit(2)
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _git_changes(root: Path, base: str) -> tuple[list[str], list[str]]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-status", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check-architecture-governance: git diff failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
    changed: list[str] = []
    added: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        changed.append(path)
        if status.startswith("A"):
            added.append(path)
    return changed, added


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic, path-aware architecture governance gate (CI only).",
    )
    ap.add_argument("--repo-root", default=None, help="repository root (default: auto)")
    ap.add_argument("--registry-only", action="store_true", help="integrity checks only")
    ap.add_argument("--base", default=None, help="git ref to diff against (e.g. origin/main)")
    ap.add_argument("--changed-files", default=None, help="file with changed paths, or '-'")
    ap.add_argument("--added-files", default=None, help="file with added paths, or '-'")
    ap.add_argument("--pr-body", default=None, help="file containing the PR body")
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD, tests)")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument(
        "--resolve",
        nargs="+",
        metavar="PATH",
        help="print the ownership-resolution record for each PATH and exit",
    )
    args = ap.parse_args(argv)

    root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parent.parent
    if not (root / GOVERNANCE_DIR).is_dir():
        print(
            f"check-architecture-governance: {GOVERNANCE_DIR} not found under {root}",
            file=sys.stderr,
        )
        return 2

    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()

    changed: Optional[list[str]] = None
    added: Optional[list[str]] = None
    if not args.registry_only:
        if args.changed_files:
            changed = _read_list(args.changed_files)
            added = _read_list(args.added_files) if args.added_files else []
        elif args.base:
            changed, added = _git_changes(root, args.base)

    body: Optional[str] = None
    if args.pr_body:
        bp = Path(args.pr_body)
        if not bp.is_file():
            print(f"check-architecture-governance: no such PR body: {args.pr_body}", file=sys.stderr)
            return 2
        body = bp.read_text(encoding="utf-8")

    checker = GovernanceChecker(root)

    if args.resolve:
        checker.check_structure()
        if not checker.load_registry():
            for f in checker.findings:
                print(f.render())
            return 1
        records = [checker.resolve(p) for p in args.resolve]
        if args.format == "json":
            print(json.dumps(records, indent=2))
        else:
            width = max(len(r["path"]) for r in records)
            print(f"{'PATH'.ljust(width)}  {'PROJECT':<20} {'RULE KIND':<17} WINNING RULE")
            print("-" * (width + 60))
            for r in records:
                proj = r["project"] or "UNCLASSIFIED"
                print(
                    f"{r['path'].ljust(width)}  {proj:<20} "
                    f"{r['rule_kind']:<17} {r['winning_rule']}"
                )
                print(f"{' ' * width}    directives: {', '.join(r['directives'])}")
                if r["declared_overlap"]:
                    print(f"{' ' * width}    declared overlap: {r['declared_overlap']} "
                          f"({', '.join(r['tied_projects'])})")
        return 0

    checker.run(changed, added, body, today)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "affected_projects": checker.affected,
                    "findings": [f.__dict__ for f in checker.findings],
                },
                indent=2,
            )
        )
    else:
        if checker.affected:
            print("Affected projects: " + ", ".join(checker.affected))
        for f in checker.findings:
            print(f.render())
        if not checker.findings:
            print("architecture governance: OK")

    return 1 if checker.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
