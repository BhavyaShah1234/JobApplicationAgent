"""An invisible cookie overlay is still an ordinary pointer target, not a bot challenge."""
import pytest
from jobagent.tools.notices import dismiss_cookie_overlay
from test_filling_strategy import world


def html(handler="document.getElementById('truste-consent-button').click()",label='consent banner',disabled=False):
    return f'''<button id=underlying onclick="window.sent=true">Sign In</button>
      <div id=consent_blackbar role=dialog aria-label="{label}">
       <button id=truste-consent-button style="display:none" {'disabled' if disabled else ''}
        onclick="document.getElementById('consent_blackbar').remove()">Understood</button>
       <div id=trustarc-banner-overlay onclick="{handler}" style="position:fixed;inset:0;opacity:0;z-index:10"></div>
      </div><script>window.sent=false</script>'''


def test_a_transparent_cookie_overlay_is_dismissed_by_its_native_handler(world):
    world.setup(html());world.page.goto('https://fixture.test/apply')
    assert dismiss_cookie_overlay(world.page)
    assert world.page.locator('#trustarc-banner-overlay').count()==0
    assert not world.page.evaluate('window.sent')
    assert not dismiss_cookie_overlay(world.page)


@pytest.mark.parametrize('handler,label,disabled',[
    ('window.sent=true','consent banner',False),
    ("document.getElementById('truste-consent-button').click()",'Security verification',False),
    ("document.getElementById('truste-consent-button').click()",'consent banner',True),
])
def test_other_handlers_challenges_and_disabled_dismissals_are_never_clicked(world,handler,label,disabled):
    world.setup(html(handler,label,disabled));world.page.goto('https://fixture.test/apply')
    assert not dismiss_cookie_overlay(world.page)
    assert not world.page.evaluate('window.sent')
    assert world.page.locator('#trustarc-banner-overlay').count()==1
