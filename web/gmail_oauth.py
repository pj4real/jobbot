"""Connecting Gmail from the dashboard instead of the terminal.

The CLI uses run_local_server(), which spins up its own throwaway web server on
a random port. That is fine at a terminal and awkward from a page that is
already a web server. So this does the redirect properly: the dashboard is the
redirect target, which a Desktop OAuth client allows because loopback redirects
are exempt from the exact-match rule.

Two separate connections, deliberately. Read and send are different scopes with
different token files, so a bug in the collector cannot send and a bug in the
mailer cannot read.
"""
from __future__ import annotations
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CREDS = ROOT / "secrets" / "credentials.json"

CONNECTIONS = {
    "read": {
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "token": ROOT / "secrets" / "token.json",
        "label": "Read access",
        "why": "Lets the finder pull your job-alerts label, and the tracker see "
               "replies. It cannot send anything.",
    },
    "send": {
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        "token": ROOT / "secrets" / "token_send.json",
        "label": "Send access",
        "why": "Lets the mailer send approved drafts. It cannot read your mail, "
               "and it still obeys the daily cap and the approval gate.",
    },
}

_pending: dict[str, dict] = {}


class OAuthError(Exception):
    pass


LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _allow_loopback_http(redirect_uri: str) -> None:
    """oauthlib refuses any non-https redirect, full stop. That rule is right
    for a server on the internet and wrong for this: RFC 8252 specifically
    allows http on the loopback interface for native apps, and Google supports
    it, which is the whole reason a Desktop client works at all.

    So the check is relaxed only after confirming the redirect really is
    loopback. If this ever ran on a real host the original protection stands.
    """
    host = (urlparse(redirect_uri).hostname or "").lower()
    if host not in LOOPBACK:
        raise OAuthError(
            f"The redirect points at '{host}', not loopback. This dashboard is "
            f"meant to run on 127.0.0.1. Refusing to disable transport security "
            f"for a remote host.")
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    # Google routinely returns openid alongside what you asked for, which
    # oauthlib treats as a scope change and raises on. Relax that, then check
    # the granted scope ourselves in finish(), which is the part that matters.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"


def _flow(which: str, redirect_uri: str):
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as e:
        raise OAuthError("google-auth-oauthlib is not installed. "
                         "pip install -r requirements.txt") from e
    if not CREDS.exists():
        raise OAuthError("No secrets/credentials.json yet. Upload it on the "
                         "files page first.")
    _allow_loopback_http(redirect_uri)
    cfg = CONNECTIONS[which]
    return Flow.from_client_secrets_file(str(CREDS), scopes=cfg["scopes"],
                                         redirect_uri=redirect_uri)


def start(which: str, redirect_uri: str) -> str:
    if which not in CONNECTIONS:
        raise OAuthError("unknown connection")
    flow = _flow(which, redirect_uri)
    state = secrets.token_urlsafe(24)
    url, _ = flow.authorization_url(
        access_type="offline",          # so we get a refresh token
        include_granted_scopes="false",  # keep the two connections separate
        prompt="consent",               # force a refresh token even on reconnect
        state=state)
    _pending[state] = {"which": which, "redirect_uri": redirect_uri}
    return url


def finish(state: str, full_url: str) -> str:
    p = _pending.pop(state, None)
    if not p:
        raise OAuthError("That consent link has expired or was already used. "
                         "Start the connection again.")
    flow = _flow(p["which"], p["redirect_uri"])
    try:
        flow.fetch_token(authorization_response=full_url)
    except Exception as e:                       # noqa: BLE001
        raise OAuthError(f"Google refused the exchange: {e}") from e

    cfg = CONNECTIONS[p["which"]]

    # we relaxed oauthlib's scope check above, so do the real one here: the
    # token has to actually carry the scope this connection is for.
    granted = set(getattr(flow.credentials, "scopes", None) or [])
    wanted = set(cfg["scopes"])
    if granted and not wanted <= granted:
        raise OAuthError(
            f"Google granted {', '.join(sorted(granted)) or 'nothing'} but this "
            f"connection needs {', '.join(sorted(wanted))}. Untick nothing on the "
            f"consent screen and try again.")

    cfg["token"].parent.mkdir(parents=True, exist_ok=True)
    cfg["token"].write_text(flow.credentials.to_json())
    extra = ""
    if not flow.credentials.refresh_token:
        extra = (" Google did not return a refresh token, so this will stop "
                 "working shortly. Disconnect and connect again.")
    return f"{cfg['label']} connected.{extra}"


def status() -> list[dict]:
    out = []
    for which, cfg in CONNECTIONS.items():
        tok = cfg["token"]
        info = {"which": which, "label": cfg["label"], "why": cfg["why"],
                "connected": tok.exists(), "account": "", "expired": False,
                "path": str(tok.relative_to(ROOT))}
        if tok.exists():
            try:
                d = json.loads(tok.read_text())
                info["account"] = d.get("account") or ""
                info["has_refresh"] = bool(d.get("refresh_token"))
            except Exception:                    # noqa: BLE001
                info["connected"] = False
        out.append(info)
    return out


def disconnect(which: str) -> str:
    """Move the token aside rather than deleting it, same as everything else."""
    import datetime
    import shutil
    cfg = CONNECTIONS.get(which)
    if not cfg or not cfg["token"].exists():
        return "nothing to disconnect"
    bak = ROOT / "data" / "backups"
    bak.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(str(cfg["token"]), bak / f"{cfg['token'].name}.{stamp}")
    return f"{cfg['label']} disconnected. The token is in data/backups."


def labels() -> tuple[list[str], str]:
    """What labels the account actually has, so the job-alerts filter can be
    checked rather than assumed."""
    cfg = CONNECTIONS["read"]
    if not cfg["token"].exists():
        return [], "not connected yet"
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(str(cfg["token"]),
                                                      cfg["scopes"])
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        got = svc.users().labels().list(userId="me").execute().get("labels", [])
        return sorted(l["name"] for l in got), ""
    except Exception as e:                       # noqa: BLE001
        return [], str(e).splitlines()[0][:200]
