"""Quarantine-before-recovery (census C4; F0197/F0208).

When a render fails QA and a recovery rung re-renders, every rung writes to the
SAME preview path (or deletes the original on repair success) — destroying the
evidence the failed attempt's post-mortem needs. The chokepoint
``agents.flyer.quarantine.quarantine_before_overwrite`` must:

1. copy the failed artifact + whichever sidecars exist into
   ``<state>/quarantine/<project_id>/<ts>-<rung>/``;
2. keep at most 3 quarantine sets per project (prune older);
3. NEVER block the recovery (hostile fs ⇒ stderr + None, no raise);
4. land one ``flyer_artifact_quarantined`` decisions.log row that round-trips
   through the LogEntry discriminated union;
5. integration-shaped: a QA-fail → recovery re-render flow leaves the failed
   original recoverable from quarantine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.flyer.quarantine import (
    KEEP_SETS_PER_PROJECT,
    quarantine_before_overwrite,
    quarantine_root,
)


def _make_failed_artifact(asset_dir: Path, *, project_id: str = "F0208") -> Path:
    """A preview + the full sidecar family, as the render/QA stack writes them."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    preview = asset_dir / f"{project_id}-C1-preview.png"
    preview.write_bytes(b"failed-render-bytes")
    Path(str(preview) + ".text.json").write_text('{"facts": []}', encoding="utf-8")
    Path(str(preview) + ".qa.json").write_text('{"status": "failed"}', encoding="utf-8")
    Path(str(preview) + ".ocr.txt").write_text("REGISTER RENDER\n", encoding="utf-8")
    Path(str(preview) + ".typeset.json").write_text('{"typeset_contract": true}', encoding="utf-8")
    preview.with_name(f"{preview.stem}.raw.png").write_bytes(b"raw-bg-bytes")
    Path(str(preview) + ".ppv1.json").write_text('{"provenance": true}', encoding="utf-8")
    return preview


# ── 1. chokepoint copies artifact + sidecars ─────────────────────────────────

def test_copies_artifact_and_existing_sidecars(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    preview = _make_failed_artifact(tmp_path / "assets")

    set_dir = quarantine_before_overwrite(
        [preview], project_id="F0208", rung="deterministic_recovery")

    assert set_dir is not None and set_dir.is_dir()
    assert set_dir.parent == quarantine_root() / "F0208"
    assert set_dir.name.endswith("-deterministic_recovery")
    copied = {p.name for p in set_dir.iterdir()}
    assert copied == {
        "F0208-C1-preview.png",
        "F0208-C1-preview.png.text.json",
        "F0208-C1-preview.png.qa.json",
        "F0208-C1-preview.png.ocr.txt",
        "F0208-C1-preview.png.typeset.json",
        "F0208-C1-preview.raw.png",
        "F0208-C1-preview.png.ppv1.json",
    }
    assert (set_dir / "F0208-C1-preview.png").read_bytes() == b"failed-render-bytes"
    # the ORIGINAL stays in place — quarantine copies, the rung overwrites
    assert preview.read_bytes() == b"failed-render-bytes"


def test_missing_sidecars_are_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    preview = asset_dir / "F0001-C1-preview.png"
    preview.write_bytes(b"lonely")

    set_dir = quarantine_before_overwrite([preview], project_id="F0001", rung="overlay_fallback")

    assert set_dir is not None
    assert {p.name for p in set_dir.iterdir()} == {"F0001-C1-preview.png"}


def test_nothing_to_copy_returns_none_and_no_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    audit = tmp_path / "decisions.log"

    result = quarantine_before_overwrite(
        [tmp_path / "assets" / "F0002-C1-preview.png"],
        project_id="F0002", rung="content_rerender", audit_log_path=audit)

    assert result is None
    assert not audit.exists()
    assert not (quarantine_root() / "F0002").exists()


# ── 2. bounded: keep the 3 newest sets per project ───────────────────────────

def test_prune_keeps_only_three_most_recent_sets(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    preview = _make_failed_artifact(tmp_path / "assets", project_id="F0003")

    set_dirs = []
    for i in range(KEEP_SETS_PER_PROJECT + 2):
        preview.write_bytes(f"attempt-{i}".encode("ascii"))
        set_dirs.append(quarantine_before_overwrite(
            [preview], project_id="F0003", rung="fabrication_rerender"))
    assert all(d is not None for d in set_dirs)

    remaining = sorted(p.name for p in (quarantine_root() / "F0003").iterdir())
    assert len(remaining) == KEEP_SETS_PER_PROJECT
    # the survivors are the NEWEST sets (microsecond stamps keep set-dir names
    # unique and lexicographically chronological even in a tight loop)
    assert remaining == sorted(d.name for d in set_dirs[-KEEP_SETS_PER_PROJECT:])
    newest = set_dirs[-1]
    assert (newest / "F0003-C1-preview.png").read_bytes() == b"attempt-4"


def test_unchanged_evidence_is_not_requarantined(tmp_path, monkeypatch):
    """Consecutive rungs in one run often see the SAME failed artifact (a repair
    rendered to a distinct path, then the fallback rung fires). Re-quarantining
    identical bytes would burn the keep-3 bound on duplicates and evict distinct
    evidence from earlier runs — the chokepoint must dedupe against the newest set."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    preview = _make_failed_artifact(tmp_path / "assets", project_id="F0008")

    first = quarantine_before_overwrite([preview], project_id="F0008", rung="legacy_autorepair")
    second = quarantine_before_overwrite([preview], project_id="F0008", rung="overlay_fallback")
    assert first is not None
    assert second is None  # unchanged evidence — no new set
    assert len(list((quarantine_root() / "F0008").iterdir())) == 1

    # the artifact CHANGED (a rung overwrote it and failed again) ⇒ new set
    preview.write_bytes(b"second-failed-render")
    third = quarantine_before_overwrite([preview], project_id="F0008", rung="overlay_fallback")
    assert third is not None
    assert len(list((quarantine_root() / "F0008").iterdir())) == 2


# ── 3. best-effort: hostile fs never blocks the recovery ─────────────────────

def test_hostile_quarantine_root_never_raises(tmp_path, monkeypatch, capsys):
    # the quarantine root path exists as a FILE ⇒ every mkdir under it fails
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "quarantine").write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("FLYER_STATE_ROOT", str(state_root))
    preview = _make_failed_artifact(tmp_path / "assets", project_id="F0004")

    result = quarantine_before_overwrite(
        [preview], project_id="F0004", rung="legacy_autorepair",
        audit_log_path=tmp_path / "decisions.log")

    assert result is None  # skipped, not raised — recovery proceeds
    assert "flyer-quarantine" in capsys.readouterr().err
    assert preview.read_bytes() == b"failed-render-bytes"  # original untouched


def test_unreadable_audit_path_never_raises(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    preview = _make_failed_artifact(tmp_path / "assets", project_id="F0005")
    hostile_audit = tmp_path / "audit-as-dir"
    hostile_audit.mkdir()  # ndjson_append/open on a DIRECTORY fails

    result = quarantine_before_overwrite(
        [preview], project_id="F0005", rung="premium_repair",
        audit_log_path=hostile_audit)

    assert result is not None  # the COPY still happened; only the audit failed
    assert (result / "F0005-C1-preview.png").exists()
    assert "audit emit failed" in capsys.readouterr().err


# ── 4. audit row lands + round-trips through the LogEntry union ──────────────

def test_audit_row_lands_and_roundtrips(tmp_path, monkeypatch):
    from pydantic import TypeAdapter
    from schemas import FlyerArtifactQuarantined, LogEntry

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    preview = _make_failed_artifact(tmp_path / "assets", project_id="F0006")
    audit = tmp_path / "decisions.log"

    set_dir = quarantine_before_overwrite(
        [preview], project_id="F0006", rung="deterministic_recovery",
        audit_log_path=audit)

    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    entry = TypeAdapter(LogEntry).validate_python(rows[0])
    assert isinstance(entry, FlyerArtifactQuarantined)
    assert entry.type == "flyer_artifact_quarantined"
    assert entry.project_id == "F0006"
    assert entry.rung == "deterministic_recovery"
    assert entry.quarantine_dir == str(set_dir)
    assert "F0006-C1-preview.png" in entry.files
    assert entry.pruned_sets == 0
    # round-trip: serialize → re-validate
    again = TypeAdapter(LogEntry).validate_json(entry.model_dump_json())
    assert again == entry


def test_direct_schema_construction_and_tag():
    from pydantic import TypeAdapter
    from schemas import FlyerArtifactQuarantined, LogEntry

    entry = FlyerArtifactQuarantined(
        ts=datetime.now(timezone.utc), project_id="F0007", rung="brand_assets_retry",
        quarantine_dir="/opt/shift-agent/state/flyer/quarantine/F0007/x",
        files=["a.png"], pruned_sets=1)
    parsed = TypeAdapter(LogEntry).validate_json(entry.model_dump_json())
    assert isinstance(parsed, FlyerArtifactQuarantined)


# ── 5. integration-shaped: QA-fail → recovery leaves the original recoverable ─

def test_qa_fail_recovery_flow_preserves_failed_artifact(tmp_path, monkeypatch):
    """The exhibits' failure mode, end-to-end at the artifact level: a failed
    render (+ QA sidecars) exists on disk; a recovery rung quarantines then
    re-renders to the SAME path (as every rung does); the failed original must
    remain byte-recoverable from quarantine with its QA verdict beside it."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path / "state"))
    asset_dir = tmp_path / "assets"
    preview = _make_failed_artifact(asset_dir, project_id="F0197")
    audit = tmp_path / "decisions.log"

    # the chokepoint call every rung makes before rendering
    set_dir = quarantine_before_overwrite(
        [preview], project_id="F0197", rung="deterministic_recovery",
        audit_log_path=audit)
    # the rung's re-render: same path, new bytes; sidecars rewritten too
    preview.write_bytes(b"recovery-render-bytes")
    Path(str(preview) + ".qa.json").write_text('{"status": "passed"}', encoding="utf-8")

    # live artifact is the recovery's; the failed original + its failing QA
    # verdict survive in quarantine for the post-mortem
    assert preview.read_bytes() == b"recovery-render-bytes"
    assert (set_dir / "F0197-C1-preview.png").read_bytes() == b"failed-render-bytes"
    assert json.loads((set_dir / "F0197-C1-preview.png.qa.json").read_text(encoding="utf-8")) == {"status": "failed"}
    assert audit.exists()


def test_generate_script_deterministic_recovery_quarantines_failed_original(monkeypatch, tmp_path):
    """Script-level proof on the REAL rung: generate-flyer-concepts' deterministic
    recovery quarantines the failed integrated render before its MODE-2 re-render
    overwrites it (harness mirrors test_flyer_generate_concepts)."""
    import importlib.machinery
    import importlib.util
    import sys
    import types

    repo = Path(__file__).resolve().parent.parent
    script = repo / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
    sys.path.insert(0, str(repo / "src" / "platform"))
    sys.path.insert(0, str(repo / "src"))
    from schemas import Config, FlyerVisualQAReport

    class _NoopFileLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    fake_safe_io.ndjson_append = lambda path, text: Path(path).open("a", encoding="utf-8").write(text + "\n")
    fake_safe_io.load_yaml_model = lambda *_a, **_k: Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "openrouter/some-model", "concept_count": 1},
    })
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    module_name = "generate_flyer_concepts_quarantine_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(script))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)

    from agents.flyer.render import RenderedAssetSpec

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_DETERMINISTIC_RECOVERY", "1")
    # allowlist-semantics unification: empty allowlist = DISABLED, so the
    # recovery gate needs the project's phone explicitly allowed
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    now = datetime(2026, 7, 4, tzinfo=timezone.utc).isoformat()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [{
            "project_id": "F0208",
            "status": "generating_concepts",
            "customer_phone": "+17329837841",
            "created_at": now,
            "updated_at": now,
            "original_message_id": "m-f0208",
            "raw_request": "Evening snacks flyer: Punugulu $5, Egg Bonda $6. Daily 4-8 PM.",
            "fields": {
                "event_or_business_name": "Lakshmi's Kitchen",
                "venue_or_location": "90 Brybar Dr St Johns FL",
                "contact_info": "+1 732 983 7841",
            },
            "locked_facts": [
                {"fact_id": "business_name", "label": "Business", "value": "Lakshmi's Kitchen", "source": "customer_profile", "required": True},
                {"fact_id": "location", "label": "Location", "value": "90 Brybar Dr St Johns FL", "source": "customer_profile", "required": True},
                {"fact_id": "contact_phone", "label": "Contact", "value": "+1 732 983 7841", "source": "customer_profile", "required": True},
            ],
        }],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(_project, output_dir, **kwargs):
        # first call = integrated primary (fails QA); second = MODE-2 recovery
        # re-render — SAME path, like the real renderer
        n = len(render_calls)
        render_calls.append(kwargs.get("force_background_only", False))
        path = Path(output_dir) / "F0208-C1-preview.png"
        path.write_bytes(b"integrated-failed" if n == 0 else b"mode2-recovered")
        return [RenderedAssetSpec(path=path, kind="concept_preview",
                                  output_format="concept_preview",
                                  width=1080, height=1350, concept_id="C1")]

    def fake_qa(project_obj, artifact_path, *, output_format, asset_id="", allow_sidecar=None):
        failed = len(render_calls) == 1  # primary fails; recovery passes
        return FlyerVisualQAReport(
            project_id=project_obj.project_id, asset_id=asset_id,
            artifact_path=str(artifact_path), artifact_sha256="0" * 64,
            project_version=project_obj.version, output_format=output_format,
            provider="test", qa_source="ocr_vision",
            status="failed" if failed else "passed",
            blockers=["missing required visible fact: business_name"] if failed else [],
            extracted_text="" if failed else "Lakshmi's Kitchen",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0208",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
        "--audit-log-path", str(tmp_path / "decisions.log"),
        "--autorepair-state-path", str(tmp_path / "autorepair_attempts.json"),
    ])

    rc = module.main()

    assert rc == 0
    assert render_calls == [False, True]  # primary, then MODE-2 recovery
    # live preview is the recovery's render...
    assert (asset_dir / "F0208-C1-preview.png").read_bytes() == b"mode2-recovered"
    # ...and the failed integrated original survives in quarantine
    project_sets = sorted((Path(str(tmp_path)) / "quarantine" / "F0208").iterdir())
    det_sets = [d for d in project_sets if "deterministic_recovery" in d.name]
    assert len(det_sets) == 1
    assert (det_sets[0] / "F0208-C1-preview.png").read_bytes() == b"integrated-failed"
    # one flyer_artifact_quarantined audit row for the rung
    rows = [json.loads(line) for line in (tmp_path / "decisions.log").read_text(encoding="utf-8").splitlines()]
    q_rows = [r for r in rows if r.get("type") == "flyer_artifact_quarantined"]
    assert len(q_rows) == 1
    assert q_rows[0]["rung"] == "deterministic_recovery"
    assert q_rows[0]["project_id"] == "F0208"
