# Installing jobbot

Two audiences, one document. Humans read the top. If you are an AI agent doing
this for someone, skip to **For an agent** at the bottom, which says exactly
what you can and cannot do on their behalf.

Everything runs on one machine. Nothing is hosted, nothing costs money, and no
data leaves the computer except the Gmail API calls you authorise yourself.

---

## Quick version

```
git clone https://github.com/pj4real/jobbot.git
cd jobbot
./scripts/install.sh
source .venv/bin/activate
python run.py web
```

Open <http://127.0.0.1:8000> and work down the **Setup** page.

`install.sh` is safe to rerun. It does everything a script can do and stops at
the three things that need a person.

---

## What you need first

| Thing | Why | If you do not have it |
|---|---|---|
| Python 3.10+ | everything | `brew install python@3.12`, or `apt install python3.12 python3.12-venv` |
| git | cloning | `brew install git`, or `apt install git` |
| ~500 MB free | Chromium is 150 MB of it | |
| A Gmail account | finding openings in your mail | the form filler works without one |

Claude Code is optional. With it, postings that the regex layer cannot parse
get handled by the model. Without it the regex layer still catches most job
alert mail, and `--no-llm` makes that explicit.

LaTeX is optional, and only if you turn on generated resumes. `brew install
tectonic`.

---

## The three things a script cannot do

### 1. A resume PDF

Put the resume you actually send at `assets/resume_default.pdf`, or upload it
on the Setup page. You can add `assets/resume_sde.pdf`, `assets/resume_ml.pdf`
and so on, and it picks by role.

### 2. Your details

Setup → Your details. Phone, college, CGPA, graduation year, and your Class X
and XII percentages, which Indian portals ask for constantly.

Then the **evidence lines**. These matter more than they look: they are the only
claims the drafter is allowed to make about you. It picks two per posting by
matching tags, and it can never write a new one. A line still containing the
word `FILL` is skipped rather than sent, so an unfinished one costs you a
weaker mail, never a false one.

### 3. A Google OAuth client

Only needed for reading your mail and sending outreach. Filling forms does not
need it.

Google moved this under **Google Auth Platform**. Older guides say APIs &
Services → Credentials, which now redirects.

1. <https://console.cloud.google.com> → create a project
2. APIs and Services → Library → search **Gmail API** → Enable
3. Google Auth Platform → Get started. Audience: **External**
4. Google Auth Platform → Audience → Test users → **add your own address**
5. Google Auth Platform → Clients → Create client → **Desktop app**
6. Download the JSON, upload it on the Setup page
7. Setup → Gmail → Connect, twice: once for read, once for send

Two mistakes account for almost every failure here:

**Picking Web application at step 5.** A web client only accepts redirect URIs
registered in advance, so it refuses the loopback redirect the dashboard uses,
and Google's error does not explain that. The upload check catches it.

**Skipping step 4.** Without yourself as a test user you get "Access blocked:
this app has not completed verification" at the consent screen.

Once it works, go to Audience → **Publish app**. While the app sits in Testing
Google expires refresh tokens after seven days and you will be reconnecting
every week. Publishing shows an "unverified app" warning at consent, which is
expected for an app whose only user is you: click Advanced, then continue.

---

## Then

```
python find.py run          find openings in your mail
python find.py resolve      open linkedin postings, find the real apply form
python fill.py "<url>"      fill any application form, you press submit
python mail.py draft        write outreach for jobs with a real address
python run.py doctor        what is still missing
```

All of it is also on the dashboard under Setup → Run, with live output.

---

## When something is wrong

`python run.py doctor` first. It lists what is missing and what each gap costs.

| Symptom | Cause | Fix |
|---|---|---|
| `no such column` | database predates a change | any command repairs it now; `python run.py init` forces it |
| `Executable doesn't exist` | Playwright's library installed, its browser not | `playwright install chromium` |
| `Missing code verifier` | consent link made by an older run of the server | start the connection again from the Setup page |
| `insecure_transport` | you are on a version before that fix | `git pull` |
| `access blocked` at consent | you are not a test user on your own app | Google Auth Platform → Audience → Test users |
| Reconnecting Gmail weekly | app still in Testing | Audience → Publish app |
| `Not filling this one` | the link is a posting page or a digest, not a form | expected. Open it and use the company's own form |
| Nothing to draft | no verified contact addresses | expected if your mail is all aggregator digests |

Logs from a dashboard run are in the page. `data/` holds the database,
screenshots, and saved pages from anything the resolver could not read.

---

## Your data

Gitignored, never committed, and a pre-commit hook refuses them if you try:

```
profile.yaml      your name, email, phone, marks
resume.yaml       your resume bank
secrets/          the OAuth client and your tokens
assets/*.pdf      your resumes
data/             the database, screenshots, browser session
```

`profile.example.yaml` and `resume.example.yaml` are the templates. `run.py
init` copies them for you.

Run `./scripts/install-hook.sh` after cloning. Git does not ship hooks with a
clone, so it is not there until you do.

---

## For an agent

You are installing this for someone. Read this section fully before starting.

### What you can do unattended

```bash
git clone https://github.com/pj4real/jobbot.git && cd jobbot
./scripts/install.sh
```

That script is idempotent and self-checking: python version, venv,
dependencies, Chromium, database, git hook, test suite. It prints `ok` or `XX`
per step. **If every line is `ok`, the automatable half is done.** Do not
reimplement its steps by hand; rerun it if you are unsure.

Then verify:

```bash
source .venv/bin/activate
python run.py doctor --quick
python tests/test_all.py | tail -3
```

`doctor` marks blocking gaps `[XX]`, optional ones `[--]`. The test suite
should end `144 passed, 0 failed` or higher. **A failing suite means do not
continue; report it.**

### What you must not attempt

**The Google OAuth setup.** It requires a browser signed into the user's own
Google account, several console pages, and a consent screen only they can
approve. Do not try to automate it, do not ask them for their password, and do
not ask them to paste a token or client secret into a chat. Point them at
section 3 above and stop.

**Their resume and details.** You cannot invent a phone number, a CGPA, or an
evidence line. An evidence line you fabricate becomes a claim in a real
application to a real company. If the user asks you to write them, ask for the
facts first and write only what they give you.

**Running `mail.py send --live`.** Two switches guard it and both exist on
purpose. Sending is theirs.

### How to report back

Say which of the three manual items remain, and give them this:

```
source .venv/bin/activate
python run.py web
```

then <http://127.0.0.1:8000>, Setup page, top to bottom.

### Facts worth having

- Entry points are `find.py`, `fill.py`, `mail.py`, `run.py`. Each has `--help`.
- Nothing in the project submits a form or sends a mail without an explicit
  human action. Tests assert this. Do not add a code path that does.
- The dashboard binds `127.0.0.1` and has no authentication, by design. Do not
  expose it on a network without reading the deployment notes first.
- `config.yaml` is validated on write and refuses unsafe values: a daily send
  cap above 40, a send gap under 20 seconds, `never_auto_submit` turned off.
- If you change `fill/rules.py`, add a case to `CASES` in `tests/test_all.py`.
