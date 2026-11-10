import re

from jobagent.tools import matching as M

CONFIG = {
    "semantic_threshold": 0.3,
    "global_exclude_patterns": [r"(?i)\b(senior|sr\.?|staff|principal|lead|director|manager|head of|vp)\b"],
    "roles": [
        {"id": "swe", "title": "Software Engineer", "priority": 3,
         "patterns": [r"(?i)\bsoftware (engineer|developer)\b", r"(?i)\bSWE\b"], "exclude_patterns": [],
         "description": "software engineering backend full-stack services api python java go cloud developer"},
        {"id": "ml", "title": "Machine Learning Engineer", "priority": 3,
         "patterns": [r"(?i)\b(machine learning|ML|AI) (engineer|scientist)\b"], "exclude_patterns": [r"(?i)\bsales\b"],
         "description": "machine learning ai llm model training evaluation deploying pytorch mlops research"},
        {"id": "data", "title": "Data Scientist", "priority": 2,
         "patterns": [r"(?i)\bdata (scientist|analyst|engineer)\b"], "exclude_patterns": [],
         "description": "data science analytics statistics sql dashboards pipelines modeling insights"},
    ],
}

# title -> expected verdict with no model judge: regex hit = queued, semantic fit = needs_review, else skipped
LISTINGS = [
    ("Software Engineer", "queued"), ("Software Engineer I", "queued"), ("Software Developer, Backend", "queued"),
    ("Entry-Level Software Engineer", "queued"), ("New Grad SWE", "queued"), ("Software Engineer II, Payments", "queued"),
    ("Machine Learning Engineer", "queued"), ("AI Engineer", "queued"), ("ML Scientist", "queued"),
    ("Machine Learning Engineer, Perception", "queued"), ("AI Scientist (Early Career)", "queued"),
    ("Data Scientist", "queued"), ("Data Analyst", "queued"), ("Data Engineer I", "queued"),
    ("Junior Data Scientist, Marketing", "queued"), ("Software Engineer, AI Services", "queued"),
    ("Software Engineer - Infrastructure", "queued"), ("Associate Data Analyst", "queued"),
    ("Software Engineer Intern", "queued"), ("Software Developer in Test", "queued"),
    ("Data Engineer, Platform", "queued"), ("AI Engineer, Applied", "queued"), ("SWE Intern", "queued"),
    ("Machine Learning Scientist I", "queued"), ("Data Scientist II", "queued"),
    ("Senior Software Engineer", "skipped"), ("Staff Machine Learning Engineer", "skipped"),
    ("Principal Data Scientist", "skipped"), ("Engineering Manager, Software", "skipped"),
    ("Lead Software Engineer", "skipped"), ("Director of Data Science", "skipped"),
    ("Sr. Software Engineer", "skipped"), ("Head of Machine Learning", "skipped"),
    ("VP, Engineering", "skipped"), ("Machine Learning Sales Engineer", "skipped"),
    ("Account Executive", "skipped"), ("Registered Nurse", "skipped"), ("Barista", "skipped"),
    ("Warehouse Associate", "skipped"), ("Marketing Coordinator", "skipped"), ("Financial Analyst", "skipped"),
    ("Truck Driver", "skipped"), ("HR Business Partner", "skipped"), ("Recruiter", "skipped"),
    ("Customer Support Specialist", "skipped"), ("Mechanical Engineer", "skipped"),
    ("Civil Engineer", "skipped"), ("Graphic Designer", "skipped"), ("Store Manager", "skipped"),
    ("Product Manager", "skipped"), ("Office Administrator", "skipped"), ("Electrician", "skipped"),
]


def embed(texts):
    """A word-overlap embedding, enough to test the threshold stage without a model."""
    vocab = {}
    out = []
    for t in texts:
        v = {}
        for w in re.findall(r"[a-z]+", t.lower()):
            if len(w) > 2:
                v[vocab.setdefault(w, len(vocab))] = 1.0
        out.append(v)
    dim = len(vocab) + 1
    return [[v.get(i, 0.0) for i in range(dim)] for v in out]


def test_labelled_listings_precision_and_recall():
    assert len(LISTINGS) >= 50
    got = {t: M.match_listing(t, "", CONFIG).verdict for t, _ in LISTINGS}
    wrong = [(t, want, got[t]) for t, want in LISTINGS if got[t] != want]
    assert not wrong, wrong
    wanted = [t for t, v in LISTINGS if v == "queued"]
    queued = [t for t, g in got.items() if g == "queued"]
    precision = len([t for t in queued if t in wanted]) / len(queued)
    recall = len([t for t in wanted if t in queued]) / len(wanted)
    assert precision == 1.0 and recall == 1.0


def test_semantic_stage_needs_the_threshold():
    # no pattern hit, but the snippet reads like the data role: a candidate for review, not an automatic queue
    m = M.match_listing("Insights Specialist", "sql dashboards statistics analytics pipelines modeling", CONFIG, embed)
    assert (m.verdict, m.stage, m.role_id) == ("needs_review", "semantic", "data")
    far = M.match_listing("Forklift Operator", "warehouse safety shifts", CONFIG, embed)
    assert far.verdict == "skipped" and far.stage == "none"
    high = {**CONFIG, "semantic_threshold": 0.95}
    assert M.match_listing("Insights Specialist", "sql dashboards statistics", high, embed).verdict == "skipped"


def test_the_judge_sets_accept_review_and_skip_bands():
    def judge_with(conf, match=True):
        return lambda title, snippet, role, prior: (match, conf, "because")
    cfg = CONFIG
    assert M.match_listing("Software Engineer", "", cfg, judge=judge_with(0.9)).verdict == "queued"
    assert M.match_listing("Software Engineer", "", cfg, judge=judge_with(0.7)).verdict == "queued"
    assert M.match_listing("Software Engineer", "", cfg, judge=judge_with(0.6)).verdict == "needs_review"
    assert M.match_listing("Software Engineer", "", cfg, judge=judge_with(0.4)).verdict == "skipped"
    assert M.match_listing("Software Engineer", "", cfg, judge=judge_with(0.95, match=False)).verdict == "skipped"
    # an excluded title never reaches the judge
    boom = lambda *a: (_ for _ in ()).throw(AssertionError("judge called"))  # noqa: E731
    assert M.match_listing("Senior Software Engineer", "", cfg, judge=boom).stage == "excluded"


def test_a_bad_pattern_in_the_users_file_is_ignored():
    cfg = {"roles": [{"id": "x", "title": "X", "patterns": ["(unclosed", r"(?i)\bengineer\b"]}]}
    assert M.match_listing("Engineer", "", cfg).verdict == "queued"


def test_roles_file_loads_and_missing_file_is_empty(tmp_path):
    f = tmp_path / "target_roles.yaml"
    f.write_text("semantic_threshold: 0.5\nroles:\n  - id: a\n    title: A\n    patterns: ['(?i)a']\n")
    assert M.load_roles(f)["roles"][0]["id"] == "a"
    assert M.load_roles(tmp_path / "none.yaml") == {"roles": []}
