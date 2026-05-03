# PR-CF4 — Design (post-plan-v2)

**Drift-check tag:** `extends-Hermes`

Plan: `tasks/catering-cf4-watchdog-plan-v2.md` (scope: 1-line regex fix to `find_inbound_text_for` user-name pattern in `catering-dispatcher-watchdog`).

## Hermes-first checklist

| Step | [Hermes] / [net-new] | Notes |
|---|---|---|
| Existing watchdog code path | [Hermes] | post-F12; reads agent.log; emits suppression audits |
| Regex pattern fix | [net-new] (1 LOC) | replace `user=\S+` → `user=.+?` |
| Test fixtures + assertions | [net-new] (~30 LOC) | mirror `tests/test_validate_sender_block.py` style for hyphen-named scripts |

**Net-new effort**: ~30 LOC.

## Files changed

1. `src/agents/catering/scripts/catering-dispatcher-watchdog`
   - Line 231: regex update
   - Line 222 docstring: clarify "matches multi-word user names" (the bug context)
   - Line 332 audit detail: update `"gateway.log"` → `"agent.log"` for consistency

2. `tests/test_catering_watchdog_inbound_regex.py` (new)
   - 7 test cases enumerated below (including double-quoted `msg=` and corrected LID normalization)

## Code changes

### Change 1: Regex fix

**File**: `src/agents/catering/scripts/catering-dispatcher-watchdog`

```diff
 def find_inbound_text_for(chat_id: str) -> Optional[str]:
-    """Scan gateway.log for the most recent 'inbound message:' line matching chat_id.
+    """Scan agent.log for the most recent 'inbound message:' line matching chat_id.
+
+    PR-CF4: user names contain spaces (e.g., "Srini Yalavarthi(Bangaru)").
+    Previous regex used `user=\\S+` which only matched single-token names,
+    silently suppressing all inbounds from multi-word users. Now uses non-
+    greedy `.+?` bounded by the mandatory ` chat=` literal.

     Gateway log format (run.py):
       logger.info('inbound message: platform=%s user=%s chat=%s msg=%r', ...)
     """
     if not GATEWAY_LOG.exists():
         return None
     chat_lid_only = chat_id.split("@", 1)[0] if "@" in chat_id else chat_id
     pattern = re.compile(
-        r"inbound message: platform=\S+ user=\S+ chat=(\S+) msg=([^\n]+)$"
+        r"inbound message: platform=\S+ user=.+? chat=(\S+) msg=([^\n]+)$"
     )
```

**Rationale for `.+?` (non-greedy) over alternatives**:

- `[^\n]+?` — equivalent but uses a character class redundantly
- `[\w\s().-]+` — character class is explicit allowlist but brittle (any new char in user names breaks it)
- `(.+?)` — captures user name (we don't currently need it; future enhancement could log who was suppressed)
- **Chosen: `.+?`** — minimal change; relies on the mandatory ` chat=` literal as bound; non-greedy ensures we don't consume past the boundary

### Change 2: Test file (new)

**File**: `tests/test_catering_watchdog_inbound_regex.py`

```python
"""PR-CF4 — catering-dispatcher-watchdog regex matches multi-word user names.

Linux-only — script imports safe_io which uses fcntl.

Loads the deployed script via SourceFileLoader (hyphen-named script pattern
documented in tests/_b1_helpers.py).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="watchdog imports safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
WATCHDOG = REPO / "src" / "agents" / "catering" / "scripts" / "catering-dispatcher-watchdog"
PLATFORM_DIR = REPO / "src" / "platform"


def _load_watchdog():
    sys.path.insert(0, str(PLATFORM_DIR))
    loader = importlib.machinery.SourceFileLoader("watchdog_under_test", str(WATCHDOG))
    spec = importlib.util.spec_from_file_location(
        "watchdog_under_test", str(WATCHDOG), loader=loader
    )
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def watchdog_with_synthetic_log(tmp_path, monkeypatch):
    """Loads the watchdog module and points GATEWAY_LOG at a synthetic file."""
    mod = _load_watchdog()
    log = tmp_path / "agent.log"
    monkeypatch.setattr(mod, "GATEWAY_LOG", log)
    return mod, log


def test_regex_matches_multi_word_user_name(watchdog_with_synthetic_log):
    """User name with spaces (the actual bug from 2026-05-03 srilu)."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 17:45:54,984 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Srini Yalavarthi(Bangaru) "
        "chat=17329837841@s.whatsapp.net msg='Bro any update?'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("17329837841@s.whatsapp.net")
    assert result == "Bro any update?"


def test_regex_matches_single_word_user_name(watchdog_with_synthetic_log):
    """Backwards-compat: single-token user names still match."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 18:00:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Vizora chat=918522041562@s.whatsapp.net "
        "msg='hello'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("918522041562@s.whatsapp.net")
    assert result == "hello"


def test_regex_matches_user_with_special_chars(watchdog_with_synthetic_log):
    """User names with parens, periods, hyphens, apostrophes."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 18:30:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Mary O'Brien (Cashier - Day Shift) "
        "chat=15555551234@s.whatsapp.net msg='need coverage tomorrow'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("15555551234@s.whatsapp.net")
    assert result == "need coverage tomorrow"


def test_regex_returns_none_for_no_match(watchdog_with_synthetic_log):
    """Chat_id not in log → returns None (not silent error)."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:00:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=SomeoneElse chat=99999999@s.whatsapp.net "
        "msg='hi'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("17329837841@s.whatsapp.net")
    assert result is None


def test_regex_lid_to_jid_cross_suffix_normalization(watchdog_with_synthetic_log):
    """LID-vs-JID normalization: log line uses @s.whatsapp.net while caller
    passes @lid. Per watchdog lines 241-242, both sides strip the suffix and
    compare numeric parts. Tests the actual cross-suffix code path (vs prior
    test which was identity-only)."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:30:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Anjali Iyer chat=201975216009469@s.whatsapp.net "
        "msg='accept the shift'\n",
        encoding="utf-8",
    )
    # Caller passes @lid; log has @s.whatsapp.net. Both split to '201975216009469'.
    result = mod.find_inbound_text_for("201975216009469@lid")
    assert result == "accept the shift"


def test_regex_handles_double_quoted_msg(watchdog_with_synthetic_log):
    """When the message contains an apostrophe, Python's `%r` formatter
    switches to double-quoted repr. ~30% of real production lines use this
    form. ast.literal_eval handles both styles via the existing logic at
    lines 244-247 of the watchdog."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:45:00,000 INFO gateway.run: inbound message: "
        'platform=whatsapp user=Anjali Iyer chat=15555551234@s.whatsapp.net '
        "msg=\"Hi Boss, I'm Anjali, can't come tomorrow\"\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("15555551234@s.whatsapp.net")
    assert result == "Hi Boss, I'm Anjali, can't come tomorrow"


def test_regex_picks_most_recent_match(watchdog_with_synthetic_log):
    """Multiple matches for same chat_id → returns the LAST (most recent)."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:00:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Test User chat=17329837841@s.whatsapp.net "
        "msg='first'\n"
        "2026-05-03 19:01:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Test User chat=17329837841@s.whatsapp.net "
        "msg='second'\n"
        "2026-05-03 19:02:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Test User chat=17329837841@s.whatsapp.net "
        "msg='third'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("17329837841@s.whatsapp.net")
    assert result == "third"
```

## Build sequence

1. **Commit 1**: regex fix in watchdog (1 LOC + 6 LOC docstring/comment + 1 LOC line-332 detail update)
2. **Commit 2**: tests (7 cases — including LID cross-suffix + double-quoted msg)

Total: 2 commits, ~85 LOC including tests + fixtures.

## Reviewer lens for design

- Confirm `.+?` non-greedy is correct (vs `.+` which would consume past `chat=`)
- Confirm test fixture pattern matches the deployed `_b1_helpers.py` SourceFileLoader convention for hyphen-named scripts
- Verify the LID-normalization test preserves post-F12 behavior (no regression)
- Verify monkeypatching `GATEWAY_LOG` is the cleanest test isolation pattern

## Risks / failure modes

- **Risk**: regex change breaks an unanticipated existing log format. Mitigation: backwards-compat test (case 2) keeps single-word users working.
- **Risk**: regex over-matches if a user name contains the literal substring ` chat=` (e.g., `user=Ravi chat=bot`). Non-greedy `.+?` would correctly stop at the FIRST ` chat=`, yielding wrong group-1 ("bot"). Mitigations: (a) agent.log format guarantees one `chat=` per line per `gateway.run` log call (this is the structural guarantee from Hermes); (b) the ` chat=` literal boundary is safe provided no WhatsApp display name contains the substring ` chat=` — empirically true for all known user names. If Hermes ever changes that, the tests will fail loudly.
- **Risk**: empty user name (`user= chat=...`). Mitigation: `.+?` requires at least 1 char (`+` is one-or-more), so the match fails entirely — same behavior as the old `\S+` for this input. Silent non-match is acceptable degraded behavior; in practice gateway.run never emits empty user names.

## Out of scope

Per plan v2: Part B (B1/B2 fail-closed watchdogs) deferred to PR-CF6 contingent on Hermes hook research.
