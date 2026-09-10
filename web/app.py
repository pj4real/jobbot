"""The control panel. Everything the CLI does, from one page on localhost.

    python run.py web     ->  http://127.0.0.1:8000

Design rule that keeps this honest: the dashboard never reimplements an action.
Every button shells out to the same find.py, mail.py or fill.py you would type,
so the guards in core/send.py and fill/filler.py are the only guards that exist
and there is no second path to anything dangerous.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db                                    # noqa: E402
from core.config import config                         # noqa: E402
from core import send as sender                        # noqa: E402
from web import runner, editor, files, gmail_oauth, profileform  # noqa: E402

app = FastAPI(title="jobbot")


class NotReady(Exception):
    """The database is not usable yet. Recoverable, so say so plainly."""


def rows(sql, args=()):
    import sqlite3
    try:
        with db.tx() as c:
            return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            raise NotReady(str(e)) from e
        raise


@app.exception_handler(NotReady)
def not_ready(request, exc):
    return page(f"""
<div class="note bad">
  <b>The database is not set up yet.</b><br>
  <span class="mono small">{exc}</span>
</div>
<p>Run this once, in the project folder:</p>
<pre>python run.py init</pre>
<p class="small dim">This happens when a first init was interrupted: the file
gets created but the tables never land in it. Init is safe to rerun as often
as you like, it only fills in what is missing.</p>
<div class="row"><a class="btn primary" href="/">try again</a></div>
""", "")


# ------------------------------------------------------------------ chrome

CSS = """
:root{--bg:#f6f7f8;--card:#fff;--ink:#14191c;--dim:#5a666d;--line:#e2e6e9;
--acc:#10566b;--accbg:#ddebf0;--warn:#a25708;--warnbg:#f7e9d6;--bad:#93321f;
--badbg:#f6e2dd;--ok:#1c6b4a;--okbg:#dff0e7;--term:#0e1416;--termink:#c8d6da}
@media(prefers-color-scheme:dark){:root{--bg:#0f1417;--card:#171d21;--ink:#e2e8eb;
--dim:#a0adb4;--line:#2a343a;--acc:#5fb4ce;--accbg:#12303b;--warn:#e0a55c;
--warnbg:#33240f;--bad:#e08573;--badbg:#351b15;--ok:#5cc79b;--okbg:#11321f;
--term:#0a0f11;--termink:#b8c8cd}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;padding:0 20px 90px}
.wrap{max-width:1200px;margin:0 auto}
header{display:flex;align-items:center;gap:20px;flex-wrap:wrap;
padding:22px 0 14px;border-bottom:2px solid var(--ink)}
h1{margin:0;font-size:19px;letter-spacing:-.02em}
h2{font-size:15px;margin:30px 0 10px;letter-spacing:-.01em}
h3{font-size:13px;margin:22px 0 6px;color:var(--dim);text-transform:uppercase;
letter-spacing:.07em}
nav{display:flex;gap:2px;flex-wrap:wrap}
nav a{color:var(--dim);text-decoration:none;font-size:13px;padding:5px 10px;
border-radius:5px}
nav a:hover{background:var(--card)}
nav a.on{color:var(--acc);background:var(--accbg);font-weight:600}
.spacer{flex:1}
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 4px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:6px;
padding:9px 13px;min-width:86px}
.stat b{display:block;font-size:19px;font-variant-numeric:tabular-nums}
.stat span{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}
.stat.hot b{color:var(--warn)}
table{width:100%;border-collapse:collapse;margin:8px 0 0;font-size:13px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
color:var(--dim);padding:0 10px 7px 0;border-bottom:1.5px solid var(--line)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:top}
td.n{font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--acc)}
.pill{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.05em;
padding:2px 6px;border-radius:3px;border:1px solid currentColor;white-space:nowrap}
.p-needs_review{color:var(--warn)}.p-approved{color:var(--ok)}
.p-submitted{color:var(--acc)}.p-rejected,.p-blocked{color:var(--bad)}
.p-replied{color:var(--ok)}
button,.btn{font:inherit;font-size:12.5px;padding:6px 12px;border-radius:6px;
border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;
text-decoration:none;display:inline-block;line-height:1.3}
button:hover,.btn:hover{border-color:var(--acc);color:var(--acc)}
button:disabled{opacity:.45;cursor:not-allowed}
button.go{border-color:var(--ok);color:var(--ok)}
button.no,.btn.no{border-color:var(--bad);color:var(--bad)}
button.danger{border-color:var(--bad);color:#fff;background:var(--bad)}
button.danger:hover{opacity:.9;color:#fff}
button.primary{border-color:var(--acc);background:var(--accbg);color:var(--acc);font-weight:600}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:7px;padding:14px}
.card h4{margin:0 0 4px;font-size:13.5px}
.card p{margin:0 0 10px;font-size:12px;color:var(--dim);line-height:1.45}
pre{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);
border-radius:5px;padding:14px 16px;white-space:pre-wrap;font-size:12.5px;
font-family:ui-monospace,Menlo,monospace;overflow-x:auto;margin:8px 0}
#log{background:var(--term);color:var(--termink);border:1px solid var(--line);
border-radius:7px;padding:14px 16px;font:12px/1.6 ui-monospace,Menlo,monospace;
white-space:pre-wrap;min-height:220px;max-height:62vh;overflow-y:auto;margin:10px 0}
#log .err{color:#e08573}#log .ok{color:#5cc79b}#log .warn{color:#e0a55c}
textarea{width:100%;min-height:60vh;font:12.5px/1.6 ui-monospace,Menlo,monospace;
padding:14px;border:1px solid var(--line);border-radius:7px;background:var(--card);
color:var(--ink);tab-size:2}
textarea.short{min-height:300px}
input[type=text],input[type=url]{width:100%;padding:9px 11px;border:1px solid var(--line);
border-radius:6px;background:var(--card);color:var(--ink);font:inherit}
.dim{color:var(--dim)}.small{font-size:12px}.mono{font-family:ui-monospace,Menlo,monospace}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;width:52px}
.bar i{display:block;height:100%;background:var(--acc)}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--warn);
border-radius:6px;padding:12px 14px;margin:12px 0;font-size:13px}
.note.bad{border-left-color:var(--bad)}
.note.ok{border-left-color:var(--ok)}
.note.info{border-left-color:var(--acc)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.dot.run{background:var(--warn);animation:pulse 1s infinite}
.dot.ok{background:var(--ok)}.dot.err{background:var(--bad)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@media(prefers-reduced-motion:reduce){.dot.run{animation:none}}
form.inline{display:inline}
label.f{display:block;font-size:12px;color:var(--dim);margin:12px 0 4px}
"""

JS = """
let poll=null, curRun=null;
function el(i){return document.getElementById(i)}
function paint(lines){
  const L=el('log'); if(!L) return;
  L.innerHTML = lines.map(t=>{
    const e=t.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    if(/\\[error\\]|FAIL|Traceback|error:|\\bXX\\b|!/.test(t)) return '<span class="err">'+e+'</span>';
    if(/\\bok\\b|passed|SENT|\\[ok\\]/.test(t)) return '<span class="ok">'+e+'</span>';
    if(/blocked|needs you|warn|DRY RUN/.test(t)) return '<span class="warn">'+e+'</span>';
    return e;
  }).join('\\n');
  L.scrollTop = L.scrollHeight;
}
function setStatus(txt, cls){
  const s=el('runstatus'); if(s) s.innerHTML='<span class="dot '+cls+'"></span>'+txt;
}
async function tick(){
  if(!curRun) return;
  const r = await fetch('/api/run/'+curRun); if(!r.ok) return;
  const d = await r.json();
  paint(d.lines);
  if(d.awaiting_release){
    setStatus(d.label+' - browser open, waiting for you','run');
    el('doneBtn').style.display='inline-block';
  } else if(d.done){
    clearInterval(poll); poll=null;
    setStatus(d.label+' - finished'+(d.code?' (exit '+d.code+')':''), d.code?'err':'ok');
    el('doneBtn').style.display='none';
    document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=false);
    if(el('refreshAfter')) setTimeout(()=>{ if(d.code===0) location.reload(); }, 900);
  } else {
    setStatus(d.label+' - running','run');
  }
}
async function run(job, extra){
  document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=true);
  paint(['starting '+job+' ...']);
  const body = new URLSearchParams(); if(extra) body.set('extra', extra);
  const r = await fetch('/api/run/'+job, {method:'POST', body});
  if(!r.ok){ paint(['could not start: '+await r.text()]);
    document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=false); return; }
  const d = await r.json(); curRun=d.run_id;
  if(poll) clearInterval(poll); poll=setInterval(tick,700); tick();
}
async function fillUrl(){
  const u=el('fillurl').value.trim(); if(!u) return;
  document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=true);
  const body=new URLSearchParams(); body.set('url',u);
  const r=await fetch('/api/fill',{method:'POST',body});
  const d=await r.json(); curRun=d.run_id;
  if(poll) clearInterval(poll); poll=setInterval(tick,700); tick();
}
async function done(){
  if(!curRun) return;
  await fetch('/api/run/'+curRun+'/release',{method:'POST'});
  el('doneBtn').style.display='none';
}
async function stopRun(){ if(curRun) await fetch('/api/run/'+curRun+'/stop',{method:'POST'}); }
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='s'){const f=el('editform'); if(f){e.preventDefault();f.submit();}}
});
"""

TABS = [("/", "queue"), ("/run", "run"), ("/review", "review"),
        ("/applications", "applications"), ("/profile", "profile"),
        ("/files", "files"), ("/gmail", "gmail"), ("/settings", "settings"),
        ("/setup", "setup"), ("/stats", "stats")]


def page(body: str, tab: str = "", with_js: bool = False) -> HTMLResponse:
    nav = "".join(f'<a href="{href}"{" class=on" if name == tab else ""}>{name}</a>'
                  for href, name in TABS)
    c = db.counts()
    badge = ""
    if c.get("app:needs_review"):
        badge = (f'<span class="pill p-needs_review">{c["app:needs_review"]} '
                 f'waiting</span>')
    js = f"<script>{JS}</script>" if with_js else ""
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>jobbot</title><style>{CSS}</style></head><body><div class="wrap">
<header><h1>jobbot</h1><nav>{nav}</nav><div class="spacer"></div>{badge}</header>
{body}</div>{js}</body></html>""")


def stat(label, value, hot=False):
    return (f'<div class="stat{" hot" if hot else ""}"><b>{value}</b>'
            f'<span>{label}</span></div>')


LOG_PANEL = """
<div class="row">
  <span id="runstatus" class="small dim">idle</span>
  <div class="spacer"></div>
  <button id="doneBtn" onclick="done()" class="go" style="display:none">
    Done, close the browser</button>
  <button onclick="stopRun()" class="no">stop</button>
</div>
<div id="log" class="dim">Output appears here.</div>
"""


# ------------------------------------------------------------------ queue

@app.get("/", response_class=HTMLResponse)
def queue(all: int = 0):
    floor = 0.0 if all else float(config()["scoring"].get("min_fit_to_draft", 0.55))
    js = rows(
        "SELECT j.id, j.title, j.fit_score, j.fit_reason, j.location, j.apply_kind,"
        "       j.apply_url, j.apply_email, j.deadline, co.name company,"
        "       ap.status, ap.id app_id"
        "  FROM jobs j LEFT JOIN companies co ON co.id=j.company_id"
        "  LEFT JOIN applications ap ON ap.job_id=j.id"
        " WHERE j.fit_score >= ? ORDER BY j.fit_score DESC, j.id DESC LIMIT 200", (floor,))
    c = db.counts()
    head = '<div class="stats">' + "".join([
        stat("jobs", c.get("jobs", 0)),
        stat("unparsed", c.get("raw_unprocessed", 0), c.get("raw_unprocessed", 0) > 0),
        stat("needs review", c.get("app:needs_review", 0), c.get("app:needs_review", 0) > 0),
        stat("approved", c.get("app:approved", 0)),
        stat("sent today", c.get("sent_today", 0)),
        stat("cap", config()["limits"]["emails_per_day"]),
    ]) + "</div>"

    trs = []
    for j in js:
        pct = int((j["fit_score"] or 0) * 100)
        status = (f'<span class="pill p-{j["status"]}">{j["status"]}</span>'
                  if j["status"] else "")
        acts = []
        if j["apply_url"]:
            acts.append(f'<button data-job="fill" onclick="run_fill(\'{j["id"]}\')">'
                        f'fill</button>')
            acts.append(f'<a class="btn" href="{j["apply_url"]}" target="_blank" '
                        f'rel="noopener">open</a>')
        if j["app_id"]:
            acts.append(f'<a class="btn" href="/review/{j["app_id"]}">draft</a>')
        trs.append(
            f'<tr><td class="n">{j["id"]}</td>'
            f'<td class="n"><div class="bar"><i style="width:{pct}%"></i></div>'
            f'<span class="small dim">{j["fit_score"]:.2f}</span></td>'
            f'<td><b>{j["company"] or "?"}</b><br>'
            f'<span class="small dim">{j["fit_reason"] or ""}</span></td>'
            f'<td>{j["title"] or ""}<br><span class="small dim">'
            f'{j["location"] or ""}{" · " + j["deadline"] if j["deadline"] else ""}'
            f'{" · " + j["apply_email"] if j["apply_email"] else ""}</span></td>'
            f'<td class="small">{j["apply_kind"] or "-"}</td>'
            f'<td>{status}</td><td class="n">{" ".join(acts)}</td></tr>')

    toggle = ('<a class="btn" href="/">only good fits</a>' if all
              else '<a class="btn" href="/?all=1">show everything</a>')
    empty = ('<tr><td colspan=7 class="dim">Nothing yet. '
             'Go to <a href="/run">run</a> and press Find openings.</td></tr>')
    return page(head + f"""
<div class="row">
  <button data-job onclick="run('find')" class="primary">Find new openings</button>
  <button data-job onclick="run('draft')">Draft outreach</button>
  <span id="refreshAfter"></span>
  {toggle}
</div>
{LOG_PANEL}
<h2>Job queue</h2>
<table><tr><th>id</th><th>fit</th><th>company</th><th>role</th><th>via</th>
<th>status</th><th></th></tr>{"".join(trs) or empty}</table>
<script>
function run_fill(id){{ fetch('/api/fill_job/'+id,{{method:'POST'}})
  .then(r=>r.json()).then(d=>{{ curRun=d.run_id;
    if(poll) clearInterval(poll); poll=setInterval(tick,700); tick(); }}); }}
</script>
""", "queue", with_js=True)


# ------------------------------------------------------------------ run

@app.get("/run", response_class=HTMLResponse)
def run_page(show: str = ""):
    def card(job, title, desc, cls=""):
        return (f'<div class="card"><h4>{title}</h4><p>{desc}</p>'
                f'<button data-job="{job}" class="{cls}" onclick="run(\'{job}\')">'
                f'run</button></div>')

    cfg = config()
    live_warn = ""
    if cfg["safety"].get("dry_run", True):
        live_warn = ('<div class="note">Live send is refused while '
                     '<span class="mono">safety.dry_run</span> is true in config. '
                     'That is the second switch, and it is on purpose. Change it in '
                     '<a href="/settings/config">settings</a> when you have read '
                     'ten drafts.</div>')

    recent = runner.recent(8)
    rec = "".join(
        f'<tr><td class="small mono">{r["id"]}</td><td class="small">{r["label"]}</td>'
        f'<td>{"<span class=pill p-approved>done</span>" if r["done"] and not r["code"] else ("<span class=pill p-rejected>exit " + str(r["code"]) + "</span>" if r["done"] else "<span class=pill p-needs_review>running</span>")}</td>'
        f'<td class="small dim n">{time.strftime("%H:%M:%S", time.localtime(r["started"]))}</td>'
        f'<td class="n"><a class="btn" href="/run?show={r["id"]}">log</a></td></tr>'
        for r in recent)

    replay = ""
    if show:
        r = runner.get(show)
        if r:
            body = "\n".join(r["lines"]) or "(no output)"
            state = "finished" if r["done"] else "running"
            code = f" exit {r['code']}" if r["done"] and r["code"] else ""
            esc = body.replace("&", "&amp;").replace("<", "&lt;")
            replay = (f'<h2>{r["label"]}</h2>'
                      f'<p class="small dim mono">{r["cmd"]} &middot; {state}{code}</p>'
                      f'<pre>{esc}</pre>'
                      f'<div class="row"><a class="btn" href="/run">back to controls</a></div>')
        else:
            replay = ('<div class="note">That run is gone. Runs are kept in memory '
                      'only, so restarting the dashboard clears them.</div>')

    return page(replay + f"""
<h2>Fill a form</h2>
<p class="small dim">Paste any application URL. A browser opens, every field it
recognises gets typed in, and it waits. It never presses submit.</p>
<div class="row">
  <input type="url" id="fillurl" placeholder="https://boards.greenhouse.io/..."
         style="max-width:560px" onkeydown="if(event.key==='Enter')fillUrl()">
  <button data-job onclick="fillUrl()" class="primary">Fill it</button>
</div>

{LOG_PANEL}

<h2>Find</h2>
<div class="grid">
  {card("find", "Find new openings", "Collect from Gmail and WhatsApp, parse, score. The daily one.", "primary")}
  {card("collect", "Collect only", "Pull new mail into the raw table, parse nothing.")}
  {card("extract", "Parse pending", "Turn raw items into jobs. Uses the model for anything regex missed.")}
  {card("extract_norm", "Parse, regex only", "Same, no model calls. Use when you are near your plan limit.")}
  {card("rescore", "Rescore everything", "After you change scoring rules in settings.")}
</div>

<h2>Mail</h2>
{live_warn}
<div class="grid">
  {card("draft", "Draft outreach", "For every job above the fit floor that has a verified address.")}
  {card("draft_tpl", "Draft from template", "Same, no model call. Predictable and instant.")}
  {card("send_dry", "Dry run the queue", "Shows exactly what would go out. Sends nothing.")}
  {card("send_live", "Send for real", "Only approved rows, only within today's cap.", "danger")}
  {card("track", "Check for replies", "Reads the threads you sent on and records outcomes.")}
</div>

<h2>Housekeeping</h2>
<div class="grid">
  {card("doctor", "Check the setup", "What is missing and what each gap costs you.")}
  {card("tests", "Run the tests", "41 checks, mostly that the guards still refuse things.")}
  {card("status", "Counts per stage", "Where everything currently sits.")}
</div>

<h2>Recent runs</h2>
<table><tr><th>id</th><th>what</th><th></th><th>started</th><th></th></tr>
{rec or '<tr><td colspan=5 class="dim">Nothing run yet this session.</td></tr>'}</table>
""", "run", with_js=True)


# ------------------------------------------------------------------ run api

@app.post("/api/run/{job}")
async def api_run(job: str, request: Request):
    form = await request.form()
    extra = str(form.get("extra") or "").strip()
    try:
        run_id = runner.start(job, shlexish(extra))
    except KeyError:
        return JSONResponse({"error": f"unknown job {job}"}, status_code=404)
    return {"run_id": run_id}


def shlexish(s: str) -> list[str]:
    import shlex
    return shlex.split(s) if s else []


@app.get("/api/run/{run_id}")
def api_run_status(run_id: str):
    r = runner.get(run_id)
    if not r:
        return JSONResponse({"error": "no such run"}, status_code=404)
    return r


@app.post("/api/run/{run_id}/release")
def api_release(run_id: str):
    return {"released": runner.release(run_id)}


@app.post("/api/run/{run_id}/stop")
def api_stop(run_id: str):
    return {"stopped": runner.stop(run_id)}


@app.post("/api/fill")
async def api_fill(request: Request):
    form = await request.form()
    url = str(form.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "not a url"}, status_code=400)
    run_id, _ = runner.start_fill(url)
    return {"run_id": run_id}


@app.post("/api/fill_job/{job_id}")
def api_fill_job(job_id: int):
    r = rows("SELECT apply_url FROM jobs WHERE id=?", (job_id,))
    if not r or not r[0]["apply_url"]:
        return JSONResponse({"error": "no apply url"}, status_code=404)
    run_id, _ = runner.start_fill(r[0]["apply_url"])
    db.log("job", job_id, "fill_launched", url=r[0]["apply_url"])
    return {"run_id": run_id}


# ------------------------------------------------------------------ review

REVIEW_SQL = """
SELECT a.id, a.status, a.job_id, d.subject, d.body, d.id draft_id, d.evidence_ids,
       j.title, j.fit_score, j.apply_url, co.name company_name,
       c.email to_email, c.name to_name, c.verified, r.path resume_path
  FROM applications a
  JOIN drafts d ON d.id=a.draft_id
  JOIN jobs j ON j.id=a.job_id
  LEFT JOIN companies co ON co.id=j.company_id
  LEFT JOIN contacts c ON c.id=d.contact_id
  LEFT JOIN resumes r ON r.id=a.resume_id
"""


@app.get("/review", response_class=HTMLResponse)
def review_list(status: str = "needs_review"):
    apps = rows(REVIEW_SQL + " WHERE a.status=? ORDER BY j.fit_score DESC", (status,))
    tabs = " ".join(
        f'<a class="btn" href="/review?status={s}">{s.replace("_"," ")}</a>'
        for s in ("needs_review", "approved", "rejected", "submitted"))
    if not apps:
        return page(f'<div class="row">{tabs}</div>'
                    f'<div class="note info">Nothing with status {status}. '
                    f'Draft some from <a href="/run">run</a>.</div>', "review", True)
    trs = "".join(
        f'<tr><td class="n">{a["id"]}</td><td class="n">{a["fit_score"]:.2f}</td>'
        f'<td><b>{a["company_name"]}</b><br>'
        f'<span class="small dim">{a["title"]}</span></td>'
        f'<td class="small">{a["to_email"] or "<i>no address</i>"}'
        f'{"" if a["verified"] else " <span class=pill p-rejected>unverified</span>"}</td>'
        f'<td class="small">{Path(a["resume_path"]).name if a["resume_path"] else "-"}</td>'
        f'<td class="n"><a class="btn" href="/review/{a["id"]}">read</a></td></tr>'
        for a in apps)
    bulk = ""
    if status == "needs_review":
        bulk = ('<form method="post" action="/review/approve_all" class="inline">'
                '<button class="go">approve all ' + str(len(apps)) + '</button></form>')
    return page(f'<div class="row">{tabs}<div class="spacer"></div>{bulk}</div>'
                f'<h2>{status.replace("_"," ")} ({len(apps)})</h2><table>'
                f'<tr><th>app</th><th>fit</th><th>company</th><th>to</th>'
                f'<th>resume</th><th></th></tr>{trs}</table>', "review", True)


@app.get("/review/{app_id}", response_class=HTMLResponse)
def review_one(app_id: int):
    r = rows(REVIEW_SQL + " WHERE a.id=?", (app_id,))
    if not r:
        return page('<div class="note bad">Not found.</div>', "review", True)
    a = r[0]
    ev = ", ".join(json.loads(a["evidence_ids"] or "[]")) or "-"
    warn = ""
    if not a["to_email"]:
        warn = ('<div class="note bad">No recipient address. The mailer will '
                'block this one.</div>')
    elif not a["verified"]:
        warn = ('<div class="note bad">Contact is unverified. The mailer refuses '
                'unverified addresses, because a bounce costs sender reputation '
                'and you cannot buy that back.</div>')
    elif a["resume_path"] and not Path(a["resume_path"]).exists():
        warn = (f'<div class="note">Resume missing at '
                f'<span class="mono">{a["resume_path"]}</span>. The mail would go '
                f'out with no attachment.</div>')
    if "FILL" in (a["body"] or ""):
        warn += ('<div class="note bad">This draft still contains a FILL '
                 'placeholder. Fix the evidence lines in '
                 '<a href="/settings/profile">profile</a>.</div>')

    nxt = rows("SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id"
               " WHERE a.status='needs_review' AND a.id != ?"
               " ORDER BY j.fit_score DESC LIMIT 1", (app_id,))
    nxt_btn = (f'<a class="btn" href="/review/{nxt[0]["id"]}">next waiting</a>'
               if nxt else "")

    return page(f"""
<h2>{a["company_name"]} &middot; {a["title"]}</h2>
<p class="small dim">application {a["id"]} &middot; status {a["status"]} &middot;
fit {a["fit_score"]:.2f} &middot; to {a["to_email"] or "none"} &middot;
evidence {ev} &middot;
resume {Path(a["resume_path"]).name if a["resume_path"] else "none"}</p>
{warn}
<form method="post" action="/review/{a["id"]}/save" id="editform">
  <label class="f">subject</label>
  <input type="text" name="subject" value="{(a["subject"] or "").replace('"','&quot;')}">
  <label class="f">body</label>
  <textarea name="body" class="short">{a["body"] or ""}</textarea>
  <div class="row">
    <button type="submit">save edits</button>
    <button type="submit" class="go" name="action" value="approve">save and approve</button>
    <button type="submit" class="no" name="action" value="reject">reject</button>
    {nxt_btn}
    <a class="btn" href="/review">back to list</a>
  </div>
</form>
<p class="small dim">Approving only marks the row. Sending is a separate step on
the <a href="/run">run</a> page, and it dry runs first. Cmd-S saves.</p>
""", "review", with_js=True)


@app.post("/review/{app_id}/save")
def review_save(app_id: int, subject: str = Form(""), body: str = Form(""),
                action: str = Form("")):
    with db.tx() as c:
        row = c.execute("SELECT draft_id FROM applications WHERE id=?",
                        (app_id,)).fetchone()
        if row:
            c.execute("UPDATE drafts SET subject=?, body=?, edited=1 WHERE id=?",
                      (subject.strip(), body.strip(), row["draft_id"]))
        if action in ("approve", "reject"):
            c.execute("UPDATE applications SET status=? WHERE id=?",
                      ("approved" if action == "approve" else "rejected", app_id))
    if action:
        db.log("application", app_id, action)
    return RedirectResponse("/review" if action else f"/review/{app_id}",
                            status_code=303)


@app.post("/review/approve_all")
def approve_all():
    with db.tx() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM applications WHERE status='needs_review'")]
        c.execute("UPDATE applications SET status='approved'"
                  " WHERE status='needs_review'")
    for i in ids:
        db.log("application", i, "approved", bulk=True)
    return RedirectResponse("/review?status=approved", status_code=303)


# ------------------------------------------------------------------ settings

@app.get("/settings", response_class=HTMLResponse)
def settings():
    cards = "".join(
        f'<div class="card"><h4>{name}</h4><p>{desc}</p>'
        f'<a class="btn" href="/settings/{key}">edit</a></div>'
        for key, (name, _, desc) in editor.EDITABLE.items())
    cfg = config()
    return page(f"""
<h2>Files you can edit here</h2>
<p class="small dim">Nothing is written unless it parses, and every save keeps
the previous version in data/backups.</p>
<div class="grid">{cards}</div>

<h2>Current safety settings</h2>
<pre>dry_run                {cfg['safety'].get('dry_run')}
never_auto_submit      {cfg['safety'].get('never_auto_submit')}
require_verified       {cfg['safety'].get('require_verified_contact')}
emails_per_day         {cfg['limits']['emails_per_day']}
sent today             {sender.sent_today()}
min_gap_seconds        {cfg['limits'].get('min_gap_seconds')}
company_cooldown_days  {cfg['limits'].get('company_cooldown_days')}
min_fit_to_draft       {cfg['scoring'].get('min_fit_to_draft')}
resume mode            {cfg.get('resume', {}).get('mode')}</pre>
""", "settings")


@app.get("/settings/{key}", response_class=HTMLResponse)
def settings_edit(key: str, saved: str = "", error: str = ""):
    if key not in editor.EDITABLE:
        return page('<div class="note bad">Not editable.</div>', "settings")
    name, kind, desc = editor.EDITABLE[key]
    text = editor.read(key)
    msg = ""
    if saved:
        msg = f'<div class="note ok">{saved}</div>'
    if error:
        msg = f'<div class="note bad">Not saved. {error}</div>'
    bks = "".join(
        f'<form method="post" action="/settings/{key}/restore" class="inline">'
        f'<input type="hidden" name="stamp" value="{b["stamp"]}">'
        f'<button class="small">{b["stamp"]}</button></form> '
        for b in editor.backups(key))
    return page(f"""
<h2>{name}</h2>
<p class="small dim">{desc} Validated as {kind} before anything is written.</p>
{msg}
<form method="post" action="/settings/{key}" id="editform">
  <textarea name="text" spellcheck="false">{text.replace("&","&amp;").replace("<","&lt;")}</textarea>
  <div class="row">
    <button type="submit" class="primary">save</button>
    <a class="btn" href="/settings">back</a>
    <div class="spacer"></div>
    <span class="small dim">Cmd-S also saves</span>
  </div>
</form>
{f'<h3>restore a previous version</h3><div class="row">{bks}</div>' if bks else ''}
""", "settings", with_js=True)


@app.post("/settings/{key}")
def settings_save(key: str, text: str = Form("")):
    if key not in editor.EDITABLE:
        return RedirectResponse("/settings", status_code=303)
    ok, msg = editor.save(key, text)
    from urllib.parse import quote
    q = f"saved={quote(msg)}" if ok else f"error={quote(msg)}"
    return RedirectResponse(f"/settings/{key}?{q}", status_code=303)


@app.post("/settings/{key}/restore")
def settings_restore(key: str, stamp: str = Form("")):
    ok, msg = editor.restore(key, stamp)
    from urllib.parse import quote
    q = f"saved={quote(msg)}" if ok else f"error={quote(msg)}"
    return RedirectResponse(f"/settings/{key}?{q}", status_code=303)


# ------------------------------------------------------------------ setup

@app.get("/setup", response_class=HTMLResponse)
def setup():
    from core.config import path, profile, flat_profile
    checks = []

    def add(label, ok, hint="", fix=""):
        checks.append((label, ok, hint, fix))

    p = path("assets", "resume_default.pdf")
    add("assets/resume_default.pdf", p.exists(),
        "The resume that gets attached and uploaded. Upload your off-campus PDF.",
        "/files")
    OPT = {"gender", "portfolio"}
    blanks = [k for k, v in flat_profile().items() if not v and k not in OPT]
    add("profile fields complete", not blanks,
        f"blank: {', '.join(blanks)}. Indian portals ask for these constantly."
        if blanks else "", "/profile")
    ev = profile().get("evidence") or []
    fills = [e["id"] for e in ev if "FILL" in (e.get("line") or "")]
    add(f"evidence lines written ({len(ev)-len(fills)}/{len(ev)})", not fills,
        f"still placeholder: {', '.join(fills)}. These are the only claims the "
        f"drafter may make about you." if fills else "", "/profile")
    add("secrets/credentials.json", path("secrets", "credentials.json").exists(),
        "Google OAuth client file. The form filler does not need it.", "/files")
    add("gmail read token", path("secrets", "token.json").exists(),
        "Connect it in one click.", "/gmail")
    add("gmail send token", path("secrets", "token_send.json").exists(),
        "Separate scope, separate consent.", "/gmail")
    from core import llm, render
    add(f"claude cli ({llm.CLI})", llm.available(),
        "Optional. Without it the regex layer still parses most alerts.")
    add(f"latex engine ({render.engine() or 'none'})", bool(render.engine()),
        "Only needed for resume mode tailored. brew install tectonic")

    def _row(label, ok, hint, fix):
        badge = ('<span class="pill p-approved">ok</span>' if ok
                 else '<span class="pill p-needs_review">todo</span>')
        sub = f'<br><span class="small dim">{hint}</span>' if hint else ""
        btn = f'<a class="btn" href="{fix}">fix</a>' if fix and not ok else ""
        return (f'<tr><td>{badge}</td><td><b>{label}</b>{sub}</td>'
                f'<td class="n">{btn}</td></tr>')

    trs = "".join(_row(*c) for c in checks)
    todo = sum(1 for _, ok, _, _ in checks if not ok)

    return page(f"""
<h2>Setup {"— all done" if not todo else f"— {todo} things left"}</h2>
<table>{trs}</table>
<div class="row"><button data-job onclick="run('doctor')" class="primary">
run the full doctor</button></div>
{LOG_PANEL}
<h2>Google credentials, once</h2>
<p class="small dim">Full steps, with the two places people get stuck, are on
the <a href="/gmail">gmail page</a>. Short version:</p>
<pre>1. console.cloud.google.com -> new project
2. APIs and Services -> Library -> enable Gmail API
3. Google Auth Platform -> Get started -> External
4. Google Auth Platform -> Audience -> add yourself as a Test user
5. Google Auth Platform -> Clients -> Create client -> Desktop app
6. Download the JSON and upload it on the files page
7. In Gmail, make a filter that labels job-alert senders  job-alerts</pre>
<p class="small dim">Two separate tokens get stored. The collector holds
gmail.readonly, the mailer holds gmail.send. A bug in one cannot do the other's
job.</p>
""", "setup", with_js=True)


# ------------------------------------------------------------------ rest

@app.get("/applications", response_class=HTMLResponse)
def applications():
    apps = rows(REVIEW_SQL + " WHERE a.status NOT IN ('needs_review')"
                             " ORDER BY a.id DESC LIMIT 200")
    trs = "".join(
        f'<tr><td class="n">{a["id"]}</td>'
        f'<td><b>{a["company_name"]}</b><br>'
        f'<span class="small dim">{a["title"]}</span></td>'
        f'<td><span class="pill p-{a["status"]}">{a["status"]}</span></td>'
        f'<td class="small">{a["to_email"] or "-"}</td>'
        f'<td class="n"><a class="btn" href="/review/{a["id"]}">open</a></td></tr>'
        for a in apps)
    return page('<h2>Applications</h2><table><tr><th>app</th><th>company</th>'
                f'<th>status</th><th>to</th><th></th></tr>'
                f'{trs or "<tr><td colspan=5 class=dim>Nothing yet.</td></tr>"}'
                '</table>', "applications")


@app.get("/stats", response_class=HTMLResponse)
def stats():
    from core import track
    rep = track.report()
    c = db.counts()
    cards = "".join([
        stat("jobs found", c.get("jobs", 0)),
        stat("mails sent", rep["sent"]),
        stat("form applications", rep["form_applications"]),
        stat("replies", rep["replies"]),
        stat("reply rate", f'{rep["reply_rate"]*100:.0f}%'),
        stat("positive", rep.get("positive", 0)),
        stat("rejections", rep.get("rejected", 0)),
        stat("form maps cached", c.get("form_maps", 0)),
    ])
    ev = rows("SELECT entity, entity_id, kind, at FROM events ORDER BY id DESC LIMIT 50")
    evs = "".join(f'<tr><td class="small dim n">{e["at"]}</td>'
                  f'<td class="small">{e["entity"]} {e["entity_id"] or ""}</td>'
                  f'<td class="small">{e["kind"]}</td></tr>' for e in ev)
    return page(f'<div class="stats">{cards}</div>'
                f'<h2>Recent activity</h2><table>{evs}</table>', "stats")


# ------------------------------------------------------------------ files

def _flash(saved: str, error: str) -> str:
    if error:
        return f'<div class="note bad">{error}</div>'
    if saved:
        return f'<div class="note ok">{saved}</div>'
    return ""


@app.get("/files", response_class=HTMLResponse)
def files_page(saved: str = "", error: str = ""):
    cards = []
    for s in files.slot_state():
        state = (f'<span class="pill p-approved">present</span> '
                 f'<span class="small dim">{s["size"]//1024} KB &middot; {s["when"]}</span>'
                 if s["exists"] else '<span class="pill p-needs_review">missing</span>')
        retire_btn = ""
        if s["exists"]:
            retire_btn = (
                f'<form method="post" action="/files/retire" class="inline">'
                f'<input type="hidden" name="path" value="{s["path"]}">'
                f'<button class="no small">remove</button></form>')
        cards.append(f"""
<div class="card">
  <h4>{s["label"]} {state}</h4>
  <p>{s["desc"]}<br><span class="mono small dim">{s["path"]}</span></p>
  <form method="post" action="/files/upload" enctype="multipart/form-data">
    <input type="hidden" name="key" value="{s["key"]}">
    <input type="file" name="f" accept="{s["accept"]}" required
           style="font-size:12px;max-width:100%">
    <div class="row"><button class="primary small">
      {"replace" if s["exists"] else "upload"}</button>{retire_btn}</div>
  </form>
</div>""")

    customs = files.custom_resumes()
    crows = "".join(
        f'<tr><td class="n">{c["job_id"]}</td><td class="small mono">{c["path"]}</td>'
        f'<td class="small dim n">{c["size"]//1024} KB &middot; {c["when"]}</td>'
        f'<td class="n"><form method="post" action="/files/retire" class="inline">'
        f'<input type="hidden" name="path" value="{c["path"]}">'
        f'<button class="no small">remove</button></form></td></tr>'
        for c in customs)

    return page(_flash(saved, error) + f"""
<h2>Files the system needs</h2>
<p class="small dim">Checked by content, not by name. A PDF that is not really a
PDF gets rejected here rather than halfway through a real application. Replaced
files go to data/backups, nothing is deleted.</p>
<div class="grid">{"".join(cards)}</div>

<h2>Hand made resumes</h2>
<p class="small dim">One per job id, and it beats every automatic choice for
that job, permanently. Use this for the postings you tailored by hand.</p>
<form method="post" action="/files/custom" enctype="multipart/form-data">
  <div class="row">
    <input type="text" name="job_id" placeholder="job id, eg 42"
           style="max-width:150px" required>
    <input type="file" name="f" accept=".pdf" required style="font-size:12px">
    <button class="primary">upload for that job</button>
  </div>
</form>
<table><tr><th>job</th><th>file</th><th></th><th></th></tr>
{crows or '<tr><td colspan=4 class="dim">None yet.</td></tr>'}</table>
""", "files")


@app.post("/files/upload")
async def files_upload(key: str = Form(...), f: UploadFile = File(...)):
    from urllib.parse import quote
    try:
        msg = files.save_slot(key, await f.read())
        return RedirectResponse(f"/files?saved={quote(msg)}", status_code=303)
    except files.Rejected as e:
        return RedirectResponse(f"/files?error={quote(str(e))}", status_code=303)


@app.post("/files/custom")
async def files_custom(job_id: str = Form(...), f: UploadFile = File(...)):
    from urllib.parse import quote
    try:
        msg = files.save_custom(job_id, await f.read())
        return RedirectResponse(f"/files?saved={quote(msg)}", status_code=303)
    except files.Rejected as e:
        return RedirectResponse(f"/files?error={quote(str(e))}", status_code=303)


@app.post("/files/retire")
def files_retire(path: str = Form(...)):
    from urllib.parse import quote
    try:
        msg = files.retire(path)
        return RedirectResponse(f"/files?saved={quote(msg)}", status_code=303)
    except files.Rejected as e:
        return RedirectResponse(f"/files?error={quote(str(e))}", status_code=303)


# ------------------------------------------------------------------ gmail

@app.get("/gmail", response_class=HTMLResponse)
def gmail_page(request: Request, saved: str = "", error: str = ""):
    has_creds = gmail_oauth.CREDS.exists()
    cards = []
    for s in gmail_oauth.status():
        if s["connected"]:
            body = ('<span class="pill p-approved">connected</span>'
                    + ('' if s.get("has_refresh") else
                       ' <span class="pill p-needs_review">no refresh token, '
                       'reconnect</span>')
                    + f'<p class="small dim" style="margin-top:8px">{s["why"]}</p>'
                    f'<form method="post" action="/gmail/disconnect" class="inline">'
                    f'<input type="hidden" name="which" value="{s["which"]}">'
                    f'<button class="no small">disconnect</button></form>')
        else:
            btn = (f'<a class="btn primary" href="/gmail/start?which={s["which"]}">'
                   f'connect</a>' if has_creds else
                   '<span class="small dim">upload the client file first</span>')
            body = (f'<span class="pill p-needs_review">not connected</span>'
                    f'<p class="small dim" style="margin-top:8px">{s["why"]}</p>{btn}')
        cards.append(f'<div class="card"><h4>{s["label"]}</h4>{body}</div>')

    creds_note = ""
    if not has_creds:
        creds_note = ('<div class="note">No OAuth client file yet. '
                      '<a href="/files">Upload it on the files page</a>, then come '
                      'back. Both connections need it.</div>')

    label_block = ""
    names, err = gmail_oauth.labels()
    if names:
        want = config()["sources"]["gmail"].get("label", "job-alerts")
        found = any(n.lower() == want.lower() for n in names)
        if found:
            label_block = (f'<div class="note ok">The label '
                           f'<span class="mono">{want}</span> exists. The collector '
                           f'reads only that label, never your inbox.</div>')
        else:
            label_block = (f'<div class="note">No label called '
                           f'<span class="mono">{want}</span> yet. Make it in Gmail '
                           f'along with a filter that applies it to your job alert '
                           f'senders, or change the name in '
                           f'<a href="/settings/config">config</a>.<br>'
                           f'<span class="small dim">Your labels: '
                           f'{", ".join(names[:25])}</span></div>')
    elif err and err != "not connected yet":
        label_block = f'<div class="note bad">Could not list labels: {err}</div>'

    return page(_flash(saved, error) + creds_note + f"""
<h2>Gmail</h2>
<p class="small dim">Two separate connections on purpose. Read and send are
different scopes stored in different files, so a bug in the collector cannot
send anything and a bug in the mailer cannot read your mail.</p>
<div class="grid">{"".join(cards)}</div>
{label_block}
<h2>Getting the client file</h2>
<p class="small dim">The console moved this under <b>Google Auth Platform</b>.
Older guides still say APIs and Services &gt; Credentials, which now redirects.</p>
<pre>1. console.cloud.google.com  ->  create a project (any name)

2. APIs and Services -> Library -> search "Gmail API" -> Enable
   Nothing else works until this is on.

3. Google Auth Platform -> Get started
   App name: anything.  User support email: your own.
   Audience: External.  Contact email: your own.

4. Google Auth Platform -> Audience -> Test users -> Add users
   Add your own gmail address.
   Skipping this is why people get "access blocked" at the consent screen.

5. Google Auth Platform -> Clients -> Create client
   Application type: Desktop app.  Name: anything.  Create.

6. Download JSON on the client you just made,
   then upload it on the files page here.</pre>
<div class="note">
  <b>Step 5 has one wrong answer.</b> Pick <b>Desktop app</b>, not Web
  application. A web client only accepts redirect URIs you registered in
  advance, so it refuses the loopback redirect this dashboard uses, and the
  error Google shows you does not explain that. The upload check on the files
  page catches it if you pick wrong.
</div>
<div class="note">
  <b>Publish it once it works.</b> While the app sits in <span class="mono">Testing</span>,
  Google expires refresh tokens after seven days, so you would be reconnecting
  every week. Google Auth Platform -&gt; Audience -&gt; <b>Publish app</b> fixes
  that. You will see an "unverified app" warning at the consent screen, which is
  expected: verification is for apps with outside users, and you are the only
  user of this one. Click Advanced, then go to the app.
</div>
""", "gmail")


@app.get("/gmail/start")
def gmail_start(request: Request, which: str = "read"):
    from urllib.parse import quote
    redirect_uri = str(request.url_for("gmail_callback"))
    try:
        url = gmail_oauth.start(which, redirect_uri)
    except gmail_oauth.OAuthError as e:
        return RedirectResponse(f"/gmail?error={quote(str(e))}", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/gmail/callback", name="gmail_callback")
def gmail_callback(request: Request, state: str = "", error: str = ""):
    from urllib.parse import quote
    if error:
        return RedirectResponse(f"/gmail?error={quote('Google returned: ' + error)}",
                                status_code=303)
    try:
        msg = gmail_oauth.finish(state, str(request.url))
    except gmail_oauth.OAuthError as e:
        return RedirectResponse(f"/gmail?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/gmail?saved={quote(msg)}", status_code=303)


@app.post("/gmail/disconnect")
def gmail_disconnect(which: str = Form(...)):
    from urllib.parse import quote
    return RedirectResponse(f"/gmail?saved={quote(gmail_oauth.disconnect(which))}",
                            status_code=303)


# ------------------------------------------------------------------ profile

@app.get("/profile", response_class=HTMLResponse)
def profile_page(saved: str = "", error: str = ""):
    d = profileform.parsed()
    blanks = set(profileform.blanks())
    secs = []
    for section, fields in profileform.FIELDS.items():
        inputs = []
        for key, label, ph, help_ in fields:
            v = str((d.get(section) or {}).get(key) or "")
            missing = f"{section}.{key}" in blanks
            flag = ' style="border-color:var(--bad)"' if missing else ""
            hint = f'<span class="small dim">{help_}</span>' if help_ else ""
            inputs.append(
                f'<label class="f">{label}'
                f'{" <span class=pill p-rejected>needed</span>" if missing else ""}'
                f'</label>'
                f'<input type="text" name="{section}.{key}" value="{v.replace(chr(34), "&quot;")}"'
                f' placeholder="{ph}"{flag}>{hint}')
        secs.append(f'<h3>{section}</h3>{"".join(inputs)}')

    evs = []
    for e in profileform.evidence_items():
        warn = (' <span class="pill p-rejected">placeholder</span>'
                if e["placeholder"] else "")
        evs.append(
            f'<label class="f">{e["id"]}{warn} '
            f'<span class="small dim">tags: {e["tags"]}</span></label>'
            f'<textarea name="ev.{e["id"]}" style="min-height:70px">{e["line"]}</textarea>')

    return page(_flash(saved, error) + f"""
<h2>Your details</h2>
<p class="small dim">These are what the form filler types and what the drafter
cites. Saving edits profile.yaml in place, so the comments in that file survive.
The raw file is still on <a href="/settings/profile">settings</a> if you prefer.</p>
<form method="post" action="/profile" id="editform">
  {"".join(secs)}
  <h3>evidence</h3>
  <p class="small dim">The only claims the drafter may make about you. It picks
  the two whose tags best match a posting and builds the mail around them.
  Anything still marked placeholder is skipped rather than sent, so a mail can
  end up with nothing to say.</p>
  {"".join(evs)}
  <div class="row"><button class="primary" type="submit">save</button>
  <span class="small dim">Cmd-S also saves</span></div>
</form>
""", "profile", with_js=True)


@app.post("/profile")
async def profile_save(request: Request):
    from urllib.parse import quote
    form = await request.form()
    text = profileform.read()
    changed = 0
    try:
        for k, v in form.items():
            if k.startswith("ev."):
                cur = {e["id"]: e["line"] for e in profileform.evidence_items()}
                if cur.get(k[3:], None) != " ".join(str(v).split()):
                    text = profileform.set_evidence(text, k[3:], str(v))
                    changed += 1
            elif "." in k:
                section, key = k.split(".", 1)
                if section in profileform.FIELDS:
                    text = profileform.set_scalar(text, section, key, str(v))
                    changed += 1
    except KeyError as e:
        return RedirectResponse(f"/profile?error={quote(str(e))}", status_code=303)

    ok, msg = profileform.save(text)
    q = f"saved={quote(msg)}" if ok else f"error={quote(msg)}"
    return RedirectResponse(f"/profile?{q}", status_code=303)
