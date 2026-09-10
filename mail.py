#!/usr/bin/env python3
"""The mailer. Drafts outreach, waits for you, then sends within caps.

    python mail.py draft              draft for every eligible job
    python mail.py followup           queue the follow ups that are due
    python mail.py queue              what is waiting for review
    python mail.py show <app_id>      read one draft in full
    python mail.py edit <app_id>      open it in $EDITOR
    python mail.py approve <app_id>   mark it ready to send
    python mail.py reject <app_id>    drop it
    python mail.py send               send everything approved, within caps
    python mail.py send --live        actually send (default is a dry run)
    python mail.py status             caps, cooldowns, what went out today

Nothing leaves your machine until a row is approved AND you pass --live.
"""
from __future__ import annotations
import sys
import os
import json
import argparse
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import db, draft as drafter, send as sender, pick   # noqa: E402
from core.config import config                                # noqa: E402


def _rows(sql, args=()):
    with db.tx() as c:
        return [dict(r) for r in c.execute(sql, args)]


APP_SQL = """
SELECT a.id, a.status, a.job_id, a.resume_id, a.created_at, a.thread_id,
       a.channel,
       d.subject, d.body, d.evidence_ids,
       j.title, j.company_id, j.fit_score, j.apply_url,
       co.name AS company_name,
       c.email AS to_email, c.name AS to_name, c.verified,
       r.path AS resume_path
  FROM applications a
  JOIN drafts d      ON d.id = a.draft_id
  JOIN jobs j        ON j.id = a.job_id
  LEFT JOIN companies co ON co.id = j.company_id
  LEFT JOIN contacts  c  ON c.id = d.contact_id
  LEFT JOIN resumes   r  ON r.id = a.resume_id
"""


def cmd_draft(a):
    cfg = config()
    floor = float(cfg["scoring"].get("min_fit_to_draft", 0.55))
    jobs = _rows(
        "SELECT j.*, co.name company_name FROM jobs j"
        " LEFT JOIN companies co ON co.id=j.company_id"
        " WHERE j.fit_score >= ?"
        "   AND j.id NOT IN (SELECT job_id FROM applications WHERE channel='email')"
        "   AND EXISTS (SELECT 1 FROM contacts c WHERE c.company_id=j.company_id"
        "               AND c.verified=1)"
        " ORDER BY j.fit_score DESC LIMIT ?", (floor, a.limit))
    if not jobs:
        print("  nothing to draft.")
        print("  Jobs need a fit score above the floor and a verified contact address.")
        print("  Postings with only an apply link go through fill.py instead.")
        return 0

    made = 0
    for j in jobs:
        con = _rows("SELECT * FROM contacts WHERE company_id=? AND verified=1 LIMIT 1",
                    (j["company_id"],))
        contact = con[0] if con else None
        print(f"  drafting {j['company_name']} / {j['title']}")

        res = pick.resolve(j)
        from core.resume import record
        if res.get("audit"):
            resume_id = record(j["id"], res["audit"])
        else:
            resume_id = record(j["id"], {
                "path": res["path"], "bullet_ids": [], "keywords": [],
                "coverage": 0.0, "tagline": f"mode={res['mode']}"})
        if res.get("missing"):
            print(f"    ! resume not found: {res['path']}")
            print(f"    ! put a pdf there, or the mail goes out with no attachment")
        elif res.get("needs_manual"):
            print(f"    ! {res['reason']}")
            print(f"    ! jd written to {res.get('jd_file')}")
            print(f"    ! drop your pdf at assets/custom/{j['id']}.pdf and rerun")

        d = drafter.build(j, contact, use_llm=not a.no_llm)
        did = drafter.save(j["id"], contact["id"] if contact else None, d)
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO applications"
                      " (job_id, draft_id, resume_id, channel, status)"
                      " VALUES (?,?,?,'email','needs_review')",
                      (j["id"], did, resume_id))
        made += 1

    print(f"\n  {made} drafts waiting. Read them: python mail.py queue")
    return 0


def cmd_followup(a):
    """Queue the follow ups that are due. They go through the same gate as
    everything else: drafted, reviewed by you, then sent within the same cap."""
    from core import track
    due = track.due_followups()
    if not due:
        print("  nothing due. A follow up is queued once, "
              f"{config()['limits'].get('followup_after_days', 7)} days after "
              "sending, and only if nobody replied.")
        return 0

    made = 0
    for app in due:
        if not app.get("to_email"):
            print(f"  skipping {app['company_name']}: no address on record")
            continue
        fu = track.build_followup(app)
        with db.tx() as c:
            did = c.execute(
                "INSERT INTO drafts (job_id, contact_id, subject, body, evidence_ids)"
                " SELECT ?, d.contact_id, ?, ?, '[]' FROM drafts d"
                " JOIN applications a ON a.draft_id = d.id WHERE a.id = ?",
                (app["job_id"], fu["subject"], fu["body"], app["id"])).lastrowid
            # a follow up is its own application row on a separate channel, so
            # the unique (job_id, channel) constraint does not fight it
            c.execute(
                "INSERT OR IGNORE INTO applications"
                " (job_id, draft_id, resume_id, channel, status, thread_id)"
                " SELECT ?, ?, a.resume_id, 'followup', 'needs_review', a.thread_id"
                "   FROM applications a WHERE a.id = ?",
                (app["job_id"], did, app["id"]))
        db.log("application", app["id"], "followup", queued_draft=did)
        print(f"  queued follow up to {app['company_name']} "
              f"(sent {app['submitted_at'][:10]})")
        made += 1

    if made:
        print(f"\n  {made} waiting for you: python mail.py queue")
    return 0


def cmd_queue(a):
    rows = _rows(APP_SQL + " WHERE a.status = ? ORDER BY j.fit_score DESC",
                 (a.status,))
    if not rows:
        print(f"  nothing with status '{a.status}'")
        return 0
    print(f"  {'app':>4}  {'fit':>4}  {'company':<22} {'to':<30} {'resume':<10} subject")
    print("  " + "-" * 116)
    for r in rows:
        print(f"  {r['id']:>4}  {r['fit_score'] or 0:>4.2f}  "
              f"{(r['company_name'] or '?')[:21]:<22} "
              f"{(r['to_email'] or 'NO ADDRESS')[:29]:<30} "
              f"{(Path(r['resume_path']).name[:9] if r['resume_path'] else '-'):<10} "
              f"{(r['subject'] or '')[:44]}")
    print(f"\n  {len(rows)} shown. python mail.py show <app> to read one.")
    return 0


def cmd_show(a):
    rows = _rows(APP_SQL + " WHERE a.id = ?", (a.id,))
    if not rows:
        print(f"  no application {a.id}")
        return 1
    r = rows[0]
    print(f"\n  application {r['id']}   status {r['status']}   fit {r['fit_score']}")
    print(f"  to        {r['to_email'] or '(none)'}"
          f"{'' if r['verified'] else '   UNVERIFIED, will be blocked'}")
    print(f"  resume    {r['resume_path'] or '(none attached)'}")
    print(f"  evidence  {', '.join(json.loads(r['evidence_ids'] or '[]')) or '-'}")
    print(f"\n  Subject: {r['subject']}")
    print("  " + "-" * 66)
    print("  " + (r["body"] or "").replace("\n", "\n  "))
    print("  " + "-" * 66)
    print(f"\n  approve: python mail.py approve {r['id']}"
          f"      edit: python mail.py edit {r['id']}")
    return 0


def cmd_edit(a):
    rows = _rows("SELECT d.id, d.subject, d.body FROM applications a"
                 " JOIN drafts d ON d.id=a.draft_id WHERE a.id=?", (a.id,))
    if not rows:
        print(f"  no application {a.id}")
        return 1
    d = rows[0]
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as f:
        f.write(f"Subject: {d['subject']}\n\n{d['body']}")
        tmp = f.name
    subprocess.call([os.environ.get("EDITOR", "nano"), tmp])
    text = Path(tmp).read_text()
    Path(tmp).unlink(missing_ok=True)

    subject, _, body = text.partition("\n\n")
    subject = subject.replace("Subject:", "", 1).strip()
    with db.tx() as c:
        c.execute("UPDATE drafts SET subject=?, body=?, edited=1 WHERE id=?",
                  (subject, body.strip(), d["id"]))
    print(f"  saved. python mail.py show {a.id}")
    return 0


def _set_status(app_id: int, status: str) -> int:
    with db.tx() as c:
        cur = c.execute("UPDATE applications SET status=? WHERE id=?", (status, app_id))
    if cur.rowcount:
        db.log("application", app_id, status)
        print(f"  application {app_id} -> {status}")
        return 0
    print(f"  no application {app_id}")
    return 1


def cmd_approve(a):
    return _set_status(a.id, "approved")


def cmd_reject(a):
    return _set_status(a.id, "rejected")


def cmd_send(a):
    cfg = config()
    dry = not a.live
    if dry:
        print("  DRY RUN. Nothing will be sent. Add --live when you mean it.\n")
    elif cfg["safety"].get("dry_run", True) and not a.force:
        # two independent switches. --live alone is deliberately not enough.
        print("  config.yaml still has safety.dry_run: true")
        print("  Set it to false once you have read at least ten drafts, then rerun.")
        print("  Or pass --force if you know exactly what you are doing.")
        return 1

    rows = _rows(APP_SQL + " WHERE a.status='approved' ORDER BY j.fit_score DESC")
    if not rows:
        print("  nothing approved. python mail.py queue")
        return 0

    left = sender.remaining_today()
    print(f"  {len(rows)} approved, {left} sends left today "
          f"(cap {cfg['limits']['emails_per_day']})\n")

    done = blocked = 0
    for i, r in enumerate(rows):
        if r.get("channel") == "followup":
            r = {**r, "resume_path": None}    # they already have it
        try:
            out = sender.send_one(r, dry_run=dry)
        except sender.Blocked as e:
            print(f"  [blocked] {r['company_name']}: {e}")
            blocked += 1
            continue
        except Exception as e:
            print(f"  [error]   {r['company_name']}: {str(e).splitlines()[0][:120]}")
            blocked += 1
            continue
        done += 1
        tag = "would send" if out.get("dry_run") else "SENT"
        print(f"  [{tag}] {r['company_name']:<22} -> {out['to']}")
        if not dry and i < len(rows) - 1 and sender.remaining_today() > 0:
            sender.pace()

    print(f"\n  {done} {'would go out' if dry else 'sent'}, {blocked} blocked")
    if dry and done:
        print("  Happy with these? python mail.py send --live")
    return 0


def cmd_status(a):
    cfg = config()
    print(f"  dry_run in config      {cfg['safety'].get('dry_run')}")
    print(f"  daily cap              {cfg['limits']['emails_per_day']}")
    print(f"  sent today             {sender.sent_today()}")
    print(f"  remaining today        {sender.remaining_today()}")
    print(f"  company cooldown       {cfg['limits'].get('company_cooldown_days')} days")
    print(f"  verified contacts only {cfg['safety'].get('require_verified_contact')}")
    counts = _rows("SELECT status, COUNT(*) n FROM applications GROUP BY status")
    if counts:
        print("\n  applications:")
        for c in counts:
            print(f"    {c['status']:<16} {c['n']}")
    recent = _rows("SELECT to_email, at FROM send_log ORDER BY at DESC LIMIT 10")
    if recent:
        print("\n  last sends:")
        for r in recent:
            print(f"    {r['at']}  {r['to_email']}")
    return 0


COMMANDS = {"draft": cmd_draft, "followup": cmd_followup, "queue": cmd_queue,
            "show": cmd_show, "edit": cmd_edit, "approve": cmd_approve,
            "reject": cmd_reject, "send": cmd_send, "status": cmd_status}


def main():
    p = argparse.ArgumentParser(prog="mail.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=sorted(COMMANDS))
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--live", action="store_true", help="actually send")
    p.add_argument("--force", action="store_true", help="ignore config dry_run")
    p.add_argument("--status", default="needs_review", help="for queue")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--no-llm", action="store_true", help="template only")
    a = p.parse_args()
    if a.command in ("show", "edit", "approve", "reject") and a.id is None:
        p.error(f"{a.command} needs an application id")
    sys.exit(COMMANDS[a.command](a) or 0)


if __name__ == "__main__":
    main()
