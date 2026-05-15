"""Deterministic flyer rendering for Hermes Flyer Studio.

This module must import cleanly inside the Hermes venv even when Pillow is not
installed there. Rendering uses local Pillow when available, otherwise it
delegates to `/usr/bin/python3` where `python3-pil` can be installed by ops.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request

from schemas import FlyerAsset, FlyerCustomerStore, FlyerOutputFormat, FlyerProject


class FlyerRenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderedAssetSpec:
    path: Path
    kind: str
    output_format: str
    width: int
    height: int
    concept_id: str = ""


@dataclass(frozen=True)
class RenderedAssetQuality:
    ok: bool
    blockers: list[str]
    warnings: list[str]
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0


PALETTES = {
    "C1": {"bg": [252, 244, 226], "primary": [130, 28, 42], "accent": [237, 171, 44], "ink": [39, 39, 39], "soft": [255, 255, 255]},
    "C2": {"bg": [238, 248, 246], "primary": [0, 106, 103], "accent": [230, 91, 63], "ink": [25, 43, 47], "soft": [255, 255, 255]},
    "C3": {"bg": [242, 241, 255], "primary": [54, 58, 122], "accent": [240, 111, 78], "ink": [30, 32, 50], "soft": [255, 255, 255]},
}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/Nirmala.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SEC = 180
def _flyer_state_root() -> Path:
    return Path(os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer/"))


def _customers_path() -> Path:
    return _flyer_state_root() / "customers.json"


CUSTOMERS_PATH = Path("/opt/shift-agent/state/flyer/customers.json")
DETERMINISTIC_MODEL_NAMES = {"", "deterministic-renderer", "pillow", "local-pillow"}


def _require_ready(project: FlyerProject) -> None:
    missing = project.fields.missing_required_fields()
    if missing:
        raise FlyerRenderError("missing required flyer fields: " + ", ".join(missing))


def _load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        return Image, ImageDraw, ImageFont
    except Exception:
        return None


def _font(ImageFont, size: int, *, bold: bool = False):
    candidates = list(FONT_CANDIDATES)
    if bold:
        candidates.insert(0, "C:/Windows/Fonts/arialbd.ttf")
        candidates.insert(0, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _read_env_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_path = Path(os.environ.get("SHIFT_AGENT_ENV_PATH", "/opt/shift-agent/.env"))
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() != name:
                continue
            return raw.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) == 1 and len(lines[0]) > 28:
        return textwrap.wrap(lines[0], width=28)
    return lines


def _aspect_ratio(size: tuple[int, int] | None) -> str:
    if size is None:
        return "4:5"
    width, height = size
    if width == height:
        return "1:1"
    ratio = width / height
    known = {
        "4:5": 4 / 5,
        "9:16": 9 / 16,
        "2:3": 2 / 3,
        "3:4": 3 / 4,
    }
    return min(known, key=lambda key: abs(known[key] - ratio))


def _telugu_hint(project: FlyerProject) -> str:
    if project.fields.preferred_language not in {"te", "mixed"}:
        return ""
    name = project.fields.event_or_business_name or ""
    hints = []
    if "ugadi" in name.lower():
        hints.append("Use tasteful Telugu script such as \"ఉగాది శుభాకాంక్షలు\" as an accent, while keeping the main title readable.")
    hints.append("Do not render missing-glyph boxes. If Telugu text is used, it must be valid Telugu script.")
    return " ".join(hints)


def _sanitize_visual_context(text: str) -> str:
    text = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[phone]", text or "")
    text = re.sub(r"\$\s*\d+(?:\.\d{2})?", "[price]", text)
    text = re.sub(r"\b\d{1,4}(?:[-/:]\d{1,4}){1,2}\b", "[date/time]", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "[number]", text)
    return text[:700]


def _schedule_hint(project: FlyerProject) -> str:
    text = project.fields.notes.strip() or project.raw_request.strip()
    if not text:
        return ""
    schedule_match = re.search(
        r"((?:starts?|starting)\s+from\s+.+?(?:saturday|sunday|weekend).+?)(?:\.|$)",
        text,
        flags=re.IGNORECASE,
    )
    if schedule_match:
        return schedule_match.group(1).strip(" .")
    weekend_match = re.search(
        r"(.{0,80}(?:saturday|sunday|weekend).{0,80})(?:\.|$)",
        text,
        flags=re.IGNORECASE,
    )
    return weekend_match.group(1).strip(" .") if weekend_match else ""


def _brand_asset_prompt(project: FlyerProject) -> str:
    active_assets = [*_active_brand_assets(project), *_project_reference_assets(project)]
    if not active_assets:
        return "- none"
    return "\n".join(
        f"- {asset.kind}: {asset.asset_id} ({Path(asset.path).name}) notes={getattr(asset, 'notes', '') or 'none'}"
        for asset in active_assets[-4:]
    )


def _active_brand_assets(project: FlyerProject):
    if "FLYER_CUSTOMERS_PATH" in os.environ:
        customers_path = Path(os.environ["FLYER_CUSTOMERS_PATH"])
    elif "FLYER_STATE_ROOT" in os.environ:
        customers_path = _customers_path()
    else:
        customers_path = CUSTOMERS_PATH
    if not customers_path.exists():
        return []
    try:
        store = FlyerCustomerStore.model_validate(json.loads(customers_path.read_text(encoding="utf-8")))
    except Exception:
        return []
    customer = store.find_customer_by_phone(str(project.customer_phone))
    if not customer:
        return []
    return [asset for asset in customer.brand_assets if asset.active and Path(asset.path).exists()]


def _project_reference_assets(project: FlyerProject):
    return [
        asset for asset in project.assets
        if asset.kind in {"logo", "reference_image"} and Path(asset.path).exists()
    ]


def _image_message_content(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None):
    prompt = _image_prompt(project, concept_id=concept_id, output_format=output_format, size=size)
    parts: list[dict] = [{"type": "text", "text": prompt}]
    brand_assets = _active_brand_assets(project)
    refs = _project_reference_assets(project)
    selected_assets = [*brand_assets[-1:], *refs[-1:]] if refs else [*brand_assets[-2:]]
    for asset in selected_assets:
        path = Path(asset.path)
        mime = asset.mime_type or mimetypes.guess_type(str(path))[0] or "image/png"
        if not mime.startswith("image/"):
            continue
        data_url = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts if len(parts) > 1 else prompt


def _image_prompt(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None) -> str:
    style_by_concept = {
        "C1": "premium ethnic grocery poster, bold festive food photography, marigold and mango-leaf accents, polished retail hierarchy",
        "C2": "warm cultural celebration flyer, South Indian festival motifs, elegant food spread, refined community-event look",
        "C3": "modern social-media creative, crisp editorial layout, bright festive palette, restaurant-quality promotional design",
    }
    details = project.fields.notes.strip() or project.raw_request.strip()
    revisions = [r.request_text for r in project.revisions[-4:]]
    revision_block = "\n".join(f"- {r}" for r in revisions) if revisions else "- none"
    reference_instruction = _reference_preservation_instruction(project)
    return f"""Create a professional SMB flyer/poster visual background for WhatsApp delivery.

Design direction: {style_by_concept.get(concept_id, style_by_concept["C1"])}.
Customer style notes: {project.fields.style_preference or "festive, clean, professional"}.
Output format: {output_format}; aspect ratio {_aspect_ratio(size)}.

Visual context only, sanitized. Do not render readable words, numbers, dates, prices, addresses, phone numbers, or flyer copy:
- theme/category: {_sanitize_visual_context(project.fields.event_or_business_name or project.raw_request or "local SMB promotion")}
- style: {_sanitize_visual_context(project.fields.style_preference or "festive, clean, professional")}

Menu/offer context for imagery only, sanitized:
{_sanitize_visual_context(details) or "- none"}

Customer brand assets to honor:
{_brand_asset_prompt(project)}

Revision notes to honor:
{_sanitize_visual_context(revision_block)}

Reference/template policy:
{reference_instruction}

Quality bar:
- Looks like a paid local marketing designer made it, not a generic template.
- Strong hierarchy, appetizing food visuals, festival warmth, no empty beige space.
- High contrast and readable on a phone screen.
- Leave clean space for a server-rendered facts panel; do not render readable dates, phone numbers, prices, venue text, QR codes, title copy, or placeholder copy anywhere.
- If customer brand assets are listed, preserve the business identity and use the active logo/template as the visual reference.
- If an uploaded reference image/template is attached, preserve its visual identity and offer category but neutralize stale readable facts where possible; exact text is handled by the compositor.
- If there is no one-time date, present the recurring schedule clearly instead of inventing a date.
- Avoid QR codes, fake logos, watermarks, unreadable microtext, and placeholder glyph boxes.
{_telugu_hint(project)}
"""


def _reference_preservation_instruction(project: FlyerProject) -> str:
    if not [*_active_brand_assets(project), *_project_reference_assets(project)]:
        return "- none"
    return (
        "- Use the attached image/logo/template as source of truth for visual identity.\n"
        "- Do not redesign from scratch.\n"
        "- Preserve business/logo identity, layout feel, cuisine/event category, and visual mood.\n"
        "- Latest revision facts override older text. Do not keep stale readable prices, dates, phone numbers, or addresses."
    )


def inspect_rendered_asset(path: Path | str, *, expected_width: int, expected_height: int, mime_type: str) -> RenderedAssetQuality:
    path = Path(path)
    blockers: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return RenderedAssetQuality(False, ["missing"], warnings)
    size_bytes = path.stat().st_size
    min_size = 500 if mime_type == "application/pdf" else 1000
    if size_bytes < min_size:
        blockers.append(f"tiny file: {size_bytes} bytes")
    if mime_type == "application/pdf":
        try:
            header = path.read_bytes()[:4]
        except OSError as e:
            blockers.append(f"unreadable pdf: {e}")
            header = b""
        if header != b"%PDF":
            blockers.append("pdf header missing")
        return RenderedAssetQuality(not blockers, blockers, warnings, expected_width, expected_height, size_bytes)
    pil = _load_pillow()
    if pil is None:
        warnings.append("pillow unavailable; skipped pixel inspection")
        return RenderedAssetQuality(not blockers, blockers, warnings, None, None, size_bytes)
    Image, _ImageDraw, _ImageFont = pil
    try:
        with Image.open(path) as img:
            width, height = img.size
            if (width, height) != (expected_width, expected_height):
                blockers.append(f"dimensions {width}x{height} != {expected_width}x{expected_height}")
            sample = img.convert("RGB").resize((32, 32))
            colors = sample.getcolors(maxcolors=4096) or []
            extrema = sample.getextrema()
            variance = sum(channel[1] - channel[0] for channel in extrema)
            if len(colors) < 4 or variance < 20:
                blockers.append("blank or low-variance image")
            return RenderedAssetQuality(not blockers, blockers, warnings, width, height, size_bytes)
    except Exception as e:
        blockers.append(f"image open failed: {e}")
        return RenderedAssetQuality(False, blockers, warnings, None, None, size_bytes)


def _critical_lines(project: FlyerProject) -> list[str]:
    lines = [project.fields.event_or_business_name or "Flyer"]
    schedule = _schedule_hint(project)
    if project.fields.event_date:
        lines.append(f"Date: {project.fields.event_date}")
    elif schedule:
        lines.append(f"Schedule: {schedule}")
    if project.fields.event_time:
        lines.append(f"Time: {project.fields.event_time}")
    if project.fields.venue_or_location:
        lines.append(f"Location: {project.fields.venue_or_location}")
    if project.fields.contact_info:
        lines.append(f"Contact: {project.fields.contact_info}")
    details = (project.fields.notes or project.raw_request or "").strip()
    if details:
        compact = re.sub(r"\s+", " ", details)
        for part in re.split(r";|\n", compact)[:5]:
            part = part.strip(" .")
            if part and part not in lines:
                lines.append(part[:120])
    return lines[:7]


def apply_critical_text_overlay(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> None:
    pil = _load_pillow()
    if pil is None:
        raise FlyerRenderError("Pillow is required for critical text overlay")
    Image, ImageDraw, ImageFont = pil
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = img.convert("RGB")
        if img.size != size:
            img = img.resize(size)
        draw = ImageDraw.Draw(img, "RGBA")
        width, height = size
        margin = max(24, int(width * 0.035))
        panel_h = min(int(height * 0.44), max(int(height * 0.22), 58 + len(_critical_lines(project)) * max(24, int(width * 0.024))))
        y0 = height - panel_h - margin
        draw.rounded_rectangle((margin, y0, width - margin, height - margin), radius=18, fill=(12, 16, 24, 218), outline=(255, 196, 58, 240), width=3)
        title_font = _font(ImageFont, max(30, int(width * 0.045)), bold=True)
        body_font = _font(ImageFont, max(20, int(width * 0.025)))
        y = y0 + int(margin * 0.75)
        lines = _critical_lines(project)
        for idx, line in enumerate(lines):
            font = title_font if idx == 0 else body_font
            fill = (255, 214, 79, 255) if idx == 0 else (255, 255, 255, 245)
            wrapped = _wrap(draw, line, font, width - margin * 3)
            for wrapped_line in wrapped[:2 if idx == 0 else 1]:
                if y + font.size > height - margin:
                    raise FlyerRenderError("critical text overlay does not fit")
                draw.text((margin + 18, y), wrapped_line, font=font, fill=fill)
                y += int(font.size * 1.18)
        img.save(target, format="PNG", optimize=True)


OVERLAY_RENDERER = r'''
import json, re, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
spec=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
src=Path(spec["source"]); target=Path(spec["target"]); size=tuple(spec["size"]); lines=spec["lines"]
def font(sz,bold=False):
    c=["/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf","/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    if bold: c.insert(0,"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for p in c:
        try:
            if Path(p).exists(): return ImageFont.truetype(p, sz)
        except OSError: pass
    return ImageFont.load_default()
def wrap(draw,text,f,maxw):
    words=(text or "").split(); out=[]; cur=""
    for w in words:
        cand=(cur+" "+w).strip(); box=draw.textbbox((0,0),cand,font=f)
        if box[2]-box[0] <= maxw or not cur: cur=cand
        else: out.append(cur); cur=w
    if cur: out.append(cur)
    return out
with Image.open(src) as img:
    img=img.convert("RGB")
    if img.size != size: img=img.resize(size)
    draw=ImageDraw.Draw(img,"RGBA"); width,height=size; margin=max(24,int(width*.035))
    panel_h=min(int(height*.44),max(int(height*.22),58+len(lines)*max(24,int(width*.024))))
    y0=height-panel_h-margin
    draw.rounded_rectangle((margin,y0,width-margin,height-margin), radius=18, fill=(12,16,24,218), outline=(255,196,58,240), width=3)
    title_font=font(max(30,int(width*.045)), True); body_font=font(max(20,int(width*.025)))
    y=y0+int(margin*.75)
    for idx,line in enumerate(lines):
        f=title_font if idx==0 else body_font; fill=(255,214,79,255) if idx==0 else (255,255,255,245)
        for wrapped in wrap(draw,line,f,width-margin*3)[:2 if idx==0 else 1]:
            if y+f.size > height-margin:
                raise SystemExit("critical text overlay does not fit")
            draw.text((margin+18,y), wrapped, font=f, fill=fill)
            y += int(f.size*1.18)
    target.parent.mkdir(parents=True, exist_ok=True)
    img.save(target, format="PNG", optimize=True)
'''


def _apply_critical_text_overlay(project: FlyerProject, source: Path | str, target: Path | str, *, size: tuple[int, int], output_format: str) -> None:
    try:
        apply_critical_text_overlay(project, source, target, size=size, output_format=output_format)
        return
    except FlyerRenderError as e:
        if "Pillow is required" not in str(e) or not Path("/usr/bin/python3").exists():
            raise
    spec = {
        "source": str(source),
        "target": str(target),
        "size": list(size),
        "output_format": output_format,
        "lines": _critical_lines(project),
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        spec_path = fh.name
    try:
        proc = subprocess.run(["/usr/bin/python3", "-c", OVERLAY_RENDERER, spec_path], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise FlyerRenderError(f"critical text overlay failed: {proc.stderr.strip() or proc.stdout.strip()}")
    finally:
        Path(spec_path).unlink(missing_ok=True)


def _decode_data_url(data_url: str) -> bytes:
    if "," not in data_url:
        raise FlyerRenderError("image response missing data URL comma")
    _prefix, encoded = data_url.split(",", 1)
    try:
        return base64.b64decode(encoded)
    except Exception as e:
        raise FlyerRenderError(f"image response base64 decode failed: {e}") from e


def _openrouter_image_bytes(project: FlyerProject, *, concept_id: str, output_format: str, size: tuple[int, int] | None, model: str, quality: str) -> bytes:
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key:
        raise FlyerRenderError("OPENROUTER_API_KEY is missing")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _image_message_content(project, concept_id=concept_id, output_format=output_format, size=size)}],
        "modalities": ["image", "text"],
        "stream": False,
        "image_config": {
            "aspect_ratio": _aspect_ratio(size),
            "image_size": "2K" if quality == "high" else "1K",
        },
    }
    req = urllib.request.Request(
        OPENROUTER_IMAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents",
            "X-Title": "Hermes Flyer Studio",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:1000]
        raise FlyerRenderError(f"OpenRouter image HTTP {e.code}: {err}") from e
    except urllib.error.URLError as e:
        raise FlyerRenderError(f"OpenRouter image connection failed: {e.reason}") from e
    doc = json.loads(body)
    choices = doc.get("choices") or []
    if not choices:
        raise FlyerRenderError(f"OpenRouter image response had no choices: {body[:500]}")
    images = choices[0].get("message", {}).get("images") or []
    if not images:
        raise FlyerRenderError(f"OpenRouter image response had no images: {body[:500]}")
    url = images[0].get("image_url", {}).get("url") or ""
    if not url.startswith("data:image/"):
        raise FlyerRenderError("OpenRouter image response did not include base64 image data")
    return _decode_data_url(url)


def _write_generated_image(raw: bytes, path: Path, *, size: tuple[int, int] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if size is None:
        pil = _load_pillow()
        if pil is None:
            raise FlyerRenderError("Pillow is required to convert generated image to PDF")
        Image, _ImageDraw, _ImageFont = pil
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(raw)
            tmp_path = Path(fh.name)
        try:
            with Image.open(tmp_path) as img:
                img.convert("RGB").save(path, "PDF", resolution=150.0)
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        path.write_bytes(raw)


def _raw_background_path(path: Path) -> Path:
    if path.suffix.lower() == ".png":
        return path.with_name(f"{path.stem}.raw.png")
    return path.with_name(f"{path.stem}.raw-background.png")


EXPORT_FROM_SOURCE_RENDERER = r'''
import sys
from pathlib import Path
from PIL import Image
src=Path(sys.argv[1]); out=Path(sys.argv[2]); width=int(sys.argv[3]); height=int(sys.argv[4]); is_pdf=sys.argv[5]=="1"
out.parent.mkdir(parents=True, exist_ok=True)
with Image.open(src) as img:
    img=img.convert("RGB")
    if is_pdf:
        img.save(out, "PDF", resolution=150.0)
    else:
        src_ratio=img.width/img.height
        dst_ratio=width/height
        if src_ratio > dst_ratio:
            new_w=int(img.height*dst_ratio)
            left=(img.width-new_w)//2
            img=img.crop((left,0,left+new_w,img.height))
        elif src_ratio < dst_ratio:
            new_h=int(img.width/dst_ratio)
            top=(img.height-new_h)//2
            img=img.crop((0,top,img.width,top+new_h))
        img=img.resize((width,height), Image.Resampling.LANCZOS)
        img.save(out, format="PNG", optimize=True)
'''


def _export_from_source_image(source: Path, path: Path, *, size: tuple[int, int] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pil = _load_pillow()
    if pil is not None:
        Image, _ImageDraw, _ImageFont = pil
        with Image.open(source) as img:
            img = img.convert("RGB")
            if size is None:
                img.save(path, "PDF", resolution=150.0)
                return
            width, height = size
            src_ratio = img.width / img.height
            dst_ratio = width / height
            if src_ratio > dst_ratio:
                new_w = int(img.height * dst_ratio)
                left = (img.width - new_w) // 2
                img = img.crop((left, 0, left + new_w, img.height))
            elif src_ratio < dst_ratio:
                new_h = int(img.width / dst_ratio)
                top = (img.height - new_h) // 2
                img = img.crop((0, top, img.width, top + new_h))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
            img.save(path, format="PNG", optimize=True)
            return
    if not Path("/usr/bin/python3").exists():
        raise FlyerRenderError("Pillow is unavailable and /usr/bin/python3 fallback is missing")
    width, height = size or (1275, 1650)
    proc = subprocess.run(
        ["/usr/bin/python3", "-c", EXPORT_FROM_SOURCE_RENDERER, str(source), str(path), str(width), str(height), "1" if size is None else "0"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise FlyerRenderError(f"source image export failed: {proc.stderr.strip()}")


def _draw_flyer_pil(project: FlyerProject, *, concept_id: str, size: tuple[int, int], pil_modules):
    Image, ImageDraw, ImageFont = pil_modules
    _require_ready(project)
    width, height = size
    palette = PALETTES.get(concept_id, PALETTES["C1"])
    img = Image.new("RGB", size, tuple(palette["bg"]))
    draw = ImageDraw.Draw(img)
    margin = int(width * 0.07)
    title_font = _font(ImageFont, max(46, int(width * 0.074)), bold=True)
    subtitle_font = _font(ImageFont, max(28, int(width * 0.038)), bold=True)
    small_font = _font(ImageFont, max(20, int(width * 0.024)))

    draw.rectangle((0, 0, width, int(height * 0.19)), fill=tuple(palette["primary"]))
    draw.rectangle((0, int(height * 0.19), width, int(height * 0.205)), fill=tuple(palette["accent"]))
    for i in range(9):
        cx = int(width * (0.08 + i * 0.105))
        cy = int(height * 0.16)
        r = int(width * 0.025)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(palette["accent"]))

    language_label = {"te": "Telugu", "hi": "Hindi", "es": "Spanish", "mixed": "Multilingual", "other": "Local language"}.get(project.fields.preferred_language, "English")
    draw.text((margin, int(height * 0.045)), language_label.upper(), font=small_font, fill=tuple(palette["soft"]))

    y = int(height * 0.245)
    for line in _wrap(draw, project.fields.event_or_business_name or "", title_font, width - margin * 2)[:3]:
        draw.text((margin, y), line, font=title_font, fill=tuple(palette["primary"]))
        y += int(title_font.size * 1.08)
    if project.fields.style_preference:
        for line in _wrap(draw, project.fields.style_preference, small_font, width - margin * 2)[:2]:
            draw.text((margin, y + 8), line, font=small_font, fill=tuple(palette["ink"]))
            y += int(small_font.size * 1.2)

    card_top = max(y + 28, int(height * 0.48))
    card_bottom = int(height * 0.82)
    draw.rounded_rectangle((margin, card_top, width - margin, card_bottom), radius=18, fill=tuple(palette["soft"]), outline=tuple(palette["accent"]), width=4)
    facts = [
        ("DATE", project.fields.event_date or ""),
        ("TIME", project.fields.event_time or ""),
        ("VENUE", project.fields.venue_or_location or ""),
        ("CONTACT", project.fields.contact_info or ""),
    ]
    fy = card_top + 36
    for label, value in facts:
        draw.text((margin + 34, fy), label, font=small_font, fill=tuple(palette["accent"]))
        fy += int(small_font.size * 1.15)
        for line in _wrap(draw, value, subtitle_font, width - margin * 2 - 68)[:2]:
            draw.text((margin + 34, fy), line, font=subtitle_font, fill=tuple(palette["ink"]))
            fy += int(subtitle_font.size * 1.18)
        fy += 10

    footer = "Send APPROVE to finalize - Hermes Flyer Studio"
    bbox = draw.textbbox((0, 0), footer, font=small_font)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, height - margin), footer, font=small_font, fill=tuple(palette["ink"]))
    return img


def _render_with_local_pillow(project: FlyerProject, path: Path, *, concept_id: str, size: tuple[int, int] | None) -> bool:
    pil = _load_pillow()
    if pil is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if size is None:
        img = _draw_flyer_pil(project, concept_id=concept_id, size=(1275, 1650), pil_modules=pil)
        img.save(path, "PDF", resolution=150.0)
    else:
        img = _draw_flyer_pil(project, concept_id=concept_id, size=size, pil_modules=pil)
        img.save(path, format="PNG", optimize=True)
    return True


SUBPROCESS_RENDERER = r'''
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
spec=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out=Path(spec["path"]); out.parent.mkdir(parents=True, exist_ok=True)
palette=spec["palette"]; size=tuple(spec["size"])
img=Image.new("RGB", size, tuple(palette["bg"])); draw=ImageDraw.Draw(img)
def font(sz,bold=False):
    c=["/usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf","/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    if bold: c.insert(0,"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    for p in c:
        try:
            if Path(p).exists(): return ImageFont.truetype(p, sz)
        except OSError: pass
    return ImageFont.load_default()
def wrap(text, f, maxw):
    words=(text or "").split(); lines=[]; cur=""
    for w in words:
        cand=(cur+" "+w).strip(); box=draw.textbbox((0,0),cand,font=f)
        if box[2]-box[0] <= maxw or not cur: cur=cand
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines
w,h=size; m=int(w*.07); tf=font(max(46,int(w*.074)),True); sf=font(max(28,int(w*.038)),True); sm=font(max(20,int(w*.024)))
draw.rectangle((0,0,w,int(h*.19)), fill=tuple(palette["primary"])); draw.rectangle((0,int(h*.19),w,int(h*.205)), fill=tuple(palette["accent"]))
for i in range(9):
    cx=int(w*(.08+i*.105)); cy=int(h*.16); r=int(w*.025); draw.ellipse((cx-r,cy-r,cx+r,cy+r), fill=tuple(palette["accent"]))
draw.text((m,int(h*.045)), spec["language"].upper(), font=sm, fill=tuple(palette["soft"]))
y=int(h*.245)
for line in wrap(spec["title"], tf, w-m*2)[:3]:
    draw.text((m,y), line, font=tf, fill=tuple(palette["primary"])); y += int(tf.size*1.08)
for line in wrap(spec.get("style",""), sm, w-m*2)[:2]:
    draw.text((m,y+8), line, font=sm, fill=tuple(palette["ink"])); y += int(sm.size*1.2)
top=max(y+28,int(h*.48)); bottom=int(h*.82)
draw.rounded_rectangle((m,top,w-m,bottom), radius=18, fill=tuple(palette["soft"]), outline=tuple(palette["accent"]), width=4)
fy=top+36
for label,value in spec["facts"]:
    draw.text((m+34,fy), label, font=sm, fill=tuple(palette["accent"])); fy += int(sm.size*1.15)
    for line in wrap(value, sf, w-m*2-68)[:2]:
        draw.text((m+34,fy), line, font=sf, fill=tuple(palette["ink"])); fy += int(sf.size*1.18)
    fy += 10
footer="Send APPROVE to finalize - Hermes Flyer Studio"; box=draw.textbbox((0,0),footer,font=sm)
draw.text(((w-(box[2]-box[0]))//2,h-m), footer, font=sm, fill=tuple(palette["ink"]))
if spec["format"]=="PDF": img.save(out,"PDF",resolution=150.0)
else: img.save(out,format="PNG",optimize=True)
'''


def _render_with_system_pillow(project: FlyerProject, path: Path, *, concept_id: str, size: tuple[int, int] | None) -> None:
    if not Path("/usr/bin/python3").exists():
        raise FlyerRenderError("Pillow is unavailable and /usr/bin/python3 fallback is missing")
    _require_ready(project)
    language = {"te": "Telugu", "hi": "Hindi", "es": "Spanish", "mixed": "Multilingual", "other": "Local language"}.get(project.fields.preferred_language, "English")
    spec = {
        "path": str(path),
        "size": list(size or (1275, 1650)),
        "format": "PDF" if size is None else "PNG",
        "palette": PALETTES.get(concept_id, PALETTES["C1"]),
        "language": language,
        "title": project.fields.event_or_business_name or "",
        "style": project.fields.style_preference,
        "facts": [
            ["DATE", project.fields.event_date or ""],
            ["TIME", project.fields.event_time or ""],
            ["VENUE", project.fields.venue_or_location or ""],
            ["CONTACT", project.fields.contact_info or ""],
        ],
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        spec_path = fh.name
    try:
        proc = subprocess.run(["/usr/bin/python3", "-c", SUBPROCESS_RENDERER, spec_path], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise FlyerRenderError(f"system Pillow renderer failed: {proc.stderr.strip()}")
    finally:
        Path(spec_path).unlink(missing_ok=True)


def _render(project: FlyerProject, path: Path, *, concept_id: str, size: tuple[int, int] | None) -> None:
    if not _render_with_local_pillow(project, path, concept_id=concept_id, size=size):
        _render_with_system_pillow(project, path, concept_id=concept_id, size=size)


def _render_model(project: FlyerProject, path: Path, *, concept_id: str, output_format: str, size: tuple[int, int] | None, model: str, quality: str) -> None:
    if model.strip().lower() in DETERMINISTIC_MODEL_NAMES:
        _render(project, path, concept_id=concept_id, size=size)
        return
    raw = _openrouter_image_bytes(project, concept_id=concept_id, output_format=output_format, size=size, model=model, quality=quality)
    if size is None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as raw_fh:
            raw_fh.write(raw)
            raw_path = Path(raw_fh.name)
        overlaid = path.with_suffix(".overlay.png")
        try:
            _apply_critical_text_overlay(project, raw_path, overlaid, size=(1275, 1650), output_format=output_format)
            _export_from_source_image(overlaid, path, size=None)
        finally:
            raw_path.unlink(missing_ok=True)
            overlaid.unlink(missing_ok=True)
        return
    raw_path = _raw_background_path(path)
    _write_generated_image(raw, raw_path, size=size)
    _apply_critical_text_overlay(project, raw_path, path, size=size, output_format=output_format)


def render_concept_previews(project: FlyerProject, output_dir: Path | str, *, model: str = "deterministic-renderer", quality: str = "low", concept_count: int = 1) -> list[RenderedAssetSpec]:
    output_dir = Path(output_dir)
    specs: list[RenderedAssetSpec] = []
    for concept_id in ("C1", "C2", "C3")[:concept_count]:
        path = output_dir / f"{project.project_id}-{concept_id}-preview.png"
        _render_model(project, path, concept_id=concept_id, output_format="concept_preview", size=(1080, 1350), model=model, quality=quality)
        quality_report = inspect_rendered_asset(path, expected_width=1080, expected_height=1350, mime_type="image/png")
        if not quality_report.ok:
            raise FlyerRenderError(f"rendered concept failed quality check: {quality_report.blockers}")
        specs.append(RenderedAssetSpec(path=path, kind="concept_preview", output_format="concept_preview", width=1080, height=1350, concept_id=concept_id))
    return specs


def render_final_package(project: FlyerProject, output_dir: Path | str, *, model: str = "deterministic-renderer", quality: str = "medium") -> list[RenderedAssetSpec]:
    output_dir = Path(output_dir)
    concept_id = project.selected_concept_id or "C1"
    selected_preview: Path | None = None
    if project.selected_concept_id:
        concept = next((c for c in project.concepts if c.concept_id == project.selected_concept_id), None)
        if concept is not None:
            asset = next((a for a in project.assets if a.asset_id == concept.preview_asset_id), None)
            if asset is not None:
                candidate = Path(asset.path)
                quality_report = inspect_rendered_asset(candidate, expected_width=1080, expected_height=1350, mime_type="image/png")
                if quality_report.ok:
                    selected_preview = candidate
    formats: list[tuple[FlyerOutputFormat, str, tuple[int, int] | None]] = [
        ("whatsapp_image", "final_whatsapp_image", (1080, 1350)),
        ("instagram_post", "final_instagram_post", (1080, 1080)),
        ("instagram_story", "final_instagram_story", (1080, 1920)),
        ("printable_pdf", "final_printable_pdf", None),
    ]
    specs: list[RenderedAssetSpec] = []
    for output_format, kind, size in formats:
        suffix = "pdf" if size is None else "png"
        path = output_dir / f"{project.project_id}-{output_format}.{suffix}"
        if selected_preview is not None and model.strip().lower() not in DETERMINISTIC_MODEL_NAMES:
            source = _raw_background_path(selected_preview)
            if not source.exists():
                source = selected_preview
            if size is None:
                temp_png = path.with_suffix(".overlay-source.png")
                overlaid_png = path.with_suffix(".overlaid.png")
                _export_from_source_image(source, temp_png, size=(1275, 1650))
                _apply_critical_text_overlay(project, temp_png, overlaid_png, size=(1275, 1650), output_format=output_format)
                _export_from_source_image(overlaid_png, path, size=None)
                temp_png.unlink(missing_ok=True)
                overlaid_png.unlink(missing_ok=True)
            else:
                temp_png = path.with_suffix(".overlay-source.png")
                _export_from_source_image(source, temp_png, size=size)
                _apply_critical_text_overlay(project, temp_png, path, size=size, output_format=output_format)
                temp_png.unlink(missing_ok=True)
        else:
            _render_model(project, path, concept_id=concept_id, output_format=output_format, size=size, model=model, quality=quality)
        width, height = size or (1275, 1650)
        quality_report = inspect_rendered_asset(path, expected_width=width, expected_height=height, mime_type="application/pdf" if size is None else "image/png")
        if not quality_report.ok:
            raise FlyerRenderError(f"rendered final failed quality check: {quality_report.blockers}")
        specs.append(RenderedAssetSpec(path=path, kind=kind, output_format=output_format, width=width, height=height, concept_id=concept_id))
    return specs


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_asset_manifest(specs: list[RenderedAssetSpec], *, first_asset_number: int, source: str, original_message_id: str) -> list[FlyerAsset]:
    now = datetime.now(timezone.utc)
    assets: list[FlyerAsset] = []
    for offset, spec in enumerate(specs):
        mime = "application/pdf" if spec.path.suffix.lower() == ".pdf" else "image/png"
        assets.append(FlyerAsset(
            asset_id=f"A{first_asset_number + offset:04d}",
            kind=spec.kind,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            path=str(spec.path),
            mime_type=mime,
            sha256=_sha256(spec.path),
            original_message_id=original_message_id,
            received_at=now,
        ))
    return assets


def next_asset_number(project: FlyerProject) -> int:
    max_seen = 0
    for asset in project.assets:
        if asset.asset_id.startswith("A") and asset.asset_id[1:].isdigit():
            max_seen = max(max_seen, int(asset.asset_id[1:]))
    return max_seen + 1
