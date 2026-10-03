import { Approval, ToolActivity } from "./useChat";

const label = (name: string) => name.replace(/__/g, ".").replace(/_/g, " ");

function summary(args: Record<string, unknown>): string {
  const text = Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

/** One tool call inside an assistant bubble; expands to show the result. */
export function ToolChip({ tool }: { tool: ToolActivity }) {
  const running = tool.output === undefined;
  const state = running ? "running" : tool.isError ? "failed" : "ok";
  return (
    <details className={`tool ${state}`}>
      <summary>
        <span className="tool-state">{running ? "…" : tool.isError ? "✕" : "✓"}</span>
        <strong>{label(tool.name)}</strong>
        {summary(tool.arguments) && <span className="muted"> {summary(tool.arguments)}</span>}
      </summary>
      {!running && <pre>{tool.output || "(no output)"}</pre>}
    </details>
  );
}

/** Piyo wants to do something that needs the user's say-so. */
export function ApprovalCard({ approval, onAnswer }: { approval: Approval; onAnswer: (ok: boolean) => void }) {
  return (
    <div className="approval" role="alertdialog" aria-label="Approval needed">
      <div>
        <strong>Piyo wants to run {label(approval.tool)}</strong>
      </div>
      <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
      <div className="approval-actions">
        <button onClick={() => onAnswer(true)}>Allow</button>
        <button className="ghost" onClick={() => onAnswer(false)}>
          Deny
        </button>
      </div>
    </div>
  );
}
