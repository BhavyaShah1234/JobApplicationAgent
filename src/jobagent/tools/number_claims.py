"""Metric figures ("30%", "10x", "80 ms", "$40k", "2M", "tenfold", "tripled") in generated text, and where the owner wrote them.

A bare digit string says little: "5" is also in "5+ years" and "10" in "10% travel". A generated result such as "cut latency by 5%" is the
candidate's own figure only when the owner's own text carries that figure. Technology versions ("Python 3.11", "CUDA 12", "ISO 26262") and plain
counts are not metrics and stay as they were: they are matched by their digits.
"""
import re

_NUM = r"\d[\d,]*(?:\.\d+)?"
METRIC = re.compile(
    rf"(?<![\w.$])(?:\$\s?{_NUM}\s?[kKmMbB]?(?![\w])"                       # $40k, $1.2M, $300
    rf"|{_NUM}\s?(?:%|percent\b)"                                              # 30%, 30 percent
    rf"|{_NUM}\s?[x×](?![\w])"                                                 # 10x
    rf"|{_NUM}[\s-]?fold\b|{_NUM}\s?times\b"                                   # 3-fold, 3 times
    rf"|{_NUM}\s?(?:ms|milliseconds?)\b"                                       # 80 ms
    rf"|{_NUM}[kKmMbB]\b\+?)", re.I)                                           # 2M, 40k
WORDS = re.compile(
    r"(?i)\b(?:(doubl|tripl|quadrupl)(?:e|ed|es|ing)|(halv)(?:e|ed|es|ing)|"
    r"((?:two|three|four|five|six|seven|eight|nine|ten|twenty|hundred|thousand|million)[- ]?(?:fold|times))|"
    r"((?:an? )?order of magnitude))\b")


_ONES = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_WORD_NUMBER = {w: i for i, w in enumerate(_ONES, 1)} | {w: 10 * i for i, w in enumerate(_TENS, 2)} | {"hundred": 100}
WORD_PERCENT = re.compile(rf"(?i)\b((?:{'|'.join(_TENS)})(?:[- ](?:{'|'.join(_ONES[:9])}))?|{'|'.join(_ONES)}|(?:one )?hundred)\s+percent\b")


def _word_number(words: str) -> int:
    """`twenty-five` -> 25, `one hundred` -> 100."""
    low = words.lower().strip()
    return 100 if low.endswith("hundred") else sum(_WORD_NUMBER.get(w, 0) for w in re.split(r"[-\s]+", low))


def _norm(token: str) -> str:
    token = token.lower().replace("×", "x").replace("percent", "%")
    token = re.sub(r"[\s,]", "", token)
    return re.sub(r"(?<=\d)-?fold$", "fold", re.sub(r"milliseconds?$", "ms", token))


def metric_tokens(text: str) -> dict[str, str] | set[str]:
    """Metric figures in `text`, in a spelling-independent form.

    Args:
        text: Plain text (LaTeX already removed).

    Returns:
        A set of normalised tokens (`30%`, `10x`, `80ms`, `$40k`, `2m`, `tripl`, `tenfold`); use `metric_spans` for the wording as written.
    """
    return set(metric_spans(text))


def metric_spans(text: str) -> dict[str, str]:
    """Metric figures in `text` as `{normalised token: wording as written}` (the first occurrence of each)."""
    found: dict[str, str] = {}
    for m in METRIC.finditer(text or ""):
        found.setdefault(_norm(m.group(0)), m.group(0).strip())
    for m in WORD_PERCENT.finditer(text or ""):
        found.setdefault(f"{_word_number(m.group(1))}%", m.group(0).strip())      # "twenty percent" is the figure 20%
    for m in WORDS.finditer(text or ""):
        stem = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").lower()
        found.setdefault(re.sub(r"[\s-]", "", stem), m.group(0).strip())
    return found


def digits(token: str) -> str:
    """The number inside a token (`30%` -> `30`), or the token itself for a word figure."""
    m = re.search(_NUM, token)
    return m.group(0).replace(",", "") if m else token


_WORDS = {w: i for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
                                      "eighteen nineteen twenty".split(), 1)} | {"thirty": 30, "forty": 40, "fifty": 50}
YEARS = re.compile(
    r"(?i)(?<![\w.])(?:(?P<a>an?)\s+(?P<au>decade)|(?P<n>\d+(?:\.\d+)?|" + "|".join(_WORDS) + r")\s*(?:\+|[-\s]?plus)?[\s-]*(?P<u>years?|yrs?|decades?))\b")


HORIZON_NOUNS = {"npv", "forecast", "forecasts", "forecasting", "projection", "projections", "projected", "model", "models", "modeling", "modelling", "simulation",
                 "simulations", "horizon", "lease", "leases", "loan", "loans", "bond", "bonds", "mortgage", "mortgages", "contract", "contracts", "warranty",
                 "warranties", "plan", "plans", "roadmap", "roadmaps", "return", "returns", "cycle", "cycles", "term", "period", "lifecycle", "retention", "archive",
                 "archives", "dataset", "datasets", "rolling", "window", "windows", "trend", "trends", "depreciation", "amortization", "payback", "discount", "moving"}
_AHEAD = re.compile(r"(?i)^\s*(?:ahead|forward|out|hence|from now|into the future|horizons?|projections?|forecasts?)\b")
_DATA = re.compile(r"(?i)^\s*of\s+(?:\w+\s+){0,2}(?:data|prices|records|logs|history|returns|observations|measurements|time series)\b")


def _is_technical_horizon(text: str, m: "re.Match") -> bool:
    """A duration that belongs to a model, contract or data span ("20-year NPV forecasting models", "10 years ahead", "25 years of market data"),
    not to the candidate's own career."""
    rest = text[m.end():]
    if text[max(0, m.start("u") - 1):m.start("u")] in "-\u2011\u2013":              # "20-year X": the duration describes X
        nxt = re.findall(r"[A-Za-z]+", rest)[:2]
        return any(w.lower() in HORIZON_NOUNS for w in nxt)
    data_span = _DATA.match(rest)
    # "Years of data engineering" describes a career; "years of market data"
    # describes the dataset. Do not discard the former as a technical horizon.
    career = data_span and re.match(
        r"(?i)^\s+(?:engineering|science|analytics|analysis|platform|infrastructure|"
        r"architecture|management|experience|work|career|expertise)\b", rest[data_span.end():])
    return bool(_AHEAD.match(rest) or (data_span and not career))


def claimed_years(text: str) -> list[float]:
    """Years of experience a text claims: `5 years`, `10+ yrs`, `a 10-year record`, `ten years`, `ten-plus years`, `a decade`, `two decades`.

    A duration that describes a technical horizon ("a 20-year NPV forecasting model", "10 years ahead", "25 years of market data") is not
    the candidate's tenure and is ignored.

    Args:
        text: A summary or other prose.

    Returns:
        Every duration found, in years (a decade counts as ten).
    """
    out = []
    for m in YEARS.finditer(text or ""):
        if _is_technical_horizon(text, m):
            continue
        if m.group("a"):
            out.append(10.0)
            continue
        n = m.group("n").lower()
        value = float(n) if n[0].isdigit() else float(_WORDS[n])
        out.append(value * (10 if m.group("u").lower().startswith("decade") else 1))
    return out
