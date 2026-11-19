"""An absent or unreviewed pilot cannot satisfy rollout gates."""
import pytest
from jobagent.experiments.adaptive_acceptance import validate,report,FAMILIES


def cases():
    return [{'id':f'{family}-{i}','family':family,'employer':f'{family}-employer-{i}',
             'url':f'https://example.test/{family}/{i}','split':'development' if i<2 else 'held_out',
             'order':['adaptive','experimental'] if i%2 else ['experimental','adaptive']}
            for family in sorted(FAMILIES) for i in range(4)]


def test_missing_runs_are_pending_and_never_promote(tmp_path):
    result=report({'cases':cases()},tmp_path)
    assert len(result['pending'])==40 and not result['filling_pilot_passed'] and not result['defaults_changed']
    assert result['summary']['adaptive']['active_s']['median'] is None


def test_held_out_employer_may_not_leak_into_development():
    sample=cases();sample[2]['employer']=sample[0]['employer']
    with pytest.raises(ValueError,match='distinct|both'):validate(sample)
