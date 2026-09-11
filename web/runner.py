"""Runs the CLI tools as background jobs and streams their output to the page.

The dashboard never reimplements what the tools do. It shells out to the same
find.py, mail.py and fill.py you would type by hand, so there is exactly one
implementation of every action and exactly one place where the guards live.
If a button here could do something the CLI cannot, that would be a second
code path to the dangerous parts, which is the thing this design avoids.
"""
from __future__ import annotations
import os
import sys
import time
import uuid
import re
import shlex
import threading
import subprocess
from pathlib import Path
from collections import deque

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# name -> (argv, human label, whether it can change the outside world)
# where to send you once a job finishes, and what to call the link. A run that
# changes something you cannot see is the same as a run that did nothing.
LANDING = {
    "find":      ("/?stage=found", "See what it found"),
    "collect":   ("/?stage=found", "See the board"),
    "extract":   ("/?stage=found", "See what it found"),
    "extract_norm": ("/?stage=found", "See what it found"),
    "resolve":   ("/?stage=found", "See the board"),
    "rescore":   ("/?stage=found", "See the board"),
    "draft":     ("/?stage=shortlisted", "Read the drafts"),
    "draft_tpl": ("/?stage=shortlisted", "Read the drafts"),
    "followup":  ("/?stage=waiting", "Read the follow ups"),
    "send_dry":  ("/?stage=shortlisted", "Back to the board"),
    "send_live": ("/?stage=applied", "See what went out"),
    "track":     ("/?stage=replied", "See the replies"),
}

JOBS: dict[str, tuple[list[str], str, bool]] = {
    "find":      (["find.py", "run"],              "Collect and parse new openings", False),
    "collect":   (["find.py", "collect"],          "Pull new mail only", False),
    "extract":   (["find.py", "extract"],          "Parse pending items", False),
    "extract_norm": (["find.py", "extract", "--no-llm"], "Parse, regex only", False),
    "resolve":   (["find.py", "resolve"],          "Open LinkedIn postings, find the real form", False),
    "rescore":   (["find.py", "rescore"],          "Rescore everything", False),
    "draft":     (["mail.py", "draft"],            "Write outreach drafts", False),
    "draft_tpl": (["mail.py", "draft", "--no-llm"], "Draft from template only", False),
    "followup":  (["mail.py", "followup"],         "Queue follow ups that are due", False),
    "send_dry":  (["mail.py", "send"],             "Dry run the approved queue", False),
    "send_live": (["mail.py", "send", "--live"],   "SEND the approved queue", True),
    "track":     (["run.py", "track"],             "Check for replies", False),
    "doctor":    (["run.py", "doctor", "--quick"], "Check the setup", False),
    "tests":     (["tests/test_all.py"],           "Run the test suite", False),
    "status":    (["run.py", "status"],            "Counts per stage", False),
}

_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _pump(run_id: str, proc: subprocess.Popen) -> None:
    run = _runs[run_id]
    try:
        for line in iter(proc.stdout.readline, ""):
            with _lock:
                run["lines"].append(line.rstrip("\n"))
        proc.wait()
    except Exception as e:                      # noqa: BLE001
        with _lock:
            run["lines"].append(f"[runner error] {e}")
    finally:
        with _lock:
            run["code"] = proc.returncode
            run["done"] = True
            run["ended"] = time.time()


def start(job: str, extra: list[str] | None = None) -> str:
    if job not in JOBS:
        raise KeyError(job)
    argv, label, _ = JOBS[job]
    cmd = [PY] + argv + (extra or [])
    run_id = uuid.uuid4().hex[:12]

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, bufsize=1, env=env)

    _runs[run_id] = {
        "id": run_id, "job": job, "label": label,
        "cmd": " ".join(shlex.quote(c) for c in cmd[1:]),
        "lines": deque(maxlen=3000), "done": False, "code": None,
        "started": time.time(), "ended": None, "proc": proc,
    }
    threading.Thread(target=_pump, args=(run_id, proc), daemon=True).start()
    return run_id


def start_fill(url: str, resume: str | None = None) -> tuple[str, Path]:
    """The filler is special: it opens a browser and has to stay open until you
    have looked at it. It waits for a release file, which the dashboard creates
    when you click Done."""
    release = ROOT / "data" / f".release-{uuid.uuid4().hex[:8]}"
    argv = [PY, "fill.py", url, "--release-file", str(release)]
    if resume:
        argv += ["--resume", resume]
    run_id = uuid.uuid4().hex[:12]

    proc = subprocess.Popen(
        argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, bufsize=1,
        env=dict(os.environ, PYTHONUNBUFFERED="1"))

    _runs[run_id] = {
        "id": run_id, "job": "fill", "label": f"Filling {url[:60]}",
        "cmd": f"fill.py {url}", "lines": deque(maxlen=3000), "done": False,
        "code": None, "started": time.time(), "ended": None,
        "proc": proc, "release": str(release),
    }
    threading.Thread(target=_pump, args=(run_id, proc), daemon=True).start()
    return run_id, release


def release(run_id: str) -> bool:
    r = _runs.get(run_id)
    if not r or not r.get("release"):
        return False
    Path(r["release"]).write_text("done")
    return True


def stop(run_id: str) -> bool:
    r = _runs.get(run_id)
    if not r or r["done"]:
        return False
    try:
        r["proc"].terminate()
        return True
    except Exception:                            # noqa: BLE001
        return False


def get(run_id: str) -> dict | None:
    r = _runs.get(run_id)
    if not r:
        return None
    with _lock:
        href, label = LANDING.get(r["job"], ("", ""))
        return {"id": r["id"], "job": r["job"], "label": r["label"], "cmd": r["cmd"],
                "done": r["done"], "code": r["code"], "lines": list(r["lines"]),
                "started": r["started"], "ended": r["ended"],
                "awaiting_release": bool(r.get("release")) and not r["done"],
                "landing": href, "landing_label": label,
                "summary": _summarise(r) if r["done"] else ""}


SUMMARY_LINES = re.compile(
    r"^\s*(\d+\s+drafts? waiting|\d+ would go out|\d+ sent|"
    r"\d+ queued|matched \d+|kept \d+|\d+ passed|pending \d+|"
    r"\d+ now point at|looked at \d+|nothing .*)", re.I)


def _summarise(r: dict) -> str:
    """The one line worth reading out of a run's output."""
    lines = [l.strip() for l in r["lines"] if l.strip()]
    for line in reversed(lines):
        if SUMMARY_LINES.match(line):
            return line
    return lines[-1][:160] if lines else ""


def recent(n: int = 12) -> list[dict]:
    with _lock:
        rs = sorted(_runs.values(), key=lambda r: -r["started"])[:n]
        return [{"id": r["id"], "job": r["job"], "label": r["label"],
                 "done": r["done"], "code": r["code"], "started": r["started"],
                 "lines": len(r["lines"])} for r in rs]


def anything_running() -> bool:
    with _lock:
        return any(not r["done"] for r in _runs.values())
