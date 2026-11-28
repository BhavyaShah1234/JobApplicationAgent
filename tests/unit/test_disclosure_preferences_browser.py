"""The native filler replaces stale demographic/sponsorship selections."""
import pytest

from jobagent.steps import apply
from test_filling_strategy import world, SourceModel


@pytest.mark.parametrize('strategy',['experimental','adaptive'])
def test_native_filler_honors_privacy_and_sponsorship_without_submitting(world,strategy):
    labels=['Gender','Race / ethnicity','Hispanic or Latino?','Veteran status','Disability status',
            'Do you require visa sponsorship?']
    world.data['preferences']=[{'question':label,'answer':'Yes' if i==5 else 'Prefer not to say'}
                               for i,label in enumerate(labels)]
    refusal=["I don't wish to answer",'Decline to self-identify','Prefer not to say',
             'I choose not to disclose','I do not want to answer','Yes']
    wrong=['Male','Asian','No','Not a veteran','No','No']
    fields=''.join(f'<label>{label}<select id="q{i}" required><option selected>{wrong[i]}</option>'
                   f'<option>{refusal[i]}</option></select></label>' for i,label in enumerate(labels))
    world.setup('<h1>Application</h1>'+fields+
        '<button onclick="window.sent++">Submit application</button><script>window.sent=0</script>')
    result=apply.run(world.page,world.job,'fixture',model=SourceModel(),
                     filling_strategy=strategy,log=lambda _:None)
    assert result['state']=='review',result.get('blockers')
    assert [world.page.locator(f'#q{i}').input_value() for i in range(6)]==refusal
    assert world.page.evaluate('window.sent')==0
