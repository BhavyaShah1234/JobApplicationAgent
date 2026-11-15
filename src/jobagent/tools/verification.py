"""Did the site ACCEPT the emailed code or link? (F08)

A verification mail may be trashed only once the site has accepted it, and the default is "not proven". Acceptance needs
AFFIRMATIVE evidence that this verification completed, found within a bounded time:

  * a sentence that asserts completion ("your email is verified", "account activated") and is neither negated ("not
    verified", "could not confirm"), pending ("processing", "please wait") nor contradicted elsewhere on the page; or
  * the agent's own address shown with sign-out / account controls (a signed-in view), with nothing pending or failed.

A form that merely disappears, a bare "welcome", an unfamiliar error, a challenge or a pending screen is NOT evidence. A
recognised rejection ends the wait at once. When the code field cannot be inspected, the answer stays unproven.
"""
from __future__ import annotations

import re
import time

COMPLETED = re.compile(r"(?i)\b(email|e-mail|address|account|identity|phone|number|code)\b[^.!?\n]{0,25}?\b(has been|have been|is|was|were|are)\b"
                       r"\s+(now\s+|successfully\s+)?(verified|confirmed|activated)\b|"
                       r"\bverification\s+(is\s+|was\s+|has been\s+)?(successful|complete|completed)\b|"
                       r"\bsuccessfully\s+(verified|confirmed|activated)\b|\bthank you for verifying\b|\bemail verified\b")
NEGATED = re.compile(r"(?i)\b(not|never|no longer|isn'?t|wasn'?t|aren'?t|hasn'?t|haven'?t|unable to|could ?n[o']?t|can ?n[o']?t|cannot|failed to|"
                     r"fail(ed|ure)?|unsuccessful|incomplete|pending|required|needed|still need|awaiting|unverified|"
                     r"un-?confirmed)\b")
PENDING = re.compile(r"(?i)\b(processing|please wait|one moment|hold on|loading|verifying|checking|in progress|working on it|"
                     r"redirecting|just a moment)\b")
REJECTED = re.compile(r"(?i)(invalid|incorrect|wrong|expired|did(n'?t| not) match|not valid|doesn'?t look right|does not look right)"
                      r"[^.\n]{0,40}(code|link|token)|(code|link|token)[^.\n]{0,40}(invalid|incorrect|expired|is wrong|not valid|"
                      r"didn'?t work)|try again")
BLOCKED = re.compile(r"(?i)(verify you are human|captcha|just a moment|checking your browser|access denied|forbidden|"
                     r"something went wrong|page not found|\b404\b|\b500\b|unexpected error|service unavailable|contact support|"
                     r"could not confirm|couldn'?t confirm|unable to (verify|confirm))")
PROMPT = re.compile(r"(?i)(enter|type|input)( the| your)?( 6-digit| six-digit)? ?(verification |security |one-time )?code|"
                    r"verification code|one-time (code|passcode)|we('| ha)ve sent")
SIGNED_IN = re.compile(r"(?i)\b(sign|log)\s?out\b|\bmy (account|profile|applications)\b|\bsigned in as\b|\blogged in as\b")


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip()]


def affirms_completion(body: str) -> bool:
    """True if the page asserts that the verification COMPLETED: at least one completion sentence that is itself not
    negated or pending, and no sentence anywhere that says the verification failed, is pending or still required."""
    if PENDING.search(body) or BLOCKED.search(body):
        return False
    positive = False
    for s in _sentences(body):
        if COMPLETED.search(s):
            if NEGATED.search(s):
                return False
            positive = True
        elif re.search(r"(?i)\bverif|confirm", s) and NEGATED.search(s):
            return False                              # "not verified", "verification not complete", "verification required"
    return positive


def contradicts(body: str) -> bool:
    """Does the page say, anywhere, that this verification failed, is pending or is still required? That overrides every
    positive sign (a completion sentence, an account view, the agent's address): the page explicitly says the opposite."""
    if PENDING.search(body) or BLOCKED.search(body):
        return True
    return any(re.search(r"(?i)\bverif|confirm", s) and NEGATED.search(s) for s in _sentences(body))


def outcome(page, still_present=None, settle_s: float = 8, poll_s: float = 0.5, email: str = "") -> str:
    """'accepted', 'rejected' or 'unproven'. `still_present()` says whether the code field is still on the page (True/False);
    if it raises, the field's state is UNKNOWN and nothing is accepted on that poll. The wait is bounded by `settle_s`."""
    deadline = time.monotonic() + settle_s
    while True:
        try:
            body = page.locator("body").inner_text(timeout=5000)[:6000]
        except Exception:
            body = ""
        if REJECTED.search(body):
            return "rejected"
        field = "none"                                 # no code field was involved (a link)
        if still_present is not None:
            try:
                field = "visible" if still_present() else "gone"
            except Exception:
                field = "unknown"                      # an inspection error is not evidence that the field is gone
        affirmed = affirms_completion(body)
        signed = bool(email and email.lower() in body.lower() and SIGNED_IN.search(body)
                      and not contradicts(body) and not PROMPT.search(body))
        if (affirmed or signed) and not contradicts(body) and field in ("none", "gone") and not PROMPT.search(body):
            return "accepted"
        if time.monotonic() >= deadline:
            return "unproven"
        page.wait_for_timeout(int(poll_s * 1000))
