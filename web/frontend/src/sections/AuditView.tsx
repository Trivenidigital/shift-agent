import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/Card";

interface AuditEntry { ts: string; event: string; actor: string; ip: string; ua: string; details: Record<string, unknown> }

export function AuditView() {
  const { data = [] } = useQuery<AuditEntry[]>({ queryKey: ["audit"], queryFn: () => api.GET<AuditEntry[]>("/audit?limit=300") });
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Cockpit audit</h2>
      <Card>
        <CardContent className="p-0">
          <table className="w-full text-xs font-mono">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr><th className="text-left px-2 py-1">Time</th><th className="text-left px-2 py-1">Event</th><th className="text-left px-2 py-1">Actor</th><th className="text-left px-2 py-1">IP</th><th className="text-left px-2 py-1">Details</th></tr>
            </thead>
            <tbody>
              {data.map((e, i) => (
                <tr key={i} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <td className="px-2 py-1 text-zinc-500">{e.ts}</td>
                  <td className="px-2 py-1 text-brand-700">{e.event}</td>
                  <td className="px-2 py-1">{e.actor}</td>
                  <td className="px-2 py-1 text-zinc-500">{e.ip}</td>
                  <td className="px-2 py-1">{JSON.stringify(e.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
