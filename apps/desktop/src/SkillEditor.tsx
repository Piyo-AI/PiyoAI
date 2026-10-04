import { useEffect, useState } from "react";

import { api, SkillCheck, SkillDraft, SkillVersion } from "./api";
import { describePermission } from "./catalog";

/** Writes a new skill (`name` undefined) or edits one the user installed or wrote. */
export function SkillEditor({
  name,
  draft,
  learned = false,
  onClose,
  onSaved,
}: {
  name?: string;
  /** Start from this text instead of the saved files (a skill learned from a chat, or a proposed improvement). */
  draft?: SkillDraft;
  learned?: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [skillMd, setSkillMd] = useState("");
  const [setupMd, setSetupMd] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [check, setCheck] = useState<SkillCheck | null>(null);
  const [history, setHistory] = useState<SkillVersion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const start = draft ? Promise.resolve(draft) : name ? api.skillFiles(name) : api.skillTemplate();
    start
      .then((f) => {
        setSkillMd(f.skill_md);
        setSetupMd(f.setup_md);
        setLoaded(true);
      })
      .catch((e) => setError((e as Error).message));
    if (name) api.skillHistory(name).then(setHistory).catch(() => {});
  }, [name, draft]);

  // Live validation: ask the core (which uses the same parser as the loader) shortly after typing stops.
  useEffect(() => {
    if (!loaded) return;
    const timer = setTimeout(() => {
      api.checkSkill(skillMd, name).then(setCheck).catch((e) => setError((e as Error).message));
    }, 300);
    return () => clearTimeout(timer);
  }, [skillMd, loaded, name]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approved = check?.added ?? [];
  const save = () =>
    run(() =>
      name ? api.saveSkillFiles(name, skillMd, setupMd, approved) : api.createSkill(skillMd, setupMd, approved, learned),
    );

  const restore = (v: SkillVersion) => {
    if (!name) return;
    // Old versions may carry permissions the skill no longer has: the core says which, and the user confirms.
    const label = `${v.version} (${new Date(v.at).toLocaleString()})`;
    if (!window.confirm(`Go back to version ${label}? Your current version is kept, so you can undo this.`)) return;
    run(async () => {
      try {
        await api.rollbackSkill(name, v.id, []);
      } catch (e) {
        const message = (e as Error).message;
        const missing = message.startsWith("These permissions were not approved: ")
          ? message.replace("These permissions were not approved: ", "").split(", ")
          : null;
        if (!missing) throw e;
        const list = missing.map(describePermission).join("\n");
        if (!window.confirm(`That version also needs these permissions:\n\n${list}\n\nAllow them?`)) throw new Error("Not restored.");
        await api.rollbackSkill(name, v.id, missing);
      }
    });
  };

  return (
    <div className="skill-editor">
      <h4>{name ? (draft ? `Improve ${name}` : `Edit ${name}`) : learned ? "Review the new skill" : "Write a skill"}</h4>
      {learned && (
        <p className="hint">
          Piyo drafted this from your chat. Read it and edit anything that is wrong or too specific. It is saved
          switched off; turn it on under Settings &gt; Skills when you are happy with it.
        </p>
      )}
      {draft && draft.removed.length > 0 && (
        <p className="hint">Piyo removed personal details from the draft: {draft.removed.join(", ")}.</p>
      )}
      {draft && draft.diff && (
        <details open>
          <summary>What changes</summary>
          <pre className="diff">{draft.diff}</pre>
        </details>
      )}
      <p className="hint">
        <code>SKILL.md</code> tells Piyo what to do and which tools it may use. The lines between the dashes are the
        settings; below them, write the steps in plain words. Piyo only gets the tools listed under{" "}
        <code>requires: tools</code>, and risky actions still ask you every time.
      </p>
      {error && <p className="error">{error}</p>}
      <label className="hint" htmlFor="skill-md">
        SKILL.md
      </label>
      <textarea
        id="skill-md"
        className="code"
        value={skillMd}
        onChange={(e) => setSkillMd(e.target.value)}
        rows={16}
        spellCheck={false}
      />
      {check && !check.ok && <p className="error">{check.error}</p>}
      {check?.ok && (
        <p className="hint">
          Looks good: {check.name} v{check.version}.{" "}
          {check.permissions.length ? `It asks for: ${check.permissions.map(describePermission).join("; ")}.` : "It asks for no tools."}
        </p>
      )}
      {check?.ok && check.added.length > 0 && (
        <p className="error">
          Saving gives it permissions it did not have: {check.added.map(describePermission).join("; ")}.
        </p>
      )}
      <label className="hint" htmlFor="setup-md">
        SETUP.md (optional: what the user must do before using it)
      </label>
      <textarea
        id="setup-md"
        className="code"
        value={setupMd}
        onChange={(e) => setSetupMd(e.target.value)}
        rows={6}
        spellCheck={false}
      />
      <p className="install-actions">
        <button type="button" disabled={busy || !check?.ok} onClick={save}>
          {check?.ok && check.added.length ? "Approve and save" : "Save"}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </p>
      {history.length > 0 && (
        <details>
          <summary>Earlier versions ({history.length})</summary>
          <ul className="permissions">
            {history.map((v) => (
              <li key={v.id}>
                v{v.version}, {new Date(v.at).toLocaleString()} ({v.reason}){" "}
                <button type="button" className="ghost" disabled={busy} onClick={() => restore(v)}>
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
