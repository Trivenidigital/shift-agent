import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";

// Commerce Order Cockpit. Slice B = read-only inbox; Slice C adds owner-only
// status transitions (fulfillment progress + pre-payment cancel). No provider,
// POS, or customer-messaging side-effects — pure order-state advancement.

interface OrderRow {
  order_id: string;
  customer_name: string | null;
  sender_phone: string | null;
  sender_lid: string | null;
  status: string;
  fulfillment_type: string | null;
  requested_time: string | null;
  payment_status: string;
  pos_sync_status: string;
  total_cents: number | null;
  currency: string | null;
  item_count: number;
  created_at: string | null;
  updated_at: string | null;
}
interface Totals {
  order_count: number;
  open_count: number;
  gross_cents_by_currency: Record<string, number>;
}
interface OrdersResponse { orders: OrderRow[]; totals: Totals; degraded: string | null; }

interface LineItem {
  display_name: string; quantity: number; line_total_cents: number;
}
interface StatusEvent {
  from_status: string | null; to_status: string; ts: string; cause: string; actor: string;
}
interface OrderDetailResponse {
  order: OrderRow & {
    line_items: LineItem[];
    delivery_address: string | null;
    order_notes: string | null;
    status_history: StatusEvent[];
    cart_id: string;
    chat_id: string;
  };
  payment_status: string;
  audit: Array<Record<string, unknown>>;
  degraded: string | null;
}

function money(cents: number | null | undefined, currency: string | null | undefined): string {
  if (cents == null) return "—";
  return `${(cents / 100).toFixed(2)} ${currency ?? "USD"}`;
}
function fmt(ts: string | null | undefined): string {
  return ts ? ts.replace("T", " ").replace(/(\+00:00|Z)$/, "") : "—";
}

const STATUS_TONE: Record<string, string> = {
  pending_payment: "text-amber-700",
  awaiting_approval: "text-amber-700",
  paid: "text-blue-700",
  preparing: "text-blue-700",
  ready: "text-emerald-700",
  out_for_delivery: "text-emerald-700",
  completed: "text-zinc-500",
  cancelled: "text-zinc-400",
  voided: "text-zinc-400",
  refunded: "text-rose-700",
};

// Slice-C action map — MUST mirror the backend SLICE_C_ALLOWED_TRANSITIONS
// exactly. The UI never offers a transition the route would refuse:
//   • `preparing` shows ONLY "Mark ready" (preparing→cancelled is excluded —
//     post-paid cancel without a refund path).
//   • "Cancel order" appears ONLY on pre-payment statuses.
//   • terminal statuses show no actions.
export interface SliceCAction {
  to_status: string;
  label: string;
  destructive?: boolean;
}
const SLICE_C_ACTIONS: Record<string, SliceCAction[]> = {
  paid: [{ to_status: "preparing", label: "Start preparing" }],
  preparing: [{ to_status: "ready", label: "Mark ready" }],
  ready: [
    { to_status: "out_for_delivery", label: "Out for delivery" },
    { to_status: "completed", label: "Mark completed" },
  ],
  out_for_delivery: [{ to_status: "completed", label: "Mark completed" }],
  pending_payment: [{ to_status: "cancelled", label: "Cancel order", destructive: true }],
  awaiting_approval: [{ to_status: "cancelled", label: "Cancel order", destructive: true }],
};
const TERMINAL_STATUSES = new Set(["completed", "cancelled", "voided", "refunded"]);

/** Pure: the Slice-C actions available from a given current status. Exported
 *  for unit testing the allowlist/disabled-state logic. */
export function sliceCActionsFor(status: string): SliceCAction[] {
  return SLICE_C_ACTIONS[status] ?? [];
}

export function CommerceOrders() {
  const [selected, setSelected] = useState<string | null>(null);
  const { data, isLoading } = useQuery<OrdersResponse>({
    queryKey: ["commerce-orders"],
    queryFn: () => api.GET<OrdersResponse>("/commerce/orders"),
    refetchInterval: 30_000,
  });
  const orders = data?.orders ?? [];
  const totals = data?.totals;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Orders</h2>
        {totals && (
          <div className="text-xs text-zinc-500 space-x-3">
            <span>{totals.open_count} open</span>
            <span>{totals.order_count} total</span>
            {Object.entries(totals.gross_cents_by_currency).map(([c, v]) => (
              <span key={c} className="font-mono">{money(v, c)}</span>
            ))}
          </div>
        )}
      </div>

      {data?.degraded && (
        <Card><CardContent className="text-sm text-rose-700">
          ⚠ Commerce order state degraded: {data.degraded}
        </CardContent></Card>
      )}

      {!isLoading && orders.length === 0 && !data?.degraded && (
        <Card><CardContent className="text-sm text-zinc-500">
          No commerce orders yet. WhatsApp pickup/delivery orders will appear here once
          Commerce ordering is activated — it is currently inactive. This is a read-only view.
        </CardContent></Card>
      )}

      {orders.length > 0 && (
        <Card><CardContent className="p-0 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr className="text-left text-zinc-500">
                <th className="px-2 py-1">Order</th>
                <th className="px-2 py-1">Customer</th>
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1">Type</th>
                <th className="px-2 py-1">Requested</th>
                <th className="px-2 py-1">Payment</th>
                <th className="px-2 py-1">POS</th>
                <th className="px-2 py-1 text-right">Items</th>
                <th className="px-2 py-1 text-right">Total</th>
                <th className="px-2 py-1">Created</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id} onClick={() => setSelected(o.order_id)}
                    className="border-b border-zinc-100 hover:bg-brand-50 cursor-pointer">
                  <td className="px-2 py-1 font-mono text-brand-700">{o.order_id}</td>
                  <td className="px-2 py-1">{o.customer_name ?? o.sender_phone ?? o.sender_lid ?? "—"}</td>
                  <td className={`px-2 py-1 font-medium ${STATUS_TONE[o.status] ?? "text-zinc-700"}`}>{o.status}</td>
                  <td className="px-2 py-1">{o.fulfillment_type ?? "—"}</td>
                  <td className="px-2 py-1 text-zinc-500">{fmt(o.requested_time)}</td>
                  <td className="px-2 py-1">{o.payment_status}</td>
                  <td className="px-2 py-1 text-zinc-500">{o.pos_sync_status}</td>
                  <td className="px-2 py-1 text-right">{o.item_count}</td>
                  <td className="px-2 py-1 text-right font-mono">{money(o.total_cents, o.currency)}</td>
                  <td className="px-2 py-1 text-zinc-500">{fmt(o.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent></Card>
      )}

      {selected && <OrderDetail orderId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function OrderDetail({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const { data } = useQuery<OrderDetailResponse>({
    queryKey: ["commerce-order", orderId],
    queryFn: () => api.GET<OrderDetailResponse>(`/commerce/orders/${orderId}`),
  });
  const o = data?.order;
  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-40" onClick={onClose}>
      <div className="w-full max-w-lg bg-white h-full overflow-y-auto p-4 space-y-3"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-bold font-mono">{orderId}</h3>
          <button className="text-zinc-400 hover:text-zinc-700" onClick={onClose}>✕</button>
        </div>
        {!o ? <p className="text-sm text-zinc-500">Loading…</p> : (
          <>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
              <dt className="text-zinc-500">Status</dt><dd>{o.status}</dd>
              <dt className="text-zinc-500">Payment</dt><dd>{data?.payment_status ?? o.payment_status}</dd>
              <dt className="text-zinc-500">Fulfillment</dt><dd>{o.fulfillment_type ?? "—"}</dd>
              <dt className="text-zinc-500">Requested</dt><dd>{fmt(o.requested_time)}</dd>
              <dt className="text-zinc-500">Customer</dt><dd>{o.customer_name ?? "—"}</dd>
              <dt className="text-zinc-500">Sender</dt><dd className="font-mono text-xs">{o.sender_phone ?? o.sender_lid ?? "—"}</dd>
              <dt className="text-zinc-500">POS sync</dt><dd>{o.pos_sync_status}</dd>
              <dt className="text-zinc-500">Created</dt><dd>{fmt(o.created_at)}</dd>
              <dt className="text-zinc-500">Updated</dt><dd>{fmt(o.updated_at)}</dd>
            </dl>
            {o.fulfillment_type === "delivery" && (
              <div className="text-sm"><span className="text-zinc-500">Address: </span>{o.delivery_address ?? "—"}</div>
            )}
            {o.order_notes && <div className="text-sm"><span className="text-zinc-500">Notes: </span>{o.order_notes}</div>}

            <div>
              <h4 className="text-sm font-semibold mt-2 mb-1">Items</h4>
              <table className="w-full text-xs">
                <tbody>
                  {(o.line_items ?? []).map((li, i) => (
                    <tr key={i} className="border-b border-zinc-100">
                      <td className="py-1">{li.quantity}× {li.display_name}</td>
                      <td className="py-1 text-right font-mono">{money(li.line_total_cents, o.currency)}</td>
                    </tr>
                  ))}
                  <tr className="font-medium">
                    <td className="py-1">Total</td>
                    <td className="py-1 text-right font-mono">{money(o.total_cents, o.currency)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div>
              <h4 className="text-sm font-semibold mt-2 mb-1">Status history</h4>
              <ul className="text-xs space-y-1">
                {(o.status_history ?? []).map((e, i) => (
                  <li key={i} className="text-zinc-600">
                    <span className="text-zinc-400">{fmt(e.ts)}</span> · {e.from_status ?? "∅"} → <span className="font-medium">{e.to_status}</span> <span className="text-zinc-400">({e.actor}: {e.cause})</span>
                  </li>
                ))}
                {(o.status_history ?? []).length === 0 && <li className="text-zinc-400">No status events.</li>}
              </ul>
            </div>

            {data?.audit && data.audit.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold mt-2 mb-1">Audit (decisions.log)</h4>
                <ul className="text-xs font-mono space-y-1">
                  {data.audit.map((a, i) => (
                    <li key={i} className="text-zinc-600">{String(a.ts ?? "")} · {String(a.type ?? "")}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="pt-2 border-t">
              {data?.degraded ? (
                <p className="text-xs text-rose-700">
                  ⚠ Order state degraded — staff actions are hidden until it can be read cleanly.
                </p>
              ) : (
                <OrderActions orderId={orderId} status={o.status} />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function OrderActions({ orderId, status }: { orderId: string; status: string }) {
  const qc = useQueryClient();
  const actions = sliceCActionsFor(status);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["commerce-orders"] });
    qc.invalidateQueries({ queryKey: ["commerce-order", orderId] });
  };

  const mutation = useMutation({
    mutationFn: (vars: { to_status: string; cause: string }) =>
      api.POST(`/commerce/orders/${orderId}/transition`, {
        to_status: vars.to_status,
        expected_from_status: status,
        cause: vars.cause,
      }),
    onSuccess: refresh,
    onError: (e) => {
      const err = e as ApiError;
      if (err.status === 409) {
        // Stale view or illegal/blocked transition — re-fetch so the operator
        // sees the authoritative status; never silently clobber.
        refresh();
        alert("Order changed since you loaded it — refreshing. Re-check the status and retry.");
      } else if (err.status === 403) {
        alert("Session is stale. Log out and back in (fresh login code) within 5 minutes, then retry.");
      } else {
        alert("Action failed: " + err.message);
      }
    },
  });

  if (TERMINAL_STATUSES.has(status)) {
    return <p className="text-xs text-zinc-400">This order is final — no further actions.</p>;
  }
  if (actions.length === 0) {
    return <p className="text-xs text-zinc-400">No staff actions available for this status.</p>;
  }

  const run = (a: SliceCAction) => {
    if (mutation.isPending) return;
    if (a.destructive) {
      const reason = window.prompt("Reason for cancelling this order? (required)");
      if (!reason || !reason.trim()) return;
      mutation.mutate({ to_status: a.to_status, cause: reason.trim() });
    } else {
      if (!window.confirm(`${a.label}?`)) return;
      mutation.mutate({ to_status: a.to_status, cause: "" });
    }
  };

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">Actions</h4>
      <div className="flex flex-wrap gap-2">
        {actions.map((a) => (
          <Button
            key={a.to_status}
            variant={a.destructive ? "destructive" : "default"}
            disabled={mutation.isPending}
            onClick={() => run(a)}
          >
            {a.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
