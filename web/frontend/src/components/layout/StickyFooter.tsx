import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useSection } from "@/hooks/useSection";
import { cn } from "@/lib/cn";

interface Dashboard {
  components: { name: string; ok: boolean; detail: string }[];
  send_counter: { day: string; count: number; last_send_ts: string } | null;
  counter_resets_at: string | null;
  disabled: boolean;
  pending_active_count: number;
}

export function StickyFooter() {
  const [, setSection] = useSection();
  const { data } = useQuery<Dashboard>({
    queryKey: ["dashboard"],
    queryFn: () => api.GET<Dashboard>("/dashboard"),
    refetchInterval: 10_000,
  });

  const allOk = data?.components.every((c) => c.ok) ?? false;
  const counter = data?.send_counter;

  return (
    <footer className="h-12 shrink-0 border-t border-zinc-200 bg-white flex items-center justify-between px-4 text-xs">
      <div className="flex items-center gap-3">
        <span className={cn("size-2 rounded-full", allOk ? "bg-green-500" : "bg-red-500")} />
        <span className="text-zinc-700">{allOk ? "All systems healthy" : "Issues detected"}</span>
        <button onClick={() => setSection("safety")} className="ml-2 text-brand-700 hover:underline">
          Safety →
        </button>
      </div>

      <div className="flex items-center gap-4 text-zinc-600">
        {counter && (
          <span>
            Today: <strong className="text-zinc-900">{counter.count}</strong>/{6} sent
          </span>
        )}
        {data?.disabled ? (
          <span className="text-red-600 font-medium">⛔ Agent disabled</span>
        ) : (
          <span className="text-green-700">● Agent active</span>
        )}
      </div>
    </footer>
  );
}
