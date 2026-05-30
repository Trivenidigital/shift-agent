import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

// ─────────────────────────────────────────────────────────────────
// P0-5/P0-6 action controls for the Manual Queue drawer.
//
// Owns:
//   • Three action buttons (Complete, Break Glass, Close No Send).
//   • Customer-message preview modal (fetched from
//     /flyer/manual-queue/{id}/action-preview) — shows exact WhatsApp
//     copy or explicit "no push will be sent" before commit.
//   • Close-specific UI: force checkbox + documented bypass-token hint.
//
// Reuses the parent's reason input (drawerReason) and the uploaded asset
// (only Complete needs it). The parent owns the mutations; this
// component hands the operator-confirmed payload back via the supplied
// callbacks. Keeps the mutation invalidation logic + drawer-close logic
// in FlyerAdmin.tsx where the other queries are wired.
// ─────────────────────────────────────────────────────────────────

export type ManualQueueActionKind = "complete" | "break_glass" | "close_no_send" | "resend_status";

export interface ActionPreview {
  action: ManualQueueActionKind;
  project_id: string;
  will_notify: boolean;
  // `customer_text` is the legacy single-string view (kept for back-compat).
  // `customer_messages` is the multi-message variant — Complete previews
  // surface TWO messages (caption + follow-up), close surfaces one, and
  // break_glass surfaces zero. Frontends should prefer `customer_messages`
  // when present.
  customer_text: string | null;
  customer_messages: string[];
  would_notify_chat_id: string;
  chat_id_source: "audit_log" | "primary_chat_id" | "none";
  note?: string;
  reason_code: string;
}

const ACTION_LABEL: Record<ManualQueueActionKind, string> = {
  complete: "Complete with uploaded asset",
  break_glass: "Break-glass",
  close_no_send: "Close (no send)",
  resend_status: "Resend status",
};

const ACTION_HEADING: Record<ManualQueueActionKind, string> = {
  complete: "Complete with uploaded asset",
  break_glass: "Mark Break-glass (no customer push)",
  close_no_send: "Close — no send (proactive customer notification)",
  resend_status: "Resend current status to customer",
};

// resend_status is a read-only nudge: no reason text, no uploaded asset.
// Every other action requires the operator reason; this set lets the
// preview-confirm gate skip the reason requirement for the nudge only.
const ACTIONS_REQUIRING_REASON: ReadonlySet<ManualQueueActionKind> = new Set([
  "complete", "break_glass", "close_no_send",
]);

// Mirrors agents.flyer.manual_queue.CLOSE_FRESH_OK_REASON_TOKENS so the
// operator sees the same tokens documented in the CLI freshness guard.
// If the backend ever changes, the 409 from /close-no-send will surface
// the canonical list — this UI list is a hint, not authority.
const CLOSE_BYPASS_TOKENS = ["duplicate", "test", "superseded", "provider_unavailable_after_retry"];

export interface ManualQueueActionsProps {
  projectId: string;
  reasonCode: string;
  reason: string;
  canComplete: boolean;
  // resend_status is gated to active manual-queue rows (queued/in_progress);
  // the parent computes this so the nudge button is only offered when the
  // backend would accept it (otherwise the POST 409s).
  canResendStatus: boolean;
  completeAsset: {
    filename: string;
    mimeType: string;
    sizeBytes: number;
    url: string;
  } | null;
  onCompleteConfirmed: () => void;
  onBreakGlassConfirmed: () => void;
  onCloseNoSendConfirmed: (opts: { force: boolean }) => void;
  onResendStatusConfirmed: () => void;
  pendingAction: ManualQueueActionKind | null;
  errors: { complete: string; breakGlass: string; closeNoSend: string; resendStatus: string };
}

export function ManualQueueActions(props: ManualQueueActionsProps) {
  const {
    projectId, reasonCode, reason, canComplete, canResendStatus, completeAsset,
    onCompleteConfirmed, onBreakGlassConfirmed, onCloseNoSendConfirmed,
    onResendStatusConfirmed, pendingAction, errors,
  } = props;

  const reasonOk = reason.trim().length >= 5;
  const [previewing, setPreviewing] = useState<ManualQueueActionKind | null>(null);
  const [preview, setPreview] = useState<ActionPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [forceClose, setForceClose] = useState(false);

  const closePreviewModal = useCallback(() => {
    setPreviewing(null);
    setPreview(null);
    setPreviewError(null);
    setForceClose(false);
  }, []);

  const openPreview = useCallback(
    async (action: ManualQueueActionKind) => {
      setPreviewing(action);
      setPreview(null);
      setPreviewError(null);
      setForceClose(false);
      setPreviewBusy(true);
      try {
        const result = await api.GET<ActionPreview>(
          `/flyer/manual-queue/${projectId}/action-preview?action=${action}`,
        );
        setPreview(result);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Preview failed";
        setPreviewError(message);
      } finally {
        setPreviewBusy(false);
      }
    },
    [projectId],
  );

  const confirmActive = pendingAction !== null;
  const currentError =
    previewing === "complete" ? errors.complete
    : previewing === "break_glass" ? errors.breakGlass
    : previewing === "close_no_send" ? errors.closeNoSend
    : previewing === "resend_status" ? errors.resendStatus
    : "";

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={!canComplete || confirmActive}
          onClick={() => openPreview("complete")}
        >
          {ACTION_LABEL.complete}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!reasonOk || confirmActive}
          onClick={() => openPreview("break_glass")}
          className="border-rose-300 text-rose-700 hover:bg-rose-50"
        >
          {ACTION_LABEL.break_glass}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!reasonOk || confirmActive}
          onClick={() => openPreview("close_no_send")}
          className="border-zinc-400 text-zinc-700 hover:bg-zinc-100"
        >
          {ACTION_LABEL.close_no_send}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!canResendStatus || confirmActive}
          onClick={() => openPreview("resend_status")}
          className="border-sky-300 text-sky-700 hover:bg-sky-50"
        >
          {ACTION_LABEL.resend_status}
        </Button>
      </div>

      {previewing && (
        <ActionPreviewModal
          action={previewing}
          preview={preview}
          previewBusy={previewBusy}
          previewError={previewError}
          reasonCode={reasonCode}
          reasonText={reason}
          reasonOk={reasonOk}
          canComplete={canComplete}
          completeAsset={completeAsset}
          forceClose={forceClose}
          onForceChange={setForceClose}
          actionPending={confirmActive}
          actionError={currentError}
          onCancel={closePreviewModal}
          onConfirm={() => {
            if (previewing === "complete") onCompleteConfirmed();
            else if (previewing === "break_glass") onBreakGlassConfirmed();
            else if (previewing === "resend_status") {
              // resend_status intentionally keeps the drawer open to show the
              // send result, but the preview modal MUST close on confirm —
              // otherwise the re-enabled confirm button invites duplicate
              // WhatsApp nudges before the operator sees the outcome. (The
              // other actions transition/close the row, so re-confirm 409s.)
              onResendStatusConfirmed();
              closePreviewModal();
            }
            else onCloseNoSendConfirmed({ force: forceClose });
          }}
        />
      )}
    </div>
  );
}

interface ActionPreviewModalProps {
  action: ManualQueueActionKind;
  preview: ActionPreview | null;
  previewBusy: boolean;
  previewError: string | null;
  reasonCode: string;
  reasonText: string;
  reasonOk: boolean;
  canComplete: boolean;
  completeAsset: {
    filename: string;
    mimeType: string;
    sizeBytes: number;
    url: string;
  } | null;
  forceClose: boolean;
  onForceChange: (val: boolean) => void;
  actionPending: boolean;
  actionError: string;
  onCancel: () => void;
  onConfirm: () => void;
}

function ActionPreviewModal(props: ActionPreviewModalProps) {
  const {
    action, preview, previewBusy, previewError,
    reasonCode, reasonText, reasonOk, canComplete,
    completeAsset, forceClose, onForceChange,
    actionPending, actionError, onCancel, onConfirm,
  } = props;

  // PR #133 review HIGH-2: confirm MUST be disabled whenever the operator
  // hasn't seen the customer-visible outcome — including when the preview
  // fetch errored. Otherwise the operator can commit blind to "what the
  // customer will receive", which defeats the P0-5 goal. If the operator
  // wants to bypass preview they have to first dismiss this modal and
  // re-trigger the action so they consciously re-attempt the preview.
  const confirmDisabled =
    actionPending
    || (ACTIONS_REQUIRING_REASON.has(action) && !reasonOk)
    || (action === "complete" && !canComplete)
    || previewBusy
    || preview === null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg overflow-y-auto rounded-md border border-zinc-300 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
          <div className="text-sm font-semibold text-zinc-800">{ACTION_HEADING[action]}</div>
          <Button variant="outline" size="sm" onClick={onCancel}>Cancel</Button>
        </div>
        <div className="space-y-3 p-4 text-sm">
          {previewBusy && <div className="text-xs text-zinc-500">Loading preview…</div>}
          {previewError && (
            <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700">
              Could not load preview: {previewError}
            </div>
          )}
          {preview && (
            <>
              <PreviewBody preview={preview} />
              {ACTIONS_REQUIRING_REASON.has(action) && (
                <div className="rounded border border-zinc-200 px-2 py-1 text-xs text-zinc-700">
                  <div className="text-xs uppercase tracking-wide text-zinc-500">Operator reason</div>
                  <div className={cn("mt-1 italic", reasonOk ? "text-zinc-800" : "text-rose-700")}>
                    {reasonOk ? reasonText : "(reason must be ≥ 5 characters — close this modal and update the reason input first)"}
                  </div>
                </div>
              )}
              {action === "close_no_send" && (
                <CloseSpecificControls
                  reasonCode={reasonCode}
                  forceClose={forceClose}
                  onForceChange={onForceChange}
                />
              )}
              {action === "complete" && !canComplete && (
                <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
                  Upload an approved asset before Complete. The Complete button stays disabled until both reason and upload are ready.
                </div>
              )}
              {action === "complete" && completeAsset && (
                <CompleteAssetConfirmation asset={completeAsset} />
              )}
            </>
          )}
          {actionError && (
            <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700">{actionError}</div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-zinc-200 bg-zinc-50 px-4 py-3">
          <Button variant="outline" size="sm" onClick={onCancel}>Cancel</Button>
          <Button
            size="sm"
            disabled={confirmDisabled}
            onClick={onConfirm}
            className={cn(
              action === "break_glass" && "border-rose-300 bg-rose-600 text-white hover:bg-rose-700",
            )}
          >
            {actionPending ? "Working…" : `Confirm ${ACTION_LABEL[action]}`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function CompleteAssetConfirmation(props: {
  asset: {
    filename: string;
    mimeType: string;
    sizeBytes: number;
    url: string;
  };
}) {
  const { asset } = props;
  const isImage = asset.mimeType.startsWith("image/");
  const isPdf = asset.mimeType === "application/pdf";
  return (
    <div className="rounded border border-amber-200 bg-amber-50 px-2 py-2 text-xs text-amber-900">
      <div className="font-semibold">Final asset confirmation</div>
      <div className="mt-1">
        Confirm this visible asset is the operator-reviewed output that will be attached to the project.
      </div>
      <div className="mt-1 font-mono text-[11px]">
        {asset.filename} / {asset.mimeType} / {Math.round(asset.sizeBytes / 1024)} KB
      </div>
      {isImage && (
        <img
          src={asset.url}
          alt="final confirmation asset"
          className="mt-2 h-48 w-full rounded border border-amber-300 bg-white object-contain"
          loading="lazy"
        />
      )}
      {isPdf && (
        <a
          href={asset.url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block rounded border border-amber-300 bg-white px-2 py-1 text-xs text-brand-700 underline-offset-2 hover:underline"
        >
          Open final PDF in new tab
        </a>
      )}
      {!isImage && !isPdf && (
        <a
          href={asset.url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block rounded border border-amber-300 bg-white px-2 py-1 text-xs text-brand-700 underline-offset-2 hover:underline"
        >
          Download final asset
        </a>
      )}
    </div>
  );
}

function PreviewBody({ preview }: { preview: ActionPreview }) {
  const messages = preview.customer_messages.length > 0
    ? preview.customer_messages
    : preview.customer_text ? [preview.customer_text] : [];

  if (preview.will_notify && messages.length > 0) {
    return (
      <div className="space-y-2">
        <div className="text-xs font-semibold text-emerald-800">
          Customer will see {messages.length === 1 ? "this WhatsApp message:" : `these ${messages.length} WhatsApp messages, in order:`}
        </div>
        {messages.map((msg, i) => (
          <div key={i} className="rounded border border-emerald-200 bg-emerald-50 px-2 py-2 text-xs">
            {messages.length > 1 && (
              <div className="text-[10px] uppercase tracking-wide text-emerald-700">Message {i + 1} of {messages.length}</div>
            )}
            <pre className="mt-1 whitespace-pre-wrap font-sans text-emerald-900">{msg}</pre>
          </div>
        ))}
        <div className="text-xs text-zinc-600">
          Notify chat_id: <span className="font-mono">{preview.would_notify_chat_id || "(none)"}</span>
          {preview.chat_id_source && (
            <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-700">
              via {preview.chat_id_source}
            </span>
          )}
        </div>
        {preview.note && <div className="text-xs text-zinc-500">{preview.note}</div>}
      </div>
    );
  }
  if (!preview.will_notify && messages.length > 0) {
    // Complete-style: text exists but isn't a proactive push.
    return (
      <div className="space-y-2">
        <div className="text-xs font-semibold text-zinc-800">
          No proactive push. Customer sees {messages.length === 1 ? "this message" : `these ${messages.length} messages, in order,`} on the next preview send:
        </div>
        {messages.map((msg, i) => (
          <div key={i} className="rounded border border-zinc-200 bg-zinc-50 px-2 py-2 text-xs">
            {messages.length > 1 && (
              <div className="text-[10px] uppercase tracking-wide text-zinc-600">Message {i + 1} of {messages.length}</div>
            )}
            <pre className="mt-1 whitespace-pre-wrap font-sans text-zinc-700">{msg}</pre>
          </div>
        ))}
        {preview.note && <div className="text-xs text-zinc-500">{preview.note}</div>}
      </div>
    );
  }
  // Break-glass: no push at all.
  return (
    <div className="rounded border border-amber-200 bg-amber-50 px-2 py-2 text-xs text-amber-900">
      <div className="font-semibold">No customer WhatsApp message will be sent.</div>
      {preview.note && <div className="mt-1">{preview.note}</div>}
    </div>
  );
}

function CloseSpecificControls(props: {
  reasonCode: string;
  forceClose: boolean;
  onForceChange: (val: boolean) => void;
}) {
  const { forceClose, onForceChange } = props;
  return (
    <div className="rounded border border-zinc-200 px-2 py-2 text-xs">
      <div className="text-xs uppercase tracking-wide text-zinc-500">Freshness guard</div>
      <div className="mt-1 text-zinc-700">
        The agent's `enforce_close_freshness_guard` rejects rows queued less than 30 minutes ago
        unless one of the documented tokens appears in the reason, or `force` is set.
      </div>
      <ul className="mt-1 list-disc pl-5 font-mono text-[11px] text-zinc-700">
        {CLOSE_BYPASS_TOKENS.map((t) => (<li key={t}>{t}</li>))}
      </ul>
      <label className="mt-2 flex items-center gap-2 text-xs text-zinc-700">
        <input
          type="checkbox"
          checked={forceClose}
          onChange={(e) => onForceChange(e.target.checked)}
        />
        <span>Force close (bypass freshness guard — use sparingly)</span>
      </label>
    </div>
  );
}

export type ManualQueueActionsPropsShape = ManualQueueActionsProps;
