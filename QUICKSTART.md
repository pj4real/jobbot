# Get applying today

Three commands and two files. No Google account needed for this part.

## 1. Install

    cd ~/projects/jobbot
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    playwright install chromium

## 2. Two things only you can provide

    assets/resume_default.pdf      whatever resume you use right now
    profile.yaml                   run `python run.py init` to create it,
                                   then fill it in from the profile page

## 3. Open the dashboard

    python run.py web

Everything is there: find openings, fill a form by pasting its URL, read and
approve drafts, edit any config file, and a setup page listing what is still
missing. Output streams into the page.

## 3b. Or from the terminal

Look at a form without touching it:

    python fill.py "https://..." --dump

Fill it:

    python fill.py "https://..."

A Chromium window opens, fields get typed in, the page is screenshotted, and
the script waits. You check it, fix whatever it flagged, press submit yourself.
It never clicks submit.

Different resume for one application:

    python fill.py "https://..." --resume assets/resume_ml.pdf

## When it misses a field

The run prints anything it did not recognise. If the same label keeps showing
up across portals, add one line to `fill/rules.py`:

    (r"your pattern here", "profile_key"),

Order matters, specific above general. Then rerun. That file is the whole
brain of the filler and it is meant to grow as you meet real forms.

## Notes

- Logins persist. The browser profile lives in `data/browser/`, so once you
  sign in to a portal it stays signed in on later runs.
- Open questions like "why do you want to join us" are deliberately left for
  you. A canned answer to that one reads worse than a blank.
- Fields like Current Company and Father's Name are matched and deliberately
  skipped, so they are not silently filled with the wrong thing.
