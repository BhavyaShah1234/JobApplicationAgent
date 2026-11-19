"""Incremental source-routed filling with conservative, serializable checkpoints.

Stored decisions are proposals on reopening, never proof that a live control is
filled. Upload proofs and approval capabilities are deliberately not serialized.
"""
from collections import Counter
from dataclasses import asdict
import json
import os
import re
import time
from pathlib import Path

from jobagent.tools.artifact_cache import digest, write_private
from .facts import Decision, key, norm, entity_scope
from .planned import PlannedFiller
from .adaptive_view import AdaptiveView


def schema(field):
    """Question semantics and offered choices, independent of temporary locators."""
    return {'label': field.label, 'help': field.help, 'section': field.section,
            'kind': field.kind, 'required': field.required, 'options': list(field.options),
            'options_state': field.options_state, 'format': field.format,
            'multiple': field.multiple, 'maxlength': field.maxlength}


class AdaptiveFiller(PlannedFiller):
    """Reuse independent accepted answers while reconciling every live mutation."""
    engine_name = 'adaptive'
    VERSION = 1
    live_view_class = AdaptiveView

    def __init__(self, *args, checkpoint_path=None, source_version='', **kwargs):
        super().__init__(*args, **kwargs)
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.source_version = source_version
        self.journal = {}
        self.bindings = {}
        self.inventory = {}
        self.invalidations = 0
        self.restored = 0
        self.resumed_keys = set()
        self._live = None
        package = Path(__file__).parent
        self.implementation = digest({'code':{name:(package/name).read_text() for name in
            ('adaptive.py','adaptive_view.py','planned.py','routing.py','facts.py','discovery.py','live_view.py','adapter.py')},
            'model':getattr(self.model,'model_name','')})
        if checkpoint_path:
            try:
                saved = json.loads(self.checkpoint_path.read_text())
                if saved['version'] == self.VERSION and saved.get('implementation') == self.implementation:
                    self.journal = saved['decisions']
                    if not isinstance(self.journal, dict): self.journal = {}
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def signature(self, field):
        """Fingerprint all decision-relevant visible field properties."""
        return digest(schema(field))

    def applicable_inventory(self, inventory):
        """Exclude only schema branches the delivered schema explicitly disables."""
        disabled = [f for f in inventory['fields'] if f.get('potential') and f.get('applicability') is False]
        return {**inventory, 'fields':[f for f in inventory['fields'] if f not in disabled],
                'explicitly_inapplicable':disabled}

    def semantic_id(self, field):
        """Rebind unique questions across navigation without using row position."""
        # Entity row numbers alone are not sufficient to restore a different
        # person's/reordered employer's answers after a browser restart.
        if (entity_scope(field.section) or field.kind in ('file','password') or
                re.search(r'\b(?:verification|security|one time|authentication|captcha)\b.{0,30}\b(?:code|token)\b',
                          norm(field.label))): return None
        frame = self._live.view.frames.get(field.frame) if self._live and self._live.view else None
        frame_url = getattr(frame, 'url', '')
        return digest({'step': self.step, 'frame_url': frame_url, 'schema': schema(field)})

    def evidence(self, decision):
        """Fingerprint bound facts, or conservatively the broader narrative/policy."""
        ids = [x for x in decision.fact_id.split(',') if x]
        facts = {f.id: asdict(f) for f in self.facts}
        if ids and all(i in facts for i in ids):
            evidence = {'facts': {i: facts[i] for i in ids}}
            if 'document:' in decision.source:
                evidence['narrative'] = self.narrative_sources
            return digest(evidence)
        # Narrative, learned rules, referral and human answers have broader
        # dependencies than a single fact. Invalidate conservatively.
        return digest({'sources': self.source_version, 'facts': facts,
                       'narrative': self.narrative_sources, 'referral': self.referral_context})

    def observe_view(self, view):
        """Invalidate dependent decisions and restore only unique source-valid proposals."""
        semantic = [self.semantic_id(f) for f in view.fields]
        counts = Counter(semantic)
        current = {}
        for field, ident in zip(view.fields, semantic):
            field_key = key(field)
            spec = self.signature(field)
            current[digest(field_key)] = {'schema': schema(field), 'semantic_id': ident,
                                          'invalid': bool(field.invalid)}
            previous = self.bindings.get(field_key)
            decision = self.cache.get(field_key)
            changed = previous and (previous['schema'] != spec or
                                    decision and previous['evidence'] != self.evidence(decision))
            if previous and (changed or field.invalid):
                self.cache.pop(field_key, None)
                self.rendered.pop(field_key, None)
                self.human_answers.pop((self.step, field_key), None)
                if changed: self.failures.pop(field_key, None)
                self.invalidations += 1
                if ident: self.journal.pop(ident, None)
            if (not ident or counts[ident] != 1 or field.invalid or field_key in self.cache): continue
            saved = self.journal.get(ident)
            if not isinstance(saved, dict): continue
            try:
                restored = Decision(**saved['decision'])
                if saved['schema'] != spec or saved['evidence'] != self.evidence(restored): continue
                # This only saves replanning. PlannedFiller still fills and
                # independently reads the live value before marking verified.
                self.cache[field_key] = restored
                self.resumed_keys.add(field_key)
                self.bindings[field_key] = {'schema': spec, 'evidence': saved['evidence']}
                self.restored += 1
            except (KeyError, TypeError, ValueError):
                continue
        self.inventory = current

    def plan(self, fields, allow_human=True):
        """Use the existing source router and bind its batch to the current schema."""
        # Schema-only future questions lack the complete visible help and
        # options. They are hints, never reusable decisions for another schema.
        self.prepared = {}
        decisions = super().plan(fields, allow_human)
        for field in fields:
            self.bindings[key(field)] = {'schema': self.signature(field),
                                         'evidence': self.evidence(decisions[field.id])}
        return decisions

    def can_execute(self, planned, current, decision):
        """Refuse dispatch if a rerender changed options, question meaning or evidence."""
        bound = self.bindings.get(key(planned))
        # File decisions are created by the shared executor after planning.
        if planned.kind == 'file': return self.signature(planned) == self.signature(current)
        valid = bool(bound and bound['schema'] == self.signature(current)
                     and bound['evidence'] == self.evidence(decision))
        if not valid:
            self.cache.pop(key(planned), None)
            self.events.append({'event': 'decision_invalidated_before_dispatch', 'kind': current.kind})
        return valid

    def can_reuse(self, field):
        """A restored proposal still needs a fresh execution/readback record."""
        return key(field) not in self.resumed_keys

    def accepted(self, view, field, decision, row):
        """Checkpoint verified answers without serializing uploads or browser objects."""
        self.resumed_keys.discard(key(field))
        ident = self.semantic_id(field)
        if ident and sum(self.semantic_id(f) == ident for f in view.fields) == 1:
            self.journal[ident] = {'schema': self.signature(field),
                                   'decision': asdict(decision), 'evidence': self.evidence(decision)}
        self.bindings[key(field)] = {'schema': self.signature(field), 'evidence': self.evidence(decision)}
        if self.checkpoint_path:
            write_private(self.checkpoint_path, {'version': self.VERSION, 'implementation':self.implementation,
                                                  'decisions': self.journal,
                                                  'inventory': self.inventory})

    def settle(self, page, live, field, chosen, actual):
        """Stop waiting as soon as readback is valid; bound asynchronous acceptance."""
        from jobagent.tools import forms
        deadline = time.monotonic() + 1.0
        while actual is not None:
            if not actual.invalid and forms.same_value(chosen,actual.value,field.kind,field.label,field.multiple): break
            if time.monotonic() >= deadline: break
            page.wait_for_timeout(50)
            if live.changed():
                fresh = live.capture()
                actual = next((f for f in fresh.fields if key(f)==key(field)),None)
            else:
                actual = live.refresh(field)
        return actual

    def result(self, *args):
        """Add invalidation metrics and private diagnostics when repairs are exhausted."""
        result = super().result(*args)
        result['adaptive'] = {'version': self.VERSION, 'invalidations': self.invalidations,
                              'restored_decisions': self.restored}
        if self.checkpoint_path and any('repair budget exhausted' in b for b in result['blockers']):
            path = self.checkpoint_path.with_suffix('.png')
            try:
                # Diagnostic only: no coordinate clicking, forced values or
                # unverified visual answer becomes a completed control.
                self._live.page.screenshot(path=str(path),full_page=True)
                os.chmod(path,0o600)
                result['adaptive']['diagnostic_screenshot'] = path.name
            except Exception:
                result['adaptive']['diagnostic_screenshot'] = None
        return result
