"""Open the owner's temporary mailbox, using the existing guarded desktop bridge.

Only the mailbox address is read. Verification controls and message links are
left to the owner; opening a mailbox never registers an account at another site.
"""

import re
import time
from urllib.parse import urlsplit

from jobagent.tools import browser, challenge

URL = 'https://temp-mail.org/'


def _address(page):
    if urlsplit(page.url).hostname not in {'temp-mail.org', 'www.temp-mail.org'}:
        raise challenge.Blocked('The temporary mailbox left temp-mail.org; inspect the desktop window')
    field = page.locator('input#mail:visible')
    if field.count() != 1:
        return ''
    value = field.input_value(timeout=3000).strip()
    return value if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value) else ''


def open_mailbox(mode='auto', *, wait_s=120, settings=None, log=print):
    """Read an address without clicking challenges, changing mailboxes or reading messages.

    Auto retries in desktop Chrome only after a recognized Docker security
    challenge. Desktop Chrome stays open after the command for owner verification
    and incoming messages. A loading mailbox is not evidence of a challenge.
    """
    if mode not in {'docker', 'desktop', 'auto'}:
        raise ValueError('Browser mode must be docker, desktop or auto')
    if not 0 < wait_s <= 3600:
        raise ValueError('Mailbox wait must be between zero and 3600 seconds')
    modes = ('docker', 'desktop') if mode == 'auto' else (mode,)
    for current in modes:
        with browser.application_context(current, URL, settings) as ctx:
            page = getattr(ctx, 'jobagent_page', None) or ctx.pages[0]
            policy = getattr(ctx, 'jobagent_policy', None)
            if current == 'docker':
                page.goto(URL, wait_until='domcontentloaded', timeout=30000)
            deadline = time.monotonic() + wait_s
            notified = False
            while time.monotonic() < deadline:
                if policy:
                    policy(page)
                block = challenge.detect(page)
                if block and block.kind != 'unknown':
                    if current == 'docker' and mode == 'auto':
                        log('temp-mail: security check in Docker; opening host desktop Chrome')
                        break
                    if not notified:
                        log('temp-mail: human verification is pending; solve it in the browser. No challenge is automated.')
                        notified = True
                elif not block:
                    if address := _address(page):
                        log(f'temp-mail: mailbox ready in {current} Chrome')
                        return {'email': address, 'browser_mode': current,
                                'desktop_window_left_open': current == 'desktop'}
                page.wait_for_timeout(1000)
            else:
                suffix = ' The desktop window remains open.' if current == 'desktop' else ''
                raise challenge.Blocked('Temporary mailbox address is not ready; inspect the browser.' + suffix)
    raise challenge.Blocked('Temporary mailbox could not be opened')
