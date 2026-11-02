"""Step 11 pure logic: generated passwords, platform detection, Workday site root."""

import re

from jobagent.tools import portal_auth as PA


def test_password_meets_usual_portal_rules():
    for _ in range(50):
        p = PA.new_password()
        assert len(p) == 18
        assert re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"\d", p) and re.search(r"[!@#$%^*]", p)
    assert PA.new_password() != PA.new_password()


def test_platform_and_workday_site_root():
    url = ("https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced/job/San-Jose/"
           "XMLNAME-2027-University-Graduate---Machine-Learning-Engineer_R172085")
    assert PA.platform_of(url) == "workday"
    assert PA._wd_site(url) == "https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced"
    assert PA.platform_of("https://careers-acme.icims.com/jobs/123/x/job") == "icims"
    assert PA.platform_of("https://job-boards.greenhouse.io/doordashusa/jobs/1") == "unknown"
