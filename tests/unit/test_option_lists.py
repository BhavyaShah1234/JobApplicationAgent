"""A combobox's options are the list it owns; the page's own menus are never options (Dayforce: 'Prefix' offered 'Search Jobs, Careers')."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms

PAGE = """<html><body>
<header><nav><a role=menuitem href=#>Search Jobs</a><a role=menuitem href=#>Careers</a></nav></header>
<input id=c role=combobox aria-controls=owned aria-expanded=false readonly placeholder="Prefix">
<div id=owned role=listbox style="display:none"><div role=option>Mr</div><div role=option>Mr</div><div role=option>Mrs</div><div role=option>Dr</div></div>
<input id=other role=combobox aria-controls=other_list readonly>
<div id=other_list role=listbox><div role=option>UNRELATED OPEN LIST</div></div>
<script>document.getElementById('c').addEventListener('click',()=>{document.getElementById('owned').style.display='block'})</script>
</body></html>"""

NO_OWNED = """<html><body><header><nav><a role=menuitem href=#>Search Jobs</a></nav></header>
<input id=c role=combobox readonly><ul id=popup style="display:none"><li role=option>Yes</li><li role=option>No</li></ul>
<script>document.getElementById('c').addEventListener('click',()=>{document.getElementById('popup').style.display='block'})</script></body></html>"""


def chrome():
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    return pw, b, b.new_page()


def test_a_combobox_reads_only_the_list_it_owns_and_never_the_headers_links():
    pw, b, page = chrome()
    try:
        page.set_content(PAGE)
        assert forms._open_options(page.main_frame, page.locator("#c"), page) == ["Mr", "Mrs", "Dr"]
    finally:
        b.close(); pw.stop()


def test_without_an_owned_list_the_page_wide_reader_still_works_but_skips_the_header_menu():
    pw, b, page = chrome()
    try:
        page.set_content(NO_OWNED)
        assert forms._open_options(page.main_frame, page.locator("#c"), page) == ["Yes", "No"]
    finally:
        b.close(); pw.stop()
