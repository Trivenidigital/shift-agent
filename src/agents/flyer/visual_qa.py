"""Visual/OCR QA gate for Flyer Studio generated artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request

from schemas import FlyerProject, FlyerVisualQAReport


PLACEHOLDER_RE = re.compile(r"\[(?:price|phone|date|time|address|item|text)[^\]]*\]|lorem ipsum", re.IGNORECASE)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SEC = 60
VISION_QA_MODEL = os.environ.get("FLYER_VISUAL_QA_MODEL") or os.environ.get("VISION_MODEL") or "openai/gpt-4o-mini"
VISION_QA_PROMPT = """Read this generated flyer/poster image as OCR/vision QA.

Return STRICT JSON only:
{
  "extracted_text": "all visible flyer text you can read, preserving names, prices, dates, phones, addresses, badges, and placeholders",
  "quality_notes": ["short factual notes about unreadable/garbled text or visible placeholders"]
}

Do not invent missing text. If no readable text exists, return an empty extracted_text string.
"""


@dataclass(frozen=True)
class VisualQAValidation:
    ok: bool
    blockers: list[str]
    report_path: Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visual_qa_path(artifact_path: Path | str) -> Path:
    return Path(str(artifact_path) + ".qa.json")


def _read_key_from_env_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "OPENROUTER_API_KEY":
                return raw.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _openrouter_key() -> str:
    return (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or _read_key_from_env_file("/root/.hermes/.env")
        or _read_key_from_env_file("/opt/shift-agent/.env")
    )


def _vision_text(path: Path) -> tuple[str, str, str, list[str]]:
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
        return "", "unavailable", "ocr_vision", ["OPENROUTER_API_KEY missing"]
    if not path.exists() or not path.is_file():
        return "", "unavailable", "ocr_vision", ["artifact missing"]
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/png"
    if not mime.startswith("image/") and mime != "application/pdf":
        return "", "unavailable", "ocr_vision", [f"unsupported OCR media type: {mime}"]
    raw = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": VISION_QA_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_QA_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}},
            ],
        }],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
        doc = json.loads(body)
        content = doc["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        return "", "unavailable", "ocr_vision", [f"vision OCR failed: {type(exc).__name__}"]
    notes = [str(item) for item in parsed.get("quality_notes") or [] if str(item).strip()]
    return str(parsed.get("extracted_text") or ""), "openrouter", "ocr_vision", notes


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
    provider_notes: list[str] = []
    if not extracted_text:
        extracted_text, provider, qa_source, provider_notes = _vision_text(artifact)
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
            blockers=["ocr/vision text unavailable for generated artifact", *provider_notes],
            extracted_text="",
            checked_at=datetime.now(timezone.utc),
        )
    normalized = re.sub(r"\s+", " ", extracted_text).casefold()
    if PLACEHOLDER_RE.search(extracted_text):
        blockers.append("placeholder text is visible in generated flyer")
    blockers.extend(note for note in provider_notes if "placeholder" in note.lower() or "unreadable" in note.lower() or "garbled" in note.lower())
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
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    else:
        atomic_write_text(path, text)
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
