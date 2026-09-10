#!/usr/bin/env python3
"""Refuse to publish anything with your details in it.

    python scripts/preflight.py            check the working tree
    python scripts/preflight.py --staged   check only what git is about to commit

Installed as a pre-commit hook by scripts/install-hook.sh, so the check runs
whether or not you remember it.

The point is not to be clever. It is to catch the four things that actually get
leaked from a project like this: a token, an OAuth client, a resume PDF, and a
profile.yaml with your phone number in it.
"""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# files that must never be tracked, whatever they contain
FORBIDDEN = [
    ("secrets/", "OAuth client and tokens"),
    ("profile.yaml", "your name, email, phone, marks"),
    ("resume.yaml", "your whole resume bank"),
    ("data/", "your job database, drafts and screenshots"),
    (".pdf", "a resume"),
    (".env", "generated secrets. env files are never committed"),
]

# content that should never appear in a tracked file
PATTERNS = [
    (re.compile(r"\b[\w.+-]+@(?:gmail|outlook|yahoo|hotmail|protonmail)\.com\b", re.I),
     "a personal email address"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),
     "what looks like an Indian mobile number"),
    (re.compile(r"[\"']client_secret[\"']\s*:\s*[\"'][\w-]{8,}[\"']", re.I),
     "an OAuth client secret"),
    (re.compile(r"[\"']refresh_token[\"']\s*:\s*[\"'][\w./+-]{20,}[\"']", re.I),
     "a refresh token"),
    (re.compile(r"\b1//[\w./-]{25,}"), "a Google refresh token"),
    (re.compile(r"[\"']client_id[\"']\s*:\s*[\"']\d+-[\w-]+\.apps\.googleusercontent",
                re.I), "a Google OAuth client id"),
    (re.compile(r"ya29\.[\w.-]{20,}"), "a Google access token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS key"),
    # a generated secret in an env file looks like none of the branded shapes
    # above. Catch the shape instead: a secret-ish name with a real value.
    (re.compile(r"^[A-Z][A-Z0-9_]*(SECRET|TOKEN|API_?KEY|PASSWORD|PASSWD)"
                r"[A-Z0-9_]*\s*=\s*\S{8,}", re.M), "a secret in an env file"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "an API key"),
]

# files allowed to mention these shapes, because explaining them is their job
ALLOW = {"scripts/preflight.py", "README.md", "CONTRIBUTING.md",
         "profile.example.yaml", "resume.example.yaml", "EXPORT_PROMPT.md",
         "web/gmail_oauth.py", "web/files.py", "tests/test_all.py"}

TEXT = {".py", ".yaml", ".yml", ".md", ".txt", ".json", ".toml", ".cfg",
        ".j2", ".sql", ".sh", ".html", ".css", ".js"}


def tracked(staged: bool) -> list[str]:
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
           if staged else ["git", "ls-files"])
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [l for l in out.stdout.splitlines() if l.strip()]


def main() -> int:
    staged = "--staged" in sys.argv
    paths = tracked(staged)
    if not paths:
        print("  nothing to check"
              + (" (no staged changes)" if staged else " (not a git repo yet)"))
        return 0

    problems: list[str] = []

    for p in paths:
        for frag, why in FORBIDDEN:
            hit = p.endswith(frag) if frag.startswith(".") else p.startswith(frag) or p == frag
            if hit:
                problems.append(f"  {p}\n      is tracked, and it holds {why}.\n"
                                f"      git rm --cached {p}")

    for p in paths:
        if p in ALLOW or Path(p).suffix not in TEXT:
            continue
        f = ROOT / p
        if not f.exists():
            continue
        try:
            body = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for rx, why in PATTERNS:
            m = rx.search(body)
            if m:
                line = body[:m.start()].count("\n") + 1
                problems.append(f"  {p}:{line}\n      contains {why}: "
                                f"{m.group()[:40]}")

    if problems:
        print(f"\n  {len(problems)} thing(s) to fix before this is public:\n")
        print("\n\n".join(problems))
        print("\n  Your details belong in profile.yaml and secrets/, both of which")
        print("  are gitignored. The templates are profile.example.yaml and")
        print("  resume.example.yaml.\n")
        return 1

    print(f"  checked {len(paths)} tracked files, nothing personal found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
