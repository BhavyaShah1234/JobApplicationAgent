"""Obvious engineering metric-direction contradictions in generated achievement text.

A drafted bullet such as "Built scalable systems, increasing API response time by 28%" reports a regression as a benefit. This module flags only the
clear case: a change verb governing, inside ONE clause, a common engineering metric whose good direction is unambiguous (lower latency, response time,
error rate, downtime, cost; higher throughput, uptime, availability, accuracy). It is deliberately small and high precision. Ambiguous quantities
(CPU utilization, cache size, resource allocation, headcount) have no direction here, and nothing is ever rewritten. Explicit trade-offs,
fault-injection/testing and honest regression observations are allowed, and so are negated or prevented changes ("without increasing latency").
Not covered: numeric from/to inversions, synonyms outside the table, and any metric not listed.
"""
import re

# metric name -> (regex of its wordings, which direction is better)
METRICS: dict[str, tuple[str, str]] = {
    "latency": (r"(?:p\d{2,3}\s+|tail\s+)?latenc(?:y|ies)", "down"),
    "response time": (r"response[\s-]times?", "down"),
    "load time": (r"(?:page|load|startup|cold[\s-]start)[\s-](?:load[\s-])?times?", "down"),
    "error rate": (r"(?:error|failure|defect|crash|bug)[\s-]rates?", "down"),
    "downtime": (r"downtime", "down"),
    "recovery time": (r"mttr|mean[\s-]time[\s-]to[\s-](?:recover(?:y)?|resolv(?:e|ution)|repair|restor(?:e|ation))", "down"),
    "cycle time": (r"(?:cycle|build|deploy(?:ment)?|processing|query|inference|turnaround|lead)[\s-]times?", "down"),
    "cost": (r"(?:(?:cloud|infrastructure|operating|operational|compute|hosting)\s+)?costs?(?![-\w])"
             r"(?!\s+(?:savings?|reductions?|efficien\w*|optimi\w*|avoidance))", "down"),
    "throughput": (r"throughput", "up"),
    "uptime": (r"uptime|availability", "up"),
    "reliability": (r"reliability", "up"),
    "accuracy": (r"accuracy", "up"),
    "success rate": (r"(?:success|conversion|cache[\s-]hit|hit)[\s-]rates?", "up"),
    "coverage": (r"(?:test|code)\s+coverage", "up"),
    "yield": (r"(?:first[\s-]pass\s+)?yield|oee", "up"),
    "request rate": (r"(?:requests|queries|transactions)\s+per\s+second|qps|tps", "up"),
}
_UP = (r"increas(?:e|es|ed|ing)|rais(?:e|es|ed|ing)|boost(?:s|ed|ing)?|grow(?:s|n|th|ing)?|grew|rose|rise|rising|climb(?:s|ed|ing)?|"
       r"spik(?:e|es|ed|ing)|jump(?:s|ed|ing)?|surg(?:e|es|ed|ing)|inflat(?:e|es|ed|ing)|elevat(?:e|es|ed|ing)|escalat(?:e|es|ed|ing)")
_DOWN = (r"reduc(?:e|es|ed|ing|tion|tions)|cut|cuts|cutting|lower(?:s|ed|ing)?|decreas(?:e|es|ed|ing)|declin(?:e|es|ed|ing)|"
         r"drop(?:s|ped|ping)?|slash(?:es|ed|ing)?|shr(?:ank|unk|ink|inking)|diminish(?:es|ed|ing)?|degrad(?:e|es|ed|ing|ation)")
_CHANGE = re.compile(rf"(?i)\b(?:(?P<up>{_UP})|(?P<down>{_DOWN}))\b")
# A change that is negated, prevented or itself reduced is not a claimed move of the metric.
_GUARD = re.compile(r"(?i)\b(?:without|not|never|no|avoid\w*|prevent\w*|mitigat\w*|limit\w*|minimi[sz]\w*|eliminat\w*|offset\w*|curb\w*|stop\w*|"
                    r"halt\w*|resolv\w*|fix\w*|reduc\w*|cut|cutting|lower\w*|decreas\w*|instead|rather|than)\b")
_BREAK = re.compile(r"(?i)[,;:()]|\s(?:and|but|while|whereas|then|which|yet|so)\s")
# A sentence that deliberately describes a trade-off, a test/injection or an honest observed regression.
_EXEMPT = re.compile(r"(?i)\btrade-?offs?\b|\btrade off\b|\baccept(?:ed|ing|s)?\b|\bat the (?:cost|expense|price) of\b|\bin exchange\b|\bdespite\b|"
                     r"\balthough\b|\bregress(?:ion|ions|ed|ing)?\b|\binject(?:ed|ing|s|ion)?\b|\bfault[\s-]|\bchaos\b|"
                     r"\b(?:stress|load|soak|failure|resilience)[\s-]test\w*|\bto test\b|\bfor testing\b|\bsimulat\w*|\bdeliberate\w*|"
                     r"\bintentional\w*|\bobserv(?:ed|ing)\b|\bnoted\b")
_COMPOUND = r"(?![\w-])(?!\s+(?:loss|losses|variance|variability|spikes?|gaps?|overhead|incidents?|errors?|failures?|regressions?|degradation|alerts?|tickets?))"
_FILLER = r"(?:\s+(?:the|its|our|an?|overall|average|mean|median|total|[\w/+#.'-]+)){0,4}?"          # a few unrelated words between verb and metric
_AFTER = {k: re.compile(rf"(?i){_FILLER}\s+(?:{p}){_COMPOUND}") for k, (p, _) in METRICS.items()}
_NOUN = {k: re.compile(rf"(?i)\b(?:{p}){_COMPOUND}\s*(?:(?:was|were|has|have|had|is|are|also|then)\s+)?(?:(?P<up>{_UP})|(?P<down>{_DOWN}))\b")
         for k, (p, _) in METRICS.items()}
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _guarded(before: str) -> bool:
    return bool(_GUARD.search(" ".join(before.split()[-3:])))


def _clause_claims(clause: str) -> list[tuple[str, str, str]]:
    found = []
    for change in _CHANGE.finditer(clause):                                   # "increasing [the] API response time"
        if _guarded(clause[:change.start()]):
            continue
        for key, rx in _AFTER.items():
            if (m := rx.match(clause, change.end())):
                found.append((key, "up" if change.group("up") else "down", clause[change.start():m.end()].strip()))
                break
    for key, rx in _NOUN.items():                                             # "response time increased"
        for m in rx.finditer(clause):
            if not _guarded(clause[:m.start()]):
                found.append((key, "up" if m.group("up") else "down", m.group(0).strip()))
    return found


def claims(sentence: str) -> list[tuple[str, str, str]]:
    """(metric, 'up' | 'down', wording) for each change verb that governs a listed metric within one clause of `sentence`."""
    out, last = [], 0
    pieces = []
    for m in _BREAK.finditer(sentence or ""):
        pieces.append(sentence[last:m.start()])
        last = m.end()
    pieces.append((sentence or "")[last:])
    for piece in pieces:
        out += _clause_claims(piece)
    return out


def contradictions(text: str) -> list[str]:
    """Clear contradictions: a change that makes a metric worse ("increasing response time", "cutting throughput"), outside an explicit trade-off, test or regression."""
    out = []
    for sentence in _SENTENCES.split(text or ""):
        if _EXEMPT.search(sentence):
            continue
        for key, direction, wording in claims(sentence):
            if direction != METRICS[key][1]:
                out.append(f"\"{wording}\" makes {key} worse, not better: state the result with the right direction "
                           f"({'lower' if METRICS[key][1] == 'down' else 'higher'} {key} is the benefit)")
    return out


def inversions(text: str, source: str) -> list[str]:
    """Claims in `text` that give the opposite direction for a metric that `source` (the final resume) states."""
    known: dict[str, set[str]] = {}
    for sentence in _SENTENCES.split(source or ""):
        for key, direction, _ in claims(sentence):
            known.setdefault(key, set()).add(direction)
    out = []
    for sentence in _SENTENCES.split(text or ""):
        if _EXEMPT.search(sentence):
            continue
        for key, direction, wording in claims(sentence):
            if key in known and direction not in known[key]:
                out.append(f"\"{wording}\" reverses the direction the resume gives for {key}")
    return out
