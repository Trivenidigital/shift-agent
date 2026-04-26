import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

interface ConfigShape {
  customer: { name: string; timezone: string; languages: string[] };
  owner: { name: string; phone: string };
  limits: { max_outbound_per_day: number; max_outbound_per_minute: number };
  alerting: { pushover_user_key: string; pushover_app_token: string };
}

export function Config() {
  const qc = useQueryClient();
  const { data } = useQuery<ConfigShape>({ queryKey: ["config"], queryFn: () => api.GET<ConfigShape>("/config") });
  const [edit, setEdit] = useState<Record<string, string | number>>({});
  useEffect(() => { setEdit({}); }, [data]);

  const patch = useMutation({
    mutationFn: (fields: Record<string, unknown>) => api.PATCH("/config", { fields }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });
  const patchSensitive = useMutation({
    mutationFn: (fields: Record<string, unknown>) => api.PATCH("/config/sensitive", { fields }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
    onError: (e) => alert("Sensitive PATCH failed (need fresh OTP — log out and back in within 5 min): " + (e as Error).message),
  });

  if (!data) return <div className="p-8">Loading…</div>;

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Config</h2>

      <Card>
        <CardHeader><CardTitle>Operational limits</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <FieldNum label="Max outbound per day (sensitive)" value={data.limits.max_outbound_per_day} onChange={(v) => setEdit((e) => ({ ...e, "limits.max_outbound_per_day": v }))} />
          <FieldNum label="Max outbound per minute (sensitive)" value={data.limits.max_outbound_per_minute} onChange={(v) => setEdit((e) => ({ ...e, "limits.max_outbound_per_minute": v }))} />
          <Button size="sm" onClick={() => patchSensitive.mutate(edit)} loading={patchSensitive.isPending} disabled={Object.keys(edit).length === 0}>
            Save (requires fresh OTP)
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Owner</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <FieldText label="Owner name" value={data.owner.name} onChange={(v) => setEdit((e) => ({ ...e, "owner.name": v }))} />
          <FieldText label="Owner phone (sensitive — account takeover risk)" value={data.owner.phone} onChange={(v) => setEdit((e) => ({ ...e, "owner.phone": v }))} />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => {
              const nonSensitive = Object.fromEntries(Object.entries(edit).filter(([k]) => !["owner.phone"].includes(k)));
              if (Object.keys(nonSensitive).length) patch.mutate(nonSensitive);
              const sensitive = Object.fromEntries(Object.entries(edit).filter(([k]) => ["owner.phone"].includes(k)));
              if (Object.keys(sensitive).length) patchSensitive.mutate(sensitive);
            }} loading={patch.isPending || patchSensitive.isPending}>Save</Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Customer</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <FieldText label="Customer name" value={data.customer.name} onChange={(v) => setEdit((e) => ({ ...e, "customer.name": v }))} />
          <FieldText label="Timezone" value={data.customer.timezone} onChange={(v) => setEdit((e) => ({ ...e, "customer.timezone": v }))} />
          <Button size="sm" onClick={() => patch.mutate(edit)}>Save</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Alerting (Pushover, masked)</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div>User key: <code className="text-xs">{data.alerting.pushover_user_key}</code></div>
          <div>App token: <code className="text-xs">{data.alerting.pushover_app_token}</code></div>
          <p className="text-xs text-zinc-500">To rotate, edit /opt/shift-agent/config.yaml directly (Phase 3 will add UI).</p>
        </CardContent>
      </Card>
    </div>
  );
}

function FieldText({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs text-zinc-500 mb-1">{label}</label>
      <Input defaultValue={value} onBlur={(e) => onChange(e.target.value)} />
    </div>
  );
}

function FieldNum({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div>
      <label className="block text-xs text-zinc-500 mb-1">{label}</label>
      <Input type="number" defaultValue={value} onBlur={(e) => onChange(parseInt(e.target.value || "0", 10))} />
    </div>
  );
}
