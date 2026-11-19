"""Locked, unsubmitted 20-posting acceptance report; no browser or submit capability.

Run `python -m jobagent.experiments.adaptive_acceptance MANIFEST.json REPORT.json`.
The manifest names paired application.json files and independent review records.
Missing/unreviewed observations keep promotion pending, never count as successes.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
from collections import Counter
from jobagent.tools.artifact_cache import digest, write_private

FAMILIES = {'workday', 'greenhouse', 'lever', 'ashby', 'successfactors'}


def validate(cases):
    """Require the agreed sample and employer-level held-out split before scoring."""
    if len(cases) != 20: raise ValueError('pilot requires exactly 20 frozen postings')
    if Counter(c['family'] for c in cases) != Counter({f:4 for f in FAMILIES}):
        raise ValueError('pilot requires four employers in each of the five portal families')
    if Counter(c['split'] for c in cases) != Counter({'development':10,'held_out':10}):
        raise ValueError('pilot requires ten development and ten held-out cases')
    if len({c['id'] for c in cases}) != 20 or len({c['url'] for c in cases}) != 20:
        raise ValueError('postings and case IDs must be distinct')
    for family in FAMILIES:
        group=[c for c in cases if c['family']==family]
        if len({c['employer'].strip().casefold() for c in group}) != 4:
            raise ValueError('each family requires four distinct employers')
    dev={c['employer'].strip().casefold() for c in cases if c['split']=='development'}
    held={c['employer'].strip().casefold() for c in cases if c['split']=='held_out'}
    if dev & held: raise ValueError('an employer cannot occur in both development and held-out cases')
    for case in cases:
        if sorted(case['order']) != ['adaptive','experimental']:
            raise ValueError('each case must record both strategies in their actual execution order')
    if sum(c['order'][0]=='adaptive' for c in cases) != 10:
        raise ValueError('counterbalance execution order: ten cases per first strategy')


def distribution(values):
    """Median and nearest-rank p90; unavailable observations are not zeros."""
    values=sorted(v for v in values if v is not None)
    return {'n':len(values),'median':statistics.median(values) if values else None,
            'p90':values[math.ceil(.9*len(values))-1] if values else None}


def report(manifest, root):
    """Aggregate matched runs and explicit human audits, without promoting defaults."""
    cases=manifest['cases'];validate(cases)
    summary={arm:{'active_s':[],'total_s':[],'model_starts':[],'reported_tokens':[],
                  'technical_unassisted':0,'reviewed':0,'critical_errors':0} for arm in ('experimental','adaptive')}
    pending=[];held_regressions=[];observations=[]
    for case in cases:
        pair={}
        for arm in summary:
            path=Path(root)/case.get(arm,'missing-result.json')
            review=case.get('review',{}).get(arm,{})
            if not path.is_file(): pending.append(f"{case['id']}:{arm}: missing result");continue
            result=json.loads(path.read_text());metric=result.get('filling_metrics',{})
            active=metric.get('active_filling_s')
            pair[arm]={'ready':result.get('ready_to_submit') is True,'active_s':active,'review':review}
            record=summary[arm]
            record['active_s'].append(active);record['total_s'].append(metric.get('elapsed_filling_s'))
            record['model_starts'].append(metric.get('writer_model_starts'))
            record['reported_tokens'].append(metric.get('writer_tokens_reported'))
            if (result.get('filling_strategy') != arm or not case.get('matched_input_fingerprint') or
                    not case.get('model') or not case.get('session_notes')):
                pending.append(f"{case['id']}:{arm}: strategy/input/model/session provenance incomplete")
            if not review.get('complete') or not review.get('reviewer'):
                pending.append(f"{case['id']}:{arm}: independent answer audit incomplete");continue
            record['reviewed']+=1
            record['critical_errors']+=review.get('critical_errors',0)
            if (review.get('technical_assists')==0 and pair[arm]['ready'] and
                    review.get('correct_required')==review.get('total_required') and
                    review.get('total_required',0)>0 and review.get('critical_errors')==0):
                record['technical_unassisted']+=1
            if any(k not in review for k in ('correct_required','total_required','critical_errors','technical_assists',
                                             'missing_fact_questions','repeated_questions')):
                pending.append(f"{case['id']}:{arm}: review metrics incomplete")
            if active is None:pending.append(f"{case['id']}:{arm}: active timing unavailable")
        if case['split']=='held_out' and len(pair)==2:
            old,new=pair['experimental']['review'],pair['adaptive']['review']
            if old.get('complete') and new.get('complete') and (
                    new.get('correct_required',0) < old.get('correct_required',0) or
                    new.get('critical_errors',0) > old.get('critical_errors',0)):
                held_regressions.append(case['id'])
        observations.append({'id':case['id'],'family':case['family'],'split':case['split'],'pair':pair})
    for arm in summary:
        for metric in ('active_s','total_s','model_starts','reported_tokens'):
            summary[arm][metric]=distribution(summary[arm][metric])
    old,new=summary['experimental']['active_s'],summary['adaptive']['active_s']
    speed=bool(old['median'] and new['median'] is not None and new['median'] <= .8*old['median']
               and new['p90'] <= old['p90'])
    gates={'complete_reviewed_sample':not pending,'zero_observed_critical_errors':summary['adaptive']['critical_errors']==0,
           'at_least_18_technically_unassisted':summary['adaptive']['technical_unassisted']>=18,
           'no_held_out_correctness_regression':not held_regressions,
           '20_percent_median_gain_without_p90_regression':speed}
    return {'version':1,'manifest_sha256':digest(manifest),'summary':summary,'pending':pending,
            'held_out_regressions':held_regressions,'gates':gates,
            'filling_pilot_passed':all(gates.values()),'defaults_changed':False,
            'scope':'This frozen sample only; document-generation gate is evaluated separately.',
            'observations':observations}


def main():
    """Write a private JSON report from a frozen sample and its reviewed results."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    write_private(args.output,report(json.loads(args.manifest.read_text()),args.manifest.parent))


if __name__=='__main__':main()
