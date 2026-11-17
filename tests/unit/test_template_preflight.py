"""A malformed owner template must fail before writing; its original PDF need not already fit one page."""
import pytest
from jobagent.tools import documents,tailor


SOURCE = r"""\documentclass{article}
\newcommand{\resumeSubheading}[4]{\begin{tabular}{lr}#1 & #2\\#3 & #4\\\end{tabular}}
\begin{document}
\resumeSubheading{Engineer}{2026}{Example Company}%{Private location}
\begin{itemize}\item Built software.\end{itemize}
\end{document}
"""


def test_missing_macro_argument_is_rejected_and_empty_argument_keeps_location_hidden():
    with pytest.raises(ValueError,match='does not compile'):
        documents.validate_template(SOURCE)
    documents.validate_template(SOURCE.replace('{Example Company}%','{Example Company}{}%'))


def test_preflight_does_not_require_the_untailored_source_to_fit_one_page():
    documents.validate_template(r'\documentclass{article}\begin{document}First\newpage Second\end{document}')


def test_both_writers_check_the_exact_source_before_parsing_or_calling_models(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from jobagent.state import JobDescription
    from jobagent.steps import multi_agent,tailor as single_agent
    data,state,job=tmp_path/'data',tmp_path/'state',tmp_path/'job'
    (data/'templates').mkdir(parents=True);(state/'companies').mkdir(parents=True);job.mkdir()
    (data/'templates/resume.tex').write_text(SOURCE)
    (state/'companies/acme.md').write_text('Example company profile')
    (job/'jd.json').write_text(JobDescription(title='Engineer',company='Acme').model_dump_json())
    settings=SimpleNamespace(data_dir=data,state_dir=state)
    monkeypatch.setattr(multi_agent,'get_settings',lambda:settings)
    monkeypatch.setattr(single_agent,'get_settings',lambda:settings)
    monkeypatch.setattr(tailor,'parse',lambda _:pytest.fail('Broken source reached parsing/writing'))
    for run in [lambda:multi_agent.run_job(job,'acme',{},()),lambda:single_agent.main(str(job),'acme',ats=False)]:
        with pytest.raises(ValueError,match='does not compile'):
            run()
