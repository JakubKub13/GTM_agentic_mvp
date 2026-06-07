/**
 * Typed fetch client + types matching the backend run/account shapes.
 *
 * Types mirror the SQLite store rows surfaced by the FastAPI read endpoints
 * (duvo/store/queries.py: list_runs / get_run / get_account_detail) and the
 * Pydantic contract (duvo/models.py: ICPScore / Signal / OutreachDraft).
 *
 * Plan #16 serves the SPA from the same FastAPI process, so the API is same-origin
 * (empty base). The Vite dev proxy forwards /runs, /auth, /me to localhost:8000.
 */

export type RunStatus =
  | "running"
  | "done"
  | "failed"
  | "interrupted"
  | "cancelled";

export type AccountStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "interrupted"
  | "cancelled";

export type Tier = "Tier 1" | "Tier 2" | "Tier 3";
export type Confidence = "high" | "medium" | "low";

/** A `runs` row (duvo/store schema + plan #15 migrations: triggered_by, companies_json). */
export interface Run {
  run_id: string;
  run_date: string | null;
  started_at: string | null;
  finished_at: string | null;
  dry_run: number;
  concurrency: number | null;
  model: string | null;
  app_env: string | null;
  accounts_total: number | null;
  accounts_succeeded: number | null;
  accounts_failed: number | null;
  status: RunStatus | string | null;
  triggered_by?: string | null;
  companies_json?: string | null;
}

/** An `account_runs` row (plan #15 adds agent_log_json). */
export interface AccountRun {
  run_id: string;
  domain: string;
  company_name: string | null;
  country: string | null;
  status: AccountStatus | string;
  score: number | null;
  tier: Tier | string | null;
  confidence: Confidence | string | null;
  needs_human_research: number | null;
  signals_count: number | null;
  signals_json: string | null;
  score_json: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  agent_log_json?: string | null;
}

/** GET /runs/{id} -> run + account snapshot (queries.get_run). */
export interface RunDetail {
  run: Run;
  accounts: AccountRun[];
}

/** Score/tier diff against the most recent real prior run (queries.get_account_detail). */
export interface AccountDiff {
  changed: boolean;
  prev_score: number | null;
  prev_tier: string | null;
}

/** GET /runs/{id}/accounts/{domain} (queries.get_account_detail). */
export interface AccountDetail {
  account: AccountRun;
  diff: AccountDiff;
}

/** A signal (duvo/models.Signal), parsed from account_runs.signals_json. */
export interface Signal {
  signal_type: "erp_migration" | "hiring" | "ma_leadership" | "pain";
  title: string;
  summary: string;
  source_url: string;
  published_date: string | null;
  relevance: string;
}

/** Outreach draft (duvo/models.OutreachDraft). */
export interface OutreachDraft {
  persona: string;
  subject: string;
  first_line: string;
  body: string;
}

/** The full ICP score (duvo/models.ICPScore), parsed from account_runs.score_json. */
export interface ICPScore {
  company_name: string;
  domain: string;
  score: number;
  tier: Tier;
  confidence: Confidence;
  why_fit: string[];
  why_not: string[];
  recommended_persona: string;
  recommended_angle: string;
  reasoning: string;
  needs_human_research: boolean;
  outreach: OutreachDraft;
}

/** Authenticated user (GET /me). */
export interface Me {
  email: string;
  name?: string | null;
  can_real_run?: boolean;
}

/** A company in a run's input (duvo/models.Company). */
export interface Company {
  name: string;
  domain: string;
  country?: string;
  description?: string;
}

/** Request body for POST /runs. */
export interface StartRunRequest {
  dry_run: boolean;
  /** Explicit confirmation required by the backend to launch a real (non-dry) run (#14). */
  confirm?: boolean;
  concurrency?: number | null;
  skip_done_today?: boolean;
  test_email?: string | null;
  /** Parsed companies; `null`/empty → the server uses its repo-default companies.csv (#19). */
  companies?: Company[] | null;
}

/** POST /runs response (plan #8). */
export interface StartRunResponse {
  run_id: string;
}

/** SSE event payloads (plan #9, #11) streamed over GET /runs/{id}/stream. */
export interface ToolEvent {
  type: "tool";
  run_id: string;
  account: string | null;
  agent: string | null;
  beat: string | null;
  tool: string;
  arg_summary: string;
  ts: string;
}

export interface AccountEvent {
  type: "account";
  run_id: string;
  account: string;
  status: AccountStatus | string;
  score?: number | null;
  tier?: string | null;
  confidence?: string | null;
  signals_count?: number | null;
  ts: string;
}

export interface StatusEvent {
  type: "status";
  run_id: string;
  status: RunStatus | string;
  ts: string;
}

export type RunStreamEvent = ToolEvent | AccountEvent | StatusEvent;

/** Thrown for non-2xx responses; carries the HTTP status for callers to branch on. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Same-origin base; override for a split deploy via VITE_API_BASE. */
const API_BASE: string = import.meta.env?.VITE_API_BASE ?? "";

const CSRF_COOKIE = "duvo_csrf";
const CSRF_HEADER = "x-csrf-token";

/** Read a cookie value by name (browser/jsdom), or null. */
function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  // Double-submit CSRF: the backend requires the X-CSRF-Token header on state-changing
  // requests to equal the (readable) duvo_csrf cookie set at login (#13).
  const csrf = method !== "GET" ? readCookie(CSRF_COOKIE) : null;
  // FormData sets its own multipart Content-Type (with boundary); only JSON string bodies
  // get an explicit application/json header.
  const isStringBody = typeof init?.body === "string";
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(isStringBody ? { "Content-Type": "application/json" } : {}),
      ...(csrf ? { [CSRF_HEADER]: csrf } : {}),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Escape one CSV cell — quote it when it contains a comma, quote, or newline. */
function csvCell(value: string | undefined | null): string {
  const s = value ?? "";
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Serialize parsed companies back into the CSV the backend's multipart upload expects. */
export function companiesToCsv(companies: Company[]): string {
  const header = "name,domain,country,description";
  const rows = companies.map((c) =>
    [c.name, c.domain, c.country, c.description].map(csvCell).join(","),
  );
  return [header, ...rows].join("\n") + "\n";
}

/** Build the multipart body for POST /runs (#8). */
function startRunForm(body: StartRunRequest): FormData {
  const form = new FormData();
  // Only attach a file when companies were parsed from an upload; omitting it tells the
  // server to use its repo-default companies.csv (#19).
  if (body.companies && body.companies.length > 0) {
    const csv = companiesToCsv(body.companies);
    form.append("file", new Blob([csv], { type: "text/csv" }), "companies.csv");
  }
  form.append("dry_run", String(body.dry_run));
  form.append("confirm", String(body.confirm ?? false));
  if (body.concurrency != null) form.append("concurrency", String(body.concurrency));
  form.append("skip_done_today", String(body.skip_done_today ?? false));
  if (body.test_email) form.append("test_email", body.test_email);
  return form;
}

export const api = {
  me: () => request<Me>("/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),

  // Backend wraps the history list as { runs: [...] } (queries.list_runs).
  listRuns: () => request<{ runs: Run[] }>("/runs").then((r) => r.runs),
  getRun: (runId: string) => request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  getAccount: (runId: string, domain: string) =>
    request<AccountDetail>(
      `/runs/${encodeURIComponent(runId)}/accounts/${encodeURIComponent(domain)}`,
    ),

  // POST /runs is multipart/form-data (UploadFile + form fields) with the CSRF header (#8/#13).
  startRun: (body: StartRunRequest) =>
    request<StartRunResponse>("/runs", {
      method: "POST",
      body: startRunForm(body),
    }),
};

/** URL for the run's SSE stream (plan #11). */
export function runStreamUrl(runId: string): string {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/stream`;
}

/**
 * Open the live run stream via the native EventSource (plan stack line).
 *
 * Parses each `message` as a {@link RunStreamEvent}; malformed frames are ignored.
 * Returns a disposer that closes the connection. Callers own reconnect/poll fallback
 * (plan #20: useRunStream).
 */
export function openRunStream(
  runId: string,
  handlers: {
    onEvent: (event: RunStreamEvent) => void;
    onError?: (err: Event) => void;
    onOpen?: () => void;
  },
): () => void {
  const source = new EventSource(runStreamUrl(runId), { withCredentials: true });

  source.onmessage = (msg: MessageEvent<string>) => {
    try {
      handlers.onEvent(JSON.parse(msg.data) as RunStreamEvent);
    } catch {
      // ignore malformed frame (e.g. heartbeat comment slipped through)
    }
  };
  if (handlers.onOpen) source.onopen = () => handlers.onOpen?.();
  source.onerror = (err) => handlers.onError?.(err);

  return () => source.close();
}
