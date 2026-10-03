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
  secrets: string[];
  /** What the skill needs from the model: vision, min_context. */
  model_needs: { vision?: boolean; min_context?: number };
  /** Why the model named in the request can't run this skill; empty when it can. */
  model_issues: string[];
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
  runs: (conversationId?: string) =>
    request<RunInfo[]>("GET", `/api/runs${conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ""}`),
  run: (id: string) => request<RunDetail>("GET", `/api/runs/${id}`),
  conversations: () => request<ConversationInfo[]>("GET", "/api/conversations"),
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
  setSkillEnabled: (name: string, enabled: boolean) => request<void>("PUT", `/api/skills/${encodeURIComponent(name)}`, { enabled }),
  audit: () => request<AuditReport>("GET", "/api/audit"),
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
