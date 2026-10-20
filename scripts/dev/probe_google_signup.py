"""Dev probe: open Google's signup page and dump its form fields (no data is submitted)."""

import json

from jobagent.config import get_settings
from jobagent.tools.browser import persistent_context, screenshot

with persistent_context() as ctx:
    page = ctx.pages[0]
    page.goto("https://accounts.google.com/signup", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    fields = page.evaluate("""() => [...document.querySelectorAll('input,select,button')]
        .filter(e => e.offsetParent !== null)
        .map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id,
                    label: e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.innerText?.slice(0, 40)}))""")
    print("URL:", page.url)
    print("TITLE:", page.title())
    print(json.dumps(fields, indent=1))
    screenshot(page, str(get_settings().state_dir / "logs" / "probe-google-signup.png"))
