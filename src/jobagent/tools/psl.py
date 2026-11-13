"""Registrable domains from the Public Suffix List (F09).

`data/public_suffix_list.dat` is the list from https://publicsuffix.org/list/public_suffix_list.dat (MPL 2.0), vendored so no
network is needed. Both its sections count: a PRIVATE suffix such as `github.io` or `herokuapp.com` is a shared hosting
space, so `acme.github.io` and `other.github.io` are different identities, and `acme.co.za` and `unrelated.co.za` are too.
A host that IS a public suffix has no registrable domain and can never be a company's own sender identity. Refresh the file
by downloading the list again; the algorithm follows https://github.com/publicsuffix/list/wiki/Format.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

LIST = Path(__file__).resolve().parent.parent / "data" / "public_suffix_list.dat"


def _ascii(label: str) -> str:
    try:
        return label.encode("idna").decode("ascii")
    except UnicodeError:
        return label


@lru_cache(maxsize=1)
def _rules() -> tuple[frozenset, frozenset, frozenset]:
    plain, wild, exc = set(), set(), set()
    for line in LIST.read_text(encoding="utf-8").splitlines():
        line = line.split()[0] if line.strip() and not line.startswith("//") else ""
        if not line:
            continue
        rule = ".".join(_ascii(p) for p in line.lstrip("!").split("."))
        (exc if line.startswith("!") else wild if rule.startswith("*.") else plain).add(rule[2:] if rule.startswith("*.") else rule)
    return frozenset(plain), frozenset(wild), frozenset(exc)


def _labels(host: str) -> list[str]:
    host = host.strip().lower().rstrip(".")
    return [_ascii(l) for l in host.split(".") if l]


def public_suffix(host: str) -> str:
    """The public suffix of `host` (the longest matching rule; an unlisted TLD is its own suffix)."""
    plain, wild, exc = _rules()
    labels = _labels(host)
    best = 1
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        n = len(labels) - i
        if cand in exc:
            return ".".join(labels[i + 1:])
        if cand in plain:
            best = max(best, n)
        parent = ".".join(labels[i + 1:])
        if parent and parent in wild:
            best = max(best, n)
    return ".".join(labels[-best:])


def is_public_suffix(host: str) -> bool:
    labels = _labels(host)
    return bool(labels) and public_suffix(host) == ".".join(labels)


def registrable_domain(host: str) -> str:
    """eTLD+1 of `host`; the host itself when it is an IP literal or a public suffix (it has no registrable part)."""
    h = host.strip().lower().strip("[]").rstrip(".")
    try:
        ipaddress.ip_address(h)
        return h
    except ValueError:
        pass
    labels = _labels(h)
    if len(labels) < 2:
        return ".".join(labels)
    suffix = public_suffix(h).split(".")
    if len(labels) <= len(suffix):
        return ".".join(labels)
    return ".".join(labels[-(len(suffix) + 1):])
