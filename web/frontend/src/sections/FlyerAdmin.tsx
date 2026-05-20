import { useEffect, useMemo, useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, FileUp, Gift, Megaphone, RefreshCw, Search, Send, Users } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/cn";
import { FlyerProjectEvidenceDrawer } from "./FlyerProjectEvidenceDrawer";
import { ManualQueueActions } from "./flyer/ManualQueueActions";

type Tab = "overview" | "customers" | "campaigns" | "projects" | "guests" | "queue";

type ManualReviewStatus = "none" | "queued" | "in_progress" | "completed" | "break_glass_sent";

interface ManualQueueRow {
  project_id: string;
  customer_phone: string;
  status: string;
  manual_status: ManualReviewStatus;
  manual_reason: string;
  manual_reason_code: string;
  manual_detail: string;
  age_hours: number;
  asset_ids: string[];
  verification_modes?: string[];
  locked_facts: unknown[];
  qa_blockers: string[];
}

interface ManualQueueGroup {
  customer_phone: string;
  count: number;
  oldest_age_hours: number;
  projects: ManualQueueRow[];
}

interface ManualQueueSummary {
  total: number;
  reason_counts: Record<string, number>;
  groups: ManualQueueGroup[];
}

interface ManualQueueDetailAsset {
  asset_id: string;
  kind: string;
  output_format: string;
  source: string;
  mime_type: string;
  sha256: string;
  sha256_short: string;
  file_sha256: string;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  delivery_status: string;
  outbound_message_id: string;
  received_at: string | null;
  delivered_at: string | null;
  media_url: string;
}

interface ManualQueueDetailManualReview {
  status: ManualReviewStatus;
  reason: string;
  reason_code: string;
  detail: string;
  queued_at: string | null;
  completed_at: string | null;
  break_glass_reason: string;
  operator_asset_ids: string[];
}

interface ManualQueueDetailTimelineEvent {
  ts: string;
  event: string;
  detail: string;
  source: string;
}

interface ManualQueueDetail {
  project_id: string;
  customer_phone: string;
  status: string;
  raw_request: string;
  original_message_id: string;
  created_at: string;
  updated_at: string;
  version: number;
  manual_review: ManualQueueDetailManualReview;
  locked_facts: { name: string; value: string; source?: string }[];
  qa_blockers: string[];
  verification_modes: string[];
  assets: ManualQueueDetailAsset[];
  final_assets: ManualQueueDetailAsset[];
  final_asset_ids: string[];
  selected_concept_id: string | null;
  fields: Record<string, unknown>;
  timeline: ManualQueueDetailTimelineEvent[];
}

interface OperatorUploadResult {
  ok: boolean;
  asset_path: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

interface CloseNoSendResult {
  ok: boolean;
  project_id: string;
  status: string;
  manual_status: string;
  backup: string;
  notification: {
    send_ok: boolean;
    chat_id: string;
    outbound_message_id: string;
    error: string;
  };
}

interface FlyerSummary {
  segments: Record<string, number>;
  total_customers: number;
  active_projects: number;
  stuck_projects: number;
  manual_edit_count: number;
  stuck_edit_count: number;
  guest_orders: number;
  campaign_asset: { path: string; exists: boolean };
}

// P0-7: provider + runtime health
type HealthSeverity = "green" | "yellow" | "red";

interface FlyerHealthComponent {
  name: string;
  severity: HealthSeverity;
  detail: string;
  checked_at: string;
}

interface FlyerHealthProvider {
  name: "openrouter_generation_vision" | "openrouter_source_edit";
  purpose: string;
  severity: HealthSeverity;
  detail: string;
  key_present: boolean;
  key_source: "process_env" | "hermes_env" | "agent_env" | null;
  model_config: Record<string, string>;
  manual_queue_impact?: { queued_count: number; oldest_age_hours: number | null };
  operator_note?: string;
  checked_at: string;
}

interface FlyerHealth {
  checked_at: string;
  // Truthful naming: these are the SHIFT-AGENT tarball markers, not the
  // cockpit's. The cockpit deploys separately and has no own marker today.
  shift_agent_deploy_tag: string | null;
  shift_agent_commit_hash: string | null;
  components: FlyerHealthComponent[];
  providers: FlyerHealthProvider[];
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
  age_minutes?: number;
  attention?: string[];
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
  { id: "queue", label: "Manual Queue" },
];

function manualStatusTone(status: ManualReviewStatus): "neutral" | "green" | "amber" | "red" | "blue" {
  switch (status) {
    case "queued":
      return "amber";
    case "in_progress":
      return "blue";
    case "completed":
      return "green";
    case "break_glass_sent":
      return "red";
    default:
      return "neutral";
  }
}

function Stat({ label, value, tone = "default" }: { label: string; value: number | string; tone?: "default" | "warn" | "good" }) {
  return (
    <div className="rounded-md border border-zinc-200 bg-white px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold", tone === "warn" && "text-amber-700", tone === "good" && "text-emerald-700")}>{value}</div>
    </div>
  );
}

function severityTone(severity: HealthSeverity): "green" | "amber" | "red" {
  return severity === "green" ? "green" : severity === "yellow" ? "amber" : "red";
}

function HealthDot({ severity }: { severity: HealthSeverity }) {
  const color = severity === "green" ? "bg-emerald-500" : severity === "yellow" ? "bg-amber-500" : "bg-red-500";
  return <span className={cn("inline-block h-2 w-2 rounded-full", color)} aria-label={`severity: ${severity}`} />;
}

function FlyerHealthPanel({ data }: { data: FlyerHealth | undefined }) {
  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Activity size={16} /> Provider & runtime health
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-zinc-500">Loading health…</div>
        </CardContent>
      </Card>
    );
  }

  const provider = (name: FlyerHealthProvider["name"]) =>
    data.providers.find((p) => p.name === name);
  const openrouter = provider("openrouter_generation_vision");
  const sourceEdit = provider("openrouter_source_edit");

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Activity size={16} /> Provider & runtime health
          </CardTitle>
          <div className="text-xs text-zinc-500" title="Shift-agent tarball deploy marker (cockpit deploys separately)">
            agent: {data.shift_agent_deploy_tag ?? data.shift_agent_commit_hash ?? "marker missing"}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {data.components.map((c) => (
            <div
              key={c.name}
              className="flex items-start gap-2 rounded-md border border-zinc-200 bg-white px-3 py-2"
            >
              <HealthDot severity={c.severity} />
              <div className="min-w-0">
                <div className="text-xs font-medium text-zinc-700">{c.name.replace(/_/g, " ")}</div>
                <div className="truncate text-xs text-zinc-500" title={c.detail}>
                  {c.detail}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          {openrouter && (
            <div className="rounded-md border-2 border-zinc-200 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <HealthDot severity={openrouter.severity} />
                  OpenRouter — generation & vision
                </div>
                <Badge tone={severityTone(openrouter.severity)}>{openrouter.severity}</Badge>
              </div>
              <div className="mt-2 text-xs text-zinc-600">{openrouter.detail}</div>
              <div className="mt-2 text-xs text-zinc-500">
                draft: <span className="font-mono">{openrouter.model_config.draft_image_model ?? "?"}</span> ·
                {" "}final: <span className="font-mono">{openrouter.model_config.final_image_model ?? "?"}</span>
              </div>
            </div>
          )}
          {sourceEdit && (
            <div className="rounded-md border-2 border-zinc-200 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <HealthDot severity={sourceEdit.severity} />
                  Source edits - OpenRouter
                </div>
                <Badge tone={severityTone(sourceEdit.severity)}>{sourceEdit.severity}</Badge>
              </div>
              <div className="mt-2 text-xs text-zinc-600">{sourceEdit.detail}</div>
              <div className="mt-2 text-xs text-zinc-500">
                edit model: <span className="font-mono">{sourceEdit.model_config.edit_image_model ?? "?"}</span>
              </div>
              {sourceEdit.manual_queue_impact && sourceEdit.manual_queue_impact.queued_count > 0 && (
                <div className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
                  <strong>{sourceEdit.manual_queue_impact.queued_count}</strong> queued; oldest{" "}
                  <strong>{sourceEdit.manual_queue_impact.oldest_age_hours ?? 0}h</strong>
                </div>
              )}
              {sourceEdit.operator_note && (
                <div className="mt-2 text-xs italic text-zinc-500">{sourceEdit.operator_note}</div>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
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

async function sendCsvCampaign(file: File, reason: string, dryRun: boolean): Promise<CampaignSendResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("reason", reason);
  form.append("dry_run", String(dryRun));
  const res = await fetch("/api/flyer/campaigns/send-csv", { method: "POST", credentials: "include", body: form });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText);
  return res.json();
}

async function uploadOperatorAsset(file: File, reason: string): Promise<OperatorUploadResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("reason", reason);
  const res = await fetch("/api/flyer/operator-uploads", { method: "POST", credentials: "include", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : res.statusText;
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// P0-1 reason-code playbook copy. Keys MUST stay in sync with
// FlyerManualReviewReason in src/platform/schemas.py and with
// CLOSED_NO_SEND_REASON_LINES / MANUAL_REVIEW_REASON_LINES in
// src/agents/flyer/workflow.py so operator guidance matches what the
// customer will hear if we proactively notify them.
const REASON_PLAYBOOK: Record<string, { title: string; next_steps: string[] }> = {
  source_edit_provider_unavailable: {
    title: "Source-edit provider down",
    next_steps: [
      "Provision/verify OPENROUTER_API_KEY on the VPS and confirm the edit model, OR",
      "Upload an approved designer flyer here and click Complete, OR",
      "Close with reason containing `provider_unavailable_after_retry` if the row is genuinely stuck.",
    ],
  },
  reference_unsupported: {
    title: "Reference file format unsupported",
    next_steps: [
      "Reply to the customer asking for a JPG or PNG source flyer, OR",
      "Upload a designer-extracted JPG/PNG version here and Complete.",
    ],
  },
  reference_provider_unavailable: {
    title: "Reference flyer not retrievable",
    next_steps: [
      "Check WhatsApp bridge / media cache, OR",
      "Ask the customer to re-upload the source flyer.",
    ],
  },
  reference_low_confidence: {
    title: "Couldn't read uploaded reference",
    next_steps: [
      "Inspect the reference thumbnail below — is it legible?",
      "Ask customer for a clearer copy OR a text description.",
    ],
  },
  reference_not_run: {
    title: "Extraction not run yet",
    next_steps: [
      "If queued >30 min, investigate extractor health / restart agent.",
      "Otherwise leave queued and re-check shortly.",
    ],
  },
  visual_qa_failed: {
    title: "Visual QA blockers",
    next_steps: [
      "Read the QA blockers below — do they reflect real defects or false positives?",
      "If real: regenerate or upload a corrected designer asset and Complete.",
      "If false-positive on text recognition: consider Break-glass with a clear audit reason.",
    ],
  },
  missing_required_facts: {
    title: "Required facts missing",
    next_steps: [
      "Read the locked facts below and the raw request — what's missing?",
      "Reply to the customer asking for the missing info (do not auto-close).",
    ],
  },
  operator_request: {
    title: "Operator-flagged review",
    next_steps: [
      "Inspect why it was flagged — check audit log for the originating cockpit action.",
      "Either Complete with a fresh asset or Close once disposition is clear.",
    ],
  },
  policy_block: {
    title: "Policy-block review",
    next_steps: [
      "Compliance/policy issue — escalate per runbook before any send.",
      "Do NOT Break-glass without explicit operator approval.",
    ],
  },
  provider_timeout: {
    title: "Provider timeout",
    next_steps: [
      "Likely transient. Re-queue / retry generation.",
      "If repeated: check provider status, then upload designer asset OR close.",
    ],
  },
  unclassified: {
    title: "Unclassified queue row",
    next_steps: [
      "Inspect raw request + assets to understand intent.",
      "Reach out to operator runbook if context is missing.",
    ],
  },
  legacy_unknown: {
    title: "Legacy queue row (pre-S1 reason tracking)",
    next_steps: [
      "Reason code wasn't recorded — inspect raw request + audit log to infer.",
      "Once disposition is clear, Complete with asset or Close with reason.",
    ],
  },
};

export function FlyerAdmin() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [query, setQuery] = useState("");
  const [segment, setSegment] = useState("");
  const [targetsText, setTargetsText] = useState("");
  const [campaignCsvFile, setCampaignCsvFile] = useState<File | null>(null);
  const [reason, setReason] = useState("operator dashboard action");
  const [selectedCustomer, setSelectedCustomer] = useState<FlyerCustomer | null>(null);
  const [extensionCount, setExtensionCount] = useState(1);
  const [customerOffset, setCustomerOffset] = useState(0);
  const CUSTOMER_PAGE_SIZE = 300;
  // P0-1 Manual Queue drawer + filter state
  const [drawerProjectId, setDrawerProjectId] = useState<string | null>(null);
  const [queueFilterReason, setQueueFilterReason] = useState("");
  const [queueFilterPhone, setQueueFilterPhone] = useState("");
  const [queueFilterAgeBucket, setQueueFilterAgeBucket] = useState("");
  const [queueFilterManualStatus, setQueueFilterManualStatus] = useState("");
  const [queueFilterProjectId, setQueueFilterProjectId] = useState("");
  // P0-2 in-drawer upload-then-complete state (per drawer instance — drawer
  // is single-row, so flat state is sufficient)
  const [drawerReason, setDrawerReason] = useState("");
  const [drawerUploadedAsset, setDrawerUploadedAsset] = useState<OperatorUploadResult | null>(null);
  const [drawerUploadError, setDrawerUploadError] = useState<string | null>(null);
  const [drawerUploadBusy, setDrawerUploadBusy] = useState(false);

  // Reset to page 1 whenever the filter changes — otherwise an offset
  // set against the old result set may overshoot the new total.
  useEffect(() => {
    setCustomerOffset(0);
  }, [query, segment]);

  const { data: summary } = useQuery<FlyerSummary>({
    queryKey: ["flyer-summary"],
    queryFn: () => api.GET<FlyerSummary>("/flyer/summary"),
    refetchInterval: 15_000,
  });
  // P0-7: provider + runtime health (read-only). 30s cadence is conservative.
  const { data: health } = useQuery<FlyerHealth>({
    queryKey: ["flyer-health"],
    queryFn: () => api.GET<FlyerHealth>("/flyer/health"),
    refetchInterval: 30_000,
  });
  const { data: customerData } = useQuery<{
    customers: FlyerCustomer[];
    total: number;
    offset: number;
    limit: number;
    truncated: boolean;
  }>({
    queryKey: ["flyer-customers", query, segment, customerOffset],
    queryFn: () =>
      api.GET<{
        customers: FlyerCustomer[];
        total: number;
        offset: number;
        limit: number;
        truncated: boolean;
      }>(
        `/flyer/customers?query=${encodeURIComponent(query)}&segment=${encodeURIComponent(segment)}&offset=${customerOffset}&limit=${CUSTOMER_PAGE_SIZE}`,
      ),
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
  const { data: queueData, refetch: refetchQueue } = useQuery<ManualQueueSummary>({
    queryKey: ["flyer-manual-queue"],
    queryFn: () => api.GET<ManualQueueSummary>("/flyer/manual-queue"),
    refetchInterval: 30_000,
  });
  const { data: queueDetail, isFetching: queueDetailFetching } = useQuery<ManualQueueDetail>({
    queryKey: ["flyer-manual-queue-detail", drawerProjectId],
    queryFn: () => api.GET<ManualQueueDetail>(`/flyer/manual-queue/${drawerProjectId}/detail`),
    enabled: !!drawerProjectId,
    refetchInterval: drawerProjectId ? 15_000 : false,
  });

  const closeDrawer = () => {
    setDrawerProjectId(null);
    setDrawerReason("");
    setDrawerUploadedAsset(null);
    setDrawerUploadError(null);
  };
  const openDrawer = (projectId: string) => {
    setDrawerReason("");
    setDrawerUploadedAsset(null);
    setDrawerUploadError(null);
    setDrawerProjectId(projectId);
  };

  // Filter the queue groups client-side. Backend filters can come later
  // (P1-1) — for now the queue is bounded enough that JS-side filtering
  // is the right tradeoff against shipping schema/route churn.
  const filteredQueueGroups: ManualQueueGroup[] = useMemo(() => {
    if (!queueData) return [];
    const reasonF = queueFilterReason.trim().toLowerCase();
    const phoneF = queueFilterPhone.trim().toLowerCase();
    const projF = queueFilterProjectId.trim().toLowerCase();
    const statusF = queueFilterManualStatus.trim();
    const ageF = queueFilterAgeBucket;
    return queueData.groups
      .map((group) => {
        const projects = group.projects.filter((row) => {
          if (reasonF && !row.manual_reason_code.toLowerCase().includes(reasonF)) return false;
          if (projF && !row.project_id.toLowerCase().includes(projF)) return false;
          if (statusF && row.manual_status !== statusF) return false;
          if (ageF === "lt_2h" && !(row.age_hours < 2)) return false;
          if (ageF === "2_24h" && !(row.age_hours >= 2 && row.age_hours < 24)) return false;
          if (ageF === "gte_24h" && !(row.age_hours >= 24)) return false;
          return true;
        });
        return { ...group, projects, count: projects.length };
      })
      .filter((group) => {
        if (group.projects.length === 0) return false;
        if (phoneF && !group.customer_phone.toLowerCase().includes(phoneF)) return false;
        return true;
      });
  }, [queueData, queueFilterReason, queueFilterPhone, queueFilterAgeBucket, queueFilterManualStatus, queueFilterProjectId]);

  const filteredQueueCount = filteredQueueGroups.reduce((acc, g) => acc + g.projects.length, 0);

  const handleOperatorUpload = async (file: File) => {
    setDrawerUploadError(null);
    setDrawerUploadBusy(true);
    try {
      if (drawerReason.trim().length < 5) {
        throw new Error("operator reason (min 5 chars) is required before upload");
      }
      const result = await uploadOperatorAsset(file, drawerReason.trim());
      setDrawerUploadedAsset(result);
    } catch (err) {
      setDrawerUploadedAsset(null);
      setDrawerUploadError(mutationErrorMessage(err));
    } finally {
      setDrawerUploadBusy(false);
    }
  };

  const completeQueueItem = useMutation({
    mutationFn: ({ projectId, assetPath, opReason }: { projectId: string; assetPath: string; opReason: string }) =>
      api.POST(`/flyer/manual-queue/${projectId}/complete`, { operator_asset_path: assetPath, reason: opReason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flyer-manual-queue"] });
      qc.invalidateQueries({ queryKey: ["flyer-summary"] });
      qc.invalidateQueries({ queryKey: ["flyer-projects"] });
    },
  });
  const breakGlassQueueItem = useMutation({
    mutationFn: ({ projectId, opReason }: { projectId: string; opReason: string }) =>
      api.POST(`/flyer/manual-queue/${projectId}/break-glass`, { reason: opReason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flyer-manual-queue"] });
      qc.invalidateQueries({ queryKey: ["flyer-summary"] });
    },
  });
  // P0-6: close/no-send mutation. Returns notification result the cockpit
  // surfaces inline so the operator sees whether the proactive customer
  // push reached the bridge or fell back to the reactive safety net.
  const closeNoSendQueueItem = useMutation({
    mutationFn: ({ projectId, opReason, force }: { projectId: string; opReason: string; force: boolean }) =>
      api.POST<CloseNoSendResult>(
        `/flyer/manual-queue/${projectId}/close-no-send`,
        { reason: opReason, force },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flyer-manual-queue"] });
      qc.invalidateQueries({ queryKey: ["flyer-summary"] });
      qc.invalidateQueries({ queryKey: ["flyer-projects"] });
    },
  });

  const customers = customerData?.customers ?? [];
  const customerTotal = customerData?.total ?? customers.length;
  const customerTruncated = customerData?.truncated ?? false;
  const projects = projectsData?.projects ?? [];
  const guests = guestData?.orders ?? [];

  const preview = useMutation({
    mutationFn: () => api.POST<CampaignPreview>("/flyer/campaigns/preview", { targets_text: targetsText, reason, dry_run: true }),
  });
  const csvPreview = useMutation({ mutationFn: postCsv });
  const sendCampaign = useMutation({
    mutationFn: (dryRun: boolean) => {
      if (campaignCsvFile) return sendCsvCampaign(campaignCsvFile, reason, dryRun);
      return api.POST<CampaignSendResult>("/flyer/campaigns/send", { targets_text: targetsText, reason, dry_run: dryRun });
    },
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
  const previewData = campaignCsvFile ? csvPreview.data : preview.data;

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
          <FlyerHealthPanel data={health} />
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-7">
            <Stat label="Customers" value={summary?.total_customers ?? "-"} />
            <Stat label="Free Trial" value={summary?.segments.free_trial ?? "-"} tone="good" />
            <Stat label="Paid" value={summary?.segments.paid ?? "-"} tone="good" />
            <Stat label="Payment Pending" value={summary?.segments.payment_pending ?? "-"} tone="warn" />
            <Stat label="One-time" value={summary?.segments.one_time ?? "-"} />
            <Stat label="Stuck" value={summary?.stuck_projects ?? "-"} tone={(summary?.stuck_projects ?? 0) > 0 ? "warn" : "default"} />
            <Stat label="Edit Queue" value={summary?.manual_edit_count ?? "-"} tone={(summary?.stuck_edit_count ?? 0) > 0 ? "warn" : "default"} />
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
                  <div className="text-xs text-zinc-500">{summary?.active_projects ?? 0} in progress; {summary?.stuck_projects ?? 0} intake and {summary?.stuck_edit_count ?? 0} edits need inspection.</div>
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
              {customerTotal > 0 && (
                <div className="flex items-center justify-between border-t border-zinc-100 px-3 py-2 text-xs text-zinc-500">
                  <div>
                    Showing {customers.length === 0 ? 0 : customerOffset + 1}
                    {customers.length > 0 ? `–${customerOffset + customers.length}` : ""}
                    {" of "}
                    {customerTotal}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={customerOffset === 0}
                      onClick={() => setCustomerOffset(Math.max(0, customerOffset - CUSTOMER_PAGE_SIZE))}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!customerTruncated}
                      onClick={() => setCustomerOffset(customerOffset + CUSTOMER_PAGE_SIZE)}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
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
                onChange={(e) => {
                  setTargetsText(e.target.value);
                  if (e.target.value.trim()) {
                    setCampaignCsvFile(null);
                    csvPreview.reset();
                  }
                }}
              />
              <div className="grid gap-2 md:grid-cols-[1fr_auto_auto_auto]">
                <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for audit log" />
                <Button
                  variant="outline"
                  onClick={() => {
                    if (campaignCsvFile) csvPreview.mutate(campaignCsvFile);
                    else preview.mutate();
                  }}
                  loading={preview.isPending || csvPreview.isPending}
                >
                  Preview
                </Button>
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
                      if (file) {
                        setCampaignCsvFile(file);
                        setTargetsText("");
                        preview.reset();
                        csvPreview.mutate(file);
                      }
                    }}
                  />
                </label>
                {campaignCsvFile && (
                  <span className="rounded-md bg-brand-50 px-2 py-1 text-xs text-brand-700">
                    CSV selected: {campaignCsvFile.name}
                  </span>
                )}
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
              <thead className="bg-zinc-50 text-xs text-zinc-500"><tr><th className="px-3 py-2 text-left">Project</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Phone</th><th className="px-3 py-2 text-left">Request</th><th className="px-3 py-2 text-left">Age</th><th className="px-3 py-2 text-left">Assets</th></tr></thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.project_id} className="border-t border-zinc-100">
                    <td className="px-3 py-2 font-mono text-xs">{project.project_id}</td>
                    <td className="px-3 py-2"><Badge tone={project.status.includes("awaiting") ? "amber" : project.status === "delivered" ? "green" : "neutral"}>{project.status}</Badge></td>
                    <td className="px-3 py-2 font-mono text-xs">{project.customer_phone}</td>
                    <td className="max-w-xl truncate px-3 py-2">{project.raw_request}</td>
                    <td className={cn("px-3 py-2 text-xs", (project.attention?.length ?? 0) > 0 ? "font-medium text-amber-700" : "text-zinc-500")}>{project.age_minutes ?? 0}m</td>
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

      {tab === "queue" && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Manual review queue</CardTitle>
                <Button onClick={() => refetchQueue()} variant="outline" size="sm"><RefreshCw size={14} className="mr-1" />Refresh</Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Stat label="Total queued" value={queueData?.total ?? "-"} tone={(queueData?.total ?? 0) > 0 ? "warn" : "default"} />
                {Object.entries(queueData?.reason_counts ?? {}).slice(0, 4).map(([code, count]) => (
                  <Stat key={code} label={code} value={count} />
                ))}
              </div>
              {/* P0-1 filter row */}
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
                <Input
                  placeholder="project id (F0058)"
                  value={queueFilterProjectId}
                  onChange={(e) => setQueueFilterProjectId(e.target.value)}
                  className="h-8 font-mono text-xs"
                />
                <Input
                  placeholder="customer phone"
                  value={queueFilterPhone}
                  onChange={(e) => setQueueFilterPhone(e.target.value)}
                  className="h-8 text-xs"
                />
                <select
                  className="h-8 rounded-md border border-zinc-300 bg-white px-2 text-xs"
                  value={queueFilterReason}
                  onChange={(e) => setQueueFilterReason(e.target.value)}
                >
                  <option value="">All reason codes</option>
                  {Object.keys(REASON_PLAYBOOK).map((code) => (
                    <option key={code} value={code}>{code}</option>
                  ))}
                </select>
                <select
                  className="h-8 rounded-md border border-zinc-300 bg-white px-2 text-xs"
                  value={queueFilterManualStatus}
                  onChange={(e) => setQueueFilterManualStatus(e.target.value)}
                >
                  <option value="">All manual statuses</option>
                  <option value="queued">queued</option>
                  <option value="in_progress">in_progress</option>
                  <option value="completed">completed</option>
                  <option value="break_glass_sent">break_glass_sent</option>
                </select>
                <select
                  className="h-8 rounded-md border border-zinc-300 bg-white px-2 text-xs"
                  value={queueFilterAgeBucket}
                  onChange={(e) => setQueueFilterAgeBucket(e.target.value)}
                >
                  <option value="">Any age</option>
                  <option value="lt_2h">&lt; 2 hours</option>
                  <option value="2_24h">2–24 hours</option>
                  <option value="gte_24h">≥ 24 hours</option>
                </select>
              </div>
              {(completeQueueItem.isError || breakGlassQueueItem.isError) && (
                <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  {mutationErrorMessage(completeQueueItem.error ?? breakGlassQueueItem.error)}
                </div>
              )}
              {(queueData?.total ?? 0) > 0 && filteredQueueCount === 0 && (
                <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-3 text-center text-xs text-zinc-500">
                  No rows match the current filters. {(queueData?.total ?? 0)} row{(queueData?.total ?? 0) === 1 ? "" : "s"} hidden.
                </div>
              )}
              {!queueData || queueData.groups.length === 0 ? (
                <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-6 text-center text-sm text-zinc-500">
                  No projects in the manual-review queue.
                </div>
              ) : (
                filteredQueueGroups.map((group) => (
                  <div key={group.customer_phone} className="rounded-md border border-zinc-200">
                    <div className="flex items-center justify-between border-b border-zinc-100 bg-zinc-50 px-3 py-2 text-sm">
                      <div className="font-mono">{group.customer_phone}</div>
                      <div className="text-xs text-zinc-500">{group.count} project{group.count === 1 ? "" : "s"} · oldest {group.oldest_age_hours}h</div>
                    </div>
                    <table className="w-full text-sm">
                      <thead className="text-xs uppercase text-zinc-500">
                        <tr>
                          <th className="px-3 py-2 text-left">Project</th>
                          <th className="px-3 py-2 text-left">Manual status</th>
                          <th className="px-3 py-2 text-left">Reason</th>
                          <th className="px-3 py-2 text-left">Age</th>
                          <th className="px-3 py-2 text-left">Detail / blockers</th>
                          <th className="px-3 py-2 text-left">Operator</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.projects.map((row) => (
                          <tr
                            key={row.project_id}
                            className="cursor-pointer border-t border-zinc-100 align-top hover:bg-brand-50/40"
                            onClick={() => openDrawer(row.project_id)}
                          >
                            <td className="px-3 py-2">
                              <div className="font-mono text-xs text-brand-700 underline-offset-2 hover:underline">{row.project_id}</div>
                              <div className="text-xs text-zinc-500">{row.status}</div>
                            </td>
                            <td className="px-3 py-2"><Badge tone={manualStatusTone(row.manual_status)}>{row.manual_status}</Badge></td>
                            <td className="px-3 py-2">
                              <div className="font-mono text-xs">{row.manual_reason_code}</div>
                              {row.verification_modes?.includes("source_edit_integrity_only") && (
                                <div className="mt-1">
                                  <Badge tone="amber">Integrity only</Badge>
                                </div>
                              )}
                            </td>
                            <td className="px-3 py-2 text-xs">{row.age_hours}h</td>
                            <td className="px-3 py-2 text-xs">
                              <div className="text-zinc-700">{row.manual_detail || "—"}</div>
                              {row.qa_blockers.length > 0 && (
                                <ul className="mt-1 list-disc pl-4 text-rose-700">
                                  {row.qa_blockers.slice(0, 3).map((b, i) => (<li key={i}>{b}</li>))}
                                </ul>
                              )}
                              {row.asset_ids.length > 0 && (
                                <div className="mt-1 text-zinc-500">assets: {row.asset_ids.join(", ")}</div>
                              )}
                            </td>
                            <td className="px-3 py-2 text-xs text-brand-700">Open →</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      )}
      {/* P0-1/P0-2/P0-3 detail drawer (slide-over panel) */}
      {drawerProjectId && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/30">
          <div className="h-full w-full max-w-2xl overflow-y-auto bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
              <div>
                <div className="font-mono text-sm font-semibold">{drawerProjectId}</div>
                <div className="text-xs text-zinc-500">Manual queue detail</div>
              </div>
              <Button variant="outline" size="sm" onClick={closeDrawer}>Close</Button>
            </div>
            <div className="space-y-4 p-4 text-sm">
              {queueDetailFetching && !queueDetail && (
                <div className="text-xs text-zinc-500">Loading project context…</div>
              )}
              {queueDetail && (
                <ManualQueueDrawerBody
                  detail={queueDetail}
                  playbook={REASON_PLAYBOOK[queueDetail.manual_review.reason_code] ?? REASON_PLAYBOOK.unclassified}
                  reason={drawerReason}
                  onReasonChange={setDrawerReason}
                  uploadedAsset={drawerUploadedAsset}
                  uploadError={drawerUploadError}
                  uploadBusy={drawerUploadBusy}
                  onUpload={handleOperatorUpload}
                  onComplete={() => {
                    if (!drawerUploadedAsset) return;
                    completeQueueItem.mutate(
                      { projectId: queueDetail.project_id, assetPath: drawerUploadedAsset.asset_path, opReason: drawerReason.trim() },
                      { onSuccess: () => closeDrawer() },
                    );
                  }}
                  onBreakGlass={() => {
                    breakGlassQueueItem.mutate(
                      { projectId: queueDetail.project_id, opReason: drawerReason.trim() },
                      { onSuccess: () => closeDrawer() },
                    );
                  }}
                  onCloseNoSend={({ force }: { force: boolean }) => {
                    closeNoSendQueueItem.mutate(
                      { projectId: queueDetail.project_id, opReason: drawerReason.trim(), force },
                      // Don't auto-close: the operator should see the proactive
                      // notification result inline before dismissing the drawer.
                    );
                  }}
                  completePending={completeQueueItem.isPending}
                  breakGlassPending={breakGlassQueueItem.isPending}
                  closeNoSendPending={closeNoSendQueueItem.isPending}
                  completeError={mutationErrorMessage(completeQueueItem.error)}
                  breakGlassError={mutationErrorMessage(breakGlassQueueItem.error)}
                  closeNoSendError={mutationErrorMessage(closeNoSendQueueItem.error)}
                  closeNoSendResult={closeNoSendQueueItem.data ?? null}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface ManualQueueDrawerBodyProps {
  detail: ManualQueueDetail;
  playbook: { title: string; next_steps: string[] };
  reason: string;
  onReasonChange: (val: string) => void;
  uploadedAsset: OperatorUploadResult | null;
  uploadError: string | null;
  uploadBusy: boolean;
  onUpload: (file: File) => void;
  onComplete: () => void;
  onBreakGlass: () => void;
  onCloseNoSend: (opts: { force: boolean }) => void;
  completePending: boolean;
  breakGlassPending: boolean;
  closeNoSendPending: boolean;
  completeError: string;
  breakGlassError: string;
  closeNoSendError: string;
  closeNoSendResult: CloseNoSendResult | null;
}

function ManualQueueDrawerBody(props: ManualQueueDrawerBodyProps) {
  const {
    detail, playbook, reason, onReasonChange,
    uploadedAsset, uploadError, uploadBusy, onUpload,
    onComplete, onBreakGlass, onCloseNoSend,
    completePending, breakGlassPending, closeNoSendPending,
    completeError, breakGlassError, closeNoSendError, closeNoSendResult,
  } = props;
  const reasonOk = reason.trim().length >= 5;
  const completeOk = reasonOk && !!uploadedAsset && !completePending;
  const pendingActionKind = completePending
    ? ("complete" as const)
    : breakGlassPending
      ? ("break_glass" as const)
      : closeNoSendPending
        ? ("close_no_send" as const)
        : null;
  const integrityOnly = detail.verification_modes.includes("source_edit_integrity_only");
  const uploadedAssetUrl = uploadedAsset ? `/api/flyer/operator-uploads/${uploadedAsset.filename}` : "";

  return (
    <div className="space-y-4">
      {/* Header block: status + reason + age */}
      <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge tone={manualStatusTone(detail.manual_review.status)}>{detail.manual_review.status}</Badge>
          <span className="font-mono">{detail.manual_review.reason_code}</span>
          {integrityOnly && <Badge tone="amber">Integrity only</Badge>}
          <span className="text-zinc-500">customer {detail.customer_phone}</span>
        </div>
        <div className="mt-1 text-xs text-zinc-600">Updated {new Date(detail.updated_at).toLocaleString()}</div>
        {detail.manual_review.detail && (
          <div className="mt-2 text-xs text-zinc-700">{detail.manual_review.detail}</div>
        )}
      </div>

      {/* Reason playbook */}
      <div className="rounded-md border border-brand-200 bg-brand-50/50 px-3 py-2 text-xs">
        <div className="font-semibold text-brand-900">{playbook.title}</div>
        <ul className="mt-1 list-disc pl-5 text-brand-900/90">
          {playbook.next_steps.map((step, i) => (<li key={i}>{step}</li>))}
        </ul>
      </div>

      {/* Raw customer request */}
      <div className="rounded-md border border-zinc-200 px-3 py-2 text-xs">
        <div className="text-xs uppercase tracking-wide text-zinc-500">Customer request</div>
        <div className="mt-1 whitespace-pre-wrap text-zinc-800">{detail.raw_request}</div>
      </div>

      {/* Locked facts */}
      {detail.locked_facts.length > 0 && (
        <div className="rounded-md border border-zinc-200 px-3 py-2 text-xs">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Locked facts</div>
          <table className="mt-1 w-full text-xs">
            <tbody>
              {detail.locked_facts.map((fact, i) => (
                <tr key={i} className="border-t border-zinc-100">
                  <td className="py-1 pr-3 font-mono text-zinc-700">{fact.name}</td>
                  <td className="py-1 text-zinc-900">{fact.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* QA blockers */}
      {detail.qa_blockers.length > 0 && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs">
          <div className="text-xs uppercase tracking-wide text-rose-700">QA blockers</div>
          <ul className="mt-1 list-disc pl-5 text-rose-800">
            {detail.qa_blockers.map((b, i) => (<li key={i}>{b}</li>))}
          </ul>
        </div>
      )}

      <FlyerProjectEvidenceDrawer detail={detail} />

      {/* Operator action: upload + complete + break-glass */}
      <div className="rounded-md border border-zinc-200 px-3 py-2">
        <div className="text-xs uppercase tracking-wide text-zinc-500">Operator action</div>
        <div className="mt-2 space-y-2">
          <Input
            placeholder="operator reason (min 5 chars)"
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
            className="h-8 text-xs"
          />
          {/* Upload control (P0-2) */}
          <label className="flex items-center gap-2 text-xs text-zinc-700">
            <FileUp size={14} className="text-brand-700" />
            <span>Upload designer asset (PNG/JPG/WEBP/PDF, up to 10 MB)</span>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,application/pdf"
              disabled={!reasonOk || uploadBusy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) onUpload(file);
                e.target.value = "";
              }}
              className="text-xs"
            />
          </label>
          {uploadBusy && (
            <div className="text-xs text-zinc-500">Uploading…</div>
          )}
          {uploadError && (
            <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700">{uploadError}</div>
          )}
          {uploadedAsset && (
            <div className="rounded border border-emerald-200 bg-emerald-50 px-2 py-2 text-xs">
              <div className="font-semibold text-emerald-800">Uploaded - preview before complete</div>
              <div className="mt-1 text-emerald-900">
                {uploadedAsset.filename} / {uploadedAsset.mime_type} / {Math.round(uploadedAsset.size_bytes / 1024)} KB
              </div>
              {uploadedAsset.mime_type.startsWith("image/") && (
                <img
                  src={uploadedAssetUrl}
                  alt="uploaded designer asset"
                  className="mt-2 h-40 w-full rounded border border-emerald-300 bg-white object-contain"
                  loading="lazy"
                />
              )}
              {uploadedAsset.mime_type === "application/pdf" && (
                <a
                  href={uploadedAssetUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block rounded border border-emerald-300 bg-white px-2 py-1 text-xs text-brand-700 underline-offset-2 hover:underline"
                >
                  Open uploaded PDF in new tab
                </a>
              )}
            </div>
          )}
          <ManualQueueActions
            projectId={detail.project_id}
            reasonCode={detail.manual_review.reason_code}
            reason={reason}
            canComplete={completeOk}
            onCompleteConfirmed={onComplete}
            onBreakGlassConfirmed={onBreakGlass}
            onCloseNoSendConfirmed={onCloseNoSend}
            pendingAction={pendingActionKind}
            completeAsset={uploadedAsset ? {
              filename: uploadedAsset.filename,
              mimeType: uploadedAsset.mime_type,
              sizeBytes: uploadedAsset.size_bytes,
              url: uploadedAssetUrl,
            } : null}
            errors={{
              complete: completeError,
              breakGlass: breakGlassError,
              closeNoSend: closeNoSendError,
            }}
          />
          {closeNoSendResult && (
            <div
              className={cn(
                "rounded border px-2 py-2 text-xs",
                closeNoSendResult.notification.send_ok
                  ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                  : "border-amber-200 bg-amber-50 text-amber-900",
              )}
            >
              <div className="font-semibold">
                {closeNoSendResult.notification.send_ok
                  ? "Customer notified."
                  : "Closed; customer notification did NOT send."}
              </div>
              <div className="mt-1">
                Project {closeNoSendResult.project_id} · status {closeNoSendResult.status} · manual {closeNoSendResult.manual_status}
              </div>
              {closeNoSendResult.notification.send_ok && (
                <div className="mt-1 font-mono">
                  chat_id {closeNoSendResult.notification.chat_id} · outbound {closeNoSendResult.notification.outbound_message_id}
                </div>
              )}
              {!closeNoSendResult.notification.send_ok && closeNoSendResult.notification.error && (
                <div className="mt-1 font-mono">
                  {closeNoSendResult.notification.error}
                </div>
              )}
              {!closeNoSendResult.notification.send_ok && (
                <div className="mt-1 text-xs">
                  Closure was persisted. The reactive "any update?" reply will still surface the closure on the customer's next inbound — that path is the safety net.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
