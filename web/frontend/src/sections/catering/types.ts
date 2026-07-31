// Response shapes for /api/catering/*. These mirror the backend's Pydantic
// models; the generated `src/api/schema.ts` is the authority, these are the
// ergonomic hand-written mirrors the rest of the cockpit uses with `api.GET<T>`.

export interface DashboardCounts {
  total_leads: number;
  new_leads: number;
  qualifying: number;
  awaiting_owner_approval: number;
  proposals_sent: number;
  booked_this_month: number;
  lost_this_month: number;
  on_hold: number;
  followups_due: number;
}

export interface ReadinessFlags {
  pricebook_present: boolean;
  pricebook_placeholder: boolean;
  pricebook_version: number | null;
  menu_present: boolean;
  menu_version: number | null;
  menu_item_count: number;
  kill_switch_engaged: boolean;
  automation_kernel_armed: boolean;
  automation_kernel_armed_source: string | null;
  followups_enabled: boolean | null;
}

export interface CateringDashboard {
  generated_at: string;
  counts: DashboardCounts;
  readiness: ReadinessFlags;
  degraded: string[];
}

export interface QualificationSummary {
  complete: boolean;
  missing: string[];
  missing_labels: string[];
  asked: string[];
  answered_count: number;
  required_count: number;
  rounds: number;
}

export interface LeadRow {
  lead_id: string;
  status: string;
  customer_phone_masked: string;
  customer_name: string | null;
  event_date: string | null;
  headcount: number | null;
  event_type: string | null;
  on_hold: boolean;
  hold_reason: string | null;
  approval_code: string | null;
  quote_version: number;
  latest_quote_total_usd: number | null;
  qualification: QualificationSummary;
  next_action: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface LeadListResponse {
  leads: LeadRow[];
  status_counts: Record<string, number>;
  available: boolean;
  degraded: string | null;
}

export interface QuoteVersionRow {
  version: number;
  /** null for the first committed version — there is nothing to diff against,
   *  so items_added holds the whole basket rather than a change. */
  from_version: number | null;
  ledger_entry_id: string | null;
  quote_total_usd: number | null;
  source: string | null;
  price_status: string | null;
  pricebook_version: number | null;
  menu_version: number | null;
  created_at: string | null;
  item_count: number;
  diff_summary: string;
  items_added: string[];
  items_removed: string[];
  quote_text_changed: boolean;
  total_delta_usd: number;
}

export interface AmendmentRow {
  amendment_id: string;
  source: string | null;
  source_transport: string | null;
  status: string | null;
  raw_text_prefix: string;
  raw_text_truncated: boolean;
  captured_at: string | null;
  applied: boolean;
  applied_at: string | null;
  disposition: string | null;
}

export interface TimelineRow {
  ts: string;
  event: string;
  detail: string;
  source: string;
}

export interface ControlChip {
  available: boolean;
  stored_mode: string | null;
  effective_mode: string | null;
  expired: boolean;
  since_ts: string;
  actor: string;
  reason: string;
  pause_expires_ts: string;
  takeover_expires_ts: string;
  note: string;
}

export interface FollowupRow {
  lead_id: string;
  followup_type: string;
  due_at: string;
  status: string;
  approval_code: string | null;
  raw: Record<string, unknown>;
}

export interface LeadDetail {
  lead_id: string;
  status: string;
  customer_phone_masked: string;
  customer_name: string | null;
  raw_inquiry: string;
  original_message_id: string;
  created_at: string | null;
  updated_at: string | null;
  extracted: Record<string, unknown>;
  qualification: QualificationSummary;
  pending_questions: string[];
  quote_text: string;
  quote_version: number;
  quote_total_usd: number | null;
  approval_code: string | null;
  on_hold: boolean;
  hold_reason: string | null;
  hold_set_at: string | null;
  deposit_status: string;
  deposit_amount_cents: number;
  selected_items: Record<string, unknown>[];
  quote_versions: QuoteVersionRow[];
  quote_ledger_available: boolean;
  amendments: AmendmentRow[];
  amendments_available: boolean;
  control: ControlChip;
  timeline: TimelineRow[];
  followups: FollowupRow[];
  followups_available: boolean;
  degraded: string[];
}

export interface MenuItemRow {
  name: string;
  price_usd: number | null;
  category: string;
  dietary_tags: string[];
  available: boolean;
  serves: number | null;
  notes: string;
}

export interface MenuResponse {
  available: boolean;
  version: number | null;
  updated_at: string | null;
  updated_by: string;
  notes: string;
  items: MenuItemRow[];
  degraded: string | null;
}

export interface PackageRow {
  id: string;
  name: string;
  price_per_person_cents: number;
  min_guests: number;
  description: string;
  dietary_profile: string[];
  active: boolean;
}

export interface FeeRow {
  id: string;
  name: string;
  kind: string;
  amount_cents: number;
  per_unit: string | null;
  active: boolean;
}

export interface DiscountRow {
  id: string;
  name: string;
  kind: string;
  value: number;
  max_amount_cents: number | null;
  requires_owner_code: boolean;
  active: boolean;
}

export interface PricebookResponse {
  available: boolean;
  version: number | null;
  effective_date: string | null;
  updated_at: string | null;
  updated_by: string;
  currency: string;
  placeholder: boolean;
  tax_rate_bps: number;
  per_person_packages: PackageRow[];
  fixed_fees: FeeRow[];
  approved_discounts: DiscountRow[];
  item_price_override_count: number;
  notes: string;
  degraded: string | null;
}

export interface QuotePreviewLine {
  name: string;
  qty: number;
  unit_cents: number | null;
  extended_cents: number | null;
  source: string | null;
}

export interface QuotePreviewFee {
  fee_id: string;
  name: string;
  kind: string;
  per_unit: string | null;
  unit_cents: number;
  units: number | null;
  extended_cents: number | null;
}

export interface QuotePreviewResponse {
  available: boolean;
  guest_count: number;
  package_id: string | null;
  package_name: string | null;
  price_per_person_cents: number | null;
  per_person_subtotal_cents: number;
  lines: QuotePreviewLine[];
  items_subtotal_cents: number;
  fees: QuotePreviewFee[];
  fees_subtotal_cents: number;
  subtotal_cents: number;
  discount_id: string | null;
  discount_name: string | null;
  discount_cents: number;
  taxable_cents: number;
  tax_rate_bps: number;
  tax_cents: number;
  total_cents: number;
  total_per_guest_cents: number;
  currency: string;
  price_status: string;
  deliverable: boolean;
  flags: string[];
  pricebook_version: number | null;
  menu_version: number | null;
  unavailable_reason: string | null;
}

export interface AllowlistSummary {
  mode: string;
  entry_count: number;
  effect: string;
  source: string | null;
}

export interface FlagValue {
  value: string;
  source: string | null;
  known: boolean;
}

export interface ConversationControlRow {
  chat_key_hash: string;
  stored_mode: string;
  effective_mode: string;
  expired: boolean;
  since_ts: string;
  actor: string;
  reason: string;
  pause_expires_ts: string;
  takeover_expires_ts: string;
}

export interface HeldLeadRow {
  lead_id: string;
  status: string;
  hold_reason: string;
  hold_set_at: string;
}

export interface ControlsResponse {
  generated_at: string;
  kill_switch_engaged: boolean;
  kill_switch_set_at: string;
  kernel_flags: Record<string, FlagValue>;
  kernel_allowlists: Record<string, AllowlistSummary>;
  containment_flags: Record<string, FlagValue>;
  budget_flags: Record<string, FlagValue>;
  conversations: ConversationControlRow[];
  conversations_available: boolean;
  suppressed_count: number;
  held_leads: HeldLeadRow[];
  followups_enabled: boolean | null;
  degraded: string[];
}

export interface FollowupsResponse {
  available: boolean;
  followups: FollowupRow[];
  due_count: number;
  unavailable_reason: string | null;
  degraded: string | null;
}

export interface ActionResult {
  ok: boolean;
  returncode: number;
  stdout: string;
  stderr: string;
}
