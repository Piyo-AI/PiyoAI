import { useCallback, useEffect, useState } from "react";

import { api, CatalogCheck } from "./api";
import { describePermission } from "./catalog";

type Update = CatalogCheck["updates"][number];

const key = (u: Update) => `piyo.update-dismissed.${u.name}.${u.version}`;

function dismissed(u: Update): boolean {
  try {
    return window.localStorage.getItem(key(u)) === "1";
  } catch {
    return false;
  }
}

/** The text of an update notice, including what approving it means. */
export function describeUpdate(u: Update): string {
  const adds = u.adds_permissions.length ? ` It also asks for: ${u.adds_permissions.map(describePermission).join("; ")}.` : "";
  return `${u.name} ${u.version} is available (you have ${u.installed_version}).${adds}`;
}

/**
 * Asks the core to compare the skills installed from the catalog with the current catalog: a withdrawn version
 * is switched off by the core, and this returns what the user has not yet acknowledged, plus updates. Runs at
 * startup and again when `refreshOn` changes (the settings window closing). Offline is silent.
 */
export function useCatalogCheck(refreshOn: unknown[]) {
  const [withdrawn, setWithdrawn] = useState<CatalogCheck["withdrawn"]>([]);
  const [updates, setUpdates] = useState<Update[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .checkCatalog()
      .then((r) => {
        if (cancelled) return;
        setWithdrawn(r.withdrawn.filter((w) => !w.acknowledged));
        setUpdates(r.updates.filter((u) => !dismissed(u)));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, refreshOn);

  const acknowledge = useCallback((name: string) => {
    setWithdrawn((all) => all.filter((w) => w.name !== name));
    api.acknowledgeWithdrawn(name).catch(() => {});
  }, []);

  const dismissUpdate = useCallback((u: Update) => {
    setUpdates((all) => all.filter((x) => x !== u));
    try {
      window.localStorage.setItem(key(u), "1");
    } catch {
      // not remembered; it will show again next start
    }
  }, []);

  return { withdrawn, updates, acknowledge, dismissUpdate };
}
