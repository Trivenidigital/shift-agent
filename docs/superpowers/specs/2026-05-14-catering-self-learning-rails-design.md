# Catering Self-Learning Rails Design

**Drift-check tag:** `extends-Hermes`

**Status:** Draft for design review.

## Goal

Add the first production-safe self-learning surface for the Catering Agent:
nightly sanitized learning signals from real catering state, rendered to the
owner in the Daily Brief when explicitly enabled.

This slice must not change customer-facing behavior. It must not let runtime
LLMs mutate code, SKILLs, prompts, prices, menu policy, or deploy config.

## New Primitives Introduced

- `CateringLearningSummary` schema and small child model in
  `src/platform/schemas.py`.
- Sidecar state file:
  `/opt/shift-agent/state/catering-learning-summary.json`.
- Sidecar lock:
  `/opt/shift-agent/state/catering-learning-summary.json.lock`.
- `daily_brief.sections` opt-in value: `catering_learning`.
- Sanitized learning-summary writer inside the existing
  `catering-pattern-report` script.
- Daily Brief renderer for the sanitized summary only.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp delivery | yes - Hermes bridge + `send-daily-brief` timer | Use unchanged; no new outbound channel. |
| Nightly learning job | yes - `catering-pattern-report.timer` + script | Extend this existing job; do not add a second aggregator. |
| Catering state | yes - JSON/Pydantic state files for leads/menu/proposals | Use existing state and schemas. |
| Owner-facing control tower | yes - Daily Brief Agent | Add opt-in readout to existing brief. |
| Runtime memory | yes - Hermes memory exists | Do not use Hermes memory as source of truth for customer/business state. |
| Self-evolution | yes - Hermes Self-Evolution Kit | Defer to offline eval/PR workflow; no prod hot-mutation. |
| Google Workspace | yes - install-now `productivity/google-workspace` | Not needed for this local summary slice. |
| Maps | yes - install-now `productivity/maps` | Not needed for this slice. |
| Airtable | yes - install-now `productivity/airtable` | Not needed for this slice; future package/cost table candidate. |
| OCR/documents | yes - install-now `productivity/ocr-and-documents` | Already complements menu ingestion; no new OCR here. |
| Notion | yes - install-now `productivity/notion` | Not needed for this slice. |
| Native MCP | yes - `mcp/native-mcp` | Check later before QBO/e-sign/payment/calendar custom code. |
| Awesome Hermes ecosystem | no drop-in catering learning reporter found | Use local deterministic state/reporting. |

Awesome-Hermes-Agent verdict: useful discovery surface, but no maintained
drop-in replaces the repo's local catering state, owner gate, and WhatsApp
Daily Brief conventions.

## Drift Checks Performed

Read before drafting:

- `src/agents/catering/scripts/catering-pattern-report`: existing nightly
  operator learning primitive. It currently scans hallucinated names and
  appends `/opt/shift-agent/lessons/catering.md`.
- `src/agents/catering/systemd/catering-pattern-report.timer`: existing
  nightly 02:00 timer.
- `src/agents/daily_brief/scripts/send-daily-brief`: existing catering block
  and Daily Brief rendering path.
- `src/platform/schemas.py`: current `BriefSection`, `CateringLeadStore`,
  `CateringProposalStore`, and `Menu.updated_at`.
- `tests/test_catering_pattern_report.py`: current pure tests for the pattern
  reporter.
- `tests/test_daily_brief_birthdays.py`: existing opt-in section and injectable
  path pattern for Daily Brief tests.

## Policy

The learning summary may include:

- aggregate counts
- off-menu request counts
- missing-info count
- menu freshness age from `Menu.updated_at`
- degraded-source names such as `menu` or `proposals`

The learning summary must not include:

- customer names
- phone numbers or JIDs
- venue/street addresses
- raw inquiry text
- proposal `request_text`
- lead ids in customer-derived learning lines
- raw or sanitized off-menu item text in v1
- prices, deposits, payment rails, booking confirmation, or quote totals

## Data Model

Add to `src/platform/schemas.py`:

```python
CateringLearningSource = Literal["catering-pattern-report"]

class CateringLearningProposalHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sent: int = Field(default=0, ge=0)
    selected: int = Field(default=0, ge=0)
    send_failed: int = Field(default=0, ge=0)
    select_failed: int = Field(default=0, ge=0)

class CateringLearningSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source: CateringLearningSource = "catering-pattern-report"
    generated_at: datetime
    window_days: int = Field(ge=1, le=365)
    proposal_health: CateringLearningProposalHealth = Field(
        default_factory=CateringLearningProposalHealth
    )
    off_menu_request_count: int = Field(default=0, ge=0)
    leads_with_off_menu_count: int = Field(default=0, ge=0)
    active_missing_info_count: int = Field(default=0, ge=0)
    menu_updated_at: Optional[datetime] = None
    menu_freshness_days: Optional[int] = Field(default=None, ge=0)
    degraded_sources: list[Literal["leads", "proposals", "menu"]] = Field(
        default_factory=list, max_length=3
    )
```

Also extend:

```python
BriefSection = Literal[
    "yesterday", "today_outlook", "alerts", "birthdays",
    "catering_learning",
]
```

Default `DailyBriefConfig.sections` stays unchanged. This is the rollout kill
switch: the learning lines are absent unless `catering_learning` is explicitly
listed.

## Pattern Report Changes

Modify `src/agents/catering/scripts/catering-pattern-report`.

New module constants:

```python
DEFAULT_PROPOSALS_PATH = Path("/opt/shift-agent/state/catering-proposals.json")
DEFAULT_MENU_PATH = Path("/opt/shift-agent/state/catering-menu.json")
DEFAULT_LEARNING_SUMMARY_PATH = Path(
    "/opt/shift-agent/state/catering-learning-summary.json"
)
DEFAULT_LEARNING_SUMMARY_LOCK = Path(
    "/opt/shift-agent/state/catering-learning-summary.json.lock"
)
DEFAULT_LEARNING_DAYS = 30
```

New CLI flags:

- `--proposals`
- `--menu`
- `--learning-summary`
- `--learning-summary-lock`
- `--learning-days`
- `--skip-learning-summary`

Existing `--days` remains the hallucinated-name scan window. `--learning-days`
defaults to 30 so the timer's existing `--days 1` service still produces a
30-day business-learning summary.

### Summary Builder

Add deployed-path bootstrap before imports that need platform modules:

```python
sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "platform"))
```

Then add `_build_learning_summary(leads_path, proposals_path, menu_path, now,
window_days) -> CateringLearningSummary`.

Implementation rules:

- Load leads with `CateringLeadStore.model_validate`; missing file counts as
  zero with `leads` degradation, and corrupt/schema-invalid data adds `leads`
  to `degraded_sources` while still returning a summary. One malformed legacy
  lead must not skip the learning sidecar.
- Load proposals with `CateringProposalStore.model_validate`; missing file
  counts as zero with no degradation, corrupt/schema-invalid adds
  `proposals` to `degraded_sources`.
- Load menu with `Menu.model_validate`; missing/corrupt/schema-invalid adds
  `menu` to `degraded_sources`.
- Use `Menu.updated_at` for menu freshness. Never use filesystem mtime.
- Off-menu window: leads with `created_at >= now - window_days`.
- Off-menu source: only count `lead.extracted.off_menu_items`; do not inspect
  `raw_inquiry`, do not inspect proposal `request_text`, and do not persist or
  render any off-menu string value in v1. `off_menu_request_count` is the total
  number of extracted off-menu entries in-window; `leads_with_off_menu_count`
  is the number of in-window leads with at least one off-menu entry.
- Active missing-info statuses:
  `NEW`, `EXTRACTING`, `AWAITING_OWNER_APPROVAL`, `CUSTOMER_FINALIZED`,
  `OWNER_APPROVED`, `OWNER_EDITED`, `SENT_TO_CUSTOMER`.
- Missing-info definition: active lead where `headcount is None` OR
  `event_date is None`.
- Proposal window: sets with `created_at >= now - window_days`.
- Proposal buckets:
  - `sent`: `SENT`, `SUPERSEDED`
  - `selected`: `SELECTED`
  - `send_failed`: `SEND_FAILED`
  - `select_failed`: `SELECT_FAILED`, `SELECTED_OWNER_CARD_FAILED`
  - ignore `DRAFT` and `SELECTING`

### No Free-Text Learning Terms In V1

`lead.extracted.off_menu_items` is LLM-extracted free text. It can contain
customer names, venue fragments, possessives, prices, and other PII. A
blacklist-only sanitizer is not safe enough for WhatsApp rendering.

For v1, the sidecar stores counts only and no free-text terms. A future slice
may add an owner-curated food taxonomy or exact canonical allowlist; until that
exists, Daily Brief must not show "top off-menu terms."

Even though v1 does not render off-menu text, tests should seed hostile
off-menu strings containing names, phone numbers, addresses, Markdown markers,
RTL/zero-width characters, prices, and payment words to prove they do not reach
the sidecar or rendered brief. Reuse the broad no-price/payment pattern from
`create-catering-proposal-options.NO_PRICE_RE` if a later v2 adds term rendering;
do not create a narrower second price filter.

### Write Semantics

When not `--dry-run` and not `--skip-learning-summary`:

- Always write the learning summary, even when the hallucination scan has zero
  findings.
- Write through `safe_io.atomic_write_json` under a lock derived from the
  actual output path: `Path(str(args.learning_summary) + ".lock")`, unless an
  explicit `--learning-summary-lock` is provided. Do not lock the default path
  when writing a custom path.
- Keep the existing append-only lessons file behavior. Do not write lessons
  when there are zero hallucination findings.

When `--dry-run`:

- Do not write the sidecar.
- Print the learning summary JSON after the existing dry-run lesson output.

Exit behavior:

- Existing bad-input exit `2` remains.
- Learning-summary degradation should not make the script non-zero unless all
  required inputs are unusable in a way that already breaks current behavior.

## Daily Brief Changes

Modify `src/agents/daily_brief/scripts/send-daily-brief`.

New constants:

```python
CATERING_LEADS_PATH = Path(os.environ.get(
    "SHIFT_AGENT_CATERING_LEADS_PATH",
    "/opt/shift-agent/state/catering-leads.json",
))
CATERING_LEARNING_SUMMARY_PATH = Path(os.environ.get(
    "SHIFT_AGENT_CATERING_LEARNING_SUMMARY_PATH",
    "/opt/shift-agent/state/catering-learning-summary.json",
))
CATERING_LEARNING_STALE_HOURS = 48
```

Change `_render_catering(now_local, include_learning=False)`:

- Replace hardcoded leads path with `CATERING_LEADS_PATH`.
- Preserve the current pipeline count output.
- If `include_learning` is false, return current output only.
- If true, call `_render_catering_learning(now_local)`.

Add `_load_catering_learning_summary() -> CateringLearningSummary | None`:

- Missing file: return `None`.
- Corrupt/schema-invalid/unexpected shape: write a WARN to stderr and return
  `None`.
- Never raise into `_render_brief_text`.

Add `_render_catering_learning(now_local) -> str`:

- If summary missing: return
  `  • Learning summary unavailable; check catering-pattern-report.timer`
  because `include_learning=True` means the operator explicitly enabled this
  section.
- If `generated_at` older than 48h: return one safe line:
  `  • Learning summary stale (>48h); check catering-pattern-report.timer`
- Otherwise render:
  - `  • Proposals (30d): N sent, N selected, N send failed, N select failed`
  - `  • Off-menu asks: N request(s) across M lead(s)`
  - `  • Missing basics: N active lead(s)`
  - `  • Menu freshness: updated Nd ago` or `  • Menu freshness: unknown`
- If `degraded_sources` is non-empty, append one safe line:
  `  • Learning summary degraded: menu, proposals`
- Wrap the entire renderer in a local broad `except Exception` guard that
  writes a WARN to stderr and returns one safe degraded line. No learning
  render failure may crash the whole Daily Brief.

Change `_render_brief_text`:

```python
include_learning = "catering_learning" in cfg.daily_brief.sections
catering_summary = _render_catering(today_local, include_learning=include_learning)
```

Default behavior is unchanged for current customers because
`catering_learning` is absent from default sections.

## Test Plan

### Schema Tests

Modify `tests/test_daily_brief_schemas.py` or add focused schema tests:

- `DailyBriefConfig(sections=["catering_learning"])` is valid.
- `CateringLearningSummary` accepts a valid minimal summary.
- `CateringLearningSummary` rejects extra fields and negative counts.

### Pattern Report Tests

Extend `tests/test_catering_pattern_report.py`:

- Summary builder counts off-menu entries/leads in the last 30 days without
  persisting any off-menu string values.
- Summary builder ignores old leads outside the window.
- Summary builder counts exact proposal buckets and ignores old proposal sets.
- Summary builder uses `Menu.updated_at` for freshness.
- Missing/corrupt/schema-invalid lead, proposal, or menu state adds degraded
  source and still returns a summary.
- `main()` writes sidecar even when hallucination findings are zero.
- `--dry-run` does not write sidecar.
- Custom `--learning-summary` path locks `custom-path.lock`, not the default
  `/opt` lock.
- The test module must use Linux skip/lazy import if top-level `safe_io`
  imports make Windows collection unsafe.

### Daily Brief Tests

Add `tests/test_daily_brief_catering_learning.py`:

- Path constants are patched to temp state; tests do not touch `/opt`.
- Default sections omit learning lines.
- `sections=["catering_learning"]` renders learning lines.
- Missing summary renders the unavailable warning when the section is enabled.
- Corrupt summary logs WARN and does not crash or render unsafe content.
- Stale summary renders the stale warning.
- Learning output never includes seeded customer name, phone, address, price,
  raw inquiry text, proposal request text, Markdown control chars, zero-width
  chars, or raw off-menu text.

## Rollout

1. Deploy with `catering_learning` absent from `daily_brief.sections`.
2. Run:
   ```bash
   sudo -u shift-agent /usr/local/bin/catering-pattern-report --dry-run --learning-days 30
   ```
   and inspect the learning summary JSON.
3. Run:
   ```bash
   sudo -u shift-agent /usr/local/bin/catering-pattern-report --learning-days 30
   ```
   to write the sidecar.
4. Temporarily test the Daily Brief in dry-run with a config copy that includes
   `catering_learning`.
5. Enable `catering_learning` in real `config.yaml` only after dry-run output is
   reviewed.
6. Restart/force-run Daily Brief as needed.

Rollback:

- Remove `catering_learning` from `daily_brief.sections`.
- The sidecar can remain on disk; it is inert when the section is disabled.

## Completion Criteria

- Existing `catering-pattern-report` hallucination behavior is preserved.
- The learning sidecar is schema-valid and written atomically.
- Daily Brief default output is unchanged unless `catering_learning` is enabled.
- Learning output contains no customer PII, raw text, prices, or payment terms.
- Local tests pass.
- VPS dry-run output is reviewed before enabling the section.
