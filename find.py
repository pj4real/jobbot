#!/usr/bin/env python3
"""The finder. Turns your inbox into a ranked queue of real openings.

    python find.py run                 collect + extract + score, the daily command
    python find.py collect             pull new items only
    python find.py extract             parse pending items only
    python find.py list                show the queue
    python find.py list --all          include low scoring and already applied
    python find.py show <id>           one job in full
    python find.py open <id>           open its apply link in your browser
    python find.py rescore             rerun scoring after editing config.yaml
    python find.py unparsed            items nothing could parse, for adding regexes
"""
from __future__ import annotations
import sys
import argparse
import json
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import db                                          # noqa: E402
from core.config import config                               # noqa: E402


def _rows(sql, args=()):
    with db.tx() as c:
        return [dict(r) for r in c.execute(sql, args)]


def cmd_collect(a):
    from core.config import config as cfg
    total = 0
    if cfg()["sources"]["gmail"].get("enabled"):
        from collectors import gmail
        mode = "whole mailbox" if cfg()["sources"]["gmail"].get("scan_all", True) \
            else "label only"
        print(f"gmail ({mode})...")
        s = gmail.collect()
        print(f"  matched {s.get('seen', 0)}  ->  kept {s.get('new', 0)}, "
              f"already had {s.get('duplicate', 0)}")
        if s.get("not_job") or s.get("muted"):
            print(f"  filtered out {s.get('not_job', 0)} not-a-job"
                  + (f", {s['muted']} from muted senders" if s.get("muted") else ""))
        if s.get("llm_calls"):
            print(f"  asked the model about {s.get('unsure', 0)} unclear ones "
                  f"in {s['llm_calls']} call(s)")
        total += s.get("new", 0)
    wa = cfg()["sources"].get("whatsapp", {})
    if wa.get("enabled"):
        from collectors import whatsapp
        print("whatsapp...")
        s = whatsapp.collect()
        print(f"  seen {s['seen']}, new {s['new']}, duplicate {s['duplicate']}")
        total += s["new"]
    if total == 0:
        print("  nothing new")
    return 0


def cmd_extract(a):
    from core import extract
    s = extract.run(use_llm=not a.no_llm)
    if not s["pending"]:
        print("  nothing pending")
        return 0
    print(f"  pending {s['pending']}  ->  new jobs {s['new_jobs']}, "
          f"duplicates {s['duplicates']}, unparsed {s['unparsed']}")
    print(f"  by regex {s.get('by_regex',0)}, by llm {s.get('by_llm',0)}")
    if s["unparsed"]:
        print("  see: python find.py unparsed")
    return 0


def cmd_run(a):
    cmd_collect(a)
    cmd_extract(a)
    print()
    return cmd_list(a)


def cmd_list(a):
    floor = 0.0 if a.all else float(config()["scoring"].get("min_fit_to_draft", 0.55))
    rows = _rows(
        "SELECT j.id, j.title, j.fit_score, j.location, j.apply_kind, j.deadline,"
        "       co.name company, ap.status"
        "  FROM jobs j"
        "  LEFT JOIN companies co ON co.id = j.company_id"
        "  LEFT JOIN applications ap ON ap.job_id = j.id"
        " WHERE j.fit_score >= ?"
        " ORDER BY j.fit_score DESC, j.id DESC LIMIT ?", (floor, a.limit))
    if not rows:
        print("  queue empty. run: python find.py run")
        return 0
    print(f"  {'id':>4}  {'fit':>4}  {'company':<24} {'title':<38} {'where':<14} {'apply':<12} status")
    print("  " + "-" * 112)
    for r in rows:
        print(f"  {r['id']:>4}  {r['fit_score'] or 0:>4.2f}  "
              f"{(r['company'] or '?')[:23]:<24} {(r['title'] or '')[:37]:<38} "
              f"{(r['location'] or '-')[:13]:<14} {(r['apply_kind'] or '-')[:11]:<12} "
              f"{r['status'] or ''}")
    print(f"\n  {len(rows)} shown. python find.py show <id> for detail.")
    return 0


def cmd_show(a):
    rows = _rows(
        "SELECT j.*, co.name company FROM jobs j"
        " LEFT JOIN companies co ON co.id=j.company_id WHERE j.id=?", (a.id,))
    if not rows:
        print(f"  no job {a.id}")
        return 1
    j = rows[0]
    print(f"\n  {j['title']}")
    print(f"  {j['company']}   {j['location'] or 'location unknown'}")
    print(f"\n  fit       {j['fit_score']}  ({j['fit_reason']})")
    print(f"  skills    {', '.join(json.loads(j['skills_json'] or '[]')) or '-'}")
    print(f"  apply     {j['apply_kind']}  {j['apply_url'] or j['apply_email'] or '-'}")
    print(f"  deadline  {j['deadline'] or '-'}")
    con = _rows("SELECT email, verified FROM contacts WHERE company_id=?", (j["company_id"],))
    if con:
        print(f"  contacts  " + ", ".join(
            f"{c['email']}{'' if c['verified'] else ' (unverified)'}" for c in con))
    desc = (j["description"] or "").strip()
    if desc:
        print("\n  " + "-" * 60)
        print("  " + desc[:1500].replace("\n", "\n  "))
    if j["apply_url"]:
        print(f"\n  fill it:  python fill.py \"{j['apply_url']}\"")
    return 0


def cmd_open(a):
    rows = _rows("SELECT apply_url FROM jobs WHERE id=?", (a.id,))
    if not rows or not rows[0]["apply_url"]:
        print("  no apply url for that job")
        return 1
    webbrowser.open(rows[0]["apply_url"])
    return 0


def cmd_rescore(a):
    from core.score import rescore_all
    print(f"  rescored {rescore_all()} jobs")
    return cmd_list(a)


def cmd_unparsed(a):
    rows = _rows("SELECT id, subject, sender FROM raw_items"
                 " WHERE processed=1 AND id NOT IN (SELECT raw_item_id FROM jobs"
                 " WHERE raw_item_id IS NOT NULL) ORDER BY id DESC LIMIT ?", (a.limit,))
    if not rows:
        print("  everything parsed")
        return 0
    print(f"  {len(rows)} items produced no job. Most are genuinely not postings.")
    print("  If you see real ones here, add a pattern to core/extract.py.\n")
    for r in rows:
        print(f"  {r['id']:>5}  {(r['sender'] or '')[:34]:<36} {(r['subject'] or '')[:60]}")
    return 0


COMMANDS = {"run": cmd_run, "collect": cmd_collect, "extract": cmd_extract,
            "list": cmd_list, "show": cmd_show, "open": cmd_open,
            "rescore": cmd_rescore, "unparsed": cmd_unparsed}


def main():
    p = argparse.ArgumentParser(prog="find.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=sorted(COMMANDS))
    p.add_argument("id", nargs="?", type=int)
    p.add_argument("--all", action="store_true", help="include low scoring jobs")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--no-llm", action="store_true", help="regex only, no model calls")
    a = p.parse_args()
    if a.command in ("show", "open") and a.id is None:
        p.error(f"{a.command} needs a job id")
    sys.exit(COMMANDS[a.command](a) or 0)


if __name__ == "__main__":
    main()
