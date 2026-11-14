"""Second provider cohort: eight development cases and four fresh compiled-profile layouts.

Freeze this file before observing provider outcomes. Later fixes make these development cases.
"""
import copy
import html
import json


FINAL = '<button type=button onclick="window.sent=true">Apply</button>'


def refreshed(previous):
    wanted=('text','travel','identity','date','uploads','conditional',
            'heldout-same-filename-documents','heldout-fifty-fields')
    by_name={c['name']:c for c in previous}
    cases=[copy.deepcopy(by_name[n]) for n in wanted]
    for c in cases:
        c['heldout']=False
        c['name']=c['name'].replace('heldout-','regression-')

    def fresh(name, markup, applicant, expected, human=None):
        c=dict(name=name,html=markup,facts=[],applicant=applicant,raw_jd='',
               expected=expected,heldout=True,human_answers=human or {})
        c['required_human_questions']=list(c['human_answers'])
        return c

    cases.append(fresh('fresh-compiled-contact',
        '<fieldset><legend>Contact information</legend>'
        '<label for=g>Given Name</label><input id=g required>'
        '<label for=f>Family Name</label><input id=f required>'
        '<label for=e>Email address</label><input id=e type=email required>'
        '<label for=p>Preferred interview format</label><select id=p required>'
        '<option value=placeholder disabled selected>Select your preferred interview format</option>'
        '<option>Live video</option><option>Phone</option></select></fieldset>'+FINAL,
        {'Contact':[{'question':'Legal name','answer':{'first':'Casey','last':'Morgan'}},
                    {'question':'Email address','answer':'casey@example.test'}]},
        {'#g':'Casey','#f':'Morgan','#e':'casey@example.test','#p':'Live video'},
        {'Preferred interview format':'Live video'}))

    inner=('<label for=s>School</label><input id=s required>'
           '<label for=d>Degree</label><input id=d required>'
           '<label for=m>Field of Study</label><input id=m required>')
    cases.append(fresh('fresh-compiled-shadow-education',
        '<h2>Education 1</h2><academic-form></academic-form><script>'
        'document.querySelector("academic-form").attachShadow({mode:"closed"}).innerHTML='
        +json.dumps(inner)+';</script>'+FINAL,
        {'Education':[{'question':'Degrees','answer':[{'school':'Example Technical University',
            'degree':'Master of Science','field':'Computer Science'}]}]},
        {'closed:#s':'Example Technical University','closed:#d':'Master of Science','closed:#m':'Computer Science'}))

    first=('<label for=mail>Email address</label><input id=mail type=email required>'
           '<button type=button onclick="parent.nextStage(document.getElementById(\'mail\').value)">Next</button>')
    last='<label for=ph>Phone Number</label><input id=ph type=tel required>'+FINAL
    cases.append(fresh('fresh-compiled-frame-wizard',
        '<h1>Contact</h1><input id=ledger type=hidden><iframe id=step height=180 srcdoc="'
        +html.escape(first,quote=True)+'"></iframe><div id=next></div><script>'
        'function nextStage(email){document.getElementById("ledger").value=email;'
        'document.getElementById("step").remove();document.getElementById("next").innerHTML='
        +json.dumps(last)+';}</script>',
        {'Contact':[{'question':'Email address','answer':'casey@example.test'},
                    {'question':'Phone','answer':{'number':'2025550148'}}]},
        {'#ledger':'casey@example.test','#ph':'2025550148'}))

    date=('<label for=start>Available start date</label><input id=start required '
          'placeholder="MM/DD/YYYY" pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}">')
    cases.append(fresh('fresh-compiled-conditional-date',
        '<label for=job>Employment type</label><select id=job required onchange="'
        +html.escape('document.getElementById("detail").innerHTML=this.value==="Full time"?'
                      +json.dumps(date)+':""',quote=True)+'">'
        '<option value="" selected>Select employment type</option><option>Full time</option>'
        '<option>Part time</option></select><div id=detail></div>'+FINAL,
        {'Preferences':[{'question':'Employment type','answer':'Full time'},
            {'question':'Available from','seen':['Available start date'],'answer':'January 2027',
             'instructions':'Month only. Ask for the exact day if the form requires a full date; do not assume one.'}]},
        {'#job':'Full time','#start':'01/04/2027'},
        {'Available start date':'01/04/2027'}))
    return cases
