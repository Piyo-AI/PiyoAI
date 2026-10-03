import { UiMessage } from "./useChat";
import { useBrowser } from "./useBrowser";
import { useBrowserInstall } from "./useBrowserInstall";

/** True while the run in progress is waiting on one of the browser tools. */
function usingBrowser(busy: boolean, messages: UiMessage[]): boolean {
  if (!busy) return false;
  const tools = messages[messages.length - 1]?.tools ?? [];
  return tools.some((t) => t.output === undefined && /^browser(\.|__)/.test(t.name));
}

/** Shown while Piyo's browser is running: what it is doing, show/hide the window, close its pages. */
export function BrowserBar({ busy, messages }: { busy: boolean; messages: UiMessage[] }) {
  const working = usingBrowser(busy, messages);
  const toolCount = messages.reduce((n, m) => n + (m.tools?.length ?? 0), 0);
  const { status, error, setVisible, closePages } = useBrowser(`${busy}:${toolCount}`);
  // Offer the download only once a browser tool has said the browser is missing (or a download is under way).
  const missing = messages.some((m) =>
    (m.tools ?? []).some((t) => t.isError && t.output?.includes("browser is not installed yet")),
  );
  const install = useBrowserInstall(`${busy}:${toolCount}`);
  const phase = install.status?.state;
  if (phase === "installing" || phase === "failed" || (phase === "missing" && missing)) {
    return (
      <div className="banner warn browser-bar" role="status">
        <p>
          {phase === "installing"
            ? `Downloading Piyo's browser… ${install.status?.percent ?? 0}%`
            : phase === "failed"
              ? install.status?.message
              : "Piyo's browser needs a one-time download (about 150 MB) before it can open pages."}
        </p>
        {phase !== "installing" && (
          <div className="inline">
            <button type="button" onClick={install.install}>
              {phase === "failed" ? "Try again" : "Install browser"}
            </button>
          </div>
        )}
        {phase === "installing" && <progress value={install.status?.percent ?? 0} max={100} />}
        {install.error && <p className="error">{install.error}</p>}
      </div>
    );
  }
  if (!status?.running && !working) return null;

  let host = "";
  try {
    host = status?.url ? new URL(status.url).host : "";
  } catch {
    /* not a URL: show nothing */
  }
  return (
    <div className="banner warn browser-bar" role="status">
      <p>
        {working ? <span className="working-dot" aria-hidden="true" /> : null}
        {working ? "Piyo is using its browser" : "Piyo's browser is open"}
        {host ? ` · ${host}` : ""}
      </p>
      <div className="inline">
        <button type="button" className="ghost" onClick={() => setVisible(!status?.visible)}>
          {status?.visible ? "Hide browser" : "Show browser"}
        </button>
        <button type="button" className="ghost" onClick={closePages} disabled={!status?.url}>
          Close pages
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
