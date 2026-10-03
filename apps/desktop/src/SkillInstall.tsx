import { useRef, useState } from "react";

import { api, InstallPreview } from "./api";

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
      </button>
      {error && <p className="error">{error}</p>}
      {preview && (
        <div className="install-review" role="dialog" aria-label="Review skill">
          <h4>
            {isUpdate ? "Update" : "Install"} {preview.name} <span className="hint">v{preview.version}</span>
            <sup className="pill unverified">Unverified</sup>
          </h4>
          <p>{preview.description}</p>
          <p className="hint">
            {preview.author ? `By ${preview.author}. ` : ""}From {preview.source}. This file did not come from the
            signed catalog, so Piyo cannot vouch for it. Only install skills from people you trust.
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
