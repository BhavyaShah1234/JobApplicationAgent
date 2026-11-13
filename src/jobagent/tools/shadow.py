"""Author shadow DOM access, including already-created closed roots, through Chrome's DOM protocol.

Roots stay closed to the application. We keep an agent-only weak registry, recover existing roots through CDP, and use a
public Playwright selector engine for interactions. No shadow-mode override, fingerprint masking or challenge handling.
"""
SCRIPT = r"""() => {
  if (window.__jobagentShadowInstalled) return;
  window.__jobagentShadowInstalled = true;
  window.__jobagentClosedRoots ||= new WeakMap();
  window.__jobagentRoot = h => h.shadowRoot || window.__jobagentClosedRoots.get(h);
  window.__jobagentAll = (selector, root=document) => {
    const found=[], seen=new Set();
    const walk=r=>{ for(const e of r.querySelectorAll(selector)) if(!seen.has(e)){seen.add(e);found.push(e);}
      for(const h of r.querySelectorAll('*')) { const s=window.__jobagentRoot(h); if(s)walk(s); } };
    walk(root); return found;
  };
  const original=Element.prototype.attachShadow;
  Element.prototype.attachShadow=function(options){const root=original.call(this,options);
    window.__jobagentClosedRoots.set(this,root);return root;};
}"""

ENGINE = r"""() => ({
  queryAll(root, selector) {
    const w=(root.ownerDocument||root).defaultView||window;
    const visible=selector.includes(':visible'); selector=selector.replaceAll(':visible','');
    const result=[],seen=new Set();
    const walk=r=>{for(const e of r.querySelectorAll(selector)) if(!seen.has(e)){
      seen.add(e); const b=e.getBoundingClientRect(),s=w.getComputedStyle(e);
      if(!visible||(b.width>0&&b.height>0&&s.visibility!=='hidden'&&s.display!=='none'))result.push(e);}
      for(const h of r.querySelectorAll('*')){const s=h.shadowRoot||w.__jobagentClosedRoots?.get(h);if(s)walk(s);}};
    walk(root);return result;
  },
  query(root, selector){return this.queryAll(root,selector)[0]||null;}
})"""


def install(playwright, context):
    """Register on the public Playwright instance before using this context."""
    if not hasattr(playwright, "selectors") or not hasattr(context, "add_init_script"):
        return  # lightweight command-boundary doubles; actual browser contexts always expose these APIs
    if not getattr(playwright, "jobagent_shadow_registered", False):
        playwright.selectors.register("jobagent", script="(" + ENGINE + ")()")
        playwright.jobagent_shadow_registered = True
    context.add_init_script("(" + SCRIPT + ")()")
    context.jobagent_shadow_selectors = True


def prepare(frame):
    """Recover existing author closed roots without opening them to the site's own DOM queries."""
    frame.evaluate(SCRIPT)
    if frame.evaluate("() => !!window.__jobagentShadowRecovered"):
        return
    context = frame.page.context
    try:
        session = context.new_cdp_session(frame)
    except Exception:
        session = context.new_cdp_session(frame.page)
    try:
        doc = session.send("DOM.getDocument", {"depth": -1, "pierce": True})
        def walk(node):
            if node.get("shadowRootType") == "closed" and node.get("backendNodeId"):
                remote = session.send("DOM.resolveNode", {"backendNodeId": node["backendNodeId"]})["object"]
                obj = remote.get("objectId")
                if obj:
                    try:
                        session.send("Runtime.callFunctionOn", {"objectId": obj, "functionDeclaration":
                            "function(){const w=this.host.ownerDocument.defaultView;"
                            "(w.__jobagentClosedRoots ||= new WeakMap()).set(this.host,this);}", "returnByValue": True})
                    finally:
                        session.send("Runtime.releaseObject", {"objectId": obj})
            for key in ("children", "shadowRoots", "pseudoElements"):
                for child in node.get(key, []):
                    walk(child)
            if node.get("contentDocument"):
                walk(node["contentDocument"])
        walk(doc["root"])
        frame.evaluate("() => {window.__jobagentShadowRecovered=true}")
    finally:
        session.detach()


def locator(frame, selector):
    if getattr(getattr(getattr(frame, "page", None), "context", None), "jobagent_shadow_selectors", False):
        return frame.locator("jobagent=" + selector)
    return frame.locator(selector)


def field_element(frame, selector):
    """Normal Locator in production; public ElementHandle fallback for independently-created test contexts."""
    loc = locator(frame, selector)
    if loc.count():
        return loc.first
    handle = frame.evaluate_handle("s => window.__jobagentAll?.(s)[0] || null", selector)
    el = handle.as_element()
    if el is None:
        handle.dispose()
        return loc.first
    return el
