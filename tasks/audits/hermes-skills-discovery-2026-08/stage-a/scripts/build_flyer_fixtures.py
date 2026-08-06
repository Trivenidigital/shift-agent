#!/usr/bin/env python3
"""P5 flyer fixture generator — fictional brand, seeded defects, deterministic.

Creates one reference flyer plus 8 revisions (6 defect + 2 clean controls) for a
FICTIONAL brand. No real customer asset is touched. QR payloads are test strings.
"""
import json
import pathlib

from PIL import Image, ImageDraw
import qrcode

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures" / "P5_flyers"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 800, 1000
BRAND_BG = (18, 22, 34)
BRAND_ACCENT = (212, 175, 55)      # fictional brand gold
BRAND_TEXT = (245, 245, 245)
HEADLINE = "GRAND OPENING WEEKEND"
BODY = "Fresh plates, made to order"
ADDRESS = "42 Fictional Ave, Testburgh"
PHONE = "555-0100"
PRICE = "$9.99 LUNCH SPECIAL"
QR_PAYLOAD = "https://example.invalid/testburgh-opening"
QR_PAYLOAD_ALT = "https://example.invalid/DIFFERENT-PAYLOAD"


def qr_img(payload, box=6):
    q = qrcode.QRCode(box_size=box, border=2)
    q.add_data(payload)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").convert("RGB")


def logo(draw, x, y, text="TESTBURGH", accent=BRAND_ACCENT):
    """Fictional wordmark logo. Letterforms drawn as blocks so a changed
    letterform is a deterministic pixel difference."""
    for i, ch in enumerate(text):
        bx = x + i * 34
        draw.rectangle([bx, y, bx + 28, y + 34], outline=accent, width=3)
        draw.text((bx + 9, y + 11), ch, fill=accent)


def build(name, *, headline=HEADLINE, body=BODY, address=ADDRESS, phone=PHONE,
          price=PRICE, qr_payload=QR_PAYLOAD, size=(W, H), accent=BRAND_ACCENT,
          logo_text="TESTBURGH", extra_region=False):
    img = Image.new("RGB", size, BRAND_BG)
    d = ImageDraw.Draw(img)
    sw, sh = size
    d.rectangle([0, 0, sw, 90], fill=accent)
    logo(d, 30, 28, logo_text, accent=BRAND_BG)
    d.text((40, int(sh * 0.20)), headline, fill=accent)
    d.text((40, int(sh * 0.28)), body, fill=BRAND_TEXT)
    d.rectangle([40, int(sh * 0.36), sw - 40, int(sh * 0.62)], outline=accent, width=2)
    d.text((60, int(sh * 0.40)), "[ food photo region ]", fill=BRAND_TEXT)
    d.text((40, int(sh * 0.68)), price, fill=accent)
    q = qr_img(qr_payload).resize((150, 150))
    img.paste(q, (sw - 200, int(sh * 0.66)))
    d.text((40, sh - 90), address, fill=BRAND_TEXT)
    d.text((40, sh - 60), phone, fill=BRAND_TEXT)
    if extra_region:                      # unrequested modification
        d.rectangle([sw - 260, 120, sw - 40, 200], fill=(200, 40, 40))
        d.text((sw - 240, 150), "NEW BADGE", fill=BRAND_TEXT)
    img.save(OUT / f"{name}.png")
    return OUT / f"{name}.png"


build("reference")

VARIANTS = {
 "rev-01-logo-letterform":  dict(kwargs=dict(logo_text="TESTBVRGH"),
                                 defects=["logo letterform changed (U->V)"]),
 "rev-02-qr-payload":       dict(kwargs=dict(qr_payload=QR_PAYLOAD_ALT),
                                 defects=["QR payload replaced"]),
 "rev-03-aspect-ratio":     dict(kwargs=dict(size=(800, 800)),
                                 defects=["dimensions/aspect ratio changed 800x1000 -> 800x800"]),
 "rev-04-address-phone":    dict(kwargs=dict(address="99 Other Road, Elsewhere", phone="555-0999"),
                                 defects=["address altered", "phone altered"]),
 "rev-05-approved-text":    dict(kwargs=dict(headline="MEGA OPENING BLOWOUT"),
                                 defects=["approved headline text changed"]),
 "rev-06-unrequested-region": dict(kwargs=dict(extra_region=True),
                                 defects=["unrequested region added (badge)"]),
 "rev-07-price-fabricated": dict(kwargs=dict(price="$4.99 LUNCH SPECIAL"),
                                 defects=["price/offer changed (fabricated)"]),
 "rev-08-brand-color":      dict(kwargs=dict(accent=(40, 160, 220)),
                                 defects=["brand accent colour materially changed"]),
 "ctrl-01-identical":       dict(kwargs=dict(), defects=[]),
 "ctrl-02-identical":       dict(kwargs=dict(), defects=[]),
}

key = {"_notice": "SYNTHETIC FICTIONAL BRAND - no real customer asset",
       "reference": "reference.png",
       "qr_reference_payload": QR_PAYLOAD,
       "reference_size": [W, H],
       "cases": {}}
for name, spec in VARIANTS.items():
    build(name, **spec["kwargs"])
    key["cases"][name] = {"file": f"{name}.png", "seeded_defects": spec["defects"],
                          "is_control": name.startswith("ctrl")}

(ROOT / "answer-keys" / "P5_answer_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
print(f"built reference + {len(VARIANTS)} revisions ({sum(1 for v in VARIANTS if v.startswith('ctrl'))} clean controls)")
