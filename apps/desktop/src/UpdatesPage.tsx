import { useUpdate } from "./useUpdate";

/** Settings > Updates: look for a new version, install it on request, restart to finish. */
export function UpdatesPage() {
  const { status, check, install, restart } = useUpdate(true);

  return (
    <section className="folders">
      <h3>Updates</h3>
      <p className="hint">
        Piyo checks for a new version when it starts and never installs one without you pressing Install. Every
        download is verified against Piyo's update key before it is installed; a file that does not match is refused.
      </p>
      {status.state === "unavailable" && <p className="hint">Updates are only available in the installed app.</p>}
      {status.state === "checking" && <p>Checking…</p>}
      {status.state === "current" && <p>Piyo is up to date.</p>}
      {status.state === "available" && (
        <>
          <p>Version {status.version} is available.</p>
          {status.notes && <pre className="hint">{status.notes}</pre>}
          <button onClick={install}>Install and restart later</button>
        </>
      )}
      {status.state === "installing" && (
        <p role="status">Downloading and installing{status.percent !== null ? ` (${status.percent}%)` : ""}…</p>
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
