"""Editing the config files from the browser, safely.

Two rules make this not-frightening:
  1. Nothing is written unless it parses. A broken config.yaml would take down
     every tool at once, and you would find out at the worst moment.
  2. Every save keeps the previous version in data/backups/, timestamped. You
     can always get back to the thing that worked.
"""
from __future__ import annotations
import ast
import shutil
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "data" / "backups"

# the only files the dashboard may touch, with how to validate each
EDITABLE = {
    "config": ("config.yaml", "yaml",
               "Caps, safety switches, scoring, which sources are on."),
    "profile": ("profile.yaml", "yaml",
                "Your details, answer bank, and the evidence lines the drafter may cite."),
    "resume": ("resume.yaml", "yaml",
               "The tagged bullet bank. Only used when resume.mode is tailored."),
    "rules": ("fill/rules.py", "python",
              "The form matcher. Add a line here when a portal field keeps being missed."),
}


def path_for(key: str) -> Path:
    if key not in EDITABLE:
        raise KeyError(key)
    return ROOT / EDITABLE[key][0]


def read(key: str) -> str:
    p = path_for(key)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def validate(key: str, text: str) -> tuple[bool, str]:
    kind = EDITABLE[key][1]
    if kind == "yaml":
        import yaml
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            return False, f"YAML error: {e}"
        if not isinstance(data, dict):
            return False, "The top level has to be a mapping of keys."
        if key == "config":
            return _check_config(data)
        if key == "profile":
            for sec in ("identity", "education"):
                if sec not in data:
                    return False, f"missing the '{sec}' section"
        return True, ""

    # python
    try:
        ast.parse(text)
    except SyntaxError as e:
        return False, f"Syntax error on line {e.lineno}: {e.msg}"
    if key == "rules":
        if "RULES" not in text or "def match" not in text:
            return False, "rules.py must still define RULES and match()."
    return True, ""


def _check_config(d: dict) -> tuple[bool, str]:
    """Guard the values that protect your Gmail account, not just the syntax."""
    for sec in ("limits", "safety", "scoring", "sources"):
        if sec not in d:
            return False, f"missing the '{sec}' section"
    try:
        cap = int(d["limits"]["emails_per_day"])
    except (KeyError, TypeError, ValueError):
        return False, "limits.emails_per_day must be a number"
    if cap > 40:
        return False, (f"emails_per_day of {cap} is refused. Gmail allows 500, but "
                       "volume from a personal address is how accounts get "
                       "restricted, and this is the account your interview "
                       "invites arrive at. Keep it at or below 40.")
    if cap < 1:
        return False, "emails_per_day below 1 means nothing can ever send"
    if d["safety"].get("never_auto_submit") is not True:
        return False, ("never_auto_submit has to stay true. The filler is only "
                       "safe on LinkedIn and Workday because it does not submit.")
    if int(d["limits"].get("min_gap_seconds", 90)) < 20:
        return False, "min_gap_seconds below 20 is a burst, which is what trips filters"
    return True, ""


def save(key: str, text: str) -> tuple[bool, str]:
    ok, err = validate(key, text)
    if not ok:
        return False, err
    p = path_for(key)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if p.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(p, BACKUPS / f"{p.name}.{stamp}")
    p.write_text(text, encoding="utf-8")

    # config and profile are lru_cached, so a save has to clear them or the
    # running dashboard keeps serving the old values
    from core import config as cfgmod
    for fn in (cfgmod.config, cfgmod.profile, cfgmod.resume):
        try:
            fn.cache_clear()
        except AttributeError:
            pass
    return True, f"saved. previous version kept in data/backups/"


def backups(key: str, n: int = 8) -> list[dict]:
    name = path_for(key).name
    if not BACKUPS.exists():
        return []
    out = sorted(BACKUPS.glob(f"{name}.*"), reverse=True)[:n]
    return [{"name": b.name, "stamp": b.name.rsplit(".", 1)[-1],
             "size": b.stat().st_size} for b in out]


def restore(key: str, stamp: str) -> tuple[bool, str]:
    p = path_for(key)
    src = BACKUPS / f"{p.name}.{stamp}"
    if not src.exists():
        return False, "no such backup"
    return save(key, src.read_text(encoding="utf-8"))
