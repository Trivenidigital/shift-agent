# Catering Autonomous Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe autonomous catering proposal loop: menu-grounded options to customers, proposal selection to owner approval, and no customer-facing pricing before owner approval.

**Architecture:** Keep Hermes as the orchestration layer and add deterministic Python chokepoints for state, validation, bridge sends, and audit. Store proposal options in a sidecar JSON file guarded by its own lock, route proposal selection through cf-router with a feature flag, and route proposal requests to a constrained source-controlled Hermes skill.

**Tech Stack:** Python scripts with Pydantic v2 schemas, JSON-on-disk with `safe_io.FileLock` and `atomic_write_json`, Hermes SKILL.md files, cf-router Hermes plugin, pytest.

---

**Drift-check tag:** `extends-Hermes`

## New Primitives Introduced

- `CateringProposalOption`, `CateringProposalSet`, `CateringProposalStore`
- `CateringProposalsGenerated`, `CateringProposalGenerationFailed`
- `CateringProposalSelected`, `CateringProposalSelectionFailed`
- `/opt/shift-agent/state/catering-proposals.json`
- `/opt/shift-agent/state/catering-proposals.json.lock`
- `/usr/local/bin/create-catering-proposal-options`
- `/usr/local/bin/select-catering-proposal`
- `creative_catering_proposals` Hermes skill
- cf-router `F7_PROPOSAL_BRANCH_ENABLED` flag and proposal request/selection classifiers

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Creative proposal generation | yes - live VPS `/root/.hermes/skills/catering/creative-catering-proposals/SKILL.md` | Source-control and constrain it; no greenfield conversational bot. |
| Menu source | yes - `/opt/shift-agent/state/catering-menu.json` and menu update skill | Reuse current menu file and exact item names. |
| Lead lifecycle | yes - `create-catering-lead`, `finalize-catering-menu`, `apply-catering-owner-decision` | Extend around these scripts, do not replace them. |
| Active lead routing | partial - cf-router Branch B | Extend with flag-gated proposal workflow classifiers. |
| Official skill hub / install-now skills / mcp/native-mcp | none applicable | No external connector needed for in-WhatsApp proposal flow. |

## File Map

Create:

- `src/agents/catering/scripts/create-catering-proposal-options`
  - Validates skill output, writes proposal sidecar state, renders customer proposal body, sends via bridge, emits audit.
- `src/agents/catering/scripts/select-catering-proposal`
  - Resolves customer choice, calls `finalize-catering-menu`, updates proposal sidecar status, sends selection ack.
- `src/agents/catering/skills/creative_catering_proposals/SKILL.md`
  - Source-controlled constrained version of the live VPS creative proposal skill.
- `tests/test_catering_proposal_schemas.py`
  - Schema and audit union tests.
- `tests/test_create_catering_proposal_options.py`
  - Linux-only subprocess tests for generation script.
- `tests/test_select_catering_proposal.py`
  - Linux-only subprocess tests for selection script.

Modify:

- `src/platform/schemas.py`
  - Add proposal sidecar schemas and audit variants; extend `LogEntry` union; extend `CfRouterIntercepted.reason`.
- `src/agents/catering/templates/catering_finalized_menu_to_owner.txt`
  - Relabel selected-menu total as internal menu-price estimate.
- `src/agents/catering/scripts/finalize-catering-menu`
  - Update inline fallback owner-card wording to match template.
- `src/agents/catering/skills/catering_dispatcher/SKILL.md`
  - Add proposal request/selection routes in priority order.
- `src/agents/shift/skills/dispatch_shift_agent/SKILL.md`
  - Add active-lead-conditioned proposal workflow addendum, not global bare keywords.
- `src/plugins/cf-router/actions.py`
  - Add proposal classifier helpers, selectable proposal lookup, and selection subprocess wrapper.
- `src/plugins/cf-router/hooks.py`
  - Add feature flag and Branch B proposal carve-out.
- `tests/test_cf_router_plugin.py`
  - Update pinned weak follow-up tests and add flag/request/selection coverage.
- `src/platform/scripts/dispatcher-accuracy-report`
  - Count `cf_router_intercepted reason=f7_proposal_selection` as dispatcher-equivalent routing.
- `tests/test_dispatcher_accuracy_report.py`
  - Pin the new pairing behavior.
- `src/agents/shift/scripts/shift-agent-deploy.sh`
  - Add `creative_catering_proposals` to required skills.
- `src/agents/shift/scripts/shift-agent-smoke-test.sh`
  - Add import/existence smoke for proposal scripts and skill.
- `tasks/todo.md`
  - Track plan, implementation, tests, deploy.

## Execution Rules

- Use TDD for each task: write failing tests, run the focused test, implement, rerun.
- Do not commit unless the user explicitly authorizes commits.
- Use `apply_patch` for manual edits.
- Linux-only script tests should follow the existing `pytestmark.skipif(platform.system() == "Windows")` pattern from `tests/test_catering_finalize_menu.py`.
- The cf-router feature flag defaults to false until deploy verification completes.

---

### Task 1: Add Proposal Schemas And Audit Variants

**Files:**
- Modify: `src/platform/schemas.py`
- Create: `tests/test_catering_proposal_schemas.py`

- [ ] **Step 1: Write schema tests**

Create `tests/test_catering_proposal_schemas.py` with:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError, TypeAdapter

from schemas import (
    CateringProposalOption,
    CateringProposalSet,
    CateringProposalStore,
    CateringProposalsGenerated,
    CateringProposalGenerationFailed,
    CateringProposalSelected,
    CateringProposalSelectionFailed,
    LogEntry,
    CfRouterIntercepted,
)


def _now():
    return datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc)


def test_proposal_option_round_trip():
    opt = CateringProposalOption(
        option_id="1",
        style_key="balanced_mixed",
        tier="balanced",
        item_names=["Chicken Biryani", "Paneer Tikka Kebab (8 PCS)"],
    )
    assert opt.option_id == "1"
    assert opt.tier == "balanced"
    assert opt.item_names == ["Chicken Biryani", "Paneer Tikka Kebab (8 PCS)"]


def test_proposal_option_rejects_empty_items():
    with pytest.raises(ValidationError):
        CateringProposalOption(
            option_id="1",
            style_key="balanced_mixed",
            tier="balanced",
            item_names=[],
        )


def test_proposal_set_sent_requires_outbound_message_id():
    with pytest.raises(ValidationError):
        CateringProposalSet(
            proposal_set_id="CPS-L0014-000001",
            lead_id="L0014",
            status="SENT",
            created_at=_now(),
            sent_at=_now(),
            outbound_message_id="",
            source_message_id="msg1",
            request_text="send two options",
            options=[
                CateringProposalOption(
                    option_id="1", style_key="classic", tier="classic",
                    item_names=["Veg Biryani"],
                )
            ],
        )


def test_proposal_store_extra_ignored_for_forward_compat():
    store = CateringProposalStore.model_validate(
        {"schema_version": 1, "next_sequence": 2, "sets": [], "future": "ok"}
    )
    assert store.next_sequence == 2


@pytest.mark.parametrize(
    "entry",
    [
        CateringProposalsGenerated(
            type="catering_proposals_generated",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            option_count=2,
            outbound_message_id="wamid.1",
        ),
        CateringProposalGenerationFailed(
            type="catering_proposal_generation_failed",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            reason="unknown_menu_item",
            detail="Bad item",
        ),
        CateringProposalSelected(
            type="catering_proposal_selected",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            option_id="2",
            customer_message_id="msg2",
            finalize_exit_code=0,
        ),
        CateringProposalSelectionFailed(
            type="catering_proposal_selection_failed",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            reason="finalize_exit_11",
            detail="quote mismatch",
        ),
    ],
)
def test_new_audit_variants_in_log_entry_union(entry):
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.type == entry.type


def test_cf_router_reason_accepts_proposal_selection():
    row = CfRouterIntercepted(
        type="cf_router_intercepted",
        ts=_now(),
        reason="f7_proposal_selection",
        chat_id="123@lid",
        subprocess_rc=0,
    )
    assert row.reason == "f7_proposal_selection"
```

- [ ] **Step 2: Run schema tests and verify they fail**

Run:

```powershell
pytest tests/test_catering_proposal_schemas.py -q
```

Expected: import failures for missing proposal schema classes.

- [ ] **Step 3: Implement schemas**

In `src/platform/schemas.py`, add near the catering lead/menu models:

```python
CateringProposalStatus = Literal[
    "DRAFT", "SENT", "SEND_FAILED", "SUPERSEDED",
    "SELECTING", "SELECTED", "SELECTED_OWNER_CARD_FAILED", "SELECT_FAILED",
]

CateringProposalTier = Literal["classic", "balanced", "premium"]


class CateringProposalOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str = Field(pattern=r"^[1-3]$")
    style_key: str = Field(min_length=1, max_length=80)
    tier: CateringProposalTier
    item_names: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=20
    )


class CateringProposalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_set_id: str = Field(pattern=r"^CPS-L[0-9]{4,}-[0-9]{6}$")
    lead_id: str = Field(pattern=r"^L[0-9]{4,}$")
    status: CateringProposalStatus
    created_at: datetime
    sent_at: Optional[datetime] = None
    outbound_message_id: str = ""
    source_message_id: str = Field(min_length=1, max_length=200)
    request_text: str = Field(default="", max_length=1000)
    options: list[CateringProposalOption] = Field(min_length=1, max_length=3)
    selected_option_id: Optional[str] = Field(default=None, pattern=r"^[1-3]$")
    failure_reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _sent_requires_outbound_id(self) -> "CateringProposalSet":
        if self.status == "SENT" and not self.outbound_message_id.strip():
            raise ValueError("SENT proposal set requires outbound_message_id")
        if self.status == "SENT" and self.sent_at is None:
            raise ValueError("SENT proposal set requires sent_at")
        return self


class CateringProposalStore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
    next_sequence: int = Field(default=1, ge=1)
    sets: list[CateringProposalSet] = Field(default_factory=list)
```

Add audit classes near other catering audit entries:

```python
class CateringProposalsGenerated(_BaseEntry):
    type: Literal["catering_proposals_generated"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = Field(min_length=1)
    option_count: int = Field(ge=1, le=3)
    outbound_message_id: str = Field(min_length=1)


class CateringProposalGenerationFailed(_BaseEntry):
    type: Literal["catering_proposal_generation_failed"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = ""
    reason: Literal[
        "unknown_menu_item", "forbidden_customer_text", "bridge_unreachable",
        "lead_not_found", "menu_missing", "invalid_options",
    ]
    detail: str = Field(default="", max_length=2000)


class CateringProposalSelected(_BaseEntry):
    type: Literal["catering_proposal_selected"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = Field(min_length=1)
    option_id: str = Field(pattern=r"^[1-3]$")
    customer_message_id: str = Field(min_length=1, max_length=200)
    finalize_exit_code: int = Field(ge=0)


class CateringProposalSelectionFailed(_BaseEntry):
    type: Literal["catering_proposal_selection_failed"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = ""
    reason: Literal[
        "no_sent_proposal", "ambiguous_selection", "invalid_selection",
        "lead_not_found", "finalize_exit_2", "finalize_exit_4",
        "finalize_exit_11", "finalize_exit_other",
    ]
    detail: str = Field(default="", max_length=2000)
```

Extend `CfRouterIntercepted.reason` with `"f7_proposal_selection"`.

Extend the `LogEntry` annotated union with all four new audit classes.

- [ ] **Step 4: Run schema tests and existing schema tests**

Run:

```powershell
pytest tests/test_catering_proposal_schemas.py tests/test_schemas.py -q
```

Expected: all pass.

- [ ] **Step 5: Checkpoint**

Task 1 execution note, 2026-05-13:

- Implemented without commit per user instruction.
- RED: `python -m pytest tests/test_catering_proposal_schemas.py -q` failed on missing proposal schema imports.
- GREEN: `python -m pytest tests/test_catering_proposal_schemas.py tests/test_schemas.py -q` passed with `53 passed`.
- Review fix added: `CateringProposalSet` now rejects duplicate `option_id`s and `selected_option_id` values that do not exist in `options`.
- Spec review and code-quality re-review approved.

Do not commit unless the user explicitly authorizes commits. If authorized:

```powershell
git add src/platform/schemas.py tests/test_catering_proposal_schemas.py
git commit -m "feat: add catering proposal state schemas"
```

---

### Task 2: Implement Proposal Generation Script

**Files:**
- Create: `src/agents/catering/scripts/create-catering-proposal-options`
- Create: `tests/test_create_catering_proposal_options.py`

- [ ] **Step 1: Write failing script tests**

Create `tests/test_create_catering_proposal_options.py`. Use the Linux-only marker:

```python
import platform
import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)
```

Include these tests:

```python
def test_generates_sent_set_and_bridge_message(env_dir, bridge_server):
    seed_lead(env_dir, lead_id="L0014", owner_code="#ABCDE")
    seed_menu(env_dir, ["Chicken Biryani", "Veg Biryani"])
    result = run_create_options(
        env_dir,
        lead_id="L0014",
        request_text="Please send two mixed menu proposals",
        options=[
            proposal_option("1", ["Chicken Biryani", "Veg Biryani"]),
            proposal_option("2", ["Chicken Biryani", "Veg Biryani"]),
        ],
    )
    assert result.returncode == 0
    store = read_proposal_store(env_dir)
    assert store["sets"][0]["status"] == "SENT"
    assert store["sets"][0]["outbound_message_id"]
    assert bridge_server.requests[-1]["body"].startswith("⚕ *Catering Agent*")
    assert "$" not in bridge_server.requests[-1]["body"]
    assert "price" not in bridge_server.requests[-1]["body"].lower()
```

```python
def test_unknown_item_fails_closed_without_bridge_send(env_dir, bridge_server):
    # option item_names includes "Live Dosa Station"
    # expected return code 2
    # assert proposal store has no selectable SENT set
    # assert no bridge requests
    # assert decisions.log has catering_proposal_generation_failed
```

```python
def test_bridge_failure_marks_send_failed_not_selectable(env_dir, bridge_server_down):
    # bridge returns 500
    # expected return code 6
    # assert latest set status == "SEND_FAILED"
    # assert outbound_message_id == ""
```

```python
def test_supersedes_prior_sent_only_after_success(env_dir, bridge_server):
    seed_sent_proposal_set(env_dir, "CPS-L0014-000001", lead_id="L0014")
    result = run_create_options(env_dir, lead_id="L0014", request_text="Send two options")
    assert result.returncode == 0
    store = read_proposal_store(env_dir)
    by_id = {item["proposal_set_id"]: item for item in store["sets"]}
    assert by_id["CPS-L0014-000001"]["status"] == "SUPERSEDED"
    assert by_id["CPS-L0014-000002"]["status"] == "SENT"
```

```python
def test_option_count_cap_rejects_three_unless_requested(env_dir, bridge_server):
    # request_text does not contain three/3, options_json has 3 options
    # expected return code 2
```

```python
def test_no_price_regex_rejects_customer_body(env_dir, bridge_server):
    # use style_key that would render forbidden "pricing" if implementation regresses
    # expected helper _assert_no_forbidden_customer_text raises or script exits 2
```

The test file can reuse patterns from `tests/test_catering_finalize_menu.py` for bridge stubs and fixture files.

- [ ] **Step 2: Run tests and verify they fail**

Run on Linux environment:

```bash
pytest tests/test_create_catering_proposal_options.py -q
```

Expected: script missing.

- [ ] **Step 3: Implement generation script**

Create executable Python script with these constants and helpers:

```python
PROPOSALS_PATH = Path("/opt/shift-agent/state/catering-proposals.json")
PROPOSALS_LOCK = Path("/opt/shift-agent/state/catering-proposals.json.lock")
LEADS_PATH = Path("/opt/shift-agent/state/catering-leads.json")
LEADS_LOCK = Path("/opt/shift-agent/state/catering-leads.json.lock")
MENU_PATH = Path("/opt/shift-agent/state/catering-menu.json")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
LOG_LOCK = Path("/opt/shift-agent/logs/decisions.log.lock")
BRIDGE_PREFIX = "\u2695 *Catering Agent*\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
NO_PRICE_RE = re.compile(r"""
    \$\s*\d+
  | \b\d+(?:\.\d{1,2})?\s*(?:usd|dollars?|bucks)\b
  | \b\d+(?:\.\d{1,2})?\s*(?:/|per\s+)(?:person|plate|guest|head|pax)\b
  | \b(?:price|priced|pricing|cost|costs|rate|rates|fee|fees|charge|charges)\b
  | \b(?:deposit|payment|pay|paid|venmo|zelle|cash\s*app|cashapp|credit\s*card|invoice)\b
  | \b(?:book|booking|booked|confirmed|confirmation)\b
""", re.IGNORECASE | re.VERBOSE)
```

Implement:

```python
def _allowed_option_count(request_text: str) -> int:
    return 3 if re.search(r"\b(three|3)\b", request_text, re.I) else 2
```

```python
def _render_option(option: CateringProposalOption, menu_by_name: dict[str, MenuItem]) -> str:
    title_by_style = {
        "balanced_mixed": "Option {n}: Balanced Veg and Non-Veg Menu",
        "premium_mixed": "Option {n}: Premium Celebration Menu",
        "classic_family": "Option {n}: Classic Family Favorites",
    }
    # Render only validated item names grouped by category.
```

```python
def _assert_no_forbidden_customer_text(text: str) -> None:
    if NO_PRICE_RE.search(text):
        raise ValueError("customer-visible proposal contains pricing/payment language")
```

Write state in this order:

1. Load lead under `LEADS_LOCK`.
2. Load menu without holding proposal lock.
3. Validate options and render body.
4. Under `PROPOSALS_LOCK`, allocate `proposal_set_id`, write `DRAFT`.
5. Send bridge outside locks.
6. Under `PROPOSALS_LOCK`, mark `SENT` plus supersede prior sent sets, or mark `SEND_FAILED`.
7. Under `LOG_LOCK`, append success or failure audit.

- [ ] **Step 4: Run generation tests**

Run:

```bash
pytest tests/test_create_catering_proposal_options.py -q
```

Expected: all pass.

- [ ] **Step 5: Checkpoint**

Task 2 execution note, 2026-05-13:

- Implemented without commit per user instruction.
- Official Windows test command is Linux-skipped by design: `python -m pytest tests/test_create_catering_proposal_options.py -q` => `8 skipped`.
- `python -m py_compile src/agents/catering/scripts/create-catering-proposal-options` passed.
- Worker used a temporary non-repo shim to exercise Linux-only tests: `8 passed`.
- Review fix added: sequence-aware superseding prevents a slow older bridge send from superseding a newer already-SENT proposal.
- Spec review and code-quality re-review approved.

Do not commit unless the user explicitly authorizes commits. If authorized:

```bash
git add src/agents/catering/scripts/create-catering-proposal-options tests/test_create_catering_proposal_options.py
git commit -m "feat: add catering proposal generation script"
```

---

### Task 3: Implement Proposal Selection Script

**Files:**
- Create: `src/agents/catering/scripts/select-catering-proposal`
- Create: `tests/test_select_catering_proposal.py`

- [ ] **Step 1: Write failing selection tests**

Create Linux-only tests covering:

```python
def test_option_number_selection_calls_finalize_with_code(env_dir, monkeypatch):
    # active SENT proposal has option_id "2"
    # lead L0014 has owner_approval_code "#ABCDE"
    # monkeypatch subprocess.run and assert argv contains:
    # --code #ABCDE --customer-message-id msg2
    # assert "--selected-items-json" in argv and "--quote-total-usd" in argv
```

```python
def test_send_failed_set_is_not_selectable(env_dir):
    # latest proposal set is SEND_FAILED
    # expected return code 4 or 2 per script contract
    # assert no finalize subprocess call
```

```python
def test_ambiguous_tier_alias_asks_clarification(env_dir, bridge_server):
    # two active options both tier premium
    # selection text "premium"
    # assert no finalize call and bridge sends numbered clarification
```

```python
@pytest.mark.parametrize("finalize_rc,expected_status,expect_selected", [
    (0, "SELECTED", True),
    (6, "SELECTED_OWNER_CARD_FAILED", True),
    (2, "SELECT_FAILED", False),
    (4, "SELECT_FAILED", False),
    (11, "SELECT_FAILED", False),
])
def test_finalize_exit_code_handling(env_dir, bridge_server, monkeypatch, finalize_rc, expected_status, expect_selected):
    # monkeypatch subprocess.run returncode
    # assert status and audit type match contract
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
pytest tests/test_select_catering_proposal.py -q
```

Expected: script missing.

- [ ] **Step 3: Implement selection script**

Core selection ladder:

```python
OPTION_RE = re.compile(r"(?i)(?:option|proposal|menu)?\s*#?\s*([1-3])\b")
ACTION_OPTION_RE = re.compile(
    r"(?i)\b(?:go with|choose|select|take|pick|finalize|confirm|lock in|proceed with|we'?ll take|i'?ll take|she'?ll take)\b.{0,40}\b(?:option|proposal|menu)?\s*#?\s*([1-3])\b"
)
TIER_RE = re.compile(r"(?i)\b(premium|balanced|classic)\b")
```

Implement:

```python
def _resolve_selection(text: str, options: list[CateringProposalOption]) -> tuple[str | None, str]:
    # Return (option_id, reason). option_id None means clarify/fail.
    # 1. action option or bare digit
    # 2. unique tier alias
    # 3. ambiguous/invalid
```

Resolve lead code under `LEADS_LOCK`:

```python
lead = next((l for l in store.leads if l.lead_id == args.lead_id), None)
code = lead.owner_approval_code
```

Invoke finalize:

```python
cmd = [
    str(PYTHON_BIN), str(FINALIZE_BIN),
    "--code", code,
    "--customer-message-id", args.customer_message_id,
    "--selected-items-json", json.dumps(selected_items),
    "--quote-total-usd", str(total),
]
```

Customer ack text:

- rc 0: "Got it - your Option N selection is saved for owner approval. Final pricing comes after owner review."
- rc 6: "Got it - your Option N selection is saved for owner review. Final pricing comes after owner review."
- rc 2/4/11: "I could not lock that option in. Please reply with Option 1 or Option 2."

- [ ] **Step 4: Run selection tests**

Run:

```bash
pytest tests/test_select_catering_proposal.py -q
```

Expected: all pass.

- [ ] **Step 5: Checkpoint**

Task 3 execution note, 2026-05-13:

- Implemented without commit per user instruction.
- Official Windows test command is Linux-skipped by design: `python -m pytest tests/test_select_catering_proposal.py -q` => `21 skipped`.
- `python -m py_compile src/agents/catering/scripts/select-catering-proposal` passed.
- Worker used a temporary non-repo shim to exercise Linux-only tests: `21 passed`.
- Review fixes added:
  - Removed loose non-action numeric selection fallback.
  - Selection now loads current menu prices before invoking `finalize-catering-menu`.
  - Finalize rc 6 sends a best-effort owner alert while preserving `SELECTED_OWNER_CARD_FAILED`.
  - Selection claim/finalize/update is atomic under `PROPOSALS_LOCK`, with stale/newer proposal and concurrent selection guards.
  - Finalize subprocess exceptions now clean up `SELECTING` to `SELECT_FAILED`.
- Spec review and code-quality re-review approved.

Do not commit unless the user explicitly authorizes commits. If authorized:

```bash
git add src/agents/catering/scripts/select-catering-proposal tests/test_select_catering_proposal.py
git commit -m "feat: add catering proposal selection script"
```

---

### Task 4: Relabel Owner-Card Totals As Internal Estimates

**Files:**
- Modify: `src/agents/catering/templates/catering_finalized_menu_to_owner.txt`
- Modify: `src/agents/catering/scripts/finalize-catering-menu`
- Modify/Test: `tests/test_catering_finalize_menu.py` or add static test

- [ ] **Step 1: Write failing static test**

Add to `tests/test_catering_finalize_menu.py`:

```python
def test_owner_card_labels_total_as_internal_estimate():
    template = (TEMPLATES_DIR / "catering_finalized_menu_to_owner.txt").read_text(encoding="utf-8")
    assert "Internal estimate from current menu item prices" in template
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "Internal estimate from current menu item prices" in script_text
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
pytest tests/test_catering_finalize_menu.py::test_owner_card_labels_total_as_internal_estimate -q
```

Expected: fail.

- [ ] **Step 3: Update template and fallback**

Replace customer-confirmed total wording with:

```text
Internal estimate from current menu item prices: ${quote_total_usd}
Review/edit before approving the final customer quote.
```

Update the inline fallback in `_render_owner_card()` with the same wording.

- [ ] **Step 4: Run focused test**

Run:

```bash
pytest tests/test_catering_finalize_menu.py::test_owner_card_labels_total_as_internal_estimate -q
```

Expected: pass.

Task 4 execution note, 2026-05-13:

- Implemented without commit per user instruction.
- Official Windows focused test is Linux-skipped by existing module guard: `python -m pytest tests/test_catering_finalize_menu.py::test_owner_card_labels_total_as_internal_estimate -q` => `1 skipped`.
- `python -m py_compile src/agents/catering/scripts/finalize-catering-menu` passed.
- Direct static verification confirmed both template and fallback contain `Internal estimate from current menu item prices`.
- Spec/code-quality review approved.

---

### Task 5: Source-Control Creative Proposal Skill And Dispatcher Text

**Files:**
- Create: `src/agents/catering/skills/creative_catering_proposals/SKILL.md`
- Modify: `src/agents/catering/skills/catering_dispatcher/SKILL.md`
- Modify: `src/agents/shift/skills/dispatch_shift_agent/SKILL.md`
- Create/Modify: `tests/test_catering_proposal_skill_md.py`

- [ ] **Step 1: Write SKILL static tests**

Create `tests/test_catering_proposal_skill_md.py`:

```python
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "src" / "agents" / "catering" / "skills" / "creative_catering_proposals" / "SKILL.md"
DISPATCHER = REPO / "src" / "agents" / "catering" / "skills" / "catering_dispatcher" / "SKILL.md"
SHIFT = REPO / "src" / "agents" / "shift" / "skills" / "dispatch_shift_agent" / "SKILL.md"


def test_creative_skill_forbids_customer_pricing_and_send_message():
    text = SKILL.read_text(encoding="utf-8")
    assert "create-catering-proposal-options" in text
    assert "NEVER call send_message" in text
    assert "NEVER include prices" in text
    assert "payment" in text.lower()


def test_catering_dispatcher_has_proposal_decision_matrix():
    text = DISPATCHER.read_text(encoding="utf-8")
    assert "creative_catering_proposals" in text
    assert "select-catering-proposal" in text
    assert "Owner reply path" in text


def test_shift_dispatcher_uses_active_lead_condition_not_global_option_keyword():
    text = SHIFT.read_text(encoding="utf-8")
    assert "active non-terminal catering lead" in text
    assert "proposal-selection" in text
    keyword_line = next(line for line in text.splitlines() if line.startswith("Catering keywords"))
    assert "`option`" not in keyword_line
    assert "`proposal`" not in keyword_line
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_catering_proposal_skill_md.py -q
```

Expected: missing skill / missing text.

- [ ] **Step 3: Add constrained skill**

Create `SKILL.md` with frontmatter:

```markdown
---
name: creative_catering_proposals
description: Generate menu-grounded catering proposal options for an active catering lead. MUST invoke /usr/local/bin/create-catering-proposal-options and MUST NOT send customer messages directly or include prices/payment/booking language.
---
```

Body requirements:

- Read active lead by id or sender context.
- Read `/opt/shift-agent/state/catering-menu.json`.
- Produce 2 options unless request text asks for three/3.
- Output JSON only to `create-catering-proposal-options --options-json -`.
- Use exact menu item names.
- Never call `send_message`.
- Never include prices, deposits, payments, booking confirmation.

- [ ] **Step 4: Update dispatcher SKILLs**

In `catering_dispatcher/SKILL.md`, add the matrix from the spec.

In `dispatch_shift_agent/SKILL.md`, add the active-lead-conditioned addendum without adding bare global keywords.

- [ ] **Step 5: Run SKILL static tests**

Run:

```powershell
pytest tests/test_catering_proposal_skill_md.py -q
```

Expected: pass.

Task 5 execution note, 2026-05-13:

- Implemented without commit/stage per user instruction.
- RED: `python -m pytest tests/test_catering_proposal_skill_md.py -q` initially failed on missing skill/dispatcher text.
- GREEN: static tests now pass with `4 passed`.
- Review fix added: `creative_catering_proposals` now shows the full `create-catering-proposal-options` invocation with `--lead-id`, `--customer-jid`, `--source-message-id`, `--request-text`, and `--options-json -`.
- The old live VPS skill was checked via SSH two-step and intentionally not copied because it included unsafe customer-facing price ranges/freeform proposal behavior.
- Spec/code-quality re-review approved.

---

### Task 6: Add cf-router Proposal Branch Behind A Flag

**Files:**
- Modify: `src/plugins/cf-router/actions.py`
- Modify: `src/plugins/cf-router/hooks.py`
- Modify: `tests/test_cf_router_plugin.py`

- [ ] **Step 1: Write cf-router tests**

Add tests:

```python
def test_proposal_branch_disabled_keeps_existing_suppression(mods, state_env):
    hooks_mod, actions_mod = mods
    hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = False
    _seed_lead(state_env, status="AWAITING_OWNER_APPROVAL")
    event = _event("She wants one mixed option and one premium option.", chat_id="201975216009469@lid")
    result = hooks_mod.pre_gateway_dispatch(event)
    assert result["action"] == "skip"
    assert "follow-up" in result["reason"]
```

```python
def test_proposal_request_actionable_allows_dispatch_when_flag_enabled(mods, state_env):
    hooks_mod, actions_mod = mods
    hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
    _seed_lead(state_env, status="AWAITING_OWNER_APPROVAL")
    event = _event("She wants one mixed option and one premium option.", chat_id="201975216009469@lid")
    result = hooks_mod.pre_gateway_dispatch(event)
    assert result is None
```

```python
def test_passive_wait_still_suppresses_when_flag_enabled(mods, state_env):
    hooks_mod, actions_mod = mods
    hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
    _seed_lead(state_env, status="AWAITING_OWNER_APPROVAL")
    event = _event("Will wait for two menu proposals. Thank you!", chat_id="201975216009469@lid")
    result = hooks_mod.pre_gateway_dispatch(event)
    assert result["action"] == "skip"
```

```python
def test_selection_intercepts_outside_catering_classifier(mods, state_env):
    hooks_mod, actions_mod = mods
    hooks_mod.F7_PROPOSAL_BRANCH_ENABLED = True
    _seed_lead(state_env, status="AWAITING_OWNER_APPROVAL")
    _seed_sent_proposal_set(state_env, lead_id="L0001")
    with patch.object(actions_mod, "invoke_select_catering_proposal", return_value=0) as mock_select:
        event = _event("go with option 2", chat_id="201975216009469@lid")
        result = hooks_mod.pre_gateway_dispatch(event)
    assert result["action"] == "skip"
    mock_select.assert_called_once()
```

- [ ] **Step 2: Run focused cf-router tests and verify failure**

Run:

```powershell
pytest tests/test_cf_router_plugin.py -q
```

Expected: new tests fail.

- [ ] **Step 3: Implement action helpers**

In `actions.py`, add:

```python
PROPOSALS_PATH = Path("/opt/shift-agent/state/catering-proposals.json")
SELECT_CATERING_PROPOSAL_BIN = Path("/usr/local/bin/select-catering-proposal")
```

Add pure helpers:

```python
def is_proposal_request(text: str) -> bool:
    # REQUEST_VERB within 80 chars before REQUEST_OBJECT, unless passive-only.
```

```python
def is_proposal_selection(text: str) -> bool:
    # Regex from spec.
```

```python
def find_selectable_proposal_set(lead_id: str) -> Optional[dict]:
    # Latest status SENT with outbound_message_id, not SUPERSEDED.
```

```python
def invoke_select_catering_proposal(lead_id: str, chat_id: str, message_id: str, text: str) -> int:
    result = subprocess.run(
        [
            str(PYTHON_BIN),
            str(SELECT_CATERING_PROPOSAL_BIN),
            "--lead-id", lead_id,
            "--customer-jid", chat_id,
            "--customer-message-id", message_id,
            "--selection-text", text,
        ],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=SUBPROCESS_TIMEOUT_SEC,
    )
    return result.returncode
```

- [ ] **Step 4: Implement hook flag and branch**

In `hooks.py`:

```python
F7_PROPOSAL_BRANCH_ENABLED = False
```

Inside Branch B after `active_lead` is known and before canonical follow-up:

```python
if F7_PROPOSAL_BRANCH_ENABLED and actions.is_proposal_selection(text):
    if actions.find_selectable_proposal_set(lead_id):
        rc = actions.invoke_select_catering_proposal(lead_id, chat_id, message_id, text)
        actions.audit_intercepted(
            reason="f7_proposal_selection",
            chat_id=chat_id,
            code=approval_code,
            subprocess_rc=rc,
            detail=f"active {lead_id}; selection handled by cf-router",
        )
        return {"action": "skip", "reason": f"cf-router F7 proposal selection for {lead_id}"}

if F7_PROPOSAL_BRANCH_ENABLED and actions.is_proposal_request(text):
    return None
```

- [ ] **Step 5: Run cf-router tests**

Run:

```powershell
pytest tests/test_cf_router_plugin.py -q
```

Expected: pass.

Task 6 execution note, 2026-05-13:

- Implemented without commit/stage per user instruction.
- Official Windows test command is Linux-skipped by existing module guard: `python -m pytest tests/test_cf_router_plugin.py -q` => `76 skipped`.
- `python -m py_compile src/plugins/cf-router/actions.py src/plugins/cf-router/hooks.py tests/test_cf_router_plugin.py` passed.
- Worker used direct in-process smoke/shim coverage for the four Task 6 happy paths and review regressions.
- Review fixes added:
  - `find_selectable_proposal_set` now only returns the latest proposal row if latest is `SENT` with an outbound id.
  - Proposal-selection classifier no longer catches non-action mentions like "revise option 2" or "don't like option 2".
  - Passive wait/status wording no longer routes to proposal generation.
  - Nonzero `select-catering-proposal` rc is audited but returns `None` so the LLM/dispatcher can recover instead of swallowing the message.
- Spec/code-quality re-review approved.

---

### Task 7: Update Routing Reliability Monitor

**Files:**
- Modify: `src/platform/scripts/dispatcher-accuracy-report`
- Modify: `tests/test_dispatcher_accuracy_report.py`

- [ ] **Step 1: Write failing monitor test**

Add:

```python
def test_cf_router_proposal_selection_pairs_like_dispatcher_routed(now):
    rows = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "m-prop", "sender_lid": "201@lid"},
        {
            "type": "cf_router_intercepted",
            "ts": _ts(now, 1),
            "reason": "f7_proposal_selection",
            "chat_id": "201@lid",
            "subprocess_rc": 0,
        },
    ]
    paired, unpaired = report_mod.pair_inbounds(rows)
    assert len(paired) == 1
    assert unpaired == []
    assert paired[0][2] == "cf_router_intercepted"
```

- [ ] **Step 2: Run focused test and verify failure**

Run:

```powershell
pytest tests/test_dispatcher_accuracy_report.py::test_cf_router_proposal_selection_pairs_like_dispatcher_routed -q
```

Expected: fail.

- [ ] **Step 3: Implement pairing**

In `pair_inbounds`, include:

```python
cf_router_routed = [
    e for e in entries
    if e.get("type") == "cf_router_intercepted"
    and e.get("reason") == "f7_proposal_selection"
]
```

Pair by timestamp window and sender LID/phone/chat id, same style as `unknown_sender_declined`.

Update report labels so `kind == "cf_router_intercepted"` contributes to paired count.

- [ ] **Step 4: Run monitor tests**

Run:

```powershell
pytest tests/test_dispatcher_accuracy_report.py -q
```

Expected: pass.

Task 7 execution note, 2026-05-13:

- Implemented without commit/stage per user instruction.
- RED: focused monitor test failed with `len(paired) == 0`.
- GREEN: `python -m pytest tests/test_dispatcher_accuracy_report.py -q` passed with `17 passed`.
- Review fix added: cf-router proposal-selection pairing now normalizes phone JIDs such as `15551234567@s.whatsapp.net` to raw inbound `+15551234567`, while non-`f7_proposal_selection` cf-router intercepts do not inflate coverage.
- Spec/code-quality re-review approved.

---

### Task 8: Update Deploy And Smoke Gates

**Files:**
- Modify: `src/agents/shift/scripts/shift-agent-deploy.sh`
- Modify: `src/agents/shift/scripts/shift-agent-smoke-test.sh`

- [ ] **Step 1: Write/extend smoke expectations**

Add required skill:

```bash
creative_catering_proposals
```

Add smoke checks:

```bash
test -x /usr/local/bin/create-catering-proposal-options
test -x /usr/local/bin/select-catering-proposal
test -f /root/.hermes/skills/creative_catering_proposals/SKILL.md
python3 - <<'PY'
from pathlib import Path
for p in [
    Path("/root/.hermes/plugins/cf-router/actions.py"),
    Path("/root/.hermes/plugins/cf-router/hooks.py"),
]:
    compile(p.read_text(), str(p), "exec")
PY
```

- [ ] **Step 2: Update deploy script required skills**

In `shift-agent-deploy.sh` `required_skills`, add `creative_catering_proposals`.

- [ ] **Step 3: Run shellcheck-equivalent smoke if available**

Run:

```powershell
pytest tests/test_repo_invariants.py -q
```

Expected: pass.

Task 8 execution note, 2026-05-13:

- Implemented without commit/stage per user instruction.
- `python -m pytest tests/test_repo_invariants.py -q` passed with `2 passed`.
- Git Bash syntax checks passed for `shift-agent-deploy.sh` and `shift-agent-smoke-test.sh`.
- Review fixes added:
  - cf-router `actions.py`/`hooks.py` compile gate now runs pre-restart in deploy, before `systemctl restart hermes-gateway`, with rollback/evict behavior.
  - `creative_catering_proposals` is not in the unconditional deploy `required_skills` gate so rollback to a pre-Task8 tarball is still possible; forward deploy smoke still requires the skill.
- Spec/code-quality re-review approved.

---

### Task 9: Final Verification And Deploy

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Run local focused tests**

Run:

```powershell
pytest tests/test_catering_proposal_schemas.py tests/test_catering_proposal_skill_md.py tests/test_cf_router_plugin.py tests/test_dispatcher_accuracy_report.py -q
```

Expected: pass. Linux-only script tests may skip on Windows; run them on VPS or Linux before deploy.

- [ ] **Step 2: Run Linux-only script tests**

On Linux:

```bash
pytest tests/test_create_catering_proposal_options.py tests/test_select_catering_proposal.py tests/test_catering_finalize_menu.py -q
```

Expected: pass.

- [ ] **Step 3: Build and deploy tarball with flag false**

Use the existing tarball deploy path. Ensure `F7_PROPOSAL_BRANCH_ENABLED = False` in deployed `hooks.py` before first restart.

- [ ] **Step 4: Verify deployed files using SSH two-step pattern**

Run from Windows:

```powershell
ssh main-vps 'bash -s' > .ssh_proposal_deploy_verify.txt 2>&1 <<'REMOTE'
set -e
test -x /usr/local/bin/create-catering-proposal-options
test -x /usr/local/bin/select-catering-proposal
test -f /root/.hermes/skills/creative_catering_proposals/SKILL.md
grep -q '^F7_PROPOSAL_BRANCH_ENABLED = False' /root/.hermes/plugins/cf-router/hooks.py
systemctl is-active hermes-gateway
curl -fsS http://127.0.0.1:3000/health
REMOTE
```

Then read `.ssh_proposal_deploy_verify.txt`.

- [ ] **Step 5: Enable flag and restart**

After deploy verification:

```powershell
ssh main-vps "sed -i 's/^F7_PROPOSAL_BRANCH_ENABLED = False/F7_PROPOSAL_BRANCH_ENABLED = True/' /root/.hermes/plugins/cf-router/hooks.py && systemctl restart hermes-gateway" > .ssh_enable_proposal_branch.txt 2>&1
```

Then read `.ssh_enable_proposal_branch.txt`.

- [ ] **Step 6: Live smoke**

Ask user to send:

```text
Need catering for 80 people next Friday
```

Then:

```text
Please send two proposals: one mixed veg/non-veg and one premium option.
```

Then:

```text
Go with option 2.
```

Verify in `/opt/shift-agent/logs/decisions.log`:

- `catering_lead_created`
- `catering_proposals_generated`
- `catering_proposal_selected`
- `catering_menu_finalized`
- owner card delivered
- no customer price/payment language before owner approval

- [ ] **Step 7: Update task checklist**

Mark implementation, tests, deploy, and smoke results in `tasks/todo.md`.

---

## Self-Review

Spec coverage:

- Sidecar state and lock: Task 1 and Task 2.
- Proposal lifecycle and non-selectable send failures: Task 1, Task 2, Task 3.
- Finalize `--code` and exit-code behavior: Task 3.
- Prose grounding and no-price regex: Task 2.
- Routing reachability and pinned-test behavior: Task 5 and Task 6.
- Dispatcher accuracy audit gap: Task 7.
- Owner-card estimate label: Task 4.
- Feature-flag rollout: Task 6, Task 8, Task 9.

Draft-token scan:

- No draft tokens or unspecified implementation steps remain.

Type consistency:

- Script/status names match the approved spec: `SENT`, `SEND_FAILED`, `SUPERSEDED`, `SELECTED`, `SELECTED_OWNER_CARD_FAILED`, `SELECT_FAILED`.
- Proposal IDs use `CPS-{lead_id}-{sequence:06d}` throughout.

---

## Task 9 Local Execution Note

Completed locally on Windows, without commit/stage/deploy:

- Focused cross-platform suite:
  `python -m pytest tests/test_catering_proposal_schemas.py tests/test_catering_proposal_skill_md.py tests/test_cf_router_plugin.py tests/test_dispatcher_accuracy_report.py tests/test_dispatcher_replay.py tests/test_repo_invariants.py -q`
  -> `68 passed, 80 skipped`.
- Linux-only script suites:
  `python -m pytest tests/test_create_catering_proposal_options.py tests/test_select_catering_proposal.py tests/test_catering_finalize_menu.py -q`
  -> skipped on Windows by design (`safe_io` imports Linux-only `fcntl`).
- Compile:
  proposal scripts, finalize script, cf-router modules, dispatcher report, and replay harness all pass `python -m py_compile`.
- Shell syntax:
  Git Bash `bash -n` passes for `shift-agent-deploy.sh` and `shift-agent-smoke-test.sh`.
- Diff hygiene:
  `git diff --check` passes with line-ending warnings only.
- Full suite:
  `python -m pytest -q` -> `7 failed, 741 passed, 598 skipped`.
  Remaining failures are unrelated local baseline issues:
  `tests/test_pr_b_v3_static.py::test_template_machinery_deleted` substring-matches `_render_quote_from_lead_state`, and web backend tests import `safe_io` on Windows where `fcntl` is unavailable.

Final review fixes applied:

- cf-router skips LLM fallback for handled proposal-selection exits `{0,2,4,6,11}`.
- `select-catering-proposal` releases `PROPOSALS_LOCK` before invoking `finalize-catering-menu`, then reacquires and rechecks for newer superseding proposal sets before marking `SELECTED`.
- proposal generation requires exact option count: 2 by default, 3 only on explicit request.
- generation failures audit and best-effort alert owner, including schema-level invalid option failures.
- Final focused re-review reported no blockers after these fixes.
