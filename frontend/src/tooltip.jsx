import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

// One floating tooltip for the whole page. Anything can ask for it with `useTip()`: hover shows it next to the pointer, keyboard focus shows it
// above the element, a tap (touch) shows it and a tap elsewhere, Escape or scrolling hides it. Without a provider (e.g. a bare component in a
// test) the binding is empty and nothing changes.
const Ctx = createContext(null);

export function TipBody({ title, rows = [], note }) {
  return (
    <>
      {title && <div className="tt">{title}</div>}
      {rows.map(([k, v], i) => (
        <div className="tr" key={i}>
          <span>{k}</span>
          <b>{v}</b>
        </div>
      ))}
      {note && <div className="tn">{note}</div>}
    </>
  );
}

function Floating({ tip }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  useLayoutEffect(() => {
    if (!tip || !ref.current) return;
    const { width, height } = ref.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(tip.x + 14, window.innerWidth - width - 8));
    const below = tip.y + 18 + height < window.innerHeight - 8;
    setPos({ left, top: below ? tip.y + 18 : Math.max(8, tip.y - height - 14) });
  }, [tip]);
  if (!tip) return null;
  return (
    <div ref={ref} role="tooltip" className="tip" style={{ left: pos.left, top: pos.top }}>
      {tip.content}
    </div>
  );
}

export function TipProvider({ children }) {
  const [tip, setTip] = useState(null);
  const api = useMemo(
    () => ({
      show: (content, x, y) => setTip({ content, x, y }),
      move: (x, y) => setTip((t) => (t ? { ...t, x, y } : t)),
      hide: () => setTip(null),
    }),
    [],
  );
  useEffect(() => {
    const away = (e) => {
      if (!(e.target instanceof Element) || !e.target.closest("[data-tip]")) setTip(null);
    };
    const esc = (e) => e.key === "Escape" && setTip(null);
    const hide = () => setTip(null);
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("scroll", hide, true);
    };
  }, []);
  return (
    <Ctx.Provider value={api}>
      {children}
      <Floating tip={tip} />
    </Ctx.Provider>
  );
}

// Combine the tooltip's handlers with a component's own (both run).
export function merge(base, extra) {
  const out = { ...base };
  for (const [k, f] of Object.entries(extra)) {
    const g = base[k];
    out[k] = g ? (e) => { f(e); g(e); } : f;
  }
  return out;
}

// `const tip = useTip();  <div {...tip(<TipBody .../>, "plain text for screen readers")}>`
export function useTip() {
  const api = useContext(Ctx);
  return useCallback(
    (content, label) => {
      if (!api) return {};
      return {
        "data-tip": "",
        tabIndex: 0,
        "aria-label": label,
        onMouseEnter: (e) => api.show(content, e.clientX, e.clientY),
        onMouseMove: (e) => api.move(e.clientX, e.clientY),
        onMouseLeave: api.hide,
        onFocus: (e) => {
          const r = e.currentTarget.getBoundingClientRect();
          api.show(content, r.left + Math.min(r.width / 2, 120), r.top);
        },
        onBlur: api.hide,
        onClick: (e) => api.show(content, e.clientX, e.clientY),
      };
    },
    [api],
  );
}

// The element's width, kept up to date as the window or its container resizes (so SVG charts draw at their real size, with crisp text).
export function useWidth(initial = 760) {
  const ref = useRef(null);
  const [w, setW] = useState(initial);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const read = () => setW(Math.max(260, Math.round(el.getBoundingClientRect().width)) || initial);
    read();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [initial]);
  return [ref, w];
}
