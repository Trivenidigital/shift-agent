"""Redacted Flyer intent training export helpers.

The export is offline and operator-curated: it creates deterministic JSONL
examples from shadow audit rows, but does not write Hermes memory directly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


try:
    from safe_io import atomic_write_text
except Exception:  # pragma: no cover - local import fallback
    def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:  # type: ignore[no-redef]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)


class FlyerIntentTrainingExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    dedupe_key: str = Field(min_length=1, max_length=96)
    message_id_hash: str = Field(min_length=1, max_length=64)
    chat_key_hash: str = Field(default="", max_length=64)
    decision_source: str = Field(default="", max_length=80)
    classifier_status: str = Field(default="", max_length=80)
    intent: str = Field(default="", max_length=80)
    action: str = Field(default="", max_length=80)
    confidence_bucket: Literal["low", "medium", "high"] = "low"
    validator_ok: bool = False
    validator_reasons: list[str] = Field(default_factory=list, max_length=20)
    route_label: str = Field(default="", max_length=80)
    outcome_label: str = Field(default="", max_length=80)
    input_features: "FlyerIntentTrainingInputFeatures" = Field(default_factory=lambda: FlyerIntentTrainingInputFeatures())


class FlyerIntentTrainingInputFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_media: bool = False
    mode: str = Field(default="", max_length=40)
    risk_scope: str = Field(default="", max_length=80)
    customer_status: str = Field(default="", max_length=80)
    project_status: str = Field(default="", max_length=80)
    intake_status: str = Field(default="", max_length=80)
    route_sequence: list[str] = Field(default_factory=list, max_length=20)


def _confidence_bucket(confidence: Any) -> Literal["low", "medium", "high"]:
    try:
        value = float(confidence or 0.0)
    except Exception:
        value = 0.0
    if value >= 0.85:
        return "high"
    if value >= 0.55:
        return "medium"
    return "low"


def _expected_action(advisory_action: str) -> str:
    return {
        "create_project": "new_project",
        "revise_project": "revision",
        "approve_project": "approval",
        "account_update": "account_update",
        "manual_review": "manual_review",
        "clarify": "clarify",
        "observe": "observe",
    }.get(str(advisory_action or ""), "unknown")


def _outcome_label(row: dict[str, Any]) -> str:
    if str(row.get("classifier_status") or "off") in {"timeout", "invalid", "error"}:
        return "classifier_failed"
    if row.get("validator_ok") is False:
        return "validator_rejected"
    advisory = str(row.get("advisory_action") or "")
    actual = str(row.get("actual_action") or "")
    expected = _expected_action(advisory)
    if expected in {"observe", "clarify"}:
        return "router_mutated" if actual in {"new_project", "revision", "approval", "account_update"} else "route_matched"
    return "route_matched" if expected == actual else "route_disagreed"


def _dedupe_key(row: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(row.get("schema_version") if row.get("schema_version") is not None else 1),
            str(row.get("chat_key_hash") or ""),
            str(row.get("message_id_hash") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]


def example_from_intent_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("type") != "flyer_hermes_intent_decision":
        return None
    message_id_hash = str(row.get("message_id_hash") or "")
    if not message_id_hash:
        return None
    source_schema_version = row.get("schema_version")
    example = FlyerIntentTrainingExample(
        schema_version=int(source_schema_version if source_schema_version is not None else 1),
        dedupe_key=_dedupe_key(row),
        message_id_hash=message_id_hash,
        chat_key_hash=str(row.get("chat_key_hash") or ""),
        decision_source=str(row.get("decision_source") or "none"),
        classifier_status=str(row.get("classifier_status") or "off"),
        intent=str(row.get("advisory_intent") or "unknown"),
        action=str(row.get("advisory_action") or "observe"),
        confidence_bucket=_confidence_bucket(row.get("confidence")),
        validator_ok=bool(row.get("validator_ok")),
        validator_reasons=[str(item)[:80] for item in (row.get("validator_reasons") or [])[:20]],
        route_label=str(row.get("actual_action") or "unknown"),
        outcome_label=_outcome_label(row),
        input_features={
            "has_media": bool(row.get("has_media")),
            "mode": str(row.get("mode") or ""),
            "risk_scope": str(row.get("risk_scope") or "none"),
            "customer_status": str(row.get("customer_status") or "")[:80],
            "project_status": str(row.get("project_status") or "")[:80],
            "intake_status": str(row.get("intake_status") or "")[:80],
            "route_sequence": [str(item)[:120] for item in (row.get("route_sequence") or [])[:20]],
        },
    )
    return example.model_dump()


def load_intent_rows(decisions_log: Path) -> list[dict[str, Any]]:
    if not decisions_log.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in decisions_log.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line.lstrip("\ufeff"))
        except Exception:
            continue
        if isinstance(row, dict) and row.get("type") == "flyer_hermes_intent_decision":
            rows.append(row)
    return rows


def build_training_examples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        example = example_from_intent_row(row)
        if not example:
            continue
        key = str(example.get("dedupe_key") or "")
        if key in seen:
            continue
        seen.add(key)
        examples.append(example)
    return examples


def export_training_examples(*, decisions_log: Path, out_path: Path, output_format: str = "jsonl") -> dict[str, Any]:
    examples = build_training_examples(load_intent_rows(decisions_log))
    if output_format != "jsonl":
        raise ValueError("only jsonl format is supported")
    content = "".join(json.dumps(example, sort_keys=True) + "\n" for example in examples)
    atomic_write_text(out_path, content)
    return {
        "schema_version": 1,
        "format": output_format,
        "input_rows": len(load_intent_rows(decisions_log)),
        "written": len(examples),
        "out": str(out_path),
    }
