import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";

interface OtpRequestResponse { token: string; expires_in_seconds: number }
interface AuthStatus { totp_enrolled: boolean; pushover_configured: boolean; auth_bypass_enabled?: boolean }

type Tab = "pushover" | "totp";

export function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [tab, setTab] = useState<Tab>("pushover");
  const [step, setStep] = useState<"request" | "verify">("request");
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestOtp = async () => {
    setLoading(true); setError(null);
    try {
      const r = await api.POST<OtpRequestResponse>("/auth/request-otp");
      if (r.token === "__bypass__") {
        onAuthed();
        return;
      }
      setToken(r.token);
      setStep("verify");
    } catch (e) {
      setError((e as Error).message);
    } finally { setLoading(false); }
  };

  useEffect(() => {
    api.GET<AuthStatus>("/auth/status")
      .then((s) => {
        setAuthStatus(s);
        if (s.auth_bypass_enabled) {
          // Test-only fast path: backend will mint a bypass JWT on request-otp.
          requestOtp();
          return;
        }
        // Default to Pushover if configured, else TOTP. If neither, surface error.
        if (s.pushover_configured) setTab("pushover");
        else if (s.totp_enrolled) setTab("totp");
        else setError("No login methods configured. Server admin must enable Pushover OTP or TOTP.");
      })
      .catch(() => setError("Cannot reach server"));
  }, []);

  const verifyOtp = async () => {
    setLoading(true); setError(null);
    try {
      await api.POST("/auth/verify-otp", { token, code });
      onAuthed();
    } catch (e) {
      setError((e as Error).message);
    } finally { setLoading(false); }
  };

  const verifyTotp = async () => {
    setLoading(true); setError(null);
    try {
      await api.POST("/auth/verify-totp", { code });
      onAuthed();
    } catch (e) {
      setError((e as Error).message);
    } finally { setLoading(false); }
  };

  if (authStatus === null) {
    return <div className="min-h-screen flex items-center justify-center text-zinc-500">{error ?? "Loading…"}</div>;
  }

  const showTabs = authStatus.totp_enrolled && authStatus.pushover_configured;

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>⚕ Shift Agent — Cockpit</CardTitle>
          <p className="text-xs text-zinc-500 mt-1">Owner sign-in</p>
        </CardHeader>
        <CardContent className="space-y-4">
          {showTabs && (
            <div className="flex border-b border-zinc-200 -mx-5 -mt-3 mb-2">
              <button
                onClick={() => { setTab("pushover"); setToken(""); setCode(""); setStep("request"); setError(null); }}
                className={cn(
                  "flex-1 py-2 text-sm border-b-2",
                  tab === "pushover" ? "border-brand-600 text-brand-700 font-medium" : "border-transparent text-zinc-500",
                )}
              >
                Pushover code
              </button>
              <button
                onClick={() => { setTab("totp"); setToken(""); setCode(""); setError(null); }}
                className={cn(
                  "flex-1 py-2 text-sm border-b-2",
                  tab === "totp" ? "border-brand-600 text-brand-700 font-medium" : "border-transparent text-zinc-500",
                )}
              >
                Authenticator (TOTP)
              </button>
            </div>
          )}

          {tab === "pushover" && authStatus.pushover_configured && (
            step === "request" ? (
              <>
                <p className="text-sm text-zinc-700">
                  Tap below — you'll receive a 6-digit login code via Pushover on your registered phone.
                </p>
                <Button onClick={requestOtp} loading={loading} className="w-full" size="lg">
                  Send login code
                </Button>
              </>
            ) : (
              <>
                <label className="text-sm text-zinc-700 block">Enter the 6-digit code from your Pushover notification.</label>
                <Input
                  aria-label="6-digit Pushover login code"
                  inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="123456"
                  value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus
                />
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep("request")} className="flex-1">Resend</Button>
                  <Button onClick={verifyOtp} loading={loading} className="flex-1" disabled={code.length !== 6}>
                    Verify
                  </Button>
                </div>
              </>
            )
          )}

          {tab === "totp" && authStatus.totp_enrolled && (
            <>
              <label className="text-sm text-zinc-700 block">Enter the 6-digit code from your authenticator app.</label>
              <Input
                aria-label="6-digit TOTP code"
                inputMode="numeric" pattern="[0-9]{6,8}" maxLength={8} placeholder="123456"
                value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus
              />
              <Button onClick={verifyTotp} loading={loading} className="w-full" disabled={code.length < 6}>
                Verify
              </Button>
            </>
          )}

          {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
