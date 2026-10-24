"""Step 4b: GitHub login (optional) and repo discovery for the resume's Projects section."""

from datetime import UTC, datetime

from jobagent import db
from jobagent.config import get_settings
from jobagent.tools import github
from jobagent.tools.browser import persistent_context
from jobagent.tools.hitl import Hitl, open_hitl
from jobagent.tools.inbox import open_inbox
from jobagent.tools.vault import Vault, open_vault


def setup(vault: Vault | None = None, hitl: Hitl | None = None, log=print) -> str:
    """Log in with the agent's Google session; returns the GitHub username."""
    vault, hitl = vault or open_vault(), hitl or open_hitl(log)
    email = (vault.get("gmail") or {}).get("email", "")
    db.record_agent_domain(db.connect(), github.BASE, "github")  # device-verification mail comes from github.com
    with persistent_context() as ctx:
        page = ctx.pages[0]
        try:
            how = github.login_with_google(page, email, hitl, log, open_inbox=open_inbox)
        except github.GitHubError as e:
            log(f"github: {e}; handing the login to you in plain Chrome")
            how = None
    if how is None:
        github.login_by_hand(hitl, log)
        how = "logged in by hand"
    with persistent_context() as ctx:
        user = github.current_user(ctx.pages[0])
    if not user:
        raise github.GitHubError("still not logged in to GitHub")
    vault.set("github", login="google", email=email, username=user,
              checked=datetime.now(UTC).isoformat(timespec="seconds"))
    log(f"github: {how}")
    return user


def check(log=print) -> list[tuple[str, bool, str]]:
    """Session still logged in; repo discovery for the resume's GitHub account works and finds its projects."""
    results: list[tuple[str, bool, str]] = []
    entry = open_vault().get("github") or {}
    with persistent_context() as ctx:
        user = github.current_user(ctx.pages[0])
    results.append(("github session", bool(user), f"logged in as {user}" if user else
                    "not logged in — run `jobagent step github`"))
    template = get_settings().data_dir / "templates" / "resume.tex"
    resume_user = github.profile_user(template.read_text()) if template.exists() else ""
    users = list(dict.fromkeys(u for u in (resume_user, user or entry.get("username", "")) if u))
    if not users:
        return results + [("repos", False, "no GitHub username (resume header or session)")]
    repos: dict[str, github.Repo] = {}
    for who in users:  # the resume's account and the agent-login account may differ
        try:
            found = github.discover_repos(who, entry.get("token"), refresh=True, log=log)
        except Exception as e:
            results.append((f"repos of {who}", False, f"{type(e).__name__}: {e}"))
            continue
        own, other = [r for r in found if r.owned], [r for r in found if not r.owned]
        results.append((f"repos of {who}", True, f"{len(own)} own, {len(other)} contributed "
                        f"({', '.join(r.full_name for r in other) or 'none'}), "
                        f"{sum(bool(r.readme) for r in found)} with README"))
        repos.update({r.full_name.lower(): r for r in found})
    if template.exists():  # every repo the template already links must be discoverable
        import re
        linked = set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)", template.read_text()))
        found = set(repos)
        missing = sorted(l for l in linked if l.lower() not in found)
        results.append(("template's project repos found", not missing,
                        f"{len(linked) - len(missing)}/{len(linked)}" + (f"; missing {missing}" if missing else "")))
    return results
