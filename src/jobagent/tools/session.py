"""One browser session abstraction for `apply` and `submit` (F14).

Both commands open the application the same way - Docker's persistent Chrome, or, only when asked for, the host desktop browser
attached over its DevTools port - and run the same filling code in it. `submit` therefore continues in the SAME kind of session
that `apply` filled in (a desktop application can be sent from the desktop browser, which is the only place its page exists),
and the reviewed-submission hook is attached to that session's page.

Sending is never implied by a mode: `submit` needs the candidate's `approve` for that application at the review prompt in every
mode (the approval capability of F01/F04 is untouched), and the desktop browser additionally needs `--allow-desktop-submit` on the
command line. `auto` (Docker first, desktop after a challenge) is for filling only: a submission cannot start in one browser and
finish in another, so it is refused.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

FILL_MODES = ("docker", "desktop", "auto")
SUBMIT_MODES = ("docker", "desktop")


@dataclass
class Session:
    mode: str          # docker | desktop
    ctx: Any
    page: Any
    policy: Any = None     # the desktop session's scope policy (tools/desktop_browser.Scope); None for Docker

    @property
    def already_open(self) -> bool:
        """The desktop application is opened by the candidate's browser, not navigated to by the agent."""
        return self.mode == "desktop"

    def run_options(self, *, observe_s: int = 0, auto_fallback: bool = False) -> dict:
        """The keyword arguments `steps.apply.run` takes for this session."""
        return {"already_open": self.already_open, "browser_policy": self.policy, "observe_s": observe_s if self.already_open else 0,
                "desktop_fallback": auto_fallback and self.mode == "docker"}


def check_mode(mode: str, *, submitting: bool, allow_desktop_submit: bool = False) -> str:
    """The mode, or a ValueError that says what to do. Filling accepts docker, desktop and auto; submitting accepts docker, and
    desktop only with the explicit flag."""
    if submitting:
        if mode == "auto":
            raise ValueError("submit does not support --browser-mode auto: a submission cannot start in one browser and finish in "
                             "another. Fill with `apply` (auto is fine there), then submit in the browser that holds the application.")
        if mode not in SUBMIT_MODES:
            raise ValueError("browser-mode must be docker or desktop")
        if mode == "desktop" and not allow_desktop_submit:
            raise ValueError("submitting from the desktop browser must be asked for explicitly: add --allow-desktop-submit. "
                             "You will still be asked to approve this application before anything is sent.")
    elif mode not in FILL_MODES:
        raise ValueError("browser-mode must be docker, desktop or auto")
    return mode


@contextmanager
def open_session(mode: str, url: str, related_urls=()) -> Iterator[Session]:
    """Open the application in `mode` ('docker' or 'desktop') and yield the session."""
    from jobagent.tools.browser import application_context

    with application_context(mode, url, related_urls=list(related_urls)) as ctx:
        page = getattr(ctx, "jobagent_page", None) or ctx.pages[0]
        yield Session(mode, ctx, page, getattr(ctx, "jobagent_policy", None))
