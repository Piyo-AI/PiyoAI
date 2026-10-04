import { useEffect, useRef, useState } from "react";

import { api, Catalog, GitStage, InstallPreview } from "./api";
import { Badge } from "./Badge";
import { CatalogBrowser } from "./CatalogBrowser";
import { BADGES, describePermission, withdrawn } from "./catalog";

/** True when version `a` is newer than `b` (dotted numbers; anything else compares as text). */
function newer(a: string, b: string): boolean {
  const pa = a.split(".").map((x) => parseInt(x, 10));
  const pb = b.split(".").map((x) => parseInt(x, 10));
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (Number.isNaN(x) || Number.isNaN(y)) return a !== b && a > b;
    if (x !== y) return x > y;
  }
  return false;
}

export function SkillInstall({ onChange }: { onChange: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<InstallPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [gitOpen, setGitOpen] = useState(false);
  const [gitUrl, setGitUrl] = useState("");
  const [tokenHosts, setTokenHosts] = useState<{ key: string; name: string; has_token: boolean }[]>([]);
  const [tokenInput, setTokenInput] = useState<Record<string, string>>({});
  const [gitChoice, setGitChoice] = useState<GitStage | null>(null);
  const [updates, setUpdates] = useState<Catalog["skills"]>([]);

  // Quietly look for newer versions of installed catalog skills (no error if offline).
  useEffect(() => {
    api
      .catalog()
      .then((c) => {
        setCatalog(c);
        setUpdates(c.skills.filter(
            (e) => !e.builtin && e.installed_version !== null && newer(e.version, e.installed_version) && !withdrawn(e, e.version),
          ));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (gitOpen) api.gitTokens().then((r) => setTokenHosts(r.hosts)).catch(() => {});
  }, [gitOpen]);

  const changeToken = async (change: () => Promise<void>) => {
    setError(null);
    try {
      await change();
      setTokenHosts((await api.gitTokens()).hosts);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const stageGit = async (folder?: string) => {
    setError(null);
    setBusy(true);
    try {
      const res = await api.stageGitSkill(gitUrl.trim(), folder, folder ? gitChoice?.commit ?? undefined : undefined);
      if (res.preview) {
        setGitChoice(null);
        setPreview(res.preview);
      } else {
        setGitChoice(res);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const browse = async () => {
    setError(null);
    setBusy(true);
    try {
      setCatalog(await api.catalog());
      setBrowsing(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const review = async (name: string) => {
    if (!catalog) return;
    setError(null);
    setBusy(true);
    try {
      setPreview(await api.stageCatalogSkill(name, catalog.commit));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const choose = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    setBusy(true);
    try {
      setPreview(await api.previewSkillInstall(await file.arrayBuffer()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = ""; // allow choosing the same file again
    }
  };

  const close = () => {
    if (preview) api.cancelSkillInstall(preview.token).catch(() => {});
    setPreview(null);
  };

  const install = async () => {
    if (!preview) return;
    setError(null);
    setBusy(true);
    try {
      await api.installSkill(preview.token, preview.added);
      setPreview(null);
      onChange();
      if (browsing) setCatalog(await api.catalog().catch(() => catalog)); // show "Installed" right away
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const isUpdate = preview?.installed_version != null;

  return (
    <div className="skill-install">
      <input
        ref={input}
        type="file"
        accept=".piyoskill,.zip"
        hidden
        onChange={(e) => choose(e.target.files?.[0])}
      />
      <button type="button" disabled={busy || preview !== null} onClick={() => input.current?.click()}>
        Install a skill from a file
      </button>{" "}
      <button type="button" className="ghost" disabled={busy || preview !== null} onClick={browsing ? () => setBrowsing(false) : browse}>
        {browsing ? "Hide catalog" : "Browse skills"}
      </button>{" "}
      <button type="button" className="ghost" disabled={busy || preview !== null} onClick={() => setGitOpen(!gitOpen)}>
        From a Git address
      </button>
      {updates.length > 0 && !preview && (
        <div className="banner warn" role="status">
          <p>
            {updates.length === 1 ? "A newer version is available: " : "Newer versions are available: "}
            {updates.map((u) => `${u.name} ${u.installed_version} → ${u.version}`).join(", ")}
          </p>
          <div className="inline">
            {updates.map((u) => (
              <button key={u.name} type="button" disabled={busy} onClick={() => review(u.name)}>
                Review {u.name}
              </button>
            ))}
          </div>
        </div>
      )}
      {gitOpen && !preview && (
        <div className="install-review">
          <p className="hint">
            Paste an address on GitHub, GitLab.com or Codeberg: the repository, or a link to a branch or folder. Piyo downloads one exact commit,
            shows you what it asks for, and installs nothing until you approve. It is not checked by anyone.
          </p>
          <div className="memory-add">
            <input
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              placeholder="https://github.com/owner/repo"
              aria-label="Git address"
            />
            <button type="button" disabled={busy || !gitUrl.trim()} onClick={() => stageGit()}>
              Look up
            </button>
          </div>
          <details className="hint">
            <summary>Private repositories{tokenHosts.some((h) => h.has_token) ? " (access token saved)" : ""}</summary>
            <p>
              For a private repository, create an access token on that site that can only read that repository, and paste it
              here. It is kept in your operating system's keychain, never shown again, and only sent to that site when you
              install from one of its addresses.
            </p>
            {tokenHosts.map((h) => (
              <div key={h.key} className="memory-add">
                <span>{h.name}</span>
                {h.has_token ? (
                  <button type="button" className="ghost" onClick={() => changeToken(() => api.deleteGitToken(h.key))}>
                    Remove the saved token
                  </button>
                ) : (
                  <>
                    <input
                      type="password"
                      autoComplete="off"
                      value={tokenInput[h.key] ?? ""}
                      onChange={(e) => setTokenInput({ ...tokenInput, [h.key]: e.target.value })}
                      placeholder={`${h.name} access token`}
                      aria-label={`${h.name} access token`}
                    />
                    <button
                      type="button"
                      disabled={!(tokenInput[h.key] ?? "").trim()}
                      onClick={() =>
                        changeToken(async () => {
                          await api.setGitToken(h.key, tokenInput[h.key]);
                          setTokenInput({ ...tokenInput, [h.key]: "" });
                        })
                      }
                    >
                      Save token
                    </button>
                  </>
                )}
              </div>
            ))}
          </details>
          {gitChoice && gitChoice.choose.length > 0 && (
            <>
              <p>This address has several skills. Which one?</p>
              <ul className="permissions">
                {gitChoice.choose.map((f) => (
                  <li key={f}>
                    <button type="button" className="ghost" disabled={busy} onClick={() => stageGit(f)}>
                      {f}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {browsing && catalog && !preview && <CatalogBrowser catalog={catalog} busy={busy} onReview={review} />}
      {preview && (
        <div className="install-review" role="dialog" aria-label="Review skill">
          <h4>
            {isUpdate ? "Update" : "Install"} {preview.name} <span className="hint">v{preview.version}</span>
            {preview.verified ? <sup><Badge badge={preview.badge} /></sup> : <sup className="pill unverified">Unverified</sup>}
          </h4>
          <p>{preview.description}</p>
          {isUpdate && <p className="hint">Replaces v{preview.installed_version}; the old version is kept as a backup.</p>}
          <div className="permission-box">
            {preview.added.length === 0 ? (
              <p className="hint">It asks for nothing you have not already approved.</p>
            ) : (
              <>
                <p>
                  <strong>{isUpdate ? "New permissions to approve:" : "This skill will be able to:"}</strong>
                </p>
                <ul className="permissions">
                  {preview.added.map((p) => (
                    <li key={p}>{describePermission(p)}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
          <p className="hint">
            {preview.author ? `By ${preview.author}. ` : ""}From {preview.source}.{" "}
            {preview.verified
              ? `${BADGES[preview.badge]?.hint ?? BADGES.community.hint}. The catalog's signature checked out and this download matches its fingerprint, so it is the version the catalog maintainers published.`
              : "This file did not come from the signed catalog, so Piyo cannot vouch for it. Only install skills from people you trust."}
          </p>
          {isUpdate && preview.permissions.length > preview.added.length && (
            <p className="hint">
              Already approved:{" "}
              {preview.permissions
                .filter((p) => !preview.added.includes(p))
                .map(describePermission)
                .join("; ")}
            </p>
          )}
          <p className="hint">Risky actions still ask you every time, whatever a skill says.</p>
          <details>
            <summary>{preview.files.length} files</summary>
            <ul className="permissions">
              {preview.files.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </details>
          <p className="install-actions">
            <button type="button" disabled={busy} onClick={install}>
              {preview.added.length ? "Approve and install" : isUpdate ? "Update" : "Install"}
            </button>
            <button type="button" className="ghost" disabled={busy} onClick={close}>
              Cancel
            </button>
          </p>
        </div>
      )}
    </div>
  );
}
