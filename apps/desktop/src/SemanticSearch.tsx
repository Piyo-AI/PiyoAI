import { useEffect, useState } from "react";

import { api, SemanticStatus } from "./api";

/** Settings > Memory: find memories by meaning. The first time it is turned on, the core downloads the model. */
export function SemanticSearch({ onChange }: { onChange: () => void }) {
  const [status, setStatus] = useState<SemanticStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    api
      .memorySemantic()
      .then(setStatus)
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    refresh();
  }, []);

  // While a download or indexing runs, keep the numbers moving.
  const busy =
    status !== null &&
    status.enabled &&
    (status.state === "installing" || (status.state === "ready" && status.indexed < status.total));
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => {
      api
        .memorySemantic()
        .then((s) => {
          setStatus(s);
          if (s.state === "ready" && s.indexed >= s.total) onChange();
        })
        .catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [busy]);

  const act = async (fn: () => Promise<SemanticStatus>) => {
    setError(null);
    try {
      setStatus(await fn());
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!status) return error ? <p className="error">{error}</p> : null;

  return (
    <>
      <label className="check">
        <input
          type="checkbox"
          checked={status.enabled}
          onChange={(e) => act(() => api.setMemorySemantic(e.target.checked))}
        />
        Find memories by meaning
      </label>
      <p className="hint">
        Lets Piyo find &ldquo;my puppy&rdquo; when you saved &ldquo;the dog is called Biscuit&rdquo;. Turning it on
        downloads about {status.approx_mb} MB once (a small search library and Google&rsquo;s EmbeddingGemma 2 model,
        from github.com and huggingface.co). It then runs on this computer only: your memories never leave it.
      </p>
      {error && <p className="error">{error}</p>}
      {status.enabled && status.state === "installing" && (
        <p className="hint">
          {status.message} {status.percent}%
          <progress max={100} value={status.percent} style={{ marginLeft: 8 }} />
        </p>
      )}
      {status.enabled && status.state === "failed" && (
        <p className="error">
          {status.message}{" "}
          <button type="button" className="ghost" onClick={() => act(() => api.setMemorySemantic(true))}>
            Try again
          </button>
        </p>
      )}
      {status.enabled && status.state === "ready" && (
        <p className="hint">
          {status.model}: {status.indexed} of {status.total} memories can be found by meaning
          {status.indexed < status.total && !status.message ? " (indexing…)" : ""}.
          {status.message && <span className="error"> {status.message}</span>}
        </p>
      )}
      {status.downloaded && (
        <p className="hint">
          <button
            type="button"
            className="ghost"
            onClick={() => {
              if (window.confirm(`Delete the downloaded model and search data (about ${status.approx_mb} MB)?`)) {
                act(() => api.removeMemorySemantic());
              }
            }}
          >
            Remove the downloaded files
          </button>
        </p>
      )}
    </>
  );
}
