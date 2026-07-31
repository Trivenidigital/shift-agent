"""M2 — import-catering-pricebook end-to-end. Linux-only (safe_io needs fcntl).

Driven the same way as tests/test_catering_finalize_menu.py: a subprocess
importlib wrapper loads the extension-less script and rebinds its hardcoded
paths at tmp locations before calling main().
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="import-catering-pricebook depends on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "import-catering-pricebook"
PLATFORM_DIR = REPO / "src" / "platform"
FIXTURE = REPO / "tests" / "fixtures" / "catering_pricebook_valid.json"
TEMPLATE = (REPO / "src" / "agents" / "catering" / "templates"
            / "catering-pricebook-template.json")


def _run(env_dir: Path, *cli_args):
    state = env_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    (env_dir / "logs").mkdir(parents=True, exist_ok=True)
    argv = ["import-catering-pricebook", *cli_args]
    wrapper = f"""
import sys, pathlib, json, io
sys.argv = {argv!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("icp_test_loaded", {str(SCRIPT)!r}).load_module()
mod.PRICEBOOK_PATH = pathlib.Path({str(state / 'catering-pricebook.json')!r})
mod.PRICEBOOK_LOCK = pathlib.Path({str(state / 'catering-pricebook.json.lock')!r})
mod.ARCHIVE_DIR = pathlib.Path({str(state / 'catering-pricebook-archive')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
buf = io.StringIO()
sys.stdout = buf
rc = -99
try:
    rc = mod.main()
except SystemExit as se:
    rc = se.code if isinstance(se.code, int) else -1
finally:
    sys.stdout = sys.__stdout__
print(json.dumps({{"rc": rc, "stdout": buf.getvalue()}}))
"""
    proc = subprocess.run([sys.executable, "-c", wrapper],
                          capture_output=True, text=True, timeout=30)
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    parsed = json.loads(lines[-1]) if lines else {"rc": -1, "stdout": ""}
    return proc, parsed


def _stdout_json(parsed):
    return json.loads(parsed["stdout"].strip().splitlines()[-1])


def _stored(env_dir: Path) -> dict:
    return json.loads(
        (env_dir / "state" / "catering-pricebook.json").read_text(encoding="utf-8"))


def _audit(env_dir: Path) -> list[dict]:
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── happy path ───────────────────────────────────────────────────────────────
def test_first_import_writes_store_and_audits(tmp_path):
    _proc, parsed = _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")
    assert parsed["rc"] == 0, parsed
    out = _stdout_json(parsed)
    assert out["status"] == "imported"
    assert out["prev_version"] == 0
    assert out["version"] == 5          # fixture is v4 -> max(0,4)+1
    assert out["package_count"] == 3 and out["fee_count"] == 3
    assert out["discount_count"] == 3 and out["item_override_count"] == 1

    stored = _stored(tmp_path)
    assert stored["schema_version"] == 1
    assert stored["pricebook"]["version"] == 5
    assert stored["pricebook"]["currency"] == "USD"

    rows = [r for r in _audit(tmp_path) if r["type"] == "catering_pricebook_updated"]
    assert len(rows) == 1
    assert rows[0]["version"] == 5 and rows[0]["prev_version"] == 0
    assert rows[0]["updated_by"] == "manual"
    # PRIVACY: counts only — no per-package price may leak into the audit log.
    assert "price_per_person_cents" not in json.dumps(rows[0])


def test_second_import_archives_the_prior_version_and_bumps(tmp_path):
    _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")
    _proc, parsed = _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner",
                         "--updated-by", "cockpit")
    assert parsed["rc"] == 0, parsed
    out = _stdout_json(parsed)
    assert out["prev_version"] == 5 and out["version"] == 6
    assert out["updated_by"] == "cockpit"

    archives = list((tmp_path / "state" / "catering-pricebook-archive").glob("*.json"))
    assert len(archives) == 1
    assert "pricebook-v5-" in archives[0].name
    assert json.loads(archives[0].read_text(encoding="utf-8"))["pricebook"]["version"] == 5


def test_reimporting_a_stale_export_cannot_roll_the_version_backwards(tmp_path):
    """max(prior, incoming)+1: editing an old export must not clobber a newer one."""
    _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")   # -> v5
    _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")   # -> v6
    stale = tmp_path / "stale.json"
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["version"] = 2
    stale.write_text(json.dumps(doc), encoding="utf-8")

    _proc, parsed = _run(tmp_path, "--file", str(stale), "--sender-role", "owner")
    assert _stdout_json(parsed)["version"] == 7


def test_store_envelope_shape_is_accepted(tmp_path):
    """Re-importing a file this script itself wrote must work."""
    _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")
    exported = tmp_path / "exported.json"
    exported.write_text(json.dumps(_stored(tmp_path)), encoding="utf-8")
    _proc, parsed = _run(tmp_path, "--file", str(exported), "--sender-role", "owner")
    assert parsed["rc"] == 0, parsed
    assert _stdout_json(parsed)["version"] == 6


def test_dry_run_validates_without_writing_or_auditing(tmp_path):
    _proc, parsed = _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner",
                         "--dry-run")
    assert parsed["rc"] == 0, parsed
    out = _stdout_json(parsed)
    assert out["status"] == "validated" and out["dry_run"] is True
    assert not (tmp_path / "state" / "catering-pricebook.json").exists()
    assert _audit(tmp_path) == []


# ── placeholder seed ─────────────────────────────────────────────────────────
def test_placeholder_template_imports_but_warns_loudly(tmp_path):
    proc, parsed = _run(tmp_path, "--file", str(TEMPLATE), "--sender-role", "owner")
    assert parsed["rc"] == 0, parsed
    assert _stdout_json(parsed)["placeholder"] is True
    assert "placeholder=true" in proc.stderr
    rows = [r for r in _audit(tmp_path) if r["type"] == "catering_pricebook_updated"]
    assert rows[0]["placeholder"] is True


# ── refusals ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("role", ["employee", "customer", "unknown"])
def test_non_owner_is_refused_before_touching_state(tmp_path, role):
    _proc, parsed = _run(tmp_path, "--file", str(FIXTURE), "--sender-role", role)
    assert parsed["rc"] == 12
    assert not (tmp_path / "state" / "catering-pricebook.json").exists()


def test_missing_file_is_refused(tmp_path):
    _proc, parsed = _run(tmp_path, "--file", str(tmp_path / "nope.json"),
                         "--sender-role", "owner")
    assert parsed["rc"] == 2


def test_malformed_json_is_refused_with_a_clear_message(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all", encoding="utf-8")
    proc, parsed = _run(tmp_path, "--file", str(bad), "--sender-role", "owner")
    assert parsed["rc"] == 2
    assert "not valid JSON" in proc.stderr


def test_inr_currency_is_refused_loudly(tmp_path):
    """Binding ruling: a non-USD pricebook must never load."""
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["currency"] = "INR"
    src = tmp_path / "inr.json"
    src.write_text(json.dumps(doc), encoding="utf-8")

    proc, parsed = _run(tmp_path, "--file", str(src), "--sender-role", "owner")
    assert parsed["rc"] == 2
    assert "USD-only" in proc.stderr
    assert not (tmp_path / "state" / "catering-pricebook.json").exists()


def test_float_money_is_refused(tmp_path):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["per_person_packages"][0]["price_per_person_cents"] = 18.50
    src = tmp_path / "floaty.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    proc, parsed = _run(tmp_path, "--file", str(src), "--sender-role", "owner")
    assert parsed["rc"] == 2
    assert "validation failed" in proc.stderr


def test_duplicate_package_ids_are_refused(tmp_path):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["per_person_packages"][1]["id"] = doc["per_person_packages"][0]["id"]
    src = tmp_path / "dupe.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    proc, parsed = _run(tmp_path, "--file", str(src), "--sender-role", "owner")
    assert parsed["rc"] == 2
    assert "duplicate id" in proc.stderr


def test_a_refusal_leaves_an_existing_pricebook_untouched(tmp_path):
    _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")
    before = (tmp_path / "state" / "catering-pricebook.json").read_text(encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": "not-an-int"}', encoding="utf-8")

    _proc, parsed = _run(tmp_path, "--file", str(bad), "--sender-role", "owner")
    assert parsed["rc"] == 2
    after = (tmp_path / "state" / "catering-pricebook.json").read_text(encoding="utf-8")
    assert after == before


def test_corrupt_existing_pricebook_is_not_silently_overwritten(tmp_path):
    """Treating an unreadable pricebook as absent would reset the version counter
    and skip the archive — the operator must inspect it first."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "catering-pricebook.json").write_text("{{{corrupt", encoding="utf-8")

    proc, parsed = _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")
    assert parsed["rc"] == 5, (parsed, proc.stderr)
    assert (state / "catering-pricebook.json").read_text(encoding="utf-8") == "{{{corrupt"


# ── the imported book is usable by the kernel ────────────────────────────────
def test_imported_pricebook_loads_back_into_the_kernel(tmp_path):
    _run(tmp_path, "--file", str(FIXTURE), "--sender-role", "owner")
    sys.path.insert(0, str(PLATFORM_DIR))
    import catering_pricing

    book = catering_pricing.load_pricebook(tmp_path / "state" / "catering-pricebook.json")
    assert book is not None and book.version == 5
    assert catering_pricing.load_pricebook(tmp_path / "state" / "nothing-here.json") is None
