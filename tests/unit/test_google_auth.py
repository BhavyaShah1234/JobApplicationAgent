import base64

import pytest

from jobagent.tools.google_auth import BLOCKED, totp

# RFC 6238 Appendix B test secret (ASCII "12345678901234567890"), SHA-1, 8 digits.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    "at,expected",
    [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")],
)
def test_totp_rfc6238_vectors(at, expected):
    assert totp(RFC_SECRET, at=at, digits=8) == expected


def test_totp_accepts_google_style_spaced_lowercase_keys():
    spaced = " ".join(RFC_SECRET[i:i + 4] for i in range(0, len(RFC_SECRET), 4)).lower()
    assert totp(spaced, at=59) == totp(RFC_SECRET, at=59) == "287082"


def test_signin_url_targets_gmail_in_english():
    from urllib.parse import parse_qs, urlparse

    from jobagent.tools.google_auth import SIGNIN_URL

    url = urlparse(SIGNIN_URL)
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["hl"] == ["en"] and query["continue"] == ["https://mail.google.com/mail/u/0/"]


@pytest.mark.parametrize(
    "text,blocked",
    [("This browser or app may not be secure", True), ("Couldn’t sign you in", True),
     ("2-Step Verification", False), ("Welcome, Bhavya", False)],
)
def test_blocked_detection(text, blocked):
    assert bool(BLOCKED.search(text)) is blocked
