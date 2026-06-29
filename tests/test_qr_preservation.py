"""Deterministic QR-preservation verification (SHADOW) — issue #515.

Pure classification of whether a customer-supplied QR survives flyer
generation/regeneration. The DECODER is injected so the classification logic is
fully deterministic + testable WITHOUT a decoder library (none is installed in
CI or on the VPS). A real generate->decode round-trip runs only where a decoder
is available (skipped otherwise). SHADOW: the module never blocks output.
"""
from __future__ import annotations

import pytest

from agents.flyer.qr_preservation import (
    classify_qr_preservation,
    default_decode_result,
    verify_qr_preservation,
)

TARGET = "https://wa.me/17329837841"
OTHER = "https://evil.example/phish"


# ── pure classifier — the 5 required scenarios + edge cases ─────────────────

def test_classify_pass():
    r = classify_qr_preservation(payloads=[TARGET], regions_detected=1, supplied_target=TARGET)
    assert r["status"] == "pass"


def test_classify_missing():
    # supplied a QR, but the output has no QR region at all
    r = classify_qr_preservation(payloads=[], regions_detected=0, supplied_target=TARGET)
    assert r["status"] == "missing"


def test_classify_corrupted_undecodable():
    # a QR region is present in the output but cannot be decoded
    r = classify_qr_preservation(payloads=[], regions_detected=1, supplied_target=TARGET)
    assert r["status"] == "corrupted_undecodable"


def test_classify_swapped():
    # a QR decoded, but it is NOT the supplied target
    r = classify_qr_preservation(payloads=[OTHER], regions_detected=1, supplied_target=TARGET)
    assert r["status"] == "swapped"


def test_classify_wrong_channel():
    # per-channel QR map: the supplied (whatsapp) target landed on the instagram flyer
    cmap = {"whatsapp": TARGET, "instagram": "https://instagram.com/store"}
    r = classify_qr_preservation(
        payloads=[TARGET], regions_detected=1, supplied_target=TARGET,
        channel="instagram", channel_target_map=cmap)
    assert r["status"] == "wrong_channel"
    assert r["expected_target"] == "https://instagram.com/store"


def test_classify_right_channel_passes():
    cmap = {"whatsapp": TARGET, "instagram": "https://instagram.com/store"}
    r = classify_qr_preservation(
        payloads=[TARGET], regions_detected=1, supplied_target=TARGET,
        channel="whatsapp", channel_target_map=cmap)
    assert r["status"] == "pass"


def test_classify_no_supplied_qr_is_not_a_failure():
    r = classify_qr_preservation(payloads=[], regions_detected=0, supplied_target="")
    assert r["status"] == "no_supplied_qr"


# ── verify_qr_preservation with an INJECTED decoder (shadow, never raises) ───

def _fake_decoder(payloads, regions, name="fake"):
    def _fn(_image_path):
        return {"payloads": list(payloads), "regions_detected": regions, "decoder": name}
    return _fn


def test_verify_pass_with_injected_decoder(tmp_path):
    img = tmp_path / "out.png"; img.write_bytes(b"x")
    r = verify_qr_preservation(img, supplied_target=TARGET, decode_fn=_fake_decoder([TARGET], 1))
    assert r["status"] == "pass" and r["decoded"] == [TARGET]


def test_verify_decoder_unavailable_is_shadow_safe(tmp_path):
    img = tmp_path / "out.png"; img.write_bytes(b"x")
    # decoder == "none" => no decoder installed => shadow degrades, never blocks
    r = verify_qr_preservation(img, supplied_target=TARGET, decode_fn=_fake_decoder([], 0, name="none"))
    assert r["status"] == "decoder_unavailable"


def test_verify_never_raises_on_decoder_error(tmp_path):
    img = tmp_path / "out.png"; img.write_bytes(b"x")
    def _boom(_p):
        raise RuntimeError("decoder blew up")
    r = verify_qr_preservation(img, supplied_target=TARGET, decode_fn=_boom)
    assert r["status"] == "decoder_unavailable"  # graceful, no crash


# ── real generate->decode round-trip (only where a decoder is installed) ─────

def test_real_round_trip_or_graceful_when_no_decoder(tmp_path):
    qrcode = pytest.importorskip("qrcode")  # generation lib; absent in minimal CI -> skip
    img_path = tmp_path / "qr.png"
    qrcode.make(TARGET).save(str(img_path))
    dec = default_decode_result(img_path)
    if dec["decoder"] == "none":
        # No decoder library installed (current VPS / CI state) -> shadow-safe.
        assert dec["payloads"] == []
    else:
        # A real decoder is present -> it must decode the generated QR exactly.
        assert TARGET in dec["payloads"]
        r = verify_qr_preservation(img_path, supplied_target=TARGET)
        assert r["status"] == "pass"
