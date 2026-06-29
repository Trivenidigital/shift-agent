"""Deterministic QR-preservation verification (SHADOW) — issue #515.

Verifies whether a customer-supplied QR code survives flyer generation/
regeneration: given the QR payloads DECODED from the output flyer (plus how many
QR regions were detected), classify the outcome against the supplied target +
channel.

DESIGN
- The classifier (`classify_qr_preservation`) is PURE and fully deterministic —
  it takes already-decoded payloads, so it is testable without any decoder
  library (none is installed in CI or on the deployed flyer venv today).
- The decoder is INJECTED into `verify_qr_preservation` (`decode_fn`). The
  production adapter (`default_decode_result`) tries opencv then pyzbar via LAZY
  imports and DEGRADES to `decoder='none'` when neither is installed — so this
  module is import-safe and adds NO hard runtime dependency. On the current VPS
  it returns `decoder_unavailable`, which the shadow caller simply logs.
- SHADOW: nothing here blocks output; `verify_qr_preservation` never raises.

NOTE (scope, issue #515): wiring this into the live pipeline as a log-only
shadow check, and choosing/installing a real decoder library, are the explicit
follow-ups — this module is the deterministic verification core only.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

# The closed set of shadow outcomes.
QR_STATUS = (
    "pass",                  # decoded QR matches the expected target
    "missing",               # supplied a QR, but the output has no QR region
    "corrupted_undecodable",  # a QR region is present but cannot be decoded
    "swapped",               # a QR decoded, but not the supplied target
    "wrong_channel",         # supplied target decoded on the wrong channel's flyer
    "no_supplied_qr",        # customer supplied no QR — nothing to verify (not a failure)
    "decoder_unavailable",   # no decoder library installed — shadow logs, no verdict
)


def _result(status: str, expected: str, payloads, channel: str, detail: str) -> dict:
    return {
        "status": status,
        "expected_target": expected,
        "decoded": list(payloads),
        "channel": channel,
        "detail": detail,
    }


def classify_qr_preservation(
    *,
    payloads: Sequence[str],
    regions_detected: int,
    supplied_target: str,
    channel: str = "",
    channel_target_map: Optional[dict] = None,
) -> dict:
    """Pure classifier. `payloads` = QR strings decoded from the OUTPUT flyer;
    `regions_detected` = how many QR-like regions the decoder found (used to tell
    'missing' from 'corrupted_undecodable'). Returns a result dict; never raises."""
    supplied_target = (supplied_target or "").strip()
    payloads = [p for p in (payloads or []) if isinstance(p, str) and p.strip()]
    cmap = channel_target_map or {}
    expected = (cmap.get(channel, supplied_target) if cmap else supplied_target).strip()

    # Nothing to verify: no supplied target AND no expected target for this
    # channel (gate on the COMPUTED `expected`, not the raw inputs, so a
    # per-channel map with no entry for this channel is not a false 'missing').
    if not expected:
        return _result("no_supplied_qr", expected, payloads, channel,
                       "no customer QR supplied for this channel")
    if regions_detected <= 0 and not payloads:
        return _result("missing", expected, payloads, channel, "no QR region detected in output")
    if not payloads:
        return _result("corrupted_undecodable", expected, payloads, channel,
                       f"{regions_detected} QR region(s) detected but none decoded")
    if expected and expected in payloads:
        return _result("pass", expected, payloads, channel, "decoded QR matches expected target")
    # A QR decoded but is not the expected target. Distinguish a channel mismatch
    # (the SUPPLIED target landed on a flyer whose channel expects a different one)
    # from an outright swap.
    if cmap and supplied_target and supplied_target in payloads and expected != supplied_target:
        return _result("wrong_channel", expected, payloads, channel,
                       f"supplied target decoded on channel {channel!r} which expects {expected!r}")
    return _result("swapped", expected, payloads, channel,
                   "decoded QR does not match the supplied target")


def default_decode_result(image_path) -> dict:
    """Best-effort production decoder. Tries opencv, then pyzbar (both LAZY
    imports). Returns {payloads, regions_detected, decoder}. Degrades to
    decoder='none' when no decoder library is installed — import-safe, adds NO
    hard dependency."""
    # opencv (cv2.QRCodeDetector) — pip-only, no system package.
    try:
        import cv2  # type: ignore  # noqa: PLC0415

        img = cv2.imread(str(image_path))
        if img is not None:
            detector = cv2.QRCodeDetector()
            ok, decoded, points, _ = detector.detectAndDecodeMulti(img)
            payloads = [d for d in (decoded or []) if d]
            if points is not None and getattr(points, "shape", None):
                regions = int(points.shape[0])
            else:
                regions = len(payloads)
            return {"payloads": payloads, "regions_detected": regions or len(payloads), "decoder": "opencv"}
    except Exception:  # noqa: BLE001 — decoder is best-effort
        pass
    # pyzbar — needs the zbar system library.
    try:
        from pyzbar import pyzbar  # type: ignore  # noqa: PLC0415
        from PIL import Image  # type: ignore  # noqa: PLC0415

        codes = pyzbar.decode(Image.open(str(image_path)))
        payloads = [c.data.decode("utf-8", "replace") for c in codes
                    if str(getattr(c, "type", "")).upper() == "QRCODE"]
        return {"payloads": payloads, "regions_detected": len(codes), "decoder": "pyzbar"}
    except Exception:  # noqa: BLE001
        pass
    return {"payloads": [], "regions_detected": 0, "decoder": "none"}


def verify_qr_preservation(
    image_path,
    *,
    supplied_target: str,
    channel: str = "",
    channel_target_map: Optional[dict] = None,
    decode_fn: Optional[Callable] = None,
) -> dict:
    """Decode the output flyer's QR (via `decode_fn`, default = best-effort
    adapter) and classify preservation. SHADOW: returns a result dict; NEVER
    raises, NEVER blocks. When no decoder is available → 'decoder_unavailable'."""
    decode_fn = decode_fn or default_decode_result
    supplied = (supplied_target or "").strip()
    try:
        dec = decode_fn(image_path)
    except Exception as e:  # noqa: BLE001 — a broken decoder must not crash the shadow caller
        return _result("decoder_unavailable", supplied, [], channel, f"decoder error: {e}")
    if dec.get("decoder") == "none":
        return _result("decoder_unavailable", supplied, [], channel, "no QR decoder installed")
    return classify_qr_preservation(
        payloads=dec.get("payloads", []),
        regions_detected=int(dec.get("regions_detected", 0) or 0),
        supplied_target=supplied_target,
        channel=channel,
        channel_target_map=channel_target_map,
    )


__all__ = [
    "QR_STATUS",
    "classify_qr_preservation",
    "default_decode_result",
    "verify_qr_preservation",
]
