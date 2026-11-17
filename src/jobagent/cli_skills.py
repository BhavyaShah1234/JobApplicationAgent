"""`jobagent skills ...`: build, grow and inspect the skill graph (step 4c)."""

import glob
import json
from pathlib import Path

import typer


def register(app: typer.Typer, console) -> None:
    skills_app = typer.Typer(no_args_is_help=True, help="The skill graph: Role -> Duty -> Skill, built from the postings the agent has read.")
    app.add_typer(skills_app, name="skills")

    skipped: list[str] = []

    def postings() -> list[tuple[dict, str, Path]]:
        """Every saved posting with its role id: the discovery match recorded in the queue, else the first role whose title pattern fits."""
        from jobagent import db
        from jobagent.config import get_settings
        from jobagent.tools import matching
        from jobagent.tools import skillgraph as SG
        s, conn = get_settings(), db.connect()
        roles = matching.load_roles().get("roles", [])
        embed = SG.default_embed(s)
        out = []
        for f in sorted(glob.glob(str(s.outputs_dir / "*" / "*" / "jd.json"))):
            try:
                jd = json.loads(Path(f).read_text())
            except (OSError, json.JSONDecodeError):
                continue
            row = conn.execute("SELECT role_id FROM jobs WHERE url = ?", (jd.get("source_url") or "",)).fetchone()
            rid = (row["role_id"] if row and row["role_id"] else "") or SG.role_for(jd, roles, embed)
            if rid:
                out.append((jd, rid, Path(f).parent))
            else:
                skipped.append(f"{jd.get('company', '')} - {jd.get('title', '')}")
        return out

    @skills_app.command("build")
    def build(no_merge: bool = typer.Option(False, "--no-merge", help="Skip the duty consolidation call."),
              rebuild: bool = typer.Option(False, "--rebuild", help="Start from an empty graph (the embedding cache is kept).")) -> None:
        """Create or extend the graph from data/target_roles.yaml and every saved posting (safe to rerun: postings already in it are skipped)."""
        from jobagent.config import get_settings
        from jobagent.log import runlog
        from jobagent.tools import matching, skillgraph as SG
        skipped.clear()
        log, s = runlog("skills"), get_settings()
        found = postings()
        try:
            with SG.locked(s, 30):                                # pipelines adding a posting wait (or skip) while the whole build runs
                g = SG.Graph() if rebuild else SG.load(s)
                before = "" if rebuild else g.content_hash()
                stats = SG.build(g, matching.load_roles().get("roles", []), [(jd, rid) for jd, rid, _ in found], embed=SG.default_embed(s),
                                 merge=None if no_merge else SG.default_merge, log=log, duty_threshold=s.skills_duty_merge)
                changed = SG.save(g, f"build: {stats['postings']} posting(s)", before, s)
        except TimeoutError as exc:
            log(f"skills: {exc}; try again in a moment")
            raise typer.Exit(1) from exc
        log(f"skills: {len(g.of_kind('role'))} role(s), {len(g.of_kind('duty'))} duty(ies), {len(g.of_kind('skill'))} skill(s); "
            f"{stats['postings']} new posting(s), {stats['skipped']} already known, {stats['failed']} failed; "
            f"{'saved as version ' + str(g.version) if changed else 'nothing changed'}")
        if stats['category_defaults']:
            log(f"skills: {stats['category_defaults']} unknown legacy category(ies) recorded as generic 'tool'; identities and edges preserved")
        for name in skipped:
            log(f"skills: no role fits '{name}'; left out")
        for rid in stats["roles_without_postings"]:
            log(f"skills: role '{rid}' has no postings yet: run `jobagent skills sync`")
        if stats["failed"]:
            raise typer.Exit(1)

    @skills_app.command("sync")
    def sync(limit: int = typer.Option(5, help="Postings to read per role without any."),
             discover: bool = typer.Option(True, "--discover/--no-discover", help="Look for postings at already-known companies when the queue has none for the role.")) -> None:
        """For each role in data/target_roles.yaml that has no postings in the graph, collect some (scrape only, never apply) and add them."""
        from jobagent import db
        from jobagent.log import runlog
        from jobagent.tools import skillgraph as SG
        from jobagent.tools.pipeline import Pipeline
        log, conn = runlog("skills"), db.connect()
        from jobagent.tools import matching
        g = SG.load()
        have = {p_["role"] for p_ in g.postings.values()}
        pipe = Pipeline(conn=conn, log=log)
        failed, none_found, worked = [], [], 0
        for r in matching.load_roles().get("roles", []):
            if r["id"] in have:
                continue
            urls = [x["url"] for x in conn.execute("SELECT url FROM jobs WHERE role_id = ? AND status IN ('queued','needs_review') "
                                                    "ORDER BY COALESCE(match_score,0) DESC LIMIT ?", (r["id"], limit))]
            if not urls and discover:
                names = [x[0] for x in conn.execute("SELECT DISTINCT company FROM jobs WHERE company IS NOT NULL AND company != '' LIMIT 40")]
                log(f"skills: no queued postings for '{r['id']}'; looking at {len(names)} known company(ies)")
                from jobagent.tools import discovery
                discovery.discover_many(names, log=log, only_us=True, judge="model")
                urls = [x["url"] for x in conn.execute("SELECT url FROM jobs WHERE role_id = ? AND status IN ('queued','needs_review') "
                                                        "ORDER BY COALESCE(match_score,0) DESC LIMIT ?", (r["id"], limit))]
            if not urls:
                log(f"skills: no postings found for role '{r['id']}'")
                none_found.append(r["id"])
                continue
            for u in urls:
                try:
                    res = pipe.run(u, through="scrape")
                except Exception as exc:                            # one posting's failure never stops the others
                    failed.append(f"{u[:70]}: {type(exc).__name__}")
                    log(f"skills: {r['id']}: {u[:70]} failed: {type(exc).__name__}: {str(exc)[:100]}")
                    continue
                log(f"skills: {r['id']}: {u[:70]} -> {res.state}")
                if res.code or res.state in ("failed", "blocked", "unavailable", "needs_review"):
                    failed.append(f"{u[:70]}: {res.state}")
                else:
                    worked += 1
        log(f"skills: {worked} posting(s) collected; {len(failed)} failed; roles with nothing found: {', '.join(none_found) or 'none'}")
        log("skills: run `jobagent skills build` to fold the new postings in (scraping already adds each one as it is read)")
        if failed:
            raise typer.Exit(1)

    @skills_app.command("show")
    def show(role: str = typer.Option(None, help="Only this role id.")) -> None:
        """Roles, their duties and the skills under each duty."""
        from rich.tree import Tree

        from jobagent.tools import skillgraph as SG
        g = SG.load()
        if not g.nodes:
            console.print("skills: the graph is empty; run `jobagent skills build`")
            return
        tree = Tree(f"skill graph v{g.version}: {len(g.of_kind('skill'))} skills, {len(g.of_kind('duty'))} duties")
        for r in g.of_kind("role"):
            if role and r["id"] != f"role:{role}":
                continue
            rb = tree.add(f"[bold]{r['name']}[/]")
            for duty, w in sorted(g.children(r["id"]), key=lambda x: -x[1]):
                db_ = rb.add(f"{g.nodes[duty]['name']} [dim]({w})[/]")
                for sid, sw in sorted(g.children(duty), key=lambda x: -x[1])[:25]:
                    db_.add(f"{g.nodes[sid]['name']} [dim]({sw})[/]")
        console.print(tree)

    @skills_app.command("explain")
    def explain(skill: str) -> None:
        """Where a skill sits: its duties, roles and closest neighbours."""
        from jobagent.tools import skillgraph as SG
        g = SG.load()
        sid = g.find("skill", skill)
        if not sid:
            console.print(f"skills: '{skill}' is not in the graph")
            raise typer.Exit(1)
        n = g.nodes[sid]
        console.print(f"{n['name']} ({n['category'] or 'uncategorised'}); also written: {', '.join(n['aliases']) or '-'}")
        for duty, w in sorted(g.parents(sid), key=lambda x: -x[1]):
            roles = [g.nodes[r]["name"] for r, _ in g.parents(duty)]
            mates = [g.nodes[c]["name"] for c, _ in sorted(g.children(duty), key=lambda x: -x[1]) if c != sid][:8]
            console.print(f"  duty '{g.nodes[duty]['name']}' ({w} posting(s)); roles: {', '.join(roles)}; with: {', '.join(mates)}", highlight=False)

    @skills_app.command("suggest")
    def suggest(job_dir: Path) -> None:
        """Neighbouring skills for a job folder's posting: the ones you have (usable on the resume) and the ones to confirm."""
        from jobagent import db
        from jobagent.tools import skillgraph as SG, tailor
        jd = json.loads((job_dir / "jd.json").read_text())
        row = db.connect().execute("SELECT role_id FROM jobs WHERE url = ?", (jd.get("source_url") or "",)).fetchone()
        terms: list[str] = []
        if (job_dir / "resume.tex").exists():
            r = tailor.parse((job_dir / "resume.tex").read_text())
            terms = [t.strip() for _, items in r.skills for t in tailor.to_plain(items).split(",") if t.strip()]
        res = SG.job_suggestions(job_dir, jd, row["role_id"] if row and row["role_id"] else "", terms)
        if not res:
            console.print("skills: nothing to suggest (graph empty, switched off, or no skill of this posting is in it)")
            return
        for k, title in (("owned", "you have these (may fill spare room)"), ("review", "not in your files (confirm with `skills approve`)")):
            console.print(f"{title}:")
            for x in res[k]:
                console.print(f"  {x['skill']}  via {x['via']}  ({x['score']})", highlight=False)

    @skills_app.command("approve")
    def approve(skill: str) -> None:
        """Say you have this skill: the next tailoring may use it (recorded in state/skills/approved.json; your data files are not edited)."""
        from jobagent.tools import skillgraph as SG
        console.print(f"skills: {skill} {'approved' if SG.approve(skill) else 'was already approved'}")

    @skills_app.command("export")
    def export(role: str = typer.Option(None), out: Path = typer.Option(None, help="Default: state/skills/graph.mmd")) -> None:
        """Write the graph as a Mermaid diagram (Role --> Duty --> Skill)."""
        from jobagent.tools import skillgraph as SG
        g = SG.load()
        path = out or SG.skills_dir() / "graph.mmd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SG.mermaid(g, role))
        console.print(f"skills: wrote {path}")
