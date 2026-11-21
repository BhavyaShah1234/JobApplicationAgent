"""Step 9: a cover letter that matches the tailored resume.

The letter is written from the job's FINAL resume.tex (so the user's own edits to it carry over), the JD and the
company research profile. Code checks keep it consistent with the resume: every number must appear in the resume,
the JD or the company profile, and every tool/technology it names must appear in one of them. The template
(data/templates/cover_letter.tex, else the committed example) has <<RECIPIENT>>, <<SUBJECT>>,
<<SALUTATION>>, <<BODY>> and <<CLOSING>> slots; everything else (the resume's header, layout) stays as is.
"""

import re
import time
import hashlib
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, create_model

from jobagent import llm
from jobagent.clock import with_today
from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools.tailor import DASH, TOOLISH, compile_pdf, proper_nouns, to_latex, to_plain
from jobagent.tools.letter_attribution import Binding
from jobagent.tools import bundle

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


class AttributedParagraph(Paragraph):
    """Opt-in paragraph schema with employer/project-specific achievement evidence."""
    bindings: list[Binding] = Field(default_factory=list)


class AttributedLetter(Letter):
    """Keep the production letter schema unchanged outside the incremental strategy."""
    paragraphs: list[AttributedParagraph] = Field(min_length=3,max_length=4)


def sourced_letter_schema(bank):
    """Offer exact source IDs and write each sentence once, without freehand evidence copies."""
    sentence = create_model('SourcedSentence',
        text=(str, Field(min_length=1, max_length=900, description='One complete sentence, plain text')),
        source_id=(Literal[tuple(['', *sorted(bank)])], Field(description=
            'Owning source ID; empty only for interest, greetings or ordinary closing language')))
    paragraph = create_model('SourcedParagraph',
        sentences=(list[sentence], Field(min_length=1, max_length=6)))
    return create_model('SourcedLetter',
        salutation=(str, Field(max_length=80)),
        paragraphs=(list[paragraph], Field(min_length=3, max_length=4)),
        closing=(Literal['Sincerely','Best regards','Kind regards','Respectfully'], Field(
            description='Valediction only; the template supplies the candidate name')))


def attach_sources(draft, bank):
    """Materialize exact claim/evidence bindings from the selected immutable sources."""
    paragraphs = []
    for paragraph in draft.paragraphs:
        bindings = []
        for sentence in paragraph.sentences:
            if not sentence.source_id:
                continue
            if sentence.source_id not in bank:
                raise ValueError('cover letter refers to an unknown source')
            bindings.append(Binding(claim=sentence.text, source_id=sentence.source_id,
                                    quote=bank[sentence.source_id]['text']))
        paragraphs.append(AttributedParagraph(text=' '.join(s.text for s in paragraph.sentences),
            evidence=list(dict.fromkeys(b.quote for b in bindings))[:6], bindings=bindings))
    return AttributedLetter(salutation=draft.salutation, closing=draft.closing, paragraphs=paragraphs)


PROMPT = """You write a one-page cover letter for the candidate whose tailored RESUME is given, for the given job.

The letter must match the resume exactly in substance:
- Every claim about the candidate (roles, employers, duties, tools, projects, numbers, education) must come from the
  RESUME. Reuse its numbers unchanged and never add new ones; you may rephrase. Write employer, project, degree
  and tool names exactly as the resume does, never abbreviated (no "TGen" for "Translational Genomics Research
  Institute").
- Never claim credentials missing from the resume, including generic closing phrases such as "my certifications".
  A certification mentioned in the posting or company profile is not the candidate's credential.
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
    from jobagent.tools import number_claims as NC
    source_figures = NC.metric_tokens(to_plain(sources))
    issues = []
    for k, p in enumerate(letter.paragraphs, 1):
        if not re.search(r"(?i)\b(?:certifications?|certificates?|certified)\b", resume) and re.search(
                r"(?i)\bmy\s+(?:(?:background|skills|qualifications|experience|knowledge|expertise|education|training)"
                r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s+))*certifications?\b|"
                r"\bI\s+(?:(?:also|currently)\s+)?(?:hold|have|earned|obtained|possess|maintain)\s+"
                r"[^.!?;\n]{0,80}\bcertificat(?:ion|e)s?\b|\bI\s+am\s+(?:[\w-]+\s+){0,5}certified\b", p.text):
            issues.append(f"paragraph {k}: a candidate certification is claimed but no certification appears in the resume")
        for n in re.findall(r"\d+(?:\.\d+)?", p.text):
            if n not in known_numbers:
                issues.append(f"paragraph {k}: the number {n!r} is not in the resume, posting or company profile")
        for token, written in NC.metric_spans(p.text).items():       # a figure needs its own spelling in a source, not just the same digits
            if token not in source_figures and NC.digits(token) in known_numbers:
                issues.append(f"paragraph {k}: the figure {written!r} is not in the resume, posting or company profile")
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


def write(job_dir: Path, jd: JobDescription, company_md: str, model, rounds: int = 3, log=print, *, incremental=False) -> dict:
    """cover_letter.tex/.pdf in job_dir from its resume.tex; returns a small report for changes.md."""
    started=time.monotonic()
    for stale in ("cover_letter.pdf", "cover_letter.tex"):     # an earlier letter must not pass as this one
        (job_dir / stale).unlink(missing_ok=True)
    resume_tex = (job_dir / "resume.tex").read_text()
    resume = resume_text(resume_tex)
    template = template_path().read_text()
    # The final resume's contact block is authoritative. A separately saved
    # letter template must not silently keep an older name/contact line.
    contact = re.search(r"\\begin\{center\}.*?\\end\{center\}", resume_tex, re.S)
    old_contact = re.search(r"\\begin\{center\}.*?\\end\{center\}", template, re.S)
    if contact and old_contact:
        template = template[:old_contact.start()] + contact.group(0) + template[old_contact.end():]
    if incremental:
        from . import letter_attribution as attribution
        from .incremental_documents import cache_for, invoke
        cache = cache_for(job_dir)
        bank = attribution.sources(resume_tex, job_dir)
        bank['company_context'] = {'entity':jd.company or '', 'text':company_md, 'kind':'company'}
        bank['posting_context'] = {'entity':'Job posting', 'text':jd.model_dump_json(), 'kind':'posting'}
        source_schema = sourced_letter_schema(bank)
    feedback, best = "", None
    for attempt in range(1, rounds + 1):
        try:
            messages = build_prompt(resume, jd, company_md, feedback)
            if incremental:
                import json
                messages.append(HumanMessage('Write paragraphs as lists of complete sentences, each with text and '
                    'one source_id from ENTITY SOURCES. The code assembles the prose and attaches the actual source '
                    'text; do not write the sentence or evidence twice. Keep each candidate achievement sentence '
                    'about one employer/project source. Cite sentences with numbers or explicit employer names '
                    'too; use an empty source_id only for interest, greetings and ordinary closing language. '
                    'Company facts may cite company_context; '
                    'the position title may cite posting_context. Neither context establishes candidate achievements. '
                    'Do not borrow a number or technology from another employer.\nENTITY SOURCES:\n' + json.dumps(bank)))
                messages.append(HumanMessage('Return exactly four paragraphs: introduction, two evidence paragraphs, '
                    'and a short close. A closing sentence that claims skills, experience or credentials still '
                    'needs its skills/summary/employment source_id; only pure courtesy or interest uses an empty ID. '
                    'Use a valediction only in closing, without a name or placeholder.'))
                # The template is part of the cache input even though only its
                # locked layout/header is rendered after generation.
                messages.append(HumanMessage('LETTER TEMPLATE VERSION: ' + hashlib.sha256(template.encode()).hexdigest()))
                def validate_draft(value):
                    """Correct missing claim bindings before caching or rendering a draft."""
                    candidate=attach_sources(value,bank)
                    if problems:=attribution.binding_issues(candidate,bank):
                        raise ValueError('; '.join(problems))
                draft = invoke(cache, 'letter', get_settings().model_writer, model, source_schema, messages,
                               log=log, validator=validate_draft,first='json_mode',deadline_s=180)
                letter = attach_sources(draft, bank)
            else:
                letter = llm.structured_invoke(model, Letter, messages, log=log)
        except llm.LLMConfigError:
            raise
        except Exception as e:  # timeout / token limit: next round, with actionable feedback
            log(f"cover letter round {attempt}: model error {type(e).__name__}")
            feedback = f"The previous draft could not be validated: {type(e).__name__}: {str(e)[-500:]}. Correct its structure and source bindings."
            continue
        # Apply the same punctuation policy as to_latex before checking the
        # content. Otherwise a verbatim JD title with an en dash fails every
        # retry even though the rendered PDF already replaces that dash.
        letter = letter.model_copy(update={
            "salutation": DASH.sub(", ", letter.salutation),
            "closing": DASH.sub(", ", letter.closing),
            "paragraphs": [p.model_copy(update={"text": DASH.sub(", ", p.text)}) for p in letter.paragraphs],
        })
        issues = consistency(letter, resume, jd, company_md)
        if not header_matches(template, resume_tex):
            issues.append("the cover letter header differs from the resume")
        if incremental:
            for review_attempt in range(2):
                try:
                    issues += attribution.check(letter, bank, cache, get_settings().model_planner, log)
                    break
                except llm.LLMConfigError:
                    raise
                except Exception as exc:
                    if review_attempt:
                        issues.append(f'achievement attribution review unavailable ({type(exc).__name__})')
                    else:
                        log("Retrying cover-letter attribution review on the unchanged letter")
        tex = render(template, letter, jd)
        pages, err = compile_pdf(tex, job_dir, name="cover_letter")
        log(f"cover letter round {attempt}: {pages or 'compile error'} page(s), {len(issues)} consistency issue(s)")
        if err:
            issues.append(f"LaTeX error: {err}")
        if pages and pages > 1:
            issues.append(f"{pages} pages; it must be one: shorten to ~300 words")
        if not pages and not err:
            issues.append("no readable cover-letter PDF was produced")
        rank = (pages == 1 and not err, -len(issues))
        if best is None or rank > best[0]:
            pdf = job_dir / "cover_letter.pdf"
            meta = job_dir / "cover_letter.layout.json"
            best = (rank, letter, list(issues), tex, pages,
                    pdf.read_bytes() if not err and pdf.exists() else None,
                    meta.read_text() if not err and meta.exists() else None)
        if not issues:
            break
        feedback = "Fix these problems: " + " | ".join(issues)
    if best is None:
        raise RuntimeError("every cover letter round failed")
    _, letter, issues, tex, pages, pdf, meta = best
    # Publish the exact already-compiled best attempt, rather than recompiling
    # it and risking losing a good PDF to a final infrastructure failure.
    bundle.publish(job_dir, {"cover_letter.tex": tex, "cover_letter.pdf": pdf, "cover_letter.layout.json": meta})
    if incremental:
        from .artifact_cache import write_private
        write_private(job_dir/'incremental-letter-metrics-v1.json', {'version':1,'elapsed_s':time.monotonic()-started,
                                                                    'rounds':attempt,'stages':cache.events})
    return {"valid": not issues and pages == 1, "pages": pages, "letter": letter, "issues": issues, "words": sum(len(p.text.split()) for p in letter.paragraphs),
            "header_matches_resume": header_matches(template, resume_tex), "template": template_path().name}
