"""The only code in this project that sends anything.

Every guard lives here, and every one of them is a hard stop rather than a
warning, because the failure mode is a restricted Gmail account in the middle
of placement season.

  approval    only rows with status='approved' are ever read
  dry run     on by default; you flip it in config.yaml, deliberately
  daily cap   counted from the send_log table, not from memory
  cooldown    one company, once, inside company_cooldown_days
  verified    never an address that was guessed rather than found
  spacing     randomized gaps, because bursts are what trips filters
"""
from __future__ import annotations
import base64
import random
import time
from email.message import EmailMessage
from pathlib import Path

from .config import config, profile, path
from . import db

# gmail.send only. It cannot read, cannot delete, cannot modify.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CREDS = path("secrets", "credentials.json")
TOKEN = path("secrets", "token_send.json")


class Blocked(Exception):
    """A guard said no. Never caught and retried automatically."""


def service():
    # imported here, not at module load, so you can draft, review, edit and
    # approve without the google libraries or any credentials present. Only
    # actually sending needs them.
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise Blocked("google api libraries missing: pip install -r requirements.txt") from e

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS.exists():
                raise Blocked(f"missing {CREDS}, see README")
            creds = InstalledAppFlow.from_client_secrets_file(
                str(CREDS), SCOPES).run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ------------------------------------------------------------------ guards

def sent_today() -> int:
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) n FROM send_log"
                         " WHERE day = date('now','localtime')").fetchone()["n"]


def remaining_today() -> int:
    cap = int(config()["limits"]["emails_per_day"])
    return max(0, cap - sent_today())


def company_on_cooldown(company_id: int) -> int:
    days = int(config()["limits"].get("company_cooldown_days", 45))
    with db.tx() as c:
        r = c.execute(
            "SELECT COUNT(*) n FROM send_log s"
            " JOIN applications a ON a.id = s.application_id"
            " JOIN jobs j ON j.id = a.job_id"
            " WHERE j.company_id = ? AND s.at > datetime('now', ?)",
            (company_id, f"-{days} days")).fetchone()
    return r["n"]


def check(app: dict) -> None:
    """Raise Blocked with a reason, or return quietly. No middle ground."""
    cfg = config()
    if app.get("status") != "approved":
        raise Blocked(f"status is '{app.get('status')}', not approved")
    if remaining_today() <= 0:
        raise Blocked(f"daily cap of {cfg['limits']['emails_per_day']} already reached")
    if cfg["safety"].get("require_verified_contact", True) and not app.get("verified"):
        raise Blocked("contact address is not verified, and guessing bounces")
    if not app.get("to_email"):
        raise Blocked("no recipient address")
    mine = (profile()["identity"].get("email") or "").lower()
    if mine and app["to_email"].strip().lower() == mine:
        raise Blocked("that is your own address. Something upstream mistook the "
                      "recipient of a job alert for the employer's contact.")
    # the cooldown exists to stop you cold mailing the same company twice. A
    # follow up on a thread you already started is the opposite of that.
    if app.get("channel") != "followup" and company_on_cooldown(app["company_id"]):
        raise Blocked(f"already contacted this company inside "
                      f"{cfg['limits'].get('company_cooldown_days',45)} days")


# ------------------------------------------------------------------ send

def compose(to: str, subject: str, body: str, attachment: str | None,
            in_reply_to: str | None = None) -> EmailMessage:
    ident = profile()["identity"]
    m = EmailMessage()
    m["To"] = to
    m["From"] = f"{ident['full_name']} <{ident['email']}>"
    m["Subject"] = subject
    m["Reply-To"] = ident["email"]
    if in_reply_to:
        # without these a follow up arrives as a second cold mail rather than a
        # reply, which reads worse than not following up at all
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body)                    # plain text only. no html part.
    if attachment:
        p = Path(attachment)
        if p.exists():
            m.add_attachment(p.read_bytes(), maintype="application",
                             subtype="pdf", filename=p.name)
    return m


def send_one(app: dict, dry_run: bool | None = None) -> dict:
    cfg = config()
    dry = cfg["safety"].get("dry_run", True) if dry_run is None else dry_run
    check(app)

    msg = compose(app["to_email"], app["subject"], app["body"],
                  app.get("resume_path"), app.get("in_reply_to"))

    if dry:
        return {"dry_run": True, "to": app["to_email"], "subject": app["subject"],
                "bytes": len(msg.as_bytes()),
                "attachment": Path(app["resume_path"]).name if app.get("resume_path") else None}

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {"raw": raw}
    if app.get("thread_id"):
        payload["threadId"] = app["thread_id"]      # keeps it in the same thread
    res = service().users().messages().send(userId="me", body=payload).execute()

    with db.tx() as c:
        c.execute("INSERT INTO send_log (day, to_email, application_id)"
                  " VALUES (date('now','localtime'), ?, ?)",
                  (app["to_email"], app["id"]))
        c.execute("UPDATE applications SET status='submitted', thread_id=?,"
                  " submitted_at=datetime('now') WHERE id=?",
                  (res.get("threadId"), app["id"]))
    db.log("application", app["id"], "sent", to=app["to_email"], thread=res.get("threadId"))
    return {"dry_run": False, "to": app["to_email"], "message_id": res.get("id"),
            "thread_id": res.get("threadId")}


def pace() -> None:
    gap = int(config()["limits"].get("min_gap_seconds", 90))
    time.sleep(random.uniform(gap, gap * 3))
