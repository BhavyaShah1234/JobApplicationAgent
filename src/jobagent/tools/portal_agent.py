"""Step 11, any portal without a dedicated adapter (iCIMS, Oracle Cloud, a company's own careers site, ...): a small
agent signs in, or registers when the portal has no account for the email.

Each step it lists the page's visible fields and buttons (main page and iframes; hidden fields are never listed, so
never filled), a model picks one action, and code performs it. Values are placeholders the code fills in: {EMAIL},
{PASSWORD} (the shared portal password), {FIRST_NAME}, {LAST_NAME}, {PHONE}, {CODE} (a verification code read from
the inbox). A verification link is opened in the same browser; the used mail is trashed with an audit row. A
challenge (CAPTCHA, bot check) is handed to the user. What worked is kept in the portal's notes for the next visit.
"""

import json
import re
from datetime import UTC, datetime
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from playwright.sync_api import Page
from pydantic import BaseModel, Field

from jobagent import llm
from jobagent.tools import challenge

MAX_STEPS = 30
CAPTCHA_FRAME = re.compile(r"(?i)hcaptcha|recaptcha|google\.com/recaptcha|challenges\.cloudflare|arkoselabs|funcaptcha")

SNAPSHOT_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const label = e => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText.trim(); }
    const l = e.closest('label'); if (l) return l.innerText.trim();
    return (e.getAttribute('aria-label') || e.placeholder || e.name || e.innerText || e.value || '').trim(); };
  const out = [];
  document.querySelectorAll('input, select, textarea, button, a[href], [role=button], [role=checkbox], [role=link]').forEach(e => {
    if (!vis(e) || e.disabled) return;
    const t = (e.getAttribute('type') || '').toLowerCase();
    if (t === 'hidden') return;
    out.push({tag: e.tagName.toLowerCase(), type: t, label: label(e).slice(0, 80),
              checked: e.checked === true, value_set: !!(e.value && t !== 'checkbox' && t !== 'radio' && t !== 'submit')});
  });
  const text = [...document.querySelectorAll('h1, h2, h3, [role=alert], .error, [class*=error i], p')]
      .filter(vis).map(e => e.innerText.trim()).filter(Boolean).join(' | ').slice(0, 900);
  return {items: out.slice(0, 80), text};
}"""

ACTION_JS = r"""([i, kind, value]) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const els = [...document.querySelectorAll('input, select, textarea, button, a[href], [role=button], [role=checkbox], [role=link]')]
      .filter(e => vis(e) && !e.disabled && (e.getAttribute('type') || '').toLowerCase() !== 'hidden');
  const e = els[i]; if (!e) return 'no such element';
  e.setAttribute('data-agent-target', '1');
  return 'ok';
}"""


class AuthAction(BaseModel):
    thought: str = Field(max_length=300, description="What the page shows and why this action")
    action: Literal["fill", "click", "check", "select", "press_enter", "wait", "signed_in", "code_sent",
                    "link_sent", "no_account_needed", "need_human", "failed"]
    element: int | None = Field(None, description="F<frame>:<index> is given as two numbers: use the index in the "
                                                  "frame named by `frame`")
    frame: int = Field(0, description="Frame number of the element (0 = main page)")
    value: str = Field("", max_length=120, description="For fill/select: a placeholder like {EMAIL} or literal text")
    registered: bool = Field(False, description="True once a create-account / sign-up form was submitted")


PROMPT = """You operate a job portal in a browser for the candidate, to get SIGNED IN to their account on this
careers site. Each turn you see the visible fields and buttons (by frame and index) and the page text, and you choose
ONE action. Rules:
- Sign in FIRST with {EMAIL} and {PASSWORD}. Only if the site clearly says there is no account for this email (or the
  sign-in fails as "user not found" / "no account" and the site offers a create-account / sign-up path) REGISTER a new
  account with {EMAIL}, {PASSWORD} (and confirm-password {PASSWORD}), {FIRST_NAME}, {LAST_NAME}, {PHONE} where asked
  ({LEGAL_FIRST_NAME} when the field asks for the legal first name);
  tick only checkboxes that accept terms or privacy needed to continue. Set registered=true when you submit it.
- Never use "forgot / reset password". If {PASSWORD} is rejected for an existing account, answer failed.
- If, while CREATING an account or setting a NEW password, the site rejects {PASSWORD} as too weak, too common or
  not meeting its rules, use {ALT_PASSWORD} in every password and confirm field from then on (it's stored for
  this portal).
- A field already filled (value_set) needs no refill unless it's wrong.
- When the page asks for a verification code sent by email, answer code_sent (the code will be read from the inbox and
  given to you as {CODE}); when it says a confirmation link was emailed, answer link_sent.
- Answer signed_in when the page clearly shows a signed-in candidate (account menu, the email, "sign out", candidate
  home/dashboard/profile) or the application form itself with no sign-in asked. Answer no_account_needed when the
  posting's application form needs no account at all (e.g. Lever, Greenhouse).
- need_human only for something a person must do that is VISIBLE on the page (a phone/SMS check, a puzzle shown
  to you); CAPTCHAs are checked separately, so never answer need_human just because a CAPTCHA script exists.
  failed when stuck.
- Never fill fields that ask for anything other than the candidate's identity and contact (no answers, no resumes)."""


def snapshot(page: Page) -> tuple[str, list]:
    # CAPTCHA providers' frames are left out: invisible ones sit on many sign-in pages; a visible challenge is caught
    # by challenge.detect() and handed to the user before the model is asked anything
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame and f.url.startswith("http")
                                  and not CAPTCHA_FRAME.search(f.url)]
    parts, kept = [], []
    for n, f in enumerate(frames):
        try:
            snap = f.evaluate(SNAPSHOT_JS)
        except Exception:
            continue
        if not snap["items"] and not snap["text"]:
            continue
        kept.append((n, f))
        items = "\n".join(f"  [{i}] {x['tag']}{'/' + x['type'] if x['type'] else ''} \"{x['label']}\""
                          f"{' (checked)' if x['checked'] else ''}{' (filled)' if x['value_set'] else ''}"
                          for i, x in enumerate(snap["items"]))
        parts.append(f"FRAME {n} {f.url[:100]}\nTEXT: {snap['text']}\nELEMENTS:\n{items}")
    return "\n\n".join(parts), dict(kept)


def _target(frames: dict, a: AuthAction):
    f = frames.get(a.frame)
    if f is None or a.element is None:
        return None
    if f.evaluate(ACTION_JS, [a.element, a.action, a.value]) != "ok":
        return None
    return f.locator('[data-agent-target="1"]').last


def run(page: Page, url: str, values: dict, inbox, hitl, host: str, log=print, model: str | None = None,
        notes: list[str] = ()) -> dict:
    """Drive the portal until signed in (or no account needed). Returns {"state", "registered", "verified_mail",
    "steps": [what was done]}."""
    agent = llm.writer(3000, timeout=240, model=model)
    history: list[str] = []
    state = {"state": "failed", "registered": False, "verified_mail": False, "steps": history}
    since = datetime.now(UTC)
    code, challenges, repeats, last_action = "", 0, 0, None
    state["alt_password_used"] = False
    for step in range(MAX_STEPS):
        page.wait_for_timeout(2500)
        if (c := challenge.detect(page)) is not None:
            # A puzzle can close by itself (timeout) without being solved; then the next submit brings a new one.
            # So the user says when it's solved, and a puzzle that keeps coming back stops the run.
            challenges += 1
            if challenges > 2:
                log(f"portal {host}: {c.kind} keeps coming back; stopping")
                state["state"] = "failed"
                history.append(f"{c.kind} challenge came back {challenges} times")
                break
            shot = f"/app/state/logs/challenge-{host}-{challenges}.png"
            from jobagent.tools.browser import screenshot
            screenshot(page, shot)
            log(f"portal {host}: {c.kind} challenge, handing it to the user ({shot})")
            hitl.ask(f"{host}: a {c.kind} puzzle is on the page (screenshot {shot}). Solve it in noVNC "
                     "(http://localhost:6080) and press its submit/verify button, then answer 'done'.")
            history.append(f"the user solved a {c.kind} challenge")
        view, frames = snapshot(page)
        msgs = [SystemMessage(PROMPT),
                HumanMessage(f"PORTAL: {host}\nURL: {page.url[:150]}\n"
                             + (f"NOTES FROM EARLIER VISITS: {list(notes)[-6:]}\n" if notes else "")
                             + (f"A verification code is available as {{CODE}}.\n" if code else "")
                             + "DONE SO FAR:\n" + ("\n".join(history[-12:]) or "(nothing)") + "\n\n" + view)]
        try:
            a = llm.structured_invoke(agent, AuthAction, msgs, first="function_calling", log=log)
        except Exception as e:
            log(f"portal {host}: model error {type(e).__name__}")
            continue
        state["registered"] |= a.registered
        log(f"portal {host} step {step}: {a.action} F{a.frame}[{a.element}] {a.value!r} - {a.thought[:120]}")
        if a.action in ("signed_in", "no_account_needed", "need_human", "failed"):
            if a.action == "need_human":
                hitl.ask(f"{host}: {a.thought} Please handle it in noVNC (http://localhost:6080), then answer 'done'.")
                history.append(f"user handled: {a.thought[:80]}")
                continue
            if a.action == "signed_in" and not signed_in_evidence(page, values):
                history.append("CLAIMED signed in but the page shows no account (no email or name): open the "
                               "profile / account page (it shows the email when signed in) and sign in if it asks")
                log(f"portal {host}: 'signed in' not confirmed by the page; continuing")
                continue
            state["state"] = a.action
            break
        if a.action in ("code_sent", "link_sent"):
            try:
                mail, v = inbox.wait_for_verification(f"https://{host}", since, kind="portal", timeout_s=300)
            except TimeoutError:
                history.append("no verification mail arrived within 5 minutes")
                continue
            if v.link and (a.action == "link_sent" or not v.code):
                page.goto(v.link, wait_until="domcontentloaded")
                history.append("opened the emailed confirmation link")
            else:
                code = v.code or ""
                history.append("the emailed code is now available as {CODE}")
            inbox.consume_and_trash(mail, "portal_verify")
            state["verified_mail"] = True
            log(f"portal {host}: verification mail used and trashed ({mail.subject!r})")
            continue
        key = (a.action, a.frame, a.element, a.value)
        repeats = repeats + 1 if key == last_action else 0
        last_action = key
        if repeats >= 2:  # the same action three times: the page isn't moving; stop and say what it shows
            from jobagent.tools.browser import screenshot
            shot = f"/app/state/logs/portal-stuck-{host}.png"
            screenshot(page, shot)
            log(f"portal {host}: stuck repeating {a.action}; page says: {view.split('ELEMENTS:')[0][-400:]} ({shot})")
            history.append("stuck: the same action three times")
            break
        if "{ALT_PASSWORD}" in a.value:
            state["alt_password_used"] = True
        if a.action == "fill" and history and history[-1].startswith(f"fill {a.value}") and \
                re.search(r"(?i)click|continue|next|submit|proceed", a.thought):
            # the model said "click Continue" but re-sent the same fill: submit the form from that field instead
            a = a.model_copy(update={"action": "press_enter"})
        loc = _target(frames, a)
        if loc is None:
            history.append(f"{a.action} on F{a.frame}[{a.element}] failed: no such element")
            continue
        value = a.value
        for k, v in {**values, "CODE": code}.items():
            value = value.replace("{" + k + "}", v or "")
        try:
            if a.action == "fill":
                loc.fill(value)
            elif a.action == "click":
                loc.click(timeout=10000)
            elif a.action == "check":
                loc.check(timeout=10000)
            elif a.action == "select":
                loc.select_option(label=value) if value else None
            elif a.action == "press_enter":
                loc.press("Enter")
            history.append(f"{a.action} {a.value or ''} on \"{_label(frames, a)}\"".strip())
        except Exception as e:
            history.append(f"{a.action} failed: {type(e).__name__}: {str(e)[:80]}")
        finally:
            try:
                frames[a.frame].evaluate("() => document.querySelectorAll('[data-agent-target]')"
                                         ".forEach(e => e.removeAttribute('data-agent-target'))")
            except Exception:
                pass
    return state


def signed_in_evidence(page: Page, values: dict) -> bool:
    """The page itself shows the account: its email or the candidate's first name."""
    keys = [values.get("EMAIL", ""), values.get("FIRST_NAME", "").split()[0] if values.get("FIRST_NAME") else ""]
    for f in page.frames:
        try:
            text = f.locator("body").inner_text(timeout=3000)
        except Exception:
            continue
        # a "Sign out" link alone isn't proof: amazon.jobs keeps it in its menu for signed-out visitors too
        if any(k and k.lower() in text.lower() for k in keys):
            return True
    return False


def _label(frames: dict, a: AuthAction) -> str:
    try:
        return frames[a.frame].evaluate(SNAPSHOT_JS)["items"][a.element]["label"][:40]
    except Exception:
        return "?"
