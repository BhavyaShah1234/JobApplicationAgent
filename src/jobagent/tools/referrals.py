"""Employee-referral answers using the user's policy and company contacts."""

import csv
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from jobagent.config import get_settings


SOURCE = re.compile(
    r"(?i)"
    r"\b(?:how|where)\b.{0,60}"
    r"\b(?:hear|heard|learn|find|found|discover)\w*\b.{0,90}"
    r"\b(?:us|company|position|job|role|opening|opportunity|vacancy)\b"
    r"|^\s*(?:(?:application|candidate|recruitment|referral)\s+)?source\b"
)

REFERRAL = re.compile(
    r"(?i)\b(?:referr\w*|employee|associate|colleague)\b"
)
NAME = re.compile(
    r"(?i)\b(?:name|first\s*name|last\s*name|full\s*name|surname)\b"
    r"|\bwho\s+(?:referred|recommended)\b"
)
EMAIL = re.compile(r"(?i)\be[- ]?mail\b")
CONTACT_DETAILS = re.compile(
    r"(?i)\b(?:contact\s+(?:information|details)|referrer\s+details)\b"
)
PRONOMINAL_CONTACT = re.compile(
    r"(?i)\b(?:their|his|her|that person'?s)\b.{0,45}\b(?:name|e[- ]?mail|phone|address)\b"
)
SOURCE_FOLLOWUP = re.compile(
    r"(?i)^\s*(?:if\s+[\"'“”‘’]?other[\"'“”‘’]?\s*[,;:]?\s*)?"
    r"(?:other\s*[,;:]?\s*)?(?:please\s+)?(?:specify|select)(?:\s+here)?\s*[:*?.]*\s*$"
)
SOURCE_CONTACT_FOLLOWUP = re.compile(
    r"(?i)^\s*if\s+you\s+(?:answered|selected|chose)\s+(.+),\s*"
    r"(?:please\s+)?specify(?:\s+here)?\s*[:*?.]*\s*$"
)


def contextualize(fields) -> None:
    """Bind adjacent sourcing follow-ups within the same frame and section."""
    parent = None
    for field in fields:
        field.source_followup = False
        conditional = SOURCE_CONTACT_FOLLOWUP.fullmatch(field.label)
        contact_followup = bool(conditional and REFERRAL.search(conditional.group(1)))
        if SOURCE.search(field.label):
            parent = field
        elif (parent is not None and field.frame == parent.frame
              and field.section == parent.section
              and (SOURCE_FOLLOWUP.fullmatch(field.label) or contact_followup)):
            field.source_followup = True
            if contact_followup:
                field.help = (field.help + ' Employee referrer contact details for the adjacent sourcing question.').strip()
        elif (parent is not None and field.frame == parent.frame
              and field.section == parent.section and _referral_option([parent.value])
              and PRONOMINAL_CONTACT.search(field.label)):
            field.help = (field.help + f" Employee referrer details for {parent.label!r}: {parent.value}.").strip()
        else:
            parent = None


def company_key(value: str) -> str:
    value = re.sub(
        r"(?i)\b(?:incorporated|inc|llc|ltd|limited|"
        r"corporation|corp|company|co)\b",
        " ",
        value,
    )
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def company_keys(company: str) -> set[str]:
    """Exact names plus an explicit, unambiguous same-company alias group; never fuzzy or substring matching."""
    import yaml
    key=company_key(company)
    try:
        path=get_settings().data_dir/'company_aliases.yaml'
        data=yaml.safe_load(path.read_text()) or {}
        if not isinstance(data,dict):return {key}
        groups=[]
        for canonical,entry in data.items():
            if not isinstance(canonical,str) or not isinstance(entry,dict):continue
            aliases=entry.get('aliases',[])
            if not isinstance(aliases,list) or any(not isinstance(x,str) for x in aliases):continue
            names={company_key(x) for x in [canonical,*aliases] if x.strip()}
            if key in names:groups.append(names)
        return groups[0] if len(groups)==1 else {key}
    except (OSError,ValueError,yaml.YAMLError):
        return {key}


def _company_email_domains(keys: set[str]) -> set[str]:
    """Use only an unambiguous same-company researched website; never guess a domain from its name."""
    from jobagent import db

    state = getattr(get_settings(), "state_dir", None)
    if state is None:
        return set()
    domains = set()
    for path in (Path(state) / "companies").glob("*.json"):
        try:
            profile = json.loads(path.read_text())
            if not isinstance(profile, dict) or company_key(profile.get("name", "")) not in keys:
                continue
            website = urlsplit(profile.get("website") or "")
            if website.scheme != "https" or not website.hostname or website.username or website.password:
                continue
            domain = db.registrable_domain(website.hostname)
            if domain:
                domains.add(domain)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return domains if len(domains) == 1 else set()


def contact_for_company(company: str) -> dict:
    path = get_settings().data_dir / "asu_contacts.csv"
    if not path.exists():
        return {}

    matches = []
    keys=company_keys(company)
    domains = _company_email_domains(keys)

    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            matched=company_key(row.get("company", ""))
            if matched not in keys:
                continue

            first = (row.get("first_name") or "").strip()
            last = (row.get("last_name") or "").strip()
            name = (row.get("name") or "").strip()
            name = name or " ".join(x for x in (first, last) if x)

            emails = []
            for column in ("email", "alternate_email"):
                candidate = (row.get(column) or "").strip()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate):
                    emails.append(candidate)
            corporate = [email for email in emails if email.rsplit("@", 1)[1].casefold() in domains]
            email = next(iter(corporate or emails), "")

            matches.append((not bool(corporate), matched != company_key(company), {
                "name": name,
                "first_name": first or (name.split()[0] if name else ""),
                "last_name": last or (name.split()[-1] if name else ""),
                "email": email,
            }))

    matches=[contact for _,_,contact in sorted(matches,key=lambda item:item[:2])]

    return next(
        (contact for contact in matches if contact["name"] and contact["email"]),
        matches[0] if matches else {},
    )


def _answer(value: str, flag: str = "") -> dict:
    return {
        "value": value,
        "source": "lookup",
        "why": "user's sourcing policy and matching asu_contacts.csv row",
        "lookup": "",
        "ask_human": bool(flag and not value),
        "flag": flag,
    }


def _referral_option(options: list[str], company: str = '') -> str:
    ranked = []

    for index, option in enumerate(options):
        leaf = option.rsplit(" > ", 1)[-1].casefold().strip()
        if company:
            leaf=re.sub(r'^'+re.escape(company.casefold())+r'\s+', '', leaf)

        if re.search(
            r"\bnon[- ]?employee\b|\bnot\s+referred\b|"
            r"\bno\s+referral\b|^none\b",
            leaf,
        ):
            continue

        employee = bool(re.search(
            r"\b(?:employee|associate|colleague|co-worker|team member)\b",
            leaf,
        ))
        referral = bool(re.search(r"referr|recommend", leaf))
        # Employment-status qualifiers preserve the meaning of Employee;
        # unrelated parentheticals and negative referral choices stay intact.
        employee_leaf = re.sub(
            r"\s*\(\s*(?:current|former)(?:\s+or\s+(?:current|former))?\s*\)\s*$", "", leaf)
        knows_employee = bool(re.search(r"\bknow\s+some(?:one|body)\s+at\s+(?:the\s+)?company\b", leaf))

        if employee and (referral or "referr" in option.casefold()):
            rank = 100
        elif knows_employee:
            rank = 90
        elif employee and re.fullmatch(
            r"(?:(?:current|former)(?:\s+or\s+(?:current|former))?\s+)?(?:employee|associate|colleague|co-worker)",
            employee_leaf,
        ):
            rank = 90
        elif referral and not re.search(
            r"\b(?:recruiter|agency|customer|client|friend|family)\b",
            leaf,
        ):
            rank = 80
        elif leaf == "from a person":
            rank = 20  # parent category; prefer an explicit employee option
        else:
            continue

        ranked.append((rank, -index, option))

    return max(ranked)[2] if ranked else ""


def answer_for(field, contact: dict, company: str = "") -> dict | None:
    """None means this is not a sourcing/referrer field."""
    if re.search(
        r"(?i)work experience|employment history|education",
        field.section,
    ):
        return None

    label = field.label
    if company:
        label = re.sub(re.escape(company), "company", label, flags=re.I)

    help_text = getattr(field, "help", "")
    context = f"{label} {help_text} {field.section}"
    source = bool(SOURCE.search(label) or getattr(field, "source_followup", False))

    name_requested = bool(NAME.search(context))
    email_requested = bool(EMAIL.search(context))
    details_requested = bool(CONTACT_DETAILS.search(context))

    detail = bool(
        REFERRAL.search(context)
        and (name_requested or email_requested or details_requested)
    )

    if not source and not detail:
        return None

    if source and field.kind in (
        "select", "combobox", "radio", "buttons", "checkboxes",
    ):
        if field.options:
            option = _referral_option(field.options, company) or next((x for x in field.options if x.strip().lower()=='other'),'')
            return _answer(
                option,
                "" if option else "no employee-referral option found",
            )

        if field.kind == "combobox":
            return _answer(
                "Referred by an employee | Employee Referral | Referral"
            )

        return _answer("", "referral options have not been discovered")

    if source and not detail:
        return _answer("Referred by employee")

    name = contact.get("name", "")
    email = contact.get("email", "")

    label_name = bool(NAME.search(label))
    label_email = bool(EMAIL.search(label))

    if not source and (
        field.kind == "email" or (label_email and not label_name)
    ):
        value = email
    elif not source and label_name and not label_email:
        if re.search(r"(?i)\bfirst\s*name\b", label):
            value = contact.get("first_name", "")
        elif re.search(r"(?i)\blast\s*name\b|\bsurname\b", label):
            value = contact.get("last_name", "")
        else:
            value = name
    else:
        if (name_requested or details_requested) and not name:
            return _answer("", "matching CSV row has no referrer's name")
        if (email_requested or details_requested) and not email:
            return _answer("", "matching CSV row has no referrer's email")

        value = f"{name} ({email})" if name and email else name or email
        if source and value:
            value = f"Referred by employee: {value}"

    if not value:
        return _answer(
            "",
            "no matching company contact with the required details in "
            "/app/data/asu_contacts.csv",
        )

    return _answer(value)
