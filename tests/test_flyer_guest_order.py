"""Payment-first guest order contracts for Flyer Studio."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.guest_order import (  # noqa: E402
    activate_guest_order,
    consume_guest_order,
    find_paid_guest_order,
    load_guest_order_store,
    release_guest_order,
    reserve_guest_order,
    start_guest_order,
)


def test_guest_order_starts_pending_payment_with_four_dollar_link(tmp_path):
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)

    result = start_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        checkout_url_template="https://pay.example/quick/{order_id}?amount={amount_cents}",
        now=now,
    )

    assert result.ok is True
    assert result.order_id == "GUEST0001"
    assert result.status == "pending_payment"
    assert "$4" in result.reply_text
    assert "https://pay.example/quick/GUEST0001?amount=400" in result.reply_text
    store = load_guest_order_store(state)
    assert store.orders[0].sender_phone == "+17329837841"


def test_guest_order_activation_then_single_use_consumes_order(tmp_path):
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        now=now,
    )

    active = activate_guest_order(
        state_path=state,
        order_id="GUEST0001",
        payment_reference="pi_test_1",
        now=now,
    )
    assert active.ok is True
    assert "Payment received" in active.reply_text
    assert find_paid_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
    ) is not None
    reserve_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )

    used = consume_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )
    assert used.ok is True
    assert used.status == "used"
    assert find_paid_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
    ) is None


def test_guest_order_activation_requires_payment_reference(tmp_path):
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        now=now,
    )

    active = activate_guest_order(
        state_path=state,
        order_id="GUEST0001",
        payment_reference="   ",
        now=now,
    )

    assert active.ok is False
    assert active.detail == "payment_reference_required"
    store = load_guest_order_store(state)
    assert store.orders[0].status == "pending_payment"
    assert store.orders[0].payment_reference == ""


def test_guest_order_consume_requires_matching_reservation(tmp_path):
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        now=now,
    )
    activate_guest_order(
        state_path=state,
        order_id="GUEST0001",
        payment_reference="pi_test_1",
        now=now,
    )

    used = consume_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )

    assert used.ok is False
    assert used.detail == "reserved_guest_order_not_found"


def test_guest_order_reservation_prevents_parallel_paid_use_until_release(tmp_path):
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        now=now,
    )
    activate_guest_order(
        state_path=state,
        order_id="GUEST0001",
        payment_reference="pi_test_1",
        now=now,
    )

    reserved = reserve_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )

    assert reserved.ok is True
    assert reserved.status == "reserved"
    assert find_paid_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
    ) is None

    released = release_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )

    assert released.ok is True
    assert released.status == "paid"
    assert find_paid_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
    ) is not None


def test_reserved_guest_order_consumes_only_matching_project(tmp_path):
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        now=now,
    )
    activate_guest_order(
        state_path=state,
        order_id="GUEST0001",
        payment_reference="pi_test_1",
        now=now,
    )
    reserve_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )

    wrong_project = consume_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F9999",
        now=now,
    )
    assert wrong_project.ok is False

    used = consume_guest_order(
        state_path=state,
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        project_id="F0020",
        now=now,
    )
    assert used.ok is True
    assert used.status == "used"


def test_consume_guest_order_idempotent_on_replay(tmp_path):
    """BUG-FLYER-QA-001: a second consume_guest_order for the same project_id
    must return idempotent success — same order_id and status as the first
    call, no double-append to used_project_ids."""
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", message_id="cta-1", now=now,
    )
    activate_guest_order(
        state_path=state, order_id="GUEST0001",
        payment_reference="pi_idem_1", now=now,
    )
    reserve_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0050", now=now,
    )

    first = consume_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0050", now=now,
    )
    second = consume_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0050", now=now,
    )

    assert first.ok is True
    assert second.ok is True
    assert second.detail == ""
    assert second.reply_text == ""
    assert second.order_id == first.order_id
    assert second.status == first.status

    store = load_guest_order_store(state)
    order = store.find_order_by_id(first.order_id)
    assert order is not None
    assert order.used_project_ids == ["F0050"]  # not double-appended


def test_consume_guest_order_replay_on_different_chat_id(tmp_path):
    """BUG-FLYER-QA-001: cross-chat replay (sender comes back from a DM after
    first consume happened in a group chat). The chat_id fallback in
    _find_consumed_guest_order must resolve the prior consume."""
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", message_id="cta-2", now=now,
    )
    activate_guest_order(
        state_path=state, order_id="GUEST0001",
        payment_reference="pi_idem_2", now=now,
    )
    reserve_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0060", now=now,
    )

    first = consume_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0060", now=now,
    )
    second = consume_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="999999999@g.us",  # different chat
        project_id="F0060", now=now,
    )

    assert first.ok is True
    assert second.ok is True
    assert second.order_id == first.order_id


def test_consume_guest_order_replay_does_not_match_unrelated_project(tmp_path):
    """BUG-FLYER-QA-001 negative: idempotent replay only fires for the
    project_id that was actually consumed; a different project_id still
    returns reserved_guest_order_not_found."""
    state = tmp_path / "guest_orders.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    start_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", message_id="cta-3", now=now,
    )
    activate_guest_order(
        state_path=state, order_id="GUEST0001",
        payment_reference="pi_idem_3", now=now,
    )
    reserve_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0070", now=now,
    )
    consume_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0070", now=now,
    )

    other = consume_guest_order(
        state_path=state, sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net", project_id="F0071", now=now,
    )
    assert other.ok is False
    assert other.detail == "reserved_guest_order_not_found"


def test_guest_order_cli_dry_flow(tmp_path):
    state = tmp_path / "guest_orders.json"
    script = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "manage-flyer-guest-order"

    start = subprocess.run(
        [
            sys.executable,
            str(script),
            "--start",
            "--sender-phone", "+17329837841",
            "--chat-id", "17329837841@s.whatsapp.net",
            "--message-id", "cta-1",
            "--state-path", str(state),
            "--config-path", str(tmp_path / "missing.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"order_id": "GUEST0001"' in start.stdout

    activate = subprocess.run(
        [
            sys.executable,
            str(script),
            "--activate",
            "--order-id", "GUEST0001",
            "--payment-reference", "manual-test",
            "--state-path", str(state),
            "--config-path", str(tmp_path / "missing.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"status": "paid"' in activate.stdout

    reserve = subprocess.run(
        [
            sys.executable,
            str(script),
            "--reserve",
            "--sender-phone", "+17329837841",
            "--chat-id", "17329837841@s.whatsapp.net",
            "--project-id", "F0020",
            "--state-path", str(state),
            "--config-path", str(tmp_path / "missing.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"status": "reserved"' in reserve.stdout

    release = subprocess.run(
        [
            sys.executable,
            str(script),
            "--release",
            "--sender-phone", "+17329837841",
            "--chat-id", "17329837841@s.whatsapp.net",
            "--project-id", "F0020",
            "--state-path", str(state),
            "--config-path", str(tmp_path / "missing.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"status": "paid"' in release.stdout


def test_guest_order_cli_consume_idempotent_on_replay(tmp_path):
    """BUG-FLYER-QA-001 end-to-end: a second --consume for the same project_id
    must exit 0 through the subprocess layer (not just in-process). This
    catches regressions in the exit-code contract that the unit tests would
    miss — the hook layer keys on subprocess.returncode."""
    state = tmp_path / "guest_orders.json"
    script = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "manage-flyer-guest-order"
    config = tmp_path / "missing.yaml"
    common = ["--state-path", str(state), "--config-path", str(config)]

    subprocess.run(
        [sys.executable, str(script), "--start",
         "--sender-phone", "+17329837841",
         "--chat-id", "17329837841@s.whatsapp.net",
         "--message-id", "cta-1", *common],
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        [sys.executable, str(script), "--activate",
         "--order-id", "GUEST0001",
         "--payment-reference", "manual-test", *common],
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        [sys.executable, str(script), "--reserve",
         "--sender-phone", "+17329837841",
         "--chat-id", "17329837841@s.whatsapp.net",
         "--project-id", "F0090", *common],
        capture_output=True, text=True, check=True,
    )

    first = subprocess.run(
        [sys.executable, str(script), "--consume",
         "--sender-phone", "+17329837841",
         "--chat-id", "17329837841@s.whatsapp.net",
         "--project-id", "F0090", *common],
        capture_output=True, text=True,
    )
    assert first.returncode == 0
    assert '"ok": true' in first.stdout
    assert '"status": "used"' in first.stdout

    # Replay: same project_id; before BUG-001 fix this exited 2 with
    # detail=reserved_guest_order_not_found, which the hook layer treated
    # as a failed consume.
    second = subprocess.run(
        [sys.executable, str(script), "--consume",
         "--sender-phone", "+17329837841",
         "--chat-id", "17329837841@s.whatsapp.net",
         "--project-id", "F0090", *common],
        capture_output=True, text=True,
    )
    assert second.returncode == 0, f"replay should exit 0, got rc={second.returncode}, stderr={second.stderr}"
    assert '"ok": true' in second.stdout
    assert '"status": "used"' in second.stdout
    assert "reserved_guest_order_not_found" not in second.stdout


def test_guest_order_cli_rejects_activation_without_payment_reference(tmp_path):
    state = tmp_path / "guest_orders.json"
    script = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "manage-flyer-guest-order"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--start",
            "--sender-phone", "+17329837841",
            "--chat-id", "17329837841@s.whatsapp.net",
            "--message-id", "cta-1",
            "--state-path", str(state),
            "--config-path", str(tmp_path / "missing.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    activate = subprocess.run(
        [
            sys.executable,
            str(script),
            "--activate",
            "--order-id", "GUEST0001",
            "--state-path", str(state),
            "--config-path", str(tmp_path / "missing.yaml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert activate.returncode == 2
    doc = json.loads(activate.stdout)
    assert doc["detail"] == "payment_reference_required"
    assert load_guest_order_store(state).orders[0].status == "pending_payment"
