**Drift-check tag:** extends-Hermes

# Flyer Source Edit Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a source-preserving edit pipeline for uploaded Flyer Studio artwork so requests like "remove extra 08:00" edit the attached flyer instead of recreating a new poster.

**Architecture:** Reuse Hermes WhatsApp ingress, reference assets, JSON project state, audit, quota, and delivery. Add a Flyer-specific image edit renderer that calls a configured image-edit provider for `manual_edit_required` projects and fails closed to designer-assisted/manual queue if the provider is unavailable.

**Tech Stack:** Python, Pydantic schemas, existing Flyer JSON state, urllib multipart calls to OpenAI Image edits API, pytest.

---

**New primitives introduced:** `edit_image_model`, `edit_image_quality`, source-preserving edit renderer, edit-processing acknowledgement.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp/media ingress | yes — deployed Hermes gateway and cf-router media paths | use it |
| State/audit/delivery | yes — Flyer JSON state, `safe_io`, bridge send, existing audit | use it |
| Image reference generation | yes — current Flyer/OpenRouter generation path | keep for new posters, not exact edits |
| Source-preserving image editing | partial — Hermes ecosystem has image generation/FAL-style skills, but no deployed source-preserving Flyer edit primitive in this tree | build narrow provider adapter |
| PR/review workflow | yes — GitHub PR workflow and repo tests | use it |

Awesome-Hermes ecosystem check: checked current Hermes docs/Awesome Hermes search results; image generation skills exist, but no installed/deployed skill provides the exact Flyer state integration and WhatsApp approval loop. Build the thin Flyer-specific bridge.

- [x] Task 1: Add failing tests for exact edit routing and project state.
- [x] Task 2: Add image-edit config and source-preserving renderer.
- [x] Task 3: Teach `generate-flyer-concepts` to edit `manual_edit_required` projects.
- [x] Task 4: Update router to attempt edit generation and fall back safely.
- [x] Task 5: Verify locally, commit, push branch, and open PR.
- [x] Task 6: Run multi-vector reviewers before merge.

## Review Notes

- Added source-preserving edit generation through OpenAI image edits with `input_fidelity=high`.
- Added quota/guest reservation discipline: reserve before generation, release on generation/preview failure, finalize/consume only after preview delivery.
- Added source-edit manifest truth guard: `source_edit_integrity_only` warns that customer approval remains the visual/text QA gate.
- Verification on Windows: Flyer suite `129 passed`, focused source-edit/guest slice `117 passed`, backend cockpit `46 passed, 1 skipped`, frontend `npm run build` passed, py-compile passed, `git diff --check` returned only line-ending warnings.
- Limitation: `tests/test_cf_router_plugin.py` is Linux-only and skipped on this Windows host because it imports `safe_io`/`fcntl`; focused behavior was added there for the Linux CI/reviewer path.
