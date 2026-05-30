import { useEffect, useState, useCallback } from "react";

export type Section =
  | "dashboard"
  | "roster"
  | "schedule"
  | "pending"
  | "decisions"
  | "config"
  | "flyer"
  | "commerce"
  | "whatsapp"
  | "safety"
  | "disclosures"
  | "audit";

// `as const satisfies` enforces that VALID has exactly one entry per Section
// variant at compile time. If a new Section is added but not added here,
// `tsc -b` errors at the satisfies site — preventing the silent "always falls
// back to dashboard" bug.
const VALID = [
  "dashboard",
  "roster",
  "schedule",
  "pending",
  "decisions",
  "config",
  "flyer",
  "commerce",
  "whatsapp",
  "safety",
  "disclosures",
  "audit",
] as const satisfies readonly Section[];

function readFromUrl(): Section {
  const s = new URLSearchParams(window.location.search).get("s");
  return (VALID.includes(s as Section) ? (s as Section) : "dashboard");
}

// Each useSection() call has its own useState. Sidebar updating URL didn't
// re-render App because pushState doesn't fire popstate. Custom event syncs
// every instance.
const SECTION_EVENT = "shift-agent:section-change";

export function useSection(): [Section, (s: Section) => void] {
  const [section, setSection] = useState<Section>(readFromUrl);

  useEffect(() => {
    const sync = () => setSection(readFromUrl());
    window.addEventListener("popstate", sync);
    window.addEventListener(SECTION_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(SECTION_EVENT, sync);
    };
  }, []);

  const navigate = useCallback((s: Section) => {
    const url = new URL(window.location.href);
    url.searchParams.set("s", s);
    window.history.pushState({}, "", url);
    window.dispatchEvent(new Event(SECTION_EVENT));
  }, []);

  return [section, navigate];
}
