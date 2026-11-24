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

from jobagent import db, llm
from jobagent.tools import verification
from jobagent.tools import challenge

MAX_STEPS = 30
MAX_UNCHANGED_WAITS = 4
CAPTCHA_FRAME = re.compile(r"(?i)hcaptcha|recaptcha|google\.com/recaptcha|challenges\.cloudflare|arkoselabs|funcaptcha")

SNAPSHOT_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const label = e => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText.trim(); }
    const l = e.closest('label'); if (l) return l.innerText.trim();
    return (e.getAttribute('aria-label') || e.title || e.placeholder || e.name || e.innerText || e.value ||
            e.querySelector('img[alt]')?.alt || e.id || '').trim(); };
  const out = [];
  document.querySelectorAll('input, select, textarea, button, a[href], [role=button], [role=checkbox], [role=link]').forEach(e => {
    if (!vis(e) || e.disabled) return;
    const t = (e.getAttribute('type') || '').toLowerCase();
    if (t === 'hidden') return;
    const r = e.getBoundingClientRect();
    const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    const blocked = top && !e.contains(top) ? (label(top) || top.id || top.tagName).slice(0, 100) : '';
    out.push({tag: e.tagName.toLowerCase(), type: t, label: label(e).slice(0, 80), blocked_by: blocked,
              checked: e.checked === true, value_set: !!(e.value && t !== 'checkbox' && t !== 'radio' && t !== 'submit'),
              options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()).filter(Boolean).slice(0, 40) : null,
              chosen: e.tagName === 'SELECT' && e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : null});
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
- A field/button marked BLOCKED_BY is covered by another element. For a blocking COOKIE NOTICE, prefer its
  Close button (including an icon labelled by its title, image or ID), or Reject optional cookies. Do not repeatedly
  click a covered control or accept optional tracking/marketing cookies. A required account terms/privacy dialog
  is different: read its text and use its Acknowledge/Accept control when needed to create the account; closing
  and reopening it does not acknowledge it. Never apply this rule to an optional marketing/sharing consent.
- Sign in FIRST with {EMAIL} and {PASSWORD}. Only if the site clearly says there is no account for this email (or the
  sign-in fails as "user not found" / "no account" and the site offers a create-account / sign-up path) REGISTER a new
  account with {EMAIL}, {PASSWORD} (and confirm-password {PASSWORD}), {FIRST_NAME}, {LAST_NAME}, {PHONE} where asked
  ({LEGAL_FIRST_NAME} when the field asks for the legal first name);
  tick only checkboxes that accept terms or privacy needed to continue. Set registered=true when you submit it.
- Some portals (UKG / UltiPro) answer "Invalid credentials" both for a wrong password and for an email that has no account. When that
  is ALL the page says and it offers "Sign up" / "Create an account", use that route once (register with {EMAIL}, {PASSWORD}, names,
  phone); if the site then says the account already exists, answer failed. Never use "Continue with Google" on ultipro.com or icims.com.
- Never use "forgot / reset password". If {PASSWORD} is rejected ("wrong username or password") and the site offers
  "Continue / Sign in with Google", click that instead: the browser is signed in to Google as {EMAIL}, and an account
  made with Google has no password. Otherwise, if {PASSWORD} is rejected for an existing account, answer failed.
- If, while CREATING an account or setting a NEW password, the site rejects {PASSWORD} as too weak, too common or
  not meeting its rules, use {ALT_PASSWORD} in every password and confirm field from then on (it's stored for
  this portal).
- A field already filled (value_set) needs no refill unless it's wrong.
- A radio marked (checked) is already selected; do not click or check it again to clear an error.
  A registration validation banner can stay visible until Create Account is retried. After the required choice is
  selected, complete any other missing required fields and retry Create Account instead of reselecting that radio.
- When the page asks for a verification code sent by email, answer code_sent (the code will be read from the inbox and
  given to you as {CODE}; for a code split over one-character boxes, fill {CODE} into the FIRST box only);
  when it says a confirmation link was emailed, answer link_sent.
- Answer signed_in when the page clearly shows a signed-in candidate (account menu, the email, "sign out", candidate
  home/dashboard/profile) or the application form itself with no sign-in asked. Answer no_account_needed when the
  posting's application form needs no account at all (e.g. Lever, Greenhouse).
- need_human only for something a person must do that is VISIBLE on the page (a phone/SMS check, a puzzle shown
  to you); CAPTCHAs are checked separately, so never answer need_human just because a CAPTCHA script exists.
  failed when stuck.
- Registration forms that REQUIRE more (SuccessFactors asks for citizenship, address and country of residence) may use only these placeholders,
  and only for a field that is required and empty: {CITIZENSHIP}, {ADDRESS_LINE1}, {CITY}, {STATE}, {POSTAL_CODE}, {COUNTY},
  {COUNTRY_OF_RESIDENCE}. Never type your own value for them. Never fill fields that ask for anything else beyond the candidate's identity
  and contact (no answers, no resumes)."""


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
                          + (f" BLOCKED_BY={x['blocked_by']!r}" if x.get('blocked_by') else "")
                          + (f" CHOICES={x['options']} (use action select with one of them; now: {x['chosen']!r})" if x.get("options") else "")
                          for i, x in enumerate(snap["items"]))
        parts.append(f"FRAME {n} {f.url[:100]}\nTEXT: {snap['text']}\nELEMENTS:\n{items}")
        if snap.get("pw", 0) >= 2:
            parts.append("NEW_PASSWORD_FORM")  # marker for the code: a set/confirm-password form is showing
    return "\n\n".join(parts), dict(kept)


REJECTED = re.compile(r"(?i)wrong (username|email|e-mail) or password|incorrect (username|email|password)|"
                      r"invalid (username|email|credentials|password)|invalid email address or password")
ACCOUNT_EXISTS = re.compile(r"(?i)already (exists?|registered|in use|have an account|been taken)|account (with this email )?exists|"
                            r"email (address )?(is )?(already )?(taken|in use|registered)")
NAV_REGISTER = re.compile(r"(?i)create (an? )?(new )?account|new (user|account)|sign\s?up|register")
NAV_LOGIN = re.compile(r"(?i)^(sign|log)\s?in$|already (have|registered)|existing (user|account)|back to (sign|log)\s?in")
MAX_TOGGLES = 4


def is_registration(view: str) -> bool:
    """Is a NEW account (or a new password) being set up on this page, as opposed to signing in to an existing one? A set/confirm
    password pair, a 'confirm password' field, or name fields beside a password field. A sign-in page that merely LINKS to
    'Create account' is not one (F27)."""
    elements = view.split("ELEMENTS:", 1)[-1]
    return "NEW_PASSWORD_FORM" in view or bool(re.search(r"(?i)(confirm|re-?enter|retype|repeat)\W+(new\W+)?password", elements)) or (
        bool(re.search(r"(?i)\b(first|last|given|family)\s*name\b", elements)) and bool(re.search(r"(?i)password", elements)))


def _settle_unknown(page: Page, seconds: int = 20):
    """An 'unknown' challenge is a page that is blank, loading or mid-navigation, not a puzzle: wait for it to become an ordinary page
    (or a real challenge) before anyone is asked for help."""
    c = challenge.detect(page)
    for _ in range(seconds):
        if c is None or c.kind != "unknown":
            return c
        page.wait_for_timeout(1000)
        c = challenge.detect(page)
    return c


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
    "steps": [what was done]}. Requests that send data (POST and the like) are watched so that a rejected login is counted only
    against a real credential submission."""
    posts: list[dict] = []
    secrets = [s for s in (values.get("PASSWORD"), values.get("ALT_PASSWORD")) if s]

    def listener(r) -> None:
        """Remember data-sending requests and ONLY whether each carried the password (never the password or the body)."""
        if r.method in ("GET", "HEAD", "OPTIONS"):
            return
        try:
            body = r.post_data or ""
        except Exception:
            body = ""
        from urllib.parse import quote, quote_plus
        carries = any(s in body or quote(s, safe="") in body or quote_plus(s) in body for s in secrets)
        posts.append({"credentials": carries})
    page.on("request", listener)
    try:
        return _run(page, url, values, inbox, hitl, host, log, model, notes, goal, posts)
    finally:
        page.remove_listener("request", listener)


SUBMIT_LABEL = re.compile(r"(?i)\b(sign\s?in|log\s?in|login|submit|continue|next|verify|proceed|go)\b")
NOT_A_SUBMIT = re.compile(r"(?i)\b(show|hide|reveal|toggle|eye|visible|view|forgot|reset|create|register|sign\s?up|cancel|back|help)\b")


def _run(page: Page, url: str, values: dict, inbox, hitl, host: str, log, model, notes, goal, posts: list) -> dict:
    agent = llm.writer(3000, timeout=240, model=model)
    history: list[str] = []
    state = {"state": "failed", "registered": False, "verified_mail": False, "steps": history}
    code_mail, accepted_mail = None, None      # the candidate whose code {CODE} holds / the one the site was seen to accept
    since = datetime.now(UTC)
    code, challenges, repeats, last_action, pw_submitted, wrong_pw = "", 0, 0, None, False, 0
    state["alt_password_used"] = False
    _extras = None           # the registration profile, loaded the first time a form asks for it
    came_from = url          # the last portal page before any Google page
    login_candidate, register_attempted, toggles, last_nav = None, False, 0, None
    unconfirmed_claims: dict[str, int] = {}
    unchanged_waits,last_wait_view=0,None
    for step in range(MAX_STEPS):
        page.wait_for_timeout(2500)
        from jobagent.tools.notices import dismiss_cookie_overlay
        if dismiss_cookie_overlay(page):
            history.append('dismissed the blocking cookie notice through its native control')
        if (c := _settle_unknown(page)) is not None:
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
        if urlparse(page.url).netloc == "accounts.google.com" and (why := social_login_refusal(came_from, "Continue with Google")):
            # a click whose label gave nothing away still landed on Google, from a portal where that is a dead end: go back
            history.append(why)
            log(f"portal {host}: {why}")
            try:
                page.go_back(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                try:
                    page.goto(url, wait_until="domcontentloaded")          # the stored login link is single-use: start from the posting
                except Exception as exc:
                    state["state"], state["reason"] = "failed", f"could not return from Google ({type(exc).__name__})"
                    break
            continue
        if urlparse(page.url).netloc != "accounts.google.com":
            came_from = page.url
        if urlparse(page.url).netloc == "accounts.google.com":  # Google's own pages: handled by code, not the model
            note = _google_step(page, values.get("EMAIL", ""))
            history.append(note)
            log(f"portal {host}: google sign-in: {note}")
            if note.startswith("blocked"):
                state["state"] = "failed"
                break
            continue
        view, frames = snapshot(page)
        if unchanged_waits>=MAX_UNCHANGED_WAITS and view==last_wait_view:
            state['reason']=f'The portal remained unchanged after {MAX_UNCHANGED_WAITS} loading waits; sign-in is unconfirmed.'
            history.append(state['reason'])
            from jobagent.tools.browser import screenshot
            screenshot(page,f'/app/state/logs/portal-stuck-{host}.png')
            log(f"portal {host}: {state['reason']}")
            break
        shown_text = view.split("ELEMENTS:")[0]
        if login_candidate is not None:
            cand, login_candidate = login_candidate, None
            # a request is evidence of a credential submission only if IT CARRIED THE CREDENTIALS: analytics, autosave and other page
            # traffic proves nothing about authentication, whatever its response
            request_seen = any(q["credentials"] for q in posts[cand["posts"]:])
            # ONE rejection per genuine credential submission: a credential-bearing request, or (when the site hides its traffic, e.g.
            # hashing in the page) a submit action made while no error was showing; an error already on the page counts again only
            # when a credential-bearing request proves a new attempt reached the server
            genuine = request_seen or (cand["submit_like"] and not cand["banner_before"])
        else:
            genuine = False
        if REJECTED.search(shown_text) and genuine:
            wrong_pw += 1
            if (hint := signup_link_hint(frames)):
                history.append(hint)
            if wrong_pw >= 2 and "PASSWORD REJECTED" not in " ".join(history[-2:]):
                history.append("PASSWORD REJECTED twice: never type a password again here (lockout risk). If the "
                               "site has a create-account, sign-up or forgot-password route use that; do NOT use social logins "
                               "(Continue with Google, Microsoft, LinkedIn, Indeed, Corporate Login): they only work for an account "
                               "already linked to them; otherwise answer failed")
                log(f"portal {host}: the password was rejected twice; no more password attempts")
        if register_attempted and ACCOUNT_EXISTS.search(shown_text) and wrong_pw >= 1:
            state["state"] = "failed"
            state["reason"] = (f"an account for {values.get('EMAIL', 'this email')} already exists but the shared password was rejected, "
                               "so its credentials are unknown: the candidate must sign in or reset it (not done automatically)")
            history.append(state["reason"])
            log(f"portal {host}: {state['reason']}")
            break
        if ("NEW_PASSWORD_FORM" in view and pw_submitted and not state["alt_password_used"]
                and re.search(r"(?i)password.{0,80}(?:too weak|too common|does not meet|doesn't meet|"
                              r"not meet|not strong|rejected)|(?:weak|invalid|unacceptable) password", shown_text)):
            # Other required-field errors can keep this form open; that does not reject its password.
            history.append("the site reports a password-strength problem with {PASSWORD}: fill "
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
        if a.action!='wait':unchanged_waits,last_wait_view=0,None
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
                unconfirmed_claims[view] = unconfirmed_claims.get(view, 0) + 1
                if unconfirmed_claims[view] >= 3:
                    state['reason'] = 'The same page failed account-identity verification three times; account sign-in is unconfirmed.'
                    history.append(state['reason'])
                    log(f"portal {host}: {state['reason']}")
                    break
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
                if verification.outcome(page, None, settle_s=6, email=values.get("EMAIL", "")) == "accepted":
                    accepted_mail = mail        # this very link, seen to work
            else:
                code = v.code or ""
                code_mail = mail                # selected, not used: a newer candidate supersedes it
                history.append("the emailed code is now available as {CODE}")
            log(f"portal {host}: verification mail found ({mail.subject!r}); it is trashed only if the site accepts it")
            continue
        if a.action == "wait":                  # the page is loading or answering: look again (bounded by MAX_STEPS)
            unchanged_waits=unchanged_waits+1 if view==last_wait_view else 1
            last_wait_view=view
            history.append("waited for the page")
            page.wait_for_timeout(1500)
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
        if a.action in ("click", "press_enter") and (why := social_login_refusal(page.url, _label(frames, a))):
            history.append(why)
            log(f"portal {host}: {why}")
            continue
        if a.action == "fill" and (why := literal_fill_refusal(a.value, _label(frames, a))):
            history.append(why)
            log(f"portal {host}: {why}")
            continue
        value = re.sub(r"\{CODE\[(\d+)\]\}", lambda m: code[int(m.group(1))] if int(m.group(1)) < len(code) else "",
                       a.value)  # one digit of a code split over several boxes (0-based)
        if any("{" + k + "}" in a.value for k in REGISTRATION_KEYS):
            if _extras is None:
                _extras = registration_extras(log)
            missing = [k for k in REGISTRATION_KEYS if "{" + k + "}" in a.value and k not in _extras]
            if missing:
                history.append(f"refused: no value is available for {missing}; answer need_human if the form requires it")
                continue
            values = {**values, **_extras}
        for k, v in {**values, "CODE": code}.items():
            value = value.replace("{" + k + "}", v or "")
        if a.action == "fill" and "PASSWORD}" in a.value and wrong_pw >= 2 and goal == "sign_in" and not is_registration(view):
            # the ban protects LOGIN attempts against an existing account; setting a new password on a registration form is not one
            history.append("refused: the password was already rejected twice on sign-in (lockout risk); on a create-account form "
                           "it may still be used to set a NEW password")
            continue
        if a.action == "fill" and loc.evaluate("e => e.tagName") == "SELECT":
            # a native list is chosen from, not typed into (e.g. a phone country code): pick the matching option
            from jobagent.tools import forms
            options = loc.evaluate("e => [...e.options].map(o => o.text.trim()).filter(Boolean)")
            opt, _ = forms.match_option(value, options)
            if opt is None:
                history.append(f"refused: \"{_label(frames, a)}\" is a list; use action select with one of {options[:20]}")
                continue
            a = a.model_copy(update={"action": "select", "value": opt})
            value = opt
        if a.action == "fill" and not a.value and (loc.input_value() if loc.evaluate("e => 'value' in e") else ""):
            history.append(f"refused: an empty fill would clear \"{_label(frames, a)}\"; click the button instead")
            continue
        if a.action == "fill" and "PASSWORD}" in a.value and (loc.get_attribute("type") or "").lower() != "password":
            history.append(f"refused: {a.value} may only go into a password field, not \"{_label(frames, a)}\"; "
                           "submit the email first if no password field is shown")
            continue
        if a.action == "click":
            nav_label = _label(frames, a)
            if goal != "sign_out" and re.search(r"(?i)\b(?:sign|log)[\s-]*out\b", nav_label):
                history.append("refused: signing out is outside this task; verify the existing account without ending its session")
                continue
            pw_just_typed = any("{PASSWORD}" in h for h in history[-3:] if h.startswith("fill"))
            nav = "register" if NAV_REGISTER.search(nav_label) else "login" if NAV_LOGIN.search(nav_label.strip()) else None
            if nav and not pw_just_typed:
                toggles += nav != last_nav and last_nav is not None
                last_nav = nav
                if toggles > MAX_TOGGLES:
                    state["state"] = "failed"
                    state["reason"] = ("the portal keeps alternating between its sign-in and create-account forms without getting "
                                       "anywhere: the account's status is unclear, so the candidate needs to look at it")
                    history.append(state["reason"])
                    log(f"portal {host}: {state['reason']}")
                    break
        if a.action in ("click", "press_enter") and any("{PASSWORD}" in h for h in history[-3:] if h.startswith("fill")):
            if is_registration(view):
                register_attempted = True
            else:
                label_now = _label(frames, a) if a.action == "click" else ""
                login_candidate = {"posts": len(posts), "banner_before": bool(REJECTED.search(shown_text)),
                                   "submit_like": a.action == "press_enter" or (
                                       bool(SUBMIT_LABEL.search(label_now)) and not NOT_A_SUBMIT.search(label_now))}
        try:
            if a.action == "fill" and code and value == code and loc.get_attribute("maxlength") == "1":
                # a code split over one-character boxes: type it; such boxes move to the next box by themselves
                loc.click()
                page.keyboard.type(code, delay=120)
            elif a.action == "fill" and (loc.get_attribute("role") or "").lower() == "combobox":
                picked = pick_combobox_option(page, loc, value)      # a search-and-click picklist (SuccessFactors citizenship, country, ...)
                if picked is None:
                    history.append(f"refused: \"{_label(frames, a)}\" is a list and offers no entry '{value}'")
                    continue
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
                # this candidate's code was entered: it counts only if the site is then seen to accept it
                if verification.outcome(page, lambda: loc.is_visible(), settle_s=6, email=values.get("EMAIL", "")) == "accepted":
                    accepted_mail = code_mail
                else:
                    history.append("the site did not accept that code yet")
        except Exception as e:
            history.append(f"{a.action} failed: {type(e).__name__}: {str(e)[:80]}")
        finally:
            try:
                frames[a.frame].evaluate("() => document.querySelectorAll('[data-agent-target]')"
                                         ".forEach(e => e.removeAttribute('data-agent-target'))")
            except Exception:
                pass
    if accepted_mail is not None and state["state"] == "signed_in":
        # only the one candidate whose code or link the site was seen to accept becomes the agent's; a superseded, rejected
        # or unused candidate does not, however the run eventually signed in
        inbox.mark_used(accepted_mail, f"{host} accepted it and the account is signed in")
        cleanup = inbox.consume_and_trash(accepted_mail, "portal_verify")
        state["verified_mail"] = cleanup.ok
        log(f"portal {host}: verification mail accepted by the site; cleanup {cleanup.status} ({accepted_mail.subject!r})")
    return state


SOCIAL_LOGIN = re.compile(r"(?i)\b(?:continue|sign\s*in|log\s*in|login|sign\s*up)\s+with\s+(?:google|microsoft|apple|facebook|linkedin|indeed|github)\b|corporate login|single sign[- ]on")


REGISTRATION_KEYS = ("CITIZENSHIP", "ADDRESS_LINE1", "CITY", "STATE", "POSTAL_CODE", "COUNTY", "COUNTRY_OF_RESIDENCE")
_REGISTRATION_PROFILE = None            # set by `apply`: a callable returning the facts a registration form may ask for


def set_registration_profile(provider) -> None:
    """The facts a registration form can require beyond identity (SuccessFactors asks for citizenship, address and country of
    residence): citizenship from the applicant file and the address the application uses for THIS job (the job-city logic).
    Loaded lazily, only when a form actually asks, and never invented by the model."""
    global _REGISTRATION_PROFILE
    _REGISTRATION_PROFILE = provider


def registration_extras(log=print) -> dict:
    if _REGISTRATION_PROFILE is None:
        return {}
    try:
        return {k: str(v) for k, v in (_REGISTRATION_PROFILE() or {}).items() if k in REGISTRATION_KEYS and v}
    except Exception as exc:
        log(f"portal: the registration profile could not be prepared ({type(exc).__name__}: {str(exc)[:120]})")
        return {}


PLACEHOLDER = re.compile(r"\{[A-Z_]+(?:\[\d+\])?\}")


def pick_combobox_option(page: Page, loc, value: str) -> str | None:
    """A combobox that filters on key events and is chosen by CLICKING an entry (SAP SuccessFactors' picklists): type the value with
    key events, wait for the list, click the entry that equals it (never a longer entry that merely contains it). The chosen entry's
    text, or None when the list offers no such entry."""
    want = re.sub(r"\s+", " ", value or "").strip().lower()
    if not want:
        return None
    loc.click()
    try:
        loc.press("Control+a")
        loc.press("Delete")
    except Exception:
        pass
    loc.press_sequentially(value, delay=70)
    page.wait_for_timeout(1200)
    lid = loc.get_attribute("aria-controls") or ""
    scope = page.locator(f"[id='{lid}']") if lid else page
    entries = scope.locator("[role=option], li").filter(visible=True)
    texts = []
    for i in range(min(entries.count(), 60)):
        t = re.sub(r"\s+", " ", entries.nth(i).inner_text() or "").strip()
        texts.append(t)
        if t.lower() == want:
            entries.nth(i).click(timeout=5000)
            page.wait_for_timeout(500)
            return t
    # A nationality list ("Indian" for the country "India"): the ONE entry that is the typed value plus at most two letters.
    near = [i for i, t in enumerate(texts) if t.lower().startswith(want) and 0 < len(t) - len(want) <= 2]
    if len(near) == 1 and len(texts) == 1:
        entries.nth(near[0]).click(timeout=5000)
        page.wait_for_timeout(500)
        return texts[near[0]]
    try:
        loc.press("Escape")
    except Exception:
        pass
    return None


SIGNUP_LABEL = re.compile(r"(?i)^\s*(?:sign\s*up|create\s+(?:an?\s+|your\s+)?account|register|join now)\s*$")


def signup_link_hint(frames: dict) -> str | None:
    """After a rejected password: where the page's own Sign up / Create account control is, by NAME (the model's index choices drift when
    an error banner shifts the list and one drifted pick landed on 'Continue with Google'). None when the page has no such control."""
    for fid, frame in frames.items():
        try:
            items = frame.evaluate(SNAPSHOT_JS)["items"]
        except Exception:
            continue
        for i, item in enumerate(items):
            if SIGNUP_LABEL.search(item.get("label", "") or ""):
                return (f"the page's own Sign up / Create account control is F{fid}[{i}] ('{item['label'][:30]}'): if a rejected password may "
                        "only mean there is no account, use THAT element (never Continue with Google)")
    return None


def literal_fill_refusal(value: str, label: str) -> str | None:
    """Why a typed value is refused (None when allowed). The sign-in agent may type only the approved placeholders (email, password,
    names, phone, code). A literal it composed itself is a made-up fact: on the Ericsson SuccessFactors registration the model typed
    'New York', '10001' and 'United States' (as the citizenship), none of which came from the candidate."""
    if value and not PLACEHOLDER.search(value):
        return (f"refused: \"{label}\" needs a fact about the candidate that was not given to you; never invent a value "
                "(address, citizenship, dates, anything). If the form cannot be completed with the given placeholders answer need_human")
    return None


def social_login_refusal(url: str, label: str) -> str | None:
    """Why a click on a social or corporate login button is refused (None when it is allowed).

    On iCIMS a social button only signs in an account that was already linked to that provider ("Unable to log in with the social
    account. Please make sure your iCIMS account is connected to one"); for an email that has no iCIMS account it dead-ends, and the
    model repeated the click until the run stopped as stuck. The way in is the application's own sign-up, or the email's own login.
    """
    if not SOCIAL_LOGIN.search(label or ""):
        return None
    if not any(h in (url or "").lower() for h in ("icims.com", "ultipro.com")):
        return None
    return ("refused: a social or corporate login only works here for an account already linked to it, and this email has no "
            "such link; use the email field (sign in, or the sign-up and verification-link route) or answer failed")


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
    original = page.url
    origin = urlparse(original)
    hrefs = page.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a => a.href)
        .filter(h => /profile|user\\/details|my-?account|account-?info|candidate-?home|dashboard/i.test(h)
                     && !/logout|signout|sign-out|help|faq|privacy/i.test(h)).slice(0, 3)""")
    for href in hrefs:
        target = urlparse(href)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            continue  # a corporate/universal account is not evidence of this recruiting portal's session
        try:
            page.goto(href, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            if (urlparse(page.url).scheme, urlparse(page.url).netloc) == (origin.scheme, origin.netloc) \
                    and _shows_account(page, values):
                return True
        except Exception:
            pass
        # A failed evidence probe must not strand the auth agent on another page or identity service.
        if page.url != original:
            try:
                page.go_back(wait_until="domcontentloaded")
                if page.url != original:
                    page.goto(original, wait_until="domcontentloaded")
            except Exception:
                return False
    return False


def _readonly_application_identity(page: Page, email: str) -> bool:
    """An application may show its signed-in email only in a disabled/read-only contact input.

    Require the exact visible identity, a contact section and both account navigation links on this
    portal's registrable domain. An editable/hidden input or a sign-out link alone is not evidence.
    Only inspect the top-level application; a foreign embedded form cannot establish its identity.
    """
    origin = urlparse(page.url)
    if not email or origin.scheme != "https" or not origin.hostname:
        return False
    try:
        return bool(page.evaluate(r"""([email, domain]) => {
          const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
          const els = s => [...document.querySelectorAll(s)].filter(visible);
          if (document.querySelector('input[type=password]')) return false;
          const identity = els('input[type=email],input[name*=email i],input[id*=email i]').some(e =>
            e.type !== 'hidden' && (e.disabled || e.readOnly) && e.value.trim().toLowerCase() === email.toLowerCase());
          if (!identity || !els('h1,h2,h3,[role=heading]').some(e =>
              /^(?:contact(?:\s+information|\s+details)?|personal information)$/i.test(e.innerText.split(/\r?\n/)[0].trim()))) return false;
          const links = els('a[href]').filter(e => {
            const u = new URL(e.href, location.href);
            return u.protocol === 'https:' && (u.hostname === domain || u.hostname.endsWith('.' + domain));
          });
          return links.some(e => /^(?:(?:my|candidate|applicant)\s+)?profile$/i.test(e.innerText.trim()) &&
              /profile|user\/details|my-?account/i.test(new URL(e.href).pathname)) &&
            links.some(e => /^(?:(?:my|job)\s+)?applications$/i.test(e.innerText.trim()) &&
              /applications|applicant|dashboard/i.test(new URL(e.href).pathname));
        }""", [email, db.registrable_domain(origin.hostname)]))
    except Exception:
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
        # Profile values are omitted from body.innerText, including collapsed accordions. Require an exact email
        # in a real profile input, visible profile/sign-out UI and no password form; a hidden metadata input is insufficient.
        if values.get("EMAIL"):
            try:
                if f.evaluate(r"""email => {
                  const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
                  const els = s => [...document.querySelectorAll(s)].filter(visible);
                  return !document.querySelector('input[type=password]') &&
                    els('h1,h2,h3,[role=heading]').some(e => /(?:my|candidate|applicant)\s+profile|^profile\s+information$/i.test(e.innerText.trim())) &&
                    els('a,button,[role=button]').some(e => /^(?:sign|log)\s*out$/i.test(e.innerText.trim())) &&
                    [...document.querySelectorAll('input[type=email],input[name*=email i],input[id*=email i]')].some(e =>
                      e.type !== 'hidden' && e.value.trim().toLowerCase() === email.toLowerCase());
                }""", values["EMAIL"]):
                    return True
            except Exception:
                pass
    return _readonly_application_identity(page, values.get("EMAIL", ""))


def _label(frames: dict, a: AuthAction) -> str:
    try:
        return frames[a.frame].evaluate(SNAPSHOT_JS)["items"][a.element]["label"][:40]
    except Exception:
        return "?"
