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
from urllib.parse import urlparse
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
  const text = [...document.querySelectorAll('h1, h2, h3, [role=alert], [aria-live], .error, [class*=error i], ' +
      '[id*=error i], [data-testid*=error i], [class*=hint i], [class*=helper i], [id*=hint i], p')]
      .filter(vis).map(e => e.innerText.trim()).filter(Boolean).join(' | ').slice(0, 900);
  const pw = [...document.querySelectorAll('input[type=password]')].filter(vis).length;
  return {items: out.slice(0, 80), text, pw};
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
    action: Literal["fill", "click", "check", "select", "press_enter", "wait", "signed_in", "signed_out", "code_sent",
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
- Never use "forgot / reset password". If {PASSWORD} is rejected ("wrong username or password") and the site offers
  "Continue / Sign in with Google", click that instead: the browser is signed in to Google as {EMAIL}, and an account
  made with Google has no password. Otherwise, if {PASSWORD} is rejected for an existing account, answer failed.
- If, while CREATING an account or setting a NEW password, the site rejects {PASSWORD} as too weak, too common or
  not meeting its rules, use {ALT_PASSWORD} in every password and confirm field from then on (it's stored for
  this portal).
- A field already filled (value_set) needs no refill unless it's wrong.
- When the page asks for a verification code sent by email, answer code_sent (the code will be read from the inbox and
  given to you as {CODE}; for a code split over one-character boxes, fill {CODE} into the FIRST box only);
  when it says a confirmation link was emailed, answer link_sent.
- Answer signed_in when the page clearly shows a signed-in candidate (account menu, the email, "sign out", candidate
  home/dashboard/profile) or the application form itself with no sign-in asked. Answer no_account_needed when the
  posting's application form needs no account at all (e.g. Lever, Greenhouse).
- need_human only for something a person must do that is VISIBLE on the page (a phone/SMS check, a puzzle shown
  to you); CAPTCHAs are checked separately, so never answer need_human just because a CAPTCHA script exists.
  failed when stuck.
- Never fill fields that ask for anything other than the candidate's identity and contact (no answers, no resumes)."""


SIGN_OUT_PROMPT = """You operate a job portal in a browser for the candidate, to SIGN THEM OUT of their account on
this careers site. Each turn you see the visible fields and buttons (by frame and index) and the page text; choose ONE
action. Open the account / profile / "My career" menu if needed, then click "Sign out" / "Log out". Answer
signed_out when the page shows a sign-in link and no account; failed when stuck. Never fill anything."""


RESET_PROMPT = """You operate a job portal in a browser for the candidate. The user approved ONE password reset for this
portal's existing account (its password is unknown). Each turn you see the visible fields and buttons (by frame and
index) and the page text; choose ONE action:
- Enter {EMAIL} where asked and follow "Forgot / Reset your password".
- When the page says a reset link or code was emailed, answer link_sent or code_sent (it's read from the inbox).
- On the set-new-password form, fill {PASSWORD} into the new and confirm fields and submit (if the site refuses it
  as too weak or common, use {ALT_PASSWORD} in both).
- Then sign in with {EMAIL} and the new password, and answer signed_in when the page shows the account.
Never change anything else (profile, email, security settings); failed when stuck."""


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
        if snap.get("pw", 0) >= 2:
            parts.append("NEW_PASSWORD_FORM")  # marker for the code: a set/confirm-password form is showing
    return "\n\n".join(parts), dict(kept)


def _target(frames: dict, a: AuthAction):
    f = frames.get(a.frame)
    if f is None or a.element is None:
        return None
    if f.evaluate(ACTION_JS, [a.element, a.action, a.value]) != "ok":
        return None
    return f.locator('[data-agent-target="1"]').last


def run(page: Page, url: str, values: dict, inbox, hitl, host: str, log=print, model: str | None = None,
        notes: list[str] = (), goal: str = "sign_in") -> dict:
    """Drive the portal until signed in (or no account needed). Returns {"state", "registered", "verified_mail",
    "steps": [what was done]}."""
    agent = llm.writer(3000, timeout=240, model=model)
    history: list[str] = []
    state = {"state": "failed", "registered": False, "verified_mail": False, "steps": history}
    since = datetime.now(UTC)
    code, challenges, repeats, last_action, pw_submitted, wrong_pw = "", 0, 0, None, False, 0
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
        if urlparse(page.url).netloc == "accounts.google.com":  # Google's own pages: handled by code, not the model
            note = _google_step(page, values.get("EMAIL", ""))
            history.append(note)
            log(f"portal {host}: google sign-in: {note}")
            if note.startswith("blocked"):
                state["state"] = "failed"
                break
            continue
        view, frames = snapshot(page)
        if re.search(r"(?i)wrong (username|email|e-mail) or password|incorrect (username|email|password)|"
                     r"invalid (username|email|credentials|password)", view.split("ELEMENTS:")[0]):
            wrong_pw += 1
            if wrong_pw >= 2 and "PASSWORD REJECTED" not in " ".join(history[-2:]):
                history.append("PASSWORD REJECTED twice: never type a password again here (lockout risk). If the "
                               "site offers Continue with Google use it; if not visible, go back to the first "
                               "sign-in page (an Edit / Back link) where it is shown; otherwise answer failed")
                log(f"portal {host}: the password was rejected twice; no more password attempts")
        if "NEW_PASSWORD_FORM" in view and pw_submitted and not state["alt_password_used"]:
            # the same set-password form came back after {PASSWORD} was submitted: the site refused it
            history.append("the site did NOT accept {PASSWORD} (the new-password form came back): fill "
                           "{ALT_PASSWORD} into BOTH password fields and submit")
            log(f"portal {host}: the shared password was refused; switching to this portal's own password")
            pw_submitted = False
        # submitted = {PASSWORD} was typed and then a button was clicked / Enter pressed on the set-password form
        recent = history[-4:]
        typed = [i for i, h in enumerate(recent) if "{PASSWORD}" in h and h.startswith("fill")]
        if "NEW_PASSWORD_FORM" in view and typed and not state["alt_password_used"] and \
                any(h.startswith(("click", "press_enter")) for h in recent[typed[-1] + 1:]):
            pw_submitted = True
        msgs = [SystemMessage({"sign_in": PROMPT, "sign_out": SIGN_OUT_PROMPT, "reset_password": RESET_PROMPT}[goal]),
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
        if a.action == "signed_out":
            # judged on the page the site lands on: visiting its profile page again can sign a remembered browser
            # straight back in (amazon.jobs' sign-in service does)
            if goal == "sign_out" and not _shows_account(page, values):
                state["state"] = "signed_out"
                break
            history.append("CLAIMED signed out but the account still shows: find the Sign out / Log out control")
            continue
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
            page_text = view.split("ELEMENTS:")[0][-500:]
            log(f"portal {host}: page before waiting for the mail: {' '.join(page_text.split())[:400]}")
            if re.search(r'(?i)"(send|get|email|resend)( me)?( a| the)?( verification)? (code|otp)"|"send code"|'
                         r'"send verification', view) and not any("send-code button" in h for h in history[-3:]):
                history.append("the page has a send-code button: click it so the code is actually sent, then "
                               "answer code_sent")
                log(f"portal {host}: a send-code button is on the page; clicking it first")
                continue
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
        value = re.sub(r"\{CODE\[(\d+)\]\}", lambda m: code[int(m.group(1))] if int(m.group(1)) < len(code) else "",
                       a.value)  # one digit of a code split over several boxes (0-based)
        for k, v in {**values, "CODE": code}.items():
            value = value.replace("{" + k + "}", v or "")
        if a.action == "fill" and "PASSWORD}" in a.value and wrong_pw >= 2 and goal == "sign_in":
            history.append("refused: the password was already rejected twice (lockout risk)")
            continue
        if a.action == "fill" and not a.value and (loc.input_value() if loc.evaluate("e => 'value' in e") else ""):
            history.append(f"refused: an empty fill would clear \"{_label(frames, a)}\"; click the button instead")
            continue
        if a.action == "fill" and "PASSWORD}" in a.value and (loc.get_attribute("type") or "").lower() != "password":
            history.append(f"refused: {a.value} may only go into a password field, not \"{_label(frames, a)}\"; "
                           "submit the email first if no password field is shown")
            continue
        try:
            if a.action == "fill" and code and value == code and loc.get_attribute("maxlength") == "1":
                # a code split over one-character boxes: type it; such boxes move to the next box by themselves
                loc.click()
                page.keyboard.type(code, delay=120)
            elif a.action == "fill":
                loc.fill(value)
            elif a.action == "click":
                label = _label(frames, a)
                loc.click(timeout=10000)
                if goal == "sign_out" and re.search(r"(?i)\b(sign|log)\s?out\b", label):
                    page.wait_for_timeout(4000)
                    if not _shows_account(page, values):  # the site took the sign-out; its landing page is clear
                        history.append(f"clicked \"{label}\"; the account no longer shows")
                        state["state"] = "signed_out"
                        break
            elif a.action == "check":
                loc.check(timeout=10000)
            elif a.action == "select":
                loc.select_option(label=value) if value else None
            elif a.action == "press_enter":
                loc.press("Enter")
            history.append(f"{a.action} {a.value or ''} on \"{_label(frames, a)}\"".strip())
            if a.action == "fill" and code and "{CODE" in a.value:
                # a code is entered: submit it (models tend to re-type it instead of pressing Verify)
                btn = frames[a.frame].get_by_role("button", name=re.compile(
                    r"^\s*(verify|submit|confirm|continue|next|sign in)\b", re.I)).filter(visible=True)
                if btn.count():
                    btn.first.click()
                    history.append("submitted the code (clicked the verify button)")
                else:
                    page.keyboard.press("Enter")
                    history.append("submitted the code (Enter)")
        except Exception as e:
            history.append(f"{a.action} failed: {type(e).__name__}: {str(e)[:80]}")
        finally:
            try:
                frames[a.frame].evaluate("() => document.querySelectorAll('[data-agent-target]')"
                                         ".forEach(e => e.removeAttribute('data-agent-target'))")
            except Exception:
                pass
    return state


def _google_step(page: Page, email: str) -> str:
    """One step on accounts.google.com for 'Continue with Google': pick the agent's account, confirm consent."""
    from jobagent.tools.google_auth import BLOCKED
    body = page.locator("body").inner_text(timeout=10000)
    if BLOCKED.search(body):
        return "blocked: Google refused this browser"
    acc = page.get_by_text(email, exact=False)
    if acc.count() and re.search(r"(?i)choose an account|sign in with google|to continue to", body):
        acc.first.click()
        page.wait_for_timeout(3000)
        return f"chose {email} in Google's account chooser"
    btn = page.get_by_role("button", name=re.compile(r"^(continue|allow)$", re.I))
    if btn.count():
        btn.first.click()
        page.wait_for_timeout(3000)
        return "confirmed Google's consent screen"
    page.wait_for_timeout(3000)
    return "waited on a Google page"


def signed_in_evidence(page: Page, values: dict) -> bool:
    """The account is shown: its email or the candidate's first name on this page, or else on the site's own
    profile / account page (many portals show neither on their home or menu pages, e.g. amazon.jobs)."""
    if _shows_account(page, values):
        return True
    hrefs = page.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a => a.href)
        .filter(h => /profile|user\\/details|my-?account|account-?info|candidate-?home|dashboard/i.test(h)
                     && !/logout|signout|sign-out|help|faq|privacy/i.test(h)).slice(0, 3)""")
    for href in hrefs:
        try:
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
        except Exception:
            continue
        if _shows_account(page, values):
            return True
    return False


def _shows_account(page: Page, values: dict) -> bool:
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
