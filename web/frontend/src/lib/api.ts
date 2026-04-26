// Lightweight typed-ish API client.
// Long-term: switch to openapi-fetch with generated types via `npm run generate:types`.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

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
    (err as any).status = res.status;
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
