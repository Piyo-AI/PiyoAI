// Client for the Piyo core's local API.

export type ApiStyle = "openai" | "anthropic";

export interface Provider {
  id: string;
  name: string;
  api_style: ApiStyle;
  base_url: string;
  requires_key: boolean;
  local: boolean;
  preset: boolean;
  public_models: boolean;
  default_model: string | null;
  docs_url: string | null;
  has_key: boolean;
}

export interface ModelInfo {
  id: string;
  /** null when the provider doesn't report pricing (most don't). */
  free: boolean | null;
  context_length: number | null;
  max_output: number | null;
  tools: boolean | null;
}

export interface OutputLimit {
  /** What is sent as the reply length cap. */
  output_limit: number;
  /** True when the user set it; otherwise it is the automatic value. */
  custom: boolean;
  /** The model's own maximum, when the provider reports it. */
  reported_max: number | null;
}

/** What a run on this model is costed with, in US dollars per million tokens. */
export interface Price {
  input: number | null;
  output: number | null;
  source: "custom" | "reported" | "local" | "unknown";
}

/** Whether the model can read images, for skills that need it. */
export interface Vision {
  mode: "auto" | "yes" | "no";
  /** What skill checks use; null when neither you nor the provider said. */
  effective: boolean | null;
  reported: boolean | null;
}

export interface ContextLimit {
  /** The window Piyo fits the conversation into, in tokens. */
  context_limit: number;
  /** True when the user set it; otherwise it is the automatic value. */
  custom: boolean;
  /** The model's own window, when the provider reports it. */
  reported: number | null;
}

export interface ToolMode {
  /** The user's choice. */
  mode: "auto" | "native" | "prompt";
  /** What a run will use: the model's own tool calling, or tools written as text. */
  effective: "native" | "prompt";
  reason: string;
}

/** Per-run budget and the local-only switch. */
export interface BrowserStatus {
  /** The window is shown (not headless). */
  visible: boolean;
  running: boolean;
  /** The current page, empty when none. */
  url: string;
}

export interface BrowserRules {
  allow: string[];
  deny: string[];
  max_pages: number;
}

export interface BrowserInstall {
  state: "unknown" | "missing" | "installed" | "installing" | "failed";
  percent: number;
  message: string;
}

export interface RunSettings {
  max_steps: number;
  max_tokens: number;
  /** Seconds of wall clock, not counting time spent waiting for an approval. */
  timeout_s: number;
  local_only: boolean;
}

export interface RunInfo {
  id: string;
  conversation_id: string;
  started_at: string;
  provider: string;
  model: string;
  request: string;
  /** done | step_limit | token_limit | timeout | truncated | cancelled | error */
  outcome: string;
  error: string | null;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  /** True when the provider did not report usage and the counts are guesses. */
  tokens_estimated: boolean;
  /** US dollars; null when the model's price is unknown, 0 for local models. */
  cost_usd: number | null;
}

export interface RunStep {
  kind: "model" | "tool" | "approval" | "skill";
  at: string;
  duration_ms: number | null;
  data: Record<string, unknown>;
}

export interface AuditEntry {
  seq: number;
  at: string;
  run_id: string;
  conversation_id: string;
  tool: string;
  summary: string;
  arguments: Record<string, unknown>;
  /** SHA-256 of the exact arguments the decision was about. */
  args_digest: string;
  /** What the model said when it asked. A claim, not a fact. */
  why: string;
  decision: "allowed" | "declined";
  hash: string;
}

export interface AuditReport {
  /** Newest first. */
  entries: AuditEntry[];
  /** False when an entry was edited or removed. */
  verified: boolean;
  problem: string | null;
}

export interface RunDetail extends RunInfo {
  steps: RunStep[];
}

export interface NewProvider {
  id: string;
  name: string;
  api_style: ApiStyle;
  base_url: string;
  requires_key: boolean;
  local: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export type ChatEvent =
  | { type: "conversation"; id: string }
  | { type: "delta"; text: string }
  | { type: "tool_start"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_end"; id: string; name: string; output: string; is_error: boolean }
  | { type: "approval_request"; id: string; tool: string; arguments: Record<string, unknown>; summary?: string; why?: string }
  | { type: "done"; reason: "done" | "step_limit" | "token_limit" | "timeout" | "truncated" | "cancelled" }
  | { type: "error"; message: string };

export interface ConversationInfo {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls: { id: string; name: string; arguments: Record<string, unknown> }[];
  tool_call_id: string | null;
  is_error: boolean;
}

export interface ConversationDetail extends ConversationInfo {
  messages: StoredMessage[];
  active_skills: string[];
}

export interface GoogleCheck {
  ok: boolean;
  results: { service: string; ok: boolean; detail: string }[];
}

export interface GoogleAccount {
  email: string;
  /** The access this account has granted, beyond identity. */
  groups: string[];
}

export interface GoogleStatus {
  state: "disconnected" | "connecting" | "connected";
  has_client: boolean;
  accounts: GoogleAccount[];
  error: string | null;
  console_url: string;
  available_groups: string[];
}

export interface SkillInfo {
  name: string;
  description: string;
  version: string;
  source: "builtin" | "user";
  tools: string[];
  has_setup: boolean;
  enabled: boolean;
  author: string | null;
  risk: string | null;
  integrations: string[];
  /** Integration -> why it isn't ready (not connected, expired); empty when all are. */
  integration_issues: Record<string, string>;
  secrets: string[];
  /** What the skill needs from the model: vision, min_context. */
  model_needs: { vision?: boolean; min_context?: number };
  /** Why the model named in the request can't run this skill; empty when it can. */
  model_issues: string[];
  /** Installed by the user, so it can be removed. */
  removable: boolean;
  /** Where an installed skill came from, e.g. "zip 3fa9c1d20b7e". */
  install_source: string | null;
  /** From the signed catalog. Installs from a file never are. */
  verified: boolean;
}

export interface CatalogEntry {
  name: string;
  version: string;
  description: string;
  author: string | null;
  license: string | null;
  permissions: string[];
  integrations: string[];
  /** The version you have installed, if any. */
  installed_version: string | null;
  /** A built-in skill has this name. */
  builtin: boolean;
}

export interface Catalog {
  /** Pass back when installing, so the listing and the download are the same commit. */
  commit: string;
  skills: CatalogEntry[];
  skipped: number;
}

export interface SchedulerJob {
  id: string;
  title: string;
  prompt: string;
  rule: Record<string, unknown>;
  /** The schedule in words, e.g. "Every day at 08:00". */
  when: string;
  provider: string;
  model: string;
  enabled: boolean;
  next_run: string | null;
  last_run: string | null;
  last_status: "done" | "error" | "waiting" | "missed" | null;
  last_conversation_id: string | null;
}

export interface SchedulerPending {
  id: string;
  job_id: string;
  job_title: string;
  conversation_id: string | null;
  tool: string;
  arguments: Record<string, unknown>;
  summary: string;
  status: string;
  result: string | null;
}

export interface SchedulerEvent {
  id: string;
  kind: "finished" | "failed" | "approval" | "missed";
  title: string;
  body: string;
  job_id: string | null;
  conversation_id: string | null;
  created_at: string;
}

export interface MemoryItem {
  id: string;
  category: string;
  text: string;
  /** "user" (typed here) or "piyo" (remembered during a chat). */
  source: string;
  created_at: string;
  updated_at: string;
}

export interface MemorySettings {
  sensitive: boolean;
  /** Categories that can be stored right now. */
  categories: string[];
  sensitive_categories: string[];
}

/** What a skill zip contains and asks for, before anything is installed. */
export interface InstallPreview {
  token: string;
  name: string;
  version: string;
  description: string;
  author: string | null;
  source: string;
  /** Labels like "tool:gmail.send", "integration:google", "secret:X", "script:run.py", "runtime:python". */
  permissions: string[];
  files: string[];
  /** Set when this replaces an installed version. */
  installed_version: string | null;
  /** The permissions the user has to approve now (all of them for a fresh install). */
  added: string[];
  verified: boolean;
}

export interface FolderEntry {
  path: string;
  /** Creating folders, moving and writing new files here need no per-call approval. Delete and overwrite always ask. */
  auto_changes: boolean;
}

interface Connection {
  port: number;
  token: string;
}

type CoreStatus =
  | { state: "starting" }
  | { state: "ready"; port: number; token: string }
  | { state: "failed"; error: string };

export const inTauri = () => "__TAURI_INTERNALS__" in window;

// Inside the Tauri app the shell starts the core and hands us its random port and token.
// In a plain browser (`npm run dev`) the core is started by hand with a fixed port and token:
//   PIYO_PORT=8765 PIYO_TOKEN=dev-token uv run piyo-core
async function resolveConnection(): Promise<Connection> {
  if (inTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    const deadline = Date.now() + 120_000; // the first start may sync Python dependencies
    for (;;) {
      const status = await invoke<CoreStatus>("core_status");
      if (status.state === "ready") return { port: status.port, token: status.token };
      if (status.state === "failed") throw new Error(status.error);
      if (Date.now() > deadline) throw new Error("The Piyo core is taking too long to start.");
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  if (import.meta.env.DEV) {
    return {
      port: Number(import.meta.env.VITE_PIYO_PORT ?? 8765),
      token: String(import.meta.env.VITE_PIYO_TOKEN ?? "dev-token"),
    };
  }
  throw new Error("The Piyo core connection is not available.");
}

let connection: Promise<Connection> | null = null;
const getConnection = () => {
  connection ??= resolveConnection();
  // A failed attempt must not be cached, or Retry could never succeed.
  connection.catch(() => (connection = null));
  return connection;
};

/** Forget the connection (the core moved or died); the next request asks again. */
export function resetConnection() {
  connection = null;
}

/** Tauri only: the system folder dialog. Returns the chosen path, or null if cancelled. */
export async function pickFolder(title: string): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog");
  const chosen = await open({ directory: true, multiple: false, title });
  return typeof chosen === "string" ? chosen : null;
}

/** Opens a web address in the user's own browser. Only http(s) is passed on. */
export async function openExternal(url: string): Promise<void> {
  if (!/^https?:\/\//i.test(url)) return;
  if (inTauri()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/** The webview does not follow links that leave the app, so send them to the browser instead. */
export function openLinksExternally(): void {
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0) return;
    const link = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!link || !/^https?:\/\//i.test(link.href) || link.origin === window.location.origin) return;
    e.preventDefault();
    openExternal(link.href).catch((err) => {
      console.error("Could not open link", err);
      const why = err instanceof Error ? err.message : String(err);
      window.alert(`Piyo could not open your browser (${why}).\n\n${link.href}`);
    });
  }, true); // capture phase: dialogs stop clicks from bubbling, so a bubbling listener never sees them
}

/** Tauri only: tell the shell to start a fresh core, then forget the old connection. */
export async function restartCore() {
  const { invoke } = await import("@tauri-apps/api/core");
  resetConnection();
  await invoke("restart_core");
}

/** Tauri only: called when the shell reports the core exited on its own. Returns an unsubscribe. */
export async function onCoreExit(handler: (message: string) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("core-exited", (e) => handler(e.payload));
}

async function upload<T>(path: string, data: ArrayBuffer): Promise<T> {
  const { port, token } = await getConnection();
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/octet-stream" },
    body: data,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { port, token } = await getConnection();
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  providers: () => request<Provider[]>("GET", "/api/providers"),
  addProvider: (p: NewProvider) => request<Provider>("POST", "/api/providers", p),
  removeProvider: (id: string) => request<void>("DELETE", `/api/providers/${id}`),
  setKey: (id: string, key: string) => request<void>("PUT", `/api/providers/${id}/key`, { key }),
  deleteKey: (id: string) => request<void>("DELETE", `/api/providers/${id}/key`),
  skills: (provider?: string, model?: string) =>
    request<{ skills: SkillInfo[]; errors: Record<string, string> }>(
      "GET",
      provider && model ? `/api/skills?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}` : "/api/skills",
    ),
  googleTest: () => request<GoogleCheck>("POST", "/api/integrations/google/test"),
  skillSetup: (name: string) =>
    request<{ name: string; markdown: string }>("GET", `/api/skills/${encodeURIComponent(name)}/setup`),
  googleStatus: () => request<GoogleStatus>("GET", "/api/integrations/google"),
  setGoogleClient: (client_id: string, client_secret: string | null) =>
    request<void>("PUT", "/api/integrations/google/client", { client_id, client_secret }),
  /** `account` adds access to that account; leave it out to add a new one. */
  connectGoogle: (groups: string[], account?: string) =>
    request<{ url: string }>("POST", "/api/integrations/google/connect", { groups, account: account ?? null }),
  cancelGoogle: () => request<void>("POST", "/api/integrations/google/cancel"),
  /** One account, or every account when none is named. */
  disconnectGoogle: (opts: { account?: string; removeClient?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (opts.account) q.set("account", opts.account);
    if (opts.removeClient) q.set("remove_client", "true");
    return request<void>("DELETE", `/api/integrations/google${q.size ? `?${q}` : ""}`);
  },
  searchStatus: () => request<{ provider: string; has_key: boolean; docs_url: string }>("GET", "/api/search"),
  setSearchKey: (key: string) => request<void>("PUT", "/api/search/key", { key }),
  deleteSearchKey: () => request<void>("DELETE", "/api/search/key"),
  outputLimit: (provider: string, model: string) =>
    request<OutputLimit>("GET", `/api/output-limit?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  contextLimit: (provider: string, model: string) =>
    request<ContextLimit>("GET", `/api/context-limit?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  setContextLimit: (provider: string, model: string, tokens: number | null) =>
    request<ContextLimit>("PUT", "/api/context-limit", { provider, model, tokens }),
  setOutputLimit: (provider: string, model: string, tokens: number | null) =>
    request<OutputLimit>("PUT", "/api/output-limit", { provider, model, tokens }),
  toolMode: (provider: string, model: string) =>
    request<ToolMode>("GET", `/api/tool-mode?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  setToolMode: (provider: string, model: string, mode: ToolMode["mode"]) =>
    request<ToolMode>("PUT", "/api/tool-mode", { provider, model, mode }),
  runs: (limit: number, offset: number, conversationId?: string) =>
    request<RunInfo[]>(
      "GET",
      `/api/runs?limit=${limit}&offset=${offset}${conversationId ? `&conversation_id=${encodeURIComponent(conversationId)}` : ""}`,
    ),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${id}`),
  conversations: (limit: number, offset: number) =>
    request<ConversationInfo[]>("GET", `/api/conversations?limit=${limit}&offset=${offset}`),
  conversation: (id: string) => request<ConversationDetail>("GET", `/api/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    request<ConversationInfo>("PATCH", `/api/conversations/${id}`, { title }),
  deleteConversation: (id: string) => request<void>("DELETE", `/api/conversations/${id}`),
  folders: () => request<{ folders: FolderEntry[] }>("GET", "/api/folders"),
  setFolders: (folders: FolderEntry[]) => request<{ folders: FolderEntry[] }>("PUT", "/api/folders", { folders }),
  price: (provider: string, model: string) =>
    request<Price>("GET", `/api/price?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  setPrice: (provider: string, model: string, input: number | null, output: number | null) =>
    request<Price>("PUT", "/api/price", { provider, model, input, output }),
  vision: (provider: string, model: string) =>
    request<Vision>("GET", `/api/vision?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  setVision: (provider: string, model: string, mode: Vision["mode"]) =>
    request<Vision>("PUT", "/api/vision", { provider, model, mode }),
  schedulerJobs: () => request<SchedulerJob[]>("GET", "/api/scheduler/jobs"),
  createJob: (job: { title: string; prompt: string; rule: Record<string, unknown>; provider: string; model: string }) =>
    request<SchedulerJob>("POST", "/api/scheduler/jobs", job),
  editJob: (id: string, changes: { title?: string; prompt?: string; rule?: Record<string, unknown>; enabled?: boolean }) =>
    request<SchedulerJob>("PUT", `/api/scheduler/jobs/${id}`, changes),
  deleteJob: (id: string) => request<void>("DELETE", `/api/scheduler/jobs/${id}`),
  runJob: (id: string) => request<{ started: boolean }>("POST", `/api/scheduler/jobs/${id}/run`),
  schedulerPending: () => request<SchedulerPending[]>("GET", "/api/scheduler/pending"),
  decidePending: (id: string, decision: "approve" | "decline") =>
    request<SchedulerPending>("POST", `/api/scheduler/pending/${id}/${decision}`),
  schedulerEvents: (unread = false) => request<SchedulerEvent[]>("GET", `/api/scheduler/events?unread=${unread}`),
  markSchedulerEventsRead: (ids: string[] | null) => request<void>("POST", "/api/scheduler/events/read", { ids }),
  memory: (q = "") => request<MemoryItem[]>("GET", `/api/memory?q=${encodeURIComponent(q)}`),
  addMemory: (text: string, category: string) => request<MemoryItem>("POST", "/api/memory", { text, category }),
  editMemory: (id: string, text: string, category: string) =>
    request<MemoryItem>("PUT", `/api/memory/${id}`, { text, category }),
  deleteMemory: (id: string) => request<void>("DELETE", `/api/memory/${id}`),
  clearMemory: () => request<{ deleted: number }>("DELETE", "/api/memory"),
  exportMemory: () => request<MemoryItem[]>("GET", "/api/memory-export"),
  memorySettings: () => request<MemorySettings>("GET", "/api/memory-settings"),
  setMemorySensitive: (sensitive: boolean) => request<MemorySettings>("PUT", "/api/memory-settings", { sensitive }),
  catalog: () => request<Catalog>("GET", "/api/catalog"),
  stageCatalogSkill: (name: string, commit: string) =>
    request<InstallPreview>("POST", "/api/catalog/install", { name, commit }),
  previewSkillInstall: (zip: ArrayBuffer) => upload<InstallPreview>("/api/skills/install/preview", zip),
  installSkill: (token: string, approved: string[]) =>
    request<{ name: string; version: string }>("POST", "/api/skills/install", { token, approved }),
  cancelSkillInstall: (token: string) => request<void>("DELETE", `/api/skills/install/${token}`),
  uninstallSkill: (name: string) => request<void>("DELETE", `/api/skills/${encodeURIComponent(name)}`),
  setSkillEnabled: (name: string, enabled: boolean) => request<void>("PUT", `/api/skills/${encodeURIComponent(name)}`, { enabled }),
  audit: (limit: number, offset: number) =>
    request<AuditReport>("GET", `/api/audit?limit=${limit}&offset=${offset}`),
  browser: () => request<BrowserStatus>("GET", "/api/browser"),
  setBrowserVisible: (visible: boolean) => request<BrowserStatus>("PUT", "/api/browser", { visible }),
  browserRules: () => request<BrowserRules>("GET", "/api/browser/rules"),
  setBrowserRules: (changes: Partial<BrowserRules>) => request<BrowserRules>("PUT", "/api/browser/rules", changes),
  browserInstall: () => request<BrowserInstall>("GET", "/api/browser/install"),
  installBrowser: () => request<BrowserInstall>("POST", "/api/browser/install"),
  stopBrowser: () => request<BrowserStatus>("POST", "/api/browser/stop"),
  runSettings: () => request<RunSettings>("GET", "/api/run-settings"),
  setRunSettings: (changes: Partial<RunSettings>) => request<RunSettings>("PUT", "/api/run-settings", changes),
  models: (id: string) => request<ModelInfo[]>("GET", `/api/providers/${id}/models`),
};

/** One chat WebSocket; reconnects on demand. */
export class ChatSocket {
  private ws: WebSocket | null = null;

  constructor(private onEvent: (e: ChatEvent) => void) {}

  private async open(): Promise<WebSocket> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return this.ws;
    const { port, token } = await getConnection();
    const ws = new WebSocket(`ws://127.0.0.1:${port}/ws/chat?token=${encodeURIComponent(token)}`);
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("Could not reach the Piyo core."));
    });
    ws.onmessage = (m) => this.onEvent(JSON.parse(m.data) as ChatEvent);
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
    };
    this.ws = ws;
    return ws;
  }

  /** Send one new message. The core keeps the history; a null conversation starts a new one. */
  async send(provider: string, model: string, message: string, conversationId: string | null): Promise<void> {
    const ws = await this.open();
    ws.send(JSON.stringify({ type: "chat", provider, model, message, conversation_id: conversationId }));
  }

  /** Answer an approval request from the core. */
  async respond(id: string, approve: boolean): Promise<void> {
    const ws = await this.open();
    ws.send(JSON.stringify({ type: "approval", id, approve }));
  }

  /** Stop the run in progress. */
  async cancel(): Promise<void> {
    const ws = await this.open();
    ws.send(JSON.stringify({ type: "cancel" }));
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
  }
}
