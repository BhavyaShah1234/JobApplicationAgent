"""A review navigation tab is not Next and no action mentioning Submit is clicked as Next."""
from jobagent.tools import forms
from test_filling_strategy import world


def test_selected_review_tab_does_not_hide_the_real_final_submit(world):
    world.setup('''<h1>Review application</h1><a role=tab aria-selected=true href=# onclick="window.nav=true">Review &amp; submit</a>
    <form><button type=submit onclick="event.preventDefault();window.sent=true">Submit application</button></form>
    <script>window.nav=false;window.sent=false</script>''');world.page.goto('https://fixture.test/apply')
    view=forms.read_page(world.page)
    assert forms.click_button(world.page,view,forms.NEXT) is None
    assert forms.has_submit_input(world.page)
    assert world.page.evaluate('nav||sent') is False


def test_review_tab_is_skipped_in_favor_of_a_real_continue(world):
    world.setup('''<h1>Application</h1><a role=tab href=# onclick="window.nav=true">Review</a>
    <button type=button onclick="window.continued=true">Continue</button><script>window.nav=false;window.continued=false</script>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    assert forms.click_button(world.page,view,forms.NEXT)=='Continue'
    assert world.page.evaluate('continued&&!nav')


def test_review_and_submit_button_cannot_send_through_the_next_path(world):
    world.setup('''<h1>Review application</h1><form><button type=button onclick="window.sent=true">Review &amp; submit</button></form>
    <script>window.sent=false</script>''');world.page.goto('https://fixture.test/apply')
    assert forms.click_button(world.page,forms.read_page(world.page),forms.NEXT) is None
    assert world.page.evaluate('sent') is False
