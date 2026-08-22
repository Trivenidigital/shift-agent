"""`get_catering_menu_items` — customer-facing read over state/catering-menu.json.

PUBLIC BY DESIGN, on the same terms as `find_nearest_location`. A catering menu
and its prices are information a business publishes; answering "do you have veg
appetizers?" needs no identity. This tool therefore has no owner gate and never
calls `identify-sender`. It reads ONLY the menu store, and its rows carry only
name / price / category / dietary tags / servings — no leads, no customer
records, no pricebook internals, no `notes` field (the owner's own free text,
which is not written for customers to read).

WHY EVERY EXIT IS OVERRIDDEN, including the success path. Same reasoning as
`location_tool`, one notch stronger: a price read out to a customer is a
COMMITMENT, and the failure mode is the model corrupting a fact — inventing a
dish, rounding $12.99 to "about thirteen", or presenting a stale menu as today's
price list. So the factual success path is exactly the one that must be
deterministic. The bounded cost, accepted deliberately: a compound question
("what's your biryani, and do you deliver to Frisco?") has its menu half answered
and the other half dropped for that turn. A dropped follow-up is recoverable; a
wrong price the customer was quoted is not.

THE MENU IS DATED, ALWAYS. Production's menu is version 2 with
`updated_at: 2026-05-05` — three and a half months old at the time of writing.
Every rendered reply therefore carries the menu's own date and an explicit
confirm-when-ordering line, unconditionally. Unconditional rather than
threshold-driven on purpose: a staleness cutoff is a number someone has to keep
true, and the day it drifts the customer is told a stale price with no
qualification at all. The structured result additionally reports
`menu_age_days`, which is the operator's number, not the customer's.

THE STATE DISTINCTION IS THE POINT — four SUCCESSFUL outcomes that must never
collapse into each other:

  disabled   — cfg.catering.enabled is false, or the block is absent and defaults
               to false. The store is never read.
  missing    — no menu file. Nothing is configured; coverage is UNKNOWN.
  empty      — file present, zero items. Configured, nothing on the menu.
  populated  — items exist. `matched` may be 0, which means nothing on the menu
               matched THIS request — not that the menu is empty.

`missing` and `empty` deliberately share one customer reply while staying
distinct in the structured result — the same split `location_tool` documents for
`not_configured` vs `no_usable_locations`: the customer cannot act on the
difference, and the operator can.

Everything else FAILS CLOSED, and fails closed WITH A BOUND REPLY: an unreadable
config or store returns `ok: false`, but binds the safe "not available right now"
sentence first, because an unbound failure on a public surface leaves the model
free to answer a price question from memory.

Items with `available: false` are filtered out before anything else. An
unavailable dish is not a dish the customer can order, and offering it is a
commitment the kitchen has already withdrawn.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

from .identity import fail, ok, refuse

TOOLSET = "shift_agent_read"
TOOL_NAME = "get_catering_menu_items"

# Mirrors schemas.MenuCategory / schemas.DietaryTag. Duplicated into the tool
# SCHEMA because a JSON-Schema enum has to be a literal list at registration
# time, before the platform modules are importable. `test_schema_enums_match_*`
# pins both against the deployed Literals so a schema change cannot drift these
# silently.
CATEGORIES = ["appetizer", "soup", "salad", "main", "side",
              "dessert", "beverage", "special", "package"]
DIETARY_TAGS = ["veg", "non-veg", "vegan", "jain", "halal", "kosher",
                "gluten-free", "nut-free", "dairy-free", "egg-free", "spicy"]

DESCRIPTION = (
    "Look up dishes on the business's catering menu and return their names, "
    "prices, categories, dietary tags and serving sizes. Use for customer "
    "questions like 'do you cater biryani?', 'what veg appetizers do you have?', "
    "'how much is the idly platter?', or 'what's on your catering menu?'. This "
    "reads the published catering menu only — it is not a quote, an order or a "
    "booking.\n"
    "\n"
    "Narrow the lookup with whichever arguments the customer's question implies: "
    "name_contains for a named dish, category for 'appetizers'/'desserts', "
    "dietary_tag for 'vegetarian'/'halal'. Omit all three to list the menu.\n"
    "\n"
    "SCOPE. This tool knows only the PUBLISHED catering menu.\n"
    "  - 'disabled': catering is switched off for this business, so nothing was "
    "checked. This is NOT a statement about what the kitchen cooks.\n"
    "  - 'missing' / 'empty': the menu is unavailable or holds no items. Neither "
    "establishes that the business does not cater.\n"
    "  - 'populated' with matched 0: nothing on the menu matched THIS request. "
    "Say the dish is not on the current catering menu — never that it does not "
    "exist, and never offer a substitute the tool did not return.\n"
    "\n"
    "RULES. Report only the dishes, prices and servings this tool returned. "
    "Never invent a dish, never quote a price this tool did not return, never "
    "round or re-express a price, and never total or discount prices — a price "
    "you state to a customer is a commitment. The menu carries its own "
    "last-updated date; it may not reflect today's prices, so never present it "
    "as guaranteed current. This tool is READ-ONLY: it cannot take an order, "
    "reserve a date, quote an event or start a catering booking. If the customer "
    "wants to order or book, say a person will follow up rather than implying "
    "anything was booked."
)

# registry.register() takes the INNER function object; get_definitions() adds the
# {"type":"function","function":...} wrap. Double-wrapping empties
# function.description and removes the tool from the deferred catalog.
SCHEMA = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "name_contains": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": ("Dish name or fragment the customer named, e.g. "
                                "'biryani' or 'idly'. Case-insensitive."),
            },
            "category": {
                "type": "string",
                "enum": CATEGORIES,
                "description": "Menu section, when the customer named one.",
            },
            "dietary_tag": {
                "type": "string",
                "enum": DIETARY_TAGS,
                "description": ("Dietary requirement the customer stated, e.g. "
                                "'veg' for vegetarian."),
            },
            "max_items": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25,
                "description": ("How many dishes to return. Omit for the "
                                "deterministic default."),
            },
        },
        "required": [],
    },
}

DEFAULT_MAX_ITEMS = 10
HARD_MAX_ITEMS = 25
MAX_NAME_QUERY_LEN = 80

PLATFORM_DIR = str(Path(__file__).resolve().parent.parent.parent / "platform")
CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH",
                                  "/opt/shift-agent/config.yaml"))
# Env-overridable literal, matching the sibling tools. `catering_paths
# .CATERING_MENU_PATH` is the canonical WRITE-side definition and holds the same
# value; it is deliberately not env-overridable, so it cannot be reused here
# without coupling the plugin's tests to the deployed tree.
# `test_menu_path_default_matches_catering_paths` pins the two together.
MENU_PATH = Path(os.environ.get("SHIFT_AGENT_CATERING_MENU_PATH",
                                "/opt/shift-agent/state/catering-menu.json"))

# Customer-safe wording. `missing` and `empty` share a reply on purpose — see the
# module docstring. `config_unavailable` and `state_unreadable` share it too:
# all four mean "I cannot show you the menu right now", and the customer's next
# action is identical in every case.
TPL_DISABLED = (
    "We don't have catering set up through this number, so I can't look up "
    "catering menu items here."
)
TPL_UNAVAILABLE = (
    "Our catering menu isn't available right now. Please contact the store "
    "directly."
)
TPL_NO_MATCH = (
    "I couldn't find that on our current catering menu (last updated "
    "{menu_date}). Please contact the store directly to ask about it."
)
TPL_HEADER = (
    "From our catering menu (prices as of {menu_date} — please confirm when "
    "you order):"
)
TPL_MORE = (
    "That's {returned} of {matched} matching items — tell me what you're "
    "looking for and I can narrow it down."
)


def _ensure_platform_path() -> None:
    """Idempotent guarded insert, matching cf-router's pattern."""
    for p in ("/opt/shift-agent", PLATFORM_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def _config():
    """The validated Config, or None when it cannot be read."""
    try:
        import yaml
        from schemas import Config
        return Config.model_validate(
            yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        return None


def _today(cfg) -> date | None:
    """Today in the CUSTOMER's timezone, or None if that cannot be established.

    Only `menu_age_days` depends on it, and that is an operator-facing number, so
    an unresolvable timezone degrades the field to None rather than failing the
    whole read — the customer-facing date comes from the menu itself.
    """
    override = os.environ.get("SHIFT_AGENT_NOW_OVERRIDE", "")
    if override:
        try:
            return datetime.fromisoformat(override).date()
        except ValueError:
            return None
    try:
        from safe_io import customer_now
        return customer_now(cfg.customer.timezone).date()
    except Exception:
        return None


def _bind_outbound(text: str) -> bool:
    """Bind the deterministic reply to THIS turn. Must run inside the handler:
    `AIAgent.run_conversation` reassigns HERMES_SESSION_ID, and a key read
    earlier fails OPEN, letting an unvalidated price through."""
    _ensure_platform_path()
    try:
        from safe_io import register_turn_outbound_override
        return bool(register_turn_outbound_override(text))
    except Exception:
        return False


def _answer(text: str, **fields) -> str:
    """The ONLY successful exit. Binding is attempted first, and nothing factual
    — not a status, not a count, not a price — is returned unless it succeeded."""
    if not _bind_outbound(text):
        return refuse("outbound_truthfulness_guard_unavailable")
    return ok(**fields)


def _fail_bound(error: str) -> str:
    """An operational failure that still leaves the customer a safe sentence.

    The sibling owner-only tools return a bare `fail()` here; on a PUBLIC surface
    that is not enough. An unbound failure hands the turn back to a model that
    was just asked what the biryani costs, with nothing to stop it answering from
    memory. So the safe wording is bound first and the failure is still reported
    honestly as `ok: false`.
    """
    if not _bind_outbound(TPL_UNAVAILABLE):
        return refuse("outbound_truthfulness_guard_unavailable")
    return fail(error)


def _price(value) -> str:
    """`$12.99`, or an explicit no-price marker. Never invented, never rounded."""
    if value is None:
        return "price on request"
    return f"${value:.2f}"


def _render(rows: list, menu_date: str, matched: int) -> str:
    """Bounded deterministic reply. Every field is emitted verbatim from the
    store; nothing is inferred, reformatted or filled in."""
    lines = [TPL_HEADER.format(menu_date=menu_date)]
    for i, r in enumerate(rows, 1):
        parts = [r["name"], _price(r["price_usd"])]
        if r["serves"] is not None:
            parts.append(f"serves ~{r['serves']}")
        if r["dietary_tags"]:
            parts.append(", ".join(r["dietary_tags"]))
        lines.append(f"{i}. " + " — ".join(parts))
    if matched > len(rows):
        lines.append(TPL_MORE.format(returned=len(rows), matched=matched))
    return "\n".join(lines)


def _max_items(args) -> int | None:
    """The row cap, or None when the model supplied something out of schema.

    Rejected rather than silently clamped, mirroring `location_tool.top_n`: a
    hidden correction answers a question the customer did not ask.
    """
    raw = args.get("max_items", DEFAULT_MAX_ITEMS)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= HARD_MAX_ITEMS else None


def handler(args=None, **kwargs) -> str:
    """Model arguments arrive positionally and carry no identity or chat source."""
    args = args or {}

    name_query = str(args.get("name_contains") or "").strip()
    if len(name_query) > MAX_NAME_QUERY_LEN:
        return refuse("invalid_arguments")
    category = str(args.get("category") or "").strip()
    if category and category not in CATEGORIES:
        return refuse("invalid_arguments")
    dietary_tag = str(args.get("dietary_tag") or "").strip()
    if dietary_tag and dietary_tag not in DIETARY_TAGS:
        return refuse("invalid_arguments")
    max_items = _max_items(args)
    if max_items is None:
        return refuse("invalid_arguments")

    _ensure_platform_path()

    cfg = _config()
    if cfg is None:
        return _fail_bound("config_unavailable")
    if not cfg.catering.enabled:
        # CateringConfig.enabled defaults False and an absent block validates to
        # that default, so an un-onboarded box lands here and the store is never
        # read.
        return _answer(TPL_DISABLED, source_status="disabled",
                       coverage_status="not_enabled")

    # Existence is checked BEFORE loading so `missing` can never be reported as
    # `empty`. Menu requires `updated_at`, so a loader-first order would raise
    # here rather than collapse — but the two states still have to be told apart
    # in the structured result, and only this ordering does that.
    if not MENU_PATH.exists():
        return _answer(TPL_UNAVAILABLE, source_status="missing",
                       coverage_status="not_configured")

    from safe_io import load_model
    from schemas import Menu
    try:
        menu, _ = load_model(MENU_PATH, Menu)
    except Exception:
        # Corrupt, empty-file or schema-invalid store. NOT an empty menu.
        return _fail_bound("state_unreadable")

    menu_date = menu.updated_at.date().isoformat()
    today = _today(cfg)
    menu_age_days = (today - menu.updated_at.date()).days if today else None

    if not menu.items:
        return _answer(TPL_UNAVAILABLE, source_status="empty",
                       menu_version=menu.version, menu_updated_at=menu_date,
                       menu_age_days=menu_age_days,
                       items_total=0, available_total=0, matched=0,
                       returned=0, items=[])

    # `available: false` is withdrawn by the kitchen — filtered before any other
    # predicate so an unavailable dish can never be matched, counted or offered.
    available = [i for i in menu.items if i.available]

    needle = name_query.casefold()
    rows = [
        {
            "name": i.name,
            "price_usd": i.price_usd,
            "category": i.category,
            "dietary_tags": list(i.dietary_tags),
            "serves": i.serves,
        }
        for i in available
        if (not needle or needle in i.name.casefold())
        and (not category or i.category == category)
        and (not dietary_tag or dietary_tag in i.dietary_tags)
    ]

    common = dict(source_status="populated", menu_version=menu.version,
                  menu_updated_at=menu_date, menu_age_days=menu_age_days,
                  items_total=len(menu.items), available_total=len(available),
                  matched=len(rows))

    if not rows:
        # The menu is populated and simply has nothing matching THIS request —
        # the state a model most reliably over-generalizes into "we don't cater
        # that" or, worse, into a helpfully invented alternative.
        return _answer(TPL_NO_MATCH.format(menu_date=menu_date),
                       returned=0, items=[], **common)

    shown = rows[:max_items]
    return _answer(_render(shown, menu_date, len(rows)),
                   returned=len(shown), items=shown, **common)
