import { useRef, useState } from "react";

import { api, Catalog, InstallPreview } from "./api";

/** "tool:gmail.send" -> a sentence the user can judge. */
export function describePermission(label: string): string {
  const i = label.indexOf(":");
  const kind = label.slice(0, i);
  const value = label.slice(i + 1);
  switch (kind) {
    case "tool":
      return `Use the ${value} tool`;
    case "integration":
      return `Use your ${value} connection`;
    case "secret":
      return `Read the stored secret ${value}`;
    case "script":
      return `Run its own script ${value}`;
    case "runtime":
      return `Run code with ${value}`;
    default:
      return label;
  }
}

export function SkillInstall({ onChange }: { onChange: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<InstallPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [browsing, setBrowsing] = useState(false);

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
      </button>
      {error && <p className="error">{error}</p>}
      {browsing && catalog && !preview && (
        <ul className="providers catalog">
          {catalog.skills.length === 0 && <li className="hint">The catalog has no skills yet.</li>}
          {catalog.skills.map((e) => {
            const update = e.installed_version !== null && e.installed_version !== e.version;
            return (
              <li key={e.name}>
                <div className="row-head">
                  <strong>{e.name}</strong>
                  <span className="hint skill-version">
                    v{e.version}
                    {e.author ? ` · ${e.author}` : ""}
                    {e.license ? ` · ${e.license}` : ""}
                  </span>
                  <button
                    type="button"
                    style={{ marginLeft: "auto" }}
                    className={e.installed_version && !update ? "ghost" : undefined}
                    disabled={busy || e.builtin || (e.installed_version !== null && !update)}
                    onClick={() => review(e.name)}
                  >
                    {e.builtin ? "Built in" : update ? `Update from v${e.installed_version}` : e.installed_version ? "Installed" : "Install"}
                  </button>
                </div>
                <p className="hint">{e.description}</p>
                <p className="hint">Asks for: {e.permissions.map(describePermission).join("; ") || "nothing"}</p>
              </li>
            );
          })}
          {catalog.skipped > 0 && (
            <li className="hint">{catalog.skipped} skill(s) need a newer version of Piyo and are not shown.</li>
          )}
        </ul>
      )}
      {preview && (
        <div className="install-review" role="dialog" aria-label="Review skill">
          <h4>
            {isUpdate ? "Update" : "Install"} {preview.name} <span className="hint">v{preview.version}</span>
            <sup className="pill unverified">Unverified</sup>
          </h4>
          <p>{preview.description}</p>
          <p className="hint">
            {preview.author ? `By ${preview.author}. ` : ""}From {preview.source}.{" "}
            {preview.source.startsWith("catalog")
              ? "The download matched the catalog's fingerprint, but the catalog is not signed yet, so Piyo cannot vouch for it."
              : "This file did not come from the catalog, so Piyo cannot vouch for it."}{" "}
            Only install skills from people you trust.
          </p>
          {isUpdate && <p className="hint">Replaces v{preview.installed_version}; the old version is kept as a backup.</p>}
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
