import { type Section, useSection } from "@/hooks/useSection";
import { cn } from "@/lib/cn";
import { Activity, AlertTriangle, Calendar, ClipboardList, Cog, FileText, MessageSquare, Phone, ScrollText, ShieldCheck, Users } from "lucide-react";

const NAV: { id: Section; label: string; icon: React.ComponentType<{ size?: number; className?: string }> }[] = [
  { id: "dashboard", label: "Dashboard", icon: Activity },
  { id: "roster", label: "Roster", icon: Users },
  { id: "schedule", label: "Schedule", icon: Calendar },
  { id: "pending", label: "Pending", icon: ClipboardList },
  { id: "decisions", label: "Decisions", icon: ScrollText },
  { id: "whatsapp", label: "WhatsApp", icon: Phone },
  { id: "config", label: "Config", icon: Cog },
  { id: "safety", label: "Safety", icon: AlertTriangle },
  { id: "disclosures", label: "Disclosures", icon: ShieldCheck },
  { id: "audit", label: "Audit", icon: FileText },
];

export function Sidebar() {
  const [section, setSection] = useSection();
  return (
    <nav className="w-56 shrink-0 border-r border-zinc-200 bg-white px-2 py-4 space-y-1 hidden md:block">
      {NAV.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          onClick={() => setSection(id)}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-left transition-colors",
            section === id ? "bg-brand-50 text-brand-700 font-medium" : "text-zinc-700 hover:bg-zinc-100",
          )}
        >
          <Icon size={16} />
          {label}
        </button>
      ))}
    </nav>
  );
}

export function MobileTabs() {
  const [section, setSection] = useSection();
  return (
    <nav className="md:hidden flex overflow-x-auto border-b border-zinc-200 bg-white">
      {NAV.map(({ id, label }) => (
        <button
          key={id}
          onClick={() => setSection(id)}
          className={cn(
            "px-3 py-2 text-xs whitespace-nowrap border-b-2",
            section === id ? "border-brand-600 text-brand-700" : "border-transparent text-zinc-600",
          )}
        >
          {label}
        </button>
      ))}
    </nav>
  );
}

export function TopBar({ owner, onLogout }: { owner: string; onLogout: () => void }) {
  return (
    <header className="h-14 flex items-center justify-between border-b border-zinc-200 bg-white px-4">
      <div className="flex items-center gap-3 font-semibold">
        <MessageSquare size={20} className="text-brand-600" />
        <span>Shift Agent — Cockpit</span>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <span className="text-zinc-600">{owner}</span>
        <button onClick={onLogout} className="text-zinc-500 hover:text-zinc-900 text-xs">Logout</button>
      </div>
    </header>
  );
}
