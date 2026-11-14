"""Fresh evaluation layouts defined after prototype tuning; freeze before provider trials."""


def heldout(case, fact):
    cases=[]
    cases.append(case('heldout-contact-alias',
        '<fieldset><legend>Contact details</legend><label for=e>Your email address</label><input id=e type=email required>'
        '<label for=p>Preferred contact method</label><select id=p required><option>Choose</option><option>Email</option><option>Phone</option></select></fieldset>',
        [fact('Email','person@example.test')],{'#e':'person@example.test','#p':'Email'},True))
    cases[-1]['human_answers']={'Preferred contact method':'Email'}
    cases.append(case('heldout-fetched-question',
        '''<label for=c>Country</label><select id=c onchange="fetch('https://fixture.example/questions').then(r=>r.json()).then(d=>document.getElementById('new').innerHTML='<label for=s>Province or State</label><input id=s required>')"><option>Choose</option><option>United States</option></select><div id=new></div>''',
        [fact('Country','United States'),fact('State','Texas')],{'#c':'United States','#s':'Texas'},True))
    cases[-1]['prepared_address']=True
    cases.append(case('heldout-same-filename-documents',
        '<section><h2>Resume</h2><label for=r>Resume</label><input type=file id=r required></section>'
        '<section><h2>Cover Letter</h2><label for=l>Cover Letter</label><input type=file id=l required></section>',[],
        {'sha:#r':'resume','sha:#l':'cover_letter'},True))
    cases[-1]['same_filename']=True
    html=[];given=[];expected={}
    for i in range(10):
        section=('Employment' if i<5 else 'Education')+f' entry {i%5+1}'
        html.append('<section><h2>'+section+'</h2>')
        for j,label in enumerate(('Organization','Role' if i<5 else 'Degree','City','Start Date','End Date')):
            ident=f'f{i}_{j}'
            value=[f'Example organization {i}',f'Example qualification {i}',f'Example city {i}','01/01/2020','12/31/2021'][j]
            html.append(f'<label for={ident}>{label}</label><input id={ident} required>')
            given.append(fact(label,value,section));expected['#'+ident]=value
        html.append('</section>')
    cases.append(case('heldout-fifty-fields',''.join(html),given,expected,True))
    cases[-1]['prepared_address']=True
    return cases
