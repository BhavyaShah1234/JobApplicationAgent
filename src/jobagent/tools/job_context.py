"""A shared source snapshot: source text is evidence, never executable instructions."""
from pathlib import Path
import json

from .artifact_cache import digest, write_private

VERSION = 1


def snapshot(job_dir, *, company='', candidate=None, template='', claims=None):
    """Normalize current per-job sources and retain their provenance for both engines.

    Candidate data is supplied by the existing profile loader, after its own policy
    handling. Credentials, cookies and inbox contents must never be supplied here.
    The disk manifest contains fingerprints, not another copy of the profile.
    """
    job_dir = Path(job_dir)
    sources = {'company': company, 'candidate': candidate or {},
               'template': template, 'claims_policy': claims or {}}
    for name in ('jd.json', 'jd.txt', 'resume.tex', 'filled.json', 'projects.json',
                 'skill_suggestions.json', 'application/answers.md'):
        path = job_dir / name
        sources[name] = path.read_text() if path.is_file() else ''
    fingerprints = {name: digest(value) for name, value in sources.items()}
    manifest = {'version': VERSION, 'sources': fingerprints,
                'fingerprint': digest({'version': VERSION, 'sources': fingerprints})}
    write_private(job_dir / 'context-manifest.json', manifest)
    return {'version': VERSION, 'sources': sources, **manifest}


def model_version():
    """Invalidate artifacts when generation/validation code changes, not just inputs."""
    root = Path(__file__).parents[1]
    names = ('steps/multi_agent.py', 'tools/tailor.py', 'tools/projects.py',
             'tools/cover_letter.py', 'tools/skillgraph.py', 'tools/incremental_documents.py',
             'tools/letter_attribution.py')
    return digest({n: (root / n).read_text() for n in names if (root / n).exists()})
