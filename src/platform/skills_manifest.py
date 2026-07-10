"""skills_manifest — deploy-time SKILL.md content-integrity manifest for project SKILLs.

Closes a live gap in the deploy pipeline: the required-SKILL *presence* gate in
shift-agent-deploy.sh verifies a SKILL.md FILE exists but never verifies its
CONTENT. A self-writing Hermes ("smarter memory edits" / curator auto-consolidation)
or an in-place SKILL.md rewrite passes the presence gate silently — the exact
2026-05-05 curator-umbrella regression class. This module computes a sha256
manifest over every shipped SKILL.md and lets the deploy gate (fail-closed) and a
between-deploy watchdog (alert-only) compare the live /root/.hermes/skills/ tree
against it.

Design mirrors the existing gate stack (check-shift-agent-patch.sh sha256 pin +
check-hermes-config-yaml.sh three-layer bash/python/baseline). **stdlib-only** so
the logic is importable pre-install and its unit tests run cross-platform (the
subprocess suites skip on Windows via fcntl; this one does not).

SCOPE / THREAT MODEL (deliberately honest — see the PR's "Threat model & limitations"):
this detects **benign** drift — an autonomous curator / self-improver or an accidental
in-place edit mutating a FLAT SKILL.md, plus deletion of a critical skill. It is NOT
adversary-resistant. The D2 watchdog runs as the same `shift-agent` uid as the Hermes
gateway (hermes-gateway.service: User=shift-agent, ReadWritePaths=/opt/shift-agent
/root/.hermes), so a *compromised* gateway can poison the watchdog's manifest / throttle /
.env and evade it. The root-run D1 deploy gate is trustworthy but at deploy largely
re-asserts the content rsync just wrote (real between-deploy protection is D2). Known
residual evasions (namespaced-skill writes, non-SKILL.md files inside a skill dir) are
disclosed in the PR, not silently unhandled.

Two consumers, two safety postures:
  * verify()  — DEPLOY GATE, fail-closed. Only checks manifest skills that are
    PRESENT on-box (conditional-deploy safe; foundation skills are namespaced and
    absent from the manifest, so they're never touched).
  * audit()   — WATCHDOG, alert-only. Flags content changes AND extra flat skill
    dirs (curator umbrellas / autonomous writes), never blocks a deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Repo-relative defaults (…/src/platform/skills_manifest.py).
_SRC_DIR = Path(__file__).resolve().parents[1]          # …/src
_REPO_ROOT = _SRC_DIR.parent                            # repo root
_DEFAULT_AGENTS_ROOT = _SRC_DIR / "agents"
_DEFAULT_BASELINE = _REPO_ROOT / "tools" / "skills-manifest.txt"

_MANIFEST_HEADER = (
    "# tools/skills-manifest.txt — content-integrity baseline for shipped project SKILLs.\n"
    "# One line per skill:  <sha256(SKILL.md)>  <skill-dir-name>  (sorted by name).\n"
    "# Regenerate after editing ANY SKILL.md:  tools/check-skills-manifest.sh build\n"
    "# Enforced fail-closed at deploy (content) + alert-only by shift-agent-skills-audit.\n"
    "# DO NOT hand-edit hashes — a mismatch fail-closes the deploy.\n"
)


class DuplicateSkillError(Exception):
    """Two shipped SKILL.md files flatten to the same skill-dir name with DIFFERENT
    content. On-box they'd land in the same /root/.hermes/skills/<name>/ and the last
    rsync would silently win — an ambiguous, unshippable state. Fail the build."""


class InvalidSkillNameError(Exception):
    """A skill directory name containing whitespace/newline. The manifest is line-oriented
    (`<sha256>  <name>`) and parse_manifest splits on whitespace, so such a name would
    truncate on round-trip and mis-key the manifest — verify() would then compare the wrong
    key, find it 'absent', and report clean: a SILENT gate bypass (the exact failure class
    this module exists to prevent). Reject at build time."""


def skill_sha256(skill_md_path: Path) -> str:
    """sha256 hex digest of a SKILL.md file's raw bytes."""
    return hashlib.sha256(Path(skill_md_path).read_bytes()).hexdigest()


def build_manifest(agents_root: Path) -> dict[str, str]:
    """Scan <agents_root>/*/skills/*/SKILL.md and return {skill_dir_name: sha256}.

    Keyed by the skill directory name because rsync flattens every agent's
    skills/ INTO the shared /root/.hermes/skills/ — the on-box namespace is flat.
    Raises DuplicateSkillError on a same-name/different-content collision.
    """
    agents_root = Path(agents_root)
    manifest: dict[str, str] = {}
    sources: dict[str, Path] = {}
    for skill_md in sorted(agents_root.glob("*/skills/*/SKILL.md")):
        name = skill_md.parent.name
        if not name or any(c.isspace() for c in name):
            raise InvalidSkillNameError(
                f"skill dir name {name!r} ({skill_md}) is empty or contains whitespace; "
                f"the line-oriented manifest cannot represent it without silent truncation."
            )
        digest = skill_sha256(skill_md)
        if name in manifest and manifest[name] != digest:
            raise DuplicateSkillError(
                f"skill '{name}' shipped with differing content by "
                f"{sources[name]} and {skill_md} — flat-namespace collision "
                f"(last rsync would win). Rename one or reconcile the content."
            )
        manifest[name] = digest
        sources.setdefault(name, skill_md)
    return manifest


def format_manifest(manifest: dict[str, str]) -> str:
    """Deterministic text form: comment header + sorted '<sha256>  <name>' lines."""
    lines = [_MANIFEST_HEADER]
    for name in sorted(manifest):
        lines.append(f"{manifest[name]}  {name}")
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_manifest(text: str) -> dict[str, str]:
    """Parse manifest text, ignoring blank lines and '#' comments."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[1]
        out[name] = digest
    return out


def scan_live_skills(skills_root: Path) -> dict[str, str]:
    """Return {name: sha256} for every FLAT skill dir directly under skills_root
    that contains a SKILL.md. Namespaced foundation skills (e.g. productivity/maps —
    where productivity/ has no direct SKILL.md) are excluded by construction, so the
    gate never trips on bundled Hermes skills we don't ship."""
    skills_root = Path(skills_root)
    if not skills_root.is_dir():
        return {}
    out: dict[str, str] = {}
    for child in sorted(skills_root.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file():
            out[child.name] = skill_sha256(skill_md)
    return out


@dataclass
class VerifyResult:
    """Deploy-gate result. `changed` = manifest skills present on-box whose content
    no longer matches. Absent-on-box manifest entries and extra live skills are
    intentionally ignored here (presence gate + watchdog own those)."""
    changed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.changed


def verify(manifest: dict[str, str], live: dict[str, str]) -> VerifyResult:
    changed = [
        name for name, digest in manifest.items()
        if name in live and live[name] != digest
    ]
    return VerifyResult(changed=sorted(changed))


@dataclass
class AuditResult:
    """Watchdog result. `changed` = in-place content mods; `extra` = flat skill dirs
    not in the manifest and not foundation-allowlisted (curator umbrella / autonomous
    write); `missing` = manifest skills absent on-box (informational — normal for a
    disabled agent); `missing_required` = the subset of `missing` that is CRITICAL (must
    always be present, e.g. dispatch_shift_agent) — its deletion is the 2026-05-05
    dispatcher-silence failure mode, so it DOES make the result unclean."""
    changed: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.changed or self.extra or self.missing_required)


def audit(
    manifest: dict[str, str],
    live: dict[str, str],
    foundation: "frozenset[str] | set[str]" = frozenset(),
    required: "frozenset[str] | set[str]" = frozenset(),
) -> AuditResult:
    changed = [n for n, d in manifest.items() if n in live and live[n] != d]
    extra = [n for n in live if n not in manifest and n not in foundation]
    missing = [n for n in manifest if n not in live]
    missing_required = [n for n in missing if n in required]
    return AuditResult(
        changed=sorted(changed), extra=sorted(extra), missing=sorted(missing),
        missing_required=sorted(missing_required),
    )


# ── CLI (build / verify / audit) — consumed by the bash wrappers ──────────────
# verify/audit emit JSON on stdout AND human text on stderr, and set the process
# exit code, mirroring src/platform/check_hermes_config_yaml.py's single-invocation
# JSON+text contract (no TOCTOU re-read).

def _load_name_set(path: str | None) -> set[str]:
    """Load a set of skill names from a file (one per line; blanks + '#'-comments
    ignored). Missing file → empty set. Used for both the foundation allowlist and the
    critical-required list."""
    if not path:
        return set()
    p = Path(path)
    if not p.is_file():
        return set()
    return {
        ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        manifest = build_manifest(Path(args.agents_root))
    except (DuplicateSkillError, InvalidSkillNameError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    text = format_manifest(manifest)
    if args.check:
        current = Path(args.check)
        if not current.is_file():
            print(f"FAIL: baseline {args.check} missing — run build mode + commit", file=sys.stderr)
            return 1
        committed = parse_manifest(current.read_text(encoding="utf-8"))
        if committed != manifest:
            only_src = sorted(set(manifest) - set(committed))
            only_base = sorted(set(committed) - set(manifest))
            changed = sorted(n for n in manifest if n in committed and committed[n] != manifest[n])
            print("FAIL: tools/skills-manifest.txt is STALE vs src/agents.", file=sys.stderr)
            if only_src:
                print(f"  new/undocumented skills: {', '.join(only_src)}", file=sys.stderr)
            if only_base:
                print(f"  removed skills still in baseline: {', '.join(only_base)}", file=sys.stderr)
            if changed:
                print(f"  content changed: {', '.join(changed)}", file=sys.stderr)
            print("  Fix: tools/check-skills-manifest.sh build  (then commit).", file=sys.stderr)
            return 1
        print("OK: skills-manifest.txt matches src/agents.", file=sys.stderr)
        return 0
    if args.out and args.out != "-":
        if not manifest:
            print(f"FAIL: refusing to write an EMPTY manifest to {args.out} "
                  f"(wrong --agents-root {args.agents_root}?)", file=sys.stderr)
            return 1
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(manifest)} skills)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        payload = {"exit_code": 2, "error": f"manifest not found: {args.manifest}", "changed": []}
        print(json.dumps(payload))
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    live = scan_live_skills(Path(args.skills_root))
    r = verify(manifest, live)
    code = 0 if r.ok else 1
    print(json.dumps({"exit_code": code, "changed": r.changed}))
    if r.ok:
        print(f"OK: all present project SKILLs match manifest ({len(manifest)} pinned).", file=sys.stderr)
    else:
        print("FAIL: SKILL.md content drift from shipped manifest:", file=sys.stderr)
        for n in r.changed:
            print(f"  - {n} (live sha256 != manifest)", file=sys.stderr)
        print("  A deployed SKILL was modified on-box (self-writing Hermes / manual edit).", file=sys.stderr)
    return code


def _cmd_audit(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(json.dumps({"exit_code": 2, "error": "manifest not found",
                          "changed": [], "extra": [], "missing": [], "missing_required": []}))
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    live = scan_live_skills(Path(args.skills_root))
    r = audit(
        manifest, live,
        foundation=_load_name_set(args.foundation),
        required=_load_name_set(args.required),
    )
    code = 0 if r.clean else 1
    print(json.dumps({
        "exit_code": code, "changed": r.changed, "extra": r.extra,
        "missing": r.missing, "missing_required": r.missing_required,
    }))
    if r.clean:
        print("OK: skills dir matches manifest (no changed/extra/missing-critical).", file=sys.stderr)
    else:
        if r.changed:
            print(f"CHANGED (in-place SKILL.md edit): {', '.join(r.changed)}", file=sys.stderr)
        if r.extra:
            print(f"EXTRA (unknown skill dir — curator umbrella / autonomous write): {', '.join(r.extra)}", file=sys.stderr)
        if r.missing_required:
            print(f"MISSING-CRITICAL (deleted required skill — dispatcher-silence class): {', '.join(r.missing_required)}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skills-manifest", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="build manifest from src/agents")
    p_build.add_argument("--agents-root", default=str(_DEFAULT_AGENTS_ROOT))
    p_build.add_argument("--out", default="-", help="output file, or '-' for stdout")
    p_build.add_argument("--check", default=None,
                         help="compare fresh build to this baseline; exit 1 if stale")
    p_build.set_defaults(func=_cmd_build)

    p_verify = sub.add_parser("verify", help="fail-closed deploy gate (content check)")
    p_verify.add_argument("--manifest", default=str(_DEFAULT_BASELINE))
    p_verify.add_argument("--skills-root", default="/root/.hermes/skills")
    p_verify.set_defaults(func=_cmd_verify)

    p_audit = sub.add_parser("audit", help="alert-only watchdog (changed + extra)")
    p_audit.add_argument("--manifest", default=str(_DEFAULT_BASELINE))
    p_audit.add_argument("--skills-root", default="/root/.hermes/skills")
    p_audit.add_argument("--foundation", default=None,
                         help="optional flat foundation-skill allowlist file")
    p_audit.add_argument("--required", default=None,
                         help="optional critical-skill list; a missing entry alerts (deletion guard)")
    p_audit.set_defaults(func=_cmd_audit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
