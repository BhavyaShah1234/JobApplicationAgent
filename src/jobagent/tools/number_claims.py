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
    for m in WORDS.finditer(text or ""):
        stem = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").lower()
        found.setdefault(re.sub(r"[\s-]", "", stem), m.group(0).strip())
    return found


def digits(token: str) -> str:
    """The number inside a token (`30%` -> `30`), or the token itself for a word figure."""
    m = re.search(_NUM, token)
    return m.group(0).replace(",", "") if m else token
