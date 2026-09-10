"""Gmail collector.

Reads one label, not your inbox. That boundary matters: the system never has
a reason to touch personal mail, and you control what it sees by editing a
Gmail filter instead of editing code.
"""
from __future__ import annotations
import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from core.config import config, path
from core import db

# readonly here. the sender uses a separate, narrower client at step 04, so a
# bug in the collector can never send anything.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDS = path("secrets", "credentials.json")
TOKEN = path("secrets", "token.json")


def service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise SystemExit("google api libraries missing: pip install -r requirements.txt") from e

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS.exists():
                raise SystemExit(
                    f"missing {CREDS}\n"
                    "Get it from console.cloud.google.com: enable the Gmail API, "
                    "create an OAuth client ID of type Desktop app, download the "
                    "JSON, save it there. See README."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _label_id(svc, name: str) -> str:
    for lb in svc.users().labels().list(userId="me").execute().get("labels", []):
        if lb["name"].lower() == name.lower():
            return lb["id"]
    raise SystemExit(
        f"no Gmail label called '{name}'. Create it and add a filter that applies "
        f"it to your job alert senders, then rerun."
    )


def _walk(part, out):
    """Depth first over the MIME tree, preferring text/plain."""
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    data = body.get("data")
    if data and mime in ("text/plain", "text/html"):
        try:
            text = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        except Exception:
            return
        out.setdefault(mime, []).append(text)
    for sub in part.get("parts", []) or []:
        _walk(sub, out)


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]*\n[ \t]*")


def _body_text(payload) -> str:
    found: dict[str, list[str]] = {}
    _walk(payload, found)
    if found.get("text/plain"):
        text = "\n".join(found["text/plain"])
    elif found.get("text/html"):
        html = "\n".join(found["text/html"])
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
        text = _TAG.sub(" ", html)
    else:
        return ""
    text = text.replace("\r", "")
    text = re.sub(r"&nbsp;?", " ", text)
    text = _WS.sub("\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def _header(msg, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def collect(verbose: bool = True) -> dict:
    cfg = config()["sources"]["gmail"]
    if not cfg.get("enabled", True):
        return {"skipped": True}

    svc = service()
    label = cfg.get("label", "job-alerts")
    lid = _label_id(svc, label)
    source_id = db.upsert_source("gmail", f"gmail:{label}")

    # after: is a date filter, so we lose nothing by re-asking for a day we
    # already have. the unique constraint on (source_id, external_id) dedupes.
    cursor = db.get_cursor(source_id)
    if cursor:
        since = cursor
    else:
        days = int(cfg.get("lookback_days", 30))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"after:{since}"

    ids, page = [], None
    while True:
        resp = svc.users().messages().list(
            userId="me", labelIds=[lid], q=query, pageToken=page, maxResults=200
        ).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break

    new = skipped = failed = 0
    newest = None
    for mid in ids:
        try:
            msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            failed += 1
            if verbose:
                print(f"  ! {mid}: {e}")
            continue

        body = _body_text(msg.get("payload", {}))
        if not body:
            body = msg.get("snippet", "")
        date_hdr = _header(msg, "Date")
        try:
            received = parsedate_to_datetime(date_hdr).astimezone(timezone.utc)
        except Exception:
            received = None
        if received and (newest is None or received > newest):
            newest = received

        added = db.add_raw(
            source_id, mid, body,
            subject=_header(msg, "Subject"),
            sender=_header(msg, "From"),
            url=f"https://mail.google.com/mail/u/0/#all/{msg.get('threadId', mid)}",
            received_at=received.isoformat() if received else None,
        )
        new += added
        skipped += (not added)

    if newest:
        # step back a day so a message that arrived mid-run is not missed
        db.set_cursor(source_id, (newest - timedelta(days=1)).strftime("%Y/%m/%d"))

    stats = {"seen": len(ids), "new": new, "duplicate": skipped, "failed": failed}
    db.log("source", source_id, "collect", **stats)
    return stats
