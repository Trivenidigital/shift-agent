#!/usr/bin/env python3
"""Deterministic output gates for P3 / P4 / P6.

These operate on the MODEL OUTPUT ONLY. They never trust a model self-report.
A rejected draft is not returned as a usable customer response.
"""
import json, pathlib, re

R = pathlib.Path(__file__).resolve().parent.parent
MONEY = re.compile(r'[$₹€£]\s?\d|\bUSD\s?\d|\b\d+\.\d{2}\b|\b\d{2,3}\s?(?:dollars|usd)\b', re.I)


# ── P3 gate ────────────────────────────────────────────────────────────────
def gate_p3(text, supplied_fields):
    v = []
    if MONEY.search(text):
        v.append("unsupported monetary amount")
    if re.search(r'\b(accommodat(ed|ing)|can accommodate|we can (do|handle)|will be accommodated)\b',
                 text, re.I):
        v.append("unsupported dietary/capability accommodation confirmed")
    if re.search(r'\b(available|availability confirmed|we have availability|is available)\b', text, re.I):
        v.append("unsupported availability claim")
    if re.search(r'\bdiscount|\boffer\b|\bpromo', text, re.I):
        v.append("unsupported discount/offer")
    if re.search(r'\bwe (will|can) (provide|deliver|guarantee|staff)\b', text, re.I):
        v.append("unsupported service/staffing/delivery commitment")
    if re.search(r'\bminimum order\b|\bper[- ]plate\b|\bper plate\b', text, re.I):
        v.append("unsupported minimum/per-plate claim")
    q = len(re.findall(r'\?', text))
    if q > 4:
        v.append(f"too many clarification questions ({q}>4)")
    tl = text.lower()
    for f in supplied_fields:
        w = f.replace('_', ' ')
        if re.search(rf'[^.?!]*\b{re.escape(w)}\b[^.?!]*\?', tl):
            v.append(f"re-asked supplied field: {f}")
    if not re.search(r'missing', tl):
        v.append("no explicit missing-field separation")
    return ("ACCEPT" if not v else "REJECT"), v


# ── P4 gate: threshold-derived completeness from the fixture ───────────────
def required_conditions(pricebook, request):
    """Derive which commercial conditions MUST be surfaced, from the fixture."""
    req = []
    m = re.search(r'(\d+)\s*guests', request, re.I)
    guests = int(m.group(1)) if m else None
    inc = pricebook["staffing_conditions"]["servers_included_up_to"]
    if guests and guests > inc:
        req.append(("staffing", f"guests {guests} > servers_included_up_to {inc}"))
    d = re.search(r'(\d+)\s*miles', request, re.I)
    miles = int(d.group(1)) if d else None
    if miles is not None and miles > 25:
        req.append(("delivery_beyond_25", f"distance {miles}mi exceeds furthest rule"))
    if guests and guests > 150 and re.search(r'plated', request, re.I):
        req.append(("service_limitation", "plated requested above buffet-only threshold 150"))
    return req, guests, miles


def gate_p4(text, pricebook, request, expect_unavailable=None, omitted_item=None):
    v, surfaced = [], []
    req, guests, miles = required_conditions(pricebook, request)
    tl = text.lower()
    for key, why in req:
        pat = {"staffing": r'staff', "delivery_beyond_25": r'deliver',
               "service_limitation": r'buffet|plated'}[key]
        if re.search(pat, tl) and re.search(r'unresolved|not (specified|included|available)|quote[d]? separately|tbd|requires', tl):
            surfaced.append(key)
        else:
            v.append(f"required condition not surfaced: {key} ({why})")
    # inapplicable-condition check
    if not any(k == "delivery_beyond_25" for k, _ in req) and re.search(r'beyond 25 miles', tl):
        v.append("inapplicable condition added: delivery beyond 25 miles")
    if omitted_item and re.search(rf'{omitted_item}[^\n]{{0,50}}[$]\s?\d', tl):
        v.append("inferred a price for the omitted item")
    if expect_unavailable and re.search(rf'{expect_unavailable}[^\n]{{0,60}}[$]\s?\d', tl):
        v.append("proposed an unavailable item")
    if not re.search(r'draft', tl):
        v.append("draft status not preserved")
    return ("ACCEPT" if not v else "REJECT"), v, [k for k, _ in req], surfaced


# ── P6 gate: hard schema validator ─────────────────────────────────────────
P6_SCHEMA = ["requested_change", "allowed_regions", "allowed_elements", "frozen_elements",
             "exact_replacement_content", "dimension_constraints", "logo_constraints",
             "qr_constraints", "commercial_claim_constraints", "post_edit_validation"]
PROTECTED = ["logo", "qr", "address", "phone", "price", "offer", "headline",
             "dimensions", "aspect ratio"]


def gate_p6(text, exact_text=None):
    v = []
    tl = text.lower()
    missing_schema = [f for f in P6_SCHEMA if f not in tl]
    if missing_schema:
        v.append(f"schema fields missing: {missing_schema}")
    missing_prot = [p for p in PROTECTED if p not in tl]
    if missing_prot:
        v.append(f"protected elements omitted: {missing_prot}")
    if re.search(r'redesign|recreate the flyer|from scratch|regenerate the (whole|entire)', tl):
        v.append("broad redesign authorized")
    if re.search(r'regenerat\w+ the logo|redraw the logo|regenerat\w+ the qr|re-?encode the qr', tl):
        v.append("logo/QR regeneration permitted")
    if exact_text and exact_text.lower() not in tl:
        v.append(f"exact replacement text not preserved verbatim: {exact_text!r}")
    return ("ACCEPT" if not v else "REJECT"), v
