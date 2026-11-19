"""Complete an emailed application code within the already approved submission attempt.

Only the new code controls may change. Original answers and uploads must still
match the approved payload. CAPTCHA widgets remain on the human path. One code
and one verification dispatch are allowed; a receipt is still needed for success.
"""

import contextlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from jobagent.tools import challenge, forms


CODE_CONTROL = r"""e => {
  const code = /\b(?:security|verification|email|one[ -]time)\s+code\b/i;
  const label = [e.getAttribute('aria-label'), e.name, e.id,
    ...[...(e.labels || [])].map(l => l.textContent)].filter(Boolean).join(' ');
  if (e.autocomplete === 'one-time-code' || code.test(label.replaceAll('_',' '))) return true;
  // Split codes often label only the group, not each character input.
  if (e.maxLength !== 1) return false;
  for (let a=e.parentElement, n=0; a && n<5; a=a.parentElement,n++) {
    const inputs=[...a.querySelectorAll('input:not([type=hidden])')];
    if (inputs.length>=4 && inputs.length<=8 && inputs.every(x=>x.maxLength===1) &&
        code.test(a.innerText || '')) return true;
  }
  return false;
}"""
VERIFY_ACTION = re.compile(r"^\s*(?:verify(?: (?:email|code))?|confirm(?: email)?|"
                           r"submit(?: (?:my |your )?application)?|complete (?:my |your )?application|continue)\s*$", re.I)


def code_fields(view):
    """Exactly one code box or one same-frame group of character boxes."""
    found = [f for f in view.fields if f.kind in ('text', 'number', 'tel')
             and forms.locate(view, f).evaluate(CODE_CONTROL)]
    if not found or len({f.frame for f in found}) != 1:
        return []
    if len(found) == 1:
        return found
    return found if 4 <= len(found) <= 8 and all(f.maxlength == 1 for f in found) else []


def unchanged_payload(page, view, fields, records, job_dir, approved_sha):
    """Ignore only the independently identified new OTP controls, never other edits."""
    from jobagent.steps.submit import freeze_payload
    excluded = {(f.frame, f.id) for f in fields}
    original = replace(view, fields=[f for f in view.fields if (f.frame, f.id) not in excluded])
    return view.coverage.complete and freeze_payload(page, original, records, job_dir)[0] == approved_sha


class EmailCodeVerifier:
    def __init__(self, inbox, jd, since, job_dir, attempt, approved_sha, records, log=print, *, approved_url, approved_frames):
        self.inbox, self.jd, self.since = inbox, jd, since
        self.job_dir, self.attempt = job_dir, attempt
        self.approved_sha, self.records, self.log = approved_sha, records, log
        self.approved_url = approved_url
        self.approved_frames = dict(approved_frames)
        self.used = False
        self.mail = None
        self.manual = False
        self.audit = {'attempt': attempt, 'payload_sha256': approved_sha, 'events': []}

    def save(self, state, **metadata):
        self.audit.update(state=state, at=datetime.now(UTC).isoformat(), **metadata)
        self.audit['events'].append({'state': state, 'at': self.audit['at'], **metadata})
        # No code, message body, URL token or code hash is stored here.
        (self.job_dir / 'application' / f'email-verification-{self.attempt}.json').write_text(
            json.dumps(self.audit, indent=2) + '\n')

    def __call__(self, page):
        if self.used:
            return False
        self.used = True
        try:
            return self.complete(page)
        except Exception as exc:
            # A code may have been dispatched. Never retry a click on exception.
            with contextlib.suppress(OSError):
                self.save('unresolved', error_type=type(exc).__name__)
            self.log('submit: email-code verification needs attention; keeping the existing attempt open')
            return False

    def unchanged(self, page, view, fields):
        """A new frame must not receive a code intended for the reviewed portal."""
        if not fields or any(f.frame not in self.approved_frames or
                             view.frames[f.frame].url != self.approved_frames[f.frame] for f in fields):
            return False
        return unchanged_payload(page, view, fields, self.records, self.job_dir, self.approved_sha)

    def complete(self, page):
        from jobagent.steps.submit import application_match

        self.audit['phase'] = 'page_check'
        # Inspect actual puzzle widgets too, rather than letting the email notice
        # hide one. No challenge controls are filled or clicked here.
        if page.url != self.approved_url or challenge.detect(page, include_application_email=False):
            return False
        view = forms.read_page(page)
        fields = code_fields(view)
        self.audit['phase'] = 'approved_payload_check'
        if not self.unchanged(page, view, fields):
            self.log('submit: email-code controls or original approved application could not be verified')
            return False
        matcher = application_match(self.inbox.conn, self.jd)
        expected = self.inbox.address.casefold()
        self.audit['phase'] = 'mail_match'
        self.log('submit: reading the application email code from the configured inbox')
        mail, verification = self.inbox.wait_for_verification(
            self.jd.get('apply_url') or self.jd['source_url'], self.since, timeout_s=120,
            address=self.inbox.address,
            header_filter=lambda h: expected in h.recipients and matcher.plausible(h)
            and h.date is not None and h.date >= self.since - timedelta(seconds=2),
        )
        code = verification.code
        self.audit['phase'] = 'code_format_check'
        length = len(fields) if len(fields) > 1 else fields[0].maxlength
        if length not in range(4, 9):
            notice = page.locator('body').inner_text(timeout=5000)
            hint = re.search(r'\b([4-8])[- ](?:character|digit)\s+code\b', notice, re.I)
            length = int(hint[1]) if hint else None
        if verification.kind != 'otp' or not code or not re.fullmatch(r'[A-Za-z0-9]{4,8}', code) or (length and len(code) != length):
            return False
        # Inbox access can take time. Re-read the actual controls and payload.
        if page.url != self.approved_url or challenge.detect(page, include_application_email=False):
            return False
        fresh = forms.read_page(page)
        current = code_fields(fresh)
        self.audit['phase'] = 'fresh_payload_check'
        if len(current) != len(fields) or not self.unchanged(page, fresh, current):
            return False
        targets = [forms.locate(fresh, f) for f in current]
        self.save('entering_code', message_id=mail.message_id, control_count=len(targets))
        if len(targets) == 1:
            targets[0].fill(code)
        else:
            for target, character in zip(targets, code):
                target.fill(character)
        if ''.join(target.input_value() for target in targets) != code:
            return False
        self.audit['phase'] = 'code_readback_verified'
        self.mail = mail
        # Some portals accept the last character immediately; never click a
        # vanished form or a new challenge after an automatic transition.
        if challenge.detect(page) is None:
            self.save('code_entered')
            return True
        if page.url != self.approved_url or challenge.detect(page, include_application_email=False):
            return False
        fresh = forms.read_page(page)
        current = code_fields(fresh)
        self.audit['phase'] = 'dispatch_payload_check'
        if not self.unchanged(page, fresh, current):
            return False
        if ''.join(forms.locate(fresh, f).input_value() for f in current) != code:
            return False
        buttons = fresh.frames[current[0].frame].get_by_role('button', name=VERIFY_ACTION).filter(visible=True)
        self.audit['phase'] = 'verification_button_check'
        if buttons.count() != 1:
            return False
        self.save('dispatching_verification', message_id=mail.message_id)
        buttons.first.click(timeout=10000)
        self.save('verification_dispatched')
        self.log('submit: emailed code entered and verification dispatched; waiting for the receipt')
        return True

    def human_handoff(self):
        """A person may replace a rejected code: do not claim our mail was accepted."""
        self.manual = True

    def accepted(self):
        if self.mail is not None:
            if self.manual:
                self.save('receipt_after_human_verification')
                return
            self.inbox.mark_used(self.mail, 'Application receipt observed after emailed-code verification')
            self.save('receipt_observed')
