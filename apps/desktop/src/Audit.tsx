import { useState } from "react";

import { api } from "./api";
import { usePaged } from "./usePaged";

const PAGE = 20;

/** Every approval decision, newest first, with a check that the record has not been altered. */
export function Audit() {
  // The check covers the whole record on every page, so keep the latest answer.
  const [status, setStatus] = useState<{ verified: boolean; problem: string | null } | null>(null);
  const entries = usePaged(
    async (limit, offset) => {
      const report = await api.audit(limit, offset);
      setStatus({ verified: report.verified, problem: report.problem });
      return report.entries;
    },
    PAGE,
    [],
  );

  return (
    <>
      <p className="hint">
        Each time Piyo asked and you answered. The exact request is kept and linked to the one before it, so a change
        to an old entry shows up here.
      </p>
      {entries.error && <p className="error">{entries.error}</p>}
      {status && (
        <p className={`audit-status ${status.verified ? "" : "bad"}`} role="status">
          {status.verified
            ? `Record checked: ${!entries.loading && entries.items.length === 0 ? "nothing recorded yet" : "no changes found"}.`
            : `This record may have been changed. ${status.problem ?? ""}`}
        </p>
      )}
      {entries.items.map((e) => (
        <div key={e.seq} className="audit-entry">
          <p>
            <span className={`decision ${e.decision}`}>{e.decision === "allowed" ? "Allowed" : "Declined"}</span>
            <strong>{e.summary}</strong>
          </p>
          <p className="hint">
            {new Date(e.at).toLocaleString()} · {e.tool}
          </p>
          {e.why && <p className="hint">Piyo said: “{e.why}”</p>}
          <details className="approval-details">
            <summary>Exact request</summary>
            <pre>{JSON.stringify(e.arguments, null, 2)}</pre>
            <p className="hint">Fingerprint of the exact arguments: {e.args_digest.slice(0, 16)}…</p>
          </details>
        </div>
      ))}
      {entries.hasMore && (
        <div className="pager">
          <button className="ghost" onClick={entries.loadMore} disabled={entries.loading}>
            {entries.loading ? "Loading…" : "Show more"}
          </button>
        </div>
      )}
    </>
  );
}
