#!/usr/bin/env python3
"""E2E simulated-customer catering conversation harness (standing regression suite).

LIVE-PARITY local twin of the production loop:
  * REAL deterministic cf-router dispatch (_pre_gateway_dispatch_impl, flag ON)
    with the house stubs from tests/test_catering_pra_reachability.py (fcntl stub,
    identify-sender stub) + tests/test_catering_v02_scripts.py (subprocess-boundary
    scripts run against a sandbox state dir, bridge sends captured).
  * REAL catering scripts (create-catering-lead, create-catering-proposal-options
    incl. the PR-D --recompose-from-sent mode, send-catering-ack,
    catering-lead-ttl-sweep) run IN-PROCESS via SourceFileLoader with path
    constants patched to the sandbox (Windows/fcntl forces in-process over
    subprocess; same code paths, one interpreter).
  * FREE-FLOW LLM (OpenRouter) plays the Hermes brain under the ACTUAL SKILL.md
    prompts (catering_dispatcher + creative_catering_proposals) when cf-router
    dispatch returns None. The brain replies naturally and may invoke a tool
    (create-catering-proposal-options / recompose-from-sent); the customer sees
    its prose plus any script-rendered output it triggers.

RUN (makes real model calls — env-gated so CI never does):
    set -a; . scratch/.e2e-llm.env; set +a       # provides OPENROUTER_API_KEY
    python tests/e2e/catering_conversation_harness.py --out tests/e2e/artifacts
Skips cleanly (raises RuntimeError before any call) when OPENROUTER_API_KEY unset.
The pytest wrapper tests/e2e/test_catering_conversation_e2e.py skipif-guards on it.
Model defaults to the tenant default openai/gpt-4o-mini; the gate must hold there.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import ssl
import tempfile
import urllib.request
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# ── Repo-relative locations (tests/e2e/ -> repo root) ─────────────────────────
REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
PLATFORM = SRC / "platform"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
TEMPLATES_SRC = SRC / "agents" / "catering" / "templates"
TESTS = REPO / "tests"
MENU_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catering-menu-e2e.json"

# Sandbox lives in a temp dir (override with E2E_SANDBOX_DIR); never in the repo tree.
STATE = Path(os.environ.get("E2E_SANDBOX_DIR") or (Path(tempfile.gettempdir()) / "catering-e2e-sandbox"))
ST = STATE / "state"
LOGS = STATE / "logs"
CONFIG_PATH = STATE / "config.yaml"
LEADS_PATH = ST / "catering-leads.json"
MENU_PATH = ST / "catering-menu.json"
PROPOSALS_PATH = ST / "catering-proposals.json"
AMEND_PATH = ST / "catering-amendments.json"
DECISIONS_LOG = LOGS / "decisions.log"
TEMPLATES = STATE / "templates"

PHONE = "+17329837841"
CHAT = "17329837841@lid"
OWNER_JID = "19045550100@s.whatsapp.net"

MODEL = os.environ.get("E2E_LLM_MODEL", "openai/gpt-4o-mini")
LLM_CALLS = 0
TLS_FALLBACK_USED = False  # set True iff strict verify failed and we downgraded (a MITM CA box)

# ── sys.path + fcntl stub (house seam) ───────────────────────────────────────
for p in (str(PLATFORM), str(SRC), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)
from fixtures_fleet import ensure_fcntl_stub, load_script  # noqa: E402
ensure_fcntl_stub()

import yaml  # noqa: E402


# ── Sandbox seed ─────────────────────────────────────────────────────────────
def _now_iso(dt: datetime) -> str:
    return dt.isoformat()


def build_sandbox() -> None:
    import shutil
    shutil.rmtree(STATE, ignore_errors=True)  # clean slate every run (drops stale dedupe/state)
    for d in (ST, LOGS, TEMPLATES):
        d.mkdir(parents=True, exist_ok=True)
    # Templates (copy real ones so the approval-card renderer finds them)
    for f in TEMPLATES_SRC.iterdir():
        if f.is_file():
            (TEMPLATES / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")

    cfg = {
        "schema_version": 1,
        "customer": {"name": "Triveni Test", "location_id": "loc_e2e",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": OWNER_JID},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    CONFIG_PATH.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    # Stale L0017 — live-shaped: AWAITING_OWNER_APPROVAL, created 2026-06-09,
    # headcount 60, event 2026-06-28 (materially different from the incident).
    leads = {
        "schema_version": 1,
        "leads": [
            {
                "lead_id": "L0017",
                "status": "AWAITING_OWNER_APPROVAL",
                "customer_phone": PHONE,
                "customer_name": None,
                "raw_inquiry": "(seed) earlier catering inquiry for a june event",
                "original_message_id": "wamid.SEED.L0017",
                "created_at": "2026-06-09T00:00:00+00:00",
                "updated_at": "2026-06-09T00:00:00+00:00",
                "extracted": {
                    "headcount": 60,
                    "event_date": "2026-06-28",
                    "event_time": None,
                    "menu_preferences": [],
                    "dietary_restrictions": [],
                    "delivery_or_pickup": "unknown",
                    "budget_hint_usd": None,
                    "notes": "seed stale lead",
                    "off_menu_items": [],
                },
                "quote_text": "Seed quote pending owner review.",
                "owner_approval_code": "#GEMAZ",
            }
        ],
    }
    LEADS_PATH.write_text(json.dumps(leads, indent=2), encoding="utf-8")

    # Menu = the 78 real live items
    menu = json.loads(MENU_FIXTURE.read_text(encoding="utf-8"))
    MENU_PATH.write_text(json.dumps(menu, indent=2), encoding="utf-8")

    PROPOSALS_PATH.write_text(json.dumps(
        {"schema_version": 1, "next_sequence": 1, "sets": []}, indent=2), encoding="utf-8")
    AMEND_PATH.write_text(json.dumps(
        {"schema_version": 1, "next_seq": 1, "records": []}, indent=2), encoding="utf-8")
    DECISIONS_LOG.write_text("", encoding="utf-8")


# ── Bridge send capture ──────────────────────────────────────────────────────
SENDS: list[dict] = []      # {turn, via, jid, message}
CURRENT_TURN = 0


def _make_capture(via: str):
    def _cap(jid: str, message: str):
        SENDS.append({"turn": CURRENT_TURN, "via": via, "jid": jid, "message": message})
        return True, f"msg_{via}_{len(SENDS):04d}"
    return _cap


# ── In-process script runner (v02 seam, in-process for Windows/fcntl) ─────────
_SCRIPT_CACHE: dict[str, object] = {}


def _load_patched(name: str, filename: str, patches: dict) -> object:
    mod = load_script(name, SCRIPTS / filename)
    for k, v in patches.items():
        setattr(mod, k, v)
    return mod


def _run_main(mod, argv, stdin_text=None):
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = argv
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)
    out, err = io.StringIO(), io.StringIO()
    rc = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin
    return rc, out.getvalue(), err.getvalue()


def run_create_lead(*, customer_phone, customer_name, raw_inquiry, message_id, fields_json):
    mod = _load_patched("e2e_ccl", "create-catering-lead", {
        "CONFIG_PATH": CONFIG_PATH, "LEADS_PATH": LEADS_PATH,
        "LEADS_LOCK": Path(str(LEADS_PATH) + ".lock"), "LOG_PATH": DECISIONS_LOG,
        "TEMPLATE_DIR": TEMPLATES, "MENU_PATH": MENU_PATH,
        "_bridge_post": _make_capture("create-catering-lead"),
    })
    argv = ["create-catering-lead", "--customer-phone", customer_phone,
            "--customer-name", customer_name, "--raw-inquiry", raw_inquiry[:1000],
            "--message-id", message_id, "--fields-json", fields_json]
    return _run_main(mod, argv)


def run_send_ack(jid, text, lead_id):
    mod = _load_patched("e2e_ack", "send-catering-ack", {
        "LOG_PATH": DECISIONS_LOG, "_bridge_post": _make_capture("send-catering-ack"),
    })
    argv = ["send-catering-ack", "--customer-jid", jid, "--message-text", text,
            "--lead-id", lead_id]
    return _run_main(mod, argv)


def run_proposal_options(*, lead_id, customer_jid, source_message_id, request_text, options_json):
    mod = _load_patched("e2e_cpo", "create-catering-proposal-options", {
        "PROPOSALS_PATH": PROPOSALS_PATH, "PROPOSALS_LOCK": Path(str(PROPOSALS_PATH) + ".lock"),
        "LEADS_PATH": LEADS_PATH, "LEADS_LOCK": Path(str(LEADS_PATH) + ".lock"),
        "MENU_PATH": MENU_PATH, "LOG_PATH": DECISIONS_LOG,
        "LOG_LOCK": Path(str(DECISIONS_LOG) + ".lock"),
        "_bridge_post": _make_capture("create-catering-proposal-options"),
        "_notify_owner_generation_failed": lambda *a, **k: None,
    })
    argv = ["create-catering-proposal-options", "--lead-id", lead_id,
            "--customer-jid", customer_jid, "--source-message-id", source_message_id,
            "--request-text", request_text, "--options-json", "-"]
    return _run_main(mod, argv, stdin_text=options_json), mod


def run_recompose_from_sent(*, lead_id, customer_jid, source_message_id, request_text):
    """PR-D mix-and-match: the deterministic --recompose-from-sent script mode."""
    mod = _load_patched("e2e_cpo_rc", "create-catering-proposal-options", {
        "PROPOSALS_PATH": PROPOSALS_PATH, "PROPOSALS_LOCK": Path(str(PROPOSALS_PATH) + ".lock"),
        "LEADS_PATH": LEADS_PATH, "LEADS_LOCK": Path(str(LEADS_PATH) + ".lock"),
        "MENU_PATH": MENU_PATH, "LOG_PATH": DECISIONS_LOG,
        "LOG_LOCK": Path(str(DECISIONS_LOG) + ".lock"),
        "_bridge_post": _make_capture("recompose-from-sent"),
        "_notify_owner_generation_failed": lambda *a, **k: None,
    })
    argv = ["create-catering-proposal-options", "--lead-id", lead_id,
            "--customer-jid", customer_jid, "--source-message-id", source_message_id,
            "--request-text", request_text, "--recompose-from-sent"]
    return _run_main(mod, argv)


def run_select_proposal(*, lead_id, customer_jid, customer_message_id, selection_text):
    mod = _load_patched("e2e_sel", "select-catering-proposal", {
        "PROPOSALS_PATH": PROPOSALS_PATH, "PROPOSALS_LOCK": Path(str(PROPOSALS_PATH) + ".lock"),
        "LEADS_PATH": LEADS_PATH, "LEADS_LOCK": Path(str(LEADS_PATH) + ".lock"),
        "MENU_PATH": MENU_PATH, "LOG_PATH": DECISIONS_LOG,
        "LOG_LOCK": Path(str(DECISIONS_LOG) + ".lock"),
        "_bridge_post": _make_capture("select-catering-proposal"),
    })
    argv = ["select-catering-proposal", "--lead-id", lead_id, "--customer-jid", customer_jid,
            "--customer-message-id", customer_message_id, "--selection-text", selection_text]
    return _run_main(mod, argv)


def run_ttl_sweep():
    mod = load_script("e2e_ttl", SCRIPTS / "catering-lead-ttl-sweep")
    mod.CONFIG_PATH = CONFIG_PATH
    mod.LEADS_PATH = LEADS_PATH
    mod.LEADS_LOCK = Path(str(LEADS_PATH) + ".lock")
    mod.LOG_PATH = DECISIONS_LOG
    return _run_main(mod, ["catering-lead-ttl-sweep"])


# ── Load plugin (reachability seam: package trick for `from . import actions`) ─
def load_plugin():
    import importlib.machinery
    import importlib.util
    pkg = "cf_router_e2e_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(spec)

    def _load(name):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        sp = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(sp)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    actions = _load("actions")
    hooks = _load("hooks")
    return hooks, actions


# ── Wire the deployed dispatch against the sandbox ────────────────────────────
def wire(hooks, actions):
    # Flags at production posture: F7 ON, PR-A discriminator/escape ON, UX reply ON.
    hooks.F7_ENABLED = True
    hooks.F7_PROPOSAL_BRANCH_ENABLED = True
    hooks.F7_PRIMARY_FOLLOWUP_REPLY = True

    # Path constants → sandbox.
    actions.CONFIG_PATH = CONFIG_PATH
    actions.LEADS_PATH = LEADS_PATH
    actions.PROPOSALS_PATH = PROPOSALS_PATH
    actions.LOG_PATH = DECISIONS_LOG
    actions.PLATFORM_DIR = PLATFORM
    actions.ROSTER_PATH = ST / "roster.json"
    actions.PENDING_PATH = ST / "pending.json"
    actions.REVENUE_ROUTE_CLARIFICATION_PATH = ST / "revenue-route-clarifications.json"
    actions._DEFAULT_CF_ROUTER_INBOUND_DEDUPE_PATH = ST / "cf-router-inbound-dedupe.json"
    actions.THROTTLE_PATH = ST / "cf-router-throttle.json"
    actions.MENU_PENDING_PATH = ST / "catering-menu-pending.json"

    # Identity + role stubs (house seam: deterministic customer identity).
    actions.lid_to_phone_via_identify_sender = lambda cid: (PHONE, "customer")
    actions.is_owner_chat = lambda cid: False
    actions.is_verified_employee_chat = lambda cid: False

    # Flyer surface OFF → pure catering path.
    actions.is_flyer_enabled = lambda: False
    actions.is_flyer_workflow_enabled = lambda: False

    # Subprocess-boundary → real scripts in-process against the sandbox.
    def _trigger_create_lead(customer_phone, customer_name, raw_inquiry, message_id,
                             extracted_fields=None):
        fields = {"headcount": None, "event_date": None, "event_time": None,
                  "menu_preferences": [], "off_menu_items": [], "dietary_restrictions": [],
                  "delivery_or_pickup": "unknown", "budget_hint_usd": None,
                  "notes": "(cf-router F7 rescue from missed-dispatch; LLM bypassed parse_catering_inquiry SKILL)"}
        if extracted_fields:
            fields.update(extracted_fields)
        rc, out, err = run_create_lead(customer_phone=customer_phone, customer_name=customer_name,
                                       raw_inquiry=raw_inquiry, message_id=message_id,
                                       fields_json=json.dumps(fields))
        if rc == 0:
            return True, out.strip().splitlines()[-1] if out.strip() else ""
        return False, f"exit={rc} stderr={err[:500]}"
    actions.trigger_create_catering_lead = _trigger_create_lead

    def _send_canonical(chat_id, lead_id):
        template = (f"Your inquiry {lead_id} is with the owner for review. "
                    f"They'll send a final quote within 24 hours. "
                    f"Reply here if you need to adjust the inquiry.")
        rc, _o, _e = run_send_ack(chat_id, template, lead_id)
        return rc == 0
    actions.send_canonical_followup_reply = _send_canonical

    def _select(lead_id, chat_id, message_id, text):
        rc, _o, _e = run_select_proposal(lead_id=lead_id, customer_jid=chat_id,
                                         customer_message_id=message_id, selection_text=text)
        return rc
    actions.invoke_select_catering_proposal = _select

    # hooks-level ack helpers (subprocess → in-process send-catering-ack).
    def _cross_ref(chat_id, new_lead_id, prior_lead_id):
        template = (f"I've also got your earlier inquiry {prior_lead_id} on file — is this a "
                    f"separate event? I've started {new_lead_id} for this one.")
        rc, _o, _e = run_send_ack(chat_id, template, new_lead_id)
        return rc == 0
    hooks._send_fresh_lead_cross_reference_ack = _cross_ref

    def _clarify(chat_id, lead_id):
        template = (f"Just to confirm — is this about your existing inquiry {lead_id}, or a "
                    f"new event? Reply and I'll route it to the right one.")
        rc, _o, _e = run_send_ack(chat_id, template, lead_id)
        return rc == 0
    hooks._send_fresh_inquiry_clarification = _clarify

    def _retry(chat_id, lead_id):
        template = (f"Sorry — we couldn't record your update to inquiry {lead_id} just now. "
                    f"Please resend it in a few minutes so we can add it for the owner.")
        rc, _o, _e = run_send_ack(chat_id, template, lead_id)
        return rc == 0
    hooks._send_amendment_retry_reply = _retry

    # Env for catering_amendments sidecar (env-overridable at call time).
    os.environ["SHIFT_AGENT_CONFIG_PATH"] = str(CONFIG_PATH)
    os.environ["SHIFT_AGENT_STATE_DIR"] = str(ST)
    os.environ["SHIFT_AGENT_CATERING_AMENDMENTS_PATH"] = str(AMEND_PATH)
    os.environ["SHIFT_AGENT_DECISIONS_LOG_PATH"] = str(DECISIONS_LOG)


# ── LLM (OpenRouter) ─────────────────────────────────────────────────────────
def llm_chat(system: str, user: str, *, max_tokens=1500) -> str:
    global LLM_CALLS
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY unset — this harness makes real model calls; "
                           "source scratch/.e2e-llm.env before running (CI must skip it).")
    LLM_CALLS += 1
    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    # TLS: verify by default (uses the OS trust store on Windows). This box sits
    # behind a corporate MITM proxy whose CA cert is malformed (Basic Constraints
    # not marked critical) — OpenSSL 3 rejects it regardless of trust store, so on
    # THIS box only we downgrade to unverified for the single OpenRouter host and
    # record the downgrade (surfaced in the report). On a clean box / CI, verify holds.
    global TLS_FALLBACK_USED
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
    except (ssl.SSLError, urllib.error.URLError) as e:
        if not (isinstance(e, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(e)):
            raise
        TLS_FALLBACK_USED = True
        unv = ssl.create_default_context()
        unv.check_hostname = False
        unv.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=120, context=unv) as resp:
            return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]


def _parse_json_blob(text: str):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    # First {...} or [...] blob
    m = re.search(r"(\{.*\}|\[.*\])", t, re.DOTALL)
    if m:
        t = m.group(1)
    return json.loads(t)


# ── Hermes emulation (dispatcher → creative → real script) ───────────────────
DISPATCHER_SKILL = (SRC / "agents" / "catering" / "skills" / "catering_dispatcher" / "SKILL.md").read_text(encoding="utf-8")
CREATIVE_SKILL = (SRC / "agents" / "catering" / "skills" / "creative_catering_proposals" / "SKILL.md").read_text(encoding="utf-8")


def _active_leads_state(actions):
    leads = actions.find_all_eligible_catering_leads_by_sender(PHONE, CHAT)
    out = []
    for lead in leads:
        ex = lead.get("extracted") or {}
        out.append({"lead_id": lead.get("lead_id"), "status": lead.get("status"),
                    "headcount": ex.get("headcount"), "event_date": ex.get("event_date")})
    return out


def _sent_sets_state():
    store = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    return [{"proposal_set_id": s["proposal_set_id"], "lead_id": s["lead_id"],
             "status": s["status"], "option_count": len(s["options"])}
            for s in store.get("sets", []) if s.get("status") == "SENT"]


PERSONA = (
    "You are the Catering Agent for Triveni Supermarket. You converse DIRECTLY "
    "with the customer over WhatsApp and compose the customer-facing reply "
    "yourself. Your operating rules are the two skill documents below "
    "(catering_dispatcher, then creative_catering_proposals) - follow them "
    "exactly, including every hard rule about pricing, off-menu items, "
    "mix-and-match, stalls, and never leaking internal flow to the customer.\n"
)

TOOL_PROTOCOL = (
    "\n\n=== TOOL PROTOCOL (harness) ===\n"
    "You have TWO tools, each of which RENDERS a menu and SENDS it to the "
    "customer. Use a tool ONLY when actually sending a menu - NEVER for a price "
    "question, an off-menu refusal, or a general question. Append EXACTLY one "
    "tool block at the very END of your reply.\n"
    "\nTOOL 1 - create-catering-proposal-options (fresh proposal options):\n"
    "<<<INVOKE create-catering-proposal-options\n"
    "lead_id: <active lead id>\n"
    "request_text: <one short line naming what the customer asked>\n"
    "options_json: <a JSON array on ONE line: [{\"option_id\":\"1\","
    "\"style_key\":\"balanced_mixed|premium_mixed|classic_family\","
    "\"tier\":\"balanced|premium|classic\",\"item_names\":[\"<exact menu name>\"]}]>\n"
    ">>>\n"
    "Rules: EXACTLY {allowed} option(s); option_id \"1\"..\"{allowed}\" unique; every "
    "item_name EXACT from the MENU below; NO prices/payment/booking words.\n"
    "\nTOOL 2 - recompose-from-sent (MIX-AND-MATCH of already-SENT options):\n"
    "<<<INVOKE recompose-from-sent\n"
    "lead_id: <active lead id>\n"
    "request_text: <the customer's EXACT combination, e.g. 'option 1 starters with option 2 mains'>\n"
    ">>>\n"
    "Use TOOL 2 whenever the customer asks to combine sections of options you "
    "already sent. Do NOT compose the items yourself and do NOT use TOOL 1 for a "
    "mix - the recompose tool pulls the named sections VERBATIM from the sent "
    "options and validates them, or sends ONE clarifying question if the request "
    "is imperfect. Pass only the combination phrasing; no options_json.\n"
    "\nBoth tools send the rendered menu to the customer, so do NOT paste menu "
    "contents in your prose. Any text OUTSIDE the block is your customer-facing "
    "message, sent verbatim. If you invoke with no prose, only the tool output is sent.\n"
)

_INVOKE_RE = re.compile(
    r"<<<INVOKE\s+(create-catering-proposal-options|recompose-from-sent)\s*(.*?)>>>", re.DOTALL)


def _menu_listing():
    menu = json.loads(MENU_PATH.read_text(encoding="utf-8"))
    nonveg = re.compile(r"\b(chicken|mutton|goat|lamb|fish|shrimp|prawn|egg|beef|turkey|keema|apollo)\b", re.I)
    lines = []
    for it in menu["items"]:
        tags = [t.lower() for t in it.get("dietary_tags", [])]
        vt = "non-veg" if ("non-veg" in tags or "nonveg" in tags or nonveg.search(it["name"])) else "veg"
        lines.append(f"{it['name']} | {it['category']} | {vt}")
    return "\n".join(lines)


def _parse_invoke_block(reply):
    """Return (prose, invoke_dict_or_None). invoke_dict: tool/lead_id/request_text/options_json_raw."""
    m = _INVOKE_RE.search(reply)
    if not m:
        return reply.strip(), None
    tool = m.group(1)
    body = m.group(2)
    prose = (reply[:m.start()] + reply[m.end():]).strip()
    lead = re.search(r"lead_id:\s*(\S+)", body)
    reqt = re.search(r"request_text:\s*(.+)", body)
    oj = re.search(r"options_json:\s*(\[.*\])", body, re.DOTALL)
    inv = {
        "tool": tool,
        "lead_id": (lead.group(1).strip() if lead else None),
        "request_text": (reqt.group(1).strip() if reqt else ""),
        "options_json_raw": (oj.group(1).strip() if oj else None),
    }
    return prose, inv


def emulate_hermes(actions, *, turn_text, message_id, history):
    """FREE-FLOW Hermes-brain emulation (PR-D). The model gets the agent persona +
    BOTH SKILL.md docs + state + menu, then replies naturally. We parse an optional
    create-catering-proposal-options invocation out of the reply and execute it
    against the sandbox; the customer sees the model's prose PLUS any script-rendered
    output it triggers. No forced JSON routing."""
    active = _active_leads_state(actions)
    sent = _sent_sets_state()
    real_active = active[0]["lead_id"] if active else None
    allowed = 3 if re.search(r"\b(three|3)\b", turn_text, re.I) else 2
    convo = "\n".join(f"{r.upper()}: {t}" for r, t in history[:-1]) or "(none)"

    system = (PERSONA + "\n=== SKILL: catering_dispatcher ===\n" + DISPATCHER_SKILL
              + "\n\n=== SKILL: creative_catering_proposals ===\n" + CREATIVE_SKILL
              + TOOL_PROTOCOL.replace("{allowed}", str(allowed)))
    user = (
        "=== STATE (read-only) ===\n"
        f"sender_role: customer\nsender_phone: {PHONE}\nchat_id: {CHAT}\n"
        "owner_name: the owner\n"
        f"active_catering_leads_for_sender (most-recent first): {json.dumps(active)}\n"
        f"SENT_proposal_sets (already sent to this customer): {json.dumps(sent)}\n"
        "=== MENU (name | category | veg/non-veg - the ONLY item names you may use) ===\n"
        + _menu_listing() + "\n"
        "=== CONVERSATION SO FAR ===\n" + convo + "\n"
        f"=== CURRENT INBOUND (message_id={message_id}) ===\n{turn_text}\n"
        "Compose your customer-facing reply now, following the skills and tool protocol."
    )

    events = []
    try:
        reply = llm_chat(system, user, max_tokens=1800)
    except Exception:
        try:
            reply = llm_chat(system, user, max_tokens=1800)
        except Exception as e2:
            events.append({"step": "agent", "error": f"{type(e2).__name__}: {e2}"})
            return events

    prose, inv = _parse_invoke_block(reply)
    events.append({"step": "agent", "prose": prose[:1000], "invoked": bool(inv)})

    if prose:
        SENDS.append({"turn": CURRENT_TURN, "via": "hermes_freeflow", "jid": CHAT, "message": prose})

    if inv is not None and inv.get("tool") == "recompose-from-sent":
        # PR-D mix-and-match: the DETERMINISTIC script pulls sections verbatim from
        # the sent options (ignoring anything the brain might have composed) and
        # validates or clarifies. The brain only had to route here.
        target = real_active or inv.get("lead_id")
        rc, out, err = run_recompose_from_sent(
            lead_id=target, customer_jid=CHAT, source_message_id=message_id,
            request_text=inv.get("request_text") or turn_text)
        events.append({"step": "recompose", "lead_id": target, "rc": rc,
                       "stdout": out.strip(), "stderr": err.strip()[:600]})
    elif inv is not None:
        target = real_active or inv.get("lead_id")
        options_json = None
        raw = inv.get("options_json_raw")
        for attempt in range(2):
            try:
                if raw is None:
                    raise ValueError("no options_json in invoke block")
                options = json.loads(raw)
                options_json = json.dumps(options)
                break
            except Exception as e:
                if attempt == 0:
                    try:
                        fix = llm_chat(system, user + "\n\nYour options_json was malformed. "
                                       "Re-emit ONLY the JSON array for options_json, nothing else.",
                                       max_tokens=1500)
                        mm = re.search(r"(\[.*\])", fix, re.DOTALL)
                        raw = mm.group(1) if mm else None
                        continue
                    except Exception:
                        raw = None
                        continue
                events.append({"step": "proposal", "error": f"options_json unparseable: {type(e).__name__}: {e}"})
                return events
        (rc, out, err), _mod = run_proposal_options(
            lead_id=target, customer_jid=CHAT, source_message_id=message_id,
            request_text=inv.get("request_text") or turn_text, options_json=options_json)
        events.append({"step": "proposal", "lead_id": target, "rc": rc,
                       "stdout": out.strip(), "stderr": err.strip()[:600]})
    return events


# ── Turn driver ──────────────────────────────────────────────────────────────
def read_log_rows():
    if not DECISIONS_LOG.exists():
        return []
    return [json.loads(l) for l in DECISIONS_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def make_event(text, mid):
    return SimpleNamespace(text=text, chat_id=CHAT, message_id=mid,
                           timestamp=str(int(datetime.now(timezone.utc).timestamp())),
                           transport="whatsapp")


TRANSCRIPT: list[str] = []
HISTORY: list[tuple] = []


def drive_turn(hooks, actions, n, text, mid):
    global CURRENT_TURN
    CURRENT_TURN = n
    send_start = len(SENDS)
    log_start = len(read_log_rows())
    HISTORY.append(("customer", text))

    ev = make_event(text, mid)
    dispatch_result = hooks._pre_gateway_dispatch_impl(ev)

    hermes_events = []
    if dispatch_result is None:
        hermes_events = emulate_hermes(actions, turn_text=text, message_id=mid, history=HISTORY)

    new_sends = SENDS[send_start:]
    new_rows = read_log_rows()[log_start:]
    # Append agent outbounds to history for context continuity.
    for s in new_sends:
        HISTORY.append(("agent", s["message"]))
    return {"turn": n, "inbound": text, "message_id": mid,
            "dispatch_result": dispatch_result, "hermes_events": hermes_events,
            "sends": new_sends, "audit_rows": new_rows}


# -- Assertions (PR-D free-flow) --------------------------------------------
def customer_sends(sends):
    return [s for s in sends if s["jid"].startswith("17329837841")]


def proposal_sends(sends):
    return [s for s in sends if s["via"] == "create-catering-proposal-options"]


def recompose_sends(sends):
    return [s for s in sends if s["via"] == "recompose-from-sent"]


def prose_sends(sends):
    return [s for s in sends if s["via"] == "hermes_freeflow"]


PRICE_DIGIT_RE = re.compile(
    r"\$\s*\d|\b\d+(?:\.\d{1,2})?\s*(?:usd|dollars?|bucks|/|per)\b"
    r"|\b\d+(?:\.\d{1,2})?\s*(?:per\s+)?(?:person|plate|guest|head|pax)\b", re.I)

STALL_RE = re.compile(r"\b(let me check|hold on|please wait|one moment|get back to you|bear with|i'?ll check)\b", re.I)
LEAK_RE = re.compile(r"#[A-HJ-NP-Z2-9]{5}\b|proposal[_ ]?set|approval code|\bL0\d{3}\b|CPS-|send_message|cockpit|with the owner for review", re.I)


def reasons(rows):
    return [r.get("reason") for r in rows if r.get("type") == "cf_router_intercepted"]


def _menu_items_in(text):
    return re.findall(r"^- (.+)$", text, re.M)


def _proposal_categories(body):
    """Category section headers present in a rendered proposal body (e.g. 'Appetizer', 'Main')."""
    return set(re.findall(r"^([A-Z][a-z]+):$", body, re.M))


def assess(turn_no, res, menu_names, no_price_re):
    A = []
    sends = res["sends"]
    csends = customer_sends(sends)
    prose = " ".join(s["message"] for s in prose_sends(sends))
    psends = proposal_sends(sends)
    pbody = psends[-1]["message"] if psends else ""
    rslist = reasons(res["audit_rows"])
    leads_now = json.loads(LEADS_PATH.read_text(encoding="utf-8"))["leads"]
    lead_ids = [l["lead_id"] for l in leads_now]
    allc = " ".join(s["message"] for s in csends)

    if turn_no == 1:
        A.append(("new_lead_created_not_L0017", any(l != "L0017" for l in lead_ids), f"leads={lead_ids}"))
        A.append(("audit_f7_fresh_inquiry_new_lead_over_stale", "f7_fresh_inquiry_new_lead_over_stale" in rslist, f"reasons={rslist}"))
        A.append(("audit_f7_proposal_request_escaped_to_dispatcher", "f7_proposal_request_escaped_to_dispatcher" in rslist, f"reasons={rslist}"))
        xref = [s for s in csends if "earlier inquiry L0017" in s["message"]]
        A.append(("cross_reference_ack_mentions_earlier_inquiry", len(xref) >= 1, "found" if xref else "MISSING"))
        opt = len(re.findall(r"^\*Option \d", pbody, re.M))
        A.append(("two_options_generated", opt == 2, f"option_titles={opt}"))
        bad = [i for i in _menu_items_in(pbody) if i not in menu_names]
        A.append(("all_items_in_menu", pbody != "" and not bad, f"off_menu={bad}"))
        A.append(("no_price_re_clean_on_proposal", pbody != "" and no_price_re.search(pbody) is None, "clean" if pbody and no_price_re.search(pbody) is None else "PRICE/empty"))
    elif turn_no == 2:
        A.append(("no_third_lead_no_refork", len(lead_ids) <= 2, f"leads={lead_ids}"))
        A.append(("some_reply_present", len(csends) >= 1, f"customer_sends={len(csends)}"))
    elif turn_no == 3:
        # PR-D: mix-and-match must go through the DETERMINISTIC recompose tool.
        rsends = recompose_sends(sends)
        rbody = rsends[-1]["message"] if rsends else ""
        audit_json = json.dumps(res["audit_rows"])
        A.append(("recompose_via_deterministic_tool",
                  "catering_recomposed_menu_sent" in audit_json,
                  f"recompose_sends={len(rsends)}"))
        bad = [i for i in _menu_items_in(rbody) if i not in menu_names]
        A.append(("catalog_exact", rbody != "" and not bad, f"off_menu={bad}"))
        A.append(("price_free_outbound", no_price_re.search(allc) is None,
                  "clean" if no_price_re.search(allc) is None else "PRICE"))
        A.append(("not_invalid_selection_dead_end", "invalid_selection" not in audit_json,
                  "no select dead-end"))
        # TIGHTENED (reviewer condition 3): the delivered menu must contain EXACTLY
        # the requested sections — "option 1 starters with option 2 mains" => the
        # rendered categories are precisely {Appetizer, Main}, no more, no fewer.
        cats = _proposal_categories(rbody)
        A.append(("recomposition_sections_exactly_requested", cats == {"Appetizer", "Main"},
                  f"categories={sorted(cats)} (want exactly Appetizer+Main)"))
    elif turn_no == "3b":
        # AMBIGUOUS PROBE (reviewer condition 3): "mix in option 3's desserts" when
        # only 2 options exist MUST fall through to ONE clarifying question — never
        # a best-guess merge, never a menu re-dump.
        audit_json = json.dumps(res["audit_rows"])
        A.append(("clarify_fallback_fired", "catering_recompose_clarify_sent" in audit_json,
                  f"audit={[r.get('type') for r in res['audit_rows']]}"))
        A.append(("no_best_guess_merge", "catering_recomposed_menu_sent" not in audit_json,
                  "no merge audit"))
        A.append(("no_menu_redump", len(proposal_sends(sends)) == 0
                  and "catering_recomposed_menu_sent" not in audit_json,
                  f"proposal_sends={len(proposal_sends(sends))}"))
        A.append(("clarify_is_a_question", len(csends) >= 1, f"customer_sends={len(csends)}"))
        A.append(("price_free_no_leak", no_price_re.search(allc) is None and not LEAK_RE.search(allc),
                  "clean" if no_price_re.search(allc) is None else "PRICE/LEAK"))
    elif turn_no == 4:
        hit = PRICE_DIGIT_RE.search(allc)
        A.append(("no_invented_price_LAUNCH_BLOCKER", hit is None, f"OFFENDING={hit.group(0)!r}" if hit else "no price"))
        A.append(("no_menu_redump_answering_price", len(psends) == 0, f"proposal_sends={len(psends)}"))
        deferral = bool(prose) and re.search(r"\b(quote|pricing|price|confirm|owner)\b", prose, re.I) is not None
        A.append(("deferral_line_present", deferral, f"prose={prose[:120]!r}"))
        A.append(("no_stall_no_leak", not STALL_RE.search(prose) and not LEAK_RE.search(prose), "clean" if not STALL_RE.search(prose) and not LEAK_RE.search(prose) else "STALL/LEAK"))
    elif turn_no == 5:
        amend = json.loads(AMEND_PATH.read_text(encoding="utf-8"))["records"]
        A.append(("R2A_capture_f7_primary_followup_suppressed", "f7_primary_followup_suppressed" in rslist, f"reasons={rslist}"))
        A.append(("amendment_recorded_135", any("135" in r.get("raw_text", "") for r in amend), f"records={[r.get('raw_text') for r in amend]}"))
        A.append(("no_new_lead", len(lead_ids) <= 3, f"leads={lead_ids}"))
        A.append(("no_proposal_escape", "f7_proposal_request_escaped_to_dispatcher" not in rslist, f"reasons={rslist}"))
    elif turn_no == 6:
        pstore = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
        allitems = [n for s in pstore["sets"] for o in s["options"] for n in o["item_names"]]
        A.append(("no_invented_lobster_in_proposals", not any("lobster" in i.lower() for i in allitems), "clean"))
        refusal = bool(prose) and "lobster" in prose.lower() and re.search(r"\b(don'?t|do not|not|isn'?t|unfortunately|currently offer|can'?t|unable)\b", prose, re.I) is not None
        A.append(("off_menu_refusal_named", refusal, f"prose={prose[:160]!r}"))
        alts = bool(prose) and (any(mn.lower() in prose.lower() for mn in menu_names) or "owner" in prose.lower())
        A.append(("alternatives_or_escalation", alts, "alternatives/owner mentioned" if alts else "MISSING"))
        A.append(("no_menu_redump_as_answer", len(psends) == 0, f"proposal_sends={len(psends)}"))
        A.append(("no_stall_no_leak", not STALL_RE.search(prose) and not LEAK_RE.search(prose), "clean" if not STALL_RE.search(prose) and not LEAK_RE.search(prose) else "STALL/LEAK"))
    elif turn_no == 7:
        A.append(("lead_number_three_opened", len(lead_ids) >= 3, f"leads={lead_ids}"))
        A.append(("discriminator_fresh_over_stale", "f7_fresh_inquiry_new_lead_over_stale" in rslist, f"reasons={rslist}"))
        xref = [s for s in csends if "earlier inquiry" in s["message"]]
        A.append(("cross_reference_present", len(xref) >= 1, "found" if xref else "MISSING"))
    elif turn_no == 8:
        A.append(("zero_unprompted_outbound", len(sends) == 0, f"sends={len(sends)}"))
    return A


def tone_judge(res):
    """Reviewer tone rule, scoped to the FREE-FLOW brain's composed prose (PR-D's
    target). Deterministic script sends (the PR-A cross-reference ack, the canonical
    R2A 'with the owner' reply, the create-lead ack) are pre-existing shipped copy
    the task said to keep as-is, so they are NOT judged here. Flags on brain prose:
    stalls-with-nothing-behind, internal-flow leakage, and (turn 4/6) menu re-dumps
    answering a non-proposal question."""
    problems = []
    sends = res["sends"]
    prose = " ".join(s["message"] for s in prose_sends(sends))
    if LEAK_RE.search(prose):
        problems.append(f"internal-leak in brain prose: {prose[:100]!r}")
    # A stall is the reviewer's failure mode only when NOTHING follows it. If a menu
    # tool (proposals or recompose merge/clarify) sent output the same turn, the
    # deliverable followed — clunky lead-in at worst, not a dead end.
    tool_output = proposal_sends(sends) or recompose_sends(sends)
    if STALL_RE.search(prose) and not tool_output:
        problems.append(f"dead-end stall (nothing followed): {prose[:100]!r}")
    t = res["turn"]
    if t in (4, 6) and proposal_sends(sends):
        problems.append(f"menu re-dump answering a non-proposal question (turn {t})")
    # Contradiction: the brain pre-announced a specific mix, then the deterministic
    # recompose CLARIFIED (couldn't do it) — the customer sees a promise then a
    # "which options?" question. The brain should stay neutral until the tool speaks.
    clarified = any(r.get("type") == "catering_recompose_clarify_sent" for r in res["audit_rows"])
    if clarified and re.search(r"\bI'?ll combine\b|\bcombining .+ with\b|\bI'?ll (?:mix|prepare) .+ (?:with|from)\b", prose, re.I):
        problems.append(f"contradiction: brain promised a mix then the tool clarified: {prose[:100]!r}")
    return ("pass" if not problems else "fail"), problems


def append_transcript(T, res, assertions, tone):
    T.append(f"\n## Turn {res['turn']}")
    T.append(f"\n**INBOUND** (`{res['message_id']}`):\n\n> {res['inbound']}\n")
    dr = res["dispatch_result"]
    T.append(f"**cf-router returned:** `{json.dumps(dr)}` ({'intercepted' if dr is not None else 'None -> free-flow brain'})\n")
    if res["hermes_events"]:
        T.append("**Free-flow brain steps:**\n")
        for e in res["hermes_events"]:
            T.append(f"- `{json.dumps(e)[:700]}`")
        T.append("")
    if res["sends"]:
        T.append("**Outbound sends (verbatim):**\n")
        for s in res["sends"]:
            tag = "CUSTOMER" if s["jid"].startswith("17329837841") else ("OWNER-CARD" if s["jid"] == OWNER_JID else "OTHER")
            T.append(f"<details><summary>[{tag}] via {s['via']} -> {s['jid']}</summary>\n\n```\n{s['message']}\n```\n</details>\n")
    else:
        T.append("**Outbound sends:** _(none)_\n")
    if res["audit_rows"]:
        T.append("**Audit rows (decisions.log delta):**\n")
        for r in res["audit_rows"]:
            keys = {k: r.get(k) for k in ("type", "reason", "lead_id", "from_status", "to_status", "proposal_set_id", "code") if k in r}
            T.append(f"- `{json.dumps(keys)[:300]}`")
        T.append("")
    T.append("**Assertions:**\n")
    for name, ok, note in assertions:
        T.append(f"- {'PASS' if ok else 'FAIL'} - `{name}` - {note}")
    T.append(f"\n**Tone verdict:** {tone[0].upper()}" + (f" - {tone[1]}" if tone[1] else ""))


TURNS = [
    (1, "Hello I have a wedding coming up for 120 guests on August 8th, out of 120 guests 90 are non-vegetarian and 30 vegetarian. Provide me two best sample menus of yours , so that I can decide."),
    (2, "For the dinner, we'd prefer a buffet service."),
    (3, "Can we do option 1 starters with the option 2 mains?"),
    (4, "What will this cost per plate?"),
    (5, "Actually make it 135 guests."),
    (6, "Can you add lobster to the menu?"),
    (7, "Also, separate event -- September 12th for 40 guests, need catering too."),
]


def run_session(session_idx, menu_names, no_price_re):
    """One full 8-turn session on a clean sandbox. Returns (per_turn_results, transcript_lines)."""
    global SENDS, HISTORY, CURRENT_TURN
    SENDS.clear()
    HISTORY.clear()
    build_sandbox()
    hooks, actions = load_plugin()
    wire(hooks, actions)
    T = []
    per_turn = []
    for n, text in TURNS:
        mid = f"wamid.PRD.S{session_idx}.T{n}.{int(datetime.now(timezone.utc).timestamp()*1000)%100000000}"
        try:
            res = drive_turn(hooks, actions, n, text, mid)
        except Exception as e:
            import traceback
            res = {"turn": n, "inbound": text, "message_id": mid, "dispatch_result": None,
                   "hermes_events": [{"step": "HARNESS_ERROR", "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[:1200]}],
                   "sends": [], "audit_rows": []}
        A = assess(n, res, menu_names, no_price_re)
        tone = tone_judge(res)
        append_transcript(T, res, A, tone)
        per_turn.append({"turn": n, "assertions": A, "tone": tone, "sends": res["sends"]})
        # Ambiguous mix-and-match probe immediately after turn 3's clean recompose.
        if n == 3:
            mid_b = f"wamid.PRD.S{session_idx}.T3b.{int(datetime.now(timezone.utc).timestamp()*1000)%100000000}"
            probe_text = "Can we mix in option 3's desserts?"
            try:
                res_b = drive_turn(hooks, actions, "3b", probe_text, mid_b)
            except Exception as e:
                import traceback
                res_b = {"turn": "3b", "inbound": probe_text, "message_id": mid_b, "dispatch_result": None,
                         "hermes_events": [{"step": "HARNESS_ERROR", "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[:1200]}],
                         "sends": [], "audit_rows": []}
            A_b = assess("3b", res_b, menu_names, no_price_re)
            tone_b = tone_judge(res_b)
            append_transcript(T, res_b, A_b, tone_b)
            per_turn.append({"turn": "3b", "assertions": A_b, "tone": tone_b, "sends": res_b["sends"]})
    # Turn 8 -- SILENCE + TTL sweep (flag OFF).
    CURRENT_TURN = 8
    os.environ.pop("CATERING_LEAD_TTL_SWEEP_ENABLED", None)
    ss = len(SENDS); ls = len(read_log_rows())
    rc, out, err = run_ttl_sweep()
    res8 = {"turn": 8, "inbound": "(SILENCE -- scheduled TTL sweep, flag OFF)", "message_id": "-",
            "dispatch_result": None, "hermes_events": [{"step": "ttl_sweep", "rc": rc, "stdout": out.strip(), "stderr": err.strip()[:300]}],
            "sends": SENDS[ss:], "audit_rows": read_log_rows()[ls:]}
    A8 = assess(8, res8, menu_names, no_price_re); tone8 = tone_judge(res8)
    append_transcript(T, res8, A8, tone8)
    per_turn.append({"turn": 8, "assertions": A8, "tone": tone8, "sends": res8["sends"]})
    return per_turn, T


TURN_ORDER = [1, 2, 3, "3b", 4, 5, 6, 7, 8]


def run_gate(sessions_n=3):
    """Run `sessions_n` independent full sessions. Returns (sessions, transcripts, stability, menu_names)."""
    menu = json.loads(MENU_FIXTURE.read_text(encoding="utf-8"))
    menu_names = {i["name"] for i in menu["items"]}
    cpo = load_script("e2e_cpo_ref", SCRIPTS / "create-catering-proposal-options")
    no_price_re = cpo.NO_PRICE_RE

    sessions, transcripts = [], []
    for si in range(1, sessions_n + 1):
        per_turn, T = run_session(si, menu_names, no_price_re)
        sessions.append(per_turn)
        transcripts.append(T)

    def turn_ok(pt):
        return all(ok for _, ok, _ in pt["assertions"]) and pt["tone"][0] == "pass"
    stability = {}
    for t in TURN_ORDER:
        stability[t] = [turn_ok(next(pt for pt in s if pt["turn"] == t)) for s in sessions]
    return sessions, transcripts, stability, menu_names


def main():
    ap = argparse.ArgumentParser(description="Catering conversation E2E gate (3 sessions).")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "artifacts"),
                    help="Directory for transcript + results artifacts.")
    ap.add_argument("--sessions", type=int, default=3)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sessions, transcripts, stability, _ = run_gate(args.sessions)

    header = (f"# E2E Catering conversation gate (free-flow, SKILL-prose + recompose assist)\n\n"
              f"Branch: feat/catering-prd-conversation-pass | Model: {MODEL} | LLM calls: {LLM_CALLS}\n"
              f"TLS to OpenRouter: {'DOWNGRADED (MITM CA box)' if TLS_FALLBACK_USED else 'verified'}\n")
    stab_tbl = ["\n---\n\n## Stability (all-assertions AND tone, per turn across sessions)\n",
                "| Turn | " + " | ".join(f"Run{i+1}" for i in range(len(sessions))) + " | Stable |",
                "|---|" + "---|" * len(sessions) + "---|"]
    for t in TURN_ORDER:
        row = stability[t]
        stab_tbl.append(f"| {t} | " + " | ".join("PASS" if x else "FAIL" for x in row)
                        + f" | {'YES' if all(row) else 'NO'} |")
    (out_dir / "e2e-transcript-prd.md").write_text(
        header + "\n".join(transcripts[0]) + "\n".join(stab_tbl) + "\n", encoding="utf-8", newline="\n")

    def ser(pt):
        return {"turn": pt["turn"], "tone": pt["tone"][0], "tone_problems": pt["tone"][1],
                "assertions": [{"name": n, "pass": bool(ok), "note": note} for n, ok, note in pt["assertions"]]}
    all_pass = all(all(stability[t]) for t in TURN_ORDER)
    results = {"branch": "feat/catering-prd-conversation-pass", "model": MODEL, "llm_calls": LLM_CALLS,
               "tls_fallback_used": TLS_FALLBACK_USED, "gate_all_pass": all_pass,
               "stability": {str(t): stability[t] for t in TURN_ORDER},
               "sessions": [[ser(pt) for pt in s] for s in sessions]}
    (out_dir / "e2e-results-prd.json").write_text(json.dumps(results, indent=2), encoding="utf-8", newline="\n")

    print(f"MODEL={MODEL} LLM_CALLS={LLM_CALLS} GATE_ALL_PASS={all_pass}")
    for t in TURN_ORDER:
        row = stability[t]
        print(f"Turn {t}: runs={['P' if x else 'F' for x in row]} stable={'YES' if all(row) else 'NO'}")
        if not all(row):
            for si, s in enumerate(sessions, 1):
                pt = next(p for p in s if p["turn"] == t)
                fails = [f"{n}:{note[:90]}" for n, ok, note in pt["assertions"] if not ok]
                if fails or pt["tone"][0] != "pass":
                    print(f"    run{si} tone={pt['tone'][0]} fails={fails} tone_problems={pt['tone'][1]}")
    print("WROTE", out_dir / "e2e-transcript-prd.md", "and", out_dir / "e2e-results-prd.json")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
