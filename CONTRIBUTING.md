# Contributing

## Before anything else

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    playwright install chromium
    python run.py init
    ./scripts/install-hook.sh      # blocks commits containing your details
    python tests/test_all.py

That hook is not optional politeness. This project's whole job is handling
someone's name, phone number, marks, resume and Gmail tokens, so the easiest
possible mistake is committing them. The hook catches tokens, OAuth secrets,
personal email addresses, phone numbers, PDFs and a tracked `profile.yaml`.

You can also run it by hand:

    python scripts/preflight.py            # the whole tree
    python scripts/preflight.py --staged   # only what you are about to commit

## What is yours and what is the project's

    profile.yaml   resume.yaml   secrets/   assets/*.pdf   data/

All gitignored. Never remove them from `.gitignore` "just for a second".

    profile.example.yaml   resume.example.yaml

Tracked. If you add a field to your own profile, add it to the example too,
with a blank value and a comment, or the next person gets a KeyError.

## The rules that are not up for negotiation

These exist because the failure modes are a restricted Gmail account or a
LinkedIn ban during someone's placement season.

1. **The filler never submits.** No code path in `fill/` may click a submit
   button. A test greps for it.
2. **Only `status='approved'` is sent.** `core/send.py` is the single door to
   the Gmail API. Nothing else may call it.
3. **`dry_run` defaults to true** and `--live` alone is not enough to override
   it. Two switches, deliberately.
4. **No email address guessing.** Unverified contacts are refused. Bounces cost
   sender reputation and it cannot be bought back.
5. **The daily cap stays at or under 40.** The config editor refuses higher.
6. **Nothing marked FILL is ever rendered** into a mail or a resume. Unfinished
   content gets dropped, never shipped.

If a change would weaken one of these, it needs a good argument in the PR, not
a quiet edit.

## Where things live

    find.py fill.py mail.py run.py     entry points
    core/     config db llm extract score draft send pick resume render track
    fill/     harvest rules filler     the form engine, deterministic first
    collectors/  gmail whatsapp
    web/      app editor files gmail_oauth profileform runner
    tests/test_all.py                  89 checks, no pytest needed

## The one thing worth contributing

`fill/rules.py`. It maps form labels to profile keys, and it is what makes the
filler work on portals nobody has written a scraper for. Every pattern you add
from a real form you met helps everyone. Order matters: specific above general,
because "current company" must be tested before "company" and "parent name"
before "name".

Add a case to `CASES` in `tests/test_all.py` alongside any new rule.

## Style

Plain Python, no framework beyond FastAPI and Playwright. No new dependency
without a reason in the PR. Comments explain why a thing is the way it is, not
what the line does.

## Tests

    python tests/test_all.py

They must pass on a clone with nothing configured. The suite copies the example
templates if your `profile.yaml` is missing, so do not write a test that
depends on your own details being present.
