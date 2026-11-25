"""A parse reload needs byte evidence AND its own fresh, successful server receipt."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jobagent.filling.uploads import UploadProof


URL='https://icims.test/jobs/1/candidate'


def profile(name='resume.pdf', *, initial=False, flag=True, error=False):
    return ('<form id=profileForm method=post enctype="multipart/form-data" action="'+URL+
            ('?uploadResume=1' if flag else '')+'">'
            '<input type=hidden id="PortalProfileFields.Resume" value=false>'
            '<input type=hidden id="PortalProfileFields.Resume_FileName" value="'+name+'">'
            '<input type=hidden id="PortalProfileFields.Resume_URL" value="">'
            '<div id="PortalProfileFields.Resume_Content"><span>'+name+'</span>'
            '<label for="PortalProfileFields.Resume_File">Resume</label>'
            '<input type=file name="PortalProfileFields.Resume_File" id="PortalProfileFields.Resume_File">'
            '<button type=button>Replace Resume</button></div>'
            + ('<div role=alert>Upload failed</div>' if error else '') + '</form>'
            '<script>window.icimsAllowResumeAutosubmit=true;'
            + ('document.querySelector("input[type=file]").onchange=()=>setTimeout(()=>profileForm.submit(),350);' if initial else '')
            + '</script>')


@pytest.fixture
def env(tmp_path):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page()
        path=tmp_path/'resume.pdf';path.write_bytes(b'%PDF-1.4 actual source bytes')
        yield page,path
        browser.close()


@pytest.mark.parametrize('case',['success','wrong_bytes','wrong_filename','http_error','other_route','no_parse_flag','visible_error'])
def test_parse_receipt_is_bound_to_dispatched_bytes_frame_route_and_server_result(env,case):
    page,path=env
    def route(r):
        if r.request.url.endswith('/shell'):
            r.fulfill(content_type='text/html',body=f'<iframe src="{URL}"></iframe>')
        elif r.request.method=='POST' or r.request.url.endswith('/other'):
            r.fulfill(status=500 if case=='http_error' else 200,content_type='text/html',
                      body=profile('different.pdf' if case=='wrong_filename' else 'resume.pdf',error=case=='visible_error'))
        else:
            r.fulfill(content_type='text/html',body=profile(initial=True,flag=case!='no_parse_flag'))
    page.route('https://icims.test/**',route)
    page.goto('https://icims.test/shell')
    frame=page.frames[1];frame.wait_for_load_state('domcontentloaded')
    if case=='other_route':frame.evaluate("profileForm.action='https://icims.test/other?uploadResume=1'")
    native=frame.locator('[id="PortalProfileFields.Resume_File"]')
    proof=UploadProof(path,native);proof.bind(native)
    actual=path
    if case=='wrong_bytes':
        actual=path.parent/'other'/'resume.pdf';actual.parent.mkdir();actual.write_bytes(b'%PDF-1.4 wrong same-named bytes')
    try:
        with frame.expect_navigation(wait_until='domcontentloaded'):
            native.set_input_files(str(actual))
            assert proof.verify() is (case!='wrong_bytes')
        assert proof.verify() is (case=='success')
        if case=='success':
            assert proof.method=='byte-verified dispatch and fresh resume-parse POST receipt'
            assert frame.locator('[id="PortalProfileFields.Resume_File"]').evaluate('e=>e.files.length')==0
            assert proof.can_rebind(frame.locator('[id="PortalProfileFields.Resume_File"]'))
            # A later navigation to identical metadata does not reuse the receipt.
            frame.goto(URL)
            assert not proof.verify()
    finally:
        proof.close()


def test_unchanged_old_filename_without_a_parse_response_is_not_a_receipt(env):
    page,path=env
    page.route('https://icims.test/**',lambda r:r.fulfill(content_type='text/html',body=profile()))
    page.goto(URL);native=page.locator('[id="PortalProfileFields.Resume_File"]')
    proof=UploadProof(path,native);proof.bind(native)
    try:
        native.set_input_files(str(path));assert proof.verify()
        native.evaluate("e=>e.value=''")
        assert not proof.verify()
    finally:proof.close()


def test_filler_retains_receipt_and_binding_after_embedded_profile_parse(env):
    from jobagent.filling.planned import PlannedFiller
    page,path=env
    posts=[]
    def route(r):
        if r.request.url.endswith('/shell'):
            r.fulfill(content_type='text/html',body=f'<iframe src="{URL}"></iframe>')
        elif r.request.method=='POST':
            posts.append(1);r.fulfill(content_type='text/html',body=profile())
        else:r.fulfill(content_type='text/html',body=profile(initial=True))
    page.route('https://icims.test/**',route);page.goto('https://icims.test/shell')
    filler=PlannedFiller([],lambda _: {})
    result=filler.fill(page,files={'resume':str(path)},max_rounds=3,advance_pages=False)
    try:
        assert result['state']=='page_complete',result['blockers']
        assert len(posts)==1
        rows=[r for r in result['records'] if r.get('kind')=='file' and r.get('outcome')=='verified']
        assert rows and rows[-1]['readback']=='resume.pdf'
        assert rows[-1]['upload_verification']=='byte-verified dispatch and fresh resume-parse POST receipt'
    finally:
        for proof in filler.upload_proofs.values():proof.close()
