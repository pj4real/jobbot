# jobbot

Off-campus application pipeline. Local, free, and nothing goes out without you.

Three small programs that share a folder and a SQLite file. They do not import
each other, so any one is useful alone and any one can be thrown away.

    find.py    gmail + a whatsapp channel  ->  ranked queue of real openings
    fill.py    any application url         ->  filled form, parked for you
    mail.py    a job + a verified address  ->  outreach, capped and gated

Plus a dashboard that drives all of it from the browser, so none of this needs
a terminal after setup.

Everything runs on your machine. No hosted service, no recurring cost, no
account beyond Google. LLM calls shell out to the Claude Code CLI if you have
it, and the system works without it on regex alone.

## Setup

    git clone https://github.com/pj4real/jobbot.git && cd jobbot
    ./scripts/install.sh
    source .venv/bin/activate
    python run.py web

Then open http://127.0.0.1:8000 and work down the **setup** page. It lists what
is missing with a fix link on each row.

`install.sh` is safe to rerun and does everything a script can: python check,
venv, dependencies, Chromium, database, git hook, test suite. It stops at the
three things that need a person.

**[INSTALL.md](INSTALL.md) has the detailed version**, including the Google
OAuth steps, a troubleshooting table, and a section written for an AI agent
doing the install on someone's behalf.

`init` copies `profile.example.yaml` to `profile.yaml` for you. That file is
gitignored, along with `resume.yaml`, `secrets/`, `data/` and every PDF, so
nothing personal is ever tracked.

### The short version of what you provide

    a resume PDF          upload it on the files page
    your details          the profile page, it is a form
    google oauth client   only if you want find.py and mail.py

`fill.py` needs none of the Google setup. A resume and your details and it
works, which is most of the daily value.

## Daily use

    python run.py web             http://127.0.0.1:8000

The dashboard drives everything: find, draft, fill a URL, dry run, send, edit
every config file, and the setup checklist. Output streams into the page as
each job runs.

It never reimplements an action. Every button shells out to the same find.py,
mail.py or fill.py you would type by hand, so the guards in core/send.py and
fill/filler.py are the only guards that exist and there is no second path to
anything dangerous. The live send button is refused by the same two switches
as the terminal.

### Pages

    queue         ranked openings, fill or draft each one
    run           every action as a button, with a live log
    review        read and edit drafts, approve or reject
    applications  everything past the review stage
    profile       your details and evidence lines, as a form
    files         upload resumes and the google client file
    gmail         connect read and send access in one click each
    settings      raw editors for config.yaml, profile.yaml, resume.yaml, rules.py
    setup         what is still missing, with a fix link on each row
    stats         reply rate, outcomes, recent activity

Nothing has to be done at a terminal any more. Resumes and the OAuth client
file upload from the files page, Gmail connects from the gmail page, and your
details edit as a form on the profile page.

Uploads are checked by content, not by filename. A .pdf that is not really a
PDF is refused there rather than halfway through a real application, and a Web
application OAuth client is refused with an explanation instead of failing
three steps later.

Editing validates before writing and refuses values that would put your Gmail
account at risk: a cap above 40, a send gap under 20 seconds, never_auto_submit
turned off. Every write keeps the previous version in data/backups, and
removing a file moves it there rather than deleting it.

The profile form edits profile.yaml in place, one line at a time, so the
comments in that file survive. The raw editor is still there if you prefer it.

### Gmail, connected from the browser

Two separate connections, deliberately. Read holds gmail.readonly in
`token.json`, send holds gmail.send in `token_send.json`. A bug in the
collector cannot send anything and a bug in the mailer cannot read your mail.

The consent redirect comes back to the dashboard itself, which a Desktop OAuth
client allows because loopback redirects are exempt from exact matching. Make
sure you create a Desktop app client, not a Web application one.

### Or from the terminal

    python find.py run            collect, parse, score, show the queue

Then per job:

    python fill.py "<apply url>"  fills the form, you press submit

Or where there is an address to write to:

    python mail.py draft          write the outreach
    python mail.py queue          what is waiting
    python mail.py show 3         read one
    python mail.py approve 3      mark it ready
    python mail.py send           dry run, shows what would go
    python mail.py send --live    actually send

`--live` alone is not enough. `config.yaml` also carries `safety.dry_run: true`
and you have to turn that off deliberately. Two switches, on purpose.


## The safety rules, and why each exists

| Rule | Where | Why |
|---|---|---|
| Never clicks submit | `fill/filler.py` | Keeps the same code legal on LinkedIn and survivable on Workday |
| Only `status='approved'` is sent | `core/send.py` | One door for every dangerous action |
| `dry_run` defaults on | `config.yaml` | Read ten drafts before the first one leaves |
| 20 mails a day | `config.yaml` | Gmail allows 500. Volume from a personal address is how accounts get restricted |
| Verified addresses only | `core/send.py` | Bounces cost sender reputation, which you cannot buy back |
| One company per 45 days | `core/send.py` | Applying twice reads worse than not applying |
| Randomized gaps | `core/send.py` | Bursts are what trip filters |
| No FILL text ever renders | `core/draft.py`, `core/resume.py` | A placeholder in a real application is unrecoverable |

`python tests/test_all.py` asserts all of these. 41 checks, no pytest needed.


## When the filler misses a field

Every run prints what it did not recognise. If a label keeps recurring across
portals, add one line to `fill/rules.py`:

    (r"your pattern", "profile_key"),

Order matters, specific above general. That file is the whole brain of the
filler and it is meant to grow as you meet real forms.


## When the finder misses a posting

    python find.py unparsed

Most of what lands there is genuinely not a job. If real postings appear, add a
pattern to `core/extract.py`. With the Claude CLI installed the LLM layer
already catches most of them; `--no-llm` turns it off.


## Resume

Default is `mode: variant`: prebuilt PDFs picked by role family.

    assets/resume_default.pdf     the fallback
    assets/resume_ml.pdf          used when role_family is ml
    assets/resume_sde.pdf         used when role_family is sde
    assets/custom/<job_id>.pdf    hand made, beats everything

When a posting is an outlier, the job is flagged and its description written to
`data/jd/`. Hand that one file to a chat, compile what comes back, drop the PDF
in `assets/custom/`. The mailer picks it up and never overwrites it.

`mode: tailored` turns on the generated path: `resume.yaml` is a tagged bullet
bank and the engine selects and orders from it but never writes new bullets.
Rendering is Jinja LaTeX through tectonic (`brew install tectonic`).


## Scheduling

    python run.py schedule
    launchctl load -w ~/Library/LaunchAgents/local.jobbot.find.plist

Only the finder is scheduled, at 09:00 and 19:00. Nothing that sends or submits
ever runs while you are not watching.


## Layout

    find.py fill.py mail.py run.py      the four entry points
    core/     config db llm extract score draft send pick resume render track
    fill/     harvest rules filler      the form engine, no llm in v1
    collectors/  gmail whatsapp
    web/app.py                          the dashboard
    templates/resume.tex.j2
    tests/test_all.py
    profile.yaml config.yaml resume.yaml
    assets/  data/  secrets/            never committed


## Contributing

See CONTRIBUTING.md. Short version: run `./scripts/install-hook.sh` first, it
blocks commits that contain your details. The most useful thing to contribute
is a pattern in `fill/rules.py` from a real portal you met.

## Not built, deliberately

n8n or any workflow engine. Email address guessing. Workday and LinkedIn
submission. An agent loop as the default form filler. Per-application resume
rewriting.
