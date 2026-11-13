"""Employee-referral answers using the user's policy and company contacts."""

import csv
import re

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


def company_key(value: str) -> str:
    value = re.sub(
        r"(?i)\b(?:incorporated|inc|llc|ltd|limited|"
        r"corporation|corp|company|co)\b",
        " ",
        value,
    )
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def contact_for_company(company: str) -> dict:
    path = get_settings().data_dir / "asu_contacts.csv"
    if not path.exists():
        return {}

    matches = []

    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if company_key(row.get("company", "")) != company_key(company):
                continue

            first = (row.get("first_name") or "").strip()
            last = (row.get("last_name") or "").strip()
            name = (row.get("name") or "").strip()
            name = name or " ".join(x for x in (first, last) if x)

            email = ""
            for column in ("email", "alternate_email"):
                candidate = (row.get(column) or "").strip()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate):
                    email = candidate
                    break

            matches.append({
                "name": name,
                "first_name": first or (name.split()[0] if name else ""),
                "last_name": last or (name.split()[-1] if name else ""),
                "email": email,
            })

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


def _referral_option(options: list[str]) -> str:
    ranked = []

    for index, option in enumerate(options):
        leaf = option.rsplit(" > ", 1)[-1].casefold().strip()

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

        if employee and (referral or "referr" in option.casefold()):
            rank = 100
        elif employee and re.fullmatch(
            r"(?:current\s+)?(?:employee|associate|colleague|co-worker)",
            leaf,
        ):
            rank = 90
        elif referral and not re.search(
            r"\b(?:recruiter|agency|customer|client|friend|family)\b",
            leaf,
        ):
            rank = 80
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
    source = bool(SOURCE.search(label))

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
            option = _referral_option(field.options)
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
