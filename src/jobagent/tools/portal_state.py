"""Forget one portal's local browser state: cookies, local and session storage, IndexedDB, caches and service workers.

For a portal whose session went stale (a registration left half-done, a cookie that keeps a page on the wrong step, a stuck
single-page app). It is scoped to the portal's own registrable domain(s) and never touches any other site (above all not Google,
the agent's mail, or the desktop browser's separate profile).

What it does NOT do, on purpose: lift a rate limit or a lockout. Those belong to the network and the email address on the portal's
server, not to this browser, so clearing state does not remove them and is never run automatically when a portal says "too many
attempts" or "temporarily restricted": that would only hide a retry from the site. It is a deliberate command (`jobagent portal-forget`
or `apply --forget-portal`). The cross-run attempt counter is separate and only cleared with `--attempts`.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from jobagent import db

# Never cleared, whoever asks: the agent's own mail and sign-in provider sessions.
PROTECTED = ("google.com", "gmail.com", "googleusercontent.com", "gstatic.com", "accounts.google.com")
STORAGE_TYPES = "cookies,local_storage,session_storage,indexeddb,cache_storage,service_workers,file_systems,websql,appcache"


def portal_hosts(url: str, known: list[str] | None = None) -> list[str]:
    """The portal's hosts: the URL's own, plus hosts the portal notes recorded that share its registrable domain."""
    host = (urlparse(url).netloc or url).lower().split(":")[0]
    base = db.registrable_domain(f"https://{host}")
    if not host or not base or any(base == p or base.endswith("." + p) for p in PROTECTED):
        return []
    hosts = {host}
    for other in known or []:
        other = (other or "").lower()
        if other and db.registrable_domain(f"https://{other}") == base:
            hosts.add(other)
    return sorted(hosts)


def forget_portal(page, url: str, known: list[str] | None = None) -> dict:
    """Clear the portal's cookies and site storage in this browser. Returns what was done, for the log."""
    import re
    hosts = portal_hosts(url, known)
    if not hosts:
        return {"cleared": [], "refused": f"{url!r} is not a portal domain this command may clear"}
    base = db.registrable_domain(f"https://{hosts[0]}")
    ctx = page.context
    try:
        before = len([c for c in ctx.cookies() if re.search(rf"(^|\.){re.escape(base)}$", c.get("domain", "").lstrip("."))])
    except Exception:
        before = None
    ctx.clear_cookies(domain=re.compile(rf"(^|\.){re.escape(base)}$"))
    cleared, errors = [], []
    try:
        cdp = ctx.new_cdp_session(page)
    except Exception as exc:
        cdp = None
        errors.append(f"no storage session ({type(exc).__name__})")
    for host in hosts:
        if cdp is None:
            break
        try:
            cdp.send("Storage.clearDataForOrigin", {"origin": f"https://{host}", "storageTypes": STORAGE_TYPES})
            cleared.append(host)
        except Exception as exc:
            errors.append(f"{host}: {type(exc).__name__}")
    return {"domain": base, "cookies_before": before, "cleared": cleared, "errors": errors}


def clear_attempts(host: str) -> bool:
    """Forget the cross-run password-attempt count for a host (the owner's explicit decision: the cap exists to protect the account)."""
    from jobagent.tools.portal_agent import _rejections_file
    path = _rejections_file(host)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def describe(report: dict) -> str:
    """Summarize the portal-state reset result and any errors."""
    if report.get("refused"):
        return f"refused: {report['refused']}"
    return (f"forgot {report['domain']}: {report['cookies_before']} cookies and the site storage of "
            f"{', '.join(report['cleared']) or 'no host'}" + (f" (problems: {json.dumps(report['errors'])})" if report["errors"] else ""))
