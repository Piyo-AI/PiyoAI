import { useEffect, useState } from "react";

import { api, GoogleStatus, openExternal } from "./api";

const GOOGLE_ACCESS: { group: string; label: string; short: string }[] = [
  { group: "gmail_draft", label: "Save email drafts", short: "drafts" },
  { group: "gmail_send", label: "Send email (you approve every send)", short: "sending" },
  { group: "gmail_modify", label: "Archive and label email (you approve each change)", short: "archive and labels" },
  { group: "calendar_write", label: "Add, change and delete calendar events (you approve each one)", short: "calendar changes" },
];

function AccessBoxes({
  granted,
  selected,
  onChange,
}: {
  granted: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="inline" style={{ flexWrap: "wrap" }}>
      {GOOGLE_ACCESS.map(({ group, label }) => (
        <label key={group} className="check">
          <input
            type="checkbox"
            checked={selected.includes(group) || granted.includes(group)}
            disabled={granted.includes(group)}
            onChange={(e) => onChange(e.target.checked ? [...selected, group] : selected.filter((g) => g !== group))}
          />
          {label}
        </label>
      ))}
    </div>
  );
}

const accessSummary = (groups: string[]) => {
  const more = GOOGLE_ACCESS.filter((a) => groups.includes(a.group)).map((a) => a.short);
  return more.length ? `reading, ${more.join(", ")}` : "reading only";
};

export function GoogleConnection({ onChange }: { onChange: () => void }) {
  const [status, setStatus] = useState<GoogleStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [signInUrl, setSignInUrl] = useState<string | null>(null);
  const [extra, setExtra] = useState<string[]>([]); // access chosen for the next sign-in
  const [panel, setPanel] = useState<string | null>(null); // "new" or the email whose access is being changed
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .googleStatus()
      .then((next) => {
        setStatus((prev) => {
          const changed =
            prev &&
            (prev.state !== next.state || prev.accounts.map((a) => a.email).join() !== next.accounts.map((a) => a.email).join());
          if (changed) onChange();
          return next;
        });
        if (next.state !== "connecting") setSignInUrl(null);
      })
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (status?.state !== "connecting") return;
    const timer = setInterval(load, 2000); // the sign-in finishes in the user's browser
    return () => clearInterval(timer);
  }, [status?.state]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  /** Opens Google's sign-in: for a new account, or (with `account`) to add access to one that exists. */
  const connect = (account?: string) =>
    act(async () => {
      const { url } = await api.connectGoogle(extra, account);
      setPanel(null);
      setSignInUrl(url);
      await openExternal(url);
    });

  const state = status?.state ?? "disconnected";
  const accounts = status?.accounts ?? [];
  const badge =
    state === "connecting" ? "connecting" : accounts.length ? `${accounts.length} connected` : "disconnected";

  return (
    <section className="folders">
      <div className="row-head">
        <h3>Google (Gmail and Calendar)</h3>
        <span className={accounts.length ? "badge ok" : "badge"}>{badge}</span>
      </div>
      <p className="hint">
        Uses your own Google Cloud OAuth client, so nothing passes through us. You can connect more than one account;
        Piyo asks which one to use when it matters. Each sign-in token is stored in your operating system's keychain
        and removed when you disconnect that account.{" "}
        {status && (
          <a href={status.console_url} target="_blank" rel="noreferrer">
            Create a client
          </a>
        )}
      </p>
      {(error || status?.error) && <p className="error">{error ?? status?.error}</p>}
      {accounts.length === 0 && (
        <form
          className="inline"
          onSubmit={(e) => {
            e.preventDefault();
            if (clientId.trim())
              act(() => api.setGoogleClient(clientId.trim(), clientSecret.trim() || null).then(() => setClientSecret("")));
          }}
        >
          <input
            autoComplete="off"
            placeholder={status?.has_client ? "Replace client ID" : "OAuth client ID"}
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
          />
          <input
            type="password"
            autoComplete="off"
            placeholder="Client secret (if your client has one)"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
          />
          <button type="submit" disabled={!clientId.trim()}>
            Save client
          </button>
        </form>
      )}

      {accounts.length > 0 && (
        <ul className="providers accounts">
          {accounts.map((a) => (
            <li key={a.email}>
              <div className="row-head">
                <strong>{a.email}</strong>
                <span className="hint" style={{ margin: 0 }}>
                  {accessSummary(a.groups)}
                </span>
              </div>
              {panel === a.email ? (
                <>
                  <AccessBoxes granted={a.groups} selected={extra} onChange={setExtra} />
                  <div className="inline">
                    <button type="button" onClick={() => connect(a.email)} disabled={extra.length === 0 || state === "connecting"}>
                      Add the selected access
                    </button>
                    <button type="button" className="ghost" onClick={() => setPanel(null)}>
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <div className="inline">
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      setExtra([]);
                      setPanel(a.email);
                    }}
                    disabled={state === "connecting"}
                  >
                    Change access
                  </button>
                  <button type="button" className="ghost" onClick={() => act(() => api.disconnectGoogle({ account: a.email }))}>
                    Disconnect
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {state === "connecting" && (
        <div className="inline">
          <span className="hint">Waiting for you to finish in the browser…</span>
          {signInUrl && (
            <button type="button" className="ghost" onClick={() => act(() => openExternal(signInUrl))}>
              Open sign-in page again
            </button>
          )}
          <button type="button" className="ghost" onClick={() => act(() => api.cancelGoogle())}>
            Cancel
          </button>
        </div>
      )}

      {state !== "connecting" && accounts.length === 0 && (
        <>
          <p className="hint">Reading is always included. Allow more only if you want Piyo to do these:</p>
          <AccessBoxes granted={[]} selected={extra} onChange={setExtra} />
          <div className="inline">
            <button type="button" onClick={() => connect()} disabled={!status?.has_client}>
              Connect Google
            </button>
          </div>
        </>
      )}

      {state !== "connecting" && accounts.length > 0 && (
        <>
          {panel === "new" ? (
            <>
              <p className="hint">Google will let you pick the account to add. Access for the new account:</p>
              <AccessBoxes granted={[]} selected={extra} onChange={setExtra} />
              <div className="inline">
                <button type="button" onClick={() => connect()}>
                  Continue in browser
                </button>
                <button type="button" className="ghost" onClick={() => setPanel(null)}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <div className="inline">
              <button
                type="button"
                onClick={() => {
                  setExtra([]);
                  setPanel("new");
                }}
              >
                Add another account
              </button>
              <button type="button" className="ghost" onClick={() => act(() => api.disconnectGoogle({ removeClient: true }))}>
                Disconnect all and forget client
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
