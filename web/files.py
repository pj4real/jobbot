"""Uploading the files the system needs, from the browser.

Everything here is validated by content, not by filename. A .pdf that is not a
PDF would fail silently at the worst moment, halfway through a real application,
and a credentials.json with the wrong shape produces an OAuth error three steps
later that tells you nothing useful.

Nothing is ever deleted outright. Replaced files move to data/backups so a
mis-drop is always recoverable.
"""
from __future__ import annotations
import json
import re
import shutil
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "data" / "backups"
MAX_BYTES = 12 * 1024 * 1024


class Rejected(Exception):
    pass


# ------------------------------------------------------------------ validators

def _pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF"):
        raise Rejected("That is not a PDF. The first bytes should be %PDF, and "
                       "portals reject anything else at upload time.")
    if len(data) < 800:
        raise Rejected(f"Only {len(data)} bytes. That is not a real resume.")


def _credentials(data: bytes) -> None:
    try:
        d = json.loads(data.decode("utf-8"))
    except Exception as e:                       # noqa: BLE001
        raise Rejected(f"Not valid JSON: {e}") from e
    if not isinstance(d, dict):
        raise Rejected("The file should be a JSON object.")
    kind = "installed" if "installed" in d else ("web" if "web" in d else None)
    if not kind:
        raise Rejected("This does not look like an OAuth client file. It should "
                       "have a top level 'installed' key. Make sure you chose "
                       "application type Desktop app, not Web application.")
    if kind == "web":
        raise Rejected("This is a Web application client. Create a Desktop app "
                       "client instead: the redirect rules are different and a "
                       "web client will refuse the loopback redirect this uses.")
    block = d["installed"]
    for k in ("client_id", "client_secret", "auth_uri", "token_uri"):
        if not block.get(k):
            raise Rejected(f"The client file is missing '{k}'. Download it again "
                           f"from the Credentials page.")


# key -> (destination, label, accept, validator, description)
SLOTS = {
    "resume_default": ("assets/resume_default.pdf", "Default resume", ".pdf", _pdf,
                       "Attached to mail and uploaded to forms unless a better "
                       "match exists."),
    "resume_sde": ("assets/resume_sde.pdf", "SDE resume", ".pdf", _pdf,
                   "Used when a posting's role family is sde."),
    "resume_ml": ("assets/resume_ml.pdf", "ML resume", ".pdf", _pdf,
                  "Used when a posting's role family is ml."),
    "resume_product": ("assets/resume_product.pdf", "Product resume", ".pdf", _pdf,
                       "Used when a posting's role family is product."),
    "resume_data": ("assets/resume_data.pdf", "Data resume", ".pdf", _pdf,
                    "Used when a posting's role family is data."),
    "credentials": ("secrets/credentials.json", "Google OAuth client", ".json",
                    _credentials,
                    "Needed by the finder and the mailer. The form filler does "
                    "not use it."),
}


def _backup(p: Path) -> None:
    if not p.exists():
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy(p, BACKUPS / f"{p.name}.{stamp}")


def save_slot(key: str, data: bytes) -> str:
    if key not in SLOTS:
        raise Rejected("unknown upload slot")
    if len(data) > MAX_BYTES:
        raise Rejected(f"{len(data)//1024//1024} MB is too large. "
                       f"Resumes should be well under 1 MB.")
    if not data:
        raise Rejected("empty file")
    dest_rel, label, _, validate, _ = SLOTS[key]
    validate(data)
    dest = ROOT / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    _backup(dest)
    dest.write_bytes(data)
    return f"{label} saved to {dest_rel}"


JOB_PDF = re.compile(r"^\d+$")


def save_custom(job_id: str, data: bytes) -> str:
    """A hand made resume for one job. Beats every automatic mode, forever."""
    if not JOB_PDF.match(str(job_id).strip()):
        raise Rejected("The job id has to be a number, the one shown in the queue.")
    _pdf(data)
    dest = ROOT / "assets" / "custom" / f"{int(job_id)}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _backup(dest)
    dest.write_bytes(data)
    return f"saved as assets/custom/{int(job_id)}.pdf, and it now wins over " \
           f"every automatic choice for that job"


def retire(rel_path: str) -> str:
    """Move a file to backups rather than deleting it. Same effect for the
    system, recoverable for you."""
    p = ROOT / rel_path
    allowed = p.is_relative_to(ROOT / "assets") or p.is_relative_to(ROOT / "secrets")
    if not allowed or not p.is_file():
        raise Rejected("not a file this page manages")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(str(p), BACKUPS / f"{p.name}.{stamp}")
    return f"{rel_path} moved to data/backups. Nothing was deleted."


def slot_state() -> list[dict]:
    out = []
    for key, (rel, label, accept, _, desc) in SLOTS.items():
        p = ROOT / rel
        out.append({
            "key": key, "path": rel, "label": label, "accept": accept,
            "desc": desc, "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
            "when": (datetime.datetime.fromtimestamp(p.stat().st_mtime)
                     .strftime("%d %b %H:%M") if p.exists() else ""),
        })
    return out


def custom_resumes() -> list[dict]:
    d = ROOT / "assets" / "custom"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.pdf")):
        out.append({"job_id": p.stem, "path": f"assets/custom/{p.name}",
                    "size": p.stat().st_size,
                    "when": datetime.datetime.fromtimestamp(p.stat().st_mtime)
                    .strftime("%d %b %H:%M")})
    return out
