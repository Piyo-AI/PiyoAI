import { FormEvent, useEffect, useState } from "react";

import { api, MemoryItem, MemorySettings } from "./api";

const LABELS: Record<string, string> = {
  preference: "Preference",
  person: "Person",
  routine: "Routine",
  account: "Account",
  outcome: "Past outcome",
  note: "Note",
  health: "Health",
  finance: "Finance",
  identity: "Identity",
};

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

interface Draft {
  id: string;
  text: string;
  category: string;
}

export function MemoryPage() {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [query, setQuery] = useState("");
  const [text, setText] = useState("");
  const [category, setCategory] = useState("note");
  const [editing, setEditing] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = (q = query) =>
    Promise.all([api.memory(q), api.memorySettings()])
      .then(([m, s]) => {
        setItems(m);
        setSettings(s);
      })
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    load("");
  }, []);

  const attempt = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (await attempt(() => api.addMemory(text, category))) setText("");
  };

  const clearAll = () => {
    if (window.confirm("Delete everything Piyo remembers about you? This cannot be undone.")) {
      attempt(() => api.clearMemory());
    }
  };

  const categories = settings?.categories ?? ["note"];
  const options = categories.map((c) => (
    <option key={c} value={c}>
      {LABELS[c] ?? c}
    </option>
  ));

  return (
    <section className="folders">
      <h3>Memory</h3>
      <p className="hint">
        What Piyo remembers about you, in plain sentences. Piyo adds to it when you ask it to remember something, and
        you can change or delete anything here. It never stores passwords, card numbers or ID numbers.
      </p>
      {error && <p className="error">{error}</p>}

      <form className="memory-add" onSubmit={add}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Add something for Piyo to remember"
          maxLength={500}
        />
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          {options}
        </select>
        <button type="submit" disabled={!text.trim()}>
          Add
        </button>
      </form>

      <label className="check">
        <input
          type="checkbox"
          checked={settings?.sensitive ?? false}
          onChange={(e) => attempt(() => api.setMemorySensitive(e.target.checked))}
        />
        Allow health, finance and identity notes
      </label>
      <p className="hint">Off by default. When off, Piyo will not store these even if you ask in chat.</p>

      <input
        className="memory-search"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          load(e.target.value);
        }}
        placeholder="Search"
        aria-label="Search memory"
      />
      {items && items.length === 0 && <p className="hint">{query ? "Nothing matches." : "Nothing remembered yet."}</p>}
      <ul className="providers">
        {items?.map((m) => (
          <li key={m.id}>
            {editing?.id === m.id ? (
              <div className="memory-add">
                <input
                  value={editing.text}
                  onChange={(e) => setEditing({ ...editing, text: e.target.value })}
                  maxLength={500}
                />
                <select value={editing.category} onChange={(e) => setEditing({ ...editing, category: e.target.value })}>
                  {options}
                </select>
                <button
                  type="button"
                  onClick={async () => {
                    if (await attempt(() => api.editMemory(m.id, editing.text, editing.category))) setEditing(null);
                  }}
                >
                  Save
                </button>
                <button type="button" className="ghost" onClick={() => setEditing(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <div className="row-head">
                <span>
                  {m.text}
                  <sup className="pill">{LABELS[m.category] ?? m.category}</sup>
                  {m.source === "piyo" && <sup className="pill">Piyo added</sup>}
                </span>
                <span style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setEditing({ id: m.id, text: m.text, category: m.category })}
                  >
                    Edit
                  </button>{" "}
                  <button type="button" className="ghost" onClick={() => attempt(() => api.deleteMemory(m.id))}>
                    Delete
                  </button>
                </span>
              </div>
            )}
          </li>
        ))}
      </ul>
      <p>
        <button
          type="button"
          className="ghost"
          onClick={() =>
            attempt(async () => download("piyo-memory.json", JSON.stringify(await api.exportMemory(), null, 2)))
          }
        >
          Export
        </button>{" "}
        <button type="button" className="ghost" onClick={clearAll} disabled={!items?.length && !query}>
          Delete everything
        </button>
      </p>
    </section>
  );
}
