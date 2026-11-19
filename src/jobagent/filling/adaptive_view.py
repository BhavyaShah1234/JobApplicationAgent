"""Observe schema/help/choice mutations as well as visible control structure."""
from .live_view import LiveView

SCHEMA_WATCH = r'''() => {
 const state=window.__jobagentSchemaWatch ||= {dirty:false, roots:new WeakSet()};
 if(!state.observer)state.observer=new MutationObserver(records=>{
   for(const r of records){
     const e=r.target.nodeType===1?r.target:r.target.parentElement;
     // Temporary custom-picker menus are collected by discover_options. Native
     // option and question/help text mutations change actual field semantics.
     if(!e||e.closest('[role=listbox],[role=menu],.react-datepicker'))continue;
     state.dirty=true;
   }
 });
 const roots=[document];
 for(let i=0;i<roots.length;i++){
   const root=roots[i];
   if(!state.roots.has(root)){
     state.observer.observe(root,{subtree:true,childList:true,characterData:true,attributes:true,
       attributeFilter:['required','aria-required','aria-label','aria-labelledby','aria-describedby',
         'disabled','hidden','type','pattern','maxlength','min','max','multiple','aria-invalid']});
     state.roots.add(root);
   }
   for(const e of root.querySelectorAll('*')){
     const shadow=e.shadowRoot||window.__jobagentClosedRoots?.get(e);
     if(shadow)roots.push(shadow);
   }
 }
 state.observer.takeRecords();state.dirty=false;
}'''


class AdaptiveView(LiveView):
    """Re-extract only when observed structural or semantic input changes occur."""
    def capture(self):
        """Read the current form, then arm watchers in its frames and shadow roots."""
        view = super().capture()
        for frame in view.frames.values(): frame.evaluate(SCHEMA_WATCH)
        return view

    def changed(self):
        """Report both structural changes and changed question/help/option semantics."""
        if super().changed(): return True
        try:
            return any(frame.evaluate('''()=>{
                const s=window.__jobagentSchemaWatch;
                if(!s)return true;
                if(s.observer.takeRecords().length)s.dirty=true;
                return s.dirty;
            }''') for frame in self.view.frames.values())
        except Exception:
            return True
