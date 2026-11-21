"""A shared source snapshot: source text is evidence, never executable instructions."""
from pathlib import Path
import hashlib

from .artifact_cache import digest, write_private

VERSION = 1


class SourceGuard:
    """Detect edits across a document stage without overwriting the owner's sources."""
    def __init__(self, paths):
        self.paths = tuple(Path(path) for path in paths)
        self.before = self.fingerprints()

    def fingerprints(self):
        """Hash actual source bytes; creation and removal are changes too."""
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                for path in self.paths}

    def check(self):
        """Stop publication if the snapshot used to generate a packet is no longer current."""
        if self.before != self.fingerprints():
            raise RuntimeError('Document source inputs changed during generation; rerun from the current sources')


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


def model_version(stage=None):
    """Invalidate artifacts when generation/validation code changes, not just inputs."""
    root = Path(__file__).parents[1]
    names = ('steps/multi_agent.py', 'tools/tailor.py', 'tools/projects.py',
             'tools/cover_letter.py', 'tools/skillgraph.py', 'tools/incremental_documents.py',
             'tools/letter_attribution.py', 'steps/tailor.py', 'tools/job_context.py',
             'tools/repo_cards.py', 'tools/layout.py', 'tools/documents.py', 'tools/document_review.py',
             'tools/jdtext.py', 'tools/bundle.py', 'steps/ats.py', 'llm.py')
    if stage in ('experience','projects','skills','summary'):
        names=('steps/multi_agent.py','tools/tailor.py','tools/projects.py','tools/skillgraph.py')
    elif stage=='project-selection':
        names=('tools/projects.py','tools/repo_cards.py','tools/tailor.py')
    elif stage=='letter':
        names=('tools/cover_letter.py','tools/tailor.py')
    elif stage=='letter-attribution':
        names=('tools/letter_attribution.py','tools/cover_letter.py','tools/tailor.py')
    elif stage and (stage.startswith('review:') or stage.startswith('adjudicate:')):
        names=('tools/incremental_documents.py','steps/multi_agent.py','tools/tailor.py')
    # Shared metric/tenure policy affects both generated content and its
    # acceptance. A helper-only correction must invalidate older artifacts.
    names=(*names,'tools/number_claims.py')
    return digest({n: (root / n).read_text() for n in names if (root / n).exists()})
