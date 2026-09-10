#!/usr/bin/env python3
"""Fill any application form and hand it back to you.

    python fill.py <url>
    python fill.py <url> --resume assets/resume_ml.pdf
    python fill.py <url> --dump          just list the fields, fill nothing

Standalone. No database, no LLM, no job row. If you delete the rest of this
project, this still works.

It opens a real Chromium window you can watch, using a persistent profile in
data/browser/, so logins to portals survive between runs. It fills every field
it recognises, screenshots the page, and waits. You review, fix what it
flagged, and press submit yourself. It never clicks submit.
"""
from __future__ import annotations
import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import flat_profile, profile, path          # noqa: E402
from fill.filler import fill_page, screenshot                # noqa: E402

PROFILE_DIR = path("data", "browser")
SHOTS = path("data", "screenshots")


def resolve_resume(arg: str | None) -> str | None:
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = path(arg)
        if not p.exists():
            sys.exit(f"resume not found: {p}")
        return str(p)
    default = path("assets", "resume_default.pdf")
    if default.exists():
        return str(default)
    print("  ! no assets/resume_default.pdf, file uploads will be skipped")
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--resume", help="pdf to upload, defaults to assets/resume_default.pdf")
    ap.add_argument("--dump", action="store_true", help="list detected fields, fill nothing")
    ap.add_argument("--headless", action="store_true",
                    help="no window. fine for --dump; you cannot review a form you cannot see")
    ap.add_argument("--wait", type=int, default=4000, help="ms to settle after load")
    ap.add_argument("--release-file",
                    help="stay open until this file appears. The dashboard uses "
                         "this so the browser waits for you rather than for a tty")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("pip install playwright && playwright install chromium")

    resume = None if args.dump else resolve_resume(args.resume)
    flat = flat_profile()
    bank = profile().get("answer_bank", {}) or {}
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=args.headless,
            viewport={"width": 1380, "height": 940},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print(f"\n  opening {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  ! navigation: {str(e).splitlines()[0][:140]}")
        page.wait_for_timeout(args.wait)

        if args.dump:
            from fill.harvest import harvest, fingerprint
            from fill import rules
            fields = harvest(page)
            print(f"\n  {len(fields)} fields, fingerprint {fingerprint(fields)}\n")
            for f in fields:
                key = rules.match(f) or "-"
                req = "*" if f.get("required") else " "
                lbl = (f.get("label") or f.get("name") or f.get("selector"))[:50]
                print(f"  {req} {f['type']:<10} {lbl:<52} -> {key}")
            ctx.close()
            return

        print("\n  filling:\n")
        res = fill_page(page, flat, bank, resume)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shot = screenshot(page, SHOTS / f"{stamp}.png")

        print(f"\n  {res.summary()}")
        if res.needs_human:
            print("\n  needs you:")
            for label, why in res.needs_human:
                print(f"    - {label[:56]:<58} {why}")
        if res.errors:
            print("\n  errors:")
            for label, why in res.errors:
                print(f"    - {label[:56]:<58} {why}")
        if res.unmapped:
            print(f"\n  {len(res.unmapped)} unrecognised fields:")
            for f in res.unmapped[:12]:
                print(f"    - {f['type']:<10} {(f.get('label') or f.get('name'))[:60]}")
            print("    add a pattern to fill/rules.py for any that repeat")

        print(f"\n  screenshot {shot}")
        print("\n  Review the page, fix anything above, then submit it yourself.")
        print("  This script will never click submit.")
        if args.release_file:
            rel = Path(args.release_file)
            print("\n  browser is open. Click Done in the dashboard when you are"
                  " finished.", flush=True)
            try:
                waited = 0
                while not rel.exists() and waited < 3600:
                    page.wait_for_timeout(1000)
                    waited += 1
            except KeyboardInterrupt:
                pass
            rel.unlink(missing_ok=True)
        else:
            try:
                input("\n  press enter here when you are done to close the browser... ")
            except (EOFError, KeyboardInterrupt):
                pass
        ctx.close()


if __name__ == "__main__":
    main()
