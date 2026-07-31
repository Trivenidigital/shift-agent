import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Badge, ErrorState, Skeleton, money, mutationErrorMessage } from "./shared";
import type {
  ActionResult,
  MenuResponse,
  PricebookResponse,
  QuotePreviewResponse,
} from "./types";

export function MenuPricingPanel() {
  const menu = useQuery<MenuResponse>({
    queryKey: ["catering-menu"],
    queryFn: () => api.GET<MenuResponse>("/catering/menu"),
  });
  const pricebook = useQuery<PricebookResponse>({
    queryKey: ["catering-pricebook"],
    queryFn: () => api.GET<PricebookResponse>("/catering/pricebook"),
  });

  return (
    <div className="space-y-4">
      {pricebook.data?.placeholder && (
        <Card>
          <CardContent className="text-sm text-amber-800">
            <span className="font-medium">⚠ This pricebook is placeholder data.</span> Quotes computed
            from it are marked <em>pending owner review</em> and will not be delivered to customers.
            Import real prices below to arm pricing.
          </CardContent>
        </Card>
      )}

      <PricebookSummary query={pricebook} />
      <PricePreviewForm pricebook={pricebook.data} />
      <MenuTable query={menu} />
      <PricebookImportBox />
    </div>
  );
}

function PricebookSummary({ query }: { query: ReturnType<typeof useQuery<PricebookResponse>> }) {
  const { data, isLoading, isError, error } = query;
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>Pricebook</CardTitle>
        {data?.available && (
          <span className="text-xs text-zinc-500">
            v{data.version} · effective {data.effective_date} · {data.currency}
          </span>
        )}
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton rows={3} />}
        {isError && <ErrorState error={error} what="the pricebook" />}
        {data && !data.available && (
          <p className="text-sm text-zinc-500">
            {data.degraded
              ? `Pricebook could not be read: ${data.degraded}`
              : "No pricebook imported yet — quotes cannot be priced until one is."}
          </p>
        )}
        {data?.available && (
          <div className="space-y-3 text-xs">
            <div>
              <div className="mb-1 font-medium text-zinc-700">
                Per-person packages ({data.per_person_packages.length})
              </div>
              {data.per_person_packages.length === 0 ? (
                <p className="text-zinc-400">None defined.</p>
              ) : (
                <table className="w-full">
                  <tbody>
                    {data.per_person_packages.map((p) => (
                      <tr key={p.id} className="border-b border-zinc-100">
                        <td className="py-1 font-mono text-zinc-500">{p.id}</td>
                        <td className="py-1">{p.name}</td>
                        <td className="py-1 text-zinc-500">min {p.min_guests}</td>
                        <td className="py-1 text-right font-mono">{money(p.price_per_person_cents)}/guest</td>
                        <td className="py-1 text-right">{p.active ? "" : <Badge tone="red">inactive</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <div className="mb-1 font-medium text-zinc-700">Fixed fees ({data.fixed_fees.length})</div>
                {data.fixed_fees.length === 0 ? (
                  <p className="text-zinc-400">None.</p>
                ) : (
                  <ul className="space-y-0.5">
                    {data.fixed_fees.map((f) => (
                      <li key={f.id} className="flex justify-between">
                        <span>
                          {f.name} <span className="text-zinc-400">({f.per_unit ?? "flat"})</span>
                        </span>
                        <span className="font-mono">{money(f.amount_cents)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <div className="mb-1 font-medium text-zinc-700">
                  Approved discounts ({data.approved_discounts.length})
                </div>
                {data.approved_discounts.length === 0 ? (
                  <p className="text-zinc-400">None.</p>
                ) : (
                  <ul className="space-y-0.5">
                    {data.approved_discounts.map((d) => (
                      <li key={d.id} className="flex justify-between">
                        <span>{d.name}</span>
                        <span className="font-mono">
                          {d.kind === "percent" ? `${(d.value / 100).toFixed(2)}%` : money(d.value)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            <div className="text-zinc-500">
              Tax {(data.tax_rate_bps / 100).toFixed(2)}% · {data.item_price_override_count} item price
              override(s) · updated by {data.updated_by || "—"}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PricePreviewForm({ pricebook }: { pricebook: PricebookResponse | undefined }) {
  const [guestCount, setGuestCount] = useState("100");
  const [packageId, setPackageId] = useState("");
  const [discountId, setDiscountId] = useState("");
  const [submitted, setSubmitted] = useState<{ guests: string; pkg: string; discount: string } | null>(null);

  const { data, isFetching, isError, error } = useQuery<QuotePreviewResponse>({
    queryKey: ["catering-quote-preview", submitted],
    queryFn: () => {
      const params = new URLSearchParams({ guest_count: submitted!.guests });
      if (submitted!.pkg) params.set("package_id", submitted!.pkg);
      if (submitted!.discount) params.set("discount_id", submitted!.discount);
      return api.GET<QuotePreviewResponse>(`/catering/quote-preview?${params.toString()}`);
    },
    enabled: submitted !== null,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Price calculator</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-zinc-500">
          Runs the same deterministic kernel the agent uses. Read-only — nothing is saved or sent.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-zinc-500">
            Guests
            <Input
              className="mt-1 w-24"
              type="number"
              min={1}
              value={guestCount}
              onChange={(e) => setGuestCount(e.target.value)}
            />
          </label>
          <label className="text-xs text-zinc-500">
            Package
            <select
              className="mt-1 block h-9 rounded-md border border-zinc-300 px-2 text-sm"
              value={packageId}
              onChange={(e) => setPackageId(e.target.value)}
            >
              <option value="">(à la carte only)</option>
              {(pricebook?.per_person_packages ?? []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <label className="text-xs text-zinc-500">
            Discount
            <select
              className="mt-1 block h-9 rounded-md border border-zinc-300 px-2 text-sm"
              value={discountId}
              onChange={(e) => setDiscountId(e.target.value)}
            >
              <option value="">(none)</option>
              {(pricebook?.approved_discounts ?? []).map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </label>
          <Button
            loading={isFetching}
            disabled={!guestCount || Number(guestCount) < 1}
            onClick={() => setSubmitted({ guests: guestCount, pkg: packageId, discount: discountId })}
          >
            Calculate
          </Button>
        </div>

        {isError && <ErrorState error={error} what="the price preview" />}
        {data && !data.available && (
          <p className="text-sm text-amber-800">{data.unavailable_reason ?? "Preview unavailable."}</p>
        )}
        {data?.available && (
          <div className="rounded border border-zinc-200 p-3 text-xs">
            <div className="mb-2 flex items-center gap-2">
              <Badge tone={data.deliverable ? "green" : "amber"}>{data.price_status}</Badge>
              {!data.deliverable && (
                <span className="text-amber-800">not deliverable to a customer as-is</span>
              )}
            </div>
            <table className="w-full">
              <tbody>
                {data.lines.map((line, i) => (
                  <tr key={`${line.name}-${i}`} className="border-b border-zinc-100">
                    <td className="py-1">
                      {line.qty}× {line.name}
                      {line.source && <span className="ml-1 text-zinc-400">({line.source})</span>}
                    </td>
                    <td className="py-1 text-right font-mono">
                      {line.extended_cents == null ? (
                        <span className="text-rose-700">unpriced</span>
                      ) : (
                        money(line.extended_cents)
                      )}
                    </td>
                  </tr>
                ))}
                {data.fees.map((fee) => (
                  <tr key={fee.fee_id} className="border-b border-zinc-100 text-zinc-600">
                    <td className="py-1">{fee.name}</td>
                    <td className="py-1 text-right font-mono">
                      {fee.extended_cents == null ? (
                        <span className="text-rose-700">needs a multiplier</span>
                      ) : (
                        money(fee.extended_cents)
                      )}
                    </td>
                  </tr>
                ))}
                <tr className="text-zinc-600">
                  <td className="py-1">Subtotal</td>
                  <td className="py-1 text-right font-mono">{money(data.subtotal_cents)}</td>
                </tr>
                {data.discount_cents > 0 && (
                  <tr className="text-emerald-700">
                    <td className="py-1">Discount ({data.discount_name})</td>
                    <td className="py-1 text-right font-mono">−{money(data.discount_cents)}</td>
                  </tr>
                )}
                <tr className="text-zinc-600">
                  <td className="py-1">Tax ({(data.tax_rate_bps / 100).toFixed(2)}%)</td>
                  <td className="py-1 text-right font-mono">{money(data.tax_cents)}</td>
                </tr>
                <tr className="font-semibold">
                  <td className="py-1">Total</td>
                  <td className="py-1 text-right font-mono">{money(data.total_cents)}</td>
                </tr>
                <tr className="text-zinc-400">
                  <td className="py-1">Per guest</td>
                  <td className="py-1 text-right font-mono">{money(data.total_per_guest_cents)}</td>
                </tr>
              </tbody>
            </table>
            {data.flags.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-amber-800">
                {data.flags.map((flag) => (
                  <li key={flag}>⚠ {flag}</li>
                ))}
              </ul>
            )}
            <div className="mt-2 text-zinc-400">
              pricebook v{data.pricebook_version ?? "—"} · menu v{data.menu_version ?? "—"}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MenuTable({ query }: { query: ReturnType<typeof useQuery<MenuResponse>> }) {
  const { data, isLoading, isError, error } = query;
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <CardTitle>Menu</CardTitle>
        {data?.available && (
          <span className="text-xs text-zinc-500">
            v{data.version} · {data.items.length} item(s) · updated by {data.updated_by || "—"}
          </span>
        )}
      </CardHeader>
      <CardContent className="p-0">
        {isLoading && <div className="p-5"><Skeleton rows={4} /></div>}
        {isError && <div className="p-5"><ErrorState error={error} what="the menu" /></div>}
        {data && !data.available && (
          <p className="p-5 text-sm text-zinc-500">
            {data.degraded ? `Menu could not be read: ${data.degraded}` : "No menu on file yet."}
          </p>
        )}
        {data?.available && data.items.length === 0 && (
          <p className="p-5 text-sm text-zinc-500">The menu is on file but has no items.</p>
        )}
        {data?.available && data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b border-zinc-200 bg-zinc-50">
                <tr className="text-left text-zinc-500">
                  <th className="px-3 py-1.5">Item</th>
                  <th className="px-3 py-1.5">Category</th>
                  <th className="px-3 py-1.5">Dietary</th>
                  <th className="px-3 py-1.5">Serves</th>
                  <th className="px-3 py-1.5 text-right">Price</th>
                  <th className="px-3 py-1.5">Availability</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.name} className="border-b border-zinc-100">
                    <td className="px-3 py-1.5">{item.name}</td>
                    <td className="px-3 py-1.5 text-zinc-500">{item.category}</td>
                    <td className="px-3 py-1.5 text-zinc-500">{item.dietary_tags.join(", ") || "—"}</td>
                    <td className="px-3 py-1.5 text-zinc-500">{item.serves ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {item.price_usd == null ? "—" : `$${item.price_usd.toFixed(2)}`}
                    </td>
                    <td className="px-3 py-1.5">
                      {item.available ? <Badge tone="green">available</Badge> : <Badge tone="red">unavailable</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data?.notes && <p className="border-t border-zinc-100 p-3 text-xs text-zinc-500">{data.notes}</p>}
      </CardContent>
    </Card>
  );
}

function PricebookImportBox() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const importer = useMutation({
    mutationFn: (vars: { document: unknown; dry_run: boolean }) =>
      api.POST<ActionResult>("/catering/pricebook/import", vars),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["catering-pricebook"] });
      qc.invalidateQueries({ queryKey: ["catering-dashboard"] });
    },
  });

  const submit = (dryRun: boolean) => {
    setParseError(null);
    let document: unknown;
    try {
      document = JSON.parse(text);
    } catch (e) {
      setParseError(`Not valid JSON: ${(e as Error).message}`);
      return;
    }
    if (!dryRun && !window.confirm("Replace the live pricebook with this document?")) return;
    importer.mutate({ document, dry_run: dryRun });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import pricebook</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-zinc-500">
          Paste a pricebook JSON document. Validate first with a dry run — it reports what would
          change without writing. Importing replaces the live pricebook (the previous version is
          archived by the agent) and needs a login code issued in the last 5 minutes.
        </p>
        <textarea
          className="h-40 w-full rounded-md border border-zinc-300 p-2 font-mono text-xs"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='{"version": 2, "effective_date": "2026-08-01", "currency": "USD", ...}'
        />
        <div className="flex gap-2">
          <Button variant="outline" disabled={!text.trim() || importer.isPending} onClick={() => submit(true)}>
            Validate (dry run)
          </Button>
          <Button disabled={!text.trim() || importer.isPending} loading={importer.isPending} onClick={() => submit(false)}>
            Import
          </Button>
        </div>
        {parseError && <p className="text-xs text-rose-700">{parseError}</p>}
        {importer.isError && <p className="text-xs text-rose-700">{mutationErrorMessage(importer.error)}</p>}
        {importer.isSuccess && (
          <pre className="whitespace-pre-wrap rounded bg-zinc-50 p-2 text-xs text-emerald-800">
            {importer.data.stdout || "Import completed."}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}
