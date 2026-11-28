"""Incremental source-routed filling with conservative, serializable checkpoints.

Stored decisions are proposals on reopening, never proof that a live control is
filled. Upload proofs and approval capabilities are deliberately not serialized.
"""
from collections import Counter
from dataclasses import asdict, replace
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from jobagent.tools.artifact_cache import digest, write_private
from jobagent.tools import forms
from .facts import Decision, key, norm, entity_scope
from .planned import PlannedFiller
from .adaptive_view import AdaptiveView


def schema(field):
    """Question semantics and offered choices, independent of temporary locators."""
    return {'label': field.label, 'help': field.help, 'section': field.section,
            'kind': field.kind, 'required': field.required, 'options': list(field.options),
            'options_state': field.options_state, 'format': field.format,
            'multiple': field.multiple, 'maxlength': field.maxlength,
            'source_other_only':field.source_other_only,'source_parent_value':field.source_parent_value}


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
        self.human_scopes = {}
        self.option_inventory = {}
        self.unavailable_choices = {}
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
        return digest(self.decision_schema(field))

    def decision_schema(self, field):
        """An unopened partial menu does not assert that previously read options vanished."""
        spec=schema(field)
        # React-style controls expose their temporary placeholder through
        # aria-describedby. Its disappearance on selection is not new guidance.
        if field.kind=='combobox' and re.fullmatch(
                r'(?:select|choose)(?:\s+(?:an?\s+)?(?:option|answer|one))?(?:\.{3}|…)?',
                field.help.strip(),re.I):
            spec['help']=''
        previous=self.option_inventory.get(key(field))
        if field.kind=='combobox' and not field.options and field.options_state!='complete' and previous:
            spec.update(options=list(previous[0]),options_state=previous[1])
        return spec

    def control_failed(self, field, decision, error):
        """Remember an unavailable literal without inferring a more specific identity."""
        if (isinstance(error,forms.UnavailableOption) and field.kind=='combobox' and field.options and
                forms.strict_option(decision.value,field.options) is None):
            self.unavailable_choices[key(field)]=(self.signature(field),decision.value)

    def reconcile_choices(self, fields, answers, missing):
        """Ask once when a tried source literal cannot identify an offered choice."""
        for field in fields:
            failed=self.unavailable_choices.get(key(field))
            decision=answers[field.id]
            if (failed and failed==(self.signature(field),decision.value) and field.options and
                    forms.strict_option(decision.value,field.options) is None):
                answers[field.id]=Decision(None,reason='saved answer does not identify an offered choice')
                if field not in missing:missing.append(field)

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
                re.search(r'\b(?:code|pin|passcode|token|otp|password|captcha)\b',
                          norm(' '.join((field.label,field.help,field.section))))): return None
        frame = self._live.view.frames.get(field.frame) if self._live and self._live.view else None
        address = urlsplit(getattr(frame, 'url', ''))
        frame_url = urlunsplit((address.scheme, address.netloc, address.path, '', ''))
        # A restarted draft can begin at a different wizard position. The
        # locally counted page number is not the identity of that portal step.
        return digest({'headings': [norm(h) for h in self._live.view.headings] if self._live else [],
                       'frame_url': frame_url, 'schema': self.decision_schema(field)})

    def evidence(self, decision):
        """Fingerprint bound facts, or conservatively the broader narrative/policy."""
        ids = [x for x in decision.fact_id.split(',') if x]
        # Fresh HITL facts are scoped before the next compiled-facts refresh.
        # Include them now, so that refresh does not change their fingerprint
        # from the broad fallback to the exact same newly supplied fact.
        facts = {f.id: asdict(f) for f in [*self.facts,*self.session_facts.values()]}
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
        super().observe_view(view)
        for field in view.fields:
            if field.kind=='combobox' and (field.options or field.options_state=='complete'):
                self.option_inventory[key(field)]=(list(field.options),field.options_state)
        semantic = [self.semantic_id(f) for f in view.fields]
        counts = Counter(semantic)
        current = {}
        for field, ident in zip(view.fields, semantic):
            field_key = key(field)
            spec = self.signature(field)
            current[digest(field_key)] = {'schema': self.decision_schema(field), 'semantic_id': ident,
                                          'invalid': bool(field.invalid)}
            previous = self.bindings.get(field_key)
            decision = self.cache.get(field_key)
            changed = previous and (previous['schema'] != spec or
                                    decision and previous['evidence'] != self.evidence(decision))
            if previous and (changed or field.invalid):
                self.cache.pop(field_key, None)
                self.rendered.pop(field_key, None)
                self.human_answers.pop((self.step, field_key), None)
                if changed:
                    self.failures.pop(field_key, None)
                    self.technical_failures.pop(field_key, None)
                    self.declined.discard((self.step,field_key))
                    if decision:self._forget_humans(set(decision.fact_id.split(',')))
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
        stale = {ident for ident,spec in self.human_scopes.items()
                 if (fact:=self.session_facts.get(ident)) is not None and any(
                     norm(field.label) in {norm(alias) for alias in fact.aliases}
                     and (fact.target_key is None or fact.target_key==key(field))
                     and (not entity_scope(fact.section) or entity_scope(fact.section)==entity_scope(field.section))
                     and self.signature(field)!=spec for field in fields)}
        self._forget_humans(stale)
        decisions = super().plan(fields, allow_human)
        replaced = set()
        for field in fields:
            decision = decisions[field.id]
            fact = self.session_facts.get(decision.fact_id)
            if fact is None or fact.instructions or decision.value is None:continue
            if (fact.target_key==key(field) and self.human_scopes.get(fact.id)==self.signature(field)):
                continue
            # The shared planner's global question alias can collapse two
            # same-labelled human replies. Give each literal its own live field
            # and semantic scope before another round can resolve from it.
            ident = 'human:'+digest({'step':self.step,'field':key(field),'schema':self.decision_schema(field)})[:24]
            self.session_facts[ident] = replace(fact,id=ident,value=decision.value,variants=[],target_key=key(field))
            self.session_fact_steps[ident] = self.step
            replaced.add(fact.id)
            decisions[field.id] = replace(decision,fact_id=ident)
            self.cache[key(field)] = decisions[field.id]
            self.human_answers[(self.step,key(field))] = decisions[field.id]
        self._forget_humans(replaced)
        for field in fields:
            for ident in decisions[field.id].fact_id.split(','):
                if ident in self.session_facts:self.human_scopes[ident]=self.signature(field)
            self.bindings[key(field)] = {'schema': self.signature(field),
                                         'evidence': self.evidence(decisions[field.id])}
        return decisions


    def _forget_humans(self, ids):
        """A literal reply to an earlier question scope cannot answer changed help/options."""
        ids = ids & self.session_facts.keys()
        if not ids:return
        for ident in ids:
            self.session_facts.pop(ident,None);self.session_fact_steps.pop(ident,None)
            self.human_scopes.pop(ident,None)
        self.facts=[fact for fact in self.facts if fact.id not in ids]
        for bank in (self.cache,self.human_answers):
            for ident,decision in list(bank.items()):
                if ids & set(decision.fact_id.split(',')):bank.pop(ident,None)

    def can_execute(self, planned, current, decision):
        """Refuse dispatch if a rerender changed options, question meaning or evidence."""
        bound = self.bindings.get(key(planned))
        # File decisions are created by the shared executor after planning.
        if planned.kind == 'file': return self.signature(planned) == self.signature(current)
        valid = bool(super().can_execute(planned,current,decision) and bound and bound['schema'] == self.signature(current)
                     and bound['evidence'] == self.evidence(decision))
        if not valid:
            self.cache.pop(key(planned), None)
            self.events.append({'event': 'decision_invalidated_before_dispatch', 'kind': current.kind})
        return valid

    def can_reuse(self, field):
        """A restored proposal still needs a fresh execution/readback record."""
        return key(field) not in self.resumed_keys and super().can_reuse(field)

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
        live=getattr(self,'_live',None)
        if live and live.view:
            exhausted={f.label for f in live.view.fields if self.failures.get(key(f),0)>=2}
            result['blockers']=['control repair budget exhausted: '+b if b in exhausted else b
                                for b in result['blockers']]
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
