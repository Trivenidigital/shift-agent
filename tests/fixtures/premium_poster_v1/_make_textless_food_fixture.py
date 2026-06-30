"""Generate the DETERMINISTIC synthetic textless-food fixture for Slice C1.

This is a Pillow-drawn impressionistic warm food scene (a dark plate with
golden-brown fried-snack blobs, chutney bowls, warm bokeh, steam) — NO text, NO
logos, NO real photo (license-clear). It exists ONLY to prove the composer can
consume a real image file with cover-fit + scrims + readable text overlay
(Slice C1 is offline / no model call). Photorealistic appetizing food comes from
Hermes-directed generation in Slice C2.

Run: python tests/fixtures/premium_poster_v1/_make_textless_food_fixture.py
Output: tests/fixtures/premium_poster_v1/textless_food_scene.png  (1200x900 landscape
so the composer's portrait cover-fit actually crops).
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H = 1200, 900
OUT = Path(__file__).resolve().parent / "textless_food_scene.png"


def _lerp(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def main():
    img = Image.new("RGB", (W, H), (20, 12, 8))
    px = img.load()
    cx, cy = W / 2, H * 0.42
    maxd = math.hypot(W, H) / 2
    top, edge = (74, 44, 22), (16, 9, 6)  # warm center -> dark edge (radial)
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - cx, y - cy) / maxd
            px[x, y] = _lerp(top, edge, min(1.0, d * 1.15))

    # warm out-of-focus bokeh (depth), upper area
    bok = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bok)
    import random
    random.seed(7)
    for _ in range(34):
        bx, by = random.randint(0, W), random.randint(0, int(H * 0.55))
        br = random.randint(14, 46)
        a = random.randint(30, 90)
        bd.ellipse([bx - br, by - br, bx + br, by + br], fill=(240, 190, 110, a))
    img = Image.alpha_composite(img.convert("RGBA"), bok.filter(ImageFilter.GaussianBlur(9))).convert("RGB")
    draw = ImageDraw.Draw(img)

    # plate
    pr_x, pr_y, pr_w, pr_h = W * 0.5, H * 0.62, W * 0.40, H * 0.30
    draw.ellipse([pr_x - pr_w, pr_y - pr_h, pr_x + pr_w, pr_y + pr_h], fill=(30, 22, 18))
    draw.ellipse([pr_x - pr_w, pr_y - pr_h, pr_x + pr_w, pr_y + pr_h], outline=(150, 110, 60), width=6)
    inner = 0.86
    draw.ellipse([pr_x - pr_w * inner, pr_y - pr_h * inner, pr_x + pr_w * inner, pr_y + pr_h * inner],
                 fill=(44, 30, 22))

    # golden-brown fried-snack blobs on the plate
    random.seed(11)
    browns = [(200, 134, 46), (176, 110, 38), (158, 96, 30), (210, 150, 70)]
    for _ in range(16):
        ang = random.uniform(0, 2 * math.pi)
        rad = random.uniform(0, pr_w * 0.62)
        fx = pr_x + math.cos(ang) * rad
        fy = pr_y + math.sin(ang) * rad * 0.62
        fr = random.randint(34, 58)
        col = random.choice(browns)
        draw.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=col)
        # highlight
        draw.ellipse([fx - fr * 0.4, fy - fr * 0.5, fx + fr * 0.1, fy - fr * 0.1],
                     fill=_lerp(col, (255, 230, 170), 0.5))

    # chutney bowls (green + red) on the plate edge
    draw.ellipse([pr_x - 70, pr_y - 30, pr_x + 30, pr_y + 50], fill=(22, 16, 12))
    draw.ellipse([pr_x - 60, pr_y - 20, pr_x + 20, pr_y + 40], fill=(74, 122, 42))   # green chutney
    draw.ellipse([pr_x + 60, pr_y - 20, pr_x + 150, pr_y + 50], fill=(22, 16, 12))
    draw.ellipse([pr_x + 70, pr_y - 10, pr_x + 140, pr_y + 40], fill=(181, 66, 30))   # tamarind/red

    # cilantro flecks
    random.seed(3)
    for _ in range(40):
        gx = pr_x + random.uniform(-pr_w * 0.6, pr_w * 0.6)
        gy = pr_y + random.uniform(-pr_h * 0.5, pr_h * 0.5)
        draw.ellipse([gx, gy, gx + 6, gy + 4], fill=(90, 140, 50))

    # soft steam wisps + warm vignette
    steam = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(steam)
    for i, sx in enumerate((pr_x - 80, pr_x + 40, pr_x + 120)):
        for t in range(0, 220, 6):
            yy = pr_y - 120 - t
            xx = sx + math.sin(t / 22 + i) * 22
            sd.ellipse([xx - 10, yy - 10, xx + 10, yy + 10], fill=(255, 245, 225, 12))
    img = Image.alpha_composite(img.convert("RGBA"), steam.filter(ImageFilter.GaussianBlur(7))).convert("RGB")

    img.save(OUT)
    print("wrote", OUT, img.size)


if __name__ == "__main__":
    main()
