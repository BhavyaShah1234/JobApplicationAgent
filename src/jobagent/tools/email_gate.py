"""An application that continues from a link in an email.

Some portals (iCIMS for Garmin, for one) answer the candidate's email address with "Please verify your email account by clicking
on the link we have just sent" and mail the link that continues the application. The form filler used to stop there: the page
has no fields and no button. `follow` recognises that page, waits for the portal's mail in the agent's own inbox (same sender and
time rules as every other verification mail), opens the link in the same browser and lets the apply loop carry on. Only the mail
that arrives after the action, from the portal's own sender domains, is used; only an https link on those domains is opened.
"""

import re
from datetime import datetime, timedelta, UTC
from urllib.parse import urlparse

from jobagent import db

GATE = re.compile(
    r"(?i)(?:verify|confirm)\s+(?:your\s+)?e-?mail(?:\s+account)?(?!\s+address)|"
    r"(?:link|e-?mail|message)\s+(?:we\s+(?:have\s+)?|has\s+|was\s+|that\s+we\s+)?(?:just\s+)?(?:been\s+)?sent|"
    r"check\s+your\s+(?:e-?mail(?!\s+address)|inbox)|"
    r"we(?:'ve|\s+have)\s+sent\s+(?:you\s+)?(?:an?\s+)?(?:e-?mail|link|message)|"
    r"click\s+(?:on\s+)?the\s+link\s+(?:in|we|sent|below)")
# links that are never the way on: unsubscribing, policies, help, images and stylesheets
LINK_SKIP = re.compile(r"(?i)unsubscribe|opt-?out|privacy|terms|preferences|\bhelp\b|support|faq|\.(?:png|jpe?g|gif|svg|css|ico)(?:\?|#|$)")
LINK_HINT = re.compile(r"(?i)verif|confirm|activat|apply|continue|resume|application|candidate|token|login|invite|sign-?in|validate|auth")
_LINK = re.compile(r"https?://[^\s\"'<>)]+", re.I)
MAX_FOLLOWS = 2               # emailed links per application run: a page that keeps asking is a stuck flow, not a slow inbox


def is_gate(text: str) -> bool:
    """Whether a page's text says the way on is a link that was just emailed."""
    return bool(GATE.search(text or ""))


def page_text(page) -> str:
    """The visible text of a page (empty when it cannot be read)."""
    try:
        return page.inner_text("body", timeout=4000)[:6000]
    except Exception:
        return ""


def pick_link(text: str, allowed: set[str]) -> str | None:
    """The link in a portal's mail that continues the application.

    Args:
        text: The mail body (links kept from HTML mail).
        allowed: The registrable domains the link may point to (the portal and its ATS family).

    Returns:
        The best https link on an allowed domain (a verification or continue-looking one first), or None.
    """
    best, best_score = None, -1
    for raw in _LINK.findall(text or ""):
        link = raw.rstrip(".,;:!?")
        parsed = urlparse(link)
        if parsed.scheme != "https" or not parsed.hostname or LINK_SKIP.search(link):
            continue
        if db.registrable_domain(link) not in allowed:
            continue
        score = 2 if LINK_HINT.search(link) else 0
        if score > best_score:
            best, best_score = link, score
    return best


def classifier(allowed: set[str]):
    """A `wait_for_verification` classifier: protected mail (offers, rejections, receipts) is never used; any other mail
    that carries a usable link on the portal's domains counts."""
    from jobagent.tools import inbox

    def classify(mail):
        if inbox.is_protected(mail):
            return None
        link = pick_link(f"{mail.text}", allowed)
        return inbox.Verification("link", None, link, 0.7) if link else None
    return classify


def follow(page, log, since: datetime, posting_url: str = "", timeout_s: float = 300, inbox=None) -> bool:
    """If the page is waiting for an emailed link, fetch it from the inbox and open it in this browser.

    Args:
        page: The Playwright page showing the "we sent you a link" message.
        log: The run's logger.
        since: When the action that triggered the mail happened (the mail must be newer, give or take 30 seconds).
        posting_url: The job posting's URL, whose domain may also be the mail's sender or link domain.
        timeout_s: How long to wait for the mail.
        inbox: An open inbox (tests); by default the vault's agent mailbox.

    Returns:
        True when a link was found and opened; False when the page is not such a page, or no mail came in time.
    """
    from jobagent.tools import challenge, inbox as inbox_mod
    if not is_gate(page_text(page)):
        return False
    site = page.url
    allowed = db.agent_domain_family(site)[0] | (db.agent_domain_family(posting_url)[0] if posting_url else set())
    log(f"apply: the page waits for an emailed link; looking in the agent's inbox (up to {int(timeout_s)} s)")
    ib = inbox or inbox_mod.open_inbox()
    try:
        mail, found = ib.wait_for_verification(site, since, kind="portal", timeout_s=timeout_s, classify=classifier(allowed),
                                               also_from=(posting_url,) if posting_url else ())
    except Exception as exc:
        log(f"apply: no usable mail arrived ({type(exc).__name__}: {str(exc)[:100]})")
        return False
    log(f"apply: found the portal's mail ({mail.subject!r}); opening its link in this browser")
    page.goto(found.link, wait_until="domcontentloaded", timeout=45000)
    challenge.guard(page)
    page.wait_for_timeout(2500)
    if not is_gate(page_text(page)):
        ib.mark_used(mail, "the page moved on after the emailed link was opened")
    return True
