import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

interface DashboardData {
  components: { name: string; ok: boolean; detail: string }[];
  send_counter: { day: string; count: number; last_send_ts: string } | null;
  counter_resets_at: string | null;
  disabled: boolean;
  pending_active_count: number;
  last_decisions: Record<string, unknown>[];
}

export function Dashboard() {
  const { data, isLoading } = useQuery<DashboardData>({
    queryKey: ["dashboard"],
    queryFn: () => api.GET<DashboardData>("/dashboard"),
    refetchInterval: 10_000,
  });

  if (isLoading || !data) return <div className="p-8 text-zinc-500">Loading…</div>;

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Dashboard</h2>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {data.components.map((c) => (
          <Card key={c.name}>
            <CardContent className="flex items-center justify-between">
              <div>
                <div className="text-sm text-zinc-500">{c.name}</div>
                <div className="text-xs text-zinc-400">{c.detail}</div>
              </div>
              <span className={cn("size-3 rounded-full", c.ok ? "bg-green-500" : "bg-red-500")} />
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader><CardTitle>Today's send counter</CardTitle></CardHeader>
        <CardContent>
          {data.send_counter ? (
            <div>
              <div className="text-3xl font-bold">{data.send_counter.count}</div>
              <div className="text-xs text-zinc-500">since {data.send_counter.day}</div>
              {data.counter_resets_at && (
                <div className="text-xs text-zinc-500 mt-2">resets at {new Date(data.counter_resets_at).toLocaleString()}</div>
              )}
            </div>
          ) : (
            <div className="text-zinc-500">No outbound today.</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Last 5 decisions</CardTitle></CardHeader>
        <CardContent>
          {data.last_decisions.length === 0 ? (
            <div className="text-zinc-500 text-sm">Quiet so far.</div>
          ) : (
            <ul className="space-y-1 text-xs font-mono">
              {data.last_decisions.map((d, i) => (
                <li key={i} className="border-l-2 border-zinc-200 pl-2">
                  <span className="text-zinc-500">{String(d.ts)}</span>{" "}
                  <span className="text-brand-700">{String(d.type)}</span>{" "}
                  {d.proposal_id != null && <span className="text-zinc-700">{String(d.proposal_id)}</span>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
