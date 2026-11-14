"""The three production issues the shared-checkpoint experiment found: upload roles from their own caption, an email address is not a
postal address, and a choice showing its own prompt is not an answer."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobagent import llm
from jobagent.steps import apply as A
from jobagent.tools import forms


def chrome_page(html, routes=None):
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    page = browser.new_page()
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
    page.goto("https://app.test/")
    return pw, browser, page


# --- 1. upload roles ---------------------------------------------------------------------------------------------------------

FLAT = """<h1>Apply</h1><p>Attach your documents below. A cover letter is optional but your resume is required.</p>
<label for=r>Resume</label><input type=file id=r>
<label for=c>Cover Letter</label><input type=file id=c>
<p>Your cover letter should be short; your resume one page.</p>"""


def test_flat_native_uploads_keep_their_own_caption_and_role():
    pw, browser, page = chrome_page(FLAT)
    try:
        uploads = [f for f in forms.read_page(page).fields if f.kind == "file"]
        assert len(uploads) == 2
        by_id = {f.label.split(" / ")[0]: f for f in uploads}
        assert set(by_id) == {"Resume", "Cover Letter"}
        for caption, f in by_id.items():
            assert "documents below" not in f.label and "optional" not in f.label        # no surrounding body text in the caption
        assert A.upload_role(by_id["Resume"].label) == "resume" and A.upload_role(by_id["Cover Letter"].label) == "cover_letter"
    finally:
        browser.close()
        pw.stop()


def test_each_role_receives_its_own_bytes_and_a_mixed_page_with_a_lazy_widget_still_works(tmp_path):
    resume, cover, other = tmp_path / "resume.pdf", tmp_path / "cover_letter.pdf", tmp_path / "portfolio.pdf"
    resume.write_bytes(b"%PDF-1.4 R")
    cover.write_bytes(b"%PDF-1.4 C")
    other.write_bytes(b"%PDF-1.4 P")
    html = FLAT + ("<div class=w><label>Portfolio</label> <button type=button onclick=\"const i=document.createElement('input');"
                   "i.type='file';i.style.display='none';document.body.appendChild(i);i.onchange=()=>{this.nextElementSibling.textContent="
                   "i.files[0].name};i.click()\">Choose file</button><span></span></div>")
    pw, browser, page = chrome_page(html)
    try:
        view = forms.read_page(page)
        files = {"resume": str(resume), "cover_letter": str(cover)}
        for f in [g for g in view.fields if g.kind == "file"]:
            role = A.upload_role(f.label)
            if role:
                forms.fill_field(page, view, f, role, files)
        held = page.evaluate("[...document.querySelectorAll('input[type=file]')].map(e => [e.id, [...e.files].map(f => f.name)])")
        assert dict((i, n) for i, n in held if i) == {"r": ["resume.pdf"], "c": ["cover_letter.pdf"]}      # the roles are not swapped
    finally:
        browser.close()
        pw.stop()


def test_a_role_is_decided_by_the_caption_never_by_surrounding_words():
    assert A.upload_role("Resume") == "resume" and A.upload_role("Cover Letter") == "cover_letter"
    assert A.upload_role("Resume / Attach your documents. A cover letter is optional / file1") == "resume"      # first part decides
    assert A.upload_role("Cover Letter / your resume is required / file2") == "cover_letter"
    assert A.upload_role("Resume and Cover Letter") == ""                                  # naming both: not guessed
    assert A.upload_role("Attachment / Attachment") == "" and A.upload_role("Upload a file") == "resume"
    assert A.upload_role("CV") == "resume" and A.upload_role("Curriculum Vitae") == "resume"


# --- 2. email is not a postal address ----------------------------------------------------------------------------------------

def test_an_email_address_field_is_not_a_postal_address():
    assert not A.is_address("Your email address") and not A.is_address("Email address", "email")
    assert not A.is_address("Website address") and not A.is_address("LinkedIn profile address")
    assert not A.is_address("Phone", "tel")
    for postal in ("Address Line 1", "City", "State", "Postal Code", "Street address", "Current address"):
        assert A.is_address(postal), postal                                               # the real postal fields are unchanged
    assert not A.is_address("Are you a current Federal, State or local government employee?")


def minimal_ctx(prepared=False):
    ctx = {"_applicant": {"Contact": [{"question": "Email address", "answer": "me@example.org"}]}, "REFERRAL": None,
           "_jd": {"company": "Acme"}}
    if prepared:
        ctx["_applicant"]["Contact"].append({"question": "Current address", "answer": {"line1": "1 Main St", "city": "Austin", "postal_code": "78701"}})
        ctx["_application_address"] = {"address": {"city": "Austin", "state": "Texas", "postal_code": "78701", "country": "United States",
                                                   "line1": "1 Main St"}, "state_code": "TX"}
    return ctx


@pytest.mark.parametrize("prepared", [False, True])
def test_a_supplied_email_survives_enforcement_with_and_without_a_prepared_postal_address(prepared):
    email = forms.Field(id=1, kind="email", label="Your email address", required=True)
    line1 = forms.Field(id=2, kind="text", label="Address Line 1", required=True)
    answers = {1: A.Answer(id=1, value="me@example.org", source="applicant"),
               2: A.Answer(id=2, value="1 Main St", source="applicant")}
    A.enforce([email, line1], answers, minimal_ctx(prepared), lambda m: None)
    assert answers[1].value == "me@example.org" and not answers[1].ask_human            # the valid email is neither erased nor asked again
    if not prepared:
        assert answers[2].ask_human and answers[2].value == ""                         # a real postal field still needs the source
    else:
        assert answers[2].value == "1 Main St"                                         # and takes the prepared address when there is one


# --- 3. a required placeholder is not an answer -------------------------------------------------------------------------------

SELECT = """<h1>Contact</h1><label>Preferred contact <select required><option value="choose" selected>Choose</option>
<option>Phone</option><option>Email</option></select></label>"""


def test_a_choice_showing_its_prompt_has_no_value():
    for shown in ("Choose", "choose", "Select...", "-- Select --", "Please select", "Please choose one", "Select an option", "Pick one"):
        assert not forms.has_value(forms.Field(id=1, kind="select", label="x", value=shown)), shown
    for real in ("Phone", "Select Plan Premium", "Choose Wisely LLC"):
        assert forms.has_value(forms.Field(id=1, kind="select", label="x", value=real)), real
    assert forms.has_value(forms.Field(id=1, kind="text", label="x", value="Choose"))      # a text box that holds the word holds it
    assert not forms.has_value(forms.Field(id=1, kind="select", label="x", value=""))


def test_a_required_select_left_at_its_prompt_is_a_blocker_not_ready():
    pw, browser, page = chrome_page(SELECT)
    try:
        f = next(g for g in forms.read_page(page).fields if g.kind == "select")
        assert f.value == "Choose" and f.required                                       # the actual readback is the prompt
        entry = {"filled": "", "answer": "", "verified": False, "required": True, "answered": True, "page": 1, "label": f.label}
        assert A.outcome_of(entry, f) == "unanswered (required)"                         # not "verified (prefilled)"
        entry["outcome"] = A.outcome_of(entry, f)
        blockers = A.blockers_of([entry])
        assert blockers and "required" in blockers[0] and "left empty" in blockers[0]
        ok = forms.Field(id=2, kind="select", label="x", required=True, value="Email")
        assert A.outcome_of({**entry, "answered": True}, ok) == "verified"                # a legitimately prefilled value still counts
    finally:
        browser.close()
        pw.stop()


def test_the_rejected_guess_leads_to_a_question_for_the_missing_preference(monkeypatch):
    """The source check rejects the inferred preference; the select still shows its prompt; the candidate must be asked."""
    f = forms.Field(id=1, kind="select", label="Preferred contact", required=True, value="Choose", options=["Choose", "Phone", "Email"])
    answers = {1: A.Answer(id=1, value="Email", source="applicant", why="inferred from the email on file")}
    monkeypatch.setattr(llm, "structured_invoke", lambda *a, **k: SimpleNamespace(field_1=A.Check(id=1, supported=False, ask_human=True)))
    ctx = {"_applicant": {}, "_jd": {"company": "Acme"}, "JD": "", "_jd_text": "", "APPLICANT": "x", "ANSWERS_FOR_THIS_APPLICATION": "(none)"}
    A.check([f], answers, ctx, object(), lambda m: None)
    assert answers[1].ask_human and answers[1].value == ""                              # the guess was rejected, not kept
    asked = []
    monkeypatch.setattr(A, "consult_learned", lambda *a, **k: None)
    monkeypatch.setattr(A, "ask_human", lambda hitl, field, *rest, **k: asked.append(field.label) or ("answer", "Email"))
    A.resolve([f], answers, ctx, object(), {"hitl": object(), "company": "Acme", "host": "x", "ask_s": 5}, lambda m: None)
    assert asked == ["Preferred contact"]                                               # asked, although the select "has a value"
    assert answers[1].value == "Email" and answers[1].source == "human"


# =================== second review round: tel-typed ZIP, and prompts the control itself marks ===================================

def zip_ctx(prepared):
    address = {"line1": "1 Example Street", "city": "Austin", "state": "Texas", "postal_code": "78701", "country": "United States"}
    ctx = {"_jd": {"company": "Fixture"}, "REFERRAL": {},
           "_applicant": {"Contact": [{"question": "Current address", "seen": ["ZIP Code"], "answer": address if prepared else {}},
                                      {"question": "Phone", "answer": "5551234567"}, {"question": "Email address", "answer": "me@example.org"}]}}
    if prepared:
        ctx["_application_address"] = {"address": address, "state_code": "TX"}
    return ctx


def test_a_postal_code_typed_tel_is_still_postal_and_a_phone_is_still_not():
    for label in ("ZIP Code", "Postal code", "Post Code", "Pincode", "Zip / Postal Code"):
        assert A.is_address(label, "tel") and not A.not_postal(label, "tel"), label
    for label in ("Phone", "Mobile number", "Telephone", "Cell phone", "Fax"):
        assert not A.is_address(label, "tel") and A.not_postal(label, "tel"), label
    assert A.not_postal("Zip code or phone number", "tel")                                    # naming a phone is a phone
    assert A.not_postal("Email", "email") and A.not_postal("Website", "url") and A.not_postal("Your email address", "text")
    assert not A.not_postal("City", "text") and A.is_address("ZIP Code", "text")


def test_a_prepared_zip_replaces_a_stale_one_even_when_the_field_is_tel_typed():
    f = forms.Field(id=0, kind="tel", label="ZIP Code", required=True)
    answers = {0: A.Answer(id=0, value="10001", source="applicant")}
    A.enforce([f], answers, zip_ctx(prepared=True), lambda m: None)
    assert answers[0].value == "78701"


def test_phone_and_email_and_link_answers_are_preserved_beside_a_prepared_zip():
    fields = [forms.Field(id=0, kind="tel", label="ZIP Code"), forms.Field(id=1, kind="tel", label="Phone number"),
              forms.Field(id=2, kind="email", label="Your email address"), forms.Field(id=3, kind="url", label="Website address"),
              forms.Field(id=4, kind="text", label="ZIP Code")]
    answers = {0: A.Answer(id=0, value="10001", source="applicant"), 1: A.Answer(id=1, value="5551234567", source="applicant"),
               2: A.Answer(id=2, value="me@example.org", source="applicant"), 3: A.Answer(id=3, value="https://x.test", source="applicant"),
               4: A.Answer(id=4, value="10001", source="applicant")}
    A.enforce(fields, answers, zip_ctx(prepared=True), lambda m: None)
    assert (answers[0].value, answers[4].value) == ("78701", "78701")                           # both ZIP controls follow the selected address
    assert answers[1].value == "5551234567" and answers[2].value == "me@example.org" and answers[3].value == "https://x.test"


def test_without_postal_evidence_a_zip_is_asked_for_not_invented_and_phones_are_untouched():
    zip_f = forms.Field(id=0, kind="tel", label="ZIP Code", required=True)
    phone = forms.Field(id=1, kind="tel", label="Phone number", required=True)
    answers = {0: A.Answer(id=0, value="10001", source="applicant"), 1: A.Answer(id=1, value="5551234567", source="applicant")}
    A.enforce([zip_f, phone], answers, zip_ctx(prepared=False), lambda m: None)
    assert answers[0].ask_human and answers[0].value == ""                                      # no source: handed to the candidate
    assert answers[1].value == "5551234567" and not answers[1].ask_human


# --- prompts the control itself marks -----------------------------------------------------------------------------------------

def prompt_select(value="placeholder", extra=""):
    return (f'<label for=p>Preferred contact method</label><select id=p required><option value="{value}" disabled selected {extra}>'
            'Select your preferred contact method</option><option>Email</option><option>Phone</option></select>')


@pytest.mark.parametrize("value", ["", "placeholder", "none-chosen"])
def test_a_disabled_selected_question_specific_prompt_is_not_a_prefill(value):
    pw, browser, page = chrome_page(prompt_select(value))
    try:
        f = next(g for g in forms.read_page(page).fields if g.kind == "select")
        assert f.required and f.options == ["Email", "Phone"]                                  # the disabled prompt is not offered as a choice
        entry = {"page": 1, "label": f.label, "required": True, "filled": "", "answer": "", "verified": False, "answered": True}
        entry["outcome"] = A.outcome_of(entry, f)
        assert not forms.has_value(f), (value, f.value, f.prompt)
        assert A.blockers_of([entry]), entry["outcome"]                                        # a missing required preference blocks
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("attr", ["hidden", "data-placeholder"])
def test_a_hidden_or_marked_prompt_option_is_also_not_a_value(attr):
    html = ('<label for=p>Contact</label><select id=p required><option value="x" selected ' + attr + '>Tell us how to reach you</option>'
            '<option>Email</option></select>')
    pw, browser, page = chrome_page(html)
    try:
        f = next(g for g in forms.read_page(page).fields if g.kind == "select")
        assert f.prompt and not forms.has_value(f)
    finally:
        browser.close()
        pw.stop()


def test_real_prefilled_choices_and_generic_prompts_keep_working():
    pw, browser, page = chrome_page('<label for=a>Plan</label><select id=a required><option>Select Plan Premium</option><option>Basic</option></select>'
                                    '<label for=b>Owner</label><select id=b required><option selected>Choose Wisely LLC</option><option>Other</option></select>'
                                    '<label for=c>Reach</label><select id=c required><option value="choose" selected>Choose</option><option>Email</option></select>'
                                    '<label for=d>Day</label><select id=d required><option disabled>Pick</option><option selected>Monday</option></select>')
    try:
        fields = {g.label: g for g in forms.read_page(page).fields if g.kind == "select"}
        assert forms.has_value(fields["Plan"]) and forms.has_value(fields["Owner"])             # legitimate values that begin with Select/Choose
        assert not forms.has_value(fields["Reach"])                                              # the generic prompt, as before
        assert forms.has_value(fields["Day"]) and fields["Day"].value == "Monday"                # a disabled option that is NOT selected changes nothing
        entry = {"page": 1, "label": "Plan", "required": True, "filled": "", "answer": "", "verified": False, "answered": True}
        assert A.outcome_of(entry, fields["Plan"]) == "verified"
    finally:
        browser.close()
        pw.stop()


def test_the_rejected_guess_for_a_question_specific_prompt_is_asked_by_the_real_hitl_path(monkeypatch):
    f = forms.Field(id=1, kind="select", label="Preferred contact method", required=True, prompt=True,
                    value="Select your preferred contact method", options=["Email", "Phone"])
    answers = {1: A.Answer(id=1, value="Email", source="applicant", why="inferred")}
    monkeypatch.setattr(llm, "structured_invoke", lambda *a, **k: SimpleNamespace(field_1=A.Check(id=1, supported=False, ask_human=True)))
    ctx = {"_applicant": {}, "_jd": {"company": "Acme"}, "JD": "", "_jd_text": "", "APPLICANT": "x", "ANSWERS_FOR_THIS_APPLICATION": "(none)"}
    A.check([f], answers, ctx, object(), lambda m: None)
    assert answers[1].ask_human and answers[1].value == ""
    asked = []
    monkeypatch.setattr(A, "consult_learned", lambda *a, **k: None)
    monkeypatch.setattr(A, "ask_human", lambda hitl, field, *rest, **k: asked.append(field.label) or ("answer", "Phone"))
    A.resolve([f], answers, ctx, object(), {"hitl": object(), "company": "Acme", "host": "x", "ask_s": 5}, lambda m: None)
    assert asked == ["Preferred contact method"] and answers[1].value == "Phone"
