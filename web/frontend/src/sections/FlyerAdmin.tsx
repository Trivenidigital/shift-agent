import { useMemo, useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FileUp, Gift, Megaphone, RefreshCw, Search, Send, Users } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/cn";

type Tab = "overview" | "customers" | "campaigns" | "projects" | "guests";

interface FlyerSummary {
  segments: Record<string, number>;
  total_customers: number;
  active_projects: number;
  stuck_projects: number;
  guest_orders: number;
  campaign_asset: { path: string; exists: boolean };
}

interface FlyerCustomer {
  customer_id: string;
  business_name: string;
  business_address: string;
  category: string;
  status: string;
  plan_id: string;
  preferred_language: string;
  public_phone: string;
  business_whatsapp_number: string;
  authorized_request_numbers: string[];
  usage_used: number;
  usage_remaining: number | null;
  trial_bonus_flyers: number;
  project_count: number;
  updated_at: string;
}

interface FlyerProject {
  project_id: string;
  status: string;
  customer_phone: string;
  updated_at: string;
  raw_request: string;
  concepts?: unknown[];
  final_asset_ids?: string[];
}

interface GuestOrder {
  order_id: string;
  sender_phone: string;
  status: string;
  flyer_count_purchased: number;
  flyer_count_used: number;
  unit_price_cents: number;
  updated_at: string;
}

interface CampaignPreview {
  valid_targets: string[];
  invalid: { row: number; value: string; error: string }[];
  duplicate_count: number;
}

interface CampaignSendResult {
  sent: number;
  failed: number;
  targets: string[];
  dry_run: boolean;
  results?: { ok: boolean; target: string; returncode?: number; error?: string; status?: string }[];
}

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "customers", label: "Customers" },
  { id: "campaigns", label: "Campaigns" },
  { id: "projects", label: "Projects" },
  { id: "guests", label: "One-time" },
];

function Stat({ label, value, tone = "default" }: { label: string; value: number | string; tone?: "default" | "warn" | "good" }) {
  return (
    <div className="rounded-md border border-zinc-200 bg-white px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold", tone === "warn" && "text-amber-700", tone === "good" && "text-emerald-700")}>{value}</div>
    </div>
  );
}

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "green" | "amber" | "red" | "blue" }) {
  const cls = {
    neutral: "bg-zinc-100 text-zinc-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-800",
    red: "bg-red-50 text-red-700",
    blue: "bg-brand-50 text-brand-700",
  }[tone];
  return <span className={cn("rounded px-2 py-0.5 text-xs font-medium", cls)}>{children}</span>;
}

function categoryTone(category: string): "neutral" | "green" | "amber" | "red" | "blue" {
  if (category === "paid") return "green";
  if (category === "free_trial") return "blue";
  if (category === "payment_pending") return "amber";
  if (category === "inactive") return "red";
  return "neutral";
}

function mutationErrorMessage(error: unknown): string {
  if (!error) return "";
  const err = error as Error & { status?: number };
  if (err.status === 403) return "Session is stale. Click Send login code again, then retry Send WhatsApp now.";
  if (err.status === 401) return "Session expired. Click Send login code again, then retry.";
  return err.message || "Request failed.";
}

async function postCsv(file: File): Promise<CampaignPreview> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/flyer/campaigns/preview-csv", { method: "POST", credentials: "include", body: form });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText);
  return res.json();
}

export function FlyerAdmin() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [query, setQuery] = useState("");
  const [segment, setSegment] = useState("");
  const [targetsText, setTargetsText] = useState("");
  const [reason, setReason] = useState("operator dashboard action");
  const [selectedCustomer, setSelectedCustomer] = useState<FlyerCustomer | null>(null);
  const [extensionCount, setExtensionCount] = useState(1);

  const { data: summary } = useQuery<FlyerSummary>({
    queryKey: ["flyer-summary"],
    queryFn: () => api.GET<FlyerSummary>("/flyer/summary"),
    refetchInterval: 15_000,
  });
  const { data: customerData } = useQuery<{ customers: FlyerCustomer[] }>({
    queryKey: ["flyer-customers", query, segment],
    queryFn: () => api.GET<{ customers: FlyerCustomer[] }>(`/flyer/customers?query=${encodeURIComponent(query)}&segment=${encodeURIComponent(segment)}`),
  });
  const { data: projectsData } = useQuery<{ projects: FlyerProject[] }>({
    queryKey: ["flyer-projects"],
    queryFn: () => api.GET<{ projects: FlyerProject[] }>("/flyer/projects"),
    refetchInterval: 15_000,
  });
  const { data: guestData } = useQuery<{ orders: GuestOrder[] }>({
    queryKey: ["flyer-guests"],
    queryFn: () => api.GET<{ orders: GuestOrder[] }>("/flyer/guest-orders"),
  });

  const customers = customerData?.customers ?? [];
  const projects = projectsData?.projects ?? [];
  const guests = guestData?.orders ?? [];

  const preview = useMutation({
    mutationFn: () => api.POST<CampaignPreview>("/flyer/campaigns/preview", { targets_text: targetsText, reason, dry_run: true }),
  });
  const csvPreview = useMutation({ mutationFn: postCsv });
  const sendCampaign = useMutation({
    mutationFn: (dryRun: boolean) => api.POST<CampaignSendResult>("/flyer/campaigns/send", { targets_text: targetsText, reason, dry_run: dryRun }),
  });
  const resetTrial = useMutation({
    mutationFn: (customerId: string) => api.POST(`/flyer/customers/${customerId}/reset-trial`, { reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flyer-summary"] });
      qc.invalidateQueries({ queryKey: ["flyer-customers"] });
    },
  });
  const extendTrial = useMutation({
    mutationFn: (customerId: string) => api.POST(`/flyer/customers/${customerId}/extend-trial`, { reason, extra_flyers: extensionCount }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flyer-summary"] });
      qc.invalidateQueries({ queryKey: ["flyer-customers"] });
    },
  });

  const latestProjectByPhone = useMemo(() => {
    const out = new Map<string, FlyerProject>();
    for (const project of projects) {
      if (!out.has(project.customer_phone)) out.set(project.customer_phone, project);
    }
    return out;
  }, [projects]);

  const campaignResult = sendCampaign.data;
  const previewData = preview.data ?? csvPreview.data;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Flyer Studio</h2>
          <p className="text-sm text-zinc-500">Campaigns, customer support, quotas, one-time orders, and stuck flyer work.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {TABS.map((item) => (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm",
                tab === item.id ? "border-brand-600 bg-brand-50 text-brand-700" : "border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "overview" && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Stat label="Customers" value={summary?.total_customers ?? "-"} />
            <Stat label="Free Trial" value={summary?.segments.free_trial ?? "-"} tone="good" />
            <Stat label="Paid" value={summary?.segments.paid ?? "-"} tone="good" />
            <Stat label="Payment Pending" value={summary?.segments.payment_pending ?? "-"} tone="warn" />
            <Stat label="One-time" value={summary?.segments.one_time ?? "-"} />
            <Stat label="Stuck" value={summary?.stuck_projects ?? "-"} tone={(summary?.stuck_projects ?? 0) > 0 ? "warn" : "default"} />
          </div>
          <Card>
            <CardHeader><CardTitle>Operator attention</CardTitle></CardHeader>
            <CardContent className="grid gap-3 lg:grid-cols-3">
              <div className="flex items-start gap-3 rounded-md border border-zinc-200 p-3">
                <Megaphone size={18} className="mt-0.5 text-brand-600" />
                <div>
                  <div className="text-sm font-medium">Campaign asset</div>
                  <div className="text-xs text-zinc-500 break-all">{summary?.campaign_asset.exists ? summary.campaign_asset.path : "Flyer.png missing"}</div>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-md border border-zinc-200 p-3">
                <AlertTriangle size={18} className="mt-0.5 text-amber-600" />
                <div>
                  <div className="text-sm font-medium">Active projects</div>
                  <div className="text-xs text-zinc-500">{summary?.active_projects ?? 0} in progress; {summary?.stuck_projects ?? 0} need inspection.</div>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-md border border-zinc-200 p-3">
                <Users size={18} className="mt-0.5 text-emerald-600" />
                <div>
                  <div className="text-sm font-medium">Customer mix</div>
                  <div className="text-xs text-zinc-500">Free, paid, payment pending, and one-time buyers in one view.</div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "customers" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <CardTitle>Customers</CardTitle>
                <div className="flex gap-2">
                  <div className="relative w-72">
                    <Search size={14} className="absolute left-2 top-2.5 text-zinc-400" />
                    <Input className="pl-8" placeholder="phone, business, customer id" value={query} onChange={(e) => setQuery(e.target.value)} />
                  </div>
                  <select className="h-9 rounded-md border border-zinc-300 bg-white px-2 text-sm" value={segment} onChange={(e) => setSegment(e.target.value)}>
                    <option value="">All</option>
                    <option value="free_trial">Free Trial</option>
                    <option value="paid">Paid</option>
                    <option value="payment_pending">Payment Pending</option>
                    <option value="inactive">Inactive</option>
                  </select>
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-xs text-zinc-500">
                  <tr>
                    <th className="px-3 py-2 text-left">Business</th>
                    <th className="px-3 py-2 text-left">Plan</th>
                    <th className="px-3 py-2 text-left">Usage</th>
                    <th className="px-3 py-2 text-left">Phone</th>
                    <th className="px-3 py-2 text-left">Latest</th>
                  </tr>
                </thead>
                <tbody>
                  {customers.map((customer) => {
                    const latest = latestProjectByPhone.get(customer.business_whatsapp_number);
                    return (
                      <tr key={customer.customer_id} onClick={() => setSelectedCustomer(customer)} className="cursor-pointer border-t border-zinc-100 hover:bg-zinc-50">
                        <td className="px-3 py-2">
                          <div className="font-medium">{customer.business_name}</div>
                          <div className="text-xs text-zinc-500">{customer.customer_id}</div>
                        </td>
                        <td className="px-3 py-2"><Badge tone={categoryTone(customer.category)}>{customer.plan_id}</Badge></td>
                        <td className="px-3 py-2">{customer.usage_used} / {customer.usage_remaining == null ? "unlimited" : customer.usage_used + customer.usage_remaining}</td>
                        <td className="px-3 py-2 font-mono text-xs">{customer.business_whatsapp_number}</td>
                        <td className="px-3 py-2 text-xs text-zinc-500">{latest ? `${latest.project_id} ${latest.status}` : `${customer.project_count} projects`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Trial controls</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {selectedCustomer ? (
                <>
                  <div>
                    <div className="font-medium">{selectedCustomer.business_name}</div>
                    <div className="text-xs text-zinc-500">{selectedCustomer.customer_id} · {selectedCustomer.business_whatsapp_number}</div>
                  </div>
                  <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for audit log" />
                  <div className="flex items-center gap-2">
                    <Input type="number" min={1} max={100} value={extensionCount} onChange={(e) => setExtensionCount(Number(e.target.value || 1))} />
                    <Button onClick={() => extendTrial.mutate(selectedCustomer.customer_id)} loading={extendTrial.isPending}>
                      <Gift size={14} /> Extend
                    </Button>
                  </div>
                  <Button variant="outline" onClick={() => resetTrial.mutate(selectedCustomer.customer_id)} loading={resetTrial.isPending}>
                    <RefreshCw size={14} /> Reset used trial quota
                  </Button>
                  <div className="text-xs text-zinc-500">Current bonus: {selectedCustomer.trial_bonus_flyers}. Every action writes a backup and cockpit audit event.</div>
                </>
              ) : (
                <div className="text-sm text-zinc-500">Select a customer to manage their trial quota.</div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "campaigns" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
          <Card>
            <CardHeader><CardTitle>Send campaign</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <textarea
                className="min-h-52 w-full rounded-md border border-zinc-300 p-3 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/50"
                placeholder="+17329837841&#10;+18479155253"
                value={targetsText}
                onChange={(e) => setTargetsText(e.target.value)}
              />
              <div className="grid gap-2 md:grid-cols-[1fr_auto_auto_auto]">
                <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for audit log" />
                <Button variant="outline" onClick={() => preview.mutate()} loading={preview.isPending}>Preview</Button>
                <Button variant="outline" onClick={() => sendCampaign.mutate(true)} loading={sendCampaign.isPending}>Dry run only</Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (confirm("Send this WhatsApp campaign now?")) sendCampaign.mutate(false);
                  }}
                  loading={sendCampaign.isPending}
                >
                  <Send size={14} /> Send WhatsApp now
                </Button>
              </div>
              <div className="flex items-center gap-2 text-sm">
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-zinc-300 px-3 py-2">
                  <FileUp size={14} />
                  Upload CSV
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) csvPreview.mutate(file);
                    }}
                  />
                </label>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Validation</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              {previewData ? (
                <>
                  <div className="grid grid-cols-3 gap-2">
                    <Stat label="Valid" value={previewData.valid_targets.length} />
                    <Stat label="Duplicates" value={previewData.duplicate_count} />
                    <Stat label="Invalid" value={previewData.invalid.length} tone={previewData.invalid.length ? "warn" : "default"} />
                  </div>
                  <div className="max-h-48 overflow-auto rounded-md border border-zinc-200">
                    {previewData.valid_targets.map((target) => <div key={target} className="border-b border-zinc-100 px-2 py-1 font-mono text-xs">{target}</div>)}
                  </div>
                  {previewData.invalid.length > 0 && <pre className="max-h-32 overflow-auto rounded-md bg-red-50 p-2 text-xs text-red-700">{JSON.stringify(previewData.invalid, null, 2)}</pre>}
                </>
              ) : (
                <div className="text-zinc-500">Paste numbers or upload a CSV with a `phone` column.</div>
              )}
              {campaignResult && (
                <div className={cn("rounded-md p-3 text-xs", campaignResult.failed ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700")}>
                  {campaignResult.dry_run ? "Dry run only" : "WhatsApp send"}: {campaignResult.sent} sent, {campaignResult.failed} failed, {campaignResult.targets.length} targets.
                  {campaignResult.results?.length ? (
                    <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap">{JSON.stringify(campaignResult.results, null, 2)}</pre>
                  ) : null}
                </div>
              )}
              {sendCampaign.error && (
                <div className="rounded-md bg-red-50 p-3 text-xs text-red-700">
                  {mutationErrorMessage(sendCampaign.error)}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "projects" && (
        <Card>
          <CardHeader><CardTitle>Projects</CardTitle></CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="px-3 py-2 text-left">Project</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Phone</th><th className="px-3 py-2 text-left">Request</th><th className="px-3 py-2 text-left">Assets</th></tr></thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.project_id} className="border-t border-zinc-100">
                    <td className="px-3 py-2 font-mono text-xs">{project.project_id}</td>
                    <td className="px-3 py-2"><Badge tone={project.status.includes("awaiting") ? "amber" : project.status === "delivered" ? "green" : "neutral"}>{project.status}</Badge></td>
                    <td className="px-3 py-2 font-mono text-xs">{project.customer_phone}</td>
                    <td className="max-w-xl truncate px-3 py-2">{project.raw_request}</td>
                    <td className="px-3 py-2 text-xs text-zinc-500">{project.concepts?.length ?? 0} concepts · {project.final_asset_ids?.length ?? 0} final</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {tab === "guests" && (
        <Card>
          <CardHeader><CardTitle>One-time flyer buyers</CardTitle></CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="px-3 py-2 text-left">Order</th><th className="px-3 py-2 text-left">Phone</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Usage</th><th className="px-3 py-2 text-left">Price</th></tr></thead>
              <tbody>
                {guests.map((order) => (
                  <tr key={order.order_id} className="border-t border-zinc-100">
                    <td className="px-3 py-2 font-mono text-xs">{order.order_id}</td>
                    <td className="px-3 py-2 font-mono text-xs">{order.sender_phone}</td>
                    <td className="px-3 py-2"><Badge tone={order.status === "paid" ? "green" : order.status === "pending_payment" ? "amber" : "neutral"}>{order.status}</Badge></td>
                    <td className="px-3 py-2">{order.flyer_count_used} / {order.flyer_count_purchased}</td>
                    <td className="px-3 py-2">${(order.unit_price_cents / 100).toFixed(2)}</td>
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
