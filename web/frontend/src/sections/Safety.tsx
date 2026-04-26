import { useReducer } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

type State =
  | { phase: "idle" }
  | { phase: "confirm"; direction: "disable" | "enable"; reason: string }
  | { phase: "submitting"; direction: "disable" | "enable"; reason: string }
  | { phase: "error"; message: string };
type Action =
  | { type: "OPEN"; direction: "disable" | "enable" }
  | { type: "REASON"; reason: string }
  | { type: "SUBMIT" }
  | { type: "DONE" }
  | { type: "ERROR"; message: string }
  | { type: "RESET" };

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "OPEN": return { phase: "confirm", direction: a.direction, reason: "" };
    case "REASON": return s.phase === "confirm" ? { ...s, reason: a.reason } : s;
    case "SUBMIT": return s.phase === "confirm" ? { phase: "submitting", direction: s.direction, reason: s.reason } : s;
    case "DONE": return { phase: "idle" };
    case "ERROR": return { phase: "error", message: a.message };
    case "RESET": return { phase: "idle" };
  }
}

interface Dashboard {
  components: { name: string; ok: boolean; detail: string }[];
  disabled: boolean;
}

export function Safety() {
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(reducer, { phase: "idle" });
  const { data } = useQuery<Dashboard>({ queryKey: ["dashboard"], queryFn: () => api.GET<Dashboard>("/dashboard") });

  const toggle = useMutation({
    mutationFn: ({ direction, reason }: { direction: "disable" | "enable"; reason: string }) =>
      api.POST(`/safety/${direction}`, { reason }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dashboard"] }); dispatch({ type: "DONE" }); },
    onError: (e) => dispatch({ type: "ERROR", message: (e as Error).message }),
  });
  const testAlert = useMutation({ mutationFn: () => api.POST("/safety/test-alert") });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Safety</h2>

      <Card>
        <CardHeader><CardTitle>Kill switch</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="text-sm text-zinc-700">
            Currently: {data?.disabled ? <strong className="text-red-600">DISABLED</strong> : <strong className="text-green-700">ACTIVE</strong>}.
          </div>

          {state.phase === "idle" && (
            <div className="flex gap-2">
              {data?.disabled ? (
                <Button variant="default" onClick={() => dispatch({ type: "OPEN", direction: "enable" })}>
                  Re-enable agent
                </Button>
              ) : (
                <Button variant="destructive" onClick={() => dispatch({ type: "OPEN", direction: "disable" })}>
                  ⛔ Disable agent NOW
                </Button>
              )}
              <Button variant="outline" onClick={() => testAlert.mutate()} loading={testAlert.isPending}>Send test alert</Button>
            </div>
          )}

          {(state.phase === "confirm" || state.phase === "submitting") && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-4 space-y-3">
              <p className="text-sm">
                Confirm <strong>{state.direction === "disable" ? "DISABLE" : "RE-ENABLE"}</strong>. Reason (≥5 chars, written to audit log):
              </p>
              <Input
                value={state.reason}
                onChange={(e) => dispatch({ type: "REASON", reason: e.target.value })}
                placeholder={state.direction === "disable" ? "e.g., suspicious traffic" : "e.g., issue resolved"}
                disabled={state.phase === "submitting"}
              />
              <div className="flex gap-2">
                <Button
                  variant={state.direction === "disable" ? "destructive" : "default"}
                  onClick={() => { dispatch({ type: "SUBMIT" }); toggle.mutate({ direction: state.direction, reason: state.reason }); }}
                  disabled={state.reason.length < 5}
                  loading={state.phase === "submitting"}
                >
                  Confirm {state.direction === "disable" ? "disable" : "enable"}
                </Button>
                <Button variant="ghost" onClick={() => dispatch({ type: "RESET" })}>Back</Button>
              </div>
            </div>
          )}

          {state.phase === "error" && (
            <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 space-y-2">
              <div>Error: {state.message}</div>
              <Button size="sm" variant="outline" onClick={() => dispatch({ type: "RESET" })}>Try again</Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
