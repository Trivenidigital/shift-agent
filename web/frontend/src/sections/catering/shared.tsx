import type React from "react";
import { Card, CardContent } from "@/components/ui/Card";
import { cn } from "@/lib/cn";

// Small presentation primitives shared by the Catering Studio panels. These
// mirror FlyerAdmin's Stat/Badge/tone idiom rather than introducing a second
// vocabulary — the cockpit has one visual language and this section joins it.

export type Tone = "neutral" | "green" | "amber" | "red" | "blue";

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: Tone }) {
  const cls = {
    neutral: "bg-zinc-100 text-zinc-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-800",
    red: "bg-red-50 text-red-700",
    blue: "bg-brand-50 text-brand-700",
  }[tone];
  return <span className={cn("rounded px-2 py-0.5 text-xs font-medium", cls)}>{children}</span>;
}

export function Stat({
  label,
  value,
  tone = "default",
  hint,
}: {
  label: string;
  value: number | string;
  tone?: "default" | "warn" | "good";
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-zinc-200 bg-white px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold", tone === "warn" && "text-amber-700", tone === "good" && "text-emerald-700")}>
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-zinc-400">{hint}</div>}
    </div>
  );
}

/** Status → tone for the catering lead lifecycle. */
export function leadStatusTone(status: string): Tone {
  switch (status) {
    case "AWAITING_OWNER_APPROVAL":
    case "CUSTOMER_FINALIZED":
      return "amber";
    case "QUALIFYING":
    case "NEW":
    case "EXTRACTING":
      return "blue";
    case "OWNER_APPROVED":
    case "SENT_TO_CUSTOMER":
    case "BOOKED":
    case "CLOSED":
      return "green";
    case "OWNER_REJECTED":
    case "NOT_CATERING":
    case "STALE":
      return "red";
    default:
      return "neutral";
  }
}

export function controlModeTone(mode: string | null): Tone {
  switch (mode) {
    case "paused":
      return "amber";
    case "opted_out":
      return "red";
    case "takeover":
      return "blue";
    case "active":
      return "green";
    default:
      return "neutral";
  }
}

export function money(cents: number | null | undefined, currency = "USD"): string {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(2)}${currency === "USD" ? "" : ` ${currency}`}`;
}

export function dollars(usd: number | null | undefined): string {
  return usd == null ? "—" : `$${usd.toLocaleString()}`;
}

export function fmtTs(ts: string | null | undefined): string {
  return ts ? ts.replace("T", " ").replace(/(\+00:00|Z)$/, "") : "—";
}

/** Loading skeleton — same rounded-block idiom used across the cockpit. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="text-sm text-zinc-500">{children}</CardContent>
    </Card>
  );
}

export function ErrorState({ error, what }: { error: unknown; what: string }) {
  const err = error as Error & { status?: number };
  const message =
    err?.status === 401
      ? "Session expired — reload and log in again."
      : err?.message || "Request failed.";
  return (
    <Card>
      <CardContent className="text-sm text-rose-700">
        Could not load {what}: {message}
      </CardContent>
    </Card>
  );
}

/** Backend-reported degradation (store unreadable / malformed). Distinct from
 *  an error: the request succeeded, the underlying data did not. */
export function DegradedBanner({ reasons }: { reasons: (string | null | undefined)[] }) {
  const shown = reasons.filter((r): r is string => Boolean(r));
  if (shown.length === 0) return null;
  return (
    <Card>
      <CardContent className="space-y-1 text-sm text-amber-800">
        <div className="font-medium">⚠ Some catering state could not be read cleanly</div>
        {shown.map((reason) => (
          <div key={reason} className="text-xs">{reason}</div>
        ))}
      </CardContent>
    </Card>
  );
}

/** 403 from a SENSITIVE route means the OTP step-up lapsed. Same remedy the
 *  Config and Commerce sections give: re-login within 5 minutes. */
export function mutationErrorMessage(error: unknown): string {
  if (!error) return "";
  const err = error as Error & { status?: number };
  if (err.status === 403) {
    return "This action needs a fresh login code. Log out and back in (within 5 minutes), then retry.";
  }
  if (err.status === 401) return "Session expired. Log in again, then retry.";
  if (err.status === 503) return "The catering agent scripts are not available on this host.";
  return err.message || "Action failed.";
}
