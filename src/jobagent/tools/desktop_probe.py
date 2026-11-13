"""Legacy Carvana feasibility probes and a compatibility desktop broker command."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from jobagent.tools import challenge
from jobagent.tools.desktop_browser import agent_profile_root, process_alive, process_arguments


HOSTS = {"www.carvana.com", "carvana.com"}


def allowed(url):
    p = urlsplit(url)
    return (p.scheme == "https" and p.hostname in HOSTS and p.port in (None, 443)
            and not p.username and not p.password and p.path.startswith("/careers/"))


def launch(root: Path, url: str):
    if not allowed(url):
        raise ValueError("Only an approved Carvana careers URL can be opened")
    root.mkdir(parents=True, exist_ok=True)
    profile = (root / "profile").resolve()
    manifest = root / "session.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if process_alive(previous['pid']):
            raise RuntimeError("Dedicated browser is already running; do not replace its session")
    chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    if not chrome:
        raise RuntimeError("Install desktop Chrome before authorizing the manual test")
    # A fresh dedicated profile only; never point remote debugging at an existing personal profile.
    if profile.exists() and not manifest.exists():
        raise RuntimeError("Unrecognized profile directory; refusing to use it")
    port = 9223
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))  # refuse an occupied debugging port
    command = [chrome, f'--user-data-dir={profile}', '--remote-debugging-address=127.0.0.1',
               f'--remote-debugging-port={port}', '--no-first-run', '--no-default-browser-check', url]
    with (root / 'chrome.log').open('ab') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
    manifest.write_text(json.dumps({'pid': process.pid, 'profile': str(profile), 'port': port,
                                    'url': url, 'stage': 'manual'}, indent=2))
    os.chmod(manifest, 0o600)
    return {'state': 'manual', 'message': 'Solve verification yourself. Agent has not attached.'}


def validate_process(root, manifest):
    expected = str((root / 'profile').resolve())
    if manifest['profile'] != expected or manifest['port'] != 9223:
        raise RuntimeError('Browser identity mismatch')
    proc = Path(f"/proc/{manifest['pid']}")
    command = process_arguments((proc / 'cmdline').read_bytes())
    if f'--user-data-dir={expected}' not in command or '--remote-debugging-port=9223' not in command:
        raise RuntimeError('Refusing to attach to another browser process')
    lock = Path(expected) / 'SingletonLock'
    if (proc / 'exe').resolve().name not in {'chrome', 'chromium', 'chromium-browser'} or \
            not lock.is_symlink() or os.readlink(lock).rsplit('-', 1)[-1] != str(manifest['pid']):
        raise RuntimeError('Dedicated Chrome executable or profile lock does not match')


def check_scope(browser, selected, expected_url=None):
    pages = [p for context in browser.contexts for p in context.pages]
    if len(pages) != 1 or selected not in pages or any(not allowed(p.url) for p in pages):
        raise RuntimeError('Unexpected tab or domain; probe stopped')
    if expected_url is not None and selected.url != expected_url:
        raise RuntimeError('Unexpected navigation; probe stopped')
    for frame in selected.frames:
        host = urlsplit(frame.url).hostname
        if host and host not in HOSTS | {'boards.greenhouse.io', 'job-boards.greenhouse.io',
                                         'challenges.cloudflare.com', 'www.google.com', 'www.gstatic.com'}:
            raise RuntimeError('Unapproved frame domain; probe stopped')


def check_pages(browser, selected, expected_url=None):
    check_scope(browser, selected, expected_url)
    if challenge.detect(selected) is not None:
        raise RuntimeError('Application is not ready; human verification required')


def serve(root):
    """Compatibility entry point for the generalized desktop application broker."""
    from jobagent.tools.desktop_browser import serve as serve_applications
    return serve_applications(root)


def probe(root: Path, stage: str, field_id: str = '', value: str = ''):
    manifest_path = root / 'session.json'
    manifest = json.loads(manifest_path.read_text())
    validate_process(root, manifest)
    if stage == 'field' and manifest.get('stage') != 'read_verified':
        raise RuntimeError('The read-only stage must succeed before a separately authorized field test')
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{manifest['port']}")
        # Never return the browser/page to general agents or close the human-owned browser.
        pages = [p for c in browser.contexts for p in c.pages if p.url == manifest['url']]
        if len(pages) != 1:
            raise RuntimeError('Expected application tab is missing or ambiguous')
        page = pages[0]
        check_pages(browser, page, manifest['url'])
        page.screenshot(path=str(root / f'{stage}-before.png'))
        if stage == 'field':
            if not field_id:
                raise ValueError('A single agreed input id is required')
            inputs = page.locator('input').all()
            matches = [e for e in inputs if e.get_attribute('id') == field_id]
            if len(matches) != 1:
                raise RuntimeError('Agreed field is missing or ambiguous')
            field = matches[0]
            if (field.get_attribute('type') or 'text').lower() not in {'text', 'email', 'tel'} or \
                    not field.is_visible() or not field.is_enabled() or not field.is_editable():
                raise RuntimeError('Only a visible, editable ordinary text field is permitted')
            check_pages(browser, page, manifest['url'])
            field.fill(value)  # the only mutating operation exposed by this probe
        elif stage != 'read':
            raise ValueError('Unknown probe stage')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            check_pages(browser, page, manifest['url'])
            page.wait_for_timeout(2000)
        page.screenshot(path=str(root / f'{stage}-after.png'))
        fields = page.locator('input:visible:not([type=password]):not([type=hidden]), textarea:visible').evaluate_all(
            "es => es.map(e=>({id:e.id,type:e.type,name:e.name,disabled:e.disabled}))")
        manifest['stage'] = 'read_verified' if stage == 'read' else 'field_verified'
        manifest_path.write_text(json.dumps(manifest, indent=2))
        result = {'state': manifest['stage'], 'fields': fields, 'url': challenge.safe_url(page.url)}
        (root / f'{stage}-result.json').write_text(json.dumps(result, indent=2))
        return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['manual', 'read', 'field', 'serve'])
    parser.add_argument('--authorized-stage', required=True)
    parser.add_argument('--url', default='https://www.carvana.com/careers/apply?gh_jid=8188615')
    parser.add_argument('--field-id', default='')
    parser.add_argument('--value', default='')
    args = parser.parse_args()
    if args.authorized_stage != args.stage:
        parser.error('Separate owner authorization for this exact stage is required')
    root = Path.cwd() / 'state' / 'carvana-desktop'
    if args.stage == 'serve':
        serve(root)
        return
    result = launch(root, args.url) if args.stage == 'manual' else probe(root, args.stage, args.field_id, args.value)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
