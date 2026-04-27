import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, type ApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

interface Employee {
  id: string;
  name: string;
  nickname?: string;
  role: string;
  phone: string;
  languages: string[];
  can_cover_roles: string[];
  status: string;
}

interface RosterData {
  location: { id: string; name: string; timezone: string };
  employees: Employee[];
  schedule: Record<string, unknown>;
}

export function Roster() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<RosterData>({ queryKey: ["roster"], queryFn: () => api.GET<RosterData>("/roster") });
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<Employee>>({});
  const [adding, setAdding] = useState(false);

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Employee> }) => api.PATCH(`/roster/employee/${id}`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roster"] }); setEditing(null); setForm({}); },
  });
  const add = useMutation({
    mutationFn: (body: Employee) => api.POST("/roster/employee", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roster"] }); setAdding(false); setForm({}); },
  });
  const terminate = useMutation({
    mutationFn: (id: string) => api.DELETE(`/roster/employee/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["roster"] }),
  });

  if (isLoading || !data) return <div className="p-8 text-zinc-500">Loading…</div>;
  void terminate; // ESLint placeholder; usage below in row buttons


  return (
    <div className="space-y-4">
      <CsvImport onSuccess={() => qc.invalidateQueries({ queryKey: ["roster"] })} />
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Roster</h2>
        <Button onClick={() => { setAdding(true); setForm({ status: "active", languages: ["en"], can_cover_roles: [] }); }}>
          + Add employee
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead className="bg-zinc-50 border-b border-zinc-200 text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="text-left px-4 py-2">ID</th>
                <th className="text-left px-4 py-2">Name</th>
                <th className="text-left px-4 py-2">Role</th>
                <th className="text-left px-4 py-2">Phone</th>
                <th className="text-left px-4 py-2">Can cover</th>
                <th className="text-left px-4 py-2">Status</th>
                <th className="text-right px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {data.employees.map((e) => {
                const isEditing = editing === e.id;
                const v: Partial<Employee> = isEditing ? { ...e, ...form } : e;
                return (
                  <tr key={e.id} className="border-b border-zinc-100 hover:bg-zinc-50">
                    <td className="px-4 py-2 font-mono text-xs">{e.id}</td>
                    <td className="px-4 py-2">{isEditing ? <Input value={v.name ?? ""} onChange={(ev) => setForm((f) => ({ ...f, name: ev.target.value }))} /> : e.name}</td>
                    <td className="px-4 py-2">{isEditing ? <Input value={v.role ?? ""} onChange={(ev) => setForm((f) => ({ ...f, role: ev.target.value }))} /> : e.role}</td>
                    <td className="px-4 py-2 font-mono text-xs">{isEditing ? <Input value={v.phone ?? ""} onChange={(ev) => setForm((f) => ({ ...f, phone: ev.target.value }))} /> : e.phone}</td>
                    <td className="px-4 py-2 text-xs">{isEditing ? <Input value={(v.can_cover_roles ?? []).join(",")} onChange={(ev) => setForm((f) => ({ ...f, can_cover_roles: ev.target.value.split(",").map((s) => s.trim()).filter(Boolean) }))} /> : e.can_cover_roles.join(", ")}</td>
                    <td className="px-4 py-2"><span className={e.status === "active" ? "text-green-700" : "text-zinc-500"}>{e.status}</span></td>
                    <td className="px-4 py-2 text-right">
                      {isEditing ? (
                        <>
                          <Button size="sm" onClick={() => patch.mutate({ id: e.id, body: form })} loading={patch.isPending}>Save</Button>
                          <Button size="sm" variant="ghost" onClick={() => { setEditing(null); setForm({}); }}>Cancel</Button>
                        </>
                      ) : (
                        <>
                          <Button size="sm" variant="outline" onClick={() => { setEditing(e.id); setForm({}); }}>Edit</Button>
                          {e.status === "active" && (
                            <Button size="sm" variant="ghost" onClick={() => { if (confirm(`Terminate ${e.name}?`)) terminate.mutate(e.id); }}>
                              Terminate
                            </Button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
              {adding && (
                <tr className="bg-amber-50">
                  <td className="px-4 py-2"><Input placeholder="e008" value={form.id ?? ""} onChange={(ev) => setForm((f) => ({ ...f, id: ev.target.value }))} /></td>
                  <td className="px-4 py-2"><Input placeholder="Name" value={form.name ?? ""} onChange={(ev) => setForm((f) => ({ ...f, name: ev.target.value }))} /></td>
                  <td className="px-4 py-2"><Input placeholder="role" value={form.role ?? ""} onChange={(ev) => setForm((f) => ({ ...f, role: ev.target.value }))} /></td>
                  <td className="px-4 py-2"><Input placeholder="+1..." value={form.phone ?? ""} onChange={(ev) => setForm((f) => ({ ...f, phone: ev.target.value }))} /></td>
                  <td className="px-4 py-2"><Input placeholder="cashier,floor" value={(form.can_cover_roles ?? []).join(",")} onChange={(ev) => setForm((f) => ({ ...f, can_cover_roles: ev.target.value.split(",").map((s) => s.trim()).filter(Boolean) }))} /></td>
                  <td className="px-4 py-2 text-xs">active</td>
                  <td className="px-4 py-2 text-right space-x-1">
                    <Button size="sm" onClick={() => add.mutate(form as Employee)} loading={add.isPending}>Save</Button>
                    <Button size="sm" variant="ghost" onClick={() => { setAdding(false); setForm({}); }}>Cancel</Button>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}


// ─── CSV bulk-import card ─────────────────────────────────────────────


function CsvImport({ onSuccess }: { onSuccess: () => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ imported: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null); setResult(null);

    if (!file.name.toLowerCase().endsWith(".csv")) {
      setError(`Expected a .csv file (got ${file.name})`);
      e.target.value = "";
      return;
    }
    if (file.size > 256_000) {
      setError(`File is ${(file.size / 1024).toFixed(0)} KB; max is 256 KB`);
      e.target.value = "";
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/roster/import-csv", {
        method: "POST",
        credentials: "include",
        body: fd,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = (body as { detail?: string })?.detail ?? res.statusText;
        if (res.status === 403) throw new Error("Fresh OTP required — log out and log back in within 5 min, then retry.");
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setResult(body as { imported: number });
      onSuccess();
    } catch (err) {
      const e2 = err as ApiError;
      setError(e2.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <Card>
      <CardHeader><CardTitle>Bulk import (CSV)</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-zinc-600">
          Replaces ALL employees with rows from a UTF-8 CSV. Required columns:
          <code className="mx-1">id, name, role, phone, can_cover_roles</code>.
          Lists pipe- or comma-separated. Cell starting with <code>= + - @</code> is rejected.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={onPick}
          disabled={busy}
          aria-label="Roster CSV upload"
          className="text-sm"
        />
        {busy && <p className="text-xs text-brand-700">Uploading…</p>}
        {result && <p className="text-xs text-green-700">✓ Imported {result.imported} employees</p>}
        {error && <p className="text-xs text-red-700 whitespace-pre-wrap">{error}</p>}
      </CardContent>
    </Card>
  );
}
