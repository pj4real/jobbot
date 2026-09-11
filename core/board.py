"""The view the user actually thinks in.

Nobody thinks in "raw_items, jobs, drafts, applications". They think: what did
you find, what am I doing about it, who owes me a reply, what is dead. This
module turns the tables into those four answers, and works out the single next
action for each row so the interface never has to guess.

Keeping it here rather than in the web layer means the CLI and the dashboard
agree about what "waiting" means.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from . import db
from .config import config

# what a row is doing, in the order a job moves through it
STAGES = ["found", "shortlisted", "applied", "waiting", "replied", "closed"]

STAGE_LABEL = {
    "found": "Found",
    "shortlisted": "To apply",
    "applied": "Applied",
    "waiting": "Waiting",
    "replied": "Replied",
    "closed": "Closed",
}

STAGE_BLURB = {
    "found": "New openings from your mail. Keep or skip each one.",
    "shortlisted": "You said yes. Nothing has gone out yet.",
    "applied": "Sent or submitted in the last few days.",
    "waiting": "No answer yet. A follow up unlocks after a week.",
    "replied": "Somebody answered. These need you.",
    "closed": "Rejected, skipped, or the deadline passed.",
}

BOARD_SQL = """
SELECT j.id, j.title, j.location, j.apply_url, j.apply_email, j.apply_kind,
       j.deadline, j.fit_score, j.fit_reason, j.role_family, j.skills_json,
       j.triage, j.triage_at, j.created_at, j.description,
       co.name AS company,
       a.id AS app_id, a.status AS app_status, a.channel, a.submitted_at,
       a.response_at, a.outcome, a.screenshot, a.thread_id,
       d.subject, c.email AS to_email, c.verified,
       r.path AS resume_path
  FROM jobs j
  LEFT JOIN companies co ON co.id = j.company_id
  LEFT JOIN applications a ON a.job_id = j.id AND a.channel != 'followup'
  LEFT JOIN drafts d ON d.id = a.draft_id
  LEFT JOIN contacts c ON c.id = d.contact_id
  LEFT JOIN resumes r ON r.id = a.resume_id
"""


def _days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(ts[:19] if "T" not in ts else ts, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - d).days
        except ValueError:
            continue
    return None


def stage_of(row: dict) -> str:
    """One row, one stage. Order matters: the first match wins."""
    st = row.get("app_status")
    if row.get("triage") == "skipped" or st == "rejected":
        return "closed"
    if st == "replied":
        return "replied" if row.get("outcome") != "rejected" else "closed"
    if st == "submitted":
        wait = int(config()["limits"].get("followup_after_days", 7))
        days = _days_since(row.get("submitted_at"))
        return "waiting" if (days is not None and days >= wait) else "applied"
    if st in ("needs_review", "approved", "filled"):
        return "shortlisted"
    if row.get("triage") == "shortlisted":
        return "shortlisted"
    return "found"


def next_action(row: dict, stage: str) -> dict:
    """The one thing to do next. `kind` drives which button the UI shows."""
    if stage == "found":
        return {"kind": "triage", "label": "Keep or skip",
                "hint": row.get("fit_reason") or ""}

    if stage == "shortlisted":
        st = row.get("app_status")
        if st == "needs_review":
            return {"kind": "review", "label": "Read the draft",
                    "hint": f"to {row.get('to_email') or 'nobody yet'}"}
        if st == "approved":
            return {"kind": "send", "label": "Approved, ready to send",
                    "hint": "goes out on the next send run"}
        if row.get("apply_url"):
            from .urls import fillable
            can_fill, _ = fillable(row["apply_url"])
            if can_fill:
                return {"kind": "fill", "label": "Fill the form",
                        "hint": row.get("apply_kind") or ""}
            # a posting page is something you read and click Apply on. Offering
            # "fill" here launched a browser at a LinkedIn digest.
            return {"kind": "open", "label": "Open the posting",
                    "hint": row.get("apply_kind") or ""}
        if row.get("apply_email"):
            return {"kind": "draft", "label": "Write the mail",
                    "hint": row["apply_email"]}
        return {"kind": "open", "label": "No apply route found",
                "hint": "open the mail it came from"}

    if stage == "applied":
        d = _days_since(row.get("submitted_at"))
        return {"kind": "wait", "label": "Sent",
                "hint": "today" if not d else f"{d} day{'s' if d != 1 else ''} ago"}

    if stage == "waiting":
        d = _days_since(row.get("submitted_at")) or 0
        return {"kind": "followup", "label": "Follow up",
                "hint": f"silent for {d} days"}

    if stage == "replied":
        return {"kind": "reply", "label": "They answered",
                "hint": row.get("outcome") or "read it"}

    return {"kind": "done", "label": (row.get("outcome") or "closed").title(),
            "hint": row.get("fit_reason") or ""}


def board(limit_per_stage: int = 60, include_closed: bool = True) -> dict:
    floor = float(config()["scoring"].get("min_fit_to_draft", 0.55))
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(BOARD_SQL + " ORDER BY j.fit_score DESC, j.id DESC")]

    cols = {s: [] for s in STAGES}
    for r in rows:
        s = stage_of(r)
        if s == "found" and (r.get("fit_score") or 0) < floor and not r.get("triage"):
            s = "closed"
            r["fit_reason"] = f"scored {r.get('fit_score', 0):.2f}, below your floor"
        if s == "closed" and not include_closed:
            continue
        r["stage"] = s
        r["action"] = next_action(r, s)
        r["skills"] = json.loads(r.get("skills_json") or "[]")
        r["waiting_days"] = _days_since(r.get("submitted_at"))
        cols[s].append(r)

    return {s: cols[s][:limit_per_stage] for s in STAGES}


def counts() -> dict:
    b = board()
    return {s: len(b[s]) for s in STAGES}


def headline() -> dict:
    """What to tell someone who just opened the page."""
    b = board()
    if b["replied"]:
        return {"tone": "good", "n": len(b["replied"]),
                "text": f"{len(b['replied'])} {'company has' if len(b['replied']) == 1 else 'companies have'} replied",
                "where": "replied"}
    ready = [r for r in b["shortlisted"] if r["action"]["kind"] == "review"]
    if ready:
        return {"tone": "act", "n": len(ready),
                "text": f"{len(ready)} draft{'s' if len(ready) != 1 else ''} to read",
                "where": "shortlisted"}
    if b["found"]:
        return {"tone": "act", "n": len(b["found"]),
                "text": f"{len(b['found'])} new opening{'s' if len(b['found']) != 1 else ''} to look at",
                "where": "found"}
    if b["shortlisted"]:
        return {"tone": "act", "n": len(b["shortlisted"]),
                "text": f"{len(b['shortlisted'])} waiting for you to apply",
                "where": "shortlisted"}
    if b["waiting"]:
        return {"tone": "wait", "n": len(b["waiting"]),
                "text": f"{len(b['waiting'])} silent for over a week",
                "where": "waiting"}
    return {"tone": "idle", "n": 0, "text": "Nothing needs you right now",
            "where": "found"}


def timeline(job_id: int) -> list[dict]:
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT kind, detail, at FROM timeline WHERE job_id=? ORDER BY at", (job_id,))]
        if not rows:
            # nothing recorded explicitly: reconstruct from what we do know
            j = c.execute("SELECT created_at, triage_at, triage FROM jobs WHERE id=?",
                          (job_id,)).fetchone()
            a = c.execute("SELECT submitted_at, response_at, outcome, channel"
                          "  FROM applications WHERE job_id=? LIMIT 1", (job_id,)).fetchone()
            if j:
                rows.append({"kind": "found", "detail": "in your mail", "at": j["created_at"]})
                if j["triage_at"]:
                    rows.append({"kind": j["triage"] or "triaged", "detail": "by you",
                                 "at": j["triage_at"]})
            if a and a["submitted_at"]:
                rows.append({"kind": "applied", "detail": a["channel"],
                             "at": a["submitted_at"]})
            if a and a["response_at"]:
                rows.append({"kind": "replied", "detail": a["outcome"] or "",
                             "at": a["response_at"]})
    return rows


def set_triage(job_id: int, verdict: str, note: str = "") -> None:
    """You keeping or skipping a job. Recorded, and the sender learns from it."""
    with db.tx() as c:
        c.execute("UPDATE jobs SET triage=?, triage_at=datetime('now') WHERE id=?",
                  (verdict, job_id))
        c.execute("INSERT INTO timeline (job_id, kind, detail) VALUES (?,?,?)",
                  (job_id, verdict, note or "by you"))
    db.log("job", job_id, f"triage_{verdict}", note=note)


def record(job_id: int, kind: str, detail: str = "", app_id: int | None = None) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO timeline (job_id, application_id, kind, detail)"
                  " VALUES (?,?,?,?)", (job_id, app_id, kind, detail))
