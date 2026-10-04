import { useMemo, useState } from "react";

import type { Catalog } from "./api";
import { Badge } from "./Badge";
import { categoriesOf, categoryLabel, describePermission, filterCatalog, setupNeeds, withdrawn } from "./catalog";

/** The skill catalog: search, categories, badges, what each skill asks for and needs, one-click Install. */
export function CatalogBrowser({
  catalog,
  busy,
  onReview,
}: {
  catalog: Catalog;
  busy: boolean;
  onReview: (name: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const categories = useMemo(() => categoriesOf(catalog.skills), [catalog.skills]);
  const shown = useMemo(() => filterCatalog(catalog.skills, query, category), [catalog.skills, query, category]);

  return (
    <div className="catalog-browser">
      <input
        type="search"
        className="catalog-search"
        placeholder="Search skills"
        aria-label="Search skills"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {categories.length > 1 && (
        <div className="catalog-categories" role="group" aria-label="Categories">
          <button type="button" className={category === null ? "chip on" : "chip"} onClick={() => setCategory(null)}>
            All ({catalog.skills.length})
          </button>
          {categories.map((c) => (
            <button
              key={c.name}
              type="button"
              className={category === c.name ? "chip on" : "chip"}
              onClick={() => setCategory(category === c.name ? null : c.name)}
            >
              {categoryLabel(c.name)} ({c.count})
            </button>
          ))}
        </div>
      )}
      <ul className="providers catalog">
        {catalog.skills.length === 0 && <li className="hint">The catalog has no skills yet.</li>}
        {catalog.skills.length > 0 && shown.length === 0 && <li className="hint">No skill matches that.</li>}
        {shown.map((e) => {
          const update = e.installed_version !== null && e.installed_version !== e.version;
          const gone = withdrawn(e, e.version);
          const haveGone = e.installed_version !== null ? withdrawn(e, e.installed_version) : null;
          const needs = setupNeeds(e);
          const label = e.builtin
            ? "Built in"
            : gone
              ? "Withdrawn"
              : update
                ? `Update from v${e.installed_version}`
                : e.installed_version
                  ? "Installed"
                  : "Install";
          return (
            <li key={e.name} className={gone ? "withdrawn" : undefined}>
              <div className="row-head">
                <strong>{e.name}</strong>
                <Badge badge={e.badge} />
                <span className="hint skill-version">
                  v{e.version}
                  {e.author ? ` · ${e.author}` : ""}
                  {e.license ? ` · ${e.license}` : ""}
                  {` · ${categoryLabel(e.category)}`}
                </span>
                <button
                  type="button"
                  style={{ marginLeft: "auto" }}
                  className={(e.installed_version && !update) || gone ? "ghost" : undefined}
                  disabled={busy || e.builtin || !!gone || (e.installed_version !== null && !update)}
                  onClick={() => onReview(e.name)}
                >
                  {label}
                </button>
              </div>
              <p className="hint">{e.description}</p>
              {gone && (
                <p className="error">
                  The catalog withdrew this version: {gone}
                  {e.installed_version === e.version ? " You have it installed; consider removing it." : ""}
                </p>
              )}
              {haveGone && e.installed_version !== e.version && (
                <p className="error">
                  You have v{e.installed_version}, which the catalog withdrew: {haveGone} Consider removing it.
                </p>
              )}
              <div className="catalog-asks">
                <span className="hint">Asks for:</span>
                {e.permissions.length === 0 && <span className="hint">nothing</span>}
                {e.permissions.map((p) => (
                  <span key={p} className="perm" title={p}>
                    {describePermission(p)}
                  </span>
                ))}
              </div>
              <p className="hint">
                <span className={`setup-${needs.level}`}>{needs.text}</span>
                {e.source && (
                  <>
                    {" · "}From {e.source.url.replace("https://", "")} at {e.source.commit.slice(0, 7)}
                  </>
                )}
              </p>
            </li>
          );
        })}
        {catalog.skipped > 0 && (
          <li className="hint">{catalog.skipped} skill(s) need a newer version of Piyo and are not shown.</li>
        )}
      </ul>
    </div>
  );
}
