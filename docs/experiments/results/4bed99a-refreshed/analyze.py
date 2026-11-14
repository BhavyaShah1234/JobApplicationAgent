"""Independent audit of frozen raw trials. Does not supply answers or modify trial artifacts."""
import json,statistics,sys,re
from pathlib import Path
root=Path(sys.argv[1]);rows=json.loads((root/'trials.json').read_text());cases={c['name']:c for c in json.loads((root/'cases.json').read_text())}
report={}
for engine in ('current','candidate'):
    arm=[r for r in rows if r['engine']==engine];per_case={};semantic=[]
    def correct(r):
        # Formatting-only telephone equality; all other field/control and provenance checks remain strict.
        expected=cases[r['fixture']]['expected'];actual=r.get('actual',{})
        if r.get('error') or set(actual)!=set(expected):return False
        for selector,want in expected.items():
            if selector.startswith(('sha:','file:','closed:','frame:')):
                # The transport grader compares document bytes / shadow or frame values, already preserved per trial.
                # Only a phone-only mismatch can be normalized here, so defer other cases to original value grade.
                if not r.get('values_correct',r.get('graded_correct',False)):return False
                continue
            got=actual.get(selector)
            if got==want:continue
            if r['fixture']=='fresh-compiled-frame-wizard' and selector=='#ph':
                got_digits=re.sub(r'\D','',str(got));want_digits=re.sub(r'\D','',str(want))
                if len(got_digits)==11 and got_digits.startswith('1'):got_digits=got_digits[1:]
                if got_digits==want_digits:
                    semantic.append({'pair_index':r.get('pair_index'),'field':selector,'reason':'same telephone digits; formatting/country-prefix only'})
                    continue
            return False
        return not r.get('missing_required_questions')
    audited={id(r):correct(r) for r in arm}
    necessary=avoidable=0
    for r in arm:
        declared=cases[r['fixture']].get('human_answers',{});seen=set()
        for q in r.get('question_labels',[]):
            if q in declared and q not in seen:necessary+=1;seen.add(q)
            else:avoidable+=1
    for name in dict.fromkeys(r['fixture'] for r in arm):
        subset=[r for r in arm if r['fixture']==name]
        per_case[name]={'n':len(subset),'median_active_s':statistics.median(r['active_s'] for r in subset),
            'raw_correct':sum(r.get('graded_correct',False) for r in subset),
            'audited_correct':sum(audited[id(r)] for r in subset),'ready':sum(bool(r.get('ready')) for r in subset),
            'ready_and_audited_correct':sum(bool(r.get('ready')) and audited[id(r)] for r in subset),
            'questions':sum(r.get('questions',0) for r in subset),'provider_calls':sum(r.get('model_calls',0) or 0 for r in subset)}
    report[engine]={'n':len(arm),'raw_correct':sum(r.get('graded_correct',False) for r in arm),
        'values_correct':sum(r.get('values_correct',False) for r in arm),'audited_correct':sum(audited.values()),
        'ready':sum(bool(r.get('ready')) for r in arm),
        'ready_and_audited_correct':sum(bool(r.get('ready')) and audited[id(r)] for r in arm),
        'false_ready_audited':sum(bool(r.get('ready')) and not audited[id(r)] for r in arm),
        'median_active_s':statistics.median(r['active_s'] for r in arm),'total_active_s':sum(r['active_s'] for r in arm),
        'necessary_questions':necessary,'avoidable_questions':avoidable,'questions':sum(r.get('questions',0) for r in arm),
        'provider_calls':sum(r.get('model_calls',0) or 0 for r in arm),'reported_tokens':sum(r.get('reported_tokens',0) or 0 for r in arm),
        'heldout_audited_correct':sum(audited[id(r)] for r in arm if r.get('heldout')),
        'exceptions':sum('error' in r for r in arm),'semantic_adjustments':semantic,'per_fixture':per_case}
report['median_time_reduction_pct']=100*(1-report['candidate']['median_active_s']/report['current']['median_active_s'])
report['no_submit_clicks']=all(not r.get('submitted',False) for r in rows)
(root/'audit.json').write_text(json.dumps(report,indent=2))
for k,v in report.items():print(k, json.dumps({a:b for a,b in v.items() if a!='per_fixture'}) if isinstance(v,dict) else v)
