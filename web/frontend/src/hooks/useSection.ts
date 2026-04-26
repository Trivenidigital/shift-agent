import { useEffect, useState, useCallback } from "react";

export type Section =
  | "dashboard"
  | "roster"
  | "schedule"
  | "pending"
  | "decisions"
  | "config"
  | "whatsapp"
  | "safety"
  | "disclosures"
  | "audit";

const VALID: Section[] = [
  "dashboard",
  "roster",
  "schedule",
  "pending",
  "decisions",
  "config",
  "whatsapp",
  "safety",
  "disclosures",
  "audit",
];

function readFromUrl(): Section {
  const s = new URLSearchParams(window.location.search).get("s");
  return (VALID.includes(s as Section) ? (s as Section) : "dashboard");
}

export function useSection(): [Section, (s: Section) => void] {
  const [section, setSection] = useState<Section>(readFromUrl);

  useEffect(() => {
    const onPop = () => setSection(readFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((s: Section) => {
    const url = new URL(window.location.href);
    url.searchParams.set("s", s);
    window.history.pushState({}, "", url);
    setSection(s);
  }, []);

  return [section, navigate];
}
