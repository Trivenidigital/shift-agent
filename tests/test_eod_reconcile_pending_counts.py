"""F0-1 regression: eod-reconcile pending-proposal counting.

Before the fix, `_aggregate_today` iterated `pending.proposals` (a
dict[str, Proposal]) which yields the id KEYS; `p.status` on a str raised
AttributeError, swallowed by the bare `except Exception:` → proposals_resolved
and proposals_unresolved stayed 0 and degraded flipped True for ANY non-empty
store. This exercises the real `_aggregate_today` with a non-empty pending
store and pins correct terminal/non-terminal counts + degraded=False.

Runs on Windows via an fcntl stub (eod-reconcile imports safe_io, which imports
fcntl at module top); `_aggregate_today` only READS state, so no lock semantics
matter here.
"""
from __future__ import annotations

from pathlib import Path

from fixtures_fleet import (
    ensure_fcntl_stub, load_script, write_config, write_pending_store,
    awaiting_proposal, sent_proposal, accepted_proposal, expired_proposal,
)

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "eod_reconcile" / "scripts" / "eod-reconcile"

EOD_BLOCK = {
    "eod_time": "23:00", "catchup_window_minutes": 15,
    "pushover_priority": 0, "pushover_only_if_unresolved": False,
}


def _load_eod():
    return load_script("eod_reconcile_counts_under_test", SCRIPT)


def _build(tmp_path, monkeypatch, proposals):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "decisions.log").write_text("", encoding="utf-8")
    write_config(tmp_path, eod=EOD_BLOCK)
    write_pending_store(tmp_path, proposals)
    # Point the log-source registry at the (empty) fixture log so today-window
    # aggregation reads no decisions rows; freeze "now" so _customer_now never
    # calls into tz helpers.
    monkeypatch.setenv("SHIFT_AGENT_LOG_SOURCE_OVERRIDE", str(logs / "decisions.log"))
    monkeypatch.setenv("SHIFT_AGENT_NOW_OVERRIDE", "2026-04-25T23:05:00-04:00")

    import yaml
    from schemas import Config
    cfg = Config.model_validate(yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8")))

    mod = _load_eod()
    mod.PENDING_PATH = tmp_path / "pending.json"
    return mod, cfg


def test_mixed_store_counts_terminal_vs_nonterminal(tmp_path, monkeypatch):
    mod, cfg = _build(tmp_path, monkeypatch, [
        awaiting_proposal("P0001", "#ABCDE"),   # non-terminal -> unresolved
        sent_proposal("P0002", "#BCDEF"),        # non-terminal -> unresolved
        accepted_proposal("P0003", "#CDEFG"),    # terminal     -> resolved
        expired_proposal("P0004", "#DEFGH"),     # terminal     -> resolved
    ])
    counts, degraded = mod._aggregate_today(cfg)
    assert counts["proposals_resolved"] == 2
    assert counts["proposals_unresolved"] == 2
    assert degraded is False


def test_all_nonterminal_store(tmp_path, monkeypatch):
    mod, cfg = _build(tmp_path, monkeypatch, [
        awaiting_proposal("P0001", "#ABCDE"),
        awaiting_proposal("P0002", "#BCDEF"),
    ])
    counts, degraded = mod._aggregate_today(cfg)
    assert counts["proposals_unresolved"] == 2
    assert counts["proposals_resolved"] == 0
    assert degraded is False


def test_empty_store_not_degraded(tmp_path, monkeypatch):
    mod, cfg = _build(tmp_path, monkeypatch, [])
    counts, degraded = mod._aggregate_today(cfg)
    assert counts["proposals_resolved"] == 0
    assert counts["proposals_unresolved"] == 0
    assert degraded is False
