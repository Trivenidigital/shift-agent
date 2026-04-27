import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

interface WhatsAppStatus {
  paired: boolean;
  me_id: string | null;
  self_chat_jid: string | null;
  bridge_uptime_seconds: number | null;
  bridge_status: string | null;
}

interface PairSessionResp { session_id: string; expires_at: string }

export function WhatsApp() {
  const qc = useQueryClient();
  const { data: status } = useQuery<WhatsAppStatus>({
    queryKey: ["wa-status"],
    queryFn: () => api.GET<WhatsAppStatus>("/whatsapp/status"),
    refetchInterval: 5_000,
  });

  const [sid, setSid] = useState<string | null>(null);
  // QR comes as ASCII-block rows from the bridge (qrcode-terminal output).
  // We render it as <pre>; bridge.js doesn't emit the raw Baileys QR string,
  // and patching bridge.js is out of scope for this PR.
  const [qrLines, setQrLines] = useState<string[]>([]);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const startPair = useMutation({
    mutationFn: () => api.POST<PairSessionResp>("/whatsapp/repair"),
    onSuccess: (r) => { setSid(r.session_id); setLogLines([]); setQrLines([]); setDone(false); setStreamError(null); },
  });
  const cancelPair = useMutation({
    mutationFn: () => sid ? api.POST(`/whatsapp/repair/${sid}/cancel`) : Promise.resolve(),
    onSuccess: () => { esRef.current?.close(); setSid(null); setQrLines([]); },
  });
  const unlink = useMutation({
    mutationFn: () => api.POST("/whatsapp/unlink"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wa-status"] }),
  });

  // SSE wiring — close immediately on error to prevent default-reconnect listener leaks.
  useEffect(() => {
    if (!sid) return;
    const es = new EventSource(`/api/whatsapp/repair/${sid}/stream`, { withCredentials: true });
    esRef.current = es;
    es.addEventListener("qr_line", (ev: MessageEvent) => {
      setQrLines((l) => {
        const next = [...l, ev.data as string];
        return next.length > 40 ? next.slice(-33) : next;
      });
    });
    es.addEventListener("log", (ev: MessageEvent) => setLogLines((l) => [...l, ev.data as string]));
    es.addEventListener("connected", () => setLogLines((l) => [...l, "✓ WhatsApp connected"]));
    es.addEventListener("complete", (ev: MessageEvent) => {
      setDone(true);
      setQrLines([]);
      try { setLogLines((l) => [...l, `✓ Pairing complete: ${ev.data}`]); } catch {}
      es.close();
      qc.invalidateQueries({ queryKey: ["wa-status"] });
      qc.invalidateQueries({ queryKey: ["config"] });
    });
    es.addEventListener("error", () => {
      es.close();
      setStreamError("Stream error — server may be restarting. Cancel and try again.");
    });
    return () => es.close();
  }, [sid, qc]);

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">WhatsApp</h2>

      <Card>
        <CardHeader><CardTitle>Current pairing</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div>Paired: <strong>{status?.paired ? "yes" : "no"}</strong></div>
          {status?.me_id && <div>Account: <span className="font-mono text-xs">{status.me_id}</span></div>}
          {status?.self_chat_jid && <div>Self-chat JID: <span className="font-mono text-xs">{status.self_chat_jid}</span></div>}
          {status?.bridge_status && <div>Bridge: {status.bridge_status} {status.bridge_uptime_seconds && <span className="text-zinc-500">({status.bridge_uptime_seconds.toFixed(0)}s)</span>}</div>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Re-pair device</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {!sid && !done && (
            <>
              <p className="text-sm text-zinc-700">Stops the gateway, wipes the WhatsApp session, and shows a QR. Scan from the owner's phone within 3 minutes.</p>
              <Button onClick={() => startPair.mutate()} loading={startPair.isPending}>Start re-pair</Button>
            </>
          )}
          {sid && !done && (
            <>
              {qrLines.length >= 25 ? (
                <div className="flex flex-col items-center gap-2">
                  <pre
                    aria-label="WhatsApp pairing QR code"
                    className="font-mono whitespace-pre bg-white p-2 border border-zinc-200 rounded select-none"
                    style={{ fontSize: "10px", lineHeight: "10px" }}
                  >
                    {qrLines.join("\n")}
                  </pre>
                  <p className="text-xs text-zinc-500 text-center">
                    WhatsApp → Settings → Linked Devices → Link a Device → scan.
                    <br />Code regenerates ~every 20s; if it expires, click Cancel and Start re-pair again.
                  </p>
                </div>
              ) : (
                <p className="text-sm text-zinc-500">Waiting for QR ({qrLines.length}/33 rows captured)…</p>
              )}
              {streamError && <div className="text-xs text-red-700">{streamError}</div>}
              <details className="text-xs">
                <summary className="cursor-pointer text-zinc-500">Bridge log ({logLines.length} lines)</summary>
                <pre className="bg-zinc-900 text-zinc-100 p-2 rounded mt-1 overflow-x-auto max-h-40 font-mono text-[10px]">
                  {logLines.join("\n")}
                </pre>
              </details>
              <Button variant="outline" size="sm" onClick={() => cancelPair.mutate()}>Cancel</Button>
            </>
          )}
          {done && (
            <div className="rounded-md border border-green-300 bg-green-50 p-3 text-sm">
              ✓ Pairing complete. Device linked and self_chat_jid updated.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-red-700">Unlink current device</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm">Removes the current WhatsApp pairing. Agent stops receiving messages until you re-pair.</p>
          <Button variant="destructive" onClick={() => { if (confirm("Unlink the WhatsApp device? You'll need to re-pair to resume.")) unlink.mutate(); }}>
            Unlink device
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
