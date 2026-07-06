#!/usr/bin/env python3
"""One-shot WS5 backfill: retroactive visual QA for an already-delivered printable_pdf.

F0203's printable_pdf was rendered and delivered before WS5 (rasterize-before-QA)
landed, so its vision-OCR QA never ran (the provider rejects application/pdf sent
as an image). This tool re-creates the PDF's PNG twin from whatever raster still
exists on disk, runs the standard visual-QA screens on it, and records the verdict.

Write discipline (read-only against live state):
  - The project store is READ under its lock and never written.
  - The finals directory is never written; the reconstructed twin and its QA
    report go to --work-dir (a fresh temp dir by default).
  - The ONLY live write is one `flyer_pdf_qa_backfill` audit row appended to
    decisions.log via the safe_io.ndjson_append chokepoint (same lock pattern as
    send-flyer-package's delivery audit).

Raster resolution order (first hit wins):
  1. --raster <path>            (explicit operator override)         exactness=explicit
  2. <pdf>.qapng.png            (post-WS5 generation-time twin)      exactness=exact
  3. selected concept preview   (closest upstream approved artifact) exactness=upstream_equivalent
If none exists, the run still records an audit row with qa_status="raster_missing".

STAGED INVOCATION (document only — NOT run by the PR that adds this tool; live
runs are operator- or main-session-executed per protocol, needs OPENROUTER_API_KEY
reachable via the usual env chokepoint):

    /usr/local/lib/hermes-agent/venv/bin/python \
        /opt/shift-agent/staging/tools/backfill-flyer-pdf-qa.py --project-id F0203

Exit codes: 0 = ran and recorded a verdict row (including raster_missing);
4 = project/asset not found; 5 = store unreadable.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(REPO_SRC / "platform"))
sys.path.insert(0, str(REPO_SRC))

from schemas import FlyerPdfQaBackfill, FlyerProjectStore  # noqa: E402
from safe_io import FileLock, ndjson_append  # noqa: E402
try:
    from flyer_render import pdf_png_twin_path  # type: ignore  # noqa: E402
except ImportError:
    from agents.flyer.render import pdf_png_twin_path  # noqa: E402
try:
    from flyer_visual_qa import run_visual_qa  # type: ignore  # noqa: E402
except ImportError:
    from agents.flyer.visual_qa import run_visual_qa  # noqa: E402

STATE_PATH = Path("/opt/shift-agent/state/flyer/projects.json")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")


def _append_audit(log_path: Path, entry_json: str) -> None:
    with FileLock(Path(str(log_path) + ".lock")):
        ndjson_append(log_path, entry_json)


def _resolve_raster(project, pdf_path: Path, explicit: str) -> tuple[Path | None, str, str]:
    """Return (raster_path, raster_source, raster_exactness)."""
    if explicit:
        p = Path(explicit)
        return (p if p.exists() else None), "explicit", "exact"
    twin = pdf_png_twin_path(pdf_path)
    if twin.exists():
        return twin, "twin_sidecar", "exact"
    concept = next((c for c in project.concepts if c.concept_id == project.selected_concept_id), None)
    if concept is not None:
        preview = next((a for a in project.assets if a.asset_id == concept.preview_asset_id), None)
        if preview is not None and Path(preview.path).exists():
            return Path(preview.path), "selected_preview", "upstream_equivalent"
    return None, "none", "none"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--project-id", default="F0203")
    p.add_argument("--state-path", default=str(STATE_PATH))
    p.add_argument("--log-path", default=str(LOG_PATH))
    p.add_argument("--raster", default="", help="explicit raster override (skips auto-resolution)")
    p.add_argument("--work-dir", default="", help="where the twin copy + QA report are written (default: fresh temp dir)")
    args = p.parse_args()

    state_path = Path(args.state_path)
    log_path = Path(args.log_path)
    try:
        with FileLock(Path(str(state_path) + ".lock")):
            store = FlyerProjectStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 — surface, don't guess
        print(json.dumps({"failure": "store_unreadable", "detail": str(exc)[:300]}))
        return 5

    project = next((prj for prj in store.projects if prj.project_id == args.project_id), None)
    if project is None:
        print(json.dumps({"failure": "project_not_found", "project_id": args.project_id}))
        return 4
    pdf_assets = [a for a in project.assets if a.kind == "final_printable_pdf"]
    if not pdf_assets:
        print(json.dumps({"failure": "no_final_printable_pdf_asset", "project_id": args.project_id}))
        return 4
    # Prefer the delivered row (the one the customer actually received).
    pdf_asset = next((a for a in pdf_assets if a.delivery_status == "sent"), pdf_assets[-1])
    pdf_path = Path(pdf_asset.path)

    raster, raster_source, raster_exactness = _resolve_raster(project, pdf_path, args.raster)
    now = datetime.now(timezone.utc)

    if raster is None:
        entry = FlyerPdfQaBackfill(
            ts=now,
            project_id=project.project_id,
            asset_id=pdf_asset.asset_id,
            pdf_path=str(pdf_path),
            raster_source="none",
            raster_exactness="none",
            qa_status="raster_missing",
        )
        _append_audit(log_path, entry.model_dump_json())
        print(json.dumps({"project_id": project.project_id, "qa_status": "raster_missing"}))
        return 0

    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="flyer-pdf-qa-backfill-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    twin_copy = work_dir / (pdf_path.name + ".qapng.png")
    shutil.copyfile(raster, twin_copy)

    report = run_visual_qa(
        project,
        twin_copy,
        output_format="printable_pdf",
        asset_id=pdf_asset.asset_id,
    )
    report_path = work_dir / (pdf_path.name + ".backfill-qa.json")
    report_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")

    entry = FlyerPdfQaBackfill(
        ts=now,
        project_id=project.project_id,
        asset_id=pdf_asset.asset_id,
        pdf_path=str(pdf_path),
        raster_source=raster_source,
        raster_exactness=raster_exactness,
        qa_status=report.status,
        blockers=list(report.blockers)[:50],
        report_path=str(report_path),
    )
    _append_audit(log_path, entry.model_dump_json())
    print(json.dumps({
        "project_id": project.project_id,
        "asset_id": pdf_asset.asset_id,
        "raster_source": raster_source,
        "raster_exactness": raster_exactness,
        "qa_status": report.status,
        "blockers": report.blockers,
        "report_path": str(report_path),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
