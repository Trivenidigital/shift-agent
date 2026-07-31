"""
Shift Agent — shared exit codes.

All scripts use these constants. Hermes surfaces non-zero exits to the LLM via stderr,
which the skill can read and branch on.
"""

# 0 — success
EXIT_OK = 0

# 1 — generic failure
EXIT_GENERIC_ERROR = 1

# 2 — agent disabled OR input/state file invalid (caller should not retry)
EXIT_DISABLED = 2
EXIT_INVALID_INPUT = 2

# 3 — rate limit / daily cap exceeded (caller can propose owner raise cap)
EXIT_CAP_EXCEEDED = 3

# 4 — resource not found (unknown proposal_id, unknown employee_id, etc.)
EXIT_NOT_FOUND = 4

# 5 — Pydantic validation failure (schema mismatch; indicates bad data on disk)
EXIT_SCHEMA_VIOLATION = 5

# 6 — external dependency unavailable (OpenRouter, WA bridge, Pushover)
EXIT_DEPENDENCY_DOWN = 6

# 7 — state file corrupt beyond recovery (caller should alert + escalate)
EXIT_STATE_CORRUPT = 7

# 8 — concurrency conflict (failed to acquire lock within timeout)
EXIT_LOCK_TIMEOUT = 8

# 9 — caller attempted an illegal state transition
EXIT_ILLEGAL_TRANSITION = 9

# 10 — environment problem (NFS detected, missing binary, etc.)
EXIT_ENVIRONMENT = 10

# 11 — PR-B v0.4: truth-guard rejected the LLM-drafted text on stdin
# (headcount integer or ISO event_date missing from the draft). Distinct from
# EXIT_DEPENDENCY_DOWN (=6, "bridge unreachable") so PR-D2's retry-state-machine
# does NOT re-attempt the bridge POST — the lead needs a re-DRAFT, which only
# the SKILL can do. Caller must NOT auto-retry; the lead stays at
# AWAITING_OWNER_APPROVAL pending a fresh owner-reply.
EXIT_TRUTH_GUARD_FAILED = 11

# 12 — privilege-denied: the --sender-role passed by the dispatcher does not
# match the role required for this state-mutating operation. Owner-only
# scripts (apply-menu-update, apply-catering-owner-decision) reject with this
# code when called by an employee/customer/unknown sender. Defense-in-depth
# against a screenshot-forwarded `#XXXXX` code that the dispatcher's
# role-gate failed to catch upstream.
EXIT_PRIVILEGE_DENIED = 12

# 13 — census C4: finalize-catering-menu --auto-default refused because the
# lead already has an active proposal set in SENT status (an option-picker flow
# the customer should choose from via select-catering-proposal). The crude
# first-5-priced-items default basket must NOT silently override a sent
# proposal. Distinct from EXIT_INVALID_INPUT (=2) so the finalize SKILL directs
# the customer to pick an option instead of retrying the finalize. Operator
# escape hatch: pass --force-default to build the default basket anyway.
EXIT_PROPOSAL_ACTIVE = 13

# 14 — M4/G4: the lead is ON HOLD, so a lead-scoped automated CUSTOMER send was
# refused (send-catering-ack, apply-catering-owner-decision approve). Distinct
# from EXIT_DEPENDENCY_DOWN (=6, "bridge unreachable") so the PR-D2 retry
# state-machine does NOT re-attempt the bridge POST — a hold is an operator
# decision, cleared only by `set-catering-lead-hold --off`, never by a retry.
# Distinct from EXIT_ILLEGAL_TRANSITION (=9) because the lead's status is
# perfectly legal; only the send is withheld.
EXIT_LEAD_ON_HOLD = 14

# 15 — M3: apply-catering-owner-decision --discount-id named a discount the
# CURRENT pricebook does not approve (unknown id) or no longer publishes
# (active=false). Refused BEFORE any state change, so the lead is untouched.
# Distinct from EXIT_INVALID_INPUT (=2) because the operator's next step is
# specific and different: re-import the pricebook, or use one of the ids it
# actually approves — not "fix your flags". Distinct from EXIT_NOT_FOUND (=4),
# which in this script already means "no lead carries that approval code", so
# reusing it would make the two failures indistinguishable in the exit status.
EXIT_UNKNOWN_DISCOUNT = 15

# 16 — apply-catering-owner-decision --discount-id was asked to recompute a lead
# that carries no CateringPricingInputs (it was finalized before pricing
# provenance existed, or on the legacy no-pricebook path). Rebuilding the quote
# from whole-dollar `selected_items` would quote a package lead's add-ons alone
# and re-derive cents that were never there, so the recompute is REFUSED before
# any state change. Distinct from EXIT_UNKNOWN_DISCOUNT (=15): the discount id is
# fine, the LEAD is the problem, and the fix is to re-finalize it — not to pick a
# different discount.
EXIT_PRICING_PROVENANCE_MISSING = 16

# 17 — the price the customer would have received is not deliverable: its
# price_status is "pending_owner_review" (placeholder pricebook, an unresolvable
# item price, or a per-unit fee whose multiplier was never supplied). The kernel
# has said the number is not a final quote, so the send is refused rather than
# labelled and sent anyway. Distinct from EXIT_TRUTH_GUARD_FAILED (=11), which is
# about the DRAFTED TEXT; this is about the NUMBER.
EXIT_PRICE_PENDING_OWNER_REVIEW = 17

# 18 — finalize-catering-menu was asked to price a per-person package for more
# guests than CateringSelectedItem.qty can hold (MAX_LINE_QTY = 500). The
# materialised package line would silently clamp to 500 while the kernel priced
# the full headcount, so the itemization and the total would disagree. Refused up
# front. Distinct from EXIT_TRUTH_GUARD_FAILED (=11), which the clamp used to
# masquerade as once the divergence grew past tolerance — that read as LLM
# hallucination and sent the operator hunting in the wrong place.
EXIT_GUEST_COUNT_UNSUPPORTED = 18
