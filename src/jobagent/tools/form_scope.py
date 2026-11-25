"""Identify separate search and job-alert forms, which are not application questions."""

UTILITY_CONTROL_JS = r'''(() => {
  const cache = new WeakMap();
  return element => {
    if (element.matches?.('input[type=password],input[type=file]')) return false;
    let form = element.form || element.closest?.('form');
    for (let host = element.getRootNode?.().host; !form && host; host = host.getRootNode?.().host)
      form = host.closest('form');
    if (!form) return false;
    if (cache.has(form)) return cache.get(form);
    const nodes = [...new Set([...form.elements, ...form.querySelectorAll('button,a[role=button],input[type=submit]')])];
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
})()'''
