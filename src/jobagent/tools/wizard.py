"""Did Next move the application to a new step? (F16)

A wizard step can keep the same heading and the same address while its fields change, and a page can change its heading
while the same step is still showing. So progress is judged from several signals together, never from headings alone:

1. the ADDRESS (path, query and fragment) - a wizard that routes each step changes it;
2. the active STEP marker the page itself exposes (`aria-current`, the selected tab, a progress bar, "Step 2 of 4");
3. the FIELD SCHEMA - which questions are on the page. A new step replaces most of them; a conditional question that
   appears (or disappears) inside the same step leaves most of them in place;
4. the buttons - only to tell a review page (no questions, Submit) from a page still loading.

Headings are not evidence of movement by themselves: a changed heading over identical fields and buttons is the same step.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

from jobagent.tools import forms

# the share of the questions that were on the page before and are gone now above which the wizard has moved to a new step
GONE_SHARE = 0.5

STEP_JS = """() => {
  const out = [], t = (e) => ((e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ')).slice(0, 60);
  for (const e of document.querySelectorAll('[aria-current]:not([aria-current=false])')) {
    if (['step', 'page', 'true', 'location'].includes(e.getAttribute('aria-current'))) out.push('current:' + t(e));
  }
  for (const e of document.querySelectorAll('[role=tab][aria-selected=true]')) out.push('tab:' + t(e));
  for (const e of document.querySelectorAll('[role=progressbar]')) {
    out.push('progress:' + [e.getAttribute('aria-valuenow'), e.getAttribute('aria-valuemax'), e.getAttribute('aria-valuetext')].join('/'));
  }
  for (const e of document.querySelectorAll('[data-automation-id=progressBarActiveStep], [data-active-step]')) out.push('active:' + t(e));
  const m = (document.body ? document.body.innerText : '').slice(0, 6000).match(/\\b(?:step|page)\\s+(\\d+)\\s*(?:of|\\/)\\s*(\\d+)/i);
  if (m) out.push('count:' + m[1] + '/' + m[2]);
  return out;
}"""


@dataclass(frozen=True)
class PageState:
    url: str
    step: tuple
    fields: frozenset
    buttons: frozenset
    headings: tuple


def url_key(url: str) -> str:
    """The address as a wizard would route it: host, path (no trailing slash), sorted query, fragment."""
    p = urlsplit(url or "")
    return f"{p.netloc.lower()}{p.path.rstrip('/')}?{urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))}#{p.fragment}"


def field_key(f) -> tuple:
    """A question's identity on a page: what it asks and what kind of answer it takes (not its position or current value)."""
    label = re.sub(r"[\s*:]+", " ", (f.label or "").lower()).strip()
    return (f.kind, label, (f.section or "").lower().strip())


def step_marker(page) -> tuple:
    try:
        return tuple(sorted(page.evaluate(STEP_JS)))
    except Exception:          # a page in the middle of navigation: no marker this time
        return ()


def state_of(page, view) -> PageState:
    return PageState(url_key(page.url), step_marker(page), frozenset(field_key(f) for f in view.fields),
                     frozenset(b.lower().strip() for b in view.buttons),
                     tuple(h for h in view.headings if not re.search(r"(?i)^errors? found|^\d+ errors?|^error", h)))


def judge(before: PageState, after: PageState) -> tuple[str, str]:
    """("advanced" | "same" | "loading", why). 'loading': the page has no questions and no buttons yet - ask again."""
    if after.url != before.url:
        return "advanced", "the address changed"
    if after.step != before.step and (after.step or before.step):
        return "advanced", "the page's step marker changed"
    if before.fields and not after.fields:
        return ("advanced", "the questions are gone and a button remains (review)") if after.buttons else ("loading", "no questions or buttons yet")
    if not before.fields and after.fields:
        return "advanced", "questions appeared"
    if before.fields:
        gone = len(before.fields - after.fields) / len(before.fields)
        if gone >= GONE_SHARE:
            return "advanced", f"{gone:.0%} of the earlier questions were replaced"
        if after.fields != before.fields:
            return "same", "questions were added or removed inside the same step"
        return "same", "the same questions are still showing"
    if after.buttons != before.buttons:                      # neither side has questions (a review or confirmation page)
        return "advanced", "the buttons changed"
    return "same", "nothing changed"


def wait_for_progress(page, before: PageState, *, attempts: int = 10, wait_ms: int = 3000, settle_ms: int = 1500):
    """After clicking Next: wait for the next step to load (Workday shows a placeholder first). Returns
    (verdict, view, state, why); `verdict` is "advanced" or "same" (the site refused, or answered with an error banner)."""
    view, state, verdict, why = None, before, "same", "nothing changed"
    for _ in range(attempts):
        page.wait_for_timeout(wait_ms)
        view = forms.read_page(page)
        state = state_of(page, view)
        verdict, why = judge(before, state)
        if view.errors or (verdict == "advanced" and (view.fields or any(forms.NEXT.search(b) or forms.SUBMIT.search(b) for b in view.buttons))):
            break
    page.wait_for_timeout(settle_ms)
    view = forms.read_page(page)
    state = state_of(page, view)
    verdict, why = judge(before, state)
    if verdict == "loading":
        verdict = "same"
    return verdict, view, state, why
