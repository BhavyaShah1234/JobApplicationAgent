"""Recruiting-compatible PDFs retain content and URI links, without OpenAction."""
import re
import subprocess
import zlib
from jobagent.tools.tailor import compile_pdf


def test_pdf_compiler_keeps_text_and_links_but_omits_the_optional_startup_action(tmp_path):
    tex=r'''\documentclass{article}
\usepackage{hyperref}
\begin{document}A real resume. \href{https://example.org/profile}{Portfolio}\end{document}'''
    pages,error=compile_pdf(tex,tmp_path)
    assert pages==1 and not error
    pdf=tmp_path/'resume.pdf';content=pdf.read_bytes();chunks=[content]
    for match in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream',content,re.S):
        try:chunks.append(zlib.decompress(match[1]))
        except zlib.error:pass
    assert not any(b'/OpenAction' in c for c in chunks)
    assert any(b'/URI' in c and b'https://example.org/profile' in c for c in chunks)
    text=subprocess.check_output(['pdftotext',str(pdf),'-'],text=True)
    assert 'A real resume.' in text and 'Portfolio' in text
    assert (tmp_path/'resume.tex').read_text()==tex, 'the original source is retained exactly'


def test_pdf_compiler_also_supports_a_document_without_hyperref(tmp_path):
    pages,error=compile_pdf(r'\documentclass{article}\begin{document}Plain text\end{document}',tmp_path)
    assert pages==1 and not error


def test_scientific_metric_exponents_and_subscripts_reach_the_compiled_pdf(tmp_path):
    from jobagent.tools.tailor import to_latex
    tex=r'\documentclass{article}\begin{document}' + to_latex('R² = 0.984; CO₂; 10⁻³; latency ≤10µs') + r'\end{document}'
    pages,error=compile_pdf(tex,tmp_path)
    assert pages==1 and not error
    text=subprocess.check_output(['pdftotext',str(tmp_path/'resume.pdf'),'-'],text=True)
    compact=''.join(text.split()).replace('−','-').replace('µ','μ')
    assert 'R2=0.984' in compact and 'CO2' in compact and '10-3' in compact
    assert '≤10μs' in compact
