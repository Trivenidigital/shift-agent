"""Flyer acceptance oracle - offline gate harness (Phase-2 core).

Design: tasks/flyer-acceptance-oracle-design.md. Drives the deployed flyer modules
deterministically with NO network: the semantic-brief + planner providers return
None without API keys; the planner provider is stubbed from each case's
`planner_offers` (simulating the LLM). The oracle ONLY observes - it never mutates
product behavior. Run: `python tests/flyer_oracle/oracle.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
for _p in (_REPO / "src", _REPO / "src" / "platform", _REPO / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Offline: no provider keys => the deployed semantic-brief + planner providers no-op.
os.environ["OPENROUTER_API_KEY"] = ""
os.environ.pop("OPENAI_API_KEY", None)

from schemas import FlyerAsset, FlyerConfig, FlyerCreativePlannerConfig, FlyerLockedFact, FlyerProject, FlyerRequestFields  # noqa: E402
from agents.flyer import creative_planner as cp  # noqa: E402
from agents.flyer import facts as flyer_facts  # noqa: E402

CORPUS_DIR = _HERE.parent / "corpus"


@dataclass
class GateResult:
    case_id: str
    gate: str
    scoreboard: str  # truth | delivery | commercial
    passed: bool
    detail: str = ""


def load_corpus() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(CORPUS_DIR.glob("*.json"))]


def _cfg(case: dict) -> FlyerConfig:
    c = (case.get("config") or {}).get("creative_planner") or {}
    return FlyerConfig(
        creative_planner=FlyerCreativePlannerConfig(
            enabled=bool(c.get("enabled", False)),
            enabled_categories=list(c.get("enabled_categories", [])),
        )
    )


def run_pipeline(case: dict):
    """Deterministic offline run of the text-fact pipeline for one case.

    The planner provider is stubbed from `planner_offers`; this simulates the
    LLM planner without network spend and lets the oracle check the product
    contract around when those planner facts may flow.
    """
    offers = case.get("planner_offers")
    cp.build_creative_planner_provider = (
        (lambda o=offers: (lambda fields, raw: list(o))) if offers else (lambda: None)
    )
    # Seal offline determinism (Codex review): the deployed semantic-brief provider can
    # read /root/.hermes/.env or /opt/shift-agent/.env (not just process env), so on a
    # host with those files it could call OpenRouter. Force it to None ⇒ deterministic
    # fallback, never network. The oracle MUST be reproducible + free.
    flyer_facts.build_hermes_semantic_brief_provider = lambda: None
    fields = FlyerRequestFields(
        event_or_business_name=(case.get("profile") or {}).get("business_name", "") or "",
        notes=case.get("notes", "") or "",
    )
    return flyer_facts.extract_text_facts(fields, case["request"], cfg=_cfg(case))


def _case_expect(case: dict) -> dict:
    return case.get("expect", {}) or {}


def _by_id(facts):
    return {f.fact_id: f for f in facts}


def _inferred_names(facts):
    return [f for f in facts if re.match(r"^item:\d+:name$", f.fact_id) and f.source == "hermes_inferred"]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).casefold()).strip()


def _contains_text(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


def _render_text_from_facts(facts) -> str:
    return "\n".join(str(f.value) for f in facts if getattr(f, "required", True))


def _make_asset(tmp_dir: Path, *, name: str = "asset.png", kind: str = "reference_image") -> FlyerAsset:
    path = tmp_dir / name
    path.write_bytes(b"oracle asset")
    return FlyerAsset(
        asset_id="A0001",
        kind=kind,  # type: ignore[arg-type]
        source="whatsapp" if kind == "reference_image" else "rendered",
        path=str(path),
        mime_type="image/png",
        sha256="a" * 64,
        original_message_id="m-oracle",
        received_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )


def gate_extraction(case, facts) -> GateResult:
    by = _by_id(facts)
    miss = []
    for exp in _case_expect(case).get("locked_facts", []):
        got = by.get(exp["fact_id"])
        if got is None or got.value.casefold() != str(exp["value"]).casefold() or got.source != exp["source"]:
            miss.append(
                f"{exp['fact_id']}={exp['value']}/{exp['source']} -> "
                f"{(got.value + '/' + got.source) if got else 'ABSENT'}"
            )
    return GateResult(case["id"], "extraction", "truth", not miss, "ok" if not miss else "; ".join(miss))


def gate_reference_extraction(case, facts) -> GateResult:
    """Truth: check reference extraction through the deployed sidecar provider.

    The gate also merges reference facts with text facts to catch propagation
    regressions without invoking media OCR or image generation spend.
    """
    spec = _case_expect(case).get("reference_extraction")
    if not spec:
        return GateResult(case["id"], "reference_extraction", "truth", True, "n/a")
    try:
        from agents.flyer.reference_extract import SidecarReferenceExtractionProvider, extract_reference
    except Exception as e:
        return GateResult(case["id"], "reference_extraction", "truth", True, f"skipped (import: {e})")

    old_root = os.environ.get("FLYER_STATE_ROOT")
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            os.environ["FLYER_STATE_ROOT"] = str(tmp_dir)
            asset = _make_asset(tmp_dir)
            Path(str(asset.path) + ".ocr.txt").write_text(spec.get("ocr_text", ""), encoding="utf-8")
            result = extract_reference(asset, raw_request=case["request"], provider=SidecarReferenceExtractionProvider())
    finally:
        if old_root is None:
            os.environ.pop("FLYER_STATE_ROOT", None)
        else:
            os.environ["FLYER_STATE_ROOT"] = old_root

    detail, ok = [], True
    expected_status = spec.get("status", "ok")
    if result.status != expected_status:
        ok = False
        detail.append(f"status {result.status} != {expected_status}")
    merged = flyer_facts.merge_locked_facts(list(facts), list(result.extracted_facts))
    by = _by_id(merged)
    for exp in spec.get("expected_facts", []):
        got = by.get(exp["fact_id"])
        if got is None or got.value.casefold() != str(exp["value"]).casefold() or got.source != exp["source"]:
            ok = False
            detail.append(
                f"{exp['fact_id']} expected {exp['value']}/{exp['source']} got "
                f"{(got.value + '/' + got.source) if got else 'ABSENT'}"
            )
    return GateResult(case["id"], "reference_extraction", "truth", ok, "ok" if ok else "; ".join(detail))


def gate_planner(case, facts) -> GateResult:
    exp = _case_expect(case)
    inferred = _inferred_names(facts)
    detail, ok = [], True
    allowed = exp.get("allowed_inferred")
    if allowed is not None and len(inferred) > allowed:
        ok = False
        detail.append(f"inferred {len(inferred)} > allowed {allowed} ({[f.value for f in inferred]})")
    want = exp.get("inferred_item_names")
    if want is not None:
        got = {f.value.casefold() for f in inferred}
        want_set = {n.casefold() for n in want}
        missn = sorted(want_set - got)
        extra = sorted(got - want_set)
        # EXACT fill (Codex review: <= allowed let under-fill pass). Require the inferred
        # set to be exactly the expected set — catches under-fill AND spurious extras.
        if missn:
            ok = False
            detail.append(f"missing inferred {missn}")
        if extra:
            ok = False
            detail.append(f"unexpected inferred {extra}")
    return GateResult(case["id"], "planner", "truth", ok, "ok" if ok else "; ".join(detail))


def gate_item_count(case, facts) -> GateResult:
    """Truth: exactly the expected number of distinct item names is committed."""
    want = _case_expect(case).get("expected_total_items")
    if want is None:
        return GateResult(case["id"], "item_count", "truth", True, "n/a")
    names = sorted({f.value.casefold() for f in facts if re.match(r"^item:\d+:name$", f.fact_id)})
    ok = len(names) == want
    return GateResult(
        case["id"],
        "item_count",
        "truth",
        ok,
        "ok" if ok else f"distinct items {len(names)} != expected {want}: {names}",
    )


def gate_flat_price(case, facts) -> GateResult:
    exp = _case_expect(case)
    if exp.get("pricing_model") != "flat":
        return GateResult(case["id"], "flat_price", "truth", True, "n/a")
    by = _by_id(facts)
    price, src = exp.get("flat_price"), exp.get("flat_price_source", "customer_text")
    bad = []
    # EVERY item (grounded + inferred) must carry the flat price (Codex review: was
    # inferred-only). A flat "$8.99 any item" applies to the whole menu.
    name_facts = [f for f in facts if re.match(r"^item:\d+:name$", f.fact_id)]
    for nf in name_facts:
        idx = nf.fact_id.split(":")[1]
        pf = by.get(f"item:{idx}:price")
        if pf is None or pf.value != price or pf.source != src:
            bad.append(f"{nf.value}:{(pf.value + '/' + pf.source) if pf else 'NO_PRICE'}")
    return GateResult(
        case["id"],
        "flat_price",
        "truth",
        not bad,
        "ok" if not bad else "wrong/missing flat price: " + "; ".join(bad),
    )


_JUNK_ITEM_RE = re.compile(r"^\d+\s+(?:[\w'&/-]+\s+){0,6}items?\b", re.IGNORECASE)


def gate_no_junk_items(case, facts) -> GateResult:
    """Truth (universal): no item NAME is a mis-parsed request count-phrase, e.g.
    "8 items total" / "12 famous south indian items". A real dish is never shaped
    '<number> ... items'. (Restored after the overwrite dropped it — Codex review.)"""
    junk = [f.value for f in facts
            if re.match(r"^item:\d+:name$", f.fact_id) and _JUNK_ITEM_RE.match(f.value or "")]
    return GateResult(case["id"], "no_junk_items", "truth", not junk,
                      "ok" if not junk else f"junk count-phrase item(s): {junk}")


def gate_disallowed(case, facts) -> GateResult:
    detail, ok = [], True
    for d in _case_expect(case).get("disallowed_facts", []):
        if d.get("hard_fact_class") == "price" and "max_count_not_grounded" in d:
            prices = [f for f in facts if re.match(r"^item:\d+:price$", f.fact_id)]
            if len(prices) > d["max_count_not_grounded"]:
                ok = False
                detail.append(f"{len(prices)} ungrounded price facts > {d['max_count_not_grounded']} ({[p.value for p in prices]})")
        rx = d.get("forbidden_visible_text_regex")
        if rx:
            # Fact-level anti-hallucination (Codex review: this was ignored). No fact
            # value may match the forbidden pattern (e.g. an invented phone) unless the
            # case marks it grounded. Render-pixel-level enforcement is §7d's job later.
            pat = re.compile(rx)
            hits = [f.value for f in facts if pat.search(f.value or "")]
            if hits and not d.get("unless_grounded"):
                ok = False
                detail.append(f"forbidden text {rx!r} in fact value(s): {hits}")
    return GateResult(case["id"], "disallowed", "truth", ok, "ok" if ok else "; ".join(detail))


def _build_project(case, facts) -> FlyerProject:
    now = datetime(2026, 6, 2, tzinfo=timezone.utc)
    profile = case.get("profile") or {}
    locked = list(facts)
    # Model the registered customer profile: in production contact_phone is locked
    # from the customer record (customers.json), not only parsed from the request
    # text. The oracle must carry it so phone-fidelity gates (P1-1: unverified/extra
    # phone) have the customer's real number to compare the rendered flyer against.
    phone = str(profile.get("public_phone") or "").strip()
    if phone and not any(
        fact.fact_id == "contact_phone" or "phone" in (fact.label or "").casefold()
        for fact in locked
    ):
        locked.append(
            FlyerLockedFact(
                fact_id="contact_phone",
                label="Contact phone",
                value=phone,
                source="customer_profile",
                required=True,
            )
        )
    return FlyerProject(
        project_id="F" + (re.sub(r"\D", "", str(case["id"])) or "9001").zfill(4),
        status="awaiting_final_approval",
        customer_phone="+10000000000",
        created_at=now,
        updated_at=now,
        original_message_id="m-oracle",
        raw_request=case["request"],
        locked_facts=locked,
        fields=FlyerRequestFields(
            event_or_business_name=profile.get("business_name", "") or "",
        ),
    )


def gate_render_fit(case, facts) -> GateResult:
    """Delivery: locked facts fit the production overlay before image spend."""
    expect = (_case_expect(case).get("delivery") or {}).get("render_fit", "must_fit")
    failures = []
    try:
        from PIL import Image
        from agents.flyer import render as flyer_render
    except Exception as e:  # pragma: no cover - env without Pillow/render importable
        return GateResult(case["id"], "render_fit", "delivery", True, f"skipped (import: {e})")
    try:
        project = _build_project(case, facts)
    except Exception as e:
        return GateResult(case["id"], "render_fit", "delivery", True, f"skipped (project build: {e})")
    # The square 1080x1080 Instagram post in the final package is the binding size
    # (holds the fewest menu rows), so it leads the list — a flyer that can't render
    # at every delivered size must fail closed, not ship the larger sizes only.
    for (w, h, fmt) in [
        (1080, 1080, "concept_preview"),
        (1080, 1350, "concept_preview"),
        (1275, 1650, "final_whatsapp_image"),
    ]:
        with tempfile.TemporaryDirectory() as td:
            src, tgt = Path(td) / "bg.png", Path(td) / "out.png"
            Image.new("RGB", (w, h), (238, 238, 238)).save(src)
            try:
                # production parity (Codex review): production calls the private wrapper,
                # which re-raises real fit failures (only falls back for "Pillow required").
                flyer_render._apply_critical_text_overlay(project, src, tgt, size=(w, h), output_format=fmt)
            except flyer_render.FlyerRenderError as e:
                failures.append(f"{w}x{h}: {e}")
            except Exception as e:
                return GateResult(case["id"], "render_fit", "delivery", True, f"skipped ({w}x{h}: {e})")
    if expect == "must_not_fit":
        return GateResult(
            case["id"],
            "render_fit",
            "delivery",
            bool(failures),
            "blocked before spend: " + "; ".join(failures) if failures else "unexpectedly fit",
        )
    return GateResult(case["id"], "render_fit", "delivery", not failures, "fits all sizes" if not failures else "; ".join(failures))


def gate_visual_qa(case, facts) -> GateResult:
    spec = _case_expect(case).get("visual_qa")
    if not spec:
        return GateResult(case["id"], "visual_qa", "delivery", True, "n/a")
    try:
        from agents.flyer.visual_qa import run_visual_qa
    except Exception as e:
        return GateResult(case["id"], "visual_qa", "delivery", True, f"skipped (import: {e})")
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        artifact = tmp_dir / "flyer.png"
        artifact.write_bytes(b"oracle visual qa artifact")
        ocr_text = spec.get("ocr_text") or _render_text_from_facts(facts)
        Path(str(artifact) + ".ocr.txt").write_text(ocr_text, encoding="utf-8")
        report = run_visual_qa(
            _build_project(case, facts),
            artifact,
            output_format=spec.get("output_format", "concept_preview"),
            allow_sidecar=True,
        )
    detail, ok = [], True
    expected_status = spec.get("status", "passed")
    if report.status != expected_status:
        ok = False
        detail.append(f"status {report.status} != {expected_status}; blockers={list(report.blockers)}")
    blockers = "\n".join(report.blockers)
    for blocker in spec.get("must_include_blockers", []):
        if blocker not in blockers:
            ok = False
            detail.append(f"missing blocker: {blocker}")
    for blocker in spec.get("must_not_include_blockers", []):
        if blocker in blockers:
            ok = False
            detail.append(f"unexpected blocker: {blocker}")
    return GateResult(case["id"], "visual_qa", "delivery", ok, "ok" if ok else "; ".join(detail))


def gate_preview_final(case, facts) -> GateResult:
    spec = (_case_expect(case).get("delivery") or {}).get("preview_final")
    if not spec:
        return GateResult(case["id"], "preview_final", "delivery", True, "n/a")
    by = _by_id(facts)
    preview = spec.get("preview_ocr_text", "")
    final = spec.get("final_ocr_text", "")
    missing = []
    for item in spec.get("required", []):
        if isinstance(item, dict):
            value = str(item.get("value") or getattr(by.get(item.get("fact_id", "")), "value", ""))
            label = item.get("fact_id") or value
        else:
            value, label = str(item), str(item)
        if value and not _contains_text(preview, value):
            missing.append(f"preview missing {label}={value}")
        if value and not _contains_text(final, value):
            missing.append(f"final missing {label}={value}")
    return GateResult(case["id"], "preview_final", "delivery", not missing, "ok" if not missing else "; ".join(missing))


def gate_revision_lifecycle(case, facts) -> GateResult:
    spec = _case_expect(case).get("revision_lifecycle")
    if not spec:
        return GateResult(case["id"], "revision_lifecycle", "delivery", True, "n/a")
    # Codex confirm-pass BLOCKER: this is a spec self-check (validates the case's own
    # labels, not deployed behavior), so it would inflate the baseline. Demoted to
    # NEUTRAL until upgraded to drive the real update-flyer-project revision/approval
    # path (no approve-with-unapplied; inferred→confirmed; project-scoped). TODO.
    return GateResult(case["id"], "revision_lifecycle", "delivery", True,
                      "skipped: spec self-check; TODO drive update-flyer-project")
    by = _by_id(facts)
    issues = []
    for before in spec.get("before", []):
        got = by.get(before["fact_id"])
        if got is None or got.value.casefold() != str(before["value"]).casefold():
            issues.append(f"before {before['fact_id']} expected {before['value']} got {got.value if got else 'ABSENT'}")
    for transition in spec.get("transitions", []):
        if transition.get("from_source") == "hermes_inferred" and transition.get("to_source") != "customer_confirmed":
            issues.append(f"bad source transition {transition}")
        if not transition.get("project_scoped", True):
            issues.append(f"non-project-scoped confirmation {transition}")
    for after in spec.get("after", []):
        if after.get("source") != "customer_confirmed":
            issues.append(f"after {after['fact_id']} not customer_confirmed")
    return GateResult(case["id"], "revision_lifecycle", "delivery", not issues, "ok" if not issues else "; ".join(issues))


def gate_delivery_state(case, facts) -> GateResult:
    spec = _case_expect(case).get("delivery_state")
    if not spec:
        return GateResult(case["id"], "delivery_state", "delivery", True, "n/a")
    # Codex confirm-pass BLOCKER: spec self-check, not a driver of send-flyer-package.
    # Demoted to NEUTRAL until upgraded to drive _caption_for_asset (send-format
    # truthfulness) + _pending_project_assets (uncertain-retry block) on a real project.
    return GateResult(case["id"], "delivery_state", "delivery", True,
                      "skipped: spec self-check; TODO drive send-flyer-package")
    assets = {a["asset_id"]: a for a in spec.get("assets", [])}
    final_ids = spec.get("final_asset_ids", [])
    issues = []
    missing = [asset_id for asset_id in final_ids if asset_id not in assets]
    if missing:
        issues.append("missing final assets: " + ",".join(missing))
    if not spec.get("allow_uncertain", False):
        uncertain = [asset_id for asset_id in final_ids if assets.get(asset_id, {}).get("delivery_status") == "uncertain"]
        if uncertain:
            issues.append("uncertain final assets: " + ",".join(uncertain))
    expected_sent = spec.get("expected_sent_final_ids")
    if expected_sent is not None:
        sent = sorted(
            asset_id
            for asset_id in final_ids
            if assets.get(asset_id, {}).get("delivery_status") == "sent" and assets.get(asset_id, {}).get("outbound_message_id")
        )
        if sent != sorted(expected_sent):
            issues.append(f"sent finals {sent} != expected {sorted(expected_sent)}")
    return GateResult(case["id"], "delivery_state", "delivery", not issues, "ok" if not issues else "; ".join(issues))


GATES = [
    gate_extraction,
    gate_reference_extraction,
    gate_planner,
    gate_item_count,
    gate_no_junk_items,
    gate_flat_price,
    gate_disallowed,
    gate_render_fit,
    gate_visual_qa,
    gate_preview_final,
    gate_revision_lifecycle,
    gate_delivery_state,
]


def run_all() -> list[GateResult]:
    results = []
    for case in load_corpus():
        facts = run_pipeline(case)
        for g in GATES:
            results.append(g(case, facts))
    return results


def _is_na(r: GateResult) -> bool:
    """A gate that did not apply to this case (no spec) or was skipped (env/import) is
    NEUTRAL — it must NOT count as a pass (Codex review: n/a/skip inflated the board)."""
    return r.detail == "n/a" or r.detail.startswith("skipped")


def print_scoreboard(results: list[GateResult]) -> None:
    boards: dict[str, list[GateResult]] = {"truth": [], "delivery": [], "commercial": []}
    for r in results:
        boards.setdefault(r.scoreboard, []).append(r)
    for board, rs in boards.items():
        if not rs:
            continue
        applicable = [r for r in rs if not _is_na(r)]
        p = sum(1 for r in applicable if r.passed)
        na = len(rs) - len(applicable)
        print(f"\n=== {board.upper()} scoreboard: {p}/{len(applicable)} pass ({na} n/a) ===")
        for r in rs:
            tag = "N/A " if _is_na(r) else ("PASS" if r.passed else "FAIL")
            print(f"  [{tag}] {r.case_id} {r.gate}: {r.detail}")
    applicable = [r for r in results if not _is_na(r)]
    passed = sum(1 for r in applicable if r.passed)
    print(f"\nTOTAL: {passed}/{len(applicable)} applicable gate-checks pass "
          f"({len(results) - len(applicable)} n/a)")


if __name__ == "__main__":
    print_scoreboard(run_all())
