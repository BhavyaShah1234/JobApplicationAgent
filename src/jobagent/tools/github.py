"""GitHub through the agent's browser session (login) and the REST API (repos for the resume's Projects section).

Login uses "Continue with Google" with the Google session already in the profile (step 2). If GitHub asks to
verify the device, the code is read from the agent's inbox (github.com is an agent domain) and the mail is trashed
once the login succeeded. Repo discovery uses the public REST API: unauthenticated (60 requests/hour: one per account
plus one per contributed repo; READMEs come from raw.githubusercontent.com; cached for a day) or with a token from the vault (`jobagent vault set github token`), which also unlocks the GraphQL
list of repositories contributed to.
"""

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Page

from jobagent.config import get_settings
from jobagent.tools.browser import plain_chrome, screenshot, type_human
from jobagent.tools.google_auth import BLOCKED as GOOGLE_BLOCKED
from jobagent.tools.hitl import Hitl

BASE, API = "https://github.com", "https://api.github.com"
_DEVICE = re.compile(r"(?i)device verification|verify (your|this) device|we just sent your authentication code|"
                     r"verification code")


class GitHubError(RuntimeError):
    pass


def _meta(page: Page, name: str) -> str:
    loc = page.locator(f'meta[name="{name}"]')
    return (loc.first.get_attribute("content") or "") if loc.count() else ""


def _user_on_page(page: Page) -> str:
    return _meta(page, "user-login") or _meta(page, "octolytics-actor-login")


def current_user(page: Page, wait_ms: int = 3000) -> str:
    """The logged-in username, or '' when logged out (the metas fill in once the dashboard has loaded)."""
    page.goto(BASE, wait_until="domcontentloaded")
    for _ in range(max(1, wait_ms // 500)):
        if user := _user_on_page(page):
            return user
        page.wait_for_timeout(500)
    return _user_on_page(page)


def _capture(page: Page, log) -> tuple[str, str]:
    shot = str(get_settings().state_dir / "logs" / f"github-login-{datetime.now(UTC):%H%M%S}.png")
    screenshot(page, shot)
    excerpt = " ".join(page.locator("body").inner_text(timeout=10_000).split())[:250]
    log(f"github: page '{page.title()}' {urlparse(page.url).netloc}{urlparse(page.url).path} | {excerpt} | {shot}")
    return shot, excerpt


def login_with_google(page: Page, email: str, hitl: Hitl, log=print, open_inbox=None, max_steps: int = 12) -> str:
    """Log in via 'Continue with Google'. Returns how it went; raises GitHubError when it can't."""
    if user := current_user(page):
        return f"already logged in as {user}"
    started = datetime.now(UTC)
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile(r"continue with google", re.I)).first.click()
    used_mail = None
    for _ in range(max_steps):
        page.wait_for_timeout(2500)
        url = urlparse(page.url)
        if url.netloc == "github.com" and not re.match(r"/(login|session)", url.path) and \
                (user := _user_on_page(page) or current_user(page)):
            if used_mail is not None:
                with open_inbox() as inbox:
                    inbox.consume_and_trash(used_mail, "github device verification")
            return f"logged in with Google as {user}"
        body = page.locator("body").inner_text(timeout=10_000)
        if url.netloc == "accounts.google.com":
            if GOOGLE_BLOCKED.search(body):
                raise GitHubError("Google refused the sign-in from the automated browser")
            account = page.get_by_text(email, exact=False)
            if re.search(r"(?i)choose an account", body) and account.count():
                account.first.click()
                log("github: chose the agent's Google account")
                continue
            consent = page.get_by_role("button", name=re.compile(r"^(continue|allow)$", re.I))
            if consent.count():
                consent.first.click()  # share basic profile (name, email) with GitHub
                log("github: confirmed GitHub on Google's consent screen")
                continue
        if url.netloc == "github.com" and _DEVICE.search(body) and used_mail is None and open_inbox is not None:
            code_box = page.locator('input[name="otp"], input#otp, input[autocomplete="one-time-code"]').first
            if code_box.count():
                log("github: device verification; reading the code from the agent's inbox")
                with open_inbox() as inbox:
                    used_mail, v = inbox.wait_for_verification(BASE, started, kind="github")
                if not v.code:
                    raise GitHubError("GitHub's verification mail had no code")
                type_human(code_box, v.code)
                page.keyboard.press("Enter")
                continue
        # Linking the Google account to the GitHub account, 2FA, or anything unexpected: the user decides.
        shot, excerpt = _capture(page, log)
        current = page.url
        hitl.ask(f"GitHub login needs you: {excerpt!r} (screenshot {shot}). Complete it in noVNC "
                 "(http://localhost:6080), then answer 'done'.", done_when=lambda: page.url != current)
    if user := current_user(page):
        return f"logged in with Google as {user} (with help)"
    raise GitHubError("could not log in to GitHub")


def login_by_hand(hitl: Hitl, log=print) -> None:
    """Fallback: the user logs in once in plain (non-automated) Chrome on the agent's profile."""
    with plain_chrome(f"{BASE}/login"):
        hitl.ask("Plain Chrome is open in noVNC (http://localhost:6080) at GitHub. Click 'Continue with Google', "
                 "choose the agent's account, finish any verification, wait for your dashboard, then answer 'done'.")
    log("github: manual login finished")


# --- repositories for the Projects section ------------------------------------------------------------------

@dataclass
class Repo:
    full_name: str
    url: str
    description: str = ""
    language: str = ""
    languages: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    pushed_at: str = ""
    stars: int = 0
    owned: bool = True          # False: someone else's repo the user contributed to
    readme: str = ""
    created_at: str = ""


def _client(token: str | None) -> httpx.Client:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "jobagent"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API, headers=headers, timeout=30, follow_redirects=True)


def _get(c: httpx.Client, path: str, **params) -> dict | list:
    r = c.get(path, params=params)
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError("GitHub API rate limit reached (60/hour without a token); try later or "
                          "`jobagent vault set github token`")
    r.raise_for_status()
    return r.json()


def _readme(full_name: str, limit: int = 6000) -> str:
    """README text from raw.githubusercontent.com (not counted against the API rate limit)."""
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "jobagent"}) as raw:
        for name in ("README.md", "readme.md", "Readme.md", "README.rst", "README.txt", "README"):
            r = raw.get(f"https://raw.githubusercontent.com/{full_name}/HEAD/{name}")
            if r.status_code == 200:
                return r.text[:limit]
    return ""


def _repo(c: httpx.Client, data: dict, owned: bool, token: str | None) -> Repo:
    """From a repo listing/detail object; languages need one API call each, so only with a token."""
    full = data["full_name"]
    langs = list(_get(c, f"/repos/{full}/languages"))[:6] if token else [l for l in [data.get("language")] if l]
    return Repo(full, data["html_url"], data.get("description") or "", data.get("language") or "", langs,
                data.get("topics") or [], data.get("pushed_at") or "", data.get("stargazers_count", 0), owned,
                _readme(full), data.get("created_at") or "")


def contributed_repos(c: httpx.Client, user: str, token: str | None) -> set[str]:
    """Other people's repos the user contributed to: PRs and authored commits (+ GraphQL when there's a token)."""
    names: set[str] = set()
    for item in _get(c, "/search/issues", q=f"author:{user} type:pr -user:{user}", per_page=100).get("items", []):
        names.add(item["repository_url"].split("/repos/", 1)[1])
    commits = c.get("/search/commits", params={"q": f"author:{user} -user:{user}", "per_page": 100})
    if commits.status_code == 200:
        names |= {i["repository"]["full_name"] for i in commits.json().get("items", [])}
    if token:
        q = ("query($u:String!){user(login:$u){repositoriesContributedTo(first:100,contributionTypes:"
             "[COMMIT,PULL_REQUEST,REPOSITORY]){nodes{nameWithOwner}}}}")
        r = c.post("/graphql", json={"query": q, "variables": {"u": user}})
        if r.status_code == 200:
            nodes = ((r.json().get("data") or {}).get("user") or {}).get("repositoriesContributedTo") or {}
            names |= {n["nameWithOwner"] for n in nodes.get("nodes", [])}
    return {n for n in names if n.split("/")[0].lower() != user.lower()}


def discover_repos(user: str, token: str | None = None, refresh: bool = False, log=print) -> list[Repo]:
    """The user's own non-fork repos plus repos they contributed to, with README text. Cached for a day."""
    cache = get_settings().state_dir / "github" / f"{user.lower()}.json"
    if cache.exists() and not refresh:
        data = json.loads(cache.read_text())
        if datetime.fromisoformat(data["fetched_at"]) > datetime.now(UTC) - timedelta(days=1):
            return [Repo(**r) for r in data["repos"]]
    with _client(token) as c:
        listed, page = [], 1
        while batch := _get(c, f"/users/{user}/repos", per_page=100, type="owner", sort="pushed", page=page):
            listed += batch  # one call per 100 repos
            if len(batch) < 100:
                break
            page += 1
        own = [r for r in listed if not r.get("fork")]
        repos = [_repo(c, r, True, token) for r in own]
        for full in sorted(contributed_repos(c, user, token)):
            try:
                repos.append(_repo(c, _get(c, f"/repos/{full}"), False, token))
            except httpx.HTTPStatusError:
                continue  # private or deleted
    log(f"github: {user}: {sum(r.owned for r in repos)} own repos, {sum(not r.owned for r in repos)} contributed")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": datetime.now(UTC).isoformat(), "repos": [asdict(r) for r in repos]},
                                indent=1))
    return repos


def profile_user(tex: str) -> str:
    """The GitHub username linked in the resume header (the account whose repos the resume shows)."""
    m = re.search(r"github\.com/([A-Za-z0-9-]+)", tex)
    return m.group(1) if m else ""
