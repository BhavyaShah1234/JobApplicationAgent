"""Discovery must use a realistic event-capable page double, without a browser."""
from types import SimpleNamespace

from jobagent.filling.discovery import Discovery
from page_fakes import EventPage


def test_page_double_supports_discovery_events_and_cleanup():
    page = EventPage()
    discovery = Discovery(page)
    assert page.listeners['requestfinished'] == [discovery.received]
    response = SimpleNamespace(
        headers={'content-type': 'application/json'},
        text=lambda: '{"questions":[{"label":"Question from response","type":"text"}]}',
    )
    page.emit('requestfinished', SimpleNamespace(response=lambda: response))
    assert discovery.delivered[0]['label'] == 'Question from response'
    discovery.close()
    assert page.listeners['requestfinished'] == []
    page.emit('requestfinished', SimpleNamespace(response=lambda: response))
    assert discovery.responses == 1
