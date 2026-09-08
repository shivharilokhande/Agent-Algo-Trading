// AgentAlgo API client
const BASE = "";

export function getToken(): string | null {
  return localStorage.getItem("agentalgo_token");
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem("agentalgo_token", t);
  else localStorage.removeItem("agentalgo_token");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(BASE + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401 && !path.startsWith("/api/auth")) {
    setToken(null);
    window.location.href = "/login";
  }
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* ignore */ }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, b?: unknown) => req<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => req<T>("PUT", p, b),
  del: <T>(p: string) => req<T>("DELETE", p),
};

// R5-9: one shared timestamp formatter — backend datetimes are UTC but often
// serialized WITHOUT a zone suffix; normalize, then always render in IST.
// R6-F3: any trailing zone designator (Z, +05:30, -05:00) counts as zoned —
// the old `includes("+")` missed negative offsets and produced invalid dates
const HAS_ZONE = /([zZ]|[+−-]\d{2}:?\d{2})$/;

export function fmtIst(ts: string | null | undefined,
                       opts: Intl.DateTimeFormatOptions =
                         { dateStyle: "medium", timeStyle: "short" }): string {
  if (!ts) return "—";
  const iso = HAS_ZONE.test(ts) ? ts : ts + "Z";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-IN", { ...opts, timeZone: "Asia/Kolkata" });
}

// R5-14: today's date in IST (an Indian-market app must not flip dates at UTC midnight)
export function istToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

// S3: mint a short-lived, run-scoped ticket so the JWT never rides a URL
export async function runStreamUrl(runId: string): Promise<string> {
  const { ticket } = await api.post<{ ticket: string }>(`/api/runs/${runId}/stream-ticket`);
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/runs/${runId}/stream?ticket=${ticket}`;
}

// ---- types ----
export interface RunOut {
  id: string; ticker: string; company_name: string; asset_type: string;
  trade_date: string; mode: string; status: string; rating: string | null;
  decision_summary: string; error: string; stats: Record<string, number>;
  benchmark: string; config: Record<string, unknown>;
  created_at: string; started_at: string | null; finished_at: string | null;
}
export interface RunEventMsg {
  seq: number; type: string; agent: string;
  payload: Record<string, any>; ts: string;
}
export interface KeyOut {
  provider: string; mask: string; status: string;
  tested_at: string | null; extra: Record<string, string>;
}
export interface MemoryOut {
  id: string; ticker: string; trade_date: string; rating: string; summary: string;
  status: string; raw_return: number | null; alpha: number | null;
  benchmark: string; reflection: string; resolved_at: string | null; created_at: string;
}
export interface ProviderInfo {
  id: string; name: string; kind: string; env: string;
  configured: boolean; status: string | null;
}
