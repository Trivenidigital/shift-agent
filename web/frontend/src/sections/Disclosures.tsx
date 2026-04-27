import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

interface DisclosureItem { id: string; text: string; signed: boolean; signed_at: string | null; signed_by_name: string | null }

export function Disclosures() {
  const qc = useQueryClient();
  const { data } = useQuery<{ disclosures: DisclosureItem[] }>({ queryKey: ["disclosures"], queryFn: () => api.GET("/disclosures") });
  const [name, setName] = useState("");

  const sign = useMutation({
    mutationFn: (id: string) => api.POST("/disclosures/sign", { disclosure_id: id, signed_by_name: name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["disclosures"] }),
    onError: (e) => alert("Sign failed (need fresh OTP — log out and back in): " + (e as Error).message),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">Disclosures</h2>
      <Card>
        <CardHeader><CardTitle>Sign-as</CardTitle></CardHeader>
        <CardContent>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your full name" />
        </CardContent>
      </Card>
      {data?.disclosures.map((d) => (
        <Card key={d.id}>
          <CardContent className="space-y-2">
            <p className="text-sm">{d.text}</p>
            {d.signed ? (
              <p className="text-xs text-green-700">✓ Signed by {d.signed_by_name} at {d.signed_at}</p>
            ) : (
              <Button size="sm" disabled={!name} onClick={() => sign.mutate(d.id)} loading={sign.isPending}>I acknowledge</Button>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
