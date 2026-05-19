"""Visual/OCR QA gate for Flyer Studio generated artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re

from schemas import FlyerProject, FlyerVisualQAReport


PLACEHOLDER_RE = re.compile(r"\[(?:price|phone|date|time|address|item|text)[^\]]*\]|lorem ipsum", re.IGNORECASE)


@dataclass(frozen=True)
class VisualQAValidation:
    ok: bool
    blockers: list[str]
    report_path: Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visual_qa_path(artifact_path: Path | str) -> Path:
    return Path(str(artifact_path) + ".qa.json")


def _sidecar_text(path: Path, *, allow_sidecar: bool) -> tuple[str, str, str]:
    sidecar = Path(str(path) + ".ocr.txt")
    if allow_sidecar and sidecar.exists():
        return sidecar.read_text(encoding="utf-8"), "sidecar", "sidecar_test"
    return "", "unavailable", "ocr_vision"


def run_visual_qa(
    project: FlyerProject,
    artifact_path: Path | str,
    *,
    output_format: str,
    asset_id: str = "",
    allow_sidecar: bool | None = None,
) -> FlyerVisualQAReport:
    artifact = Path(artifact_path)
    if allow_sidecar is None:
        allow_sidecar = os.environ.get("FLYER_QA_ALLOW_SIDECAR") == "1"
    extracted_text, provider, qa_source = _sidecar_text(artifact, allow_sidecar=allow_sidecar)
    blockers: list[str] = []
    if not extracted_text:
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact),
            artifact_sha256=sha256_file(artifact),
            project_version=project.version,
            output_format=output_format,
            provider=provider,
            qa_source=qa_source,
            status="provider_unavailable",
            blockers=["ocr/vision text unavailable for generated artifact"],
            extracted_text="",
            checked_at=datetime.now(timezone.utc),
        )
    normalized = re.sub(r"\s+", " ", extracted_text).casefold()
    if PLACEHOLDER_RE.search(extracted_text):
        blockers.append("placeholder text is visible in generated flyer")
    for fact in project.locked_facts:
        if not fact.required:
            continue
        if fact.value.casefold() not in normalized:
            blockers.append(f"missing required visible fact: {fact.fact_id}")
    return FlyerVisualQAReport(
        project_id=project.project_id,
        asset_id=asset_id,
        artifact_path=str(artifact),
        artifact_sha256=sha256_file(artifact),
        project_version=project.version,
        output_format=output_format,
        provider=provider,
        qa_source=qa_source,
        status="failed" if blockers else "passed",
        blockers=blockers,
        extracted_text=extracted_text,
        checked_at=datetime.now(timezone.utc),
    )


def write_visual_qa_report(report: FlyerVisualQAReport, artifact_path: Path | str) -> Path:
    artifact = Path(artifact_path)
    data = report.model_dump(mode="json")
    data["artifact_sha256"] = sha256_file(artifact)
    path = visual_qa_path(artifact)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def validate_visual_qa_report(
    artifact_path: Path | str,
    *,
    project_id: str,
    project_version: int,
    output_format: str,
    allow_sidecar: bool | None = None,
) -> VisualQAValidation:
    artifact = Path(artifact_path)
    path = visual_qa_path(artifact)
    blockers: list[str] = []
    if allow_sidecar is None:
        allow_sidecar = os.environ.get("FLYER_QA_ALLOW_SIDECAR") == "1"
    if not path.exists():
        return VisualQAValidation(False, ["visual QA report missing"], path)
    try:
        report = FlyerVisualQAReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return VisualQAValidation(False, [f"visual QA report unreadable: {exc}"], path)
    if report.project_id != project_id:
        blockers.append("visual QA project mismatch")
    if report.project_version != project_version:
        blockers.append("visual QA project version mismatch")
    if report.output_format != output_format:
        blockers.append("visual QA output format mismatch")
    if report.artifact_sha256 != sha256_file(artifact):
        blockers.append("visual QA artifact hash mismatch")
    if report.status != "passed":
        blockers.append("visual QA did not pass")
    if report.qa_source == "sidecar_test" and not allow_sidecar:
        blockers.append("sidecar visual QA is disabled")
    blockers.extend(report.blockers)
    return VisualQAValidation(not blockers, blockers, path)
