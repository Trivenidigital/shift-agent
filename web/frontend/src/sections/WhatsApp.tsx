import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
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
  const [qr, setQr] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const startPair = useMutation({
    mutationFn: () => api.POST<PairSessionResp>("/whatsapp/repair"),
    onSuccess: (r) => { setSid(r.session_id); setLogLines([]); setQr(null); setDone(false); },
  });
  const cancelPair = useMutation({
    mutationFn: () => sid ? api.POST(`/whatsapp/repair/${sid}/cancel`) : Promise.resolve(),
    onSuccess: () => { setSid(null); setQr(null); esRef.current?.close(); },
  });
  const unlink = useMutation({
    mutationFn: () => api.POST("/whatsapp/unlink"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wa-status"] }),
  });

  // SSE wiring
  useEffect(() => {
    if (!sid) return;
    const es = new EventSource(`/api/whatsapp/repair/${sid}/stream`, { withCredentials: true });
    esRef.current = es;
    es.addEventListener("log", (ev: MessageEvent) => {
      const line = ev.data;
      setLogLines((l) => [...l, line]);
      // Try to extract a QR data string heuristically
      if (line.length > 50 && /^[0-9A-Za-z+/=,]+$/.test(line)) setQr(line);
    });
    es.addEventListener("connected", () => setLogLines((l) => [...l, "✓ WhatsApp connected"]));
    es.addEventListener("complete", (ev: MessageEvent) => {
      setDone(true);
      setQr(null);
      try { setLogLines((l) => [...l, `✓ Pairing complete: ${ev.data}`]); } catch {}
      es.close();
      qc.invalidateQueries({ queryKey: ["wa-status"] });
      qc.invalidateQueries({ queryKey: ["config"] });
    });
    es.addEventListener("error", () => setLogLines((l) => [...l, "✗ stream error"]));
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
              {qr ? (
                <div className="flex flex-col items-center gap-2">
                  <QRCodeSVG value={qr} size={300} level="L" />
                  <p className="text-xs text-zinc-500">WhatsApp → Settings → Linked Devices → Link a Device → scan</p>
                </div>
              ) : (
                <p className="text-sm text-zinc-500">Waiting for QR…</p>
              )}
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
