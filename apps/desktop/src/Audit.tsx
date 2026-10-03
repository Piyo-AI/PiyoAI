import { useEffect, useState } from "react";

import { api, AuditReport } from "./api";

/** Every approval decision, newest first, with a check that the record has not been altered. */
export function Audit() {
  const [report, setReport] = useState<AuditReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .audit()
      .then(setReport)
      .catch((e) => setError((e as Error).message));
  }, []);

  return (
    <>
      <p className="hint">
        Each time Piyo asked and you answered. The exact request is kept and linked to the one before it, so a change
        to an old entry shows up here.
      </p>
      {error && <p className="error">{error}</p>}
      {report && (
        <p className={`audit-status ${report.verified ? "" : "bad"}`} role="status">
          {report.verified
            ? `Record checked: ${report.entries.length === 0 ? "nothing recorded yet" : "no changes found"}.`
            : `This record may have been changed. ${report.problem ?? ""}`}
        </p>
      )}
      {report?.entries.map((e) => (
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
    </>
  );
}
