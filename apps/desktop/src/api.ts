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
  tools: boolean | null;
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
  | { type: "delta"; text: string }
  | { type: "tool_start"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_end"; id: string; name: string; output: string; is_error: boolean }
  | { type: "approval_request"; id: string; tool: string; arguments: Record<string, unknown> }
  | { type: "done"; reason: "done" | "step_limit" | "truncated" | "cancelled" }
  | { type: "error"; message: string };

export interface SkillInfo {
  name: string;
  description: string;
  version: string;
  source: "builtin" | "user";
  tools: string[];
  has_setup: boolean;
  enabled: boolean;
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

// In development the core is started by hand with a fixed port and token:
//   PIYO_PORT=8765 PIYO_TOKEN=dev-token uv run piyo-core
// The Tauri shell will later start the core itself and supply a random port and token.
async function resolveConnection(): Promise<Connection> {
  if (import.meta.env.DEV) {
    return {
      port: Number(import.meta.env.VITE_PIYO_PORT ?? 8765),
      token: String(import.meta.env.VITE_PIYO_TOKEN ?? "dev-token"),
    };
  }
  throw new Error("The Piyo core connection is not available.");
}

let connection: Promise<Connection> | null = null;
const getConnection = () => (connection ??= resolveConnection());

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
  skills: () => request<{ skills: SkillInfo[]; errors: Record<string, string> }>("GET", "/api/skills"),
  folders: () => request<{ folders: FolderEntry[] }>("GET", "/api/folders"),
  setFolders: (folders: FolderEntry[]) => request<{ folders: FolderEntry[] }>("PUT", "/api/folders", { folders }),
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

  async send(provider: string, model: string, messages: ChatMessage[]): Promise<void> {
    const ws = await this.open();
    ws.send(JSON.stringify({ type: "chat", provider, model, messages }));
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
