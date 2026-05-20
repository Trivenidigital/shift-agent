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

export type ManualQueueActionKind = "complete" | "break_glass" | "close_no_send";

export interface ActionPreview {
  action: ManualQueueActionKind;
  project_id: string;
  will_notify: boolean;
  customer_text: string | null;
  would_notify_chat_id: string;
  note?: string;
  reason_code: string;
}

const ACTION_LABEL: Record<ManualQueueActionKind, string> = {
  complete: "Complete with uploaded asset",
  break_glass: "Break-glass",
  close_no_send: "Close (no send)",
};

const ACTION_HEADING: Record<ManualQueueActionKind, string> = {
  complete: "Complete with uploaded asset",
  break_glass: "Mark Break-glass (no customer push)",
  close_no_send: "Close — no send (proactive customer notification)",
};

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
  onCompleteConfirmed: () => void;
  onBreakGlassConfirmed: () => void;
  onCloseNoSendConfirmed: (opts: { force: boolean }) => void;
  pendingAction: ManualQueueActionKind | null;
  errors: { complete: string; breakGlass: string; closeNoSend: string };
}

export function ManualQueueActions(props: ManualQueueActionsProps) {
  const {
    projectId, reasonCode, reason, canComplete,
    onCompleteConfirmed, onBreakGlassConfirmed, onCloseNoSendConfirmed,
    pendingAction, errors,
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
          forceClose={forceClose}
          onForceChange={setForceClose}
          actionPending={confirmActive}
          actionError={currentError}
          onCancel={closePreviewModal}
          onConfirm={() => {
            if (previewing === "complete") onCompleteConfirmed();
            else if (previewing === "break_glass") onBreakGlassConfirmed();
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
    forceClose, onForceChange,
    actionPending, actionError, onCancel, onConfirm,
  } = props;

  const confirmDisabled =
    actionPending
    || !reasonOk
    || (action === "complete" && !canComplete)
    || previewBusy
    || (preview === null && !previewError);

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
              <div className="rounded border border-zinc-200 px-2 py-1 text-xs text-zinc-700">
                <div className="text-xs uppercase tracking-wide text-zinc-500">Operator reason</div>
                <div className={cn("mt-1 italic", reasonOk ? "text-zinc-800" : "text-rose-700")}>
                  {reasonOk ? reasonText : "(reason must be ≥ 5 characters — close this modal and update the reason input first)"}
                </div>
              </div>
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

function PreviewBody({ preview }: { preview: ActionPreview }) {
  if (preview.will_notify && preview.customer_text) {
    return (
      <div className="space-y-2">
        <div className="rounded border border-emerald-200 bg-emerald-50 px-2 py-2 text-xs">
          <div className="font-semibold text-emerald-800">Customer will see this WhatsApp message:</div>
          <pre className="mt-1 whitespace-pre-wrap font-sans text-emerald-900">{preview.customer_text}</pre>
        </div>
        <div className="text-xs text-zinc-600">
          Notify chat_id: <span className="font-mono">{preview.would_notify_chat_id || "(none)"}</span>
        </div>
        {preview.note && <div className="text-xs text-zinc-500">{preview.note}</div>}
      </div>
    );
  }
  if (!preview.will_notify && preview.customer_text) {
    // Complete-style preview: text exists but isn't a proactive push.
    return (
      <div className="space-y-2">
        <div className="rounded border border-zinc-200 bg-zinc-50 px-2 py-2 text-xs">
          <div className="font-semibold text-zinc-800">No proactive push. Customer sees this on the next preview send:</div>
          <pre className="mt-1 whitespace-pre-wrap font-sans text-zinc-700">{preview.customer_text}</pre>
        </div>
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
