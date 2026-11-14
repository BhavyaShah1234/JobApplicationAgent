import json, statistics
from pathlib import Path
root=Path('/tmp/form-provider-registered-collected')
d=json.loads((root/'trials.json').read_text())
report={}
for engine in ('current','candidate'):
    rows=[r for r in d if r['engine']==engine]
    unsupported=[{'trial_index':i,'fixture':r['fixture'],'issue':'contact preference filled without supplied fact or owner answer'} for i,r in enumerate(d) if r['engine']==engine and r['fixture']=='heldout-contact-alias' and r.get('actual',{}).get('#p') not in ('','Choose',None) and 'Preferred contact method' not in r.get('question_labels',[])]
    report[engine]={
        'trials':len(rows),'values_correct':sum(r['graded_correct'] for r in rows),
        'source_and_values_correct':sum(r['graded_correct'] and not (r['fixture']=='heldout-contact-alias' and 'Preferred contact method' not in r.get('question_labels',[])) for r in rows),
        'ready':sum(bool(r.get('ready')) for r in rows),
        'false_ready_values':sum(r.get('ready',False) and not r['graded_correct'] for r in rows),
        'unsupported_answer_observations':unsupported,
        'median_active_s_all':statistics.median(r['active_s'] for r in rows),
        'median_active_s_values_correct':statistics.median(r['active_s'] for r in rows if r['graded_correct']),
        'total_active_s':sum(r['active_s'] for r in rows),
        'questions':sum(r.get('questions',0) for r in rows),
        'avoidable_questions':sum(r.get('avoidable_questions',0) for r in rows),
        'necessary_questions':sum(r.get('necessary_questions',0) for r in rows),
        'provider_calls':sum(r.get('model_calls',0) or 0 for r in rows),
        'provider_reported_tokens':sum(r.get('reported_tokens',0) or 0 for r in rows),
        'browser_or_model_errors':sum('error' in r for r in rows),
        'heldout_values_correct':sum(r['graded_correct'] for r in rows if r.get('heldout')),
        'heldout_trials':sum(bool(r.get('heldout')) for r in rows),
    }
    report[engine]['per_fixture']={}
    for name in dict.fromkeys(r['fixture'] for r in rows):
        subset=[r for r in rows if r['fixture']==name]
        report[engine]['per_fixture'][name]={'n':len(subset),'median_active_s':statistics.median(r['active_s'] for r in subset),
             'values_correct':sum(r['graded_correct'] for r in subset),'ready':sum(bool(r.get('ready')) for r in subset),
             'avoidable_questions':sum(r.get('avoidable_questions',0) for r in subset),
             'necessary_questions':sum(r.get('necessary_questions',0) for r in subset),
             'provider_calls':sum(r.get('model_calls',0) or 0 for r in subset)}
report['median_active_time_reduction_percent']=100*(1-report['candidate']['median_active_s_all']/report['current']['median_active_s_all'])
report['avoidable_question_reduction_percent']=100*(1-report['candidate']['avoidable_questions']/report['current']['avoidable_questions']) if report['current']['avoidable_questions'] else None
report['no_final_clicks']=all(not r.get('submitted',False) for r in d)
(root/'audit.json').write_text(json.dumps(report,indent=2))
for engine in ('current','candidate'):
    print(engine,json.dumps({k:v for k,v in report[engine].items() if k!='per_fixture'}))
print('Time reduction %',report['median_active_time_reduction_percent'])
