import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

interface DecisionEntry {
  ts: string;
  type: string;
  proposal_id: string | null;
  extras: Record<string, unknown>;
}

export function Decisions() {
  const [type, setType] = useState("");
  const [pid, setPid] = useState("");
  const { data = [] } = useQuery<DecisionEntry[]>({
    queryKey: ["decisions", type, pid],
    queryFn: () => {
      const qs = new URLSearchParams();
      if (type) qs.set("type", type);
      if (pid) qs.set("proposal_id", pid);
      qs.set("limit", "200");
      return api.GET<DecisionEntry[]>(`/decisions?${qs}`);
    },
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Decisions log</h2>
      <Card>
        <CardContent className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
          <div><label className="block text-xs text-zinc-500">Type</label><Input value={type} onChange={(e) => setType(e.target.value)} placeholder="proposal_created" /></div>
          <div><label className="block text-xs text-zinc-500">Proposal ID</label><Input value={pid} onChange={(e) => setPid(e.target.value)} placeholder="P0006" /></div>
          <Button variant="outline" onClick={() => window.location.assign("/api/decisions.csv")}>Export CSV (fresh OTP)</Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <table className="w-full text-xs font-mono">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr><th className="text-left px-2 py-1">Time</th><th className="text-left px-2 py-1">Type</th><th className="text-left px-2 py-1">Proposal</th><th className="text-left px-2 py-1">Extras</th></tr>
            </thead>
            <tbody>
              {data.map((d, i) => (
                <tr key={i} className="border-b border-zinc-100 hover:bg-zinc-50">
                  <td className="px-2 py-1 text-zinc-500">{d.ts}</td>
                  <td className="px-2 py-1 text-brand-700">{d.type}</td>
                  <td className="px-2 py-1">{d.proposal_id ?? "—"}</td>
                  <td className="px-2 py-1 text-zinc-700">{JSON.stringify(d.extras)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
