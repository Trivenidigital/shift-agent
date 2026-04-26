import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { STATUS_BADGE, TERMINAL_STATUSES, type ProposalStatus } from "@/lib/proposalStatus";
import { cn } from "@/lib/cn";

interface ProposalView {
  proposal_id: string;
  code: string;
  status: ProposalStatus;
  absent_employee_id: string;
  candidate_employee_id: string | null;
  absent_date: string;
  absent_shift: string;
  absent_role: string;
  absent_reason: string;
  created_ts: string;
  last_updated_ts: string;
  outbound_message_id: string | null;
}

export function Pending() {
  const qc = useQueryClient();
  const [includeTerminal, setIncludeTerminal] = useState(false);
  const { data = [] } = useQuery<ProposalView[]>({
    queryKey: ["pending", includeTerminal],
    queryFn: () => api.GET<ProposalView[]>(`/pending?include_terminal=${includeTerminal}`),
    refetchInterval: 10_000,
  });

  const cancel = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.POST(`/pending/${id}/cancel`, { reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pending"] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Pending proposals</h2>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeTerminal} onChange={(e) => setIncludeTerminal(e.target.checked)} />
          Show terminal (closed) proposals
        </label>
      </div>

      <div className="space-y-2">
        {data.length === 0 && <Card><CardContent className="text-zinc-500 text-sm">No proposals match.</CardContent></Card>}
        {data.map((p) => {
          const badge = STATUS_BADGE[p.status];
          return (
            <Card key={p.proposal_id}>
              <CardContent className="space-y-2">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-zinc-600">{p.proposal_id}</span>
                  <span className="font-mono text-xs px-2 py-0.5 rounded bg-zinc-100">{p.code}</span>
                  <span className={cn("text-xs px-2 py-0.5 rounded inline-flex items-center gap-1", badge.color)}>
                    {badge.spin && <span className="size-2 animate-spin rounded-full border border-current border-t-transparent" />}
                    {badge.label}
                  </span>
                </div>
                <div className="text-sm">
                  <strong>{p.absent_employee_id}</strong> out {p.absent_date} ({p.absent_reason}) — needs {p.absent_role} cover{" "}
                  {p.candidate_employee_id && <>→ <strong>{p.candidate_employee_id}</strong></>}
                </div>
                <div className="text-xs text-zinc-500 font-mono">
                  created {p.created_ts} · last updated {p.last_updated_ts}
                  {p.outbound_message_id && <> · msg_id={p.outbound_message_id}</>}
                </div>
                {!TERMINAL_STATUSES.has(p.status) && (
                  <CancelButton onCancel={(reason) => cancel.mutate({ id: p.proposal_id, reason })} pending={cancel.isPending} />
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function CancelButton({ onCancel, pending }: { onCancel: (reason: string) => void; pending: boolean }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open)
    return <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Cancel proposal</Button>;
  return (
    <div className="flex gap-2 items-center">
      <Input placeholder="Reason (≥5 chars)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button size="sm" variant="destructive" onClick={() => onCancel(reason)} disabled={reason.length < 5} loading={pending}>
        Confirm cancel
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Back</Button>
    </div>
  );
}
