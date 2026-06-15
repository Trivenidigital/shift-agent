# Follow-up: `OPENAI_API_KEY` absent on main-vps → `gpt-image-1` source-edit path degraded

**Opened:** 2026-06-14 · **Severity:** Medium · **Status:** OPEN — separate track, does NOT block Flyer Architecture A Slice 1.

## Finding
During Architecture A measurement (2026-06-14), a runtime-state check found **`OPENAI_API_KEY` is not present** on main-vps (checked env + `/root/.hermes/.env` + `/opt/shift-agent/.env`). The configured production **source-edit model is `gpt-image-1`** (`schemas.py:940`, called via `render.py:2748 _openai_source_edit_bytes` → `https://api.openai.com/v1/images/edits` with `OPENAI_API_KEY`).

## Impact
The legacy **source-edit / "edit uploaded flyer" path raises `FlyerRenderError("OPENAI_API_KEY is missing or placeholder")`** and falls back to lossy regeneration — matching the 2026-05-30 incident. Customer edit-of-uploaded-flyer requests are therefore degraded in production right now.

## Why it does NOT block Slice 1 (or Architecture A)
Architecture A's generation and revision both use **OpenRouter** (`OPENROUTER_API_KEY`, present): integrated generation via `google/gemini-3.1-flash-image-preview`, and the revision loop (Slice 2) via gemini-3.1 image-to-image. None of the A path touches `gpt-image-1` / `OPENAI_API_KEY`.

## Options (operator decision)
1. **Provision** `OPENAI_API_KEY` on main-vps (operator holds the key) → restores the legacy source-edit path.
2. **Decommission** the `gpt-image-1` source-edit path and migrate source-edit to the OpenRouter gemini image-edit path (consistent with Architecture A; one provider).

## Recommendation
Decide after Slice 1/2 land — if the gemini image-edit revision loop (Slice 2) proves out, option 2 removes a provider dependency and a silent-degradation surface. Until then, option 1 is the quick restore.
