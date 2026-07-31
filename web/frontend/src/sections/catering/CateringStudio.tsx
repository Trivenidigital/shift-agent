import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { ControlsPanel } from "./ControlsPanel";
import { FollowupsPanel } from "./FollowupsPanel";
import { LeadDetailDrawer } from "./LeadDetailDrawer";
import { MenuPricingPanel } from "./MenuPricingPanel";
import {
  Badge,
  DegradedBanner,
  ErrorState,
  Skeleton,
  Stat,
  dollars,
  fmtTs,
  leadStatusTone,
} from "./shared";
import type { CateringDashboard, LeadListResponse } from "./types";

type Tab = "overview" | "leads" | "menu" | "controls" | "followups";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "leads", label: "Leads" },
  { id: "menu", label: "Menu & Pricing" },
  { id: "controls", label: "Controls" },
  { id: "followups", label: "Follow-ups" },
];

export function CateringStudio() {
  const [tab, setTab] = useState<Tab>("overview");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedLead, setSelectedLead] = useState<string | null>(null);

  const dashboard = useQuery<CateringDashboard>({
    queryKey: ["catering-dashboard"],
    queryFn: () => api.GET<CateringDashboard>("/catering/dashboard"),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Catering Studio</h2>
          <p className="text-sm text-zinc-500">
            Leads, quotes, menu and pricing, and everything that controls whether the catering agent
            may talk to customers.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {TABS.map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm",
                tab === item.id
                  ? "border-brand-600 bg-brand-50 text-brand-700"
                  : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "overview" && (
        <OverviewTab
          query={dashboard}
          onDrillDown={(status) => { setStatusFilter(status); setTab("leads"); }}
        />
      )}

      {tab === "leads" && (
        <LeadsTab
          statusFilter={statusFilter}
          onStatusFilter={setStatusFilter}
          onSelect={setSelectedLead}
        />
      )}

      {tab === "menu" && <MenuPricingPanel />}
      {tab === "controls" && <ControlsPanel />}
      {tab === "followups" && <FollowupsPanel />}

      {selectedLead && (
        <LeadDetailDrawer leadId={selectedLead} onClose={() => setSelectedLead(null)} />
      )}
    </div>
  );
}

function OverviewTab({
  query,
  onDrillDown,
}: {
  query: ReturnType<typeof useQuery<CateringDashboard>>;
  onDrillDown: (status: string) => void;
}) {
  const { data, isLoading, isError, error } = query;
  if (isLoading) return <Card><CardContent><Skeleton rows={5} /></CardContent></Card>;
  if (isError) return <ErrorState error={error} what="the catering dashboard" />;
  if (!data) return null;

  const { counts, readiness } = data;
  return (
    <div className="space-y-5">
      <DegradedBanner reasons={data.degraded} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
        <button onClick={() => onDrillDown("NEW")} className="text-left">
          <Stat label="New" value={counts.new_leads} />
        </button>
        <button onClick={() => onDrillDown("QUALIFYING")} className="text-left">
          <Stat label="Qualifying" value={counts.qualifying} />
        </button>
        <button onClick={() => onDrillDown("AWAITING_OWNER_APPROVAL")} className="text-left">
          <Stat
            label="Awaiting you"
            value={counts.awaiting_owner_approval}
            tone={counts.awaiting_owner_approval > 0 ? "warn" : "default"}
          />
        </button>
        <button onClick={() => onDrillDown("SENT_TO_CUSTOMER")} className="text-left">
          <Stat label="Proposals sent" value={counts.proposals_sent} />
        </button>
        <Stat label="Follow-ups due" value={counts.followups_due} tone={counts.followups_due > 0 ? "warn" : "default"} />
        <Stat label="Booked this month" value={counts.booked_this_month} tone="good" />
        <Stat label="Lost this month" value={counts.lost_this_month} />
      </div>

      <Card>
        <CardContent className="space-y-2">
          <h3 className="text-sm font-semibold">Readiness</h3>
          <ul className="space-y-1 text-sm">
            <ReadinessLine
              ok={readiness.pricebook_present && !readiness.pricebook_placeholder}
              warn={readiness.pricebook_placeholder}
              label="Pricebook"
              detail={
                !readiness.pricebook_present
                  ? "not imported — quotes cannot be priced"
                  : readiness.pricebook_placeholder
                    ? `v${readiness.pricebook_version} is PLACEHOLDER data — quotes will not be delivered`
                    : `v${readiness.pricebook_version} in effect`
              }
            />
            <ReadinessLine
              ok={readiness.menu_present}
              label="Menu"
              detail={
                readiness.menu_present
                  ? `v${readiness.menu_version} · ${readiness.menu_item_count} item(s)`
                  : "no menu on file"
              }
            />
            <ReadinessLine
              ok={!readiness.kill_switch_engaged}
              label="Kill switch"
              detail={readiness.kill_switch_engaged ? "ENGAGED — all sends refused" : "clear"}
            />
            <ReadinessLine
              ok={readiness.automation_kernel_armed}
              label="Automation-control kernel"
              detail={
                readiness.automation_kernel_armed_source === null
                  ? "unknown (env not readable from the cockpit)"
                  : readiness.automation_kernel_armed
                    ? `armed (${readiness.automation_kernel_armed_source})`
                    : `not armed (${readiness.automation_kernel_armed_source})`
              }
              unknown={readiness.automation_kernel_armed_source === null}
            />
            {/* Follow-ups being off is a configuration choice, not a fault —
                it gets a neutral mark, unlike a missing pricebook. */}
            <ReadinessLine
              ok={readiness.followups_enabled === true}
              label="Follow-ups"
              detail={
                readiness.followups_enabled === null
                  ? "unknown (agent config not readable)"
                  : readiness.followups_enabled
                    ? "enabled"
                    : "disabled in the agent config"
              }
              unknown={readiness.followups_enabled === null}
              neutral={readiness.followups_enabled === false}
            />
            <ReadinessLine
              ok={counts.on_hold === 0}
              warn={counts.on_hold > 0}
              label="Leads on hold"
              detail={counts.on_hold === 0 ? "none" : `${counts.on_hold} lead(s) held`}
            />
          </ul>
          <p className="pt-1 text-xs text-zinc-400">Snapshot taken {fmtTs(data.generated_at)}.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function ReadinessLine({
  ok,
  warn,
  unknown,
  neutral,
  label,
  detail,
}: {
  ok: boolean;
  warn?: boolean;
  unknown?: boolean;
  neutral?: boolean;
  label: string;
  detail: string;
}) {
  const mark = unknown ? "?" : ok ? "✓" : neutral ? "·" : warn ? "⚠" : "✕";
  const tone =
    unknown || neutral
      ? "text-zinc-400"
      : ok
        ? "text-emerald-600"
        : warn
          ? "text-amber-600"
          : "text-rose-600";
  return (
    <li className="flex items-baseline gap-2">
      <span className={cn("w-4 font-semibold", tone)}>{mark}</span>
      <span className="w-52 shrink-0 text-zinc-700">{label}</span>
      <span className="text-zinc-500">{detail}</span>
    </li>
  );
}

function LeadsTab({
  statusFilter,
  onStatusFilter,
  onSelect,
}: {
  statusFilter: string;
  onStatusFilter: (s: string) => void;
  onSelect: (leadId: string) => void;
}) {
  const { data, isLoading, isError, error } = useQuery<LeadListResponse>({
    queryKey: ["catering-leads", statusFilter],
    queryFn: () =>
      api.GET<LeadListResponse>(
        statusFilter
          ? `/catering/leads?status=${encodeURIComponent(statusFilter)}`
          : "/catering/leads",
      ),
    refetchInterval: 30_000,
  });

  if (isLoading) return <Card><CardContent><Skeleton rows={6} /></CardContent></Card>;
  if (isError) return <ErrorState error={error} what="the lead list" />;
  if (!data) return null;

  const statuses = Object.keys(data.status_counts).sort();

  return (
    <div className="space-y-3">
      {!data.available && (
        <Card>
          <CardContent className="text-sm text-rose-700">
            ⚠ The catering lead store could not be read: {data.degraded}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => onStatusFilter("")}
          className={cn(
            "rounded border px-2 py-1 text-xs",
            statusFilter === "" ? "border-brand-600 bg-brand-50 text-brand-700" : "border-zinc-200 bg-white text-zinc-600",
          )}
        >
          All
        </button>
        {statuses.map((status) => (
          <button
            key={status}
            onClick={() => onStatusFilter(status)}
            className={cn(
              "rounded border px-2 py-1 text-xs",
              statusFilter === status
                ? "border-brand-600 bg-brand-50 text-brand-700"
                : "border-zinc-200 bg-white text-zinc-600",
            )}
          >
            {status} ({data.status_counts[status]})
          </button>
        ))}
      </div>

      {data.leads.length === 0 ? (
        <Card>
          <CardContent className="text-sm text-zinc-500">
            {statusFilter
              ? `No leads with status ${statusFilter}.`
              : "No catering leads yet. Inquiries arriving on WhatsApp appear here."}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <table className="w-full text-xs">
              <thead className="border-b border-zinc-200 bg-zinc-50">
                <tr className="text-left text-zinc-500">
                  <th className="px-3 py-1.5">Lead</th>
                  <th className="px-3 py-1.5">Customer</th>
                  <th className="px-3 py-1.5">Event date</th>
                  <th className="px-3 py-1.5 text-right">Guests</th>
                  <th className="px-3 py-1.5">Status</th>
                  <th className="px-3 py-1.5">Qualification</th>
                  <th className="px-3 py-1.5 text-right">Est. value</th>
                  <th className="px-3 py-1.5">Next action</th>
                  <th className="px-3 py-1.5">Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.leads.map((lead) => (
                  <tr
                    key={lead.lead_id}
                    onClick={() => onSelect(lead.lead_id)}
                    className="cursor-pointer border-b border-zinc-100 hover:bg-brand-50"
                  >
                    <td className="px-3 py-1.5 font-mono text-brand-700">{lead.lead_id}</td>
                    <td className="px-3 py-1.5">
                      {lead.customer_name ?? "—"}
                      <div className="font-mono text-[11px] text-zinc-400">{lead.customer_phone_masked}</div>
                    </td>
                    <td className="px-3 py-1.5">{lead.event_date ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right">{lead.headcount ?? "—"}</td>
                    <td className="px-3 py-1.5">
                      <Badge tone={leadStatusTone(lead.status)}>{lead.status}</Badge>
                      {lead.on_hold && <span className="ml-1"><Badge tone="amber">hold</Badge></span>}
                    </td>
                    <td className="px-3 py-1.5">
                      <QualificationBar
                        answered={lead.qualification.answered_count}
                        required={lead.qualification.required_count}
                        complete={lead.qualification.complete}
                      />
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {dollars(lead.latest_quote_total_usd)}
                    </td>
                    <td className="px-3 py-1.5 text-zinc-600">{lead.next_action}</td>
                    <td className="px-3 py-1.5 text-zinc-400">{fmtTs(lead.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function QualificationBar({
  answered,
  required,
  complete,
}: {
  answered: number;
  required: number;
  complete: boolean;
}) {
  const pct = required > 0 ? Math.round((answered / required) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded bg-zinc-100">
        <div
          className={cn("h-full", complete ? "bg-emerald-500" : "bg-amber-500")}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-zinc-500">{answered}/{required}</span>
    </div>
  );
}
