import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import {
  Badge,
  DegradedBanner,
  ErrorState,
  Skeleton,
  controlModeTone,
  dollars,
  fmtTs,
  leadStatusTone,
  mutationErrorMessage,
} from "./shared";
import type { ActionResult, LeadDetail } from "./types";

// Right-hand drawer mirroring FlyerProjectEvidenceDrawer / the Commerce order
// drawer: fixed overlay, click-outside to close, stacked evidence sections.
// Every mutation POSTs to the backend, which shells to the agent script that
// owns the lock and the audit row — nothing here writes catering state.

const EXTRACTED_LABELS: [string, string][] = [
  ["event_date", "Event date"],
  ["event_time", "Event time"],
  ["headcount", "Guests"],
  ["event_type", "Event type"],
  ["venue", "Venue"],
  ["service_style", "Service style"],
  ["veg_guest_count", "Veg guests"],
  ["nonveg_guest_count", "Non-veg guests"],
  ["dietary_restrictions", "Dietary"],
  ["delivery_or_pickup", "Delivery/pickup"],
  ["budget_hint_usd", "Budget hint"],
  ["off_menu_items", "Off-menu asks"],
  ["notes", "Notes"],
];

function renderValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function LeadDetailDrawer({ leadId, onClose }: { leadId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [editText, setEditText] = useState("");
  const [quoteText, setQuoteText] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [showEdit, setShowEdit] = useState(false);

  const { data, isLoading, isError, error } = useQuery<LeadDetail>({
    queryKey: ["catering-lead", leadId],
    queryFn: () => api.GET<LeadDetail>(`/catering/leads/${leadId}`),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["catering-lead", leadId] });
    qc.invalidateQueries({ queryKey: ["catering-leads"] });
    qc.invalidateQueries({ queryKey: ["catering-dashboard"] });
    qc.invalidateQueries({ queryKey: ["catering-controls"] });
  };

  const decision = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.POST<ActionResult>(`/catering/leads/${leadId}/decision`, body),
    onSuccess: () => { setShowEdit(false); setEditText(""); setQuoteText(""); refresh(); },
  });
  const hold = useMutation({
    mutationFn: (body: { on: boolean; reason: string }) =>
      api.POST<ActionResult>(`/catering/leads/${leadId}/hold`, body),
    onSuccess: refresh,
  });
  const amend = useMutation({
    mutationFn: () => api.POST<ActionResult>(`/catering/leads/${leadId}/amend-apply`, { mode: "amendment" }),
    onSuccess: refresh,
  });

  const busy = decision.isPending || hold.isPending || amend.isPending;
  const actionError =
    mutationErrorMessage(decision.error) ||
    mutationErrorMessage(hold.error) ||
    mutationErrorMessage(amend.error);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-2xl space-y-4 overflow-y-auto bg-white p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-mono text-lg font-bold">{leadId}</h3>
            {data && (
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Badge tone={leadStatusTone(data.status)}>{data.status}</Badge>
                {data.on_hold && <Badge tone="amber">ON HOLD</Badge>}
                {data.control.available && data.control.effective_mode && (
                  <Badge tone={controlModeTone(data.control.effective_mode)}>
                    conversation: {data.control.effective_mode}
                  </Badge>
                )}
                {data.approval_code && (
                  <span className="font-mono text-xs text-zinc-500">{data.approval_code}</span>
                )}
              </div>
            )}
          </div>
          <button className="text-zinc-400 hover:text-zinc-700" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {isLoading && <Skeleton rows={8} />}
        {isError && <ErrorState error={error} what="this lead" />}

        {data && (
          <>
            <DegradedBanner reasons={data.degraded} />

            <Section title="Event details">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-zinc-500">Customer</dt>
                <dd>
                  {data.customer_name ?? "—"}{" "}
                  <span className="font-mono text-xs text-zinc-500">{data.customer_phone_masked}</span>
                </dd>
                {EXTRACTED_LABELS.map(([key, label]) => (
                  <Row key={key} label={label} value={renderValue(data.extracted[key])} />
                ))}
                <dt className="text-zinc-500">Created</dt>
                <dd className="text-xs text-zinc-500">{fmtTs(data.created_at)}</dd>
                <dt className="text-zinc-500">Updated</dt>
                <dd className="text-xs text-zinc-500">{fmtTs(data.updated_at)}</dd>
              </dl>
              {data.raw_inquiry && (
                <p className="mt-2 rounded bg-zinc-50 p-2 text-xs text-zinc-600">{data.raw_inquiry}</p>
              )}
            </Section>

            <Section title="Qualification">
              <QualificationChecklist
                missing={data.qualification.missing}
                missingLabels={data.qualification.missing_labels}
                answered={data.qualification.answered_count}
                required={data.qualification.required_count}
                rounds={data.qualification.rounds}
                pending={data.pending_questions}
              />
            </Section>

            <Section title="Quote history">
              {!data.quote_ledger_available ? (
                <p className="text-xs text-amber-800">
                  Quote ledger could not be read — version history is unavailable.
                </p>
              ) : data.quote_versions.length === 0 ? (
                <p className="text-xs text-zinc-400">No quote version committed yet.</p>
              ) : (
                <ul className="space-y-2">
                  {data.quote_versions.map((v) => (
                    <li key={v.version} className="rounded border border-zinc-200 p-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-medium">
                          v{v.version} · {dollars(v.quote_total_usd)}
                        </span>
                        <span className="text-zinc-400">{fmtTs(v.created_at)}</span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-zinc-500">
                        {v.source && <Badge>{v.source}</Badge>}
                        {v.price_status && <Badge tone={v.price_status === "pending_owner_review" ? "amber" : "green"}>{v.price_status}</Badge>}
                        <span>{v.item_count} item(s)</span>
                        {v.pricebook_version != null && <span>pricebook v{v.pricebook_version}</span>}
                        {v.menu_version != null && <span>menu v{v.menu_version}</span>}
                      </div>
                      {v.diff_summary && (
                        <div className="mt-1 text-zinc-600">
                          Changed: {v.diff_summary}
                          {v.quote_text_changed && " · quote text edited"}
                        </div>
                      )}
                      {(v.items_added.length > 0 || v.items_removed.length > 0) && (
                        <div className="mt-0.5 text-zinc-500">
                          {v.items_added.length > 0 && <span className="text-emerald-700">+ {v.items_added.join(", ")} </span>}
                          {v.items_removed.length > 0 && <span className="text-rose-700">− {v.items_removed.join(", ")}</span>}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {data.quote_text && (
                <pre className="mt-2 whitespace-pre-wrap rounded bg-zinc-50 p-2 text-xs text-zinc-700">
                  {data.quote_text}
                </pre>
              )}
            </Section>

            <Section title={`Amendments (${data.amendments.length})`}>
              {!data.amendments_available ? (
                <p className="text-xs text-amber-800">Amendment sidecar could not be read.</p>
              ) : data.amendments.length === 0 ? (
                <p className="text-xs text-zinc-400">No amendments captured for this lead.</p>
              ) : (
                <ul className="space-y-1">
                  {data.amendments.map((a) => (
                    <li key={a.amendment_id} className="rounded border border-zinc-200 p-2 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-mono">{a.amendment_id}</span>
                        <Badge tone={a.applied ? "green" : "amber"}>
                          {a.applied ? `applied ${fmtTs(a.applied_at)}` : "not applied"}
                        </Badge>
                      </div>
                      <p className="mt-1 text-zinc-600">
                        {a.raw_text_prefix}
                        {a.raw_text_truncated && <span className="text-zinc-400"> …(truncated)</span>}
                      </p>
                      <div className="mt-0.5 text-zinc-400">
                        {a.source ?? "?"} · {a.source_transport ?? "?"} · {fmtTs(a.captured_at)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            {data.followups.length > 0 && (
              <Section title="Follow-ups">
                <ul className="space-y-1 text-xs">
                  {data.followups.map((f, i) => (
                    <li key={`${f.followup_type}-${i}`} className="flex items-center justify-between">
                      <span>{f.followup_type || "followup"}</span>
                      <span className="text-zinc-500">
                        {f.status || "—"} · due {fmtTs(f.due_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <Section title="Conversation control">
              {!data.control.available ? (
                <p className="text-xs text-zinc-400">{data.control.note || "No control state available."}</p>
              ) : (
                <div className="space-y-1 text-xs text-zinc-600">
                  <div>
                    Stored <Badge tone={controlModeTone(data.control.stored_mode)}>{data.control.stored_mode}</Badge>{" "}
                    · effective{" "}
                    <Badge tone={controlModeTone(data.control.effective_mode)}>{data.control.effective_mode}</Badge>
                    {data.control.expired && <span className="ml-2 text-zinc-400">(window lapsed)</span>}
                  </div>
                  {data.control.reason && <div>Reason: {data.control.reason}</div>}
                  {data.control.actor && <div>Set by: {data.control.actor} · {fmtTs(data.control.since_ts)}</div>}
                  {data.control.note && <div className="text-zinc-400">{data.control.note}</div>}
                </div>
              )}
            </Section>

            <Section title="Audit timeline">
              {data.timeline.length === 0 ? (
                <p className="text-xs text-zinc-400">No audit rows reference this lead yet.</p>
              ) : (
                <ul className="space-y-1 text-xs">
                  {data.timeline.map((row, i) => (
                    <li key={`${row.ts}-${i}`} className="text-zinc-600">
                      <span className="text-zinc-400">{fmtTs(row.ts)}</span> ·{" "}
                      <span className="font-medium">{row.event}</span>
                      {row.detail && <span className="text-zinc-500"> — {row.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Owner actions">
              {actionError && <p className="mb-2 text-xs text-rose-700">{actionError}</p>}
              {decision.isSuccess && <p className="mb-2 text-xs text-emerald-700">Decision applied.</p>}

              {!data.approval_code && (
                <p className="mb-2 text-xs text-zinc-500">
                  This lead has no owner approval code yet — approve/reject become available once a
                  quote is drafted.
                </p>
              )}

              <div className="space-y-3">
                <div>
                  <label className="text-xs text-zinc-500" htmlFor="quote-text">
                    Quote text to send (optional — leave blank to render from the customer&apos;s finalized selection)
                  </label>
                  <textarea
                    id="quote-text"
                    className="mt-1 h-24 w-full rounded-md border border-zinc-300 p-2 text-xs"
                    value={quoteText}
                    onChange={(e) => setQuoteText(e.target.value)}
                    placeholder="Your quote for 120 guests…"
                  />
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={busy || !data.approval_code}
                    loading={decision.isPending}
                    onClick={() => {
                      if (!window.confirm("Approve this quote and send it to the customer?")) return;
                      decision.mutate({ decision: "approve", quote_text: quoteText });
                    }}
                  >
                    Approve &amp; send
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy || !data.approval_code}
                    onClick={() => setShowEdit((v) => !v)}
                  >
                    Request revision
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={busy || !data.approval_code}
                    onClick={() => {
                      const reason = window.prompt("Reason for rejecting this lead? (optional)") ?? "";
                      if (!window.confirm("Reject this lead?")) return;
                      setRejectReason(reason);
                      decision.mutate({ decision: "reject", reason });
                    }}
                  >
                    Reject
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    loading={hold.isPending}
                    onClick={() => {
                      if (data.on_hold) {
                        if (!window.confirm("Lift the hold on this lead?")) return;
                        hold.mutate({ on: false, reason: "" });
                        return;
                      }
                      const reason = window.prompt("Why put this lead on hold?") ?? "";
                      if (!reason.trim()) return;
                      hold.mutate({ on: true, reason: reason.trim() });
                    }}
                  >
                    {data.on_hold ? "Lift hold" : "Put on hold"}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy || data.amendments.every((a) => a.applied)}
                    loading={amend.isPending}
                    onClick={() => {
                      if (!window.confirm("Apply captured amendments to this lead?")) return;
                      amend.mutate();
                    }}
                  >
                    Apply amendments
                  </Button>
                </div>
                {rejectReason && decision.isSuccess && (
                  <p className="text-xs text-zinc-400">Recorded reason: {rejectReason}</p>
                )}

                {showEdit && (
                  <div className="rounded border border-zinc-200 p-2">
                    <label className="text-xs text-zinc-500" htmlFor="edit-text">
                      What should change? This is recorded as the owner&apos;s revision request.
                    </label>
                    <textarea
                      id="edit-text"
                      className="mt-1 h-20 w-full rounded-md border border-zinc-300 p-2 text-xs"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      placeholder="Drop the goat curry, add two veg trays…"
                    />
                    <div className="mt-2 flex gap-2">
                      <Button
                        size="sm"
                        disabled={busy || !editText.trim()}
                        loading={decision.isPending}
                        onClick={() => decision.mutate({ decision: "edit", edit_text: editText.trim() })}
                      >
                        Send revision request
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setShowEdit(false)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}

                <div className="text-xs text-zinc-400">
                  Approve, reject, revision and hold need a login code issued in the last 5 minutes.
                </div>
              </div>
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-zinc-100 pt-3">
      <h4 className="mb-2 text-sm font-semibold">{title}</h4>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className={cn(value === "—" && "text-zinc-400")}>{value}</dd>
    </>
  );
}

function QualificationChecklist({
  missing,
  missingLabels,
  answered,
  required,
  rounds,
  pending,
}: {
  missing: string[];
  missingLabels: string[];
  answered: number;
  required: number;
  rounds: number;
  pending: string[];
}) {
  const pct = required > 0 ? Math.round((answered / required) * 100) : 0;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="h-2 w-40 overflow-hidden rounded bg-zinc-100">
          <div
            className={cn("h-full", missing.length === 0 ? "bg-emerald-500" : "bg-amber-500")}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-xs text-zinc-500">
          {answered}/{required} answered · {rounds} question round(s)
        </span>
      </div>
      {missing.length === 0 ? (
        <p className="text-xs text-emerald-700">All required details captured.</p>
      ) : (
        <ul className="text-xs text-zinc-600">
          {missingLabels.map((label, i) => (
            <li key={missing[i]}>
              <span className="text-amber-700">○</span> {label}
              {pending.includes(missing[i]) && <span className="text-zinc-400"> (asked, awaiting reply)</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
