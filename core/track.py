"""What happened after you applied.

Reply detection reads the threads you sent on, using the readonly scope the
collector already has. It classifies coarsely on purpose: an autoresponder is
not a reply, and a rejection is worth recording without pretending to grade it.
"""
from __future__ import annotations
import re

from . import db
from .config import config

AUTO = re.compile(r"out of office|auto[- ]?reply|do not reply|received your application|"
                  r"thank you for applying|we have received|application received", re.I)
REJECT = re.compile(r"not (moving|proceeding|selected|shortlisted)|unfortunately|"
                    r"regret to inform|other candidates|not a (fit|match)|"
                    r"decided to move forward with", re.I)
POSITIVE = re.compile(r"schedule|interview|next round|call|availability|"
                      r"assessment|test link|would like to speak|shortlisted", re.I)


def classify(text: str) -> str:
    if POSITIVE.search(text):
        return "positive"
    if REJECT.search(text):
        return "rejected"
    if AUTO.search(text):
        return "auto"
    return "reply"


def check_replies(verbose: bool = True) -> dict:
    """Look at every thread we sent on and see if anyone answered."""
    from collectors.gmail import service
    with db.tx() as c:
        apps = [dict(r) for r in c.execute(
            "SELECT id, thread_id FROM applications"
            " WHERE thread_id IS NOT NULL AND status='submitted'")]
    if not apps:
        return {"checked": 0, "replies": 0}

    svc = service()
    stats = {"checked": 0, "replies": 0, "positive": 0, "rejected": 0, "auto": 0}
    for a in apps:
        stats["checked"] += 1
        try:
            th = svc.users().threads().get(userId="me", id=a["thread_id"],
                                           format="full").execute()
        except Exception:
            continue
        msgs = th.get("messages", [])
        if len(msgs) < 2:
            continue                                  # only our own mail in there

        inbound = [m for m in msgs[1:] if "SENT" not in (m.get("labelIds") or [])]
        if not inbound:
            continue

        text = " ".join(m.get("snippet", "") for m in inbound)
        kind = classify(text)
        stats["replies"] += 1
        stats[kind] = stats.get(kind, 0) + 1
        with db.tx() as c:
            c.execute("UPDATE applications SET status=?, outcome=?,"
                      " response_at=datetime('now') WHERE id=?",
                      ("replied", kind, a["id"]))
        db.log("application", a["id"], "reply", kind=kind, snippet=text[:200])
        if verbose:
            print(f"  reply on application {a['id']}: {kind}")
    return stats


FOLLOWUP = """Hi{name},

Following up on my application for the {title} role, sent on {date}.

Still very interested. Happy to send anything else that would help.

Thanks,
{me}"""


def due_followups() -> list[dict]:
    cfg = config()["limits"]
    days = int(cfg.get("followup_after_days", 7))
    maxn = int(cfg.get("followups_max", 1))
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT a.id, a.job_id, a.submitted_at, j.title, co.name company_name,"
            "       c.email to_email, c.name to_name, a.thread_id, r.path resume_path"
            "  FROM applications a"
            "  JOIN jobs j       ON j.id = a.job_id"
            "  JOIN drafts d     ON d.id = a.draft_id"
            "  LEFT JOIN companies co ON co.id = j.company_id"
            "  LEFT JOIN contacts  c  ON c.id = d.contact_id"
            "  LEFT JOIN resumes   r  ON r.id = a.resume_id"
            " WHERE a.status='submitted'"
            "   AND a.submitted_at < datetime('now', ?)"
            "   AND (SELECT COUNT(*) FROM events e WHERE e.entity='application'"
            "        AND e.entity_id=a.id AND e.kind='followup') < ?",
            (f"-{days} days", maxn))]


def build_followup(app: dict) -> dict:
    from .config import profile
    name = f" {app['to_name'].split()[0]}" if app.get("to_name") else ""
    return {
        "subject": f"Re: {app['title']} - following up",
        "body": FOLLOWUP.format(
            name=name, title=app["title"],
            date=(app.get("submitted_at") or "")[:10],
            me=profile()["identity"]["full_name"]),
    }


def report() -> dict:
    with db.tx() as c:
        total = c.execute("SELECT COUNT(*) n FROM applications"
                          " WHERE status IN ('submitted','replied')").fetchone()["n"]
        rows = {r["outcome"]: r["n"] for r in c.execute(
            "SELECT outcome, COUNT(*) n FROM applications"
            " WHERE outcome IS NOT NULL GROUP BY outcome")}
        forms = c.execute("SELECT COUNT(*) n FROM applications"
                          " WHERE channel='form' AND status='submitted'").fetchone()["n"]
    replies = sum(v for k, v in rows.items() if k != "auto")
    return {"sent": total, "form_applications": forms, "replies": replies,
            "reply_rate": round(replies / total, 3) if total else 0.0, **rows}
