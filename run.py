#!/usr/bin/env python3
"""jobbot. Three tools that share a folder.

  find.py    openings   ->  ranked queue      (safe, run it on a schedule)
  fill.py    a url      ->  filled form       (safe, never submits)
  mail.py    a job      ->  outreach          (gated, capped, dry run first)

This file holds the setup and housekeeping commands that do not belong to any
one of them.

    python run.py init        create the database and folders
    python run.py doctor      check everything before you trust it
    python run.py web         the review dashboard on localhost:8000
    python run.py status      counts per stage
    python run.py track       check for replies to what you sent
    python run.py report      your actual numbers
    python run.py resume      tailor a resume against a jd file
    python run.py schedule    install the daily launchd job
"""
from __future__ import annotations
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DIRS = ("data/resumes", "data/screenshots", "data/browser", "data/jd",
        "secrets", "assets/custom", "templates")


TEMPLATES = [("profile.example.yaml", "profile.yaml"),
             ("resume.example.yaml", "resume.yaml")]


def cmd_init(args):
    import shutil
    from core import db

    # your details live in files that are gitignored, so a fresh clone has
    # none of them. Copy the templates rather than making people find out
    # from a stack trace.
    made = []
    for src, dst in TEMPLATES:
        s, d = ROOT / src, ROOT / dst
        if s.exists() and not d.exists():
            shutil.copy(s, d)
            made.append(dst)

    db.init()
    for d in DIRS:
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    print(f"  database  {db.DB_PATH}")
    print(f"  folders   {len(DIRS)} ready")
    for m in made:
        print(f"  created   {m}  (from the template, yours to fill in)")
    print("\n  next: python run.py doctor")


def cmd_doctor(args):
    from core.config import config, profile, flat_profile, path
    from core import db, llm
    ok = True
    warn = []

    def check(label, good, hint="", fatal=True):
        nonlocal ok
        mark = "ok" if good else ("XX" if fatal else "--")
        print(f"  [{mark}] {label}")
        if not good:
            if fatal:
                ok = False
            else:
                warn.append(label)
            if hint:
                print(f"        {hint}")

    print("\nenvironment")
    check(f"python {sys.version.split()[0]} >= 3.10", sys.version_info >= (3, 10))
    for mod, hint, fatal in [
        ("yaml", "pip install pyyaml", True),
        ("playwright", "pip install playwright && playwright install chromium", True),
        ("fastapi", "pip install fastapi uvicorn jinja2", False),
        ("googleapiclient", "pip install google-api-python-client", False),
        ("google_auth_oauthlib", "pip install google-auth-oauthlib", False),
        ("jinja2", "pip install jinja2", False),
    ]:
        try:
            __import__(mod)
            check(f"import {mod}", True)
        except ImportError:
            check(f"import {mod}", False, hint, fatal)

    print("\nconfig")
    check("config.yaml loads", bool(config()))
    check("profile.yaml loads", bool(profile()))
    OPTIONAL = {"gender", "portfolio"}
    blanks = [k for k, v in flat_profile().items() if not v and k not in OPTIONAL]
    check("no blank required profile fields", not blanks,
          f"blank: {', '.join(blanks)}" if blanks else "")

    ev = profile().get("evidence") or []
    fills = [e["id"] for e in ev if "FILL" in (e.get("line") or "")]
    check(f"evidence lines usable ({len(ev)-len(fills)}/{len(ev)})", not fills,
          f"still placeholder: {', '.join(fills)}" if fills else "", fatal=False)

    print("\nfiles")
    rdefault = path("assets", "resume_default.pdf")
    check("assets/resume_default.pdf", rdefault.exists(),
          "put the resume you actually send there")
    tables = db.table_names()   # this also repairs an empty file, so ask after
    want = {"jobs", "raw_items", "applications", "drafts", "contacts",
            "companies", "resumes", "form_maps", "events", "llm_cache",
            "send_log", "sources"}
    missing = sorted(want - set(tables))
    check(f"database schema ({len(tables)} tables)", not missing,
          f"missing: {', '.join(missing)}. run: python run.py init" if missing else "")
    if db.schema_created and not missing:
        # check() only prints hints on failure, and this one belongs on a pass
        print("        the file was there but empty, so the schema has just been")
        print("        built. That is what an interrupted first init leaves behind.")

    print("\ngmail")
    creds = path("secrets", "credentials.json")
    check("secrets/credentials.json", creds.exists(),
          "console.cloud.google.com, enable Gmail API, OAuth client of type "
          "Desktop app, download the json. See README.", fatal=False)
    check("read token (after first collect)", path("secrets", "token.json").exists(),
          "run: python find.py collect", fatal=False)
    check("send token (after first send)", path("secrets", "token_send.json").exists(),
          "created the first time you send", fatal=False)

    print("\nllm")
    check(f"claude cli on PATH ({llm.CLI})", llm.available(),
          "install Claude Code, or set JOBBOT_CLAUDE_BIN. Everything still "
          "works without it, just with regex only.", fatal=False)
    if llm.available() and not args.quick:
        try:
            out = llm.ask("Reply with exactly: PONG", task="doctor", use_cache=False)
            check("claude -p round trip", "PONG" in out.upper(), out[:110], fatal=False)
        except Exception as e:
            check("claude -p round trip", False, str(e)[:160], fatal=False)

    print("\nlatex (only needed for resume.mode: tailored)")
    from core import render
    eng = render.engine()
    check(f"latex engine ({eng or 'none'})", bool(eng),
          "brew install tectonic", fatal=False)

    print("\nsafety")
    cfg = config()
    check(f"dry_run is {cfg['safety'].get('dry_run')}",
          cfg["safety"].get("dry_run") is True,
          "still true, which is right until you have read ten drafts", fatal=False)
    check("never_auto_submit is true", cfg["safety"].get("never_auto_submit") is True,
          "leave this on")
    check(f"daily cap {cfg['limits']['emails_per_day']} <= 30",
          int(cfg["limits"]["emails_per_day"]) <= 30,
          "higher than 30 from a personal gmail invites trouble")

    print()
    if ok and not warn:
        print("  all good.")
    elif ok:
        print(f"  ready to use. {len(warn)} optional things not set up yet.")
    else:
        print("  fix the XX lines above.")
    return 0 if ok else 1


def cmd_web(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("pip install fastapi uvicorn jinja2")
    print(f"\n  dashboard on http://127.0.0.1:{args.port}")
    if args.reload:
        print("  auto reloading on file changes")
    print()
    uvicorn.run("web.app:app", host="127.0.0.1", port=args.port,
                reload=args.reload, reload_dirs=[str(ROOT)] if args.reload else None,
                log_level="warning")


def cmd_status(args):
    from core import db, llm
    c = db.counts()
    w = max(len(k) for k in c) if c else 10
    for k, v in c.items():
        print(f"  {k.ljust(w)}  {v}")
    s = llm.cache_stats()
    if s:
        print("\n  llm cache (calls you did not have to repeat):")
        for task, n in s.items():
            print(f"    {task}: {n}")


def cmd_track(args):
    from core import track
    s = track.check_replies()
    print(f"  checked {s['checked']} threads, {s['replies']} replies")
    for k in ("positive", "rejected", "auto"):
        if s.get(k):
            print(f"    {k}: {s[k]}")
    due = track.due_followups()
    if due:
        print(f"\n  {len(due)} follow ups due:")
        for d in due:
            print(f"    app {d['id']}  {d['company_name']}  sent {d['submitted_at'][:10]}")
        print("  (follow up sending goes through mail.py, same gate)")


def cmd_report(args):
    from core import track
    for k, v in track.report().items():
        print(f"  {k.ljust(18)}  {v}")


def cmd_resume(args):
    from core import resume, render
    from core.config import config
    if not render.engine():
        print("  no LaTeX engine. brew install tectonic")
        return 1
    if args.jd:
        jd = Path(args.jd).read_text()
    elif not sys.stdin.isatty():
        jd = sys.stdin.read()
    else:
        print("  pass --jd <file> or pipe the posting on stdin")
        return 1
    a = resume.build(jd, args.out or "data/resumes/tailored.pdf",
                     job_title=args.title or "", company=args.company or "")
    print(f"  wrote     {a['path']}  ({a['pages']} page)")
    print(f"  keywords  {', '.join(a['keywords']) or '(none matched)'}")
    print(f"  coverage  {a['coverage']}")
    print(f"  tagline   {a['tagline']}")
    print(f"  bullets   {', '.join(a['bullet_ids'])}")
    floor = config().get("resume", {}).get("min_keyword_coverage", 0.4)
    if a["coverage"] < floor:
        print(f"\n  coverage below {floor}. The bank is missing a bullet for this")
        print("  kind of role, which is a signal to add one, not to reword.")
    return 0


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.jobbot.find</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{root}/find.py</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>{root}/data/find.log</string>
  <key>StandardErrorPath</key><string>{root}/data/find.err</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
"""


def cmd_schedule(args):
    """Write the launchd job. Deliberately only schedules the finder: nothing
    that sends or submits should ever run while you are not looking."""
    target = Path.home() / "Library" / "LaunchAgents" / "local.jobbot.find.plist"
    body = PLIST.format(python=sys.executable, root=ROOT)
    if args.print_only:
        print(body)
        return 0
    if not target.parent.exists():
        print(f"  {target.parent} does not exist. Are you on macOS?")
        return 1
    target.write_text(body)
    print(f"  wrote {target}")
    print("\n  load it:    launchctl load -w " + str(target))
    print("  unload it:  launchctl unload -w " + str(target))
    print("  runs find.py at 09:00 and 19:00. Logs to data/find.log")
    print("\n  Only the finder is scheduled. mail.py and fill.py stay manual,")
    print("  because nothing that sends or submits should run unattended.")
    return 0


COMMANDS = {"init": cmd_init, "doctor": cmd_doctor, "web": cmd_web,
            "status": cmd_status, "track": cmd_track, "report": cmd_report,
            "resume": cmd_resume, "schedule": cmd_schedule}


def main():
    p = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=sorted(COMMANDS))
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true",
                   help="web: restart on file changes. Handy while setting up.")
    p.add_argument("--quick", action="store_true", help="doctor: skip the live llm call")
    p.add_argument("--jd", help="resume: job description file")
    p.add_argument("--out", help="resume: output pdf")
    p.add_argument("--title"), p.add_argument("--company")
    p.add_argument("--print-only", action="store_true", help="schedule: just print the plist")
    a = p.parse_args()
    sys.exit(COMMANDS[a.command](a) or 0)


if __name__ == "__main__":
    main()
