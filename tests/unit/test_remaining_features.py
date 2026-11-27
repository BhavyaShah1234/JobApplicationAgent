"""The owner-requested remaining F15/F17/F25 work: relationships, in-process execution and real closed-root controls."""
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagent import db
from jobagent.steps import submit


def test_relationships_use_stable_ids_and_enforce_parent_integrity(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite")
    jd = {"company": "Acme", "title": "Engineer", "source_url": "https://acme.test/jobs/1", "apply_url": "https://apply.acme.test/1"}
    submit.record(c, Path("outputs/acme/1"), jd, state="filled")
    app = c.execute("SELECT * FROM applications").fetchone()
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("SELECT url FROM jobs WHERE id=?", (app["job_id"],)).fetchone()[0] == jd["source_url"]
    c.execute("INSERT INTO submission_attempts(job_dir,attempt_no,state,started_at) VALUES(?,1,'not_sent','now')", (app["job_dir"],))
    c.commit()
    assert c.execute("SELECT application_id FROM submission_attempts").fetchone()[0] == app["id"]
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("DELETE FROM applications WHERE id=?", (app["id"],))
    c.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE applications SET job_id=99999 WHERE id=?", (app["id"],))
    c.rollback()
    assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_relationship_migration_preserves_legacy_attempts_and_is_idempotent(tmp_path):
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE applications(id INTEGER PRIMARY KEY,job_dir TEXT UNIQUE,company TEXT,title TEXT,url TEXT,portal TEXT,state TEXT,filled_at TEXT,decided_at TEXT,submitted_at TEXT,confirmation_id TEXT,confirmation TEXT,confirmed_at TEXT,notes TEXT);"
                    "CREATE TABLE submission_attempts(id INTEGER PRIMARY KEY,job_dir TEXT,attempt_no INTEGER,state TEXT,payload_sha TEXT,payload_path TEXT,approved_at TEXT,started_at TEXT,clicked_at TEXT,finished_at TEXT,evidence TEXT,notes TEXT,UNIQUE(job_dir,attempt_no));"
                    "INSERT INTO submission_attempts(id,job_dir,attempt_no,state,started_at) VALUES(17,'outputs/legacy/1',1,'unknown','then');")
    c.close()
    c = db.connect(path)
    row = c.execute("SELECT t.id,t.application_id,a.job_id,a.state FROM submission_attempts t JOIN applications a ON a.id=t.application_id").fetchone()
    assert row["id"] == 17 and row["state"] == "submission_unknown" and row["job_id"]
    before = tuple(row)
    c.close()
    c = db.connect(path)
    assert tuple(c.execute("SELECT t.id,t.application_id,a.job_id,a.state FROM submission_attempts t JOIN applications a ON a.id=t.application_id").fetchone()) == before
    assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_pipeline_invokes_the_same_callbacks_in_process_and_closes_context(monkeypatch):
    import typer
    from jobagent import main
    from jobagent.tools.pipeline import Pipeline
    app = typer.Typer()
    seen = []
    @app.command("research")
    def command(ctx: typer.Context, companies: list[str], workers: int = 5):
        seen.append((os.getpid(), companies, workers))
        ctx.call_on_close(lambda: seen.append("closed"))
    @app.command("other")
    def other():
        pass
    monkeypatch.setattr(main, "app", app)
    assert Pipeline._invoke("research", companies=["Acme"]) == 0
    assert seen == [(os.getpid(), ["Acme"], 5), "closed"]


@pytest.mark.parametrize('resolver_upgrade',[False,True])
def test_pipeline_resumes_scrape_and_research_without_spawning_processes(tmp_path,monkeypatch,resolver_upgrade):
    from jobagent.tools.pipeline import Pipeline
    from jobagent.tools import places
    resolved=[]
    resolve_job=places.resolve_job
    def observe_resolution(*args,**kwargs):
        resolved.append(places.RESOLVER_VERSION)
        return resolve_job(*args,**kwargs)
    monkeypatch.setattr(places,'resolve_job',observe_resolution)
    url = "https://acme.test/jobs/1"
    s = SimpleNamespace(state_dir=tmp_path / "state", outputs_dir=tmp_path / "outputs")
    c = db.connect(tmp_path / "jobs.sqlite")
    calls = []
    d = s.outputs_dir / "acme" / "1"
    def invoke(stage, **kw):
        calls.append(stage)
        if stage == "scrape":
            d.mkdir(parents=True)
            (d / "jd.json").write_text(json.dumps({"source_url": url, "company": "Acme"}))
            (d / "jd.txt").write_text("Posting")
            c.execute("INSERT INTO jobs(url,company,job_id,status) VALUES(?,'Acme','1','scraped')", (url,))
            c.commit()
        elif stage == "research":
            p = s.state_dir / "companies"; p.mkdir(parents=True)
            for ext in (".json", ".md"):
                (p / ("acme" + ext)).write_text('{"name":"Acme"}' if ext == ".json" else "profile")
        elif stage == "apply":
            p = d / "application"; p.mkdir(exist_ok=True)
            (p / "application.json").write_text(json.dumps({"state": "review", "ready_to_submit": True}))
        return 0
    pipeline = Pipeline(c, s, log=lambda _: None, invoke=invoke)
    assert pipeline.run(url).state == "filled"
    if resolver_upgrade:
        monkeypatch.setattr(places,'RESOLVER_VERSION',places.RESOLVER_VERSION+1)
    assert pipeline.run(url).state == "filled"
    assert calls.count("scrape") == calls.count("research") == 1
    assert calls.count("apply") == 2  # reread browser state, never cache an external form as already filled
    assert len(resolved)==(2 if resolver_upgrade else 1)
    assert {"scrape", "research"} <= json.loads((d / "workflow.json").read_text())["stages"].keys()
    stale = d / "application" / "application.json"
    os.utime(stale, (1, 1))
    pipeline.invoke = lambda stage, **kw: 1 if stage == "apply" else invoke(stage, **kw)
    assert pipeline.run(url).state == "failed"  # an earlier ready form cannot override a current pre-fill failure


def test_refreshed_posting_content_still_invalidates_company_research(tmp_path):
    """Ignoring derived location metadata must not hide a changed source posting."""
    from jobagent.tools.pipeline import Pipeline
    url='https://acme.test/jobs/1'
    settings=SimpleNamespace(state_dir=tmp_path/'state',outputs_dir=tmp_path/'outputs')
    conn=db.connect(tmp_path/'jobs.sqlite')
    job=settings.outputs_dir/'acme'/'1'
    posting={'source_url':url,'company':'Acme','title':'Engineer'}
    calls=[]
    def invoke(stage,**kwargs):
        calls.append(stage)
        if stage=='scrape':
            job.mkdir(parents=True,exist_ok=True)
            (job/'jd.json').write_text(json.dumps(posting))
            (job/'jd.txt').write_text('Posting')
            with conn:
                conn.execute("INSERT INTO jobs(url,company,job_id,status) VALUES(?,'Acme','1','scraped') "
                             "ON CONFLICT(url) DO UPDATE SET status='scraped'",(url,))
        elif stage=='research':
            directory=settings.state_dir/'companies';directory.mkdir(parents=True,exist_ok=True)
            (directory/'acme.json').write_text(json.dumps({'name':'Acme'}))
            (directory/'acme.md').write_text('Profile for '+posting['title'])
        elif stage=='apply':
            (job/'application').mkdir(exist_ok=True)
            (job/'application/application.json').write_text(json.dumps({'state':'review','ready_to_submit':True}))
        return 0
    pipeline=Pipeline(conn,settings,log=lambda _:None,invoke=invoke)
    try:
        assert pipeline.run(url).state=='filled'
        # An independent refresh changed the on-disk posting after the first run.
        posting['title']='Platform Engineer'
        (job/'jd.json').write_text(json.dumps(posting))
        assert pipeline.run(url).state=='filled'
        assert calls.count('scrape')==calls.count('research')==2
        assert pipeline.run(url).state=='filled'
        assert calls.count('scrape')==calls.count('research')==2
    finally:
        conn.close()


def test_existing_closed_roots_remain_closed_and_are_read_and_filled(tmp_path):
    from playwright.sync_api import sync_playwright
    from jobagent.tools import forms, shadow
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        page = ctx.new_page()
        page.set_content("""<div id=host></div><script>document.querySelector('#host').attachShadow({mode:'closed'}).innerHTML =
          `<label>Name<input name=fullname required></label><label>State<select><option>Arizona</option><option>California</option></select></label>
           <fieldset><legend>Available?</legend><label><input type=radio name=available value=Yes>Yes</label><label><input type=radio name=available value=No>No</label></fieldset>
           <label>Resume<input type=file></label>`;</script>""")
        shadow.install(pw, ctx)
        view = forms.read_page(page)
        assert view.coverage.complete and len(view.fields) == 4
        assert page.evaluate("() => document.querySelector('#host').shadowRoot === null")
        values = {"text": "Example Candidate", "select": "California", "radio": "Yes"}
        pdf = tmp_path / "resume.pdf"; pdf.write_bytes(b"test PDF bytes")
        for f in view.fields:
            forms.fill_field(page, view, f, "resume" if f.kind == "file" else values[f.kind], {"resume": str(pdf)})
        after = forms.read_page(page)
        assert {f.value for f in after.fields} >= {"Example Candidate", "California", "Yes"}
        assert after.coverage.complete
        b.close()


def test_closed_root_options_and_later_created_closed_roots_are_accessible():
    from playwright.sync_api import sync_playwright
    from jobagent.tools import forms, shadow
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(); shadow.install(pw, ctx)
        page = ctx.new_page()
        page.set_content("""<div id=h></div><script>
          const r=document.querySelector('#h').attachShadow({mode:'closed'});
          r.innerHTML='<button role="combobox" aria-label="Source" aria-haspopup="listbox">Select One</button><div id=opts style="display:none"><div role="option">Alpha</div><div role="option">Beta</div></div>';
          r.querySelector('button').onclick=()=>r.querySelector('#opts').style.display='block';
          r.querySelectorAll('[role=option]').forEach(e=>e.onclick=()=>{r.querySelector('button').textContent=e.textContent;r.querySelector('#opts').style.display='none'});
        </script>""")
        view = forms.read_page(page)
        forms.discover_options(page, view, log=lambda _: None)
        f = next(f for f in view.fields if f.kind == "combobox")
        assert f.options == ["Alpha", "Beta"]
        assert forms.fill_field(page, view, f, "Beta", {}) == "Beta"
        page.evaluate("() => {const h=document.body.appendChild(document.createElement('div'));h.attachShadow({mode:'closed'}).innerHTML='<label>Late field<input name=late></label>'}")
        after = forms.read_page(page)
        late = next(f for f in after.fields if f.label == "Late field")
        forms.fill_field(page, after, late, "read dynamically", {})
        assert next(f for f in forms.read_page(page).fields if f.label == "Late field").value == "read dynamically"
        assert page.evaluate("() => document.querySelector('#h').shadowRoot === null")
        b.close()
