"""WhatsApp channel collector, via a self-hosted WAHA instance.

Channels are one-way broadcast and Meta's Cloud API does not expose them, so
this is unofficial by necessity. WAHA is the least bad route: free core
edition, runs in Docker, gives you structured messages instead of DOM soup.

    docker run -it --rm -p 3000:3000/tcp devlikeapro/waha
    open http://localhost:3000  and scan the QR with your phone

Then in config.yaml set sources.whatsapp.enabled: true and put the channel id
in channel_id (find it via /api/{session}/channels).

Read only. This never sends, which is what keeps the ban risk low.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from core.config import config
from core import db


def _get(url: str, params: dict | None = None, timeout: int = 20):
    import urllib.request
    import urllib.parse
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def list_channels() -> list[dict]:
    cfg = config()["sources"]["whatsapp"]
    base = cfg.get("waha_url", "http://localhost:3000").rstrip("/")
    session = cfg.get("session", "default")
    return _get(f"{base}/api/{session}/channels")


def collect(verbose: bool = True) -> dict:
    cfg = config()["sources"]["whatsapp"]
    if not cfg.get("enabled"):
        return {"skipped": True, "seen": 0, "new": 0, "duplicate": 0}

    base = cfg.get("waha_url", "http://localhost:3000").rstrip("/")
    session = cfg.get("session", "default")
    chan = cfg.get("channel_id", "")
    if not chan:
        print("  ! no channel_id in config.yaml. Available channels:")
        try:
            for ch in list_channels():
                print(f"      {ch.get('id')}   {ch.get('name')}")
        except Exception as e:
            print(f"      could not reach WAHA at {base}: {e}")
            print("      is the container running?")
        return {"seen": 0, "new": 0, "duplicate": 0}

    source_id = db.upsert_source("whatsapp", f"whatsapp:{chan}")
    try:
        msgs = _get(f"{base}/api/{session}/chats/{chan}/messages",
                    {"limit": int(cfg.get("limit", 200)), "downloadMedia": "false"})
    except Exception as e:
        print(f"  ! WAHA unreachable at {base}: {e}")
        return {"seen": 0, "new": 0, "duplicate": 0, "failed": 1}

    new = dup = 0
    for m in msgs if isinstance(msgs, list) else []:
        body = (m.get("body") or "").strip()
        if len(body) < 40:                    # stickers, reactions, one word posts
            continue
        ts = m.get("timestamp")
        received = (datetime.fromtimestamp(ts, timezone.utc).isoformat()
                    if isinstance(ts, (int, float)) else None)
        added = db.add_raw(
            source_id, str(m.get("id") or m.get("_serialized") or hash(body)), body,
            subject=body.split("\n")[0][:120], sender=f"whatsapp:{chan}",
            url="", received_at=received)
        new += added
        dup += (not added)

    stats = {"seen": len(msgs) if isinstance(msgs, list) else 0, "new": new,
             "duplicate": dup, "failed": 0}
    db.log("source", source_id, "collect", **stats)
    return stats
