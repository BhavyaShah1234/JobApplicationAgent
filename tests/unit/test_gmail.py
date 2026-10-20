import inspect
import re

import pytest

from jobagent.steps import gmail


@pytest.mark.parametrize(
    "url,signed_in",
    [
        # The signup flow itself carries continue=...ManageAccount; it must not count as signed in.
        ("https://accounts.google.com/lifecycle/steps/signup/name?dsh=S1:2&flowEntry=SignUp"
         "&continue=https://accounts.google.com/ManageAccount?nc%3D1", False),
        ("https://accounts.google.com/v3/signin/identifier?continue=https://mail.google.com/", False),
        ("https://myaccount.google.com/?utm_source=sign_in_no_continue&pli=1", True),
        ("https://mail.google.com/mail/u/0/#inbox", True),
    ],
)
def test_signed_in_uses_host_only(url, signed_in):
    assert gmail._signed_in(url) is signed_in


@pytest.mark.parametrize(
    "text,blocked",
    [
        ("Sorry, we could not create your Google Account.", True),
        ("Couldn't create your Google Account", True),
        ("This browser or app may not be secure. Learn more", True),
        ("Couldn’t sign you in", True),
        ("Confirm you're not a robot", False),
        ("Verify some info before creating an account", False),
    ],
)
def test_blocked_detection(text, blocked):
    assert bool(gmail.BLOCKED.search(text)) is blocked


@pytest.mark.parametrize(
    "text,username",
    [("bhavyamineshshah@gmail.com", "bhavyamineshshah"), ("shah.bhavya.m@gmail.com", "shah.bhavya.m"),
     ("Create your own Gmail address", None), ("abc@gmail.com", None)],
)
def test_suggestion_format(text, username):
    m = gmail._SUGGESTION.match(text)
    assert (m.group(1) if m else None) == username


def test_no_inline_regex_flags_reach_playwright():
    # Playwright converts Python regexes to JavaScript, where inline (?i) is a syntax error.
    source = inspect.getsource(gmail)
    offenders = re.findall(r"get_by_\w+\([^)]*\(\?i\)", source)
    assert not offenders, offenders
