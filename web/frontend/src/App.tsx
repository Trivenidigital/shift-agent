import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LoginScreen } from "@/components/auth/LoginScreen";
import { Sidebar, MobileTabs, TopBar } from "@/components/layout/Layout";
import { StickyFooter } from "@/components/layout/StickyFooter";
import { useSection } from "@/hooks/useSection";
import { Dashboard } from "@/sections/Dashboard";
import { Roster } from "@/sections/Roster";
import { Schedule } from "@/sections/Schedule";
import { Pending } from "@/sections/Pending";
import { Decisions } from "@/sections/Decisions";
import { Config } from "@/sections/Config";
import { Safety } from "@/sections/Safety";
import { WhatsApp } from "@/sections/WhatsApp";
import { Disclosures } from "@/sections/Disclosures";
import { AuditView } from "@/sections/AuditView";

interface Me { owner_phone: string; owner_name: string; issued_at: number; expires_at: number }

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [section] = useSection();

  useEffect(() => {
    api.GET<Me>("/auth/me")
      .then((m) => { setMe(m); setAuthed(true); })
      .catch(() => setAuthed(false));
  }, []);

  // Auto-logout 60s before token expires (prevents silent fetch failures)
  useEffect(() => {
    if (!me) return;
    const msUntilExpiry = me.expires_at * 1000 - Date.now() - 60_000;
    if (msUntilExpiry <= 0) { setAuthed(false); return; }
    const t = window.setTimeout(() => setAuthed(false), msUntilExpiry);
    return () => window.clearTimeout(t);
  }, [me]);

  if (authed === null) return <div className="min-h-screen flex items-center justify-center text-zinc-500">Loading…</div>;
  if (!authed) return <LoginScreen onAuthed={() => window.location.reload()} />;

  return (
    <div className="h-screen flex flex-col">
      <TopBar
        owner={me ? `${me.owner_name} (${me.owner_phone})` : ""}
        onLogout={async () => { await api.POST("/auth/logout"); window.location.reload(); }}
      />
      <MobileTabs />
      <div className="flex-1 flex overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-6">
          {section === "dashboard" && <Dashboard />}
          {section === "roster" && <Roster />}
          {section === "schedule" && <Schedule />}
          {section === "pending" && <Pending />}
          {section === "decisions" && <Decisions />}
          {section === "config" && <Config />}
          {section === "safety" && <Safety />}
          {section === "whatsapp" && <WhatsApp />}
          {section === "disclosures" && <Disclosures />}
          {section === "audit" && <AuditView />}
        </main>
      </div>
      <StickyFooter />
    </div>
  );
}
