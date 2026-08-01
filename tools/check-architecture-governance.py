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

REUSE_MAP_FIELDS = (
    "Requested outcome",
    "Affected projects",
    "Applicable directives",
    "Existing platform/model capabilities reused",
    "Existing deterministic kernels reused",
    "Existing stores/workflows reused",
    "Thin adapters",
    "New subsystem",
    "Evidence existing capabilities were insufficient",
    "Architecture exception",
    "Shared-platform impact",
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

EMPTY_VALUES = {"", "none", "n/a", "na", "-", "tbd", "todo"}

# Cursor rule stem → registry project id. A stem absent here must equal an id.
CURSOR_RULE_ALIASES = {"shared-platform-directive": "shift-platform"}
CURSOR_UNIVERSAL_RULE = "engineering-directive"

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

    @classmethod
    def _field_value(cls, body: str, label: str) -> Optional[str]:
        m = re.search(
            rf"^\s*[-*]?\s*{re.escape(label)}\s*:\s*(.*)$",
            cls._deemphasize(body),
            re.MULTILINE | re.IGNORECASE,
        )
        if m is None:
            return None
        return m.group(1).strip()

    def check_pr_body(self, body: str, changed: list[str], today: _dt.date) -> None:
        if REUSE_MAP_HEADING not in self._deemphasize(body):
            self.add(
                "GOV-PR-NOMAP",
                f"PR body has no `{REUSE_MAP_HEADING}` section — a verbal reuse claim "
                "is insufficient",
            )
            return

        for label in REUSE_MAP_FIELDS:
            if self._field_value(body, label) is None:
                self.add("GOV-PR-FIELD", f"Capability Reuse Map is missing the `{label}:` field")

        for pid in self.affected:
            if pid not in body:
                self.add(
                    "GOV-PR-PROJECT",
                    f"changed files affect project `{pid}` but the Capability Reuse Map "
                    "does not name it",
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
            value = (self._field_value(body, "Shared-platform impact") or "").lower()
            if value in EMPTY_VALUES:
                self.add(
                    "GOV-PR-SHARED",
                    f"`{pid}` is shared platform and its runtime changed — "
                    "`Shared-platform impact:` must describe the effect, not be empty",
                )
            dependents = [
                p.id for p in self.projects if pid in p.shared_dependencies
            ]
            # Look only in the fields that are supposed to carry the answer —
            # a project id mentioned incidentally elsewhere is not a declaration.
            declaration = " ".join(
                self._field_value(body, label) or ""
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
            for kind, rx in SUBSYSTEM_INDICATORS.items():
                if re.search(rx, path, re.IGNORECASE):
                    suspicious.append((path, kind))
                    break
        if not suspicious:
            return

        declared_new = (self._field_value(body, "New subsystem") or "").lower()
        extends = bool(EXTENDS_DECLARATION.search(body))
        cited = self._field_value(body, "Architecture exception") or ""
        eids = re.findall(r"\b([A-Z][A-Z0-9]*-EX-\d+)\b", cited)

        for path, kind in suspicious:
            if extends:
                continue
            if declared_new not in EMPTY_VALUES:
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
