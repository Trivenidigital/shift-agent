#!/usr/bin/env python3
"""Phase D offline prototype — flyer locked facts → GBP post + IG caption.

Reads a checked-in FIXTURE COPY of a real FlyerProject row (never the live
store) and emits two draft text files via pure template composition:

  <project_id>-gbp-post.txt    Google Business Profile post body (paste-ready)
  <project_id>-ig-caption.txt  Instagram caption (paste-ready)

Deterministic, offline, zero LLM calls. The copy contract is the Flyer
fact-safety bright line extended verbatim: every content word in the output
comes from the project's locked facts; the only non-fact text allowed is the
ALLOWED_CONNECTIVES vocabulary below, authored together with its
FORBIDDEN_SUBSTRINGS lists (leak law: a caption vocabulary ships WITH its
forbidden-substrings at authoring time, never later).

`screen_draft` enforces the contract mechanically (residue check): strip every
fact value and fact-derived hashtag slug from the output, then require every
remaining word to be an authored connective. A future LLM upgrade slots in as
an alternative composer BEHIND the same screen — LLM output that fails
`screen_draft` is discarded in favor of this deterministic template.

Spec: tasks/phase-d-flyer-to-gbp-spec.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# GBP post bodies are capped at 1500 chars by Google; IG captions at 2200.
GBP_POST_MAX_CHARS = 1500
IG_CAPTION_MAX_CHARS = 2200

# ── Authored vocabulary (the ONLY non-fact words allowed in output) ──────────
# Lowercase word set for the residue check. Adding a word here is a copy-
# contract change and must be reviewed with the forbidden lists below.
ALLOWED_CONNECTIVES = frozenset({"at", "menu", "call"})

# Leak law list 1 — operator/internal jargon that must NEVER reach a customer-
# facing caption. Extends customer_copy_policy.BANNED_CUSTOMER_COPY_TERMS with
# social-draft-specific surfaces. Matched case-insensitively as substrings.
FORBIDDEN_SUBSTRINGS_JARGON = (
    "operator",
    "provider",
    "reason_code",
    "locked fact",
    "manual_edit",
    "queued project",
    "created flyer project",
    "source-preserving",
    "hermes",
    "pipeline",
    "kill switch",
    "render",
    "project f0",
)

# Leak law list 2 — unverified-claim vocabulary. These words assert things the
# locked facts do not; the deterministic template can never emit them, but the
# list is the authoring-time contract for the future LLM composer slot (its
# output is screened against this list AFTER fact-value stripping, so a fact
# value that legitimately contains one of these words is never a violation).
FORBIDDEN_SUBSTRINGS_CLAIMS = (
    "best",
    "authentic",
    "famous",
    "award",
    "#1",
    "no. 1",
    "guaranteed",
    "organic",
    "halal",
    "vegan",
    "gluten",
    "free delivery",
    "discount",
    "% off",
    "limited time",
    "today only",
    "while supplies last",
    "delicious",
    "fresh",
    "homemade",
    "secret recipe",
)


def _fact_map(row: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for fact in row.get("locked_facts") or []:
        fact_id = str(fact.get("fact_id") or "")
        value = " ".join(str(fact.get("value") or "").split())
        if fact_id and value and fact_id not in out:
            out[fact_id] = value
    return out


def _item_names(facts: dict[str, str]) -> list[str]:
    names: dict[int, str] = {}
    for fact_id, value in facts.items():
        match = re.fullmatch(r"item:(\d+):name", fact_id)
        if match:
            names[int(match.group(1))] = value
    return [names[index] for index in sorted(names)]


def hashtag_slug(value: str) -> str:
    """Deterministic fact-derived hashtag: title-case words, strip non-alnum.
    Apostrophes are removed before word-splitting so possessives stay one word
    ("Lakshmi's Kitchen" → LakshmisKitchen, not LakshmiSKitchen)."""
    words = re.split(r"[^0-9A-Za-z]+", re.sub(r"['’]", "", value))
    return "".join(word[:1].upper() + word[1:] for word in words if word)


def compose_gbp_post(row: dict) -> str:
    facts = _fact_map(row)
    lines: list[str] = []
    title = facts.get("campaign_title", "")
    business = facts.get("business_name", "")
    if title and business:
        lines.append(f"{title} at {business}")
    elif title or business:
        lines.append(title or business)
    if facts.get("pricing_structure"):
        lines.append("")
        lines.append(facts["pricing_structure"])
    items = _item_names(facts)
    if items:
        lines.append("Menu: " + ", ".join(items))
    tail = [facts.get(key, "") for key in ("schedule", "location")]
    tail = [value for value in tail if value]
    if tail:
        lines.append("")
        lines.extend(tail)
    if facts.get("promotion_end"):
        lines.append(facts["promotion_end"])
    if facts.get("contact_phone"):
        lines.append("Call " + facts["contact_phone"])
    return "\n".join(lines).strip() + "\n"


def compose_ig_caption(row: dict) -> str:
    facts = _fact_map(row)
    body = compose_gbp_post(row).rstrip("\n")
    tags = [
        "#" + hashtag_slug(facts[key])
        for key in ("business_name", "campaign_title")
        if facts.get(key) and hashtag_slug(facts[key])
    ]
    if tags:
        body += "\n\n" + " ".join(tags)
    return body + "\n"


def screen_draft(text: str, row: dict) -> list[str]:
    """Copy-contract screen. Returns a list of violations (empty = clean).

    1. Jargon check on the FULL text (internal terms never belong, even
       inside a fact value — a fact value containing 'operator' would itself
       be a poisoned fact and must block).
    2. Residue check: strip fact values + fact-derived hashtag slugs, then
       every remaining word must be in ALLOWED_CONNECTIVES.
    3. Claim check on the residue only (fact values are the licensed claims).
    """
    violations: list[str] = []
    lowered = text.lower()
    for term in FORBIDDEN_SUBSTRINGS_JARGON:
        if term in lowered:
            violations.append(f"jargon:{term}")

    facts = _fact_map(row)
    residue = text
    strippable = sorted(
        list(facts.values()) + [hashtag_slug(value) for value in facts.values()],
        key=len,
        reverse=True,
    )
    for value in strippable:
        if value:
            residue = re.sub(re.escape(value), " ", residue, flags=re.IGNORECASE)

    residue_lower = residue.lower()
    for term in FORBIDDEN_SUBSTRINGS_CLAIMS:
        if term in residue_lower:
            violations.append(f"claim:{term}")
    for word in re.findall(r"[a-z]+", residue_lower):
        if word not in ALLOWED_CONNECTIVES:
            violations.append(f"non_fact_word:{word}")
    return violations


def generate(fixture_path: Path, out_dir: Path) -> list[Path]:
    row = json.loads(fixture_path.read_text(encoding="utf-8"))
    project_id = str(row.get("project_id") or "")
    if not project_id:
        raise SystemExit(f"fixture has no project_id: {fixture_path}")
    if row.get("status") != "delivered":
        raise SystemExit(
            f"social drafts only compose for delivered projects, got {row.get('status')}"
        )
    drafts = {
        f"{project_id}-gbp-post.txt": (compose_gbp_post(row), GBP_POST_MAX_CHARS),
        f"{project_id}-ig-caption.txt": (compose_ig_caption(row), IG_CAPTION_MAX_CHARS),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, (text, max_chars) in drafts.items():
        violations = screen_draft(text, row)
        if violations:
            raise SystemExit(f"{name}: copy-contract violations: {violations}")
        if len(text) > max_chars:
            raise SystemExit(f"{name}: {len(text)} chars exceeds cap {max_chars}")
        path = out_dir / name
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="path to a project-row fixture json")
    parser.add_argument("--out", required=True, help="output directory for draft .txt files")
    args = parser.parse_args()
    for path in generate(Path(args.fixture), Path(args.out)):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
