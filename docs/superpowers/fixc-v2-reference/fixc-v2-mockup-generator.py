#!/usr/bin/env python3
"""Fix C v2 design-exploration mockups (THROWAWAY — no production code/state).

Renders 3 finished flyer directions for the F0175 brief, each = a real
food-hero textless background (OpenRouter gemini) + a deterministic text overlay
(PIL + vendored OFL fonts). Exact facts only; food-hero (no people); no coupon
cards for A. Outputs /tmp/fixc-v2/{A,B,C}.png.
"""
import base64, json, os, urllib.request, urllib.error
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = "/tmp/fixc-v2"; os.makedirs(OUT, exist_ok=True)
FONTS = "/opt/shift-agent/fonts"
W, H = 1080, 1350
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-3.1-flash-image-preview"
KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ---- exact facts (unchanged) ----
BRAND = "Lakshmi's Kitchen"
TITLE = "Weekend Specials"
SCHED = "Saturday & Sunday  ·  4 – 8 PM"
OFFER_L = "ANY ITEM"
OFFER_P = "$7.99"
ITEMS = ["Idli", "Dosa", "Vada", "Uttapam", "Pongal", "Sambar"]
PRICE = "$7.99"
ADDR = "90 Brybar Dr, St Johns FL"
PHONE = "+1 732-983-7841"

def F(name, size):
    return ImageFont.truetype(f"{FONTS}/{name}", size)

def gen_bg(prompt, path, size_tag="2K"):
    if os.path.exists(path):
        print("  reuse", path); return
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
        "max_tokens": 4096,
        "image_config": {"aspect_ratio": "4:5", "image_size": size_tag},
    }
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents"})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = json.loads(r.read().decode())
    url = body["choices"][0]["message"]["images"][0]["image_url"]["url"]
    raw = base64.b64decode(url.split(",", 1)[1])
    open(path, "wb").write(raw)
    print("  gen", path, len(raw), "bytes")

def cover(img, w, h):
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    x = (img.width - w) // 2; y = (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))

def scrim(base, box, c0, c1, vertical=True):
    """Gradient scrim (c0 alpha at start → c1 alpha at end) over box (x0,y0,x1,y1)."""
    x0, y0, x1, y1 = box; w, h = x1 - x0, y1 - y0
    grad = Image.new("L", (1, h) if vertical else (w, 1))
    for i in range(h if vertical else w):
        t = i / max(1, (h if vertical else w) - 1)
        grad.putpixel((0, i) if vertical else (i, 0), int(c0 + (c1 - c0) * t))
    grad = grad.resize((w, h))
    blk = Image.new("RGB", (w, h), (12, 10, 9))
    base.paste(blk, (x0, y0), grad)

def center_text(d, cx, y, text, font, fill, ls=0):
    if ls:
        text = (" " * 0).join(text)  # placeholder; letterspacing done manually below
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=fill)
    return font.size

def center_spaced(d, cx, y, text, font, fill, extra):
    total = sum(d.textlength(ch, font=font) + extra for ch in text) - extra
    x = cx - total / 2
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + extra

# ============================ A — EDITORIAL LUXURY ============================
def compose_A(bg_path):
    base = cover(Image.open(bg_path), W, H)
    # top + bottom scrims so text reads on food
    scrim(base, (0, 0, W, 470), 175, 0)
    scrim(base, (0, 760, W, H), 0, 205)
    d = ImageDraw.Draw(base)
    GOLD = (208, 178, 110); IVORY = (244, 240, 232)
    # emblem ring + monogram
    cx = W // 2
    d.ellipse((cx - 34, 56, cx + 34, 124), outline=GOLD, width=3)
    mono = F("PlayfairDisplay-Black.ttf", 38)
    center_text(d, cx, 70, "LK", mono, GOLD)
    # brand small-caps
    center_spaced(d, cx, 138, BRAND.upper(), F("CormorantGaramond-SemiBold.ttf", 34), IVORY, 6)
    # decorative rules around title
    d.line((cx - 250, 250, cx - 90, 250), fill=GOLD, width=2)
    d.line((cx + 90, 250, cx + 250, 250), fill=GOLD, width=2)
    d.ellipse((cx - 4, 246, cx + 4, 254), fill=GOLD)
    # title (elegant display)
    center_text(d, cx, 210, TITLE, F("PlayfairDisplay-Bold.ttf", 78), IVORY)
    center_spaced(d, cx, 300, SCHED.upper(), F("CormorantGaramond-SemiBold.ttf", 26), IVORY, 3)
    # gold offer seal (right, over food)
    sx, sy, sr = W - 175, 600, 96
    d.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=(120, 24, 28), outline=GOLD, width=4)
    center_spaced(d, sx, sy - 58, OFFER_L, F("CormorantGaramond-SemiBold.ttf", 24), GOLD, 4)
    center_text(d, sx, sy - 28, OFFER_P, F("PlayfairDisplay-Black.ttf", 72), IVORY)
    center_spaced(d, sx, sy + 46, "EACH", F("CormorantGaramond-SemiBold.ttf", 22), GOLD, 4)
    # menu as a refined TWO-COLUMN TYPOGRAPHIC LIST with dot leaders (NO cards)
    mfont = F("CormorantGaramond-SemiBold.ttf", 40)
    pfont = F("PlayfairDisplay-Bold.ttf", 34)
    d.line((90, 905, W - 90, 905), fill=GOLD, width=1)
    center_spaced(d, cx, 925, "SIX SOUTH-INDIAN FAVORITES", F("CormorantGaramond-SemiBold.ttf", 24), GOLD, 4)
    col_x = [120, 580]; col_w = 380
    for i, it in enumerate(ITEMS):
        cxn = col_x[i % 2]; row = i // 2
        yy = 985 + row * 78
        d.text((cxn, yy), it, font=mfont, fill=IVORY)
        pw = d.textlength(PRICE, font=pfont)
        d.text((cxn + col_w - pw, yy + 2), PRICE, font=pfont, fill=GOLD)
        # dot leader
        nw = d.textlength(it, font=mfont)
        lx = cxn + nw + 14; rx = cxn + col_w - pw - 14; yy2 = yy + 44
        x = lx
        while x < rx:
            d.ellipse((x, yy2, x + 2, yy2 + 2), fill=(150, 140, 120)); x += 12
    # footer
    d.line((90, 1238, W - 90, 1238), fill=GOLD, width=1)
    center_spaced(d, cx, 1258, f"{ADDR}    |    {PHONE}", F("CormorantGaramond-SemiBold.ttf", 26), IVORY, 1)
    base.save(f"{OUT}/A.png"); print("  saved A")

# ====================== B — SOCIAL / RESTAURANT PROMO ========================
def compose_B(bg_path):
    base = cover(Image.open(bg_path), W, H)
    scrim(base, (0, 0, W, 360), 165, 0)
    # bold dark band lower-center for the massive offer
    scrim(base, (0, 560, W, 1010), 0, 225)
    scrim(base, (0, 1010, W, H), 225, 235)
    d = ImageDraw.Draw(base)
    YEL = (255, 209, 64); WHITE = (255, 255, 255); RED = (214, 40, 40)
    cx = W // 2
    # brand top
    center_spaced(d, cx, 56, BRAND.upper(), F("Montserrat-Bold.ttf", 34), WHITE, 4)
    center_text(d, cx, 120, TITLE.upper(), F("Montserrat-ExtraBold.ttf", 84), YEL)
    # MASSIVE offer
    center_spaced(d, cx, 600, OFFER_L, F("Montserrat-Bold.ttf", 56), WHITE, 8)
    center_text(d, cx, 660, OFFER_P, F("Montserrat-ExtraBold.ttf", 250), YEL)
    # red ribbon for schedule
    d.rectangle((cx - 330, 935, cx + 330, 992), fill=RED)
    center_spaced(d, cx, 948, SCHED.upper(), F("Montserrat-Bold.ttf", 26), WHITE, 1)
    # bold compact menu strip (one line of items)
    items_line = "  •  ".join(ITEMS)
    center_text(d, cx, 1050, items_line, F("Montserrat-Bold.ttf", 36), WHITE)
    center_spaced(d, cx, 1110, "EVERY ITEM JUST $7.99", F("Montserrat-ExtraBold.ttf", 34), YEL, 1)
    # contact footer bar
    d.rectangle((0, 1270, W, H), fill=(20, 18, 16))
    center_spaced(d, cx, 1292, f"{ADDR}   |   {PHONE}", F("Montserrat-SemiBold.ttf", 26), WHITE, 1)
    base.save(f"{OUT}/B.png"); print("  saved B")

# ========================= C — MODERN FOOD BRAND ============================
def compose_C(bg_path):
    base = cover(Image.open(bg_path), W, H)
    # clean lower panel (modern, airy) — solid soft card over lower 42%
    panel = Image.new("RGB", (W, H), (250, 249, 246))
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask); md.rectangle((0, 800, W, H), fill=255)
    base.paste(panel, (0, 0), mask)
    scrim(base, (0, 0, W, 240), 150, 0)
    d = ImageDraw.Draw(base)
    INK = (28, 28, 30); ACCENT = (198, 90, 50); MUTE = (120, 120, 125); WHITE = (255, 255, 255)
    cx = W // 2
    # brand top (over food)
    center_spaced(d, cx, 60, BRAND.upper(), F("Montserrat-SemiBold.ttf", 30), WHITE, 6)
    # title in clean lower panel
    center_text(d, cx, 845, TITLE, F("Montserrat-ExtraBold.ttf", 70), INK)
    center_spaced(d, cx, 930, SCHED.upper(), F("Montserrat-SemiBold.ttf", 24), MUTE, 2)
    # offer tag (clean pill)
    pill_w = 300
    d.rounded_rectangle((cx - pill_w // 2, 975, cx + pill_w // 2, 1035), radius=30, fill=ACCENT)
    center_spaced(d, cx, 990, f"{OFFER_L}  {OFFER_P}", F("Montserrat-Bold.ttf", 30), WHITE, 1)
    # airy menu list — two columns, thin dividers, no boxes
    mfont = F("Montserrat-SemiBold.ttf", 34); pfont = F("Montserrat-Bold.ttf", 30)
    col_x = [140, 590]; col_w = 360
    for i, it in enumerate(ITEMS):
        cxn = col_x[i % 2]; row = i // 2; yy = 1085 + row * 70
        d.text((cxn, yy), it, font=mfont, fill=INK)
        pw = d.textlength(PRICE, font=pfont)
        d.text((cxn + col_w - pw, yy + 2), PRICE, font=pfont, fill=ACCENT)
        d.line((cxn, yy + 52, cxn + col_w, yy + 52), fill=(225, 223, 218), width=1)
    # footer
    center_spaced(d, cx, 1300, f"{ADDR}    {PHONE}", F("Montserrat-SemiBold.ttf", 24), MUTE, 1)
    base.save(f"{OUT}/C.png"); print("  saved C")

BG = {
 "A": "Editorial fine-dining food photography: a single hero plate of crispy golden masala dosa with coconut chutney and a small bowl of sambar, dramatic warm side lighting, rich shallow depth of field, dark elegant marble surface, generous calm dark negative space along the top and the bottom third for text, no text, no words, no people, no hands, no logos, luxury food-magazine quality, vertical 4:5.",
 "B": "Bold vibrant mouth-watering food advertising photo: an abundant overhead spread of South Indian dishes — dosa, idli, vada, uttapam — bright saturated punchy lighting, full-bleed, high energy and appetite appeal, keep the lower-center area visually simpler/darker for a large text overlay, no text, no words, no people, no hands, no logos, vertical 4:5.",
 "C": "Minimal modern food photography: a clean close-up of a single dosa and two small accompaniment bowls on a light neutral linen background, soft natural daylight, lots of clean negative space, contemporary food-brand aesthetic, the lower half calm and uncluttered, no text, no words, no people, no hands, no logos, vertical 4:5.",
}

if __name__ == "__main__":
    for k, p in BG.items():
        print("BG", k); gen_bg(p, f"{OUT}/bg_{k}.png")
    compose_A(f"{OUT}/bg_A.png")
    compose_B(f"{OUT}/bg_B.png")
    compose_C(f"{OUT}/bg_C.png")
    print("DONE", os.listdir(OUT))
