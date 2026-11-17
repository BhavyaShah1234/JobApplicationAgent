import { useCallback, useEffect, useRef, useState } from "react";
import { get, SignedOut } from "./api.js";

// Fetch `path` now and then every `ms` milliseconds (not while the tab is hidden). Changing the data never rebuilds the page: React updates
// only what changed, so text typed into a box is never lost. Returns {data, error, reload}.
export function usePoll(path, ms = 6000) {
  const [state, setState] = useState({ data: null, error: null });
  const alive = useRef(true);
  const load = useCallback(async () => {
    try {
      const data = await get(path);
      if (alive.current) setState({ data, error: null });
    } catch (e) {
      if (!alive.current) return;
      if (e instanceof SignedOut) window.dispatchEvent(new Event("signed-out"));
      setState((s) => ({ data: s.data, error: e }));
    }
  }, [path]);
  useEffect(() => setState({ data: null, error: null }), [path]);          // a different resource starts empty; a different interval does not
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
