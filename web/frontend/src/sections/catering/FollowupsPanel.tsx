import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge, ErrorState, Skeleton, fmtTs } from "./shared";
import type { FollowupsResponse } from "./types";

// The follow-up store is owned by M5 and may not exist yet. An absent file is
// an empty state with an explanation, not an error — the panel says which.

export function FollowupsPanel() {
  const { data, isLoading, isError, error } = useQuery<FollowupsResponse>({
    queryKey: ["catering-followups"],
    queryFn: () => api.GET<FollowupsResponse>("/catering/followups"),
  });

  if (isLoading) return <Card><CardContent><Skeleton rows={4} /></CardContent></Card>;
  if (isError) return <ErrorState error={error} what="follow-ups" />;
  if (!data) return null;

  if (!data.available) {
    return (
      <Card>
        <CardContent className="text-sm text-zinc-500">
          {data.degraded
            ? `Follow-up store could not be read: ${data.degraded}`
            : (data.unavailable_reason ?? "Follow-ups are not available.")}
        </CardContent>
      </Card>
    );
  }

  if (data.followups.length === 0) {
    return (
      <Card>
        <CardContent className="text-sm text-zinc-500">
          No follow-ups scheduled. They appear here once a lead reaches a stage that schedules one.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>Follow-ups</CardTitle>
        <span className="text-xs text-zinc-500">
          {data.due_count} due · {data.followups.length} total
        </span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-b border-zinc-200 bg-zinc-50">
              <tr className="text-left text-zinc-500">
                <th className="px-3 py-1.5">Lead</th>
                <th className="px-3 py-1.5">Type</th>
                <th className="px-3 py-1.5">Due</th>
                <th className="px-3 py-1.5">Status</th>
                <th className="px-3 py-1.5">Approval code</th>
              </tr>
            </thead>
            <tbody>
              {data.followups.map((row, i) => (
                <tr key={`${row.lead_id}-${row.followup_type}-${i}`} className="border-b border-zinc-100">
                  <td className="px-3 py-1.5 font-mono text-brand-700">{row.lead_id || "—"}</td>
                  <td className="px-3 py-1.5">{row.followup_type || "—"}</td>
                  <td className="px-3 py-1.5 text-zinc-500">{fmtTs(row.due_at)}</td>
                  <td className="px-3 py-1.5">
                    <Badge tone={row.status === "sent" ? "green" : row.status === "cancelled" ? "neutral" : "amber"}>
                      {row.status || "scheduled"}
                    </Badge>
                  </td>
                  <td className="px-3 py-1.5 font-mono text-zinc-500">{row.approval_code ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
