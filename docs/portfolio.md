# SMB-Agents Portfolio — One-Pager Spec for 20 Agents

**Project:** SMB-Agents (autonomous AI agents for ethnic SMBs — restaurants, groceries, food courts, catering)
**Reference customer profile:** South Asian diaspora businesses (e.g., Triveni Supermarket — 9 locations across TX/MD/NC/SC/OH/VA)
**Architecture:** One Hetzner VPS per customer (~$7/month) + central operator VPS for fleet management
**Stack:** Hermes Agent (skills, gateway, subagent delegation) + per-customer JSON/SQLite data layer + WhatsApp/Telegram messaging
**Build commitment:** All 20 agents in build-now portfolio (per founder's directive after explicit pushback)

---

## Consolidated Portfolio — Solid 17 (2026-04-29)

After a session-2026-04-29 review of 19 candidate additions to this portfolio, the spec was consolidated to **17 active agents + 5 backlog**. Founder's earlier counter-recommendation (12–14 well-built agents) effectively landed.

**What changed:**
- **Reframed in place:** Agent #11 Festival & Event Outreach → **Festival & Peak Prep** (ops-side: staffing/inventory/menu signals 3–5 days out, not customer marketing).
- **Retired (replaced):** Agent #17 Unit Economics → replaced by new **Agent #22 P&L Anomaly Detective** (light: flag anomalies from POS + cost data, no recipe modeling).
- **Retired (folded):** Agent #18 Customer Complaint → folded into Agent #9 (VIP) + Agent #4 (Daily Brief). Agent #20 Owner Wellbeing → folded into Agent #4 (Daily Brief) + platform quiet-hours rule.
- **New top-level agents:** Agent #21 **Expense Bookkeeper** (priority build — phone-receipt → categorize → QuickBooks); Agent #22 **P&L Anomaly Detective** (Tier-2 stub).
- **Sub-skill expansions** added inline within Agents #1, #2, #3, #4, #6, #7, #12, #13. Search for `[2026-04-29 add]` in this doc.
- **Backlog (build only on customer demand):** Agents #8 Receiving & QA, #19 Equipment & Maintenance, plus 3 new entries (#23 Order Status & Pickup, #24 Upsell & Menu Personalizer, #25 Third-Party Delivery Coordinator) at the end of this doc.

**See:** `tasks/solid17-consolidation-plan.md` for execution checklist; portal at `http://46.62.206.192:8080/portal/` reflects this consolidation as of 2026-04-29.

The per-agent specs below are preserved as-is from the original v1 spec, with inline reframing/sub-skill notes added. Sections marked **RETIRED** or **REFRAMED** explain how they map to the consolidated structure.

---

## How to read this document

Each agent has the same structure:

- **Purpose** — what it does and why an SMB owner pays for it
- **Primary skills** — Hermes skills that compose the agent (3–10 per agent typically)
- **Data dependencies** — fields in the per-customer data layer it reads/writes
- **Owner approval gates** — actions requiring explicit owner approval vs auto
- **Integration points** — other agents it talks to, external APIs, POS systems
- **Key risks** — the failure modes that would lose a customer
- **Build complexity** — Low (1–2 weeks) / Medium (3–4 weeks) / High (1–3 months)

This document is a *planning surface*, not a build commitment. Specs at this stage are 60–70% accurate at best; expect significant revision once a real design partner is using each agent against actual messages.

---

## OPERATIONS DOMAIN (Agents 1–3)

---

### Agent 1 — Shift Agent

**Purpose:** Handle sick-call and absence reports from employees; route coverage; reduce owner cognitive load to a single approve/reject decision per absence.

**Primary skills:**
- `handle_sick_call` — parse incoming message, extract who/when/reason
- `roster_lookup` — find scheduled employees and coverage candidates
- `find_coverage` — apply role-match + language-match + availability logic
- `notify_owner` — draft proposal, request approval
- `notify_employee_coverage` — after owner approval, send coverage request to chosen replacement
- `log_decision` — append structured JSON to decisions log
- `escalate_unfilled` — if no coverage candidate available or all decline, page owner directly
- `predict_no_show` — *[2026-04-29 add]* Phase 2 skill: predict no-shows from past patterns + religious holidays + exam weeks; surface flag in morning brief

**Data dependencies:**
- Reads: `employees`, `schedule`, `locations`, `owner_preferences` (escalation rules)
- Writes: `decisions_log`, `coverage_history`

**Owner approval gates:**
- All outbound messages to non-absent employees require approval (Phase 0–1)
- Phase 2: auto-approve when top candidate has >X confidence score and language matches; owner reviews exceptions

**Integration points:**
- Talks to: Daily Brief Agent (feeds yesterday's sick-call summary)
- External: WhatsApp/Telegram gateway

**Key risks:**
- Hallucinated employee names → guarded by "never invent" skill rule + roster ground truth
- Sender impersonation → addressed by explicit identity confirmation, never silent phone-match
- Voice-note-only messages → Phase 0 acknowledges and asks for text; voice transcription is Phase 2

**Build complexity:** Medium. In progress (Phase 0). First agent shipped → use as template for subsequent.

---

### Agent 2 — Catering Lead Agent

**Purpose:** Capture inbound catering inquiries from any channel (WhatsApp, phone voicemail, web form, walk-in note), structure them, draft quotes, track lead status from inquiry → quote → booking → fulfillment → payment. Highest revenue impact agent — catering is 30–40% margin vs 18–22% on grocery, and current inquiry loss rate is severe.

**Primary skills:**
- `parse_catering_inquiry` — extract event date, headcount, dietary restrictions, budget hints, contact info
- `check_capacity` — verify location/date availability against existing bookings
- `draft_quote` — apply pricing logic from menu + catering markup + headcount tiers
- `lookup_returning_customer` — match inquirer against past customers, surface preferences
- `generate_followup` — schedule check-ins for unanswered quotes
- `track_lead_status` — state machine: inquiry → quote_sent → confirmed → fulfilled → paid → followup
- `escalate_high_value` — flag inquiries above threshold for owner direct response
- `send_deposit_link` — *[2026-04-29 add]* Phase 2 skill: generate Zelle/Cash App/Stripe deposit link based on `deposit_threshold_guests` and `deposit_pct`; gate behind owner approval; reconcile via Agent #15 Cash & AR

**Data dependencies:**
- Reads: `menu`, `catering_pricing`, `bookings`, `customers`, `dietary_taxonomy`
- Writes: `leads`, `quotes`, `lead_status_log`

**Owner approval gates:**
- All quotes require approval before sending (Phase 0–1)
- Phase 2: quotes under $X auto-send if returning customer with >Y past orders

**Integration points:**
- Talks to: Catering Follow-up Agent (post-event), VIP Customer Agent (returning customer detection), Cash & AR Agent (payment tracking)
- External: WhatsApp, web form (if customer has one), email

**Key risks:**
- Misread headcount or date → real revenue loss; mitigate with explicit confirmation
- Pricing errors → owner approval gate is the safety net
- Dietary restriction errors (halal, vegetarian, jain, allergies) → high-stakes; strict structured taxonomy

**Build complexity:** High. Multi-channel intake + pricing logic + state machine + customer matching. ~6–8 weeks.

---

### Agent 3 — Multi-Location Coordinator

**Purpose:** Cross-location coordination for customers with 2+ locations. Coverage transfers between locations, location-by-location daily briefs, cross-location queries from owner ("who's at Houston tomorrow?"). Only relevant for multi-location customers — single-location customers skip this.

**Primary skills:**
- `cross_location_query` — answer "who's working at X" / "what's stock at Y"
- `propose_inter_location_transfer` — when one location is short-staffed, find spare from another
- `consolidate_briefs` — aggregate per-location daily briefs into master view
- `route_owner_query` — when owner asks ambiguous question, identify which location(s) it concerns
- `flag_anomaly_across_locations` — pattern detection across locations (all stores low on basmati = supplier issue)
- `propose_inter_location_transfer` — *[2026-04-29 add]* Phase 2 skill expansion: detect overstock at one location vs. understock at another, propose transfer with owner approval, write reciprocal entries to QuickBooks (replaces ad-hoc, unrecorded transfers between Triveni-style multi-location stores)

**Data dependencies:**
- Reads: All location-scoped data (rosters, schedules, inventory, sales)
- Writes: `cross_location_transfers`, `multi_location_alerts`

**Owner approval gates:**
- Inter-location employee transfers require approval (operationally significant)
- Cross-location alerts auto-route based on owner-defined rules

**Integration points:**
- Talks to: Shift Agent (inter-location coverage), Daily Brief Agent (consolidated view)
- External: WhatsApp/Telegram

**Key risks:**
- Stale data across locations → freshness checks before recommendations
- Privacy: Location A staff shouldn't see Location B roster details unless owner authorizes

**Build complexity:** Medium. Logic is clean; complexity is in data freshness across locations.

---

## OWNER-FACING SYNTHESIS (Agents 4–5)

---

### Agent 4 — Daily Brief Agent

**Purpose:** Every morning at owner-configured time (typically 7am), deliver a structured summary of yesterday's activity and today's outlook. This is the agent owners actually pay for — it's what reduces "I don't know what's happening in my own business" anxiety.

**Primary skills:**
- `aggregate_yesterday` — pull from all agent decision logs (Shift, Catering, Inventory, etc.)
- `forecast_today` — schedule today, expected catering pickups, low stock alerts, festival flags
- `flag_anomalies` — patterns that deviate from baseline (e.g., 3 sick calls vs typical 0–1)
- `format_brief` — render to channel-appropriate format (WhatsApp message, telegram, email)
- `personalize_tone` — adjust formality based on owner preference
- `forecast_demand` — *[2026-04-29 add]* Phase 2 skill: pull historical Clover/Square sales + local events → predict weekend peaks and festival surges; emit signals consumed by Agent #1 Shift (staffing) and Agent #6 Inventory (perishables)
- `weekly_owner_load_summary` — *[2026-04-29 add]* Phase 2 weekly section (not daily): how many approvals owner handled, which agent caught the most fires, suggested delegation moves. Absorbs the metrics surface formerly proposed for the retired Agent #20 Owner Wellbeing.

**Data dependencies:**
- Reads: All other agents' decision logs, `owner_preferences`, `festival_calendar`
- Writes: `briefs_sent`, `owner_engagement_log`

**Owner approval gates:**
- None — this is read-only synthesis, no actions taken

**Integration points:**
- Reads from: every other agent's logs
- External: WhatsApp/Telegram delivery

**Key risks:**
- Information overload → strict length cap (~150 words max), bullet-driven
- Hallucinated stats → all numbers must trace to source log; never compute without basis
- Wrong time delivery (waking owner, missing morning routine) → respect owner_preferences.brief_time

**Build complexity:** Low–Medium. Mostly orchestration of data already produced by other agents. Build value scales with how many other agents exist.

---

### Agent 5 — End-of-Day Reconciliation Agent

**Purpose:** At closing time per location, generate a closing summary: today's events resolved/unresolved, register vs sales-report reconciliation, exceptions for owner morning review.

**Primary skills:**
- `compile_day_events` — what happened today across all agents
- `reconcile_register` — compare physical register count to POS sales total, flag discrepancies
- `flag_unresolved` — sick calls without confirmed coverage, pending catering approvals, etc.
- `prepare_morning_handoff` — feed Daily Brief Agent with structured carry-over items

**Data dependencies:**
- Reads: All agent logs from today, POS daily totals (if integrated), register counts
- Writes: `eod_reports`, `reconciliation_discrepancies`

**Owner approval gates:**
- Reconciliation discrepancies above $X are flagged for owner review next morning, not auto-resolved

**Integration points:**
- Talks to: Daily Brief Agent (handoff), Cash & AR Agent (reconciliation data)
- External: POS API (Phase 2), photo capture for register count (Phase 3)

**Key risks:**
- POS integration variance — Clover, Square, Cash App, custom — every customer has different shape
- Register count requires manual input; agent can't physically count cash

**Build complexity:** Medium. Logic is straightforward; pain is POS integration.

---

## INVENTORY & SUPPLIER (Agents 6–8)

---

### Agent 6 — Inventory Tracker

**Purpose:** Track stock levels across SKUs, alert on low-stock and expiring perishables, ingest informal stock counts from staff voice notes and photos. Critical for ethnic groceries where perishables (fresh meat, dairy, vegetables, sweets) drive significant waste if mismanaged.

**Primary skills:**
- `parse_voice_stock_count` — voice note → structured stock update (Phase 2; Phase 1 = text only)
- `ingest_pos_inventory` — daily sales-driven decrement (requires POS integration)
- `flag_low_stock` — threshold-based alerts per SKU
- `flag_expiring_perishables` — date-based alerts for items nearing expiry
- `recommend_reorder` — suggest order quantities based on velocity and lead time
- `cross_check_physical` — periodic prompt for staff to physically verify stock vs system
- `suggest_use_today_recipe` — *[2026-04-29 add]* Phase 2 skill: when an item is near expiry, suggest "use today" recipes for food court / discount tags for sweets and dairy; reduces last-minute pull-and-toss cycle

**Data dependencies:**
- Reads: `sku_catalog`, `stock_levels`, `sales_velocity`, `supplier_lead_times`
- Writes: `stock_updates`, `low_stock_alerts`, `expiry_alerts`, `reorder_suggestions`

**Owner approval gates:**
- Reorder suggestions are recommendations only — owner places actual orders (Phase 1–2)
- Phase 3: auto-reorder for staple items below threshold with pre-approved suppliers

**Integration points:**
- Talks to: Supplier Coordination Agent (for reorders), Daily Brief Agent (low-stock summary)
- External: POS API for sales decrement; phone camera for photo intake (Phase 2)

**Key risks:**
- POS integration depth — different POS per customer = different integration cost
- SKU taxonomy explosion — ethnic groceries have thousands of SKUs, many with overlapping/synonymous names ("toor dal" vs "yellow split pigeon peas")
- Perishable accuracy — wrong expiry date → real food safety risk

**Build complexity:** High. Hard without POS integration. Plan for customer #3+ when integration cost is amortized.

---

### Agent 7 — Supplier Coordination Agent

**Purpose:** Maintain relationships with each supplier — preferred contact channel, response patterns, payment terms, dispute history. Automate order follow-ups, log disputes, surface supplier-level issues to owner.

**Primary skills:**
- `route_to_supplier` — pick correct contact and channel for given product
- `format_order` — translate internal order into supplier's expected format (PDF, WhatsApp, email, phone-readable)
- `follow_up_pending` — chase orders that are past expected delivery
- `log_dispute` — capture quality issues, short shipments, price discrepancies
- `summarize_supplier_relationship` — owner query "how's it going with X supplier"
- `detect_price_drift` — *[2026-04-29 add]* Phase 2 skill: track per-SKU price across suppliers over time; alert when drift exceeds threshold ("same ghee now $4 more"); suggest bulk deals or alternates. Replaces the per-SKU cost-change detection that would have lived in the retired Agent #17.

**Data dependencies:**
- Reads: `suppliers`, `purchase_orders`, `supplier_dispute_history`, `payment_terms`
- Writes: `supplier_communications`, `disputes`

**Owner approval gates:**
- All outbound supplier communications require owner review in Phase 0–1
- Phase 2: routine reorders auto-send for trusted suppliers; disputes always require approval

**Integration points:**
- Talks to: Inventory Tracker (reorder triggers), Cash & AR Agent (payment status)
- External: WhatsApp, email, phone (voice note generation Phase 3)

**Key risks:**
- Supplier relationships are personal — agent must not strain them with pushy auto-followups
- Tone calibration matters — "Where is my order?" vs "Just checking in on the order from Tuesday" differs by supplier and culture

**Build complexity:** Medium. Logic clean; nuance is in tone/relationship management.

---

### Agent 8 — Receiving & QA Agent

**Purpose:** When shipments arrive, structure receipt against purchase order, log discrepancies (short shipment, damaged goods, wrong item, expiry too short). Requires staff phone-camera input, which adds friction.

**Primary skills:**
- `parse_receipt_photo` — OCR + product matching against PO line items
- `compare_to_po` — flag discrepancies (quantity, price, condition)
- `log_quality_issue` — structured intake of damage/expiry concerns
- `notify_supplier_dispute` — feed Supplier Agent for follow-up
- `update_inventory` — push received quantities to Inventory Tracker

**Data dependencies:**
- Reads: `purchase_orders`, `sku_catalog`
- Writes: `receipts`, `quality_issues`, `supplier_dispute_triggers`

**Owner approval gates:**
- Quality issue escalations require owner approval before notifying supplier
- Inventory updates auto-apply once receipt is confirmed

**Integration points:**
- Talks to: Inventory Tracker, Supplier Coordination Agent
- External: Photo capture from staff phone; OCR service (cloud or local)

**Key risks:**
- OCR accuracy on packaging photos varies wildly
- Staff compliance — if it's annoying to use, staff won't, and agent has no data
- Adds 2–5 min to receiving process; needs to feel worth it

**Build complexity:** High. Photo processing + OCR + product matching is non-trivial. Tier 3 in honest assessment, included for portfolio completeness.

---

## CUSTOMER & MARKETING (Agents 9–11)

---

### Agent 9 — VIP Customer Agent

**Purpose:** Recognize high-value repeat customers, surface them to owner/staff at point of contact, drive personal-touch outreach (anniversary, birthday, festival). Plays into the 60–75% repeat-customer revenue base of ethnic SMBs.

**Primary skills:**
- `identify_vip` — match incoming contact (phone, name, email) against repeat-customer list
- `surface_preferences` — past orders, dietary, family details (carefully scoped)
- `prompt_personal_touch` — suggest staff/owner outreach moments (birthday, anniversary, festival)
- `track_loyalty_signals` — order frequency, recency, value
- `flag_at_risk` — VIP whose visits dropped off; suggest re-engagement

**Data dependencies:**
- Reads: `customers`, `orders_history`, `events_calendar` (birthdays, anniversaries opt-in)
- Writes: `vip_outreach_log`, `at_risk_flags`

**Owner approval gates:**
- All outbound VIP messages require approval — relationship-sensitive
- Phase 2: pre-approved templates for routine festival greetings auto-send

**Integration points:**
- Talks to: Catering Lead Agent (VIP returning customer), Festival Outreach Agent
- External: WhatsApp/SMS for personal outreach

**Key risks:**
- Privacy creep — knowing too much about customer personal details feels intrusive
- Spam-adjacent — too-frequent outreach erodes the relationship it's supposed to build
- Cultural fit — what feels warm in one community feels overfamiliar in another

**Build complexity:** Medium. Logic clean; nuance is in tone and frequency calibration.

---

### Agent 10 — Catering Follow-up Agent

**Purpose:** After a catering event completes, automated thank-you with personal touch, feedback request, repeat-booking nudge for next year/next festival. Lives next to Catering Lead Agent in the lifecycle.

**Primary skills:**
- `detect_event_completion` — trigger when catering booking moves to fulfilled state
- `draft_thank_you` — warm, personal, references event specifics (headcount, occasion)
- `request_feedback` — single-question survey, channel-appropriate
- `schedule_anniversary_nudge` — calendar reminder for next year's outreach
- `escalate_negative_feedback` — flag complaints to owner same day

**Data dependencies:**
- Reads: `bookings` (state=fulfilled), `customers`, `events_calendar`
- Writes: `followups`, `feedback_log`, `anniversary_calendar`

**Owner approval gates:**
- Initial thank-you auto-sends with template
- Anniversary nudges require owner approval (high personal-touch value if owner adds note)
- Negative feedback always escalates immediately, no auto-response

**Integration points:**
- Talks to: Catering Lead Agent (lifecycle state), VIP Customer Agent (lifetime value updates)
- External: WhatsApp/SMS

**Key risks:**
- Auto-sent feedback requests can feel cold for a personal industry
- Anniversary nudges 11 months later feel artificial without owner personalization

**Build complexity:** Low–Medium. Mostly state-machine triggered messaging.

---

### Agent 11 — Festival & Event Outreach Agent

> **REFRAMED 2026-04-29 → Festival & Peak Prep.** Same domain, narrower and stronger framing: ops-side prep (3–5 days before each festival, alert staffing/inventory/menu specials based on last year's actuals), NOT customer marketing campaigns. Owners pay faster for "don't run out of paneer during Diwali" than for outreach campaigns. The customer-marketing pitch below is preserved as v1 spec; the active build target is the ops-prep reframe. Tier promoted from 3 → 2 (active opt-in scaffold).

**Purpose:** Drive outreach campaigns timed to South Asian festivals (Diwali, Ugadi, Pongal, Onam, Eid, Holi, etc.) and family events. Reads from shared festival calendar; coordinates with VIP and Catering agents to surface relevant customers per festival.

**Primary skills:**
- `lookup_upcoming_festivals` — multi-tradition calendar (Hindu, Muslim, Christian, Sikh, Jain regional variants)
- `match_customers_to_festival` — language/region/past-order signals
- `draft_festival_message` — tradition-appropriate tone, multilingual options
- `coordinate_promotional_offers` — link to menu/catering pricing for festival specials
- `schedule_send` — distribute outreach across days to avoid spam-feel

**Data dependencies:**
- Reads: `festival_calendar`, `customers`, `customer_language_preferences`, `menu`
- Writes: `outreach_campaigns`, `campaign_engagement_log`

**Owner approval gates:**
- All campaigns require owner approval at draft stage
- Per-customer message personalization auto-fills from templates

**Integration points:**
- Talks to: VIP Customer Agent, Catering Lead Agent
- External: WhatsApp bulk messaging (BSP-backed for compliance)

**Key risks:**
- Festival list quality is the moat — get it wrong (mix up Pongal and Sankranti, miss regional variants) and you lose credibility instantly
- BSP messaging compliance — bulk outreach has stricter rules than 1:1
- Tone calibration — religious holidays vs cultural celebrations vs personal events have very different appropriate registers

**Build complexity:** Medium. Logic clean; the *content* (calendar, regional knowledge, tone library) is the real work and the real moat.

---

## WORKFORCE (Agents 12–14)

---

### Agent 12 — Hiring & Onboarding Agent

**Purpose:** When a new employee joins, walk them through paperwork (W-4, I-9, food handler certification), schedule training, create roster entry, set up communication channels. Sporadic but high-value-per-use.

**Primary skills:**
- `intake_new_hire` — collect basic info, generate I-9 / W-4 prompts
- `schedule_training` — book training sessions, assign to existing staff trainer
- `create_roster_entry` — populate `employees` data layer with new hire
- `track_compliance_paperwork` — chase missing forms, flag deadlines
- `pair_with_buddy` — assign existing employee as onboarding buddy
- `deliver_training_curriculum` — *[2026-04-29 add]* Phase 2 skill: bite-sized SOPs delivered by WhatsApp on a schedule (dosa station, register close, allergen handling). Reduces the 2–4 weeks of lost productivity from tribal-knowledge transfer.
- `quiz_via_whatsapp` — *[2026-04-29 add]* Phase 2 skill: short comprehension quizzes after each SOP module; track completion; flag knowledge gaps to owner

**Data dependencies:**
- Reads: `employees` (existing staff), `roles`, `training_modules`, `compliance_calendar`
- Writes: `new_hires`, `onboarding_status`, `training_assignments`

**Owner approval gates:**
- All compliance paperwork prompts go through owner before reaching employee
- Buddy assignment requires owner confirmation

**Integration points:**
- Talks to: Compliance Calendar Agent (form deadlines), Shift Agent (when employee becomes schedulable)
- External: e-signature service for forms (DocuSign/HelloSign or equivalent)

**Key risks:**
- Compliance paperwork errors are legally consequential — agent must never advise on legal matters, only facilitate forms
- Cultural fit in family businesses — owner often hires personal connections; agent must not feel like it's bureaucratizing relationships

**Build complexity:** Medium. Logic + form integration is real work but well-bounded.

---

### Agent 13 — Compliance Calendar Agent

**Purpose:** Track recurring compliance items: health department inspection cycles, food handler certification renewals, business license renewals, sales tax filing deadlines per state, fire inspection, ABC license (if applicable). Mostly notification logic — could be a skill bundle inside Daily Brief, but for portfolio purposes it's its own agent.

**Primary skills:**
- `track_recurring_deadlines` — calendar of all compliance dates per location/jurisdiction
- `notify_upcoming` — escalating reminders (30/14/7/3/1 day before)
- `link_to_resources` — provide owner with form links, agency contacts, prep checklists
- `log_completion` — record when items were filed/renewed
- `flag_overdue` — escalate missed deadlines
- `prefill_servsafe_log` — *[2026-04-29 add]* Phase 2 skill: prefill recurring ServSafe / temperature / sanitation log entries with owner-confirmable templates; never auto-submit, owner reviews and signs off

**Data dependencies:**
- Reads: `compliance_calendar`, `locations`, `licenses`, `state_regulations`
- Writes: `compliance_notifications`, `compliance_status_log`

**Owner approval gates:**
- Notifications auto-send at scheduled intervals
- Resource links and checklists are informational only, never advisory

**Integration points:**
- Talks to: Daily Brief Agent (upcoming deadlines summary)
- External: state agency websites for resource links (no automated filing — too high-stakes)

**Key risks:**
- Wrong deadline = real legal/operational consequence
- Multi-state operations have very different schedules; getting one wrong is expensive
- Agent must never advise on compliance decisions, only surface dates and resources

**Build complexity:** Low–Medium. Calendar-driven logic is simple; the *content* (correct deadlines per state per license type) is the work.

---

### Agent 14 — Employee Document Tracker

**Purpose:** Track expiring employment documents — H-1B work authorization expirations, I-9 re-verification dates, food handler certs, driver's licenses for delivery staff. Liability-adjacent: agent surfaces dates, never advises on legal status.

**Primary skills:**
- `track_document_expiry` — calendar per employee per document type
- `notify_advance_warning` — escalating reminders (90/60/30/14 day before)
- `flag_expired` — alert owner immediately when document expires
- `provide_renewal_resources` — link to relevant forms/agencies (informational only)

**Data dependencies:**
- Reads: `employees`, `employee_documents`, `document_types`
- Writes: `document_alerts`, `expiry_log`

**Owner approval gates:**
- All notifications auto-send to owner
- Never sends to employee directly without owner approval (privacy, legal sensitivity)
- Never advises on visa, I-9, or immigration matters

**Integration points:**
- Talks to: Daily Brief Agent, Compliance Calendar Agent
- External: None (intentionally — no automated agency interaction)

**Key risks:**
- Liability if agent accidentally implies legal advice → strict boilerplate disclaimers, structured data only, no free-text advisory output
- Privacy — employment authorization data is sensitive; access strictly limited to owner

**Build complexity:** Low. Logic is simple. Real work is liability-conscious prompt design + disclaimers.

---

## FINANCIAL (Agents 15–17)

---

### Agent 15 — Cash & AR Agent

**Purpose:** Track invoiced catering balances, send payment reminders on schedule, escalate overdue accounts to owner. Real-money impact — catering invoices often run $1K–$20K and aging matters.

**Primary skills:**
- `track_invoices` — open balances by customer, age them
- `schedule_reminders` — escalating cadence (gentle / firm / final notice)
- `draft_reminder` — channel-appropriate (WhatsApp casual, email formal)
- `escalate_overdue` — flag accounts past threshold for owner direct contact
- `reconcile_payment` — match incoming payments to invoices

**Data dependencies:**
- Reads: `invoices`, `customers`, `payments_received`, `aging_buckets`
- Writes: `reminder_log`, `payment_reconciliation`, `escalations`

**Owner approval gates:**
- All outbound reminders require owner approval in Phase 0–1
- Phase 2: gentle reminders auto-send with template; firm/final notices always require approval
- Escalations to collections (rare for SMB) require explicit owner action

**Integration points:**
- Talks to: Catering Lead Agent (invoice creation), Daily Brief Agent (AR aging summary)
- External: Payment processor (Stripe, Square, Razorpay) for reconciliation; QuickBooks for sync (Phase 2+)

**Key risks:**
- Wrong reminder to wrong customer = relationship damage
- Premature firm-tone reminder = relationship damage
- QuickBooks sync lag (the customer's existing problem) → agent surfaces but doesn't solve

**Build complexity:** Medium. Tone calibration is the hard part.

---

### Agent 16 — Sales Tax Filing Agent

**Purpose:** Multi-state sales tax tracking. For multi-state operators (Triveni: TX/MD/NC/SC/OH/VA — 6 states with different rates and filing schedules), this is a significant pain. High value, narrow scope, mostly calendar + form-prep work.

**Primary skills:**
- `track_filing_deadlines` — per-state, per-frequency (monthly/quarterly/annual)
- `compile_taxable_sales` — pull from POS for the period (requires integration)
- `prepare_filing_package` — formatted summary ready for owner/accountant to review
- `notify_filing_due` — escalating reminders
- `log_filed` — record filing date and confirmation

**Data dependencies:**
- Reads: `locations`, `state_tax_rules`, `pos_sales_data`, `filing_history`
- Writes: `tax_filing_calendar`, `filing_packages`, `filing_log`

**Owner approval gates:**
- Filing packages are prepared for owner/accountant review, never auto-filed
- Tax math must be verifiable — agent shows work, never opaque calculation

**Integration points:**
- Talks to: End-of-Day Reconciliation Agent (sales data), Compliance Calendar Agent (deadline visibility)
- External: POS API; state tax websites for resource links (no auto-filing)

**Key risks:**
- Wrong tax math = audit liability
- Out-of-date rate or rule = liability; rate changes need monitoring
- Cross-state rules vary significantly (e.g., grocery food tax rules differ by state)

**Build complexity:** Medium–High. Math accuracy + jurisdictional knowledge = real work.

---

### Agent 17 — Unit Economics Agent

> **RETIRED 2026-04-29 → replaced by Agent #22 P&L Anomaly Detective (light).** Original A17 required deep recipe modeling and clean COGS data — most customers don't have either. The replacement is shallower: flag margin drops + per-location underperformance from POS + basic cost data, no per-SKU recipe modeling. New config key `pnl_anomaly` (not `unit_economics`); fresh agent slot. See Agent #22 spec at the end of this doc.

**Purpose:** Per-SKU and per-menu-item profit analysis, pricing recommendations based on supplier cost changes, low-margin item flagging. Requires deep POS integration and supplier cost data.

**Primary skills:**
- `compute_per_sku_margin` — sales price minus COGS per unit
- `compute_per_menu_item_margin` — recipe-based COGS for prepared items
- `detect_supplier_cost_change` — alert when input cost shifts by threshold
- `recommend_repricing` — proposed price changes to maintain margin
- `flag_low_margin` — surface items losing money or near-zero margin

**Data dependencies:**
- Reads: `sku_catalog`, `menu_recipes`, `supplier_costs`, `pos_sales_data`, `pricing`
- Writes: `margin_analysis`, `repricing_recommendations`, `cost_change_alerts`

**Owner approval gates:**
- All pricing change recommendations require explicit owner approval before menu/POS update
- Cost change alerts are informational, no auto-action

**Integration points:**
- Talks to: Inventory Tracker (cost data), Supplier Coordination Agent (cost change source)
- External: POS for pricing updates (Phase 3); supplier pricing data is mostly manual entry initially

**Key risks:**
- Recipe accuracy for prepared items (sweets, bakery, food court) is the hard part — no two batches are identical
- Pricing is owner judgment + relationship-sensitive (regulars notice price hikes)
- COGS data is messy in real businesses — agent recommendations only as good as the input

**Build complexity:** High. Requires POS depth, recipe modeling, and clean cost data — most customers won't have any of these initially.

---

## EXCEPTION HANDLING (Agents 18–20)

---

### Agent 18 — Customer Complaint Agent

> **RETIRED 2026-04-29 → folded into Agent #9 (VIP) + Agent #4 (Daily Brief).** Severity classification + escalation belongs near the customer relationship surface (A9). Patterns / surface_recurring belongs in the morning brief (A4). No standalone agent needed. Sensitive content design exceeded what a thin separate agent could meaningfully add.

**Purpose:** When a complaint arrives (negative review, refund request, food safety concern), structured intake, owner notification, suggested response with brand-tone matching. Sensitive — owner judgment matters more than agent output.

**Primary skills:**
- `parse_complaint` — extract: who, what, when, severity, channel, sentiment
- `classify_severity` — minor (snippy comment) → moderate (refund request) → severe (food safety / health concern)
- `escalate_immediate` — severe complaints page owner same minute
- `draft_response` — multiple options (apologetic, professional, firm-but-fair) for owner to pick
- `track_resolution` — log outcome and follow up if needed
- `surface_patterns` — flag if same complaint type recurring

**Data dependencies:**
- Reads: `complaints`, `customers`, `brand_tone_guide`, `resolution_history`
- Writes: `complaint_log`, `response_drafts`, `resolution_outcomes`

**Owner approval gates:**
- ALL complaint responses require owner approval — no auto-response ever
- Severe complaints (food safety, allergic reaction) escalate immediately, never delay
- Refund decisions are owner-only

**Integration points:**
- Talks to: VIP Customer Agent (if complainant is VIP, severity adjusts)
- External: Yelp/Google Review APIs for surfaced reviews (Phase 2); WhatsApp/email for direct complaints

**Key risks:**
- Wrong tone in response = viral negative review
- Defensive auto-language = relationship damage
- Severity misclassification = either underreaction (food safety) or overreaction (rude DM treated as crisis)

**Build complexity:** Medium. Sensitive content design > technical complexity.

---

### Agent 19 — Equipment & Maintenance Agent

**Purpose:** Track repair history and preventive maintenance for POS terminals, refrigeration units, ovens, vehicles, A/C, fire suppression. Niche, low frequency — mostly a calendar with structured intake when things break.

**Primary skills:**
- `log_equipment_issue` — when staff reports something broken, structured intake
- `match_to_history` — has this happened before? Same vendor?
- `route_to_vendor` — pick correct repair service per equipment type per location
- `schedule_preventive` — reminder calendar for filter changes, oil changes, calibrations
- `track_warranty` — flag when issues fall under warranty
- `cost_history_summary` — owner query "how much have we spent on the Houston walk-in this year?"

**Data dependencies:**
- Reads: `equipment_inventory`, `vendors`, `maintenance_history`, `warranty_status`
- Writes: `repair_tickets`, `preventive_calendar`, `vendor_communications`

**Owner approval gates:**
- All vendor calls/messages require owner approval
- Repair authorization above $X requires explicit approval

**Integration points:**
- Talks to: Multi-Location Coordinator (cross-location patterns)
- External: WhatsApp/phone to vendors

**Key risks:**
- Equipment outage = revenue stop; agent's job is fast routing, not delay
- Vendor relationships are personal; auto-tone can feel cold

**Build complexity:** Low. Mostly state tracking and routing.

---

### Agent 20 — Owner Wellbeing Agent

> **RETIRED 2026-04-29 → folded.** The original spec already recommended this collapse. The "weekly owner-load summary" piece moves to Agent #4 Daily Brief as a weekly section. The "block non-urgent pings during family time" piece is a platform-level quiet-hours rule, not an agent. AI-as-therapist framing is dropped entirely.

**Purpose:** Check in with owner on workload, surface days where the agent suite caught a lot of fires, suggest when to take a break. *Honest read: this veers into AI-as-therapist territory which is poor product positioning. Recommend reframing as a section of Daily Brief Agent ("you handled X today, top 3 stressors were Y, agent handled Z without you needing to intervene") rather than its own addressable agent. Listed for portfolio completeness per founder directive.*

**Primary skills:**
- `compile_owner_load_metrics` — count of owner approvals in a day, decision count, response time
- `flag_high_stress_pattern` — sustained spikes in load, consecutive late nights
- `surface_relief_signals` — which agent prevented owner intervention today
- `suggest_break_window` — quietest hours/days based on operational patterns

**Data dependencies:**
- Reads: All agent decision logs, owner activity timestamps
- Writes: `owner_load_metrics`, `wellbeing_signals`

**Owner approval gates:**
- All output is informational, never advisory on personal/health matters
- Strictly metrics-based; no interpretation of owner's emotional state

**Integration points:**
- Reads from: every agent
- Talks to: Daily Brief Agent (probably should just live there)

**Key risks:**
- Crossing into mental health advisory = inappropriate scope, real liability
- Owners may find it patronizing
- Metrics can be misread without context (a busy day isn't necessarily a bad day)

**Build complexity:** Low (as metrics layer). Don't build as standalone agent.

---

## NEW AGENTS (added 2026-04-29 in Solid 17 consolidation)

---

### Agent 21 — Expense Bookkeeper Agent

**Purpose:** Owner snaps a photo of a supplier receipt or expense (gas, repair, supplies); agent OCRs it, categorizes personal-vs-business and into chart-of-accounts buckets, drafts a QuickBooks entry for owner approval. The endemic pain in family SMBs: commingled personal/business cards, shoebox receipts, accountant-time blown on cleanup.

**Primary skills:**
- `parse_receipt_photo` — OCR + structured extraction (vendor, date, amount, line items where visible)
- `classify_personal_vs_business` — LLM classification with confidence floor; below threshold flags for owner
- `categorize_chart_of_accounts` — map to QuickBooks buckets (COGS / supplies / utilities / repairs / personal-draw / etc.)
- `draft_qb_entry` — formatted QB-ready transaction draft for owner review
- `push_to_quickbooks` — Phase 2; never auto-pushes in Phase 0–1
- `flag_anomaly` — duplicate receipts, suspiciously round amounts, vendors not seen before

**Data dependencies:**
- Reads: `chart_of_accounts`, `vendors_seen`, `receipts_history`
- Writes: `receipt_log`, `expense_drafts`, `qb_sync_queue`

**Owner approval gates:**
- ALL personal-vs-business classifications surface to owner in Phase 0–1; no silent auto-tag
- ALL QuickBooks pushes require explicit owner confirm in Phase 0–1
- Phase 2: high-confidence (>0.85) categorizations auto-tag with daily review summary; QB push still owner-confirmed

**Integration points:**
- Talks to: Agent #15 Cash & AR (catering deposits reconcile here), Agent #4 Daily Brief (weekly expense summary)
- External: QuickBooks Online API, OCR (cloud or local), photo intake via WhatsApp

**Key risks:**
- Mis-classification of personal as business (or vice versa) has tax consequences — owner approval gate is the safety net
- OCR accuracy on crumpled receipts is genuinely poor — agent must surface uncertainty, not guess
- QuickBooks API friction (auth, rate limits, account-list drift) is real engineering cost

**Build complexity:** Medium. OCR + LLM categorization is well-bounded. QuickBooks integration is the slow part. Phase 0 stub is trivial; Phase 1 build is ~3–4 weeks of *net-new* work — Hermes substrate (dispatch, audit, approval codes, vision input pipeline mirroring Catering's `parse-menu-photo`) carries an estimated ~80% of the lift.

**Why priority build:** Highest-ROI new agent in the consolidation review. Commingled cards is universal pain; visible weekly value to owner; first agent with a *hard-dollar* ROI lever (potentially reduces accountant fees by $200–300/mo, unlike the leverage agents whose value is "saved time and stress").

**Build gating (added 2026-04-29 per Stage 1 decision doc; revised 2026-04-29 r2 after Catering menu E2E test + Hermes-first rule installation):** v0.1 implementation is gate-released on **two** investigations:

1. **Customer-discovery behavioural commitment.** Two questions to 2–3 design-partner owners ("walk me through how you handle a receipt today" + "would you actually use this 5×/day"), then a behavioural test: get 1+ owner to commit to a 2-week prototype trial running ≥5 receipts/day. Intent ≠ retention; behaviour reveals it. Also surface the hard-dollar question: "what does your accountant charge to clean up receipts each month?"
2. **QBO API ground-truthing.** Read current Intuit Developer documentation. Confirm: OAuth flow + write-permission scope + attachment support + rate limits + sandbox/production approval timing + accountant-side webhooks (so agent knows if a record was edited downstream) + multi-company scenarios (some owners have separate QBO files per LLC). Half-day, prevents a "we can't actually do that" moment 4 weeks into the build.

**Retired gate:** ~~OCR viability test~~ — removed 2026-04-29 r2 after Catering's menu pipeline tested **end-to-end in production** (owner sends menu image to WhatsApp → Hermes extracts → structured menu created → customer-facing reply). Receipt-specific extractor edge cases (faded thermal, handwritten supplier receipts, multi-language code-switching) move to v0.1 hardening tasks during build, not a pre-build investigation. The OCR surface is proven.

**Hermes carries the entire source-in → vision-extract → structured-out → response-out loop** — verified E2E in Catering 2026-04-29. For Expense Bookkeeper this directly transfers, with no new infrastructure: WhatsApp media inbound + routing, vision extraction with structured JSON output, skill chaining, `decisions.log` audit chain, `#XXXXX` approval codes with 4h proposal TTL + dead-man escalation (Catering template), `sender_role` identity check, multi-channel response, LLM gateway. The original "four architectural surfaces" framing in the Stage 1 doc treated this loop as new infrastructure; per the Hermes-first rule (see project `CLAUDE.md`), it is substrate.

**Genuinely net-new engineering surfaces (the only ones requiring real investment):**
- **External write API (QBO):** OAuth flow + token storage + write-scope API client. ~1–1.5 weeks. Unfamiliar but well-trodden engineering.
- **Money-moving UX discipline:** code+amount approval format (`#A47C2 $234.50` so owners can't pattern-match-YES on the wrong receipt), perceptual-hash dedup (not byte-hash — same receipt photographed twice has different bytes), per-amount cockpit-vs-WhatsApp threshold (>$X routes to web review with line-item breakdown), 24h reversibility window. ~1 week. Hermes provides the primitives (audit + approval codes); the *discipline* must still be designed deliberately. This is the hardest remaining product risk.
- **Receipt extractor schema + classifier + chart-of-accounts mapper:** skill-level work, mirrors Catering's `parse-menu-photo`. ~3–5 days.
- **Edge-case hardening + safety + reversibility logic:** ~3–5 days.

**Revised effort estimate for v0.1: ~2–3 weeks of net-new engineering** (down from the 3–4w estimate before Hermes was credited honestly). Hermes carries the rest.

**v0.1 explicit scope cuts (deferred to v0.2+):** voice notes, multi-language code-switching during owner approval, self-improvement loop / learned classification, multi-currency / FX, batch processing, multi-page PDFs, family-member receipt forwarding, per-location auto-tagging, vendor creation (agent flags new vendors for owner instead), tax-jurisdiction reasoning (use QBO's existing tax setup).

**Money-moving guardrails (v0.1, mandatory):** code+amount approval format (`#A47C2 $234.50`, not just code) so owners can't pattern-match-approve the wrong receipt; perceptual hash for duplicate detection (not byte-hash — same receipt photographed twice has different bytes); per-amount approval threshold (>$X routes to cockpit web review with line-item breakdown, ≤$X allows WhatsApp approve); 24h reversibility window with explicit "outside window → escalate to owner" path. The Stage 1 doc identified Surface 3 (money-moving) as the core risk; these are the primitives that address it.

**Reference:** Stage 1 decision doc + Hermes-substrate addendum, retained outside the repo until investigations clear.

---

### Agent 22 — P&L Anomaly Detective Agent

**Purpose:** Daily/weekly heartbeat across locations. Flag margin drops ("biriyani margin dropped 8%") and per-location underperformance ("Pineville location is 15% below trailing 4-week average"). Owner-facing alarm only — never auto-acts on pricing or operations. Replaces the retired Agent #17 Unit Economics with a shallower, customer-tractable shape.

**Primary skills:**
- `detect_margin_drop` — per-product or per-category margin vs. trailing window; alert when delta exceeds `margin_drop_alert_pct`
- `detect_location_underperform` — per-location revenue/volume vs. baseline; alert when delta exceeds `location_underperform_alert_pct`
- `surface_top_drivers` — when an alert fires, surface the top 3 line items contributing
- `suggest_action` — informational only ("supplier cost up 12% on basmati, was last repriced 8 months ago"); never auto-action

**Data dependencies:**
- Reads: POS sales data (Clover/Square API), supplier costs (manual or Agent #7 feed), `pricing`, location baselines
- Writes: `anomaly_alerts`, `anomaly_history`

**Owner approval gates:**
- Alerts are informational; agent never adjusts pricing or pushes to POS
- Repricing recommendations (if surfaced) are owner judgment, never auto-applied

**Integration points:**
- Talks to: Agent #4 Daily Brief (anomaly summary in morning brief), Agent #7 Supplier Coordination (cost-change feed), Agent #6 Inventory (volume baseline)
- External: POS API (Clover, Square)

**Key risks:**
- POS data quality varies wildly; agent must distinguish "real anomaly" from "POS hiccup"
- False positives erode owner trust faster than a missed alert; tune thresholds conservatively
- Cost data without POS is half the picture; agent declines until both are configured

**Build complexity:** Medium. Threshold logic is simple; the work is POS depth and threshold calibration per customer. Lighter than retired A17 by design — no recipe modeling, no auto-repricing.

**Replaces:** Agent #17 Unit Economics (retired 2026-04-29). New config key `pnl_anomaly` (NOT `unit_economics`); fresh agent slot, not a renamed slot.

---

## NEW BACKLOG ENTRIES (added 2026-04-29; build only on customer demand)

---

### Agent 23 — Order Status & Pickup Agent (BACKLOG)

**Purpose:** Customers text "where's my order?" → agent checks kitchen status, sends photo of ready bag with name/number callout. Reduces in-store pickup confusion at restaurants and food courts.

**Why backlog:** Requires KDS (Kitchen Display System) or POS order-state integration that current architecture doesn't have. Promote on first restaurant pilot that has a Clover/Square order pipeline ready to integrate.

**Build complexity:** Medium once integration is unblocked.

---

### Agent 24 — Upsell & Menu Personalizer Agent (BACKLOG)

**Purpose:** During phone or online order intake, suggest contextual add-ons ("add fresh paneer for $3?") based on past orders + dietary preferences.

**Why backlog:** Restaurant-only scope, requires deep POS or phone-AI integration at order capture time, ROI murky vs. POS vendors' own upsell tools (which are increasingly built-in). Skip until a customer specifically asks AND has the POS depth to integrate.

**Build complexity:** High (integration heavy, narrow channel).

---

### Agent 25 — Third-Party Delivery Coordinator Agent (BACKLOG)

**Purpose:** Monitor DoorDash / UberEats / GrubHub tablets, prevent double-cooking, auto-update order status, reconcile platform fees.

**Why backlog:** No consolidation API exists between these platforms — known industry pain ("tablet hell"). Build cost is enormous (screen-scraping or per-platform integration with rate-limited APIs). The value (preventing double-cooking, fee reconciliation) is real but bounded. Skip until a customer with this exact pain is paying.

**Build complexity:** High (no clean integration path).

---

## Portfolio summary — Solid 17 (consolidated 2026-04-29)

**Active build commitment: 17 agents.** Founder's earlier counter-recommendation of "12–14 well-built agents" effectively landed.

### Tier 1 — Operations & Synthesis (6: must-build)
Agents 1, 2, 3, 4, 5, **21** (Expense Bookkeeper, NEW priority).

### Tier 2 — Build After Paying Customers (11: opt-in scaffolds)
Agents 6, 7, 9, 10, **11 (reframed as Festival & Peak Prep)**, 12, 13, 14, 15, 16, **22** (P&L Anomaly Detective, NEW).

### Backlog — On Customer Demand (5: paper specs only)
Agents 8 (Receiving & QA), 19 (Equipment & Maintenance), **23** (Order Status & Pickup), **24** (Upsell & Menu Personalizer), **25** (Third-Party Delivery Coordinator).

### Retired (folded or replaced; no longer addressable)
- Agent 17 Unit Economics → replaced by Agent 22 P&L Anomaly Detective
- Agent 18 Customer Complaint → folded into Agents 9 (VIP) + 4 (Daily Brief)
- Agent 20 Owner Wellbeing → folded into Agent 4 (Daily Brief weekly section) + platform quiet-hours rule

**Implementation status as of 2026-04-29:**
- 3 Live in production: Shift (#1), Daily Brief (#4), EOD Reconciliation (#5)
- 11 Scaffolded with opt-in disabled: #2, #3, #6, #7, #9, #10, #12, #13, #14, #15, #16
- 2 Stub pending (in `tasks/solid17-consolidation-plan.md` Phase 2/3): #21 Expense Bookkeeper, #22 P&L Anomaly Detective
- 1 Tier-promoted, scaffold pending: #11 Festival & Peak Prep (was Tier-3 deferred)
- 5 Paper specs only (backlog): #8, #19, #23, #24, #25

**Implementation status update — 2026-05-04:**
- **Agent #3 Multi-Location Coordinator v0.1** SHIPPED + DEPLOYED (PR #62) — customer closest-store query via productivity/maps + owner Phase 1 query.
- **Agent #13 Compliance Calendar v0.1** SHIPPED + DEPLOYED (PR #63 + hotfix #64) — daily reminder cron with 3-layer idempotency + bounded catch-up + owner mark-done SKILL.
- **Agent #21 Expense Bookkeeper** scaffold already shipped earlier; QBO write-API integration DEFERRED pending QBO sandbox creds (operator action; see `tasks/overnight-2026-05-04-closeout.md`).
- **Agent #22 P&L Anomaly Detective** Tier-2 SCAFFOLD shipped (PR #65) — full anomaly logic deferred to v0.2 gated on customer POS choice.
- **Agent #19 Equipment Maintenance** Tier-2 SCAFFOLD shipped (this PR) — full per-vendor logic deferred to v0.2.
- **Agents #8 / #23 / #24 / #25** remain BACKLOG per their own portfolio entries (build only on customer demand). Honest Hermes-first defer documented in `tasks/overnight-2026-05-04-closeout.md`.

**Portal:** `http://46.62.206.192:8080/portal/` reflects this consolidated view as of 2026-04-29.

---

## Next steps from this document

1. Execute Phase 2/3 of `tasks/solid17-consolidation-plan.md`: scaffold Agent #21 (Expense Bookkeeper) and Agent #22 (P&L Anomaly Detective) with the same Tier-2 stub pattern (`schemas.py` config class + dispatcher SKILL.md + opt-in disabled by default).
2. Promote Agent #11 from paper-spec to active scaffold under the reframed Festival & Peak Prep shape (heartbeat 3–5 days before festivals; consume `forecast_demand` from Agent #4).
3. Phase 1 builds for the highest-ROI new agents — recommended order: #21 Expense Bookkeeper first (universal pain, tractable build), then #11 Festival & Peak Prep (recurring value, low integration cost), then #22 P&L Anomaly Detective (gated on POS data depth at first paying customer).
4. Update `MEMORY.md` portfolio status snapshot once #21 / #22 land in code.

*Document status: v2 consolidated 2026-04-29. v1 spec preserved above for traceability — see `git log docs/portfolio.md` for diff.*

---

# Portfolio expansion v3 — 25-agent strategic reshape (2026-05-04)

User-supplied portfolio reshape after the 2026-05-04 overnight closeout introduced 9 business-domain groupings + 16 net-new agents on top of the Solid 17 base. Gap analysis at `tasks/audits/portfolio-expansion-2026-05-04.md` is canonical; this section captures the placeholders for the new agents. **Numbering note:** existing agents keep historical 1-25 codebase numbers (referenced in SKILL.md, audit log type strings, commit messages); new agents take #26-#41 slots.

## Domain reorganization (target view)

| Domain | Agents (code-internal #) |
|---|---|
| Workforce & Scheduling | #1 Shift Agent (LIVE), #12 Hiring & Onboarding (scaffold), **NEW #26 Performance & Training Coach** |
| Catering & High-Margin Revenue | #2 Catering Lead (LIVE infra, opt-in) + #10 Catering Followup (scaffold) [combined as "Catering Lead + Closer"], **NEW #27 Catering Equipment & Packaging Tracker** |
| Inventory, Supply & Waste | #6 Inventory + #7 Supplier (scaffold) [combined as "Smart Reorder + Supplier Negotiator"], **NEW #28 Perishable Priority & Waste Reducer**, **NEW #29 Slow-Mover Liquidation** |
| Kitchen & Order Operations (Big Gap — 0 existing) | **NEW #30 Order Accuracy Guardian** (HIGH PRIORITY), **NEW #31 Kitchen Load Balancer & ETA**, **NEW #32 Special Request Memory** |
| Customer Experience & Loyalty (Big Gap — 0 existing) | **NEW #33 Loyalty & Punch-Card**, **NEW #34 Menu Suggestion & Upsell**, **NEW #35 Referral & Review Responder** |
| Finance & Back-Office | #15 Cash & AR (scaffold), #21 Expense Bookkeeper (scaffold), #22 P&L Anomaly Detective (scaffold), **NEW #36 Credit Customer & Temple Account Manager** |
| Multi-Location & Growth | #3 Multi-Location Coordinator (**LIVE v0.1** PR #62), **NEW #37 New Location Feasibility Scout** |
| Marketing & Community | #11 Festival & Peak Prep (paper→scaffold pending), **NEW #38 Local Community Broadcast**, **NEW #39 Photo Menu Curator**, **NEW #40 Competitor Price Watcher** |
| Compliance, Equipment & Owner Protection | #13 Compliance Calendar → **reframed as "Food Safety & Compliance Guardian" v0.2** (LIVE v0.1 PR #63), #16 Sales Tax Filing (scaffold; absorbed under broader Compliance Guardian), #19 Equipment & Maintenance (**scaffold** PR #66), **NEW #41 Owner Wellbeing & Burnout Guardian** (revival of retired #20) |

## NEW agent specs (placeholders v0.0 — full specs land per build cycle)

### Agent #26 — Performance & Training Coach

**Purpose:** Gentle feedback, skill tracking, SOP quizzes via WhatsApp. Mirror existing Hiring agent's WhatsApp quiz substrate.

**Hermes-first effort:** LOW–MEDIUM. JSON-on-disk skill matrix per employee + cron for quiz delivery. No external APIs.

**Build complexity:** Low–Medium. Tractable in ~1 day per skills-roadmap.md substrate inventory.

---

### Agent #27 — Catering Equipment & Packaging Tracker

**Purpose:** Track deposit collection + return reminders for catering equipment (chafers, hot-boxes, serving platters). Extends existing catering lead state.

**Hermes-first effort:** LOW. Mirror compliance reminder pattern (Agent #13) with deposit-state machine.

**Build complexity:** Low. Tractable in ~1 day.

---

### Agent #28 — Perishable Priority & Waste Reducer

**Purpose:** Daily "use-first" list + near-expiry recipes/discounts. Reduces ethnic SMB waste from items like fresh paneer, naan dough, prepared chutneys.

**Hermes-first effort:** MEDIUM. Requires POS data for sales velocity + inventory dates. **Gated on customer POS choice** (same as Agent #22).

**Build complexity:** Medium. Defer until first POS-onboarded customer.

---

### Agent #29 — Slow-Mover Liquidation

**Purpose:** Flags slow-moving stock; suggests bundling/discounts/donations.

**Hermes-first effort:** MEDIUM. Same POS-depth gate as #28.

**Build complexity:** Medium.

---

### Agent #30 — Order Accuracy Guardian (HIGH PRIORITY per user)

**Purpose:** Cross-checks orders vs kitchen tickets BEFORE handover. Reduces wrong-order fixes that erode customer trust.

**Hermes-first effort:** MEDIUM-HIGH. Requires KDS or POS order-state integration (same blocker class as Agent #23 Order Status — DEFERRED INDEFINITELY per portfolio.md "build only on customer demand" until first restaurant pilot has Clover/Square order pipeline).

**Build complexity:** Medium-High. Cannot ship until customer onboards POS with order-state webhook.

---

### Agent #31 — Kitchen Load Balancer & ETA

**Purpose:** Real-time busyness monitoring + accurate ETAs to customers (vs over-promising on standard 20-min defaults).

**Hermes-first effort:** MEDIUM-HIGH. Same KDS/POS gate as #30.

**Build complexity:** Medium-High.

---

### Agent #32 — Special Request Memory

**Purpose:** Remembers no-onion / Jain / extra-spicy / no-cilantro preferences across orders. Surfaces to kitchen on each new order from the same customer.

**Hermes-first effort:** LOW. Per-customer JSON state file keyed by phone or chat_id. CRM-lite. Substrate sufficient.

**Build complexity:** Low. Tractable in ~1 day.

---

### Agent #33 — Loyalty & Punch-Card

**Purpose:** WhatsApp-based points, auto-rewards, birthday offers. Ethnic SMBs frequently run informal punch-card programs already; this digitizes them.

**Hermes-first effort:** LOW–MEDIUM. JSON state per customer + cron for birthdays + reward triggers. No external APIs.

**Build complexity:** Low–Medium. Tractable in ~1 day.

---

### Agent #34 — Menu Suggestion & Upsell

**Purpose:** Real-time personalized upsells during ordering ("you usually get fresh paneer with biriyani — add today?"). DIFFERENT from retired old-#24 Upsell which was POS-vendor-side; this one is owner-controlled WhatsApp-side.

**Hermes-first effort:** MEDIUM. POS history + LLM. Gated on POS depth.

**Build complexity:** Medium.

---

### Agent #35 — Referral & Review Responder

**Purpose:** Manages referral program (track who referred whom, reward both) + auto-replies to Google Maps / Facebook reviews (after owner approval gate).

**Hermes-first effort:** MEDIUM. Referral side is JSON state. Review side requires Google My Business API + Facebook Graph API. **Investigate `mcp/native-mcp` first per skills-roadmap.md** — community MCP servers may shrink the integration cost.

**Build complexity:** Medium. Referral standalone is tractable; review-responder is gated on MCP availability + per-platform API access.

---

### Agent #36 — Credit Customer & Temple Account Manager

**Purpose:** Monthly statements + gentle reminders for institutional accounts (temples, community organizations, regular caterers who pay net-30). Extends Agent #15 Cash & AR with the temple/community-org subtype which has different tone calibration than typical commercial AR.

**Hermes-first effort:** LOW–MEDIUM. Extends existing CashArConfig with account-type field; reuses cadence + escalation logic. Tone templates differ.

**Build complexity:** Low–Medium. Tractable in ~1 day after #15 Cash & AR ships.

---

### Agent #37 — New Location Feasibility Scout

**Purpose:** Analyzes demographics/competition for expansion. Owner asks "should I open in Plano?" → agent returns demographics summary, competitor map, traffic estimates.

**Hermes-first effort:** HIGH. Multiple external APIs (US Census, Google Places, traffic data). No single bundled skill covers this.

**Build complexity:** High. Defer until clearly demanded.

---

### Agent #38 — Local Community Broadcast

**Purpose:** Opt-in WhatsApp lists for specials and festivals. Owner manages opt-in list; agent fans out broadcast (respecting WhatsApp's broadcast-list limits + the existing 100/day outbound cap).

**Hermes-first effort:** LOW. Opt-in list = JSON state file; broadcast = mirror Daily Brief send pattern. Existing safe_io.bridge_post handles the actual delivery.

**Build complexity:** Low. Tractable in ~1 day.

---

### Agent #39 — Photo Menu Curator

**Purpose:** Helps maintain/update food photos for online ordering platforms (DoorDash/UberEats menus, Google Business). Owner sends new dish photo → agent extracts metadata, suggests caption, queues for approval.

**Hermes-first effort:** LOW–MEDIUM. Reuses existing Hermes vision substrate (same pipeline as parse_catering_inquiry image extraction). Storage is JSON+filesystem.

**Build complexity:** Low–Medium. Tractable in ~1-2 days.

---

### Agent #40 — Competitor Price Watcher

**Purpose:** Tracks key items at nearby competitors (online menus, third-party delivery prices). Surfaces drift to owner.

**Hermes-first effort:** HIGH. Per-competitor scraping (different parser per restaurant chain). Brittle. Multiple legal-status questions (TOS).

**Build complexity:** High. Defer.

---

### Agent #41 — Owner Wellbeing & Burnout Guardian (revived)

**Purpose:** Weekly load summary (hours worked, decisions made, sleep gap) + quiet-hours rule (no notifications between owner-configured times unless critical). Was retired in 2026-04-29 consolidation as "folded into Daily Brief"; user re-promotes as standalone agent for visibility.

**Hermes-first effort:** LOW. Weekly summary patches send-daily-brief; quiet-hours is a config flag + guard in notify_owner_with_fallback. No external APIs.

**Build complexity:** Low. Tractable in ~1 day. **Highest-ROI tractable build** in the new portfolio per gap analysis.

---

## Status corrections vs user's mental model

User's portfolio statements that need correction:
- "**Catering Lead + Closer ✅ Live**" → infrastructure deployed (catering_dispatcher SKILL + parse_catering_inquiry + apply_catering_owner_decision all live on srilu); but `cfg.catering.enabled` is opt-in per customer.
- "**Multi-Location Coordinator ✅ Scaffolded**" → STALE. Agent #3 v0.1 is **LIVE** (PR #62, deployed to srilu 2026-05-04). Customer closest-store query via `productivity/maps` is functional.
- "**Equipment & Maintenance Agent (Backlog → New)**" → STALE. Agent #19 scaffold is **shipped** (PR #66, deployed 2026-05-04). The "Backlog → New" promotion happened earlier today.

## Build-priority recommendation

Per gap-analysis doc + Hermes-first discipline, **highest-ROI tractable next builds (no external blockers):**

1. **#41 Owner Wellbeing & Burnout Guardian** — pure substrate; weekly Daily Brief patch + quiet-hours flag. ~1 day.
2. **#32 Special Request Memory** — CRM-lite JSON state; matches existing patterns. ~1 day.
3. **#33 Loyalty & Punch-Card** — JSON state + cron + WhatsApp. ~1 day.
4. **#26 Performance & Training Coach** — mirror Hiring quiz substrate. ~1-2 days.
5. **#38 Local Community Broadcast** — mirror Daily Brief send pattern. ~1 day.
6. **#36 Credit Customer & Temple Account Manager** — extend Cash & AR scaffold. ~1 day.
7. **#39 Photo Menu Curator** — reuse vision substrate. ~1-2 days.
8. **#27 Catering Equipment & Packaging Tracker** — mirror Compliance reminder pattern. ~1 day.

**Deferred (POS-gated):** #28, #29, #30 [HIGH PRIORITY but blocked], #31, #34.

**Deferred (external API):** #35 [investigate MCP first], #37, #40.

**Action item:** user authorization required for any build cycle. Per overnight closeout discipline, do not speculatively build any of these until customer demand or operator authorization. Recommended next overnight batch: #41 + #32 + #33 (3 small tractable agents, ~3 days total).

---

## Implementation status update — 2026-05-04 v3 (post-portfolio-expansion)

- **5 LIVE in production**: #1 Shift, #3 Multi-Location, #4 Daily Brief, #5 EOD, #13 Compliance Calendar
- **12 SCAFFOLDED opt-in**: #2 Catering, #6 Inventory, #7 Supplier, #9 Festival, #10 Catering Followup, #12 Hiring, #14 Employee Docs, #15 Cash & AR, #16 Sales Tax, #19 Equipment Maintenance, #21 Expense Bookkeeper, #22 P&L Anomaly Detective
- **16 NEW (paper-spec, placeholders only)**: #26-#41 per the new domain reorganization
- **5 BACKLOG**: #8 Receiving & QA, #11 Festival promoted-but-scaffold-pending, #23 Order Status, #24 Upsell (old retired #24, NOT user's #34), #25 Third-Party Delivery — all per portfolio.md "build only on customer demand"

**Total agent slots:** 38 (5 live + 12 scaffolded + 16 paper-spec + 5 backlog).

*Document status: v3 portfolio expansion 2026-05-04. v2 / v1 above retained for traceability.*