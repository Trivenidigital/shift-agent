"""Hermes+OpenRouter bare-trunk flyer generation (Slice 1 + Slice 2 wiring).

The bare trunk is one OpenRouter integrated-poster generation. It does NOT reinvent grounding or QA:
it builds locked facts with the existing `facts.py` extractor (registered identity + brief content +
firewall-cleared `creative_planner` items) and gates the render with the existing `visual_qa.run_visual_qa`
(item-count/coverage, invented-date/claim, wrong-brand, locked-fact presence). Only a thin cross-business
identity conflict pre-check survives from the earlier slice (the one policy facts.py doesn't enforce).

Pure identity logic lives at module top (unit-tested). Heavy deps are lazy-imported inside functions.
"""
from __future__ import annotations

import itertools
import re

# --- identity conflict pre-check (cross-business safety) -----------------------
_BUSINESS_TYPE_TOKENS = {
    "kitchen", "restaurant", "cafe", "grill", "bar", "salon", "spa", "studio", "store", "shop",
    "market", "supermarket", "mart", "bakery", "pizza", "pizzeria", "eatery", "diner", "deli",
    "hair", "nail", "barbershop", "barber", "boutique", "grocery", "cuisine", "catering", "caterer",
    "bistro", "tavern", "pub", "lounge", "club", "hotel", "motel", "inn", "foods", "food",
    "meats", "meat", "pharmacy", "clinic", "gym", "fitness", "auto", "garage", "bakehouse",
}
_GENERIC_FILLER = {
    "and", "of", "for", "specials", "special", "menu", "sale", "offer", "deal", "deals",
    "promo", "promotion", "weekend", "daily", "weekly", "monthly", "new", "grand", "opening",
    "best", "house", "co", "company", "llc", "inc", "ltd",
    "my", "our", "your", "this", "that", "in", "town", "local", "here",
}
_ARTICLES = {"a", "an", "the"}


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


_BUSINESS_TYPE_STEMS = {_stem(w) for w in _BUSINESS_TYPE_TOKENS}
_GENERIC_STOPLIST = {_stem(w) for w in (_BUSINESS_TYPE_TOKENS | _GENERIC_FILLER)}


def _name_tokens(name: str) -> list[str]:
    s = (name or "").lower().replace("’", "'")
    s = re.sub(r"'s\b", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    toks = [_stem(t) for t in s.split() if t]
    return [t for t in toks if t not in _ARTICLES]


def _distinctive_tokens(name: str) -> set[str]:
    return {t for t in _name_tokens(name) if t not in _GENERIC_STOPLIST}


def _has_business_type_token(name: str) -> bool:
    return any(t in _BUSINESS_TYPE_STEMS for t in _name_tokens(name))


def reconcile_identity(stated_name, customer):
    """Cross-business conflict gate (the one identity policy facts.py does not enforce).

    Returns (verdict, canonical):
      - ("conflict", None)    stated name names a DIFFERENT business -> block, no contact leak
      - ("ok", canonical)     no conflict (registered/alias/generic/event) -> proceed; facts.py grounds it
      - ("unregistered", stated)
    facts.py owns identity grounding (profile facts + allow_text_identity); this only fails closed when
    the message names a materially different business than the registered account.
    """
    if customer is None:
        return ("unregistered", (stated_name or "").strip() or None)
    canonical = (getattr(customer, "business_name", "") or "").strip()
    stated = (stated_name or "").strip()
    if not stated:
        return ("ok", canonical)
    stated_distinct = _distinctive_tokens(stated)
    if not stated_distinct:
        return ("ok", canonical)
    if stated_distinct & _distinctive_tokens(canonical):
        return ("ok", canonical)
    if _has_business_type_token(stated):
        return ("conflict", None)
    return ("ok", canonical)


# --- lazy dual-path imports (src layout vs deployed flat layout) ---------------
def _schemas():
    import schemas
    return schemas


def _render_mod():
    try:
        import flyer_render as R
    except ImportError:
        from agents.flyer import render as R
    return R


def _intake_fields():
    try:
        import flyer_intake_fields as IF
    except ImportError:
        from agents.flyer import intake_fields as IF
    return IF


def _facts_mod():
    try:
        import flyer_facts as F
    except ImportError:
        from agents.flyer import facts as F
    return F


def _visual_qa_mod():
    try:
        import flyer_visual_qa as VQ
    except ImportError:
        from agents.flyer import visual_qa as VQ
    return VQ


def _context_builder():
    """The Creative-Director brief builder (PR3). Flat on the VPS as
    flyer_context_builder, package-style in the repo tree (mirrors facts/render).
    build_flyer_brief internally reuses flyer_brief_validator.required_fact_ids to
    enforce required-fact coverage, so a brief omitting a required fact returns
    status="invalid" before this caller ever renders."""
    try:
        import flyer_context_builder as CB
    except ImportError:
        from agents.flyer import flyer_context_builder as CB
    return CB


# --- config -------------------------------------------------------------------
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
GEN_MODEL = os.environ.get("FLYER_BARE_GEN_MODEL", "google/gemini-3-pro-image-preview")
CUSTOMERS_PATH = Path(os.environ.get("FLYER_CUSTOMERS_PATH", "/opt/shift-agent/state/flyer/customers.json"))
CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG", "/opt/shift-agent/config.yaml"))
_ENV_PATHS = ["/root/.hermes/.env", "/opt/shift-agent/.env"]

# --- slice 2a: flyer session persistence (for revision-apply) ------------------
# On a successful send, render_grounded persists the transient project + the path of the TEXTLESS
# raw background so a later uniform-price-header revision (slice 2c) can re-overlay the SAME
# background (no new generation, no credits). Keyed by sanitized chat — same scheme as the
# cf-router recent-flyer marker, so a routed --revision-apply finds the session.
SESSION_DIR = Path(os.environ.get("FLYER_BARE_SESSION_DIR", "/opt/shift-agent/state/bare_flyer/sessions"))
BG_DIR = Path(os.environ.get("FLYER_BARE_BG_DIR", "/opt/shift-agent/state/bare_flyer/backgrounds"))
_FALSE_VALUES = {"0", "false", "no", "off"}
REVISION_APPLY_ENABLED = (os.environ.get("FLYER_BARE_REVISION_APPLY", "1").strip().lower() not in _FALSE_VALUES)
# Slice 3: skill-driven iteration (re-roll / specific_revision / style_reuse) on the text --revision
# branch, instead of the "resend full details" dead-end. Default OFF (scoped via the allowlist).
ITERATION_ENABLED = (os.environ.get("FLYER_BARE_ITERATION", "0").strip().lower() not in _FALSE_VALUES)
REVISION_CAPTURE_RAW_BG = os.environ.get("FLYER_BARE_REVISION_CAPTURE_RAW_BG") == "1"

# --- PR3: Creative-Director render branch (flag + allowlist scoped) ------------
# Caller-provenance pin so the operator can verify, from the audit log, EXACTLY
# which deployed code emitted a row before enabling the feature.
MODULE_VERSION = "pr3-creative-director"
AUDIT_LOG_PATH = Path(os.environ.get("FLYER_DECISIONS_LOG", "/opt/shift-agent/logs/decisions.log"))
# The gate env vars (read at call time, NOT import time, so tests + an operator
# `export` take effect without a reimport). The flag MUST be exactly "1" AND the
# resolved sender MUST be in the allowlist for the CD path to arm — anything else
# is byte-identical legacy behavior.
CREATIVE_DIRECTOR_ENABLED_ENV = "FLYER_CREATIVE_DIRECTOR_ENABLED"
CREATIVE_DIRECTOR_ALLOWLIST_ENV = "FLYER_CREATIVE_DIRECTOR_ALLOWLIST"


def _normalize_sender(value: str) -> str:
    """Canonical comparison form for a phone/LID so the allowlist and the resolved
    sender match across format variants. Strips a chat-JID suffix (``@s.whatsapp.net``
    / ``@lid``), a leading ``+``, internal phone punctuation/whitespace, and
    case-folds. Preserves alphanumeric LID bodies (LIDs are not purely numeric)."""
    s = (value or "").strip()
    if "@" in s:
        s = s.split("@", 1)[0]
    s = s.lstrip("+")
    s = re.sub(r"[\s\-().]", "", s)
    return s.casefold()


def _creative_director_allowlist() -> set[str]:
    """Parse FLYER_CREATIVE_DIRECTOR_ALLOWLIST (comma-separated phones/LIDs) into a
    normalized set. Empty/unset ⇒ empty set ⇒ no sender is allowlisted."""
    raw = os.environ.get(CREATIVE_DIRECTOR_ALLOWLIST_ENV, "") or ""
    return {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}


def _resolved_sender(chat_id: str, sender_phone: str | None) -> str:
    """The trusted resolved sender bare_render already uses (resolve_customer's
    identifier): the passed sender phone/LID, else the phone embedded in a
    WhatsApp chat JID, else the chat_id itself (a LID JID). NEVER message content."""
    if sender_phone:
        return sender_phone
    if chat_id and chat_id.endswith("@s.whatsapp.net"):
        return chat_id.split("@", 1)[0]
    return chat_id or ""


def _creative_director_armed(resolved_sender: str) -> bool:
    """The PR3 gate: flag == "1" AND the normalized resolved sender is allowlisted."""
    if os.environ.get(CREATIVE_DIRECTOR_ENABLED_ENV) != "1":
        return False
    return _normalize_sender(resolved_sender) in _creative_director_allowlist()


# --- Advisory skill-driven integrated scene (separate flag from the CD+overlay path) ---
SKILL_DRIVEN_SCENE_ENABLED_ENV = "FLYER_SKILL_DRIVEN_SCENE"
SKILL_DRIVEN_SCENE_ALLOWLIST_ENV = "FLYER_SKILL_DRIVEN_SCENE_ALLOWLIST"


def _skill_driven_scene_allowlist() -> set[str]:
    raw = os.environ.get(SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, "") or ""
    return {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}


def _skill_driven_scene_armed(resolved_sender: str) -> bool:
    """Gate for the advisory integrated-scene art direction — INDEPENDENT of the CD+overlay flag.
    Flag == "1" AND the normalized resolved sender is allowlisted."""
    if os.environ.get(SKILL_DRIVEN_SCENE_ENABLED_ENV) != "1":
        return False
    return _normalize_sender(resolved_sender) in _skill_driven_scene_allowlist()


# Slice 3 iteration gate: FLYER_BARE_ITERATION flag + its own allowlist (scoped rollout).
ITERATION_ALLOWLIST_ENV = "FLYER_BARE_ITERATION_ALLOWLIST"


def _iteration_armed(resolved_sender: str) -> bool:
    """The iteration handler runs only when FLYER_BARE_ITERATION is on AND the resolved sender is in
    FLYER_BARE_ITERATION_ALLOWLIST (creative behavior, scoped like the scene flag — Codex 2026-06-07)."""
    if not ITERATION_ENABLED:
        return False
    raw = os.environ.get(ITERATION_ALLOWLIST_ENV, "") or ""
    allow = {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}
    return _normalize_sender(resolved_sender) in allow


def _advisory_scene_direction(raw_text: str, locked_facts, customer, resolved_sender: str):
    """ADVISORY occasion/theme art direction for the INTEGRATED renderer. Returns a VisualDirection
    when armed (FLYER_SKILL_DRIVEN_SCENE + allowlist) AND the skill produced one; otherwise None so the
    caller falls back to today's Python scene. NEVER raises and NEVER fail-closes — this improves
    vague-request quality, it is not a firewall, and it is decoupled from FLYER_CREATIVE_DIRECTOR_ENABLED
    and from build_flyer_brief's fail-closed contract."""
    if not _skill_driven_scene_armed(resolved_sender):
        return None
    try:
        cb = _context_builder()
        advise = getattr(cb, "advise_scene_direction", None)
        if advise is None:
            return None
        return advise(raw_text, locked_facts, customer)
    except Exception:  # noqa: BLE001 — advisory only; ANY failure -> None -> Python scene fallback
        return None


def _emit_creative_director_audit(*, chat_id: str, resolved_sender: str, reached: bool,
                                  status: str, allowlisted: bool,
                                  error_summary: str = "", errors: list[str] | None = None,
                                  unavailable_reason: str = "", render_error: str = "") -> None:
    """Emit the FlyerCreativeDirectorRouted row on EVERY new-flyer render via the
    canonical decisions.log chokepoint (ndjson_append + flock), mirroring
    generate-flyer-concepts:_audit_append. Emitted whether or not the flag is on, so
    "flag off ⇒ reached=False, status=disabled/not_allowlisted" is provable from
    logs. Best-effort: an audit failure must never block (or alter) the render.

    Observability (2026-06-06): ``error_summary`` / ``errors`` / ``unavailable_reason`` /
    ``render_error`` record WHY a non-shipping outcome happened so a failed live retest is
    diagnosable from the row alone (e.g. status="ok" + render_error reveals a brief that
    validated but failed to render). All optional + LOG-ONLY; never alters the render."""
    try:
        schemas = _schemas()
        try:
            from safe_io import flock as _flock, ndjson_append as _ndjson_append  # type: ignore
        except Exception:  # noqa: BLE001 — tests / non-VPS layouts may lack safe_io helpers (fcntl)
            _flock = None  # type: ignore
            _ndjson_append = None  # type: ignore
        entry = schemas.FlyerCreativeDirectorRouted(
            type="flyer_creative_director_routed",
            ts=datetime.now(timezone.utc),
            creative_director_reached=bool(reached),
            creative_director_status=status,
            module_version=MODULE_VERSION,
            module_file=str(__file__),
            resolved_sender=(resolved_sender or "")[:200],
            allowlisted=bool(allowlisted),
            chat_id=(chat_id or "")[:200],
            error_summary=(error_summary or "")[:200],
            # islice caps to the first 20 BEFORE str()-ing each — a huge/lazy iterable is
            # never fully consumed and a bad entry past index 20 can't drop the audit row.
            errors=[str(e)[:200] for e in itertools.islice(errors or [], 20)],
            unavailable_reason=(unavailable_reason or "")[:80],
            render_error=(render_error or "")[:120],
        )
        line = entry.model_dump_json()
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _flock is not None and _ndjson_append is not None:
            # VPS path: the canonical flock + ndjson_append chokepoint (same as
            # generate-flyer-concepts:_audit_append).
            with _flock(AUDIT_LOG_PATH):
                _ndjson_append(AUDIT_LOG_PATH, line)
        else:
            # Non-VPS / fcntl-less env (e.g. tests): plain open-append-close so the
            # row is still emitted. Logrotate uses `create` mode → never cache the fd.
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        return


def _sanitize_chat(chat_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]", "_", chat_id or "")[:80]


def _session_path(chat_id: str) -> Path:
    return SESSION_DIR / f"{_sanitize_chat(chat_id)}.json"


def _pending_session_path(chat_id: str) -> Path:
    return SESSION_DIR / f"{_sanitize_chat(chat_id)}.pending.json"


def _unique_bg_path(chat_id: str) -> Path:
    """A per-render background path so a failed send never overwrites the background the previously
    committed session points to (Codex F3)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return BG_DIR / f"{_sanitize_chat(chat_id)}.{ts}.raw.png"


def _price_from_text(text: str):
    """The single distinct $-price the customer named in this revision text, or None. The requested
    header price (e.g. '$8.99') must win over the session's existing item price (Codex F1)."""
    prices = {re.sub(r"\s+", "", p) for p in re.findall(r"\$\s?\d+(?:\.\d{1,2})?", text or "")}
    return next(iter(prices)) if len(prices) == 1 else None


def _write_session(chat_id: str, project, raw_bg_path, brief: str, *, model: str = "",
                   size=(1080, 1350), pending: bool = False) -> None:
    """Persist the flyer's session (full project dump + raw textless bg path). Writes a PENDING file
    that the orchestrator commits only after delivery succeeds (Codex F3). Best-effort — a failure
    here must never break the send."""
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = _pending_session_path(chat_id) if pending else _session_path(chat_id)
        path.write_text(json.dumps({
            "chat_id": chat_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "brief": (brief or "")[:2000],
            "project": json.loads(project.model_dump_json()),  # datetime-safe; FlyerProject is extra=forbid
            "raw_background_path": str(raw_bg_path),
            "model": model or GEN_MODEL,
            "output_size": list(size),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def commit_session(chat_id: str) -> None:
    """Promote the pending session to committed (called by the orchestrator AFTER delivery succeeds,
    alongside the recent marker). A failed send leaves the pending file uncommitted, so a later
    revision keeps using the last DELIVERED flyer's session/background. Best-effort."""
    try:
        pending = _pending_session_path(chat_id)
        if pending.exists():
            os.replace(pending, _session_path(chat_id))
    except Exception:  # noqa: BLE001
        pass


def _load_session(chat_id: str):
    """Load the committed session dict for a chat, or None."""
    try:
        p = _session_path(chat_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None

SEND = "send"
CONFLICT = "conflict"
FAILCLOSED = "failclosed"
UNREGISTERED = "unregistered"
REVISION_NEEDED = "revision_needed"
REROLL = "reroll"  # no-change "generate again" -> re-render the saved validated project, fresh variant
ITERATION_REVISED = "iteration_revised"          # specific_revision: re-render saved project, new scene
ITERATION_STYLE_REUSE = "iteration_style_reuse"  # style_reuse: new flyer (new facts) + prior style
ITERATION_UNCLEAR = "iteration_unclear"          # no/stale session or unclear -> one concise question

# Customer-facing copy attached to a re-roll's fresh variant (operator-approved 2026-06-07). It tells
# the customer the same details were kept and invites a SPECIFIC change (which routes to the revision
# path) rather than another vague re-roll.
REROLL_INVITE = (
    "I made a fresh version using the same details. If you want specific changes — "
    "wording, items, prices, dates, colors, or layout — just tell me what to adjust."
)

# A saved session older than this is treated as stale: re-roll fails closed (asks for details) rather
# than silently re-rendering an abandoned flyer. Generous default (7 days); real re-rolls happen within
# minutes. Env-overridable for ops.
_REROLL_MAX_AGE_HOURS = float(os.environ.get("FLYER_BARE_REROLL_MAX_AGE_HOURS", "168") or "168")

# A follow-up that references a prior flyer / asks for a change. The stateless bare trunk has no prior
# context to merge against (Rewire 2 / persistence), so rendering it as a fresh brief invents a flyer
# (the reported "Grand Opening" failure). Until merge lands, detect and BLOCK these (ask for full brief).
_REVISION_RE = re.compile(
    r"\b(?:the\s+(?:generated\s+)?flyer|this\s+flyer|the\s+image|the\s+poster|the\s+design)\b"
    r"|\byou\s+(?:missed|forgot|did\s*n.?t|did\s+not|left\s+out)\b"
    r"|\b(?:please\s+)?(?:re-?do|re-?generate|regenerate|try\s+again|do\s+it\s+again)\b"
    r"|\b(?:change|update|fix|correct|replace|remove|edit|redo)\s+(?:the|that|this|its)\b"
    r"|\bexplicitly\s+asked\b",
    re.IGNORECASE,
)


def _looks_like_revision(text: str) -> bool:
    return bool(_REVISION_RE.search(text or ""))


# A PURE re-roll = "make it again, same details" with NO specific change. We re-render the saved
# project verbatim, so we must NOT treat a request that ALSO carries a change as a re-roll — even
# when the change sits between the verb and "again" ("generate this flyer in blue again", "...no
# Italian again", "...add phone again"). Detection is TOKEN-based, NOT span-removal: require a
# re-roll signal AND that every word is a re-roll/filler token; any other word (a noun/adjective/
# number/new value) is a specific change -> route to REVISION_NEEDED (Codex 2026-06-07).
_REROLL_SIGNAL_RE = re.compile(
    r"\b(?:re-?generate|regenerate|re-?do|redo|re-?create|recreate|re-?make|remake|"
    r"do\s+it\s+again|do\s+over|try\s+again|start\s+over|once\s+more|make\s+another|"
    r"another\s+(?:one|version|take|go)|new\s+version)\b",
    re.IGNORECASE,
)
_QUALITY_REROLL_SIGNAL_RE = re.compile(
    r"\b(?:flyer|flier|poster|design|image|output|quality|this|it)\b"
    r"[\s\S]{0,120}\b(?:bad|poor|terrible|awful|miserable|unacceptable|not\s+acceptable|"
    r"not\s+good|can(?:not|'?t)\s+accept|cant\s+accept)\b"
    r"|"
    r"\b(?:bad|poor|terrible|awful|miserable|unacceptable|not\s+acceptable|"
    r"not\s+good|can(?:not|'?t)\s+accept|cant\s+accept)\b"
    r"[\s\S]{0,120}\b(?:flyer|flier|poster|design|image|output|quality|this|it)\b",
    re.IGNORECASE,
)
_REROLL_VERB_RE = re.compile(r"\b(?:generate|create|make|design|render|do|build|produce)\b", re.IGNORECASE)
_AGAIN_RE = re.compile(r"\bagain\b", re.IGNORECASE)
# A NEGATED re-roll ("do not generate again", "don't regenerate", "stop generating") must NOT re-roll.
# The re-roll verb must IMMEDIATELY follow the negation (only auxiliaries between), so "do not design
# again" is a negation but "I don't LIKE this design, generate again" is NOT — there "design" is a noun
# after "like this", not the verb being negated (Codex 2026-06-07). Verb STEMs (no trailing boundary)
# so "generating"/"designing" are caught too.
_REROLL_NEGATION_RE = re.compile(
    r"\b(?:do\s*not|do\s*n['’]?t|don['’]?t|never|please\s+stop|stop|no\s+need\s+to)\b"
    r"\s+(?:to\s+|want\s+to\s+|wanna\s+|need\s+to\s+|please\s+|just\s+|ever\s+)*"
    r"(?:re-?generat|regenerat|generat|re-?creat|recreat|creat|re-?mak|remak|mak|"
    r"design|render|build|produc|redo|retry|try)",
    re.IGNORECASE,
)
# Bare tokens that carry NO change content: filler + re-roll verbs/objects. A pure re-roll contains
# ONLY these; any other token signals a specific change. (Not safety-critical: an unrecognized token
# falls to REVISION_NEEDED via the follow-up gate, never to a fresh render.)
_REROLL_OK_TOKENS = frozenset({
    "i", "we", "you", "u", "please", "pls", "plz", "kindly", "can", "could", "would", "will",
    "the", "this", "that", "it", "its", "flyer", "flier", "poster", "image", "picture", "pic",
    "one", "again", "now", "just", "so", "did", "do", "does", "done", "not", "dont", "didnt",
    "like", "liked", "love", "want", "wanted", "need", "a", "an", "of", "to", "for", "me", "my",
    "with", "same", "fresh", "new", "version", "details", "but", "and", "ok", "okay", "hi", "hey",
    "thanks", "thank", "too", "really", "very", "is", "quality", "bad", "poor", "terrible",
    "awful", "acceptable", "unacceptable", "accept", "cant", "cannot", "output", "look",
    "looks", "miserable",
    # re-roll verbs / phrase words as bare tokens
    "generate", "regenerate", "create", "recreate", "make", "remake", "design", "render", "build",
    "produce", "redo", "retry", "try", "over", "another", "once", "more", "start", "take", "go",
})


def _has_reroll_signal(text: str) -> bool:
    """A re-roll intent is present: an explicit re-roll phrase, or a generate-verb with "again". Used
    (with _looks_like_revision) to recognize a FOLLOW-UP about a prior flyer vs a fresh request."""
    t = text or ""
    return bool(_REROLL_SIGNAL_RE.search(t)) or bool(_QUALITY_REROLL_SIGNAL_RE.search(t)) or (
        bool(_REROLL_VERB_RE.search(t)) and bool(_AGAIN_RE.search(t))
    )


def _is_pure_reroll(text: str) -> bool:
    """True only for a no-change, non-negated re-roll ("I didn't like it, generate again"). Requires
    a re-roll signal, no negation of it, AND that EVERY word is a re-roll/filler token; any
    substantive word (a noun/adjective/number/new value), even between the verb and "again", makes it
    a specific change — routed to REVISION_NEEDED, not a re-roll."""
    t = (text or "").strip()
    if not _has_reroll_signal(t) or _REROLL_NEGATION_RE.search(t):
        return False
    # Strip apostrophes first so "don't"/"i'd" tokenize as one filler token ("dont"/"id"), not
    # "don"+"t" — otherwise a contraction would falsely read as a substantive (non-filler) word.
    normalized = t.casefold().replace("'", "").replace("’", "")
    return all(tok in _REROLL_OK_TOKENS for tok in re.findall(r"[a-z0-9]+", normalized))


def _api_key() -> str:
    v = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if v:
        return v
    for p in _ENV_PATHS:
        pp = Path(p)
        if not pp.exists():
            continue
        try:
            for line in pp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY=") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _load_flyer_cfg():
    """Load FlyerConfig (config.flyer) so facts.py can gate the creative planner. None on failure
    -> facts.py treats it as planner-off (safe)."""
    schemas = _schemas()
    try:
        try:
            from safe_io import load_yaml_model
            return load_yaml_model(CONFIG_PATH, schemas.Config).flyer
        except Exception:
            import yaml
            return schemas.Config.model_validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).flyer
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"bare_flyer: flyer cfg load failed: {type(e).__name__}: {e}\n")
        return None


# --- customer resolution ------------------------------------------------------
def _load_customer_store():
    schemas = _schemas()
    if not CUSTOMERS_PATH.exists():
        return None
    try:
        return schemas.FlyerCustomerStore.model_validate(json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"bare_flyer: customer store load failed: {type(e).__name__}: {e}\n")
        return None


def resolve_customer(chat_id: str, sender_phone: str | None = None):
    """Typed FlyerCustomerProfile for an unambiguous ACTIVE/TRIAL sender, else None."""
    store = _load_customer_store()
    if store is None:
        return None
    phone = sender_phone
    if not phone and chat_id.endswith("@s.whatsapp.net"):
        phone = chat_id.split("@", 1)[0]
    try:
        customer = store.find_customer_by_sender(phone, chat_id)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"bare_flyer: customer resolve failed: {type(e).__name__}: {e}\n")
        return None
    if customer is not None and getattr(customer, "status", "") not in {"trial", "active"}:
        return None
    return customer


def _routable_customer_phone(customer):
    for attr in ("business_whatsapp_number", "onboarded_by_phone"):
        val = getattr(customer, attr, None)
        if val:
            return val
    nums = getattr(customer, "authorized_request_numbers", None) or []
    if nums:
        return nums[0]
    return getattr(customer, "public_phone", None)


def _build_transient_project(customer, fields, locked_facts, raw_text: str, message_id, chat_id: str):
    schemas = _schemas()
    now = datetime.now(timezone.utc)
    return schemas.FlyerProject(
        project_id="F0000",
        status="generating_concepts",
        customer_id=(getattr(customer, "customer_id", "") or ""),
        customer_phone=_routable_customer_phone(customer),
        chat_id=chat_id or "",
        created_at=now,
        updated_at=now,
        original_message_id=(message_id or f"bare-{int(now.timestamp())}")[:200],
        raw_request=(raw_text or " ")[:2000] or " ",
        fields=fields,
        locked_facts=locked_facts,
    )


# --- grounding (facts.py) -----------------------------------------------------
def _build_locked_facts(customer, fields, raw_text: str, message_id: str, flyer_cfg):
    """Mirror create-flyer-project's canonical combine: registered identity (profile) + brief content
    + firewall-cleared creative-planner items (when the planner flag/category is enabled in cfg)."""
    F = _facts_mod()
    profile_facts = []
    if getattr(customer, "status", "") in {"trial", "active"}:
        profile_facts = F.profile_locked_facts(customer, raw_request=raw_text, message_id=message_id)
    return F.merge_locked_facts(
        profile_facts,
        F.extract_text_facts(
            fields, raw_text, message_id=message_id,
            profile_business_name=getattr(customer, "business_name", ""),
            allow_text_identity=not bool(profile_facts),
            cfg=flyer_cfg,
        ),
    )


# --- generation ---------------------------------------------------------------
def _build_facts_prompt(project, strict_note: str = "") -> str:
    """Integrated-poster prompt built from the project's LOCKED FACTS (grounded identity + content +
    firewall-cleared planner items). The model renders all text; run_visual_qa enforces it."""
    facts = list(getattr(project, "locked_facts", []) or [])
    ident_ids = {"business_name", "contact_phone", "location"}
    ident = [f for f in facts if f.fact_id in ident_ids]
    # facts.py emits item:N:name and item:N:price as SEPARATE facts — group by index N so each
    # card is "name - price" and the count is NAMES only (not name+price pairs counted as 2).
    items_by_idx: dict[str, dict] = {}
    for f in facts:
        m = re.match(r"^item:(\d+):(name|price)$", str(f.fact_id))
        if m:
            items_by_idx.setdefault(m.group(1), {})[m.group(2)] = f.value
    other = [f for f in facts if f.fact_id not in ident_ids and not re.match(r"^item:\d+:", str(f.fact_id))]
    parts = [
        "Design a single complete, finished promotional flyer/poster as ONE integrated image.",
        "Render ALL text directly inside the image, large and legible, spelled correctly.",
        "Use ONLY the facts listed below. Do NOT invent, alter, or omit the business name, address, "
        "phone, prices, or dates. Do NOT add any claim, offer, service, or event (delivery, catering, "
        'discounts, online ordering, reservations, "best in town", grand opening, etc.) that is not listed.',
    ]
    for f in ident:
        parts.append(f"{f.label} (render exactly): {f.value}")
    for f in other:
        parts.append(f"{f.label}: {f.value}")
    named = [(idx, d) for idx, d in sorted(items_by_idx.items(), key=lambda kv: int(kv[0])) if d.get("name")]
    if named:
        parts.append(f"Menu items to feature — exactly {len(named)} items, each rendered as ONE labeled "
                     "card showing the item name with its price (do NOT make a separate card for a price):")
        for _idx, d in named:
            parts.append(f"  - {d['name']}" + (f" - {d['price']}" if d.get("price") else ""))
    if strict_note:
        parts.append(strict_note)
    return "\n".join(parts)


def _generate_image(prompt: str, *, model: str, aspect_ratio: str = "4:5", image_size: str = "2K") -> bytes:
    import base64

    key = _api_key()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is missing")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
        "stream": False,
        "image_config": {"aspect_ratio": aspect_ratio, "image_size": image_size},
    }
    req = urllib.request.Request(
        OPENROUTER_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents",
            "X-Title": "Hermes Bare Flyer",
        },
        method="POST",
    )
    last: Exception | None = None
    body = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"OpenRouter image HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt == 2:
                raise RuntimeError(f"OpenRouter image request failed: {type(e).__name__}: {e}") from e
            time.sleep(2 * (attempt + 1))
    if not body and last is not None:
        raise RuntimeError(f"OpenRouter image request failed: {type(last).__name__}: {last}")
    doc = json.loads(body)
    images = (doc.get("choices") or [{}])[0].get("message", {}).get("images") or []
    if not images:
        raise RuntimeError(f"OpenRouter returned no image: {body[:300]}")
    url = images[0].get("image_url", {}).get("url") or ""
    if "," not in url:
        raise RuntimeError("OpenRouter image had no base64 data")
    return base64.b64decode(url.split(",", 1)[1])


def _generate_poster(project, *, strict_note: str = "", raw_bg_dest=None, scene_direction=None) -> bytes:
    """Generate with an integrated poster by default.

    Normal bare generation opts into the May 18 direct full-poster contract while keeping the
    current configured model. If raw_bg_dest is provided, revision-apply needs a textless raw
    background for no-credit re-overlay, so only that session path keeps background-only rendering
    and copies the raw sidecar before the temp dir closes.
    """
    import os as _os
    import tempfile
    from pathlib import Path as _Path

    had_integrated_env = "FLYER_ALLOW_INTEGRATED_POSTER" in _os.environ
    previous_integrated_env = _os.environ.get("FLYER_ALLOW_INTEGRATED_POSTER", "")
    if raw_bg_dest is None:
        _os.environ["FLYER_ALLOW_INTEGRATED_POSTER"] = "1"
    else:
        _os.environ.pop("FLYER_ALLOW_INTEGRATED_POSTER", None)
    rmod = _render_mod()
    try:
        with tempfile.TemporaryDirectory() as _td:
            specs = rmod.render_concept_previews(
                project, _td, model=GEN_MODEL, quality="medium", concept_count=1,
                repair_instruction=strict_note, scene_direction=scene_direction,
            )
            final = _Path(specs[0].path)
            png = final.read_bytes()
            if raw_bg_dest is not None:
                raw = _Path(rmod._raw_background_path(final))
                if not raw.exists():
                    raise RuntimeError(f"textless raw background missing at {raw.name}")
                _Path(raw_bg_dest).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(raw, raw_bg_dest)
            return png
    finally:
        if had_integrated_env:
            _os.environ["FLYER_ALLOW_INTEGRATED_POSTER"] = previous_integrated_env
        else:
            _os.environ.pop("FLYER_ALLOW_INTEGRATED_POSTER", None)


# --- PR3: Creative-Director render (textless background + deterministic overlay) ---
def _render_creative_director(project, background_brief: str) -> bytes:
    """Render the CD path: generate a TEXTLESS background from ``background_brief``,
    then composite the deterministic critical-text overlay on top. The visible facts
    come ONLY from the overlay (project.locked_facts — including the customer_text
    spans build_flyer_brief materialized), NEVER from the model — the core invariant.

    Mirrors render.render_source_edit_preview's "raw bytes → write as background →
    overlay → fail-closed" shape, reusing the deployed ``_apply_critical_text_overlay``
    WRAPPER — Pillow when present in the service venv, else the ``/usr/bin/python3``
    system-Pillow fallback (the SAME wrapper existing overlay paths use; the public
    ``apply_critical_text_overlay`` is Pillow-only, Codex PR3 P2). Any overlay failure
    propagates so render_grounded fails safe (manual route), never ships an incomplete
    flyer."""
    import tempfile
    from pathlib import Path as _Path

    raw = _generate_image(background_brief, model=GEN_MODEL)
    rmod = _render_mod()
    with tempfile.TemporaryDirectory() as _td:
        bg = _Path(_td) / "cd_background.png"
        out = _Path(_td) / "cd_final.png"
        bg.write_bytes(raw)
        rmod._apply_critical_text_overlay(project, str(bg), str(out),
                                          size=(1080, 1350), output_format="concept_preview")
        return out.read_bytes()


# --- QA gate (visual_qa.run_visual_qa) ----------------------------------------
def _qa_allows_send(report) -> bool:
    """Send only when the existing visual QA passes (or warn-tier). Block/provider-unavailable -> hold."""
    status = getattr(report, "status", "")
    severity = getattr(report, "severity", "block")
    if status == "passed":
        return True
    if status == "failed" and severity == "warn":
        return True
    return False


def _skip_visual_qa_enabled() -> bool:
    return (os.environ.get("FLYER_BARE_SKIP_VISUAL_QA") or "").strip().lower() in {"1", "true", "yes", "on"}


# ── Visible-contract referee (2026-06-07) ──────────────────────────────────────
# Deterministic post-render gate: read the rendered flyer's VISIBLE text back
# (Hermes vision OCR) and prove it obeys the concrete locked facts. Runs
# INDEPENDENTLY of FLYER_BARE_SKIP_VISUAL_QA (that flag disables the broad subjective
# QA, which stays off) and is scoped by flag + allowlist. Blocks ONLY on positive
# concrete violations; an unreadable read-back sends-anyway + logs 'unverified'
# (operator 2026-06-07) — a strict improvement, never a new false-hold source.
VISIBLE_CONTRACT_ENABLED_ENV = "FLYER_VISIBLE_CONTRACT"
VISIBLE_CONTRACT_ALLOWLIST_ENV = "FLYER_VISIBLE_CONTRACT_ALLOWLIST"


def _visible_contract_mod():
    try:
        import flyer_visible_contract as VC
    except ImportError:
        from agents.flyer import visible_contract as VC
    return VC


def _visible_contract_armed(project) -> bool:
    if os.environ.get(VISIBLE_CONTRACT_ENABLED_ENV) != "1":
        return False
    raw = os.environ.get(VISIBLE_CONTRACT_ALLOWLIST_ENV, "") or ""
    allow = {n for n in (_normalize_sender(p) for p in raw.split(",")) if n}
    return _normalize_sender(getattr(project, "customer_phone", "") or "") in allow


def _emit_visible_contract_audit(*, project, status: str, reason: str = "", blockers=None) -> None:
    """Best-effort metrics row (FlyerVisibleContractChecked) via the canonical
    decisions.log chokepoint. LOG-ONLY; an audit failure never blocks the render."""
    try:
        schemas = _schemas()
        try:
            from safe_io import flock as _flock, ndjson_append as _ndjson_append  # type: ignore
        except Exception:  # noqa: BLE001
            _flock = None  # type: ignore
            _ndjson_append = None  # type: ignore
        entry = schemas.FlyerVisibleContractChecked(
            type="flyer_visible_contract_checked",
            ts=datetime.now(timezone.utc),
            visible_contract_status=status,
            visible_contract_reason=(reason or "")[:200],
            blockers=[str(b)[:200] for b in itertools.islice(blockers or [], 20)],
            module_version=MODULE_VERSION,
            module_file=str(__file__),
            resolved_sender=(getattr(project, "customer_phone", "") or "")[:200],
            project_id=(getattr(project, "project_id", "") or "")[:80],
        )
        line = entry.model_dump_json()
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _flock is not None and _ndjson_append is not None:
            with _flock(AUDIT_LOG_PATH):
                _ndjson_append(AUDIT_LOG_PATH, line)
        else:
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        return


def _run_visible_contract_gate(image_bytes: bytes, project):
    """Read the rendered flyer's visible text (Hermes vision) and run the concrete
    referee. Returns (ok, blockers). Unreadable read-back ⇒ send-anyway (ok=True) +
    log 'unverified' (does NOT hold this scoped phase). Concrete violation ⇒ ok=False
    + blockers ⇒ caller returns FAILCLOSED."""
    import tempfile

    VQ = _visual_qa_mod()
    VC = _visible_contract_mod()
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    extracted, reason = "", ""
    try:
        Path(tmp).write_bytes(image_bytes)
        try:
            extracted, _provider, _qa_source, notes = VQ._vision_text(Path(tmp))
        except Exception as e:  # noqa: BLE001
            extracted, reason = "", f"vision_error:{type(e).__name__}"
        if not extracted:
            reason = reason or ("; ".join(str(n) for n in (notes or []))[:160] or "empty_readback")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if not extracted:
        _emit_visible_contract_audit(project=project, status="unverified", reason=reason)
        return (True, [f"visible_contract_unverified:{reason}"[:200]])
    try:
        blockers = VC.validate_visible_contract(project, extracted)
    except Exception as e:  # noqa: BLE001 — the referee must never crash the render path
        _emit_visible_contract_audit(project=project, status="unverified", reason=f"validator_error:{type(e).__name__}")
        return (True, [f"visible_contract_unverified:validator_error:{type(e).__name__}"])
    if blockers:
        _emit_visible_contract_audit(project=project, status="blocked", blockers=blockers)
        return (False, blockers)
    _emit_visible_contract_audit(project=project, status="pass")
    return (True, [])


def run_visual_qa(image_bytes: bytes, project):
    """Run the deployed visual QA over generated bytes (writes a temp artifact it needs as a path).
    Returns (ok, blockers). Conservative: any error -> not ok."""
    import tempfile

    # Concrete visible-contract referee (scoped by flag+allowlist) — runs FIRST and
    # INDEPENDENTLY of the broad-QA skip below. Blocks only on positive violations;
    # an unverifiable read-back returns ok=True (send-anyway) and falls through. ANY
    # referee-infrastructure failure (e.g. the module not deployed) is swallowed to
    # send-anyway+unverified — the referee must NEVER crash or hold the render.
    if _visible_contract_armed(project):
        try:
            vc_ok, vc_blockers = _run_visible_contract_gate(image_bytes, project)
        except Exception as e:  # noqa: BLE001
            _emit_visible_contract_audit(project=project, status="unverified", reason=f"gate_error:{type(e).__name__}")
            vc_ok, vc_blockers = True, []
        if not vc_ok:
            return (False, vc_blockers)

    if _skip_visual_qa_enabled():
        return (True, ["visual_qa_disabled"])

    VQ = _visual_qa_mod()
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        Path(tmp).write_bytes(image_bytes)
        report = VQ.run_visual_qa(project, tmp, output_format="whatsapp_image", asset_id="C1")
    except Exception as e:  # noqa: BLE001
        return (False, [f"qa_error:{type(e).__name__}"])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return (_qa_allows_send(report), list(getattr(report, "blockers", []) or []) or [getattr(report, "status", "qa_failed")])


def _render_error_detail(e: Exception, project, stage: str) -> str:
    """LOG-ONLY diagnostic for a bare-path render failure: names the exact cause + the
    render stage + a fact id/length summary, so a fail-closed `render_error:FlyerRenderError`
    is not opaque in send.log. The descriptive FlyerRenderError message (e.g. "OpenRouter
    image HTTP 402: requires more credits, or fewer max_tokens", "critical text facts do not
    fit", "missing required flyer fields: ...") already lives in the exception — the bare
    loop just dropped it. Returned ONLY in the final fail-closed blockers (logged); never fed
    to the retry strict-note, so render/send behavior is unchanged. The ENTIRE body is
    guarded — diagnostics must NEVER raise into the render path (the caller's except runs
    unguarded), even for a pathological exception ``__str__`` (Codex 2026-06-06)."""
    try:
        msg = " ".join(str(e).split())[:200]
        parts = [f"render_detail[stage={stage}]: {type(e).__name__}: {msg}" if msg
                 else f"render_detail[stage={stage}]: {type(e).__name__}"]
        facts = list(getattr(project, "locked_facts", []) or [])
        lens = ",".join(
            f"{getattr(f, 'fact_id', '?')}={len(str(getattr(f, 'value', '') or ''))}" for f in facts
        )
        over = [getattr(f, "fact_id", "?") for f in facts
                if len(str(getattr(f, "value", "") or "")) > 180]
        parts.append(f"facts[{lens}]" + (f" over180={over}" if over else ""))
        return " | ".join(parts)
    except Exception:  # noqa: BLE001 — diagnostics must NEVER raise into the render path
        try:
            return f"render_detail[stage={stage}]: {type(e).__name__}"
        except Exception:  # noqa: BLE001 — even type/repr is pathological; degrade gracefully
            return "render_detail: unavailable"


_DISABLE_BRAND_ASSETS_FACT_ID = "render:disable_brand_assets"


def _wrong_brand_blocker_seen(blockers: list[str]) -> bool:
    for blocker in blockers or []:
        normalized = str(blocker or "").strip().casefold()
        if normalized.startswith("visible wrong business/brand:") or normalized.startswith("visible wrong business:"):
            return True
    return False


def _with_saved_brand_assets_disabled(project):
    if any(getattr(fact, "fact_id", "") == _DISABLE_BRAND_ASSETS_FACT_ID for fact in project.locked_facts):
        return project
    schemas = _schemas()
    disabled_fact = schemas.FlyerLockedFact(
        fact_id=_DISABLE_BRAND_ASSETS_FACT_ID,
        label="Render Control",
        value="true",
        source="system",
    )
    return project.model_copy(update={"locked_facts": [*project.locked_facts, disabled_fact]})


# --- orchestration ------------------------------------------------------------
def render_grounded(chat_id: str, raw_text: str, *, message_id: str | None = None,
                    sender_phone: str | None = None):
    """Registered-path grounded render. (SEND, png) | (CONFLICT, {...}) | (FAILCLOSED, [blockers]) |
    (REVISION_NEEDED, None) | (UNREGISTERED, None)."""
    # A FOLLOW-UP about a prior flyer = a re-roll signal ("generate again", "regenerate", "redo") OR
    # a revision reference ("this flyer", "change the ..."). Handle it BEFORE the new-flyer path so a
    # follow-up can never be mis-rendered as a brand-new flyer (Codex 2026-06-07):
    #   - a CLEAN pure re-roll (no change, not negated, saved same-chat session) re-renders the saved
    #     validated facts as a fresh variant (no re-extraction, no revision-merge);
    #   - ANYTHING else (a specific change, a negation, ambiguous, or no/stale session) -> the
    #     resend-full-details guidance. Never a fresh render, never a silently-dropped change.
    if _has_reroll_signal(raw_text) or _looks_like_revision(raw_text) or _REROLL_NEGATION_RE.search(raw_text):
        if _is_pure_reroll(raw_text):
            status, payload = render_reroll(chat_id)
            if status != REVISION_NEEDED:  # REROLL (sent) or FAILCLOSED (render failed) -> return as-is
                return (status, payload)
        return (REVISION_NEEDED, None)
    customer = resolve_customer(chat_id, sender_phone)
    if customer is None:
        return (UNREGISTERED, None)

    IF = _intake_fields()
    fields = IF._extract_fields(raw_text, now=datetime.now(timezone.utc))
    verdict, _canonical = reconcile_identity(fields.event_or_business_name, customer)
    if verdict == "conflict":
        return (CONFLICT, {"stated": (fields.event_or_business_name or "").strip(),
                           "registered": getattr(customer, "business_name", "")})
    hydrate_fields = getattr(IF, "_hydrate_fields_from_customer", None)
    if hydrate_fields is not None:
        fields = hydrate_fields(fields, customer=customer)

    flyer_cfg = _load_flyer_cfg()
    locked_facts = _build_locked_facts(customer, fields, raw_text, message_id or "", flyer_cfg)

    # PR3: Creative-Director branch — armed ONLY when the flag is "1" AND this trusted
    # resolved sender is allowlisted. The audit row is emitted on EVERY new-flyer render
    # (armed or not) so the caller is provable from logs BEFORE the operator enables it.
    resolved_sender = _resolved_sender(chat_id, sender_phone)
    allowlisted = _normalize_sender(resolved_sender) in _creative_director_allowlist()
    if _creative_director_armed(resolved_sender):
        return _render_creative_director_grounded(
            chat_id, raw_text, customer, fields, locked_facts,
            message_id=message_id, resolved_sender=resolved_sender,
        )
    # Flag off OR sender not allowlisted ⇒ byte-identical legacy path below. Audit it.
    _emit_creative_director_audit(
        chat_id=chat_id, resolved_sender=resolved_sender, reached=False,
        status=("not_allowlisted" if os.environ.get(CREATIVE_DIRECTOR_ENABLED_ENV) == "1" else "disabled"),
        allowlisted=allowlisted,
    )

    project = _build_transient_project(customer, fields, locked_facts, raw_text, message_id, chat_id)

    # Session persistence is enabled by default for customer follow-up edits. Raw-background capture is
    # separately opt-in so the first flyer can keep the direct integrated-poster path.
    raw_bg_dest = _unique_bg_path(chat_id) if (REVISION_APPLY_ENABLED and REVISION_CAPTURE_RAW_BG) else None
    # Advisory skill-driven occasion/theme scene (FLYER_SKILL_DRIVEN_SCENE + allowlist). None ⇒ today's
    # Python scene (fallback); never fail-closed. Computed once; reused for both render attempts.
    scene_direction = _advisory_scene_direction(raw_text, locked_facts, customer, resolved_sender)
    last_blockers: list[str] = []
    last_render_detail = ""
    render_project = project
    for attempt in range(2):
        last_render_detail = ""  # reset per attempt so a stale detail never rides a later QA-fail
        strict = "" if attempt == 0 else (
            "CRITICAL: the previous render had these problems: " + "; ".join(last_blockers)
            + ". Fix them — render every listed fact exactly, include every listed item, and add "
              "nothing that is not listed."
        )
        try:
            png = _generate_poster(render_project, strict_note=strict, raw_bg_dest=raw_bg_dest, scene_direction=scene_direction)
        except Exception as e:  # noqa: BLE001
            # last_blockers (the retry strict-note input) keeps the SAME generic shape — no
            # behavior change. The descriptive cause is captured LOG-ONLY in last_render_detail
            # and surfaced only in the FINAL fail-closed blockers (→ send.log), so a bare-path
            # FlyerRenderError is diagnosable instead of opaque (operator obs request 2026-06-06).
            last_blockers = [f"render_error:{type(e).__name__}"]
            last_render_detail = _render_error_detail(e, render_project, "generate_poster")
            continue
        ok, blockers = run_visual_qa(png, render_project)
        if ok:
            if REVISION_APPLY_ENABLED:
                # Write a PENDING session (project + optional raw bg). The orchestrator commits it
                # only AFTER delivery succeeds, so an undelivered render never advances revision state.
                _write_session(chat_id, render_project, raw_bg_dest or "", raw_text, model=GEN_MODEL, pending=True)
            return (SEND, png)
        last_blockers = blockers
        if _wrong_brand_blocker_seen(last_blockers):
            render_project = _with_saved_brand_assets_disabled(project)
    return (FAILCLOSED, last_blockers + ([last_render_detail] if last_render_detail else []))


def _render_creative_director_grounded(chat_id, raw_text, customer, fields, locked_facts, *,
                                       message_id=None, resolved_sender=""):
    """PR3 Creative-Director branch (reached ONLY when the flag + allowlist gate armed).

    Calls build_flyer_brief, then handles its typed status STRICTLY — only "ok" renders
    (via the textless-background + deterministic-overlay CD path), and EVERY other armed
    status fails safe to the existing fail-closed return; it NEVER falls back to the legacy
    integrated poster. Emits the FlyerCreativeDirectorRouted audit (reached=True) with the
    BriefResult status on every outcome."""
    def _audit(status: str, *, error_summary: str = "", errors: list[str] | None = None,
               unavailable_reason: str = "", render_error: str = "") -> None:
        _emit_creative_director_audit(
            chat_id=chat_id, resolved_sender=resolved_sender, reached=True,
            status=status, allowlisted=True, error_summary=error_summary, errors=errors,
            unavailable_reason=unavailable_reason, render_error=render_error,
        )

    # build_flyer_brief materializes validated customer_text spans INTO locked_facts in
    # place (list mutation), so the project built AFTER it carries those spans for the
    # overlay. The brief itself is structure only — the visible facts are the overlay's.
    # _context_builder() is resolved INSIDE the try so an import/resolution failure on the
    # live (flat) deploy fails safe — audit "unavailable" + FAILCLOSED — and NEVER raises
    # uncaught out of render_grounded. (Codex PR3 BLOCKER: an armed render must ALWAYS
    # emit the routing audit + fail closed, never propagate.)
    try:
        CB = _context_builder()
        result = CB.build_flyer_brief(raw_text, locked_facts, customer)
    except Exception as e:  # noqa: BLE001 — armed-but-unresolvable/throwing brain is unavailable, fail safe
        _audit("unavailable", unavailable_reason=f"brief_exception:{type(e).__name__}",
               error_summary=f"brief_exception:{type(e).__name__}")
        return (FAILCLOSED, [f"creative_director_error:{type(e).__name__}"])

    status = getattr(result, "status", "unavailable")
    if status != "ok" or result.brief is None:
        # "invalid" (firewall rejected) or "unavailable" (brain unreachable): fail safe to
        # the existing fail-closed path → manual / honest reply. NEVER the legacy poster.
        # ("disabled" cannot occur here — the gate already proved the flag is "1".)
        norm_status = status if status in {"invalid", "unavailable"} else "unavailable"
        result_errors = list(getattr(result, "errors", []) or [])
        if norm_status == "invalid":
            _audit("invalid", errors=result_errors,
                   error_summary="invalid: " + (result_errors[0] if result_errors else "validator_rejected"))
        else:
            ureason = getattr(result, "reason", "") or "gateway_unreachable"
            _audit("unavailable", unavailable_reason=ureason, error_summary=f"unavailable:{ureason}")
        return (FAILCLOSED, [f"creative_director_{status}"] + result_errors)

    # status == "ok": build the project from the (now span-augmented) locked facts and
    # render via the CD path. The deterministic overlay places the required visible facts
    # — required_fact_ids(locked_facts) ∩ locked_facts — from project.locked_facts (see
    # render.collect_text_facts / _menu_overlay_payload) and fails closed if any required
    # fact can't fit. Coverage of required_fact_ids was ALREADY enforced inside
    # build_flyer_brief's validator (status would be "invalid" otherwise). Facts come from
    # the overlay, never the model — the invariant.
    project = _build_transient_project(customer, fields, locked_facts, raw_text, message_id, chat_id)
    try:
        png = _render_creative_director(project, result.brief.background_brief)
    except Exception as e:  # noqa: BLE001 — overlay/background failure ⇒ fail safe, never legacy
        # The BRIEF validated (status stays "ok") but the textless-bg / overlay render
        # threw → the flyer did NOT ship. ``render_error`` + ``error_summary`` make this
        # outcome distinguishable in the audit from a clean ship (which leaves them "").
        _audit("ok", render_error=type(e).__name__,
               error_summary=f"render_error:{type(e).__name__}", errors=[str(e)[:200]])
        return (FAILCLOSED, [f"creative_director_render_error:{type(e).__name__}"])

    ok, blockers = run_visual_qa(png, project)
    if ok:
        _audit("ok")  # clean ship → error_summary stays "" (the grep-able "did it ship?" signal)
        if REVISION_APPLY_ENABLED:
            _write_session(chat_id, project, "", raw_text, model=GEN_MODEL, pending=True)
        return (SEND, png)
    _audit("ok", error_summary="qa_failed", errors=list(blockers or []))
    return (FAILCLOSED, blockers)


# --- slice 2c: uniform-price-header revision-apply ----------------------------
def _session_uniform_price(project) -> "str | None":
    """The single price shared by EVERY named item, or None. Requires that every item:N:name has a
    matching item:N:price and all are equal — a partially-priced menu must NOT invent a uniform
    header (Codex F2)."""
    names: set[str] = set()
    prices: dict[str, str] = {}
    for f in project.locked_facts:
        m = re.match(r"^item:(\d+):(name|price)$", str(f.fact_id))
        if not m:
            continue
        if m.group(2) == "name":
            names.add(m.group(1))
        elif str(f.value or "").strip():
            prices[m.group(1)] = re.sub(r"\s+", "", str(f.value))
    if not names or set(prices) != names:
        return None
    vals = set(prices.values())
    return next(iter(vals)) if len(vals) == 1 else None


def _apply_uniform_price_header(project, price: str):
    """Transform the project for the uniform-price-header layout: demote every per-item price fact to
    non-required (so visual_qa skips the per-item pair check), set the render:price_layout system
    fact, and add ONE required pricing_structure header fact ("Every item $X")."""
    import schemas
    kept = []
    for f in project.locked_facts:
        if re.match(r"^item:\d+:price$", str(f.fact_id)):
            kept.append(f.model_copy(update={"required": False}))   # header owns the price now
        elif f.fact_id in ("render:price_layout", "pricing_structure"):
            continue                                                # replaced below
        else:
            kept.append(f)
    kept.append(schemas.FlyerLockedFact(fact_id="render:price_layout", label="Price layout",
                                        value="uniform_header", source="system", required=False))
    kept.append(schemas.FlyerLockedFact(fact_id="pricing_structure", label="Pricing",
                                        value=f"Every item {price}", source="system", required=True))
    return project.model_copy(update={"locked_facts": kept, "updated_at": datetime.now(timezone.utc)})


def _project_item_names(project) -> dict[int, str]:
    names: dict[int, str] = {}
    for fact in getattr(project, "locked_facts", []) or []:
        match = re.match(r"^item:(\d+):name$", str(getattr(fact, "fact_id", "")))
        if not match:
            continue
        value = str(getattr(fact, "value", "") or "").strip()
        if value:
            names[int(match.group(1))] = value
    return names


def _item_name_price_pattern(name: str):
    tokens = re.findall(r"[A-Za-z0-9]+", name or "")
    if not tokens:
        return None
    sep = r"[\s'&/.-]+"
    return re.compile(
        r"\b" + sep.join(re.escape(token) for token in tokens) + r"\b"
        r"\s*(?:-|:)?\s*\$\s*(?P<price>\d+(?:\.\d{1,2})?)",
        flags=re.IGNORECASE,
    )


def _item_price_updates_from_text(project, raw_text: str) -> dict[int, str]:
    updates: dict[int, str] = {}
    body = raw_text or ""
    for index, name in _project_item_names(project).items():
        pattern = _item_name_price_pattern(name)
        if pattern is None:
            continue
        matches = list(pattern.finditer(body))
        if matches:
            updates[index] = f"${matches[-1].group('price')}"
    return updates


def _per_item_uniform_price_from_text(raw_text: str) -> str | None:
    price = _price_from_text(raw_text)
    if not price:
        return None
    body = " ".join((raw_text or "").lower().split())
    if re.search(r"\b(header|top|common|uniform|single)\b", body):
        return None
    placeholder_ref = re.search(r"\b(?:pending|tbd|placeholder|\[\s*price\s*\]|price\s+missing)\b", body)
    per_item_ref = re.search(r"\b(?:each|every|all)\s+(?:item|items)\b|\bitem\s+price\b|\bper[-\s]?item\b", body)
    update_ref = re.search(r"\b(?:replace|update|edit|modify|change|set|fix|correct)\b", body)
    if placeholder_ref and per_item_ref and update_ref:
        return price
    if per_item_ref and update_ref and re.search(r"\b(?:priced\s+at|price\s+(?:is|as|to)|for|at)\b", body):
        return price
    return None


def _apply_per_item_prices(project, updates: dict[int, str]):
    """Set item:N:price facts for existing item cards. This is the per-card counterpart to
    _apply_uniform_price_header and is used for "replace PENDING with $X" plus explicit
    item-price-pair corrections."""
    if not updates:
        return None
    import schemas
    existing_names = _project_item_names(project)
    updates = {idx: price for idx, price in updates.items() if idx in existing_names and price}
    if not updates:
        return None
    kept = []
    for fact in project.locked_facts:
        fid = str(fact.fact_id)
        price_match = re.match(r"^item:(\d+):price$", fid)
        if price_match and int(price_match.group(1)) in updates:
            continue
        if fid == "render:price_layout":
            continue
        if fid == "pricing_structure":
            value = str(getattr(fact, "value", "") or "").strip().lower()
            source = str(getattr(fact, "source", "") or "")
            if source == "system" or value.startswith("every item"):
                continue
        kept.append(fact)
    for idx in sorted(updates):
        kept.append(schemas.FlyerLockedFact(
            fact_id=f"item:{idx}:price",
            label="Price",
            value=updates[idx],
            source="customer_text",
            required=True,
        ))
    return project.model_copy(update={"locked_facts": kept, "updated_at": datetime.now(timezone.utc)})


def _reoverlay(project, raw_bg_path) -> bytes:
    """Re-run the deterministic critical-text overlay on the STORED textless background (no new image
    generation -> no credits). Raises (caught by render_revision_apply) if the overlay cannot fit."""
    import tempfile
    from pathlib import Path as _Path

    rmod = _render_mod()
    with tempfile.TemporaryDirectory() as _td:
        out = _Path(_td) / "revised.png"
        rmod._apply_critical_text_overlay(project, str(raw_bg_path), str(out),
                                          size=(1080, 1350), output_format="concept_preview")
        return out.read_bytes()


def _session_is_stale(sess) -> bool:
    """True when the saved session is older than the re-roll TTL (re-roll then fails closed)."""
    try:
        sent_at = sess.get("sent_at")
        if not sent_at:
            return False  # legacy session without a timestamp -> treat as fresh
        ts = datetime.fromisoformat(sent_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return age_hours > _REROLL_MAX_AGE_HOURS
    except Exception:  # noqa: BLE001
        return False  # unparseable timestamp -> don't block a re-roll on it


def render_reroll(chat_id: str):
    """No-change re-roll ("generate again"): re-render the saved, already-validated project as a FRESH
    variant — preserving every locked fact exactly, WITHOUT re-extracting the customer's text or
    invoking revision-merge. Fails closed (never a wrong flyer):
      (REROLL, png)            -> fresh variant rendered + QA-passed
      (REVISION_NEEDED, None)  -> flag off / no session / stale session / project won't rebuild
      (FAILCLOSED, [blockers]) -> the re-render itself failed (names the stage, like render_grounded)
    """
    import schemas

    if not REVISION_APPLY_ENABLED:
        return (REVISION_NEEDED, None)
    sess = _load_session(chat_id)
    if not sess or _session_is_stale(sess):
        return (REVISION_NEEDED, None)
    try:
        project = schemas.FlyerProject.model_validate(sess["project"])
    except Exception:  # noqa: BLE001
        return (REVISION_NEEDED, None)

    last_blockers: list[str] = []
    last_render_detail = ""
    for attempt in range(2):
        last_render_detail = ""  # reset per attempt so a stale detail never rides a later QA-fail
        # The retry note carries ONLY prior QA blockers — NEVER the customer's "generate again" text —
        # so a re-roll can never inject new copy into the locked facts.
        strict = "" if attempt == 0 else (
            "CRITICAL: the previous render had these problems: " + "; ".join(last_blockers)
            + ". Fix them — render every listed fact exactly, include every listed item, and add "
              "nothing that is not listed."
        )
        try:
            png = _generate_poster(project, strict_note=strict, raw_bg_dest=None)
        except Exception as e:  # noqa: BLE001
            last_blockers = [f"reroll_render_error:{type(e).__name__}"]
            last_render_detail = _render_error_detail(e, project, "reroll_generate_poster")
            continue
        ok, blockers = run_visual_qa(png, project)
        if ok:
            # Re-persist the SAME project as pending; the orchestrator commits it after delivery so an
            # undelivered re-roll never advances the saved session.
            _write_session(chat_id, project, "", sess.get("brief", ""),
                           model=sess.get("model") or GEN_MODEL, pending=True)
            return (REROLL, png)
        last_blockers = blockers
    return (FAILCLOSED, last_blockers + ([last_render_detail] if last_render_detail else []))


# --- Slice 3: skill-driven iteration (re-roll / specific_revision / style_reuse) ------------------

ITERATION_UNCLEAR_REPLY = (
    "I can update your last flyer or reuse its style for a new one. Tell me what to change "
    "(e.g. “make it more festive”, “use blue and gold”, “add balloons”), say "
    "“generate again” for a fresh take, or “use this design for a <new> flyer”."
)

# style_reuse intent = reuse a prior flyer's design/look for a NEW flyer. This is an INTENT classifier
# (routing — Python's job), NOT an occasion/holiday keyword list.
_STYLE_REUSE_PHRASE_RE = re.compile(
    r"\b(?:use|reuse|keep|same|with\s+(?:the\s+)?same)\b[^.!?]{0,25}"
    r"\b(?:design|theme|style|look|layout|template|format)\b",
    re.IGNORECASE,
)
# Require a NEW flyer ("create A/AN/ANOTHER/NEW <subject> flyer"), so "make THE/THIS flyer blue" stays
# a specific_revision of the saved flyer — not a new sparse one (Codex 2026-06-07).
_CREATE_NEW_FLYER_RE = re.compile(
    r"\b(?:create|make|design|generate|build|need|want|prepare|do)\b[^.!?]{0,40}"
    r"\b(?:a|an|another|new)\b[^.!?]{0,40}\bflyer\b",
    re.IGNORECASE,
)


def _is_style_reuse(text: str) -> bool:
    """True for 'use this design/theme/look … create A NEW <subject> flyer' — reuse a prior look for a
    NEW flyer. A bare 'make THIS/THE flyer <change>' is a revision, not a style-reuse."""
    t = text or ""
    return bool(_STYLE_REUSE_PHRASE_RE.search(t)) and bool(_CREATE_NEW_FLYER_RE.search(t))


def _advise_scene(request_text: str, locked_facts, customer):
    """Call the flyer_generation skill (ungated) for a VisualDirection from a composed request. The
    iteration handler is gated by FLYER_BARE_ITERATION; the skill is advisory (returns None on any
    problem). Never raises."""
    try:
        cb = _context_builder()
        advise = getattr(cb, "advise_scene_direction", None)
        if advise is None:
            return None
        return advise(request_text, locked_facts, customer)
    except Exception:  # noqa: BLE001 — advisory only
        return None


def _render_with_scene(chat_id, project, sess, scene_direction, *, status, err_prefix, brief=None):
    """Render ``project`` with an advisory ``scene_direction`` (mirrors render_reroll's 2-attempt QA
    loop). (status, png) on QA-pass; (FAILCLOSED, [blockers]) otherwise. ``brief`` overrides the
    session brief written for the new pending session (style_reuse persists the NEW request, not the
    old flyer's brief — Codex 2026-06-07)."""
    persist_brief = brief if brief is not None else (sess or {}).get("brief", "")
    last_blockers: list[str] = []
    last_detail = ""
    for attempt in range(2):
        last_detail = ""
        strict = "" if attempt == 0 else (
            "CRITICAL: the previous render had these problems: " + "; ".join(last_blockers)
            + ". Fix them — render every listed fact exactly, include every listed item, and add "
              "nothing that is not listed."
        )
        try:
            png = _generate_poster(project, strict_note=strict, raw_bg_dest=None, scene_direction=scene_direction)
        except Exception as e:  # noqa: BLE001
            last_blockers = [f"{err_prefix}:{type(e).__name__}"]
            last_detail = _render_error_detail(e, project, err_prefix)
            continue
        ok, blockers = run_visual_qa(png, project)
        if ok:
            _write_session(chat_id, project, "", persist_brief,
                           model=(sess or {}).get("model") or GEN_MODEL, pending=True)
            return (status, png)
        last_blockers = blockers
    return (FAILCLOSED, last_blockers + ([last_detail] if last_detail else []))


def render_iteration(chat_id: str, raw_text: str, *, message_id: str | None = None, sender_phone: str | None = None):
    """Bare iteration handler for a follow-up on a delivered flyer (the text --revision branch).
    Classifies intent (Python routing; the skill is the creative advisor) and routes — instead of the
    "resend full details" dead-end. Returns:
      (REROLL, png)                -> pure re-roll (no change), fresh variant
      (ITERATION_REVISED, png)     -> specific visual revision applied to the SAVED project (facts kept)
      (ITERATION_STYLE_REUSE, png) -> NEW flyer (new facts) reusing the prior style
      (ITERATION_UNCLEAR, None)    -> no/stale session or unclear -> ONE concise question
      (REVISION_NEEDED, None)      -> flag off (caller keeps today's resend reply)
      (FAILCLOSED, [blockers])     -> a render failed
    Facts are NEVER re-extracted from a revision; a style_reuse extracts FRESH facts and never copies
    the old flyer's facts. A specific_revision needs the skill to produce updated art direction; if it
    can't, we ask a concise question rather than render a no-op or a wrong flyer.
    """
    import schemas
    # Pure re-roll first — deterministic, no skill, works even when phrased with "design".
    if _is_pure_reroll(raw_text):
        return render_reroll(chat_id)
    if not _iteration_armed(_resolved_sender(chat_id, sender_phone)):
        return (REVISION_NEEDED, None)   # flag off / not allowlisted -> caller keeps today's resend reply
    sess = _load_session(chat_id)
    if not sess or _session_is_stale(sess):
        return (ITERATION_UNCLEAR, None)
    customer = resolve_customer(chat_id, sender_phone)

    if _is_style_reuse(raw_text):
        # NEW flyer reusing the prior look. Extract FRESH facts from THIS request; never copy old facts.
        if customer is None:
            return (ITERATION_UNCLEAR, None)
        IF = _intake_fields()
        fields = IF._extract_fields(raw_text, now=datetime.now(timezone.utc))
        hydrate_fields = getattr(IF, "_hydrate_fields_from_customer", None)
        if hydrate_fields is not None:
            fields = hydrate_fields(fields, customer=customer)
        flyer_cfg = _load_flyer_cfg()
        new_facts = _build_locked_facts(customer, fields, raw_text, message_id or "", flyer_cfg)
        new_project = _build_transient_project(customer, fields, new_facts, raw_text, message_id, chat_id)
        composed = (raw_text + " . Reuse the visual STYLE (palette, design approach, polish) of the "
                    "customer's previous flyer where it fits, but with subjects and theme suited to THIS "
                    "new request.").strip()
        vd = _advise_scene(composed, list(new_facts), customer)
        # Renders the new flyer regardless of vd (vd None -> today's Python scene for the new request).
        return _render_with_scene(chat_id, new_project, sess, vd, status=ITERATION_STYLE_REUSE,
                                  err_prefix="style_reuse_render_error", brief=raw_text)

    # specific_revision: re-render the SAVED project (facts preserved) with a skill-updated scene.
    try:
        project = schemas.FlyerProject.model_validate(sess["project"])
    except Exception:  # noqa: BLE001
        return (ITERATION_UNCLEAR, None)
    prior_brief = str((sess or {}).get("brief") or "")
    composed = (prior_brief + " . Customer revision — apply to the VISUAL look ONLY (theme, scene, "
                "colors, motifs, composition); keep ALL text and facts exactly the same: "
                + (raw_text or "")).strip()
    vd = _advise_scene(composed, list(project.locked_facts), customer)
    if vd is None:
        return (ITERATION_UNCLEAR, None)
    return _render_with_scene(chat_id, project, sess, vd, status=ITERATION_REVISED,
                              err_prefix="revision_render_error")


def render_revision_apply(chat_id: str, raw_text: str):
    """Apply the supported uniform-price-header edit to the persisted session + re-overlay the stored
    textless background (slice 2c). Returns (SEND, png) | (REVISION_NEEDED, None) | (FAILCLOSED, [..]).
    Degrades to REVISION_NEEDED ("resend full details") — never a wrong render — when the flag is off,
    no session/background exists, the dump won't rebuild, or the item prices are not uniform."""
    import schemas

    if not REVISION_APPLY_ENABLED:
        return (REVISION_NEEDED, None)
    sess = _load_session(chat_id)
    if not sess:
        return (REVISION_NEEDED, None)
    raw_bg = sess.get("raw_background_path") or ""
    try:
        project = schemas.FlyerProject.model_validate(sess["project"])
    except Exception:  # noqa: BLE001
        return (REVISION_NEEDED, None)
    item_updates = _item_price_updates_from_text(project, raw_text)
    per_item_uniform = _per_item_uniform_price_from_text(raw_text)
    if per_item_uniform:
        item_updates = {idx: per_item_uniform for idx in _project_item_names(project)}
    item_project = _apply_per_item_prices(project, item_updates)
    if item_project is not None:
        project = item_project
    else:
        # The price the customer NAMED in this revision wins ("$8.99 header"); fall back to the
        # session's uniform price only when no price was requested (Codex F1).
        price = _price_from_text(raw_text) or _session_uniform_price(project)
        if not price:
            return (REVISION_NEEDED, None)  # no requested price + non-uniform session -> resend
        project = _apply_uniform_price_header(project, price)
    use_raw_bg = bool(raw_bg and Path(raw_bg).exists())
    try:
        if use_raw_bg:
            png = _reoverlay(project, raw_bg)
        else:
            strict = (
                "Apply this customer revision to the stored flyer facts: "
                + (raw_text or "").strip()
                + ". Keep the same business identity and all stored facts. "
                  "Do not add unrelated text."
            )
            png = _generate_poster(project, strict_note=strict, raw_bg_dest=None)
    except Exception as e:  # noqa: BLE001
        prefix = "reoverlay_error" if use_raw_bg else "revision_render_error"
        return (FAILCLOSED, [f"{prefix}:{type(e).__name__}"])
    ok, blockers = run_visual_qa(png, project)
    if ok:
        # pending: the orchestrator commits the revised session only after delivery (Codex F3).
        _write_session(chat_id, project, raw_bg if use_raw_bg else "", raw_text, model=sess.get("model") or GEN_MODEL, pending=True)
        return (SEND, png)
    return (FAILCLOSED, blockers)


def render_unregistered(raw_text: str) -> bytes:
    """Unregistered / ambiguous sender: render from stated details only; no registered grounding."""
    prompt = (
        "Design a single complete, finished promotional flyer/poster as ONE integrated image. "
        "Render ALL text directly inside the image, large and legible, spelled correctly. "
        "Use ONLY the details the customer provided below. Do NOT invent a business name, address, "
        "phone number, prices, dates, or any claim/offer (delivery, catering, discounts, ordering, "
        '"best in town", grand opening, etc.) that is not stated.\n\nCustomer request:\n' + (raw_text or "").strip()
    )
    return _generate_image(prompt, model=GEN_MODEL)
