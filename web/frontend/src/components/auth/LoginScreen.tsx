import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";

interface OtpRequestResponse { token: string; expires_in_seconds: number }

export function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const [step, setStep] = useState<"request" | "verify">("request");
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestOtp = async () => {
    setLoading(true); setError(null);
    try {
      const r = await api.POST<OtpRequestResponse>("/auth/request-otp");
      setToken(r.token);
      setStep("verify");
    } catch (e) {
      setError((e as Error).message);
    } finally { setLoading(false); }
  };

  const verifyOtp = async () => {
    setLoading(true); setError(null);
    try {
      await api.POST("/auth/verify-otp", { token, code });
      onAuthed();
    } catch (e) {
      setError((e as Error).message);
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>⚕ Shift Agent — Cockpit</CardTitle>
          <p className="text-xs text-zinc-500 mt-1">Owner sign-in</p>
        </CardHeader>
        <CardContent className="space-y-4">
          {step === "request" ? (
            <>
              <p className="text-sm text-zinc-700">
                Tap the button below — you'll receive a 6-digit login code via Pushover on your registered phone.
              </p>
              <Button onClick={requestOtp} loading={loading} className="w-full" size="lg">
                Send login code
              </Button>
            </>
          ) : (
            <>
              <p className="text-sm text-zinc-700">Enter the 6-digit code from your Pushover notification.</p>
              <Input
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                placeholder="123456"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                autoFocus
              />
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setStep("request")} className="flex-1">Resend</Button>
                <Button onClick={verifyOtp} loading={loading} className="flex-1" disabled={code.length !== 6}>
                  Verify
                </Button>
              </div>
            </>
          )}
          {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
