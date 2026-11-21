"""Manual source-construction review: a short caveat must not disappear from its factual context."""
from jobagent.tools import projects as P


def test_a_short_simulation_qualifier_cannot_be_dropped_from_a_functional_claim():
    fact = ("Built a Python robotics service that sends motion commands to physical warehouse robots, "
            "routes operator requests through authenticated control endpoints, and coordinates navigation "
            "goals while recording robot status and command acknowledgements for audit.")
    caveat = "It is only simulated."
    own = {"full_name": "owner/prototype", "url": "https://github.com/owner/prototype",
           "title": "Robotics Prototype", "summary": fact + " " + caveat,
           "results": [], "tech_stack": ["Python"]}
    entry = P.from_card(own)
    if entry is None:
        return  # a qualifier-dependent fact may be conservatively excluded; publishing it unqualified is not safe
    for text in (entry.paragraph, entry.short):
        if text and fact in text:
            assert caveat in text
