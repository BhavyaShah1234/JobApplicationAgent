import { useCallback, useEffect, useRef, useState } from "react";
import { get, SignedOut } from "./api.js";

// Fetch `path` now and then every `ms` milliseconds (not while the tab is hidden). Changing the data never rebuilds the page: React updates
// only what changed, so text typed into a box is never lost. Returns {data, error, reload}.
export function usePoll(path, ms = 6000) {
  const [state, setState] = useState({ data: null, error: null, updatedAt: null });
  const alive = useRef(true);
  const resource = useRef(path);
  const sequence = useRef(0);
  const pending = useRef(null);
  resource.current = path;
  const load = useCallback(async () => {
    if (pending.current?.path === path) return pending.current.promise;
    const id = ++sequence.current;
    const request = (async () => {
    try {
      const data = await get(path);
      if (alive.current && resource.current === path && id === sequence.current) setState({ data, error: null, updatedAt: Date.now() });
    } catch (e) {
      if (!alive.current || resource.current !== path || id !== sequence.current) return;
      if (e instanceof SignedOut) window.dispatchEvent(new Event("signed-out"));
      setState((s) => ({ ...s, error: e }));
    } finally {
      if (pending.current?.id === id) pending.current = null;
    }
    })();
    pending.current = { path, id, promise: request };
    return request;
  }, [path]);
  useEffect(() => setState({ data: null, error: null, updatedAt: null }), [path]);
  useEffect(() => {
    alive.current = true;
    load();
    const id = ms ? setInterval(() => !document.hidden && load(), ms) : null;
    return () => {
      alive.current = false;
      if (id) clearInterval(id);
    };
  }, [load, ms]);
  return { ...state, reload: load };
}
