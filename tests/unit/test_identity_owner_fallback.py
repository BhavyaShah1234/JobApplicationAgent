"""Read-only-review regression: owner-template identity must also win provider fallback."""
import pytest
from test_project_identity_dedup import A, A_COPY, IMAGES, wire, run, repos, picks

@pytest.mark.parametrize('outage',[True,False])
def test_provider_fallback_prefers_the_owner_template_representative(monkeypatch,outage):
    wire(monkeypatch,[A_COPY,A,IMAGES],response=None if outage else picks(A_COPY,A_COPY),known={'owner/pulsepanel':('PulsePanel, Owner Showcase','January 2026')})
    chosen=run()
    assert repos(chosen)==['owner/pulsepanel','owner/imageatlas']
    assert chosen.entries[0].title=='PulsePanel, Owner Showcase'


def test_an_unusable_owner_card_does_not_hide_a_usable_collaborator(monkeypatch):
    unusable={**A,'summary':'The prototype '+('records timestamped observations for operators, '*30)+'and provides a read-only dashboard.','results':[]}
    wire(monkeypatch,[unusable,A_COPY,IMAGES],known={'owner/pulsepanel':('PulsePanel, Owner Showcase','January 2026')})
    chosen=run()
    assert set(repos(chosen))=={'collaborator/pulsepanel','owner/imageatlas'}
