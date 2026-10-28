"""Step 9: a cover letter that matches the tailored resume.

The letter is written from the job's FINAL resume.tex (so the user's own edits to it carry over), the JD and the
company research profile. Code checks keep it consistent with the resume: every number must appear in the resume,
the JD or the company profile, and every tool/technology it names must appear in one of them. The template
(data/templates/cover_letter.tex, else the committed example) has <<RECIPIENT>>, <<SUBJECT>>,
<<SALUTATION>>, <<BODY>> and <<CLOSING>> slots; everything else (the resume's header, layout) stays as is.
"""

import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from jobagent.clock import with_today
from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools.tailor import DASH, TOOLISH, compile_pdf, proper_nouns, to_latex, to_plain

SLOTS = ("<<RECIPIENT>>", "<<SUBJECT>>", "<<SALUTATION>>", "<<BODY>>", "<<CLOSING>>")
# Words TOOLISH matches that aren't technologies (letter boilerplate, degrees, places).
_NOT_TOOLS = {"I", "MS", "BS", "USA", "US", "MI", "OH", "IL", "AZ", "CA", "NY", "TX", "AI", "ML", "RE"}


def template_path() -> Path:
    d = get_settings().data_dir / "templates"
    return d / "cover_letter.tex" if (d / "cover_letter.tex").exists() else d / "cover_letter.example.tex"


def resume_text(tex: str) -> str:
    """Readable plain text of the resume body (Summary to the end) for the model and the consistency checks."""
    body = tex[tex.index("\\section{Summary}"):tex.index("\\end{document}")]
    body = re.sub(r"(?<!\\)%.*", "", body)                                  # comments (e.g. % backfill), not \%
    body = re.sub(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\])?", " ", body)              # environments
    body = body.replace("\\\\", "\n")                                          # \\ line breaks
    body = re.sub(r"\\href\{[^}]*\}\{", "{", body)                           # keep link text, drop URLs
    body = re.sub(r"\\section\{([^}]*)\}", r"\n\n## \1\n", body)
    body = re.sub(r"\\resumeItem\{", "\n- {", body)
    body = re.sub(r"\\item\[\]", "\n", body)
    body = re.sub(r"\\(resumeSubheading|resumeProjectHeading)", "\n", body)
    body = re.sub(r"\\textsuperscript\{([^}]*)\}", r"^\1", body)
    body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", body)                 # remaining commands
    body = to_plain(body).replace("{", " ").replace("}", " ").replace("$", "")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", body)).strip()


class Paragraph(BaseModel):
    text: str = Field(max_length=1400, description="Plain text, no LaTeX")
    evidence: list[str] = Field(max_length=6, description="Short quotes of the resume lines / company facts used")


class Letter(BaseModel):
    salutation: str = Field(max_length=80, description="e.g. 'Dear Plante Moran Hiring Team,'")
    paragraphs: list[Paragraph] = Field(min_length=3, max_length=4)
    closing: str = Field(max_length=30, description="e.g. 'Sincerely' (no comma)")


PROMPT = """You write a one-page cover letter for the candidate whose tailored RESUME is given, for the given job.

The letter must match the resume exactly in substance:
- Every claim about the candidate (roles, employers, duties, tools, projects, numbers, education) must come from the
  RESUME. Reuse its numbers unchanged and never add new ones; you may rephrase. Write employer, project, degree
  and tool names exactly as the resume does, never abbreviated (no "TGen" for "Translational Genomics Research
  Institute").
- Facts about the company come only from the COMPANY PROFILE (cite them in `evidence`).
- Structure, 3-4 paragraphs, 280-380 words in total, first person:
  1. The role (posting's title) and why this company: one specific fact from the company profile tied to what
     the candidate offers.
  2-3. The 2-3 strongest matches between the posting's main duties/requirements and the resume's experience and
     projects, each with the concrete tools and results the resume states. Mirror the posting's keywords.
  4. A short close: what the candidate would bring, interest in a conversation, thanks.
- Professional, specific, no clichés ("I am writing to express my interest", "team player", "passionate"), no
  pronouns other than I/my/me, no salary or availability claims, no mention of the resume being tailored.
- Never use em dashes (—) or a spaced dash as punctuation; use commas, semicolons, colons, parentheses or
  separate sentences.
- A change by a percentage takes "by": "cutting failed refreshes by 45%" (the resume may have been written either
  way; always write it with "by").
- `evidence`: for each paragraph, the short resume lines / company facts it relies on."""


def build_prompt(resume: str, jd: JobDescription, company_md: str, feedback: str = "") -> list:
    job = {"title": jd.title, "company": jd.company, "locations": jd.locations, "role_summary": jd.role_summary,
           "responsibilities": jd.responsibilities, "basic_qualifications": jd.basic_qualifications,
           "preferred_qualifications": jd.preferred_qualifications, "values": jd.values}
    text = (f"JOB POSTING:\n{job}\n\nCOMPANY PROFILE:\n{company_md[:3000]}\n\nRESUME (tailored for this job):\n"
            f"{resume}")
    if feedback:
        text += f"\n\nREVISION REQUEST:\n{feedback}"
    return [SystemMessage(with_today(PROMPT)), HumanMessage(text)]


def consistency(letter: Letter, resume: str, jd: JobDescription, company_md: str) -> list[str]:
    """Numbers and technologies in the letter that appear nowhere in the resume, the JD or the company profile."""
    sources = "\n".join([resume, company_md, jd.model_dump_json()])
    low = sources.lower()
    known_numbers = set(re.findall(r"\d+(?:\.\d+)?", sources))
    issues = []
    for k, p in enumerate(letter.paragraphs, 1):
        for n in re.findall(r"\d+(?:\.\d+)?", p.text):
            if n not in known_numbers:
                issues.append(f"paragraph {k}: the number {n!r} is not in the resume, posting or company profile")
        for name in sorted((set(TOOLISH.findall(p.text)) | proper_nouns(p.text)) - _NOT_TOOLS):
            if name.lower() not in low:
                issues.append(f"paragraph {k}: {name!r} is not in the resume, posting or company profile")
        if dashes := len(DASH.findall(p.text)):
            issues.append(f"paragraph {k}: {dashes} dash(es) used as punctuation; rewrite with commas, semicolons, "
                          "colons or separate sentences")
    if DASH.search(letter.salutation + letter.closing):
        issues.append("a dash in the salutation or closing")
    words = sum(len(p.text.split()) for p in letter.paragraphs)
    if words > 420:
        issues.append(f"{words} words; keep it to 280-380")
    return issues


def recipient(jd: JobDescription) -> str:
    lines = ["Hiring Team", jd.company or ""]
    if jd.locations:
        lines.append(jd.locations[0])
    return "\\\\\n".join(to_latex(x) for x in lines if x)


def real_req_id(req: str | None, url: str = "") -> bool:
    """A requisition number worth quoting ("JR-03394", "12795", "JR2026744"), not a job board's posting id. An id
    that is part of the posting URL is the board's own id (Greenhouse "8130881", Ashby/Lever UUIDs) unless it looks
    like a requisition code (letters and digits)."""
    if not req or len(req) > 16 or re.fullmatch(r"[0-9a-fA-F-]{12,}", req):
        return False
    if req.lower() in (url or "").lower():
        return bool(re.search(r"[A-Za-z]", req) and re.search(r"\d", req))
    return True


def subject(jd: JobDescription) -> str:
    url = f"{jd.source_url or ''} {jd.apply_url or ''}"
    return to_latex(jd.title or "") + (f" (Req. {to_latex(jd.req_id)})" if real_req_id(jd.req_id, url) else "")


def render(template: str, letter: Letter, jd: JobDescription) -> str:
    body = "\n\n".join(to_latex(p.text) for p in letter.paragraphs)
    values = {"<<RECIPIENT>>": recipient(jd), "<<SUBJECT>>": subject(jd), "<<SALUTATION>>": to_latex(letter.salutation),
              "<<BODY>>": body, "<<CLOSING>>": to_latex(letter.closing.rstrip(","))}
    for slot in SLOTS:
        template = template.replace(slot, values[slot])
    return template


def header_matches(letter_template: str, resume_tex: str) -> bool:
    """The letter's header block must be the resume's header block (same name and contact line)."""
    grab = lambda t: (m.group(0) if (m := re.search(r"\\begin\{center\}.*?\\end\{center\}", t, re.S)) else "")
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    return norm(grab(letter_template)) == norm(grab(resume_tex))


def write(job_dir: Path, jd: JobDescription, company_md: str, model, rounds: int = 3, log=print) -> dict:
    """cover_letter.tex/.pdf in job_dir from its resume.tex; returns a small report for changes.md."""
    resume_tex = (job_dir / "resume.tex").read_text()
    resume = resume_text(resume_tex)
    template = template_path().read_text()
    feedback, best = "", None
    for attempt in range(1, rounds + 1):
        try:
            letter = model.with_structured_output(Letter, method="json_schema").invoke(
                build_prompt(resume, jd, company_md, feedback))
        except Exception as e:  # timeout / token limit: next round
            log(f"cover letter round {attempt}: model error {type(e).__name__}")
            continue
        issues = consistency(letter, resume, jd, company_md)
        tex = render(template, letter, jd)
        pages, err = compile_pdf(tex, job_dir, name="cover_letter")
        log(f"cover letter round {attempt}: {pages or 'compile error'} page(s), {len(issues)} consistency issue(s)")
        if err:
            issues.append(f"LaTeX error: {err}")
        if pages and pages > 1:
            issues.append(f"{pages} pages; it must be one: shorten to ~300 words")
        if best is None or len(issues) < len(best[1]):
            best = (letter, issues, tex)
        if not issues:
            break
        feedback = "Fix these problems: " + " | ".join(issues)
    if best is None:
        raise RuntimeError("every cover letter round failed")
    letter, issues, tex = best
    compile_pdf(tex, job_dir, name="cover_letter")  # the best round, not necessarily the last
    return {"letter": letter, "issues": issues, "words": sum(len(p.text.split()) for p in letter.paragraphs),
            "header_matches_resume": header_matches(template, resume_tex), "template": template_path().name}
