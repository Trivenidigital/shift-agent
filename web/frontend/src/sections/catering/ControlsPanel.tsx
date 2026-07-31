import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge, ErrorState, Skeleton, controlModeTone, fmtTs } from "./shared";
import type { ControlsResponse, FlagValue } from "./types";

// Read-only mirror of `catering-control-status`. The controls live in four
// different stores, so this is the one place that answers "what is currently
// allowed to talk to customers, and what is muted".

export function ControlsPanel() {
  const { data, isLoading, isError, error } = useQuery<ControlsResponse>({
    queryKey: ["catering-controls"],
    queryFn: () => api.GET<ControlsResponse>("/catering/controls"),
    refetchInterval: 30_000,
  });

  if (isLoading) return <Card><CardContent><Skeleton rows={6} /></CardContent></Card>;
  if (isError) return <ErrorState error={error} what="the control snapshot" />;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Kill switch</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {data.kill_switch_engaged ? (
            <p className="text-rose-700">
              <span className="font-medium">ENGAGED</span> — every outbound send is refused.
              {data.kill_switch_set_at && <span className="text-zinc-500"> Set {fmtTs(data.kill_switch_set_at)}.</span>}
            </p>
          ) : (
            <p className="text-emerald-700">Clear — sends are not blocked by the kill switch.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Automation-control kernel</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs">
          <FlagTable flags={data.kernel_flags} />
          <div>
            <div className="mb-1 font-medium text-zinc-700">Allowlists</div>
            <ul className="space-y-1">
              {Object.entries(data.kernel_allowlists).map(([name, summary]) => (
                <li key={name} className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-zinc-500">{name}</span>
                  <Badge tone={summary.mode === "wildcard" ? "green" : summary.mode === "empty" ? "red" : "blue"}>
                    {summary.mode}
                  </Badge>
                  <span className="text-zinc-600">{summary.effect}</span>
                  {summary.source === null && <span className="text-zinc-400">(not readable from the cockpit)</span>}
                </li>
              ))}
            </ul>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Per-conversation modes</CardTitle>
          <span className="text-xs text-zinc-500">{data.suppressed_count} suppressed</span>
        </CardHeader>
        <CardContent className="p-0">
          {!data.conversations_available ? (
            <p className="p-5 text-sm text-amber-800">The automation-control store could not be read.</p>
          ) : data.conversations.length === 0 ? (
            <p className="p-5 text-sm text-zinc-500">
              No conversation has a stored mode — every chat runs normally.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-b border-zinc-200 bg-zinc-50">
                  <tr className="text-left text-zinc-500">
                    <th className="px-3 py-1.5">Conversation</th>
                    <th className="px-3 py-1.5">Stored</th>
                    <th className="px-3 py-1.5">Effective</th>
                    <th className="px-3 py-1.5">Set by</th>
                    <th className="px-3 py-1.5">Reason</th>
                    <th className="px-3 py-1.5">Since</th>
                  </tr>
                </thead>
                <tbody>
                  {data.conversations.map((row) => (
                    <tr key={row.chat_key_hash} className="border-b border-zinc-100">
                      <td className="px-3 py-1.5 font-mono text-zinc-500">
                        {row.chat_key_hash.slice(0, 12)}…
                      </td>
                      <td className="px-3 py-1.5">
                        <Badge tone={controlModeTone(row.stored_mode)}>{row.stored_mode}</Badge>
                      </td>
                      <td className="px-3 py-1.5">
                        <Badge tone={controlModeTone(row.effective_mode)}>{row.effective_mode}</Badge>
                        {row.expired && <span className="ml-1 text-zinc-400">(lapsed)</span>}
                      </td>
                      <td className="px-3 py-1.5 text-zinc-500">{row.actor || "—"}</td>
                      <td className="px-3 py-1.5 text-zinc-500">{row.reason || "—"}</td>
                      <td className="px-3 py-1.5 text-zinc-400">{fmtTs(row.since_ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Leads on hold ({data.held_leads.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {data.held_leads.length === 0 ? (
            <p className="text-sm text-zinc-500">No lead is on hold.</p>
          ) : (
            <ul className="space-y-1 text-xs">
              {data.held_leads.map((lead) => (
                <li key={lead.lead_id} className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-brand-700">{lead.lead_id}</span>
                  <Badge tone="amber">{lead.status}</Badge>
                  <span className="text-zinc-600">{lead.hold_reason || "no reason recorded"}</span>
                  <span className="text-zinc-400">{fmtTs(lead.hold_set_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Follow-ups</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {data.followups_enabled === null ? (
            <p className="text-zinc-500">Unknown — the agent config is not readable from the cockpit.</p>
          ) : data.followups_enabled ? (
            <p className="text-emerald-700">Enabled in the agent config.</p>
          ) : (
            <p className="text-zinc-500">Disabled in the agent config.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Transport, containment and budget flags</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs">
          <p className="text-zinc-500">
            These are read from the agent&apos;s environment. The cockpit runs as a separate service, so
            a flag it cannot see is reported as unknown rather than as &quot;off&quot;.
          </p>
          <FlagTable flags={data.containment_flags} />
          <FlagTable flags={data.budget_flags} />
        </CardContent>
      </Card>

      {data.degraded.length > 0 && (
        <Card>
          <CardContent className="space-y-1 text-xs text-amber-800">
            <div className="font-medium">Read problems</div>
            {data.degraded.map((reason) => (
              <div key={reason}>{reason}</div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function FlagTable({ flags }: { flags: Record<string, FlagValue> }) {
  const entries = Object.entries(flags);
  if (entries.length === 0) return null;
  return (
    <table className="w-full">
      <tbody>
        {entries.map(([name, flag]) => (
          <tr key={name} className="border-b border-zinc-100">
            <td className="py-1 font-mono text-zinc-500">{name}</td>
            <td className="py-1 text-right">
              {!flag.known ? (
                <span className="text-zinc-400">unknown (env not readable)</span>
              ) : (
                <>
                  <span className="font-mono">{flag.value || "(empty)"}</span>
                  <span className="ml-2 text-zinc-400">{flag.source}</span>
                </>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
