"""Shared ATS sign-in and explicit states for credentials, challenges and scan limits."""

import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from jobagent.tools import google_auth
from jobagent.tools.vault import open_vault


class AutomationBlocked(RuntimeError):
    def __init__(self, message: str, status: str):
        super().__init__(message)
        self.status = status


def check_scan_blockers(text: str, site: str) -> None:
    """A quota or a rejected challenge is not a successful zero-point scan."""
    if re.search(
        r"(?i)captcha verification failed|please (?:complete|solve) (?:the )?captcha|"
        r"verify (?:that )?you are human|checking your browser",
        text,
    ):
        raise AutomationBlocked(
            f"{site}: human verification was rejected or is pending; the site may not display a puzzle",
            "human_required",
        )
    if re.search(
        r"(?i)(?:available|remaining) scans?\s*:\s*0\b|"
        r"\b(?:no|zero) (?:free )?(?:scans?|credits?) (?:left|remaining|available)\b|"
        r"\b0 (?:free )?(?:scans?|credits?) (?:left|remaining|available)\b|"
        r"(?:daily|weekly|monthly|scan) limit (?:has been )?reached|"
        r"(?:used|reached) (?:all )?your (?:free |daily |weekly )?(?:scans?|checks?)|"
        r"you['’]ve used your free tries|\b(\d+)\s+of\s+\1\s+credits? used\b",
        text,
    ):
        raise AutomationBlocked(f"{site}: scan allowance exhausted", "quota_exhausted")


def account_email(site: str) -> str:
    vault = open_vault()
    return (vault.get(f"portal:{site}") or {}).get("email") or (vault.get("gmail") or {}).get("email") or ""


def _google_trigger(page):
    # Some sites keep a second login link behind a modal. Use the modal's link first.
    containers = [page.locator('[role="dialog"]:visible'), page]
    for container in containers:
        for selector in ("button:visible", "a:visible", '[role="button"]:visible'):
            candidates = container.locator(selector).filter(
                has_text=re.compile(r"^(?:continue|sign (?:in|up)|log in)?\s*(?:with\s+)?Google\s*$", re.I)
            )
            if candidates.count():
                return candidates.last
    # Google's embedded button is inside its own frame, not the site's main DOM.
    for frame in page.frames:
        url = urlsplit(frame.url)
        if url.hostname == "accounts.google.com" and url.path == "/gsi/button":
            button = frame.get_by_role("button")
            if button.count() and button.first.is_visible():
                return button.first
    return None


def _google_login(page, site: str, ready, timeout_s: float = 90) -> None:
    """Use the saved Google identity; never buy a plan or rotate accounts for more credits."""
    if ready(page):
        return
    vault = open_vault()
    gmail = vault.get("gmail") or {}
    email = account_email(site)
    if not email:
        raise AutomationBlocked(f"{site}: save the account email in the vault", "authentication_required")

    deadline = time.monotonic() + timeout_s
    trigger_deadline = min(deadline, time.monotonic() + 15)
    trigger = _google_trigger(page)
    while trigger is None and time.monotonic() < trigger_deadline:
        if ready(page):
            return
        page.wait_for_timeout(500)
        trigger = _google_trigger(page)
    if trigger is None:
        raise AutomationBlocked(
            f"{site}: sign in once in noVNC; no Google sign-in control is available",
            "authentication_required",
        )
    before = set(page.context.pages)
    trigger.click(timeout=15_000, no_wait_after=True)
    done = set()

    while time.monotonic() < deadline:
        page.wait_for_timeout(600)
        if ready(page):
            vault.set(f"portal:{site}", email=email, auth="google")
            return
        new_pages = [p for p in page.context.pages if p not in before and not p.is_closed()]
        auth = new_pages[-1] if new_pages else page
        if auth.is_closed() or urlsplit(auth.url).hostname != "accounts.google.com":
            continue  # the provider may still be processing its OAuth callback
        body = auth.locator("body").inner_text(timeout=10_000)
        if body.strip().casefold() in ("", "loading"):
            continue
        check_scan_blockers(body, site)
        if google_auth.BLOCKED.search(body):
            raise AutomationBlocked(
                f"{site}: Google requires an interactive sign-in in noVNC",
                "authentication_required",
            )
        if re.search(r"(?i)Google will allow .+ to access this info", body):
            if re.search(r"(?i)Google Drive|your calendars|your contacts|read.*Gmail", body):
                raise AutomationBlocked(
                    f"{site}: review the additional Google permissions in noVNC",
                    "authentication_required",
                )
            auth.get_by_role("button", name="Continue", exact=True).click(timeout=12_000)
            continue
        if auth.locator(google_auth.PASSWORD_FIELD).count() and (
            gmail.get("email", "").casefold() != email.casefold() or not gmail.get("password")
        ):
            raise AutomationBlocked(
                f"{site}: complete Google sign-in for the saved site identity in noVNC",
                "authentication_required",
            )
        try:
            action = google_auth._step(
                auth, email, gmail.get("password", ""), gmail.get("totp_secret"),
                lambda message: None, done,
            )
        except (google_auth.SignInBlocked, google_auth.PasswordRejected) as exc:
            raise AutomationBlocked(
                f"{site}: Google sign-in needs attention in noVNC",
                "authentication_required",
            ) from exc
        except PlaywrightError:
            # OAuth can finish and close its popup while _step waits for the
            # next Google field. Re-check the provider rather than failing a
            # successful sign-in. Errors on a still-open Google page remain real.
            if not page.is_closed() and (auth.is_closed() or urlsplit(auth.url).hostname != "accounts.google.com"):
                continue
            raise
        if not action:
            # A visible MFA, phone or security prompt must be completed by its owner.
            raise AutomationBlocked(
                f"{site}: finish the Google security prompt in noVNC and retry",
                "authentication_required",
            )
    raise AutomationBlocked(f"{site}: sign-in did not finish in time", "authentication_required")


def human_handoff(page, site: str, message: str, ready, timeout_s: float = 120) -> None:
    """Keep Chrome open and resume only after the owner actually clears the blocker."""
    from jobagent.tools.hitl import HitlTimeout, open_hitl

    page.bring_to_front()
    try:
        open_hitl().ask(
            f"{site}: {message}. Complete it in noVNC (http://localhost:6080/vnc.html), "
            "then answer 'done'; 'skip' leaves this site pending.",
            done_when=lambda: ready(page), timeout_s=timeout_s,
        )
    except HitlTimeout as exc:
        raise AutomationBlocked(f"{site}: human verification is still pending", "human_required") from exc
    page.wait_for_timeout(700)
    if not ready(page):
        raise AutomationBlocked(f"{site}: human verification is still pending", "human_required")


def google_login(page, site: str, ready, timeout_s: float = 90) -> None:
    try:
        _google_login(page, site, ready, timeout_s)
    except AutomationBlocked as exc:
        if exc.status not in {"authentication_required", "human_required"}:
            raise
        human_handoff(page, site, str(exc), ready)
