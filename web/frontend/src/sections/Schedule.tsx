import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

interface ScheduleResp { schedule: Record<string, { employee_id: string; shift: string; role: string }[]> }

function todayPlus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function Schedule() {
  const qc = useQueryClient();
  const [from, setFrom] = useState(todayPlus(0));
  const [to, setTo] = useState(todayPlus(7));

  const { data } = useQuery<ScheduleResp>({
    queryKey: ["schedule", from, to],
    queryFn: () => api.GET<ScheduleResp>(`/schedule?from=${from}&to=${to}`),
  });

  const put = useMutation({
    mutationFn: ({ date, entries }: { date: string; entries: { employee_id: string; shift: string; role: string }[] }) =>
      api.PUT(`/schedule/${date}`, { entries }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedule"] }),
  });

  const days = data ? Object.keys(data.schedule).sort() : [];

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Schedule</h2>

      <Card>
        <CardContent className="flex gap-3 items-end">
          <div><label className="block text-xs text-zinc-500">From</label><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
          <div><label className="block text-xs text-zinc-500">To</label><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div>
        </CardContent>
      </Card>

      {days.length === 0 && <Card><CardContent className="text-zinc-500">No scheduled shifts in this window.</CardContent></Card>}

      {days.map((d) => (
        <DayCard key={d} date={d} entries={data!.schedule[d]} onSave={(entries) => put.mutate({ date: d, entries })} pending={put.isPending} />
      ))}
    </div>
  );
}

function DayCard({ date, entries, onSave, pending }: { date: string; entries: { employee_id: string; shift: string; role: string }[]; onSave: (e: typeof entries) => void; pending: boolean }) {
  const [draft, setDraft] = useState(entries);
  return (
    <Card>
      <CardHeader><CardTitle>{date}</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        {draft.map((e, i) => (
          <div key={i} className="grid grid-cols-12 gap-2 items-center">
            <Input className="col-span-3" value={e.employee_id} onChange={(ev) => setDraft((d) => d.map((x, j) => j === i ? { ...x, employee_id: ev.target.value } : x))} placeholder="employee_id" />
            <Input className="col-span-3" value={e.shift} onChange={(ev) => setDraft((d) => d.map((x, j) => j === i ? { ...x, shift: ev.target.value } : x))} placeholder="09:00-17:00" />
            <Input className="col-span-3" value={e.role} onChange={(ev) => setDraft((d) => d.map((x, j) => j === i ? { ...x, role: ev.target.value } : x))} placeholder="role" />
            <Button className="col-span-2" size="sm" variant="ghost" onClick={() => setDraft((d) => d.filter((_, j) => j !== i))}>Remove</Button>
          </div>
        ))}
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setDraft((d) => [...d, { employee_id: "", shift: "", role: "" }])}>+ Add row</Button>
          <Button size="sm" onClick={() => onSave(draft)} loading={pending}>Save day</Button>
        </div>
      </CardContent>
    </Card>
  );
}
