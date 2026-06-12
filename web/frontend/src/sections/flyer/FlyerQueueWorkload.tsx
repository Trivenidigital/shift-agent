// ─────────────────────────────────────────────────────────────────
// P5 team-ops slice 2: queue workload view.
//
// A pure read-model over the manual-queue rows the cockpit already has
// client-side (ManualQueueSummary.groups[].projects[]). Aggregates the
// open queue by owner so a team can see — at a glance — who is working
// how many cases, how stale their oldest claim is, and how many cases are
// still unclaimed. No backend call, no customer-facing surface.
// ─────────────────────────────────────────────────────────────────

export interface WorkloadRow {
  claimed_by: string;
  is_stale?: boolean;
  age_minutes?: number;
  age_hours: number;
}

export interface OwnerWorkload {
  owner: string; // "" represents the unclaimed bucket
  count: number;
  stale: number;
  oldestAgeMinutes: number;
}

export interface WorkloadSummary {
  owners: OwnerWorkload[]; // claimed buckets, busiest first
  unclaimed: OwnerWorkload | null;
}

function rowAgeMinutes(row: WorkloadRow): number {
  return row.age_minutes ?? row.age_hours * 60;
}

/** Aggregate queue rows by owner. Pure — kept separate so the logic stays
 *  reviewable (and unit-testable once a frontend harness exists). */
export function summarizeWorkload(rows: WorkloadRow[]): WorkloadSummary {
  const byOwner = new Map<string, OwnerWorkload>();
  for (const row of rows) {
    const key = row.claimed_by || "";
    const cur = byOwner.get(key) ?? { owner: key, count: 0, stale: 0, oldestAgeMinutes: 0 };
    cur.count += 1;
    if (row.is_stale) cur.stale += 1;
    cur.oldestAgeMinutes = Math.max(cur.oldestAgeMinutes, rowAgeMinutes(row));
    byOwner.set(key, cur);
  }
  const unclaimed = byOwner.get("") ?? null;
  const owners = [...byOwner.values()]
    .filter((o) => o.owner !== "")
    // Busiest first; break ties by the more urgent (older) oldest claim.
    .sort((a, b) => b.count - a.count || b.oldestAgeMinutes - a.oldestAgeMinutes);
  return { owners, unclaimed };
}

function formatAge(mins: number): string {
  const m = Math.max(0, Math.round(mins));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function FlyerQueueWorkload({
  rows,
  adminHandle,
}: {
  rows: WorkloadRow[];
  adminHandle: string;
}) {
  if (rows.length === 0) return null;
  const { owners, unclaimed } = summarizeWorkload(rows);
  const me = adminHandle.trim();

  return (
    <div className="mb-3 rounded-md border border-zinc-200 bg-white px-3 py-2">
      <div className="mb-2 text-xs uppercase tracking-wide text-zinc-500">Team workload</div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {owners.length === 0 && (!unclaimed || unclaimed.count === 0) && (
          <span className="text-zinc-400">Queue is empty.</span>
        )}
        {owners.map((o) => {
          const isMe = me !== "" && o.owner === me;
          return (
            <span
              key={o.owner}
              className={
                "inline-flex items-center gap-1 rounded border px-2 py-1 " +
                (isMe ? "border-brand-300 bg-brand-50 text-brand-800" : "border-zinc-200 bg-zinc-50 text-zinc-700")
              }
              title={`oldest claim ${formatAge(o.oldestAgeMinutes)} old`}
            >
              <span className="font-medium">👤 {o.owner}{isMe ? " (you)" : ""}</span>
              <span className="font-mono">{o.count}</span>
              <span className="text-zinc-400">· oldest {formatAge(o.oldestAgeMinutes)}</span>
              {o.stale > 0 && (
                <span className="rounded bg-rose-50 px-1 text-[10px] font-medium text-rose-700">
                  {o.stale} stale
                </span>
              )}
            </span>
          );
        })}
        {unclaimed && unclaimed.count > 0 && (
          <span
            className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800"
            title={`oldest unclaimed ${formatAge(unclaimed.oldestAgeMinutes)} old`}
          >
            <span className="font-medium">Unclaimed</span>
            <span className="font-mono">{unclaimed.count}</span>
            <span className="text-amber-600">· oldest {formatAge(unclaimed.oldestAgeMinutes)}</span>
            {unclaimed.stale > 0 && (
              <span className="rounded bg-rose-100 px-1 text-[10px] font-medium text-rose-700">
                {unclaimed.stale} stale
              </span>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
