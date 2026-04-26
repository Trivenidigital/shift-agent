// All 11 proposal statuses (must match schemas.py ProposalStatus).
export const PROPOSAL_STATUSES = [
  "awaiting_owner_approval",
  "approved",
  "reconciling",
  "sent",
  "send_failed",
  "accepted",
  "declined",
  "denied_by_owner",
  "expired",
  "cancelled",
  "no_response_timeout",
] as const;
export type ProposalStatus = (typeof PROPOSAL_STATUSES)[number];

type Badge = { color: string; label: string; spin: boolean };

// Compile-time exhaustiveness: every status must have a badge.
export const STATUS_BADGE = {
  awaiting_owner_approval: { color: "bg-amber-100 text-amber-900", label: "Awaiting your approval", spin: false },
  approved: { color: "bg-blue-100 text-blue-900", label: "Approved — sending", spin: true },
  reconciling: { color: "bg-blue-100 text-blue-900", label: "Sending…", spin: true },
  sent: { color: "bg-indigo-100 text-indigo-900", label: "Sent — awaiting reply", spin: false },
  send_failed: { color: "bg-red-100 text-red-900", label: "Send failed", spin: false },
  accepted: { color: "bg-green-100 text-green-900", label: "Accepted", spin: false },
  declined: { color: "bg-slate-200 text-slate-700", label: "Declined", spin: false },
  denied_by_owner: { color: "bg-slate-200 text-slate-700", label: "Denied by you", spin: false },
  expired: { color: "bg-slate-200 text-slate-700", label: "Expired", spin: false },
  cancelled: { color: "bg-slate-200 text-slate-700", label: "Cancelled", spin: false },
  no_response_timeout: { color: "bg-amber-100 text-amber-900", label: "No reply (timeout)", spin: false },
} satisfies Record<ProposalStatus, Badge>;

export const TERMINAL_STATUSES = new Set<ProposalStatus>([
  "accepted",
  "declined",
  "denied_by_owner",
  "expired",
  "cancelled",
  "no_response_timeout",
]);
