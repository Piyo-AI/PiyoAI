import { useState } from "react";

import { RESTART_WARNING } from "./Toasts";
import { UpdateApi, useAppVersion, useRollback } from "./useUpdate";

/** Settings > Updates: look for a new version, download it (in the background), install it on request. The state is the app's, shared with the toast. */
export function UpdatesPage({ update, busy }: { update: UpdateApi; busy: boolean }) {
  const { status, check, download, install, restart } = update;
  const [confirming, setConfirming] = useState(false);
  const version = useAppVersion();
  const rollback = useRollback();

  return (
    <section className="folders">
      <h3>Updates</h3>
      {version && <p>Current version: {version}</p>}
      <p className="hint">
        Piyo checks for a new version when it starts and never downloads or installs one without you asking: Download runs in the
        background, and Install and restart only happens when you confirm. Every download is verified against Piyo's update key before it
        is installed; a file that does not match is refused.
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
          <button onClick={download}>{status.flagged ? "Download anyway" : "Download"}</button>
          <p className="hint">The download runs in the background. Nothing is installed until you choose Install and restart.</p>
        </>
      )}
      {status.state === "downloading" && (
        <div role="status">
          <p>Downloading the update{status.percent !== null ? ` (${status.percent}%)` : ""}. You can close Settings and keep working.</p>
          <progress max={100} value={status.percent ?? undefined} />
        </div>
      )}
      {status.state === "ready" && (
        <div role="status">
          <p>Version {status.version} is downloaded and verified.</p>
          {confirming ? (
            <>
              <p className="hint">
                {RESTART_WARNING}
                {busy ? " A chat is still running; it will be stopped." : ""}
              </p>
              <button onClick={install}>Restart and install</button>{" "}
              <button className="ghost" onClick={() => setConfirming(false)}>
                Not yet
              </button>
            </>
          ) : (
            <button onClick={() => setConfirming(true)}>Install and restart…</button>
          )}
        </div>
      )}
      {status.state === "installing" && <p role="status">Checking the signature and installing. Piyo restarts in a moment…</p>}
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
