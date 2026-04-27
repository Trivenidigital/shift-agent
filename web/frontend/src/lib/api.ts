// Backwards-compat thin wrapper around the openapi-fetch typed client.
// Existing callers use `api.GET<Type>(...)` / `api.POST<Type>(...)` syntax;
// this wrapper keeps that ergonomics while the underlying call goes through
// the generated `paths` so future migrations can use full openapi-fetch types.
//
// To get FULL type safety on a route, import the typed client directly:
//   import { typedApi } from "@/lib/api";
//   const { data, error } = await typedApi.GET("/roster", {});
//
// New code should prefer typedApi. The generic-T `api` is kept for the
// existing call sites until they're migrated.

import createClient from "openapi-fetch";
import type { paths } from "@/api/schema";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export const typedApi = createClient<paths>({
  baseUrl: BASE,
  credentials: "include",
});

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {}
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    (err as ApiError).status = res.status;
    throw err;
  }
  if (res.headers.get("content-type")?.includes("application/json")) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}

export const api = {
  GET: <T,>(path: string) => req<T>("GET", path),
  POST: <T,>(path: string, body?: unknown) => req<T>("POST", path, body),
  PATCH: <T,>(path: string, body?: unknown) => req<T>("PATCH", path, body),
  PUT: <T,>(path: string, body?: unknown) => req<T>("PUT", path, body),
  DELETE: <T,>(path: string) => req<T>("DELETE", path),
};

export type ApiError = Error & { status?: number };
