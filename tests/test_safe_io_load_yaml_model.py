"""safe_io.load_yaml_model — YAML-validated Pydantic load.

Regression context: PR #30/#32 Expense Bookkeeper scripts called
load_model (JSON-only) on config.yaml, causing safe_load_json to
rename-quarantine the customer's actual config on every call. This
helper is the correct chokepoint for YAML+Pydantic loads.

Pure-function unit tests. Skipped on Windows because safe_io imports
fcntl unconditionally at module level.
"""
from __future__ import annotations
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io imports fcntl unconditionally (Linux-only module)",
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
if platform.system() != "Windows":
    from safe_io import load_yaml_model  # noqa: E402
    from pydantic import BaseModel, ValidationError  # noqa: E402

    class _SampleModel(BaseModel):
        name: str
        count: int


def test_load_yaml_model_valid_yaml(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("name: triveni\ncount: 9\n", encoding="utf-8")
    m = load_yaml_model(p, _SampleModel)
    assert m.name == "triveni"
    assert m.count == 9


def test_load_yaml_model_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_yaml_model(tmp_path / "nonexistent.yaml", _SampleModel)


def test_load_yaml_model_empty_file_raises(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        load_yaml_model(p, _SampleModel)
    assert "empty" in str(exc.value).lower()


def test_load_yaml_model_invalid_yaml_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: triveni\ncount: : invalid yaml :\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        load_yaml_model(p, _SampleModel)
    assert "yaml" in str(exc.value).lower() or "parse" in str(exc.value).lower()


def test_load_yaml_model_validation_error_raises(tmp_path):
    """Schema validation failure propagates as Pydantic ValidationError."""
    p = tmp_path / "bad-shape.yaml"
    p.write_text("name: triveni\ncount: not-an-int\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_yaml_model(p, _SampleModel)


def test_load_yaml_model_does_not_rename_on_parse_error(tmp_path):
    """REGRESSION GUARD: this is the entire reason the helper exists.

    safe_load_json (called by load_model) rename-quarantines a file whose
    content fails json.loads. load_yaml_model MUST NOT do that — operator
    needs to see the parse error and fix the file in place.
    """
    p = tmp_path / "config.yaml"
    p.write_text("name: triveni\ncount: : invalid yaml :\n", encoding="utf-8")
    try:
        load_yaml_model(p, _SampleModel)
    except RuntimeError:
        pass  # expected
    # Critical assertion: the file IS still at its original path.
    assert p.exists(), "load_yaml_model must NOT rename the file on parse error"
    siblings = list(tmp_path.glob("config.yaml.corrupt-*"))
    assert siblings == [], f"unexpected corrupt-rename siblings: {siblings}"


def test_load_yaml_model_does_not_rename_on_yaml_content_via_json_loader_path(tmp_path):
    """The original bug: calling load_model (JSON-loader) on YAML content
    triggers the corrupt-rename. Confirm load_yaml_model NEVER touches that
    code path. Belt-and-suspenders for the symptom.
    """
    p = tmp_path / "config.yaml"
    p.write_text("name: triveni\ncount: 1\n", encoding="utf-8")
    # Call multiple times — file should remain at original path
    for _ in range(3):
        load_yaml_model(p, _SampleModel)
    assert p.exists()
    assert list(tmp_path.glob("config.yaml.corrupt-*")) == []
