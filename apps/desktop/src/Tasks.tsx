import { useState } from "react";

import { api, RunDetail, RunInfo } from "./api";
import { Audit } from "./Audit";
import { usePaged } from "./usePaged";

const PAGE = 20;

interface Props {
  conversationId: string | null;
  onClose: () => void;
}

const OUTCOMES: Record<string, string> = {
  done: "Finished",
  step_limit: "Stopped: step limit",
  token_limit: "Stopped: token budget",
  timeout: "Stopped: time limit",
  truncated: "Cut off: reply too long",
  cancelled: "Stopped by you",
  error: "Failed",
};

const seconds = (ms: number | null) => (ms == null ? "" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const cost = (r: RunInfo) =>
  r.cost_usd == null
    ? ""
    : r.cost_usd === 0
      ? " · free"
      : ` · ${r.tokens_estimated ? "~" : ""}$${r.cost_usd < 0.01 ? r.cost_usd.toFixed(4) : r.cost_usd.toFixed(2)}`;

const tokens = (r: RunInfo) =>
  `${r.tokens_estimated ? "~" : ""}${r.input_tokens.toLocaleString()} in / ${r.output_tokens.toLocaleString()} out`;

/** Task log: every agent run with its model turns, tool calls and approvals. */
export function Tasks({ conversationId, onClose }: Props) {
  const [thisChat, setThisChat] = useState(!!conversationId);
  const filter = thisChat && conversationId ? conversationId : undefined;
  const runs = usePaged((limit, offset) => api.runs(limit, offset, filter), PAGE, [filter]);
  const [open, setOpen] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"tasks" | "approvals">("tasks");

  const show = (id: string) =>
    api
      .run(id)
      .then(setOpen)
      .catch((e) => setError((e as Error).message));

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>{open ? "Task" : tab === "approvals" ? "Approvals" : "Task log"}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        {(error || runs.error) && <p className="error">{error || runs.error}</p>}
        {!open && (
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "tasks"} className={tab === "tasks" ? "active" : ""} onClick={() => setTab("tasks")}>
              Tasks
            </button>
            <button role="tab" aria-selected={tab === "approvals"} className={tab === "approvals" ? "active" : ""} onClick={() => setTab("approvals")}>
              Approvals
            </button>
          </div>
        )}
        {!open && tab === "approvals" ? (
          <Audit />
        ) : open ? (
          <RunView run={open} onBack={() => setOpen(null)} />
        ) : (
          <>
            <p className="hint">
              What Piyo did for each request. Secrets are removed before anything is saved here.
            </p>
            {conversationId && (
              <label className="check">
                <input type="checkbox" checked={thisChat} onChange={(e) => setThisChat(e.target.checked)} />
                This chat only
              </label>
            )}
            {!runs.loading && runs.items.length === 0 && <p className="hint">No tasks yet.</p>}
            <ul className="runs">
              {runs.items.map((r) => (
                <li key={r.id}>
                  <button className="ghost run-row" onClick={() => show(r.id)}>
                    <span className="run-request">{r.request || "(no text)"}</span>
                    <span className={`run-outcome ${r.outcome}`}>{OUTCOMES[r.outcome] ?? r.outcome}</span>
                    <span className="hint">
                      {new Date(r.started_at).toLocaleString()} · {r.model} · {seconds(r.duration_ms)} · {tokens(r)}{cost(r)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {runs.hasMore && (
              <div className="pager">
                <button className="ghost" onClick={runs.loadMore} disabled={runs.loading}>
                  {runs.loading ? "Loading…" : "Show more"}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function RunView({ run, onBack }: { run: RunDetail; onBack: () => void }) {
  const skillsLoaded = run.steps.filter((s) => s.kind === "skill").map((s) => String(s.data.name));
  return (
    <>
      <button className="ghost" onClick={onBack}>
        ← All tasks
      </button>
      <p className="run-request">{run.request}</p>
      <p className="hint">
        {OUTCOMES[run.outcome] ?? run.outcome} · {run.model} · {seconds(run.duration_ms)} · {tokens(run)}{cost(run)}
        {run.tokens_estimated && " (estimated)"}
      </p>
      {run.error && <p className="error">{run.error}</p>}
      {skillsLoaded.length > 0 && <p className="hint">Skills used: {skillsLoaded.join(", ")}</p>}
      <ol className="steps">
        {run.steps.map((s, i) => (
          <Step key={i} step={s} />
        ))}
      </ol>
    </>
  );
}

function Step({ step }: { step: RunDetail["steps"][number] }) {
  const d = step.data as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  let title: string;
  let body: string;
  let state = "";
  if (step.kind === "model") {
    title = d.tool_calls?.length ? `Model asked for ${d.tool_calls.join(", ")}` : "Model replied";
    body = d.text || "";
    state = d.truncated ? "failed" : "";
  } else if (step.kind === "skill") {
    title = `Loaded skill ${d.name}`;
    body = "";
  } else if (step.kind === "approval") {
    title = `${d.approved ? "You allowed" : "You declined"}: ${d.summary || d.tool}`;
    body = JSON.stringify(d.arguments, null, 2);
    state = d.approved ? "ok" : "failed";
  } else {
    title = d.name;
    body = `${JSON.stringify(d.arguments)}\n\n${d.output}`;
    state = d.is_error ? "failed" : "ok";
  }
  return (
    <li>
      <details className={`tool ${state}`}>
        <summary>
          {title}
          <span className="hint">
            {" "}
            {seconds(step.duration_ms)}
            {step.kind === "model" && ` · ${d.input_tokens} in / ${d.output_tokens} out${d.estimated ? " (est.)" : ""}`}
          </span>
        </summary>
        {body && <pre>{body}</pre>}
      </details>
    </li>
  );
}
