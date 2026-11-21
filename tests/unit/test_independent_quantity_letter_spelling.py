"""Count provenance must permit natural word/digit spelling without inventing a new figure."""
import pytest
from jobagent.tools import cover_letter as C
from jobagent.state import JobDescription

@pytest.mark.parametrize('source,claim',[
 ('Built twelve REST endpoints with FastAPI.','I built 12 REST endpoints with FastAPI.'),
 ('Completed Docker builds in forty seconds.','I completed Docker builds in 40 seconds.'),
])
def test_letter_accepts_same_source_quantity_when_words_become_digits(source,claim):
    resume='Engineer at Atlas Systems LLC. '+source
    bank={'employment:0':{'entity':'Atlas Systems LLC','kind':'employment','text':resume}}
    letter=C.Letter(salutation='Dear Hiring Team,',closing='Sincerely',paragraphs=[C.Paragraph(text=t,evidence=[]) for t in ('I am interested.', 'At Atlas Systems LLC, '+claim, 'Thank you.')])
    assert C.consistency(letter,resume,JobDescription(title='Engineer',company='Example'),'',source_bank=bank)==[]
