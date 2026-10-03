import { useEffect, useState } from "react";

import { api, BrowserRules } from "./api";

const lines = (text: string) =>
  text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

/** Settings > Browser: which sites Piyo's browser may visit and how many pages one request may open. */
export function BrowserSettings() {
  const [saved, setSaved] = useState<BrowserRules | null>(null);
  const [allow, setAllow] = useState("");
  const [deny, setDeny] = useState("");
  const [pages, setPages] = useState("");
  const [error, setError] = useState<string | null>(null);

  const show = (r: BrowserRules) => {
    setSaved(r);
    setAllow(r.allow.join("\n"));
    setDeny(r.deny.join("\n"));
    setPages(String(r.max_pages));
  };
  useEffect(() => {
    api
      .browserRules()
      .then(show)
      .catch((e) => setError((e as Error).message));
  }, []);

  const dirty =
    saved != null &&
    (lines(allow).join("\n") !== saved.allow.join("\n") ||
      lines(deny).join("\n") !== saved.deny.join("\n") ||
      Number(pages) !== saved.max_pages);

  const save = async () => {
    setError(null);
    try {
      show(await api.setBrowserRules({ allow: lines(allow), deny: lines(deny), max_pages: Number(pages) }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="folders">
      <h3>Sites Piyo's browser may visit</h3>
      <p className="hint">
        One site per line, like example.com (it covers its subdomains). Blocked sites always win. If the allowed list
        has anything in it, Piyo can open only those sites; leave it empty to allow any public site.
      </p>
      {error && <p className="error">{error}</p>}
      {saved && (
        <form
          className="limits"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <label>
            Allowed sites
            <textarea rows={4} value={allow} onChange={(e) => setAllow(e.target.value)} placeholder="Any public site" />
          </label>
          <label>
            Blocked sites
            <textarea rows={4} value={deny} onChange={(e) => setDeny(e.target.value)} />
          </label>
          <label>
            Pages one request may open
            <input type="number" min={1} max={500} value={pages} onChange={(e) => setPages(e.target.value)} />
          </label>
          <button type="submit" disabled={!dirty}>
            Save
          </button>
        </form>
      )}
    </section>
  );
}
