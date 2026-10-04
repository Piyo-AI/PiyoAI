import { useCallback, useEffect, useState } from "react";

import { api, openExternal, TelemetryInfo } from "./api";

const POLICY_URL = "https://github.com/Piyo-AI/PiyoAI/blob/main/PRIVACY.md";

/** Settings > Privacy: the opt-in for anonymous reports, and exactly what would be sent. */
export function PrivacyPage() {
  const [info, setInfo] = useState<TelemetryInfo | null>(null);
  const [error, setError] = useState("");

  const run = useCallback(async (fn: () => Promise<TelemetryInfo>) => {
    setError("");
    try {
      setInfo(await fn());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    run(api.telemetry);
  }, [run]);

  const on = info?.choice === "on";

  return (
    <section className="folders">
      <h3>Privacy</h3>
      <p className="hint">
        Your messages and any files or pages Piyo reads go to the model provider you choose (or stay on this computer
        with a local model). Settings &gt; Limits has an option to use local models only.
      </p>

      <h4>Anonymous usage and crash reports</h4>
      <label className="check">
        <input
          type="checkbox"
          checked={on}
          disabled={!info}
          onChange={(e) => run(() => api.setTelemetry(e.target.checked))}
        />
        Share anonymous usage counts and crash reports
      </label>
      {info?.choice === "unset" && <p className="hint">You have not been asked yet, so nothing is collected.</p>}
      <ul className="hint">
        <li>Shared: Piyo's version, your operating system, which tools and catalog skills ran, and error types with a stack trace.</li>
        <li>
          Never shared: your messages, files, email or chats, web addresses, file names, keys, account names, or the
          text of error messages.
        </li>
        <li>A random install ID ties reports together. Turning this off deletes the ID and anything waiting.</li>
        <li>Crash reports go to Sentry and usage counts to PostHog, both cloud services run by those companies.</li>
      </ul>
      <p>
        <a
          href={POLICY_URL}
          onClick={(e) => {
            e.preventDefault();
            openExternal(POLICY_URL);
          }}
        >
          Read the privacy policy
        </a>
      </p>
      {error && <p className="error">{error}</p>}

      {on && info && (
        <>
          <h4>What would be sent</h4>
          {!info.endpoint_configured && (
            <p className="hint">
              No reporting service is set up in this version, so nothing is sent: the events below only wait on this
              computer.
            </p>
          )}
          <p className="hint">
            Crashes go to Sentry and usage counts to PostHog, both cloud services. Install ID: {info.install_id}
          </p>
          {info.sends.length === 0 ? (
            <p>Nothing is waiting.</p>
          ) : (
            <>
              {info.sends.map((s) => (
                <div key={s.service}>
                  <p className="hint">
                    To {s.service}
                    {s.configured ? "" : " (not set up in this version)"}:
                  </p>
                  <pre className="telemetry-events">{JSON.stringify(s.payload, null, 2)}</pre>
                </div>
              ))}
              <div className="inline">
                <button className="ghost" onClick={() => run(api.clearTelemetry)}>
                  Delete these events
                </button>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
