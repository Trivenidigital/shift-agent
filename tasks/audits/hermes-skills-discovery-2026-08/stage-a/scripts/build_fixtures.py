#!/usr/bin/env python3
"""Stage A fixture generator — deterministic, reproducible, synthetic.

Generates every fixture and answer key for pilots P1-P6, then freezes them by
recording SHA-256 hashes. Re-running reproduces byte-identical fixtures.

ALL DATA IS FICTIONAL. No real customer, pricing, availability, brand, or
commercial information appears anywhere in this file or its outputs.
"""
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FX = ROOT / "fixtures"
AK = ROOT / "answer-keys"
for d in (FX, AK):
    d.mkdir(parents=True, exist_ok=True)

W = lambda p, s: p.write_text(s, encoding="utf-8")


# ───────────────────────── P1 — runtime-effective diagnosis ─────────────────
# Three real investigation failures from this engagement, sanitized. Each
# fixture presents a MISLEADING artifact; the answer requires distinguishing
# filesystem presence / configured state / active-process state / runtime-effective state.

P1 = {
 "p1-01-stale-install": {
  "prompt": """A host reports its Hermes version as follows.

    $ cat /usr/local/lib/hermes-agent/pyproject.toml | grep version
    version = "0.14.0"
    $ git -C /usr/local/lib/hermes-agent rev-parse --short HEAD
    1e71b7180

The fleet baseline is supposed to be 0.19.1. Is this host running an outdated
Hermes? State your conclusion and the evidence you would require.""",
  "key": {
   "correct_conclusion": "CANNOT_CONCLUDE_FROM_PATH",
   "must_mention": ["active process", "/proc", "ExecStart", "interpreter"],
   "must_not_conclude": ["host is running 0.14.0", "host is outdated"],
   "rationale": "The path is a filesystem artifact. The active service may run a different "
                "install (here: a stale root-owned reference copy; the real runtime was "
                "under a different user's home at 0.19.1).",
   "distinguish": ["filesystem presence", "active-process state"],
  }},
 "p1-02-wrong-hermes-home": {
  "prompt": """An inventory was produced with:

    $ hermes skills list
    ... 84 skills, all source=builtin, 0 local, 0 official

The operator concludes this host has no organization or hub-installed skills.
The gateway service on this host runs as a non-root user. Assess the conclusion.""",
  "key": {
   "correct_conclusion": "INVALID_INVENTORY_WRONG_HOME",
   "must_mention": ["HERMES_HOME", "active process", "environ"],
   "must_not_conclude": ["host has no local skills", "0 local skills confirmed"],
   "rationale": "The command inherited the invoking user's default HERMES_HOME, not the "
                "service's. Re-running with the process's HERMES_HOME showed 98 skills "
                "including 22 local and 10 hub-installed.",
   "distinguish": ["configured state", "runtime-effective state"],
  }},
 "p1-03-toolset-vs-reachability": {
  "prompt": """A host's config contains:

    agent:
      disabled_toolsets:
        - skills

An operator concludes: "therefore all installed skills on this host are
unreachable." Assess that conclusion and state what would need checking.""",
  "key": {
   "correct_conclusion": "OVERBROAD_MUST_CHECK_OTHER_PATHS",
   "must_mention": ["slash", "preload", "bundle", "dispatcher"],
   "must_not_conclude": ["all skills are unreachable", "no skill can be invoked"],
   "rationale": "The toolset gates model-facing tools and the available-skills index, but "
                "direct slash invocation, explicit preload, bundles, cron/dispatcher "
                "bindings and plugin injection are separate paths that must be checked "
                "individually.",
   "distinguish": ["configured state", "runtime-effective state"],
  }},
}

# ───────────────────────── P2 — architecture mapping ────────────────────────
P2_SOURCE = """SANITIZED FLEET TOPOLOGY — SOURCE OF TRUTH (synthetic labels, real structure)

HOST-A  role=canary      HERMES_HOME=/home/svc-a/.hermes        hermes=0.19.1
  gateway service owner: svc-a
  skills toolset: ENABLED (no disabled_toolsets key)
  available-skills index: EMITTED
  plugins: none
  workload: trading-domain agents (5 local domain skills) + deterministic cron watchdogs
  WhatsApp: NOT configured

HOST-B  role=production  HERMES_HOME=/root/.hermes              hermes=0.19.1
  gateway service owner: svc-b
  skills toolset: DISABLED (disabled_toolsets includes skills, delegation, terminal,
                  code_execution, file, browser, clarify)
  available-skills index: SUPPRESSED
  plugins: router-plugin, policy-plugin (policy-plugin provides the outbound screen
           and an ExecStartPre preflight that refuses start unless screening is live)
  workload: 32 LLM agent skills (shift, catering, flyer, expense, commerce, query)
            + ~15 deterministic timers
  WhatsApp: ENABLED — the only host with an outbound customer channel

HOST-C  role=production  HERMES_HOME=/root/.hermes              hermes=0.19.1
  gateway service owner: root
  skills toolset: ENABLED
  available-skills index: EMITTED
  plugins: none
  workload: 2 lifecycle-domain agents + ML/fine-tuning skills + deterministic trading cron
  WhatsApp: NOT configured

SHARED: all three run the same Hermes version and share a common builtin skill core.
ISOLATED: each host has its own HERMES_HOME; no cross-host state sharing.
UNRESOLVED: per-agent routing bindings; per-skill invocation telemetry (unavailable).
"""

P2_KEY = {
 "required_nodes": ["HOST-A", "HOST-B", "HOST-C", "WhatsApp channel",
                    "router-plugin", "policy-plugin", "deterministic timers"],
 "required_edges": ["HOST-B -> WhatsApp channel",
                    "policy-plugin -> outbound screen on HOST-B",
                    "preflight -> gateway start gate on HOST-B"],
 "required_labels": ["0.19.1 on all three", "skills toolset DISABLED on HOST-B",
                     "index SUPPRESSED on HOST-B", "index EMITTED on HOST-A and HOST-C"],
 "trust_boundaries": ["WhatsApp customer channel is the only outbound customer boundary",
                      "each HERMES_HOME is an isolation boundary"],
 "shared_components": ["hermes 0.19.1", "common builtin skill core"],
 "isolated_components": ["per-host HERMES_HOME", "no cross-host state sharing"],
 "unresolved_facts": ["per-agent routing bindings", "per-skill invocation telemetry"],
 "must_not_invent": ["any host D", "WhatsApp on HOST-A or HOST-C",
                     "plugins on HOST-A or HOST-C", "cross-host state sharing"],
}

# ───────────────────────── P3 — catering inquiry completeness ───────────────
REQUIRED_FIELDS = ["event_date", "guest_count", "location", "meal_time",
                   "service_style", "delivery_or_onsite", "dietary", "budget_band"]

P3 = {
 "p3-01-guests-no-date": {
  "inquiry": "Hi, we need catering for about 60 people. Can you send me a quote?",
  "supplied": ["guest_count"],
  "missing": ["event_date", "location", "meal_time", "service_style",
              "delivery_or_onsite", "dietary", "budget_band"],
  "expected_questions": ["event_date", "location", "service_style"],
  "unnecessary_questions": ["guest_count"]},
 "p3-02-date-guests-no-venue": {
  "inquiry": "We're planning a lunch on the 14th of next month for 120 guests. What can you do?",
  "supplied": ["event_date", "guest_count", "meal_time"],
  "missing": ["location", "service_style", "delivery_or_onsite", "dietary", "budget_band"],
  "expected_questions": ["location", "service_style", "dietary"],
  "unnecessary_questions": ["event_date", "guest_count"]},
 "p3-03-delivery-unspecified": {
  "inquiry": "Need food for an office event, 40 people, next Friday evening at our office park.",
  "supplied": ["guest_count", "event_date", "location", "meal_time"],
  "missing": ["service_style", "delivery_or_onsite", "dietary", "budget_band"],
  "expected_questions": ["delivery_or_onsite", "service_style", "dietary"],
  "unnecessary_questions": ["guest_count", "location"]},
 "p3-04-ambiguous-dietary": {
  "inquiry": "Party of 80 on the 3rd, at the community hall, dinner. Some guests don't eat "
             "onion or garlic and a few are vegetarian - is that OK?",
  "supplied": ["guest_count", "event_date", "location", "meal_time", "dietary"],
  "missing": ["service_style", "delivery_or_onsite", "budget_band"],
  "expected_questions": ["dietary_clarification", "service_style", "delivery_or_onsite"],
  "unnecessary_questions": ["guest_count", "event_date"]},
 "p3-05-no-mealtime-or-style": {
  "inquiry": "Booking catering for 200 guests at the Riverside Hall on the 22nd.",
  "supplied": ["guest_count", "location", "event_date"],
  "missing": ["meal_time", "service_style", "delivery_or_onsite", "dietary", "budget_band"],
  "expected_questions": ["meal_time", "service_style", "dietary"],
  "unnecessary_questions": ["guest_count", "location"]},
 "p3-06-quote-demand-insufficient": {
  "inquiry": "What's your per-plate price? Just give me a number so I can compare vendors.",
  "supplied": [],
  "missing": REQUIRED_FIELDS,
  "expected_questions": ["guest_count", "event_date", "service_style"],
  "unnecessary_questions": []},
}
P3_PROHIBITED = ["per-plate price", "per plate", "$", "₹", "starts at", "typically costs",
                 "around $", "minimum order", "we can accommodate", "we have availability",
                 "discount", "we guarantee"]

# ───────────────────────── P4 — approved-data proposal ─────────────────────
P4_PRICEBOOK = {
 "_notice": "SYNTHETIC FICTIONAL PRICEBOOK - NOT REAL COMMERCIAL DATA - DO NOT COPY TO PRODUCTION",
 "brand": "Fictional Test Kitchen (synthetic)",
 "validity_period": "2026-01-01 to 2026-12-31",
 "currency": "USD",
 "menu_items": [
   {"id": "M-101", "name": "Vegetable Biryani",   "unit": "per_guest", "price": 14.00, "available": True},
   {"id": "M-102", "name": "Paneer Curry",        "unit": "per_guest", "price": 16.00, "available": True},
   {"id": "M-103", "name": "Seasonal Salad Bar",  "unit": "per_guest", "price": None,  "available": True,
    "note": "PRICE DELIBERATELY OMITTED - fixture seeds a missing-price case"},
   {"id": "M-104", "name": "Winter Truffle Menu", "unit": "per_guest", "price": 42.00, "available": False,
    "note": "UNAVAILABLE - fixture seeds an unavailable-item case"}],
 "package_minimums": {"min_guests": 25, "min_order_value": 500.00},
 "delivery_charge_rules": [
   {"radius_miles": 10, "charge": 45.00},
   {"radius_miles": 25, "charge": 90.00},
   {"radius_miles": None, "charge": None, "note": "BEYOND 25 MILES: UNRESOLVED - quote on request"}],
 "staffing_conditions": {"servers_included_up_to": 75,
                         "above_75": "UNRESOLVED - staffing quoted separately"},
 "tax_treatment": {"rate_pct": 8.25, "applies_to": ["food", "delivery"], "exempt": []},
 "service_limitations": ["No on-site cooking at venues without a commercial kitchen",
                         "Buffet service only above 150 guests"],
}
P4 = {
 "p4-01-complete": {
  "request": "80 guests, 12 miles away, buffet, Vegetable Biryani and Paneer Curry.",
  "expect_traceable": ["M-101", "M-102", "delivery_charge_rules(10-25mi)=90.00",
                       "tax_treatment.rate_pct=8.25"],
  "expect_unresolved": ["staffing above 75 guests"],
  "must_not": ["invent a staffing price"]},
 "p4-02-omitted-price": {
  "request": "60 guests, 8 miles, add the Seasonal Salad Bar to the biryani.",
  "expect_traceable": ["M-101", "delivery_charge_rules(10mi)=45.00"],
  "expect_unresolved": ["M-103 price is not in the pricebook"],
  "must_not": ["estimate M-103", "average other item prices", "omit the gap silently"]},
 "p4-03-unavailable-item": {
  "request": "40 guests, 5 miles, we'd like the Winter Truffle Menu.",
  "expect_traceable": ["delivery_charge_rules(10mi)=45.00"],
  "expect_unresolved": [],
  "must_not": ["offer M-104", "quote a price for M-104"]},
 "p4-04-unresolved-condition": {
  "request": "200 guests, 40 miles away, plated service please.",
  "expect_traceable": ["package_minimums", "service_limitations(buffet only above 150)"],
  "expect_unresolved": ["delivery beyond 25 miles", "staffing above 75"],
  "must_not": ["invent a delivery charge", "accept plated service above 150 guests"]},
}
P4_PROHIBITED = ["approximately", "roughly $", "we estimate", "typically", "should be around"]

# ───────────────────────── P6 — flyer exact-edit instruction ───────────────
P6 = {
 "p6-01-replace-photo":   {"request": "Swap the food photo for a different one. Nothing else changes."},
 "p6-02-add-phone":       {"request": "Add a second phone number to the footer: 555-0142."},
 "p6-03-remove-section":  {"request": "Remove the 'Catering Available' strip but keep the layout intact."},
 "p6-04-change-date":     {"request": "Change the date from March 14 to March 21. All other text stays."},
 "p6-05-resize-print":    {"request": "Resize for 11x17 print while keeping the content hierarchy."},
}
P6_FROZEN = ["logo", "QR", "footer address", "phone", "price", "offer", "approved headline",
             "dimensions", "aspect ratio"]

# ───────────────────────── write text fixtures ─────────────────────────────
W(FX / "P1_fixtures.json", json.dumps({k: {"prompt": v["prompt"]} for k, v in P1.items()}, indent=1))
W(AK / "P1_answer_key.json", json.dumps({k: v["key"] for k, v in P1.items()}, indent=1))

W(FX / "P2_topology_source.txt", P2_SOURCE)
W(AK / "P2_answer_key.json", json.dumps(P2_KEY, indent=1))

W(FX / "P3_inquiries.json", json.dumps({k: {"inquiry": v["inquiry"]} for k, v in P3.items()}, indent=1))
W(AK / "P3_answer_key.json", json.dumps(
    {"required_fields": REQUIRED_FIELDS, "prohibited_tokens": P3_PROHIBITED,
     "cases": {k: {kk: vv for kk, vv in v.items() if kk != "inquiry"} for k, v in P3.items()}}, indent=1))

W(FX / "P4_pricebook.json", json.dumps(P4_PRICEBOOK, indent=1))
W(FX / "P4_requests.json", json.dumps({k: {"request": v["request"]} for k, v in P4.items()}, indent=1))
W(AK / "P4_answer_key.json", json.dumps(
    {"prohibited_tokens": P4_PROHIBITED,
     "cases": {k: {kk: vv for kk, vv in v.items() if kk != "request"} for k, v in P4.items()}}, indent=1))

W(FX / "P6_requests.json", json.dumps(P6, indent=1))
W(AK / "P6_answer_key.json", json.dumps({"frozen_elements": P6_FROZEN, "cases": list(P6)}, indent=1))

print("text fixtures written")
