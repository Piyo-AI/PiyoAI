import { useAppVersion, useRollback, useUpdate } from "./useUpdate";

/** Settings > Updates: look for a new version, install it on request, restart to finish. */
export function UpdatesPage() {
  const { status, check, install, restart } = useUpdate(true);
  const version = useAppVersion();
  const rollback = useRollback();

  return (
    <section className="folders">
      <h3>Updates</h3>
      {version && <p>Current version: {version}</p>}
      <p className="hint">
        Piyo checks for a new version when it starts and never downloads or installs one without you pressing Download and install. Every
        download is verified against Piyo's update key before it is installed; a file that does not match is refused.
      </p>
      {rollback.state !== "idle" && (
        <div className="banner warn" role="status">
          {rollback.state === "failed" ? (
            <p className="error">Could not go back to version {rollback.version}: {rollback.error}</p>
          ) : (
            <>
              <p>
                Going back to version {rollback.version}:{" "}
                {rollback.state === "installing"
                  ? "installing, Piyo restarts in a moment…"
                  : `downloading${rollback.percent !== null ? ` (${rollback.percent}%)` : ""}…`}
              </p>
              <progress max={100} value={rollback.percent ?? undefined} />
            </>
          )}
        </div>
      )}
      {status.state === "unavailable" && <p className="hint">Updates are only available in the installed app.</p>}
      {status.state === "checking" && <p>Checking…</p>}
      {status.state === "current" && <p>Piyo is up to date.</p>}
      {status.state === "available" && (
        <>
          <p>Version {status.version} is available.</p>
          {status.flagged && (
            <p className="error">
              Warning: version {status.version} did not start properly on this computer before, and Piyo went back
              to an earlier version. Installing it again may fail the same way.
            </p>
          )}
          {status.notes && <pre className="hint">{status.notes}</pre>}
          <button onClick={install}>{status.flagged ? "Download and install anyway" : "Download and install"}</button>
        </>
      )}
      {status.state === "installing" && (
        <div role="status">
          {status.phase === "preparing" && <p>Getting ready: checking the update and contacting the download server…</p>}
          {status.phase === "downloading" && (
            <>
              <p>Downloading the update{status.percent !== null ? ` (${status.percent}%)` : ""}…</p>
              <progress max={100} value={status.percent ?? undefined} />
            </>
          )}
          {status.phase === "installing" && <p>Download finished. Checking its signature and installing…</p>}
        </div>
      )}
      {status.state === "restart" && (
        <>
          <p role="status">The update is installed. Restart Piyo to use it.</p>
          <button onClick={restart}>Restart now</button>
        </>
      )}
      {status.state === "error" && <p className="error">Could not update: {status.message}</p>}
      {(status.state === "idle" || status.state === "current" || status.state === "error") && (
        <button className="ghost" onClick={check}>
          Check again
        </button>
      )}
    </section>
  );
}
