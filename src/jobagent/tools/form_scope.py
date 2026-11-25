"""Identify separate search and job-alert forms, which are not application questions."""

FORM_OWNER_JS = r'''element => {
  const prototypes={INPUT:HTMLInputElement.prototype,SELECT:HTMLSelectElement.prototype,
    TEXTAREA:HTMLTextAreaElement.prototype,BUTTON:HTMLButtonElement.prototype,
    FIELDSET:HTMLFieldSetElement.prototype,OUTPUT:HTMLOutputElement.prototype,OBJECT:HTMLObjectElement.prototype};
  const nativeElements=Object.getOwnPropertyDescriptor(HTMLFormElement.prototype,'elements').get;
  const genuine=form=>{try{nativeElements.call(form);return form}catch{return null}};
  const prototype=prototypes[element.tagName];
  if(prototype){
    try{const form=genuine(Object.getOwnPropertyDescriptor(prototype,'form').get.call(element));if(form)return form}catch{}
  }
  let form=genuine(Element.prototype.closest.call(element,'form'));
  for(let host=element.getRootNode?.().host;!form&&host;host=host.getRootNode?.().host)
    form=genuine(Element.prototype.closest.call(host,'form'));
  return form;
}'''

UTILITY_CONTROL_JS = r'''(() => {
  const cache = new WeakMap();
  const formOwner=(__FORM_OWNER__);
  return element => {
    if (element.matches?.('input[type=password],input[type=file]')) return false;
    const form = formOwner(element);
    if (!form) return false;
    if (cache.has(form)) return cache.get(form);
    // A control named "elements" shadows form.elements in generated iForms.
    // The native getter also preserves controls associated via form="id".
    const controls = Object.getOwnPropertyDescriptor(HTMLFormElement.prototype,'elements').get.call(form);
    const nodes = [...new Set([...controls, ...form.querySelectorAll('button,a[role=button],input[type=submit]')])];
    const names = nodes.filter(e => e.matches('button,a[role=button],input[type=submit]'))
      .map(e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim());
    const application = names.some(t => /\b(?:apply|submit (?:your |my )?application|create (?:an? |your )?(?:account|profile))\b/i.test(t)) ||
      !!form.querySelector('input[type=password],input[type=file]');
    const search = form.getAttribute('role') === 'search' || !!form.closest('[role=search]');
    const fields = nodes.filter(e => e.matches('input,select,textarea') &&
      !['hidden','submit','button','reset','image'].includes(e.type));
    const alert = names.some(t => /^create (?:a |an? )?(?:job )?alert$/i.test(t)) && fields.length > 0 &&
      fields.every(e => ['number','email','checkbox','select-one','select-multiple'].includes(e.type) ||
        e.type === 'text' && /e-?mail/i.test([e.name,e.id,e.placeholder,e.getAttribute('aria-label')].join(' ')));
    const utility = !application && (search || alert);
    cache.set(form, utility);
    return utility;
  };
})()'''.replace('__FORM_OWNER__',FORM_OWNER_JS)
