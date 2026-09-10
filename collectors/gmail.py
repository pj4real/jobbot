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
    """Scan the mailbox.

    Two modes. `scan_all` walks the whole mailbox through Gmail's own search,
    which is what you want: openings arrive from friends, placement cells and
    company addresses that no filter would have caught. `label` mode reads one
    label, which is cheaper and narrower.

    Either way nothing but the shortlist is ever downloaded, because the first
    cut happens on Google's servers.
    """
    cfg = config()["sources"]["gmail"]
    if not cfg.get("enabled", True):
        return {"skipped": True}

    svc = service()
    if cfg.get("scan_all", True):
        return _collect_scan(svc, cfg, verbose)

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


# ---------------------------------------------------------------- whole inbox

def _collect_scan(svc, cfg, verbose: bool) -> dict:
    from core import triage

    source_id = db.upsert_source("gmail", "gmail:scan")
    days = int(cfg.get("lookback_days", 30))
    cursor = db.get_cursor(source_id)
    extra = f"after:{cursor}" if cursor else ""
    query = triage.gmail_query(days=days, extra=extra)

    ids, page = [], None
    cap = int(cfg.get("max_messages", 800))
    while len(ids) < cap:
        resp = svc.users().messages().list(
            userId="me", q=query, pageToken=page,
            maxResults=min(200, cap - len(ids))).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break

    if verbose:
        print(f"  gmail search matched {len(ids)} messages")

    stats = {"seen": len(ids), "new": 0, "duplicate": 0, "failed": 0,
             "not_job": 0, "muted": 0, "unsure": 0, "llm_calls": 0}
    newest = None
    unsure: list[dict] = []

    for mid in ids:
        try:
            msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception as e:
            stats["failed"] += 1
            if verbose:
                print(f"  ! {mid}: {e}")
            continue

        subject = _header(msg, "Subject")
        sender = _header(msg, "From")
        body = _body_text(msg.get("payload", {})) or msg.get("snippet", "")
        addr = triage.address_of(sender)

        date_hdr = _header(msg, "Date")
        try:
            received = parsedate_to_datetime(date_hdr).astimezone(timezone.utc)
        except Exception:
            received = None
        if received and (newest is None or received > newest):
            newest = received

        verdict, conf, why = triage.classify(subject, body, sender)

        if verdict == "not_job":
            stats["muted" if "marked this sender" in why else "not_job"] += 1
            continue
        if verdict == "unsure":
            unsure.append({"id": mid, "subject": subject, "sender": sender,
                           "body": body, "received": received})
            continue

        stats["new"] += _store(source_id, mid, subject, sender, body, received, msg)

    # only the genuinely ambiguous reach the model, and only in batches
    if unsure:
        stats["unsure"] = len(unsure)
        batch = int(config().get("llm", {}).get("batch_size", 15))
        from core.llm import available
        if available():
            for i in range(0, len(unsure), batch):
                chunk = unsure[i:i + batch]
                stats["llm_calls"] += 1
                if verbose:
                    print(f"  asking about {len(chunk)} ambiguous messages")
                keep = triage.classify_llm(chunk)
                for j, it in enumerate(chunk):
                    if keep.get(j, True):
                        stats["new"] += _store(source_id, it["id"], it["subject"],
                                               it["sender"], it["body"],
                                               it["received"], None)
                        triage.remember(triage.address_of(it["sender"]), "job",
                                        "judged a job sender", found=1)
                    else:
                        triage.remember(triage.address_of(it["sender"]), "not_job",
                                        "judged not a job sender")
                        stats["not_job"] += 1
        else:
            # no model available: keep them rather than lose an opening
            for it in unsure:
                stats["new"] += _store(source_id, it["id"], it["subject"],
                                       it["sender"], it["body"], it["received"], None)

    if newest:
        db.set_cursor(source_id, (newest - timedelta(days=1)).strftime("%Y/%m/%d"))

    stats["duplicate"] = max(0, stats["seen"] - stats["new"] - stats["not_job"]
                             - stats["muted"] - stats["failed"])
    db.log("source", source_id, "scan", **stats)
    return stats


def _store(source_id, mid, subject, sender, body, received, msg) -> int:
    from core import triage
    thread = (msg or {}).get("threadId", mid)
    added = db.add_raw(
        source_id, mid, body, subject=subject, sender=sender,
        url=f"https://mail.google.com/mail/u/0/#all/{thread}",
        received_at=received.isoformat() if received else None)
    if added:
        triage.note_seen(triage.address_of(sender))
    return 1 if added else 0
