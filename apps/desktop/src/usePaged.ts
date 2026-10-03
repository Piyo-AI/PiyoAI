import { useCallback, useEffect, useRef, useState } from "react";

/**
 * A list loaded a page at a time. `fetchPage` returns `limit` items starting at `offset`; the hook asks for one
 * extra item to learn whether there is a next page, so the API needs no total count.
 * The list resets when `deps` change. `reload` refreshes what is already shown (after a change elsewhere).
 */
export function usePaged<T>(
  fetchPage: (limit: number, offset: number) => Promise<T[]>,
  pageSize: number,
  deps: unknown[],
) {
  const [items, setItems] = useState<T[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;
  const shown = useRef(0);
  // Only the newest request may write state, so a slow old answer cannot overwrite a newer list.
  const ticket = useRef(0);

  const load = useCallback(
    async (count: number, offset: number, replace: boolean) => {
      const mine = ++ticket.current;
      setLoading(true);
      try {
        const got = await fetchRef.current(count + 1, offset);
        if (mine !== ticket.current) return;
        const page = got.slice(0, count);
        shown.current = replace ? page.length : shown.current + page.length;
        setItems((old) => (replace ? page : [...old, ...page]));
        setHasMore(got.length > count);
        setError("");
      } catch (e) {
        if (mine === ticket.current) setError((e as Error).message);
      } finally {
        if (mine === ticket.current) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    shown.current = 0;
    setItems([]);
    setHasMore(false);
    load(pageSize, 0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const loadMore = useCallback(() => load(pageSize, shown.current, false), [load, pageSize]);
  const reload = useCallback(() => load(Math.max(shown.current, pageSize), 0, true), [load, pageSize]);

  return { items, hasMore, loading, error, loadMore, reload };
}
