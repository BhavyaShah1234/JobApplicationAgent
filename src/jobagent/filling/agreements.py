"""Project explicit standing owner permission onto agreement controls."""
import hashlib
import re

from .facts import Fact, key, norm, scope_allows

POLICY_QUESTION = 'Always accept terms, agreements, consents and acknowledgements'
POLICY_CONTEXT = 'standing-agreement-permission-v1'
PREFIX = 'application.answer.agreement-policy:'


def agreement_value(field):
    """Choose an offered acceptance, never a history answer or submit action."""
    if field.kind not in {'checkbox', 'checkboxes', 'radio', 'buttons', 'select', 'combobox', 'text', 'textarea'}:
        return None
    q = norm(field.label + ' ' + field.help)
    if re.search(r'captcha|\b(?:not a robot|i am human|human verification|verify you are human)\b',
                 q + ' ' + norm(field.identity)):
        return None
    if re.search(r'\b(?:bound by|subject to|previously signed|already signed|previously agreed|non compete|non solicitation)\b', q):
        return None
    action = re.search(r'\b(?:agree|disagree|accept|acknowledge|acknowledgement|acknowledgment|consent|certify|confirm|understand|read)\b', q)
    subject = re.search(r'\b(?:terms|conditions|agreements?|nda|arbitration|privacy|polic(?:y|ies)|notice|'
                        r'code of conduct|data processing|background check|communications|messages|'
                        r'truthful|accurate|accuracy|at will)\b', q + ' ' + norm(field.section))
    if not action or not subject:
        return None
    # Consent does not establish that a separate activity was completed or that
    # the applicant possesses a qualification or personal status.
    if re.search(r'\b(?:marked|added|opened|completed|visited)\b|\b(?:years? of experience|citizen|convicted|hold a clearance)\b', q):
        return None
    negative = bool(re.search(r'\b(?:do not|don t|not) (?:agree|accept|consent|acknowledge)\b|\bdisagree\b', q))
    if field.kind == 'checkbox':
        return '' if negative else 'checked'
    if field.kind in {'text', 'textarea'}:
        return 'Yes' if re.search(r'\b(?:enter|type|answer)\s+yes\b', q) else 'I agree'
    if field.options_state != 'complete':
        return None
    if negative and {norm(o) for o in field.options} == {'yes', 'no'}:
        return next(o for o in field.options if norm(o) == 'no')
    positive = []
    for option in field.options:
        value = norm(option)
        if re.search(r'\bsubmit\b|\b(?:send|finish|complete) (?:my |this |the )?application\b', value):
            continue
        if re.search(r'^(?:no|not|disagree|decline|reject|withdraw)\b|\bopt out\b|'
                     r'\b(?:do not|don t|not) (?:agree|accept|consent|acknowledge)\b', value):
            continue
        if value not in {'yes', 'agree', 'accept', 'acknowledge', 'acknowledged', 'consent', 'i agree', 'i accept'} and not re.search(
                r'^yes\b|\b(?:agree|accept|acknowledge|consent|certify|understand)\b|\bi have read\b', value):
            continue
        # Some portals expose another agreement's choices in this catalogue.
        if 'arbitration' in q and re.search(r'\bagreement\b', value) and 'arbitration' not in value:
            continue
        if re.search(r'\b(?:non disclosure|nondisclosure|nda)\b', q) and 'arbitration' not in q and 'arbitration' in value:
            continue
        positive.append(option)
    positive = list(dict.fromkeys(positive))
    if len(positive) == 1:
        return positive[0]
    if positive and field.kind == 'checkboxes' and field.multiple:
        return '; '.join(positive)
    return None


def permission(entries):
    """Only the named, globally scoped owner opt-in grants this permission."""
    matching = [e for e in entries if e.get('question') == POLICY_QUESTION and
                e.get('company') == 'any' and e.get('policy_context') == POLICY_CONTEXT]
    return len(matching) == 1 and norm(matching[0].get('answer', '')) in {'yes', 'true'}


def project(fields, facts, entries):
    """Bind permission to this exact field, retaining more specific live replies."""
    result = [f for f in facts if not f.id.startswith(PREFIX)]
    if not permission(entries):
        return result
    for field in fields:
        if any(f.id.startswith('application.answer.') and not f.id.startswith(PREFIX) and
               scope_allows(field, f) and norm(field.label) in map(norm, f.aliases) for f in result):
            continue
        value = agreement_value(field)
        if value is None:
            continue
        ident = PREFIX + hashlib.sha256(repr(key(field)).encode()).hexdigest()[:20]
        result.append(Fact(ident, [field.label], value,
                           'owner standing agreement permission: data/learned.yaml',
                           target_key=key(field), policy_context=POLICY_CONTEXT))
    return result
