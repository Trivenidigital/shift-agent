"""AN-1 (Flyer Studio E2E adversarial audit 2026-07-13).

An approval that arrives BEFORE a preview exists (status generating_concepts /
awaiting_concept_selection) must get a clear progress reply, not be silently
rewritten as a revision edit. Tests the pure decision helper
``_flyer_early_approval_progress_reply`` (the send/audit/skip wiring around it is
exercised by the Linux CI intercept suite).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_hooks():
    pkg_name = "cf_router_flyer_an1_pkg_under_test"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]
    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = importlib.util.module_from_spec(pkg_spec)
    for sub in ("actions", "hooks"):
        full = f"{pkg_name}.{sub}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{sub}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
    return sys.modules[f"{pkg_name}.hooks"]


H = _load_hooks()


def test_an1_generating_concepts_gives_progress_not_revision():
    reply = H._flyer_early_approval_progress_reply("generating_concepts")
    assert reply and "still being prepared" in reply


def test_an1_awaiting_concept_selection_asks_to_pick():
    reply = H._flyer_early_approval_progress_reply("awaiting_concept_selection")
    assert reply and "1, 2, or 3" in reply


def test_an1_final_and_delivered_statuses_route_normally():
    # A real approvable/other status must NOT be intercepted by AN-1 (None => the
    # normal finalize / revision routing runs).
    for status in ("awaiting_final_approval", "revising_design",
                   "delivered_with_warning", "delivered", "intake_started"):
        assert H._flyer_early_approval_progress_reply(status) is None, status
