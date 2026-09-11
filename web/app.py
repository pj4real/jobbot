"""The dashboard.

Organised around what you think about, not what the database stores: what did
it find, what am I applying to, who owes me a reply, what is dead. Three tabs,
not ten.

It never reimplements an action. Every button shells out to the same find.py,
mail.py or fill.py you would type by hand, so the guards in core/send.py and
fill/filler.py are the only guards that exist.
"""
from __future__ import annotations
import html
import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db, board, triage                      # noqa: E402
from core.config import config                          # noqa: E402
from core import send as sender                         # noqa: E402
from web import runner, editor, files, gmail_oauth, profileform, ui   # noqa: E402

app = FastAPI(title="jobbot")
E = html.escape


class NotReady(Exception):
    """The database has no schema yet. Recoverable, so say so plainly."""


def rows(sql, args=()):
    import sqlite3
    try:
        with db.tx() as c:
            return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            raise NotReady(str(e)) from e
        raise


def page(body: str, tab: str = "", scripts: str = "") -> HTMLResponse:
    return HTMLResponse(ui.shell(body, tab, scripts))


@app.exception_handler(NotReady)
def not_ready(request, exc):
    return page(ui.note(
        "<b>The database is not set up yet.</b><br>"
        "Run <span class='mono'>python run.py init</span> in the project folder. "
        "It is safe to rerun and only fills in what is missing.", "bad"))


def flash(saved: str = "", error: str = "") -> str:
    if error:
        return ui.note(E(error), "bad")
    if saved:
        return ui.note(E(saved), "good")
    return ""


# ================================================================== board

ACTION_CHIP = {"triage": "act", "review": "act", "followup": "act",
               "reply": "good", "send": "go", "fill": "go", "draft": "go"}


def job_row(r: dict) -> str:
    a = r["action"]
    fit = r.get("fit_score") or 0
    co = E(r.get("company") or "Unknown company")
    title = E(r.get("title") or "")
    meta = []
    if r.get("location"):
        meta.append(E(r["location"]))
    if r.get("apply_kind") and r["apply_kind"] != "unknown":
        meta.append(E(r["apply_kind"]))
    if r.get("deadline"):
        meta.append("closes " + E(r["deadline"]))
    if r.get("to_email"):
        meta.append(E(r["to_email"]))
    if r.get("waiting_days") is not None and r["stage"] in ("applied", "waiting"):
        d = r["waiting_days"]
        meta.append(f"sent {'today' if d == 0 else str(d) + 'd ago'}")
    for s in (r.get("skills") or [])[:4]:
        meta.append(E(s))

    buttons = []
    jid = r["id"]
    if a["kind"] == "triage":
        buttons.append(
            f'<form method="post" action="/job/{jid}/keep" style="display:inline">'
            f'<button class="go sm">Keep</button></form>')
        buttons.append(
            f'<form method="post" action="/job/{jid}/skip" style="display:inline">'
            f'<button class="btn-quiet sm">Skip</button></form>')
    elif a["kind"] == "fill":
        buttons.append(f'<button class="go sm" data-fill="{jid}">Fill form</button>')
    elif a["kind"] == "review":
        buttons.append(f'<a class="btn btn-go sm" href="/draft/{r["app_id"]}">Read draft</a>')
    elif a["kind"] == "draft":
        buttons.append(f'<button class="go sm" data-job="draft">Write mail</button>')
    elif a["kind"] == "followup":
        buttons.append(f'<button class="btn-warn sm" data-job="followup">Follow up</button>')
    elif a["kind"] == "reply":
        buttons.append(f'<a class="btn sm" href="/job/{jid}">Open</a>')
    elif a["kind"] == "open" and r.get("apply_url"):
        buttons.append(f'<a class="btn btn-go sm" href="{E(r["apply_url"])}" '
                       f'target="_blank" rel="noopener">Open posting</a>')
    if r.get("apply_url") and a["kind"] not in ("triage",):
        buttons.append(f'<a class="btn sm" href="{E(r["apply_url"])}" target="_blank" '
                       f'rel="noopener">Site</a>')

    chip = ACTION_CHIP.get(a["kind"], "")
    hint = ""
    if a["kind"] == "triage":
        # the scorer's reasoning belongs in the meta line, not shouted in a chip
        why = (r.get("fit_reason") or "").split(";")[0].strip()
        hint = f'<span class="chip">{E(why)[:26]}</span>' if why else ""
    elif a.get("hint"):
        hint = f'<span class="chip {chip}">{E(a["hint"])[:32]}</span>' 

    return f"""<div class="row" data-job="{jid}">
  <div class="fit"><div class="meter"><i style="width:{int(fit*100)}%"></i></div>
    <b>{fit:.2f}</b></div>
  <div class="who">
    <div class="co"><a href="/job/{jid}">{co}</a></div>
    <div class="role">{title}</div>
    <div class="meta">{"".join(f"<span>{m}</span>" for m in meta[:6])}</div>
  </div>
  <div class="act">{hint}{"".join(buttons)}</div>
</div>"""


BOARD_JS = """
<script>
document.addEventListener('click', e => {
  const b = e.target.closest('[data-fill]');
  if (!b) return;
  b.disabled = true; b.textContent = 'opening...';
  fetch('/api/fill_job/' + b.dataset.fill, {method:'POST'})
    .then(r => r.json())
    .then(d => { location.href = '/setup?run=' + d.run_id + '#run'; });
});
let sel = -1;
const rowsOf = () => [...document.querySelectorAll('.row[data-job]')];
function focusRow(i){
  const rs = rowsOf(); if(!rs.length) return;
  sel = Math.max(0, Math.min(rs.length-1, i));
  rs.forEach(r => r.style.outline='');
  const r = rs[sel];
  r.style.outline = '2px solid var(--accent)';
  r.scrollIntoView({block:'nearest'});
}
document.addEventListener('keydown', e => {
  if (/input|textarea|select/i.test(document.activeElement.tagName)) return;
  if (e.key === 'j') { focusRow(sel+1); e.preventDefault(); }
  if (e.key === 'k') { focusRow(sel-1); e.preventDefault(); }
  const rs = rowsOf();
  if (sel < 0 || !rs[sel]) return;
  const id = rs[sel].dataset.job;
  if (e.key === 'y') { post('/job/'+id+'/keep'); }
  if (e.key === 'n') { post('/job/'+id+'/skip'); }
  if (e.key === 'Enter') { location.href = '/job/'+id; }
});
function post(url){
  const f = document.createElement('form');
  f.method='post'; f.action=url; document.body.appendChild(f); f.submit();
}
</script>"""


@app.get("/", response_class=HTMLResponse)
def home(stage: str = "", saved: str = "", error: str = ""):
    b = board.board()
    counts = {s: len(b[s]) for s in board.STAGES}
    head = board.headline()
    stage = stage or head["where"]
    if stage not in board.STAGES:
        stage = "found"

    rail = "".join(
        f'<a href="/?stage={s}" class="{"on" if s == stage else ""}'
        f'{" hot" if s in ("found", "replied", "waiting") and counts[s] and s != stage else ""}">'
        f'{board.STAGE_LABEL[s]} <b>{counts[s]}</b></a>'
        for s in board.STAGES)

    items = b[stage]
    if items:
        listing = '<div class="rows">' + "".join(job_row(r) for r in items) + '</div>'
    else:
        listing = EMPTY_FOR.get(stage, ui.empty("Nothing here", ""))

    sub = ""
    if head["tone"] != "idle":
        sub = '<span class="sub">press <kbd>j</kbd> <kbd>k</kbd> to move, ' \
              '<kbd>y</kbd> keep, <kbd>n</kbd> skip</span>'

    return page(f"""
{flash(saved, error)}
<div class="headline tone-{head['tone']}">
  <h1>{E(head['text'])}</h1>{sub}
  <div class="grow"></div>
  <button class="go" data-job="find" onclick="location.href='/setup?start=find#run'">
    Scan my mail</button>
</div>
<div class="rail">{rail}</div>
<p class="rail-note">{E(board.STAGE_BLURB[stage])}</p>
{listing}
""", "Board", BOARD_JS)


EMPTY_FOR = {
    "found": ui.empty("No new openings", "Scan your mail and anything that looks "
                      "like a posting lands here.",
                      '<a class="btn btn-go" href="/setup?start=find#run">Scan my mail</a>'),
    "shortlisted": ui.empty("Nothing to apply to yet",
                            "Keep something from Found and it moves here."),
    "applied": ui.empty("No applications yet",
                        "Fill a form or send a mail and it shows up here."),
    "waiting": ui.empty("Nobody is overdue",
                        "Applications appear here once they have been silent for a week."),
    "replied": ui.empty("No replies yet",
                        "Run Check replies after you have sent a few."),
    "closed": ui.empty("Nothing closed", "Skipped and rejected jobs collect here."),
}


# ================================================================== one job

@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(job_id: int):
    r = rows(board.BOARD_SQL + " WHERE j.id = ?", (job_id,))
    if not r:
        return page(ui.note("No such job.", "bad"), "Board")
    j = r[0]
    j["stage"] = board.stage_of(j)
    j["action"] = board.next_action(j, j["stage"])
    j["skills"] = json.loads(j.get("skills_json") or "[]")

    tl = "".join(
        f'<li class="{"now" if i == 0 else ""}"><b>{E(t["kind"].replace("_", " ").title())}</b> '
        f'<span class="dim">{E(t.get("detail") or "")}</span><br>'
        f'<span class="when">{E((t.get("at") or "")[:16])}</span></li>'
        for i, t in enumerate(reversed(board.timeline(job_id))))

    raw = rows("SELECT ri.subject, ri.sender, ri.url, ri.received_at"
               "  FROM jobs j JOIN raw_items ri ON ri.id = j.raw_item_id"
               " WHERE j.id = ?", (job_id,))
    src = ""
    if raw:
        s = raw[0]
        addr = triage.address_of(s.get("sender") or "")
        src = f"""<h3>Where it came from</h3>
<div class="card">
  <div class="sm"><b>{E(s.get('subject') or '')}</b></div>
  <div class="sm dim mono">{E(s.get('sender') or '')}</div>
  <div class="row-f">
    <a class="btn sm" href="{E(s.get('url') or '#')}" target="_blank" rel="noopener">
      Open in Gmail</a>
    <form method="post" action="/sender/mute" style="display:inline">
      <input type="hidden" name="addr" value="{E(addr)}">
      <input type="hidden" name="back" value="/job/{job_id}">
      <button class="btn-quiet sm">Never show this sender again</button>
    </form>
  </div>
</div>"""

    acts = []
    if j.get("apply_url"):
        from core.urls import fillable
        can_fill, why = fillable(j["apply_url"])
        if can_fill:
            acts.append(f'<button class="go" data-fill="{job_id}">Fill the form</button>')
        acts.append(f'<a class="btn{"" if can_fill else " btn-go"}" '
                    f'href="{E(j["apply_url"])}" target="_blank" rel="noopener">'
                    f'Open the posting</a>')
        if not can_fill:
            src += ui.note(E(why), "act")
    if j.get("app_id"):
        acts.append(f'<a class="btn" href="/draft/{j["app_id"]}">Open the draft</a>')
    if j["stage"] == "found":
        acts.insert(0, f'<form method="post" action="/job/{job_id}/keep" '
                       f'style="display:inline"><button class="go">Keep</button></form>')
        acts.append(f'<form method="post" action="/job/{job_id}/skip" '
                    f'style="display:inline"><button class="btn-quiet">Skip</button></form>')

    desc = E((j.get("description") or "").strip())[:4000]

    return page(f"""
<div class="headline">
  <h1>{E(j.get('company') or '?')}</h1>
  <span class="chip {'act' if j['stage'] in ('found','waiting') else 'go'}">
    {E(board.STAGE_LABEL[j['stage']])}</span>
</div>
<p class="dim">{E(j.get('title') or '')}{' · ' + E(j['location']) if j.get('location') else ''}
{' · closes ' + E(j['deadline']) if j.get('deadline') else ''}</p>
<div class="row-f">{"".join(acts)}<a class="btn btn-quiet" href="/">Back</a></div>

<div class="stats">
  {ui.stat("fit", f"{j.get('fit_score') or 0:.2f}")}
  {ui.stat("route", E(j.get('apply_kind') or '-'))}
  {ui.stat("waiting", (str(board._days_since(j.get('submitted_at'))) + "d") if j.get('submitted_at') else "-")}
</div>
<p class="sm dim" style="margin-top:10px">Scored because: {E(j.get('fit_reason') or 'no signals')}</p>

<h3>What happened</h3>
<ul class="tl">{tl or '<li>Found in your mail.</li>'}</ul>
{src}
<h3>The posting</h3>
<div class="card sm" style="white-space:pre-wrap;max-height:420px;overflow:auto">{desc or 'No text captured.'}</div>
""", "Board", BOARD_JS)


@app.post("/job/{job_id}/keep")
def job_keep(job_id: int):
    board.set_triage(job_id, "shortlisted")
    return RedirectResponse("/?stage=found", status_code=303)


@app.post("/job/{job_id}/skip")
def job_skip(job_id: int):
    board.set_triage(job_id, "skipped")
    return RedirectResponse("/?stage=found", status_code=303)


@app.post("/sender/mute")
def sender_mute(addr: str = Form(...), back: str = Form("/")):
    from urllib.parse import quote
    triage.mute(addr)
    return RedirectResponse(f"{back}?saved={quote(f'Muted {addr}. Nothing from that address will be shown again.')}",
                            status_code=303)


@app.post("/sender/unmute")
def sender_unmute(addr: str = Form(...)):
    from urllib.parse import quote
    triage.unmute(addr)
    return RedirectResponse(f"/setup?saved={quote(f'{addr} will be judged on merit again.')}#senders",
                            status_code=303)


# ================================================================== drafts

DRAFT_SQL = """
SELECT a.id, a.status, a.job_id, a.channel, d.subject, d.body, d.id draft_id,
       d.evidence_ids, j.title, j.fit_score, co.name company_name,
       c.email to_email, c.name to_name, c.verified, r.path resume_path
  FROM applications a
  JOIN drafts d ON d.id = a.draft_id
  JOIN jobs j ON j.id = a.job_id
  LEFT JOIN companies co ON co.id = j.company_id
  LEFT JOIN contacts c ON c.id = d.contact_id
  LEFT JOIN resumes r ON r.id = a.resume_id
"""


@app.get("/draft/{app_id}", response_class=HTMLResponse)
def draft_page(app_id: int):
    r = rows(DRAFT_SQL + " WHERE a.id = ?", (app_id,))
    if not r:
        return page(ui.note("No such draft.", "bad"), "Board")
    a = r[0]
    warn = ""
    if not a["to_email"]:
        warn = ui.note("No recipient address, so this one will be blocked at send.", "bad")
    elif not a["verified"]:
        warn = ui.note("This address was never verified. The mailer refuses "
                       "unverified addresses, because a bounce costs sender "
                       "reputation and you cannot buy that back.", "bad")
    if "FILL" in (a["body"] or ""):
        warn += ui.note("This draft still has a FILL placeholder in it. Fix the "
                        "evidence lines on <a href='/setup#profile'>Setup</a>.", "bad")

    nxt = rows("SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id"
               " WHERE a.status='needs_review' AND a.id != ?"
               " ORDER BY j.fit_score DESC LIMIT 1", (app_id,))

    return page(f"""
<div class="headline"><h1>{E(a['company_name'] or '?')}</h1>
  <span class="chip {'act' if a['status'] == 'needs_review' else 'go'}">{E(a['status'])}</span></div>
<p class="dim sm">{E(a['title'] or '')} · to {E(a['to_email'] or 'nobody')} ·
resume {E(Path(a['resume_path']).name if a['resume_path'] else 'none')}</p>
{warn}
<form method="post" action="/draft/{app_id}/save" id="editform">
  <label class="f" for="subject">Subject</label>
  <input type="text" id="subject" name="subject" value="{E(a['subject'] or '')}">
  <label class="f" for="body">Message</label>
  <textarea id="body" name="body">{E(a['body'] or '')}</textarea>
  <div class="row-f">
    <button class="go" type="submit" name="action" value="approve">Approve</button>
    <button type="submit">Save</button>
    <button class="btn-bad" type="submit" name="action" value="reject">Reject</button>
    {f'<a class="btn" href="/draft/{nxt[0]["id"]}">Next waiting</a>' if nxt else ''}
    <a class="btn btn-quiet" href="/">Back to board</a>
  </div>
</form>
<p class="sm dim">Approving only marks it ready. Sending is a separate step on
<a href="/setup#run">Setup</a>, and it dry runs first.</p>
""", "Board", """<script>
document.addEventListener('keydown', e => {
  if ((e.metaKey||e.ctrlKey) && e.key === 's') {
    e.preventDefault(); document.getElementById('editform').submit(); }
});</script>""")


@app.post("/draft/{app_id}/save")
def draft_save(app_id: int, subject: str = Form(""), body: str = Form(""),
               action: str = Form("")):
    with db.tx() as c:
        row = c.execute("SELECT draft_id, job_id FROM applications WHERE id=?",
                        (app_id,)).fetchone()
        if row:
            c.execute("UPDATE drafts SET subject=?, body=?, edited=1 WHERE id=?",
                      (subject.strip(), body.strip(), row["draft_id"]))
        if action in ("approve", "reject"):
            c.execute("UPDATE applications SET status=? WHERE id=?",
                      ("approved" if action == "approve" else "rejected", app_id))
    if action and row:
        db.log("application", app_id, action)
        board.record(row["job_id"], action, "by you", app_id)
    return RedirectResponse("/" if action else f"/draft/{app_id}", status_code=303)


# ================================================================== activity

@app.get("/activity", response_class=HTMLResponse)
def activity():
    from core import track
    rep = track.report()
    c = db.counts()
    bc = board.counts()
    sst = triage.sender_stats()

    cards = "".join([
        ui.stat("found", c.get("jobs", 0)),
        ui.stat("applied", bc["applied"] + bc["waiting"] + bc["replied"], "go"),
        ui.stat("replies", rep["replies"], "good" if rep["replies"] else ""),
        ui.stat("reply rate", f"{rep['reply_rate']*100:.0f}%"),
        ui.stat("waiting", bc["waiting"], "act" if bc["waiting"] else ""),
        ui.stat("sent today", c.get("sent_today", 0)),
        ui.stat("muted senders", sst["not_job"]),
        ui.stat("form maps", c.get("form_maps", 0)),
    ])

    hist = rows(
        "SELECT t.kind, t.detail, t.at, j.title, co.name company, j.id job_id"
        "  FROM timeline t LEFT JOIN jobs j ON j.id=t.job_id"
        "  LEFT JOIN companies co ON co.id=j.company_id"
        " ORDER BY t.id DESC LIMIT 80")
    def _hist_row(h):
        co = E(h.get("company") or "?")
        link = f'<a href="/job/{h["job_id"]}">{co}</a>' if h.get("job_id") else co
        return (f'<tr><td class="dim mono sm">{E((h.get("at") or "")[:16])}</td>'
                f'<td>{E(h["kind"].replace("_", " ").title())} '
                f'<span class="dim sm">{E(h.get("detail") or "")}</span></td>'
                f'<td>{link}<div class="dim sm">{E(h.get("title") or "")}</div>'
                f'</td></tr>')

    if hist:
        body = ('<div class="scroll"><table><tr><th>when</th><th>what</th>'
                '<th>company</th></tr>'
                + "".join(_hist_row(h) for h in hist) + "</table></div>")
    else:
        body = ui.empty("Nothing has happened yet",
                        "Scan your mail and this fills in as you go.")

    return page(f'<div class="headline"><h1>Activity</h1></div>'
                f'<div class="stats">{cards}</div>'
                f'<h2>Everything that happened</h2>{body}', "Activity")


# ================================================================== setup

def _setup_checks():
    from core.config import path, profile, flat_profile
    from core import llm
    out = []
    p = path("assets", "resume_default.pdf")
    out.append(("Resume", p.exists(),
                "The file that gets attached and uploaded.", "#files"))
    OPT = {"gender", "portfolio"}
    blanks = [k for k, v in flat_profile().items() if not v and k not in OPT]
    out.append(("Your details", not blanks,
                f"Missing: {', '.join(blanks)}" if blanks else "All filled in.",
                "#profile"))
    ev = profile().get("evidence") or []
    fills = [e["id"] for e in ev if "FILL" in (e.get("line") or "")]
    out.append((f"Evidence lines ({len(ev)-len(fills)} of {len(ev)})", not fills,
                f"Still placeholders: {', '.join(fills)}" if fills
                else "These are the only claims the drafter may make about you.",
                "#profile"))
    out.append(("Google client file", path("secrets", "credentials.json").exists(),
                "Needed to read your mail. Filling forms does not need it.", "#gmail"))
    out.append(("Gmail read access", path("secrets", "token.json").exists(),
                "Lets it scan your mail for openings.", "#gmail"))
    out.append(("Gmail send access", path("secrets", "token_send.json").exists(),
                "Only needed if you want it to send outreach.", "#gmail"))
    return out


@app.get("/setup", response_class=HTMLResponse)
def setup(saved: str = "", error: str = "", run: str = "", start: str = ""):
    checks = _setup_checks()
    todo = sum(1 for _, ok, _, _ in checks if not ok)
    rowsh = "".join(
        f'<tr><td>{"<span class=chip go>done</span>" if ok else "<span class=chip act>to do</span>"}</td>'
        f'<td><b>{E(label)}</b><div class="dim sm">{hint}</div></td>'
        f'<td style="text-align:right"><a class="btn sm" href="{href}">'
        f'{"change" if ok else "fix"}</a></td></tr>'
        for label, ok, hint, href in checks)

    slots = "".join(
        f'''<div class="card">
  <h4>{E(s["label"])} {"<span class=chip go>have it</span>" if s["exists"]
      else "<span class=chip act>missing</span>"}</h4>
  <p>{E(s["desc"])}</p>
  <form method="post" action="/files/upload" enctype="multipart/form-data">
    <input type="hidden" name="key" value="{s["key"]}">
    <input type="file" name="f" accept="{s["accept"]}" required
           style="font-size:12px;max-width:100%" id="up_{s["key"]}">
    <div class="row-f"><button class="go sm">
      {"Replace" if s["exists"] else "Upload"}</button></div>
  </form>
</div>''' for s in files.slot_state())

    gm = "".join(
        f'''<div class="card"><h4>{E(g["label"])}
  {"<span class=chip go>connected</span>" if g["connected"]
   else "<span class=chip act>not connected</span>"}</h4>
  <p>{E(g["why"])}</p>
  {f'<form method="post" action="/gmail/disconnect" style="display:inline">'
     f'<input type=hidden name=which value="{g["which"]}">'
     f'<button class="btn-quiet sm">Disconnect</button></form>'
   if g["connected"] else
   (f'<a class="btn btn-go sm" href="/gmail/start?which={g["which"]}">Connect</a>'
    if gmail_oauth.CREDS.exists()
    else '<span class="dim sm">Upload the client file first</span>')}
</div>''' for g in gmail_oauth.status())

    d = profileform.parsed()
    blankset = set(profileform.blanks())
    fields = []
    for section, defs in profileform.FIELDS.items():
        inner = "".join(
            f'<label class="f" for="f_{section}_{k}">{E(lab)}'
            f'{" <span class=chip act>needed</span>" if f"{section}.{k}" in blankset else ""}'
            f'</label><input type="text" id="f_{section}_{k}" name="{section}.{k}" '
            f'value="{E(str((d.get(section) or {}).get(k) or ""))}" placeholder="{E(ph)}">'
            for k, lab, ph, _ in defs)
        fields.append(f'<h3>{E(section)}</h3>{inner}')
    evs = "".join(
        f'<label class="f" for="ev_{e["id"]}">{E(e["id"])}'
        f'{" <span class=chip act>placeholder</span>" if e["placeholder"] else ""}'
        f'</label><textarea id="ev_{e["id"]}" name="ev.{e["id"]}" '
        f'style="min-height:66px">{E(e["line"])}</textarea>'
        for e in profileform.evidence_items())

    muted = triage.muted_senders(60)
    mrows = "".join(
        f'<tr><td class="mono sm">{E(m["address"])}</td>'
        f'<td class="dim sm">{E(m.get("reason") or "")}</td>'
        f'<td class="dim sm mono">{m["seen"]}x</td>'
        f'<td style="text-align:right"><form method="post" action="/sender/unmute" '
        f'style="display:inline"><input type=hidden name=addr value="{E(m["address"])}">'
        f'<button class="btn-quiet sm">Unmute</button></form></td></tr>'
        for m in muted)

    cfg = config()
    edit_links = "".join(
        f'<a class="btn sm" href="/settings/{k}">{E(v[0])}</a>'
        for k, v in editor.EDITABLE.items())

    def rc(job, title, desc, cls=""):
        return (f'<div class="card"><h4>{title}</h4><p>{desc}</p>'
                f'<button class="{cls} sm" data-job="{job}">Run</button></div>')

    return page(f"""
{flash(saved, error)}
<div class="headline"><h1>Setup{"" if not todo else f" &middot; {todo} left"}</h1></div>
<div class="scroll"><table>{rowsh}</table></div>

<h2 id="run">Run something</h2>
<div class="row-f">
  <span id="runstatus" class="dim sm">idle</span><div class="grow"></div>
  <button class="btn-quiet sm" onclick="stopRun()">Stop</button>
</div>
<div id="log" class="dim">Output appears here.</div>
<div id="result"></div>
<div class="cards">
  {rc("find", "Scan my mail", "Search your whole mailbox for openings, score them, add them to the board.", "go")}
  {rc("resolve", "Resolve LinkedIn postings", "Opens the LinkedIn jobs you were emailed and follows Apply to the company's own form. Those become fillable.", "go")}
  {rc("draft", "Write outreach", "Draft a mail for every kept job that has a verified address.")}
  {rc("followup", "Queue follow ups", "One per application, a week after sending, only if nobody replied.")}
  {rc("send_dry", "Preview sending", "Shows exactly what would go out. Sends nothing.")}
  {rc("send_live", "Send for real", "Only approved drafts, only within today's cap.", "btn-bad")}
  {rc("track", "Check for replies", "Reads the threads you sent on and records what came back.")}
  {rc("doctor", "Check the setup", "What is missing and what each gap costs you.")}
  {rc("tests", "Run the tests", "111 checks, mostly that the guards still refuse things.")}
</div>
{ui.note("Sending for real is refused while <span class='mono'>dry_run</span> is on in "
         "<a href='/settings/config'>config</a>. That is the second switch, on purpose.", "act")
 if cfg["safety"].get("dry_run", True) else ""}

<h2 id="files">Files</h2>
<div class="cards">{slots}</div>

<h2 id="gmail">Gmail</h2>
<p class="sm dim">Two separate connections. Read and send are different scopes in
different files, so a bug in the scanner cannot send and a bug in the mailer
cannot read.</p>
<div class="cards">{gm}</div>
<details><summary class="sm dim" style="cursor:pointer;margin:10px 0">
  How to get the client file</summary>
<div class="card sm mono" style="white-space:pre-wrap;margin-top:8px">1. console.cloud.google.com -> new project
2. APIs and Services -> Library -> enable Gmail API
3. Google Auth Platform -> Get started -> External
4. Google Auth Platform -> Audience -> add yourself as a Test user
5. Google Auth Platform -> Clients -> Create client -> Desktop app
6. Download the JSON and upload it above

Pick Desktop app, not Web application.
Once it works, Audience -> Publish app, or tokens expire every 7 days.</div>
</details>

<h2 id="profile">Your details</h2>
<p class="sm dim">What the form filler types and what the drafter is allowed to
cite. Saving edits profile.yaml in place, so its comments survive.</p>
<form method="post" action="/profile" id="editform">
  {"".join(fields)}
  <h3>evidence</h3>
  <p class="sm dim">The drafter picks the two whose tags best match a posting.
  Anything still marked placeholder is skipped rather than sent.</p>
  {evs}
  <div class="row-f"><button class="go" type="submit">Save details</button>
  <span class="dim sm">Cmd-S also saves</span></div>
</form>

<h2 id="senders">Muted senders</h2>
<p class="sm dim">Senders you told it to ignore. Nothing from these reaches the
board, and judging one costs nothing ever again. Your decisions here outrank
every rule the scanner has.</p>
{f'<div class="scroll"><table><tr><th>address</th><th>why</th><th>seen</th><th></th></tr>{mrows}</table></div>'
 if muted else ui.empty("Nothing muted yet",
   "Open a job, and Never show this sender again mutes it from there.")}

<h2>Config files</h2>
<p class="sm dim">Validated before anything is written, and the previous version
is always kept in data/backups.</p>
<div class="row-f">{edit_links}</div>
""", "Setup", RUN_JS + (f"<script>autoStart('{E(start)}','{E(run)}')</script>"
                        if (start or run) else ""))


RUN_JS = """
<script>
let poll=null, curRun=null;
const el=i=>document.getElementById(i);
function paint(lines){
  const L=el('log'); if(!L) return;
  L.innerHTML = lines.map(t=>{
    const e=t.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    if(/\\[error\\]|FAIL|Traceback|error:|\\bXX\\b|!/.test(t)) return '<span class="e">'+e+'</span>';
    if(/\\bok\\b|passed|SENT|\\[ok\\]|saved/.test(t)) return '<span class="g">'+e+'</span>';
    if(/blocked|needs you|DRY RUN|to do/.test(t)) return '<span class="w">'+e+'</span>';
    return e;
  }).join('\\n');
  L.scrollTop=L.scrollHeight;
}
function status(t,c){const s=el('runstatus');
  if(s) s.innerHTML='<span class="dot '+c+'"></span>'+t;}
async function tick(){
  if(!curRun) return;
  const r=await fetch('/api/run/'+curRun); if(!r.ok) return;
  const d=await r.json(); paint(d.lines);
  if(d.awaiting_release){ status(d.label+' — browser open, waiting for you','run');
    let b=el('doneBtn'); if(!b){ b=document.createElement('button');
      b.id='doneBtn'; b.className='go sm'; b.textContent='Done, close the browser';
      b.onclick=()=>{fetch('/api/run/'+curRun+'/release',{method:'POST'});b.remove();};
      el('runstatus').after(b);} }
  else if(d.done){ clearInterval(poll); poll=null;
    status(d.label+(d.code?' — failed (exit '+d.code+')':' — done'), d.code?'err':'ok');
    document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=false);
    el('doneBtn')?.remove();
    showResult(d); }
  else status(d.label+' — running','run');
}
function showResult(d){
  const box = el('result'); if(!box) return;
  if (d.code) {
    box.className = 'note bad';
    box.innerHTML = '<b>That did not finish.</b> The last lines above say why.';
    return;
  }
  const nothing = /^nothing\b/i.test(d.summary || '');
  box.className = 'note ' + (nothing ? '' : 'good');
  box.innerHTML = '<b>' + (d.summary || 'Finished.') + '</b>'
    + (d.landing && !nothing
       ? ' <a class="btn btn-go sm" style="margin-left:10px" href="' + d.landing
         + '">' + d.landing_label + '</a>'
       : '');
}
function watch(id){ curRun=id; if(poll) clearInterval(poll);
  poll=setInterval(tick,700); tick(); }
async function run(job){
  document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=true);
  el('result')?.replaceChildren();
  paint(['starting '+job+' ...']);
  // bring the output to the eye. The cards are below the log, so without this
  // the whole run happens off screen.
  document.getElementById('run')?.scrollIntoView({behavior:'smooth',block:'start'});
  const r=await fetch('/api/run/'+job,{method:'POST'});
  if(!r.ok){ paint(['could not start: '+await r.text()]);
    document.querySelectorAll('button[data-job]').forEach(b=>b.disabled=false); return; }
  watch((await r.json()).run_id);
}
function stopRun(){ if(curRun) fetch('/api/run/'+curRun+'/stop',{method:'POST'}); }
function autoStart(job,runId){
  if(runId){ watch(runId); document.getElementById('run')?.scrollIntoView(); return; }
  if(job){ run(job); document.getElementById('run')?.scrollIntoView(); }
}
document.addEventListener('click',e=>{
  const b=e.target.closest('button[data-job]'); if(b) run(b.dataset.job);
});
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='s'){const f=el('editform');
    if(f){e.preventDefault();f.submit();}}
});
</script>"""


# ================================================================== actions

@app.post("/profile")
async def profile_save(request: Request):
    from urllib.parse import quote
    form = await request.form()
    text = profileform.read()
    try:
        cur = {e["id"]: e["line"] for e in profileform.evidence_items()}
        for k, v in form.items():
            if k.startswith("ev."):
                if cur.get(k[3:]) != " ".join(str(v).split()):
                    text = profileform.set_evidence(text, k[3:], str(v))
            elif "." in k:
                section, key = k.split(".", 1)
                if section in profileform.FIELDS:
                    text = profileform.set_scalar(text, section, key, str(v))
    except KeyError as e:
        return RedirectResponse(f"/setup?error={quote(str(e))}#profile", status_code=303)
    ok, msg = profileform.save(text)
    q = f"saved={quote(msg)}" if ok else f"error={quote(msg)}"
    return RedirectResponse(f"/setup?{q}#profile", status_code=303)


@app.post("/files/upload")
async def files_upload(key: str = Form(...), f: UploadFile = File(...)):
    from urllib.parse import quote
    try:
        msg = files.save_slot(key, await f.read())
        return RedirectResponse(f"/setup?saved={quote(msg)}#files", status_code=303)
    except files.Rejected as e:
        return RedirectResponse(f"/setup?error={quote(str(e))}#files", status_code=303)


@app.get("/gmail/start")
def gmail_start(request: Request, which: str = "read"):
    from urllib.parse import quote
    try:
        url = gmail_oauth.start(which, str(request.url_for("gmail_callback")))
    except gmail_oauth.OAuthError as e:
        return RedirectResponse(f"/setup?error={quote(str(e))}#gmail", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/gmail/callback", name="gmail_callback")
def gmail_callback(request: Request, state: str = "", error: str = ""):
    from urllib.parse import quote
    if error:
        return RedirectResponse(f"/setup?error={quote('Google returned: ' + error)}#gmail",
                                status_code=303)
    try:
        msg = gmail_oauth.finish(state, str(request.url))
    except gmail_oauth.OAuthError as e:
        return RedirectResponse(f"/setup?error={quote(str(e))}#gmail", status_code=303)
    return RedirectResponse(f"/setup?saved={quote(msg)}#gmail", status_code=303)


@app.post("/gmail/disconnect")
def gmail_disconnect(which: str = Form(...)):
    from urllib.parse import quote
    return RedirectResponse(
        f"/setup?saved={quote(gmail_oauth.disconnect(which))}#gmail", status_code=303)


@app.get("/settings/{key}", response_class=HTMLResponse)
def settings_edit(key: str, saved: str = "", error: str = ""):
    if key not in editor.EDITABLE:
        return page(ui.note("Not editable.", "bad"), "Setup")
    name, kind, desc = editor.EDITABLE[key]
    bks = "".join(
        f'<form method="post" action="/settings/{key}/restore" style="display:inline">'
        f'<input type="hidden" name="stamp" value="{b["stamp"]}">'
        f'<button class="sm">{b["stamp"]}</button></form> '
        for b in editor.backups(key))
    return page(f"""
{flash(saved, error)}
<div class="headline"><h1>{E(name)}</h1></div>
<p class="sm dim">{E(desc)} Checked as {E(kind)} before anything is written.</p>
<form method="post" action="/settings/{key}" id="editform">
  <textarea class="tall" name="text" id="text" spellcheck="false">{E(editor.read(key))}</textarea>
  <div class="row-f"><button class="go" type="submit">Save</button>
    <a class="btn btn-quiet" href="/setup">Back</a>
    <div class="grow"></div><span class="dim sm">Cmd-S saves</span></div>
</form>
{f'<h3>restore an earlier version</h3><div class="row-f">{bks}</div>' if bks else ''}
""", "Setup", RUN_JS)


@app.post("/settings/{key}")
def settings_save(key: str, text: str = Form("")):
    from urllib.parse import quote
    if key not in editor.EDITABLE:
        return RedirectResponse("/setup", status_code=303)
    ok, msg = editor.save(key, text)
    q = f"saved={quote(msg)}" if ok else f"error={quote(msg)}"
    return RedirectResponse(f"/settings/{key}?{q}", status_code=303)


@app.post("/settings/{key}/restore")
def settings_restore(key: str, stamp: str = Form("")):
    from urllib.parse import quote
    ok, msg = editor.restore(key, stamp)
    q = f"saved={quote(msg)}" if ok else f"error={quote(msg)}"
    return RedirectResponse(f"/settings/{key}?{q}", status_code=303)


# ================================================================== run api

@app.post("/api/run/{job}")
def api_run(job: str):
    try:
        return {"run_id": runner.start(job)}
    except KeyError:
        return JSONResponse({"error": f"unknown job {job}"}, status_code=404)


@app.get("/api/run/{run_id}")
def api_run_status(run_id: str):
    r = runner.get(run_id)
    return r or JSONResponse({"error": "no such run"}, status_code=404)


@app.post("/api/run/{run_id}/release")
def api_release(run_id: str):
    return {"released": runner.release(run_id)}


@app.post("/api/run/{run_id}/stop")
def api_stop(run_id: str):
    return {"stopped": runner.stop(run_id)}


@app.post("/api/fill_job/{job_id}")
def api_fill_job(job_id: int):
    r = rows("SELECT apply_url FROM jobs WHERE id=?", (job_id,))
    if not r or not r[0]["apply_url"]:
        return JSONResponse({"error": "no apply url"}, status_code=404)
    run_id, _ = runner.start_fill(r[0]["apply_url"])
    db.log("job", job_id, "fill_launched", url=r[0]["apply_url"])
    board.record(job_id, "filling", "form opened")
    return {"run_id": run_id}
