import type React from "react";
import { ExternalLink } from "lucide-react";

type BadgeTone = "neutral" | "green" | "amber" | "red" | "blue";

export interface EvidenceAsset {
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

export interface EvidenceTimelineEvent {
  ts: string;
  event: string;
  detail: string;
  source: "project_state" | "decisions" | "cockpit_audit" | string;
}

export interface EvidenceDetail {
  assets: EvidenceAsset[];
  final_assets: EvidenceAsset[];
  timeline: EvidenceTimelineEvent[];
}

const FINAL_OUTPUT_ORDER = [
  "whatsapp_image",
  "instagram_post",
  "instagram_story",
  "printable_pdf",
];

const OUTPUT_LABELS: Record<string, string> = {
  whatsapp_image: "WhatsApp image",
  instagram_post: "Instagram post",
  instagram_story: "Instagram story",
  printable_pdf: "Printable PDF",
  concept_preview: "Preview",
};

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: BadgeTone }) {
  const cls: Record<BadgeTone, string> = {
    neutral: "bg-zinc-100 text-zinc-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    red: "bg-rose-50 text-rose-700",
    blue: "bg-brand-50 text-brand-700",
  };
  return (
    <span className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium ${cls[tone]}`}>
      {children}
    </span>
  );
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "size unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceTone(source: string): BadgeTone {
  if (source === "decisions") return "blue";
  if (source === "cockpit_audit") return "amber";
  return "neutral";
}

function deliveryTone(status: string): BadgeTone {
  if (status === "sent" || status === "delivered") return "green";
  if (status === "failed") return "red";
  if (status === "pending") return "amber";
  return "neutral";
}

function assetSortKey(asset: EvidenceAsset): string {
  const idx = FINAL_OUTPUT_ORDER.indexOf(asset.output_format);
  return `${idx === -1 ? 99 : idx}-${asset.asset_id}`;
}

function dimensionsLabel(asset: EvidenceAsset): string {
  if (asset.width && asset.height) return `${asset.width}x${asset.height}`;
  return "dimensions unknown";
}

function AssetPreviewCard({ asset, label }: { asset: EvidenceAsset; label?: string }) {
  const isImage = asset.mime_type.startsWith("image/");
  const isPdf = asset.mime_type === "application/pdf";
  const hashLabel = asset.sha256_short || asset.sha256.slice(0, 16) || asset.file_sha256.slice(0, 16) || "no hash";

  return (
    <div className="rounded-md border border-zinc-200 bg-white p-2">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-zinc-900">
            {label ?? OUTPUT_LABELS[asset.output_format] ?? asset.kind}
          </div>
          <div className="font-mono text-[10px] text-zinc-500">
            {asset.asset_id} / {asset.kind}
          </div>
        </div>
        <Badge tone={deliveryTone(asset.delivery_status)}>{asset.delivery_status || "unknown"}</Badge>
      </div>

      {isImage && (
        <img
          src={asset.media_url}
          alt={`${label ?? asset.kind} ${asset.asset_id}`}
          className="h-36 w-full rounded border border-zinc-200 bg-zinc-50 object-contain"
          loading="lazy"
        />
      )}
      {isPdf && (
        <a
          href={asset.media_url}
          target="_blank"
          rel="noreferrer"
          className="flex h-36 w-full items-center justify-center gap-2 rounded border border-zinc-200 bg-zinc-50 text-xs text-brand-700 underline-offset-2 hover:underline"
        >
          Open PDF <ExternalLink size={12} />
        </a>
      )}
      {!isImage && !isPdf && (
        <a
          href={asset.media_url}
          target="_blank"
          rel="noreferrer"
          className="flex h-36 w-full items-center justify-center gap-2 rounded border border-zinc-200 bg-zinc-50 text-xs text-brand-700 underline-offset-2 hover:underline"
        >
          Download asset <ExternalLink size={12} />
        </a>
      )}

      <dl className="mt-2 grid grid-cols-[88px_1fr] gap-x-2 gap-y-1 text-[10px] text-zinc-600">
        <dt className="text-zinc-400">Format</dt>
        <dd className="break-words">{asset.output_format || "unknown"}</dd>
        <dt className="text-zinc-400">MIME</dt>
        <dd className="break-words">{asset.mime_type || "unknown"}</dd>
        <dt className="text-zinc-400">Dimensions</dt>
        <dd>{dimensionsLabel(asset)}</dd>
        <dt className="text-zinc-400">Size</dt>
        <dd>{formatBytes(asset.size_bytes)}</dd>
        <dt className="text-zinc-400">Source</dt>
        <dd className="break-words">{asset.source || "unknown"}</dd>
        <dt className="text-zinc-400">Hash</dt>
        <dd className="break-all font-mono">{hashLabel}</dd>
        {asset.outbound_message_id && (
          <>
            <dt className="text-zinc-400">Outbound</dt>
            <dd className="break-all font-mono">{asset.outbound_message_id}</dd>
          </>
        )}
        {asset.delivered_at && (
          <>
            <dt className="text-zinc-400">Delivered</dt>
            <dd>{new Date(asset.delivered_at).toLocaleString()}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

export function FlyerProjectEvidenceDrawer({ detail }: { detail: EvidenceDetail }) {
  const referenceAssets = detail.assets.filter((a) => a.kind === "reference_image" || a.kind === "logo");
  const finalAssets = [...detail.final_assets].sort((a, b) => assetSortKey(a).localeCompare(assetSortKey(b)));

  return (
    <div className="space-y-4">
      {referenceAssets.length > 0 && (
        <div className="rounded-md border border-zinc-200 px-3 py-2">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Source / reference</div>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {referenceAssets.map((asset) => (
              <AssetPreviewCard
                key={asset.asset_id}
                asset={asset}
                label={asset.kind === "logo" ? "Logo" : "Reference"}
              />
            ))}
          </div>
        </div>
      )}

      <div className="rounded-md border border-zinc-200 px-3 py-2">
        <div className="text-xs uppercase tracking-wide text-zinc-500">Final output assets</div>
        {finalAssets.length === 0 ? (
          <div className="mt-2 rounded border border-zinc-100 bg-zinc-50 px-2 py-3 text-xs text-zinc-500">
            No final package assets are attached yet.
          </div>
        ) : (
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {finalAssets.map((asset) => (
              <AssetPreviewCard key={asset.asset_id} asset={asset} />
            ))}
          </div>
        )}
      </div>

      <div className="rounded-md border border-zinc-200 px-3 py-2 text-xs">
        <div className="text-xs uppercase tracking-wide text-zinc-500">Timeline</div>
        <ul className="mt-2 space-y-2">
          {detail.timeline.map((row, i) => (
            <li
              key={`${row.ts}-${row.event}-${i}`}
              className="grid grid-cols-[130px_1fr] gap-2 border-t border-zinc-100 pt-2 first:border-t-0 first:pt-0"
            >
              <span className="font-mono text-[10px] text-zinc-500">{new Date(row.ts).toLocaleString()}</span>
              <span>
                <span className="mr-2 font-mono text-zinc-800">{row.event}</span>
                <Badge tone={sourceTone(row.source)}>{row.source || "project_state"}</Badge>
                {row.detail && <span className="mt-1 block text-zinc-600">{row.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
