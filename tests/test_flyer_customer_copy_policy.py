from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SELF_EVAL = REPO / "tools" / "flyer-self-evaluation.py"

sys.path.insert(0, str(SRC))

from agents.flyer import customer_copy_policy as policy


def load_self_eval():
    spec = importlib.util.spec_from_file_location("flyer_self_eval_policy_test", SELF_EVAL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_self_eval_and_tests_use_same_customer_copy_policy_constants():
    module = load_self_eval()

    assert module.INTERNAL_COPY_TERMS == policy.BANNED_CUSTOMER_COPY_TERMS
    assert module.STATIC_COPY_SCAN_FUNCTIONS == policy.STATIC_CUSTOMER_COPY_FUNCTIONS


def test_customer_copy_policy_detects_project_ids_internal_terms_and_raw_echo():
    result = policy.scan_customer_text(
        "Project F-0065 was queued with provider=openrouter. Original customer request: make it red",
        raw_request="make it red",
    )

    categories = {hit.category for hit in result.hits}
    assert {"project_id", "internal_term", "raw_request_echo"} <= categories


def test_static_send_literal_scan_catches_hook_local_customer_copy():
    source = """
def helper(actions, chat_id):
    actions.send_flyer_text(
        chat_id,
        "Flyer Studio\\n------------\\nProject F0065 provider reason_code leaked",
    )
"""

    scanned = policy.extract_send_call_literals(source)
    assert "Project F0065 provider reason_code leaked" in scanned
    assert policy.scan_customer_text(scanned).hits
