# PR Review 1/5 — Code Quality + Conventions (pr-review-toolkit:code-reviewer)

**Verdict:** 4 BLOCKERs, 9 MAJORs, 2 MINORs.

## BLOCKERS (fixed in commit f1806f0)

**B1.** `urllib.parse` never imported in `shift-agent-notify-owner` — AttributeError on first Pushover call. → fixed
**B2.** `E164Phone` uses Pydantic v1 `__get_validators__` API on Pydantic v2 codebase → validators never run. → fixed via `__get_pydantic_core_schema__`
**B3.** `log-decision` legacy-compat path silently drops entries missing `type` field. → TODO: reject or auto-wrap with error
**B4.** `create-proposal` appends `ProposalCreated` AFTER releasing pending lock → orphan risk if process dies between dump_model and ndjson_append. → TODO: log first under lock, then pending update.

## MAJORS

- `send-coverage-message` sleeps for rate-limit WHILE HOLDING pending+counter locks — blocks all other proposal ops up to 2s at 30/min. Move sleep before locks. [open]
- `safe_load_json` `path.with_suffix(path.suffix + ".corrupt-X")` raises ValueError on dotted suffix. → fixed via `path.with_name`
- `atomic_write_text` same bug → fixed
- `shift-agent-backup.sh` `grep -c "a\|b"` regex counts lines not files → weakened invariant. TODO: grep -Fxq per expected file.
- `shift-agent-backup.sh` parses YAML with `grep | sed` → fragile. TODO: python3 yaml.safe_load oneliner.
- `shift-agent-health-check.sh` `grep -q '"data"'` substring match accepts 401 error bodies containing "data". TODO: curl -w %{http_code} comparison.
- `shift-agent-notify-owner` used deprecated `datetime.utcnow()` returning naive → fixed
- `Roster.find_by_phone` `h.effective_to is None or True` dead logic → fixed
- `send-coverage-message._revert_everything` uses stale `proposal` variable from outside lock → TODO: re-read under lock + assert status.

## MINORS

- `shift-agent-fsck.py:141` dead code `if False else` → TODO: delete
- Unused imports across several scripts (schemas.py `constr`, send-coverage-message `E164Phone`, etc.). Run `pyflakes` → TODO.
