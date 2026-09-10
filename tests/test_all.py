#!/usr/bin/env python3
"""Run from the project root:  python tests/test_all.py

No pytest needed. Every test that matters here is about a guard NOT firing when
it should, so they assert on blocked paths rather than happy paths.
"""
from __future__ import annotations
import os
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0
FAILURES = []

# a fresh clone has no profile.yaml or resume.yaml: they are gitignored. Copy
# the templates so the suite runs on a clone with nothing configured.
import shutil as _sh                                            # noqa: E402
for _src, _dst in (("profile.example.yaml", "profile.yaml"),
                   ("resume.example.yaml", "resume.yaml")):
    if (ROOT / _src).exists() and not (ROOT / _dst).exists():
        _sh.copy(ROOT / _src, ROOT / _dst)
        print(f"(copied {_src} -> {_dst} so the suite has something to read)")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}")
        print(f"  FAIL {name}   {detail}")


def _refuse(fn):
    """True if fn() raises, which for these helpers is the correct behaviour."""
    try:
        fn()
        return False
    except Exception:
        return True


def section(t):
    print(f"\n{t}")
    print("-" * (len(t) + 2))


# a scratch database so tests never touch your real one
TMP = Path(tempfile.mkdtemp(prefix="jobbot-test-"))
os.environ["JOBBOT_TEST"] = "1"
from core import db                                            # noqa: E402
db.DB_PATH = TMP / "jobs.db"
db.init()

from core import extract, score, draft, send, pick             # noqa: E402
from fill import rules                                         # noqa: E402


# ------------------------------------------------------------------ extract
section("extraction")

item = {"subject": "Zeta is hiring: Software Development Engineer Intern",
        "sender": "careers@zeta.tech",
        "body": "Zeta is hiring for Software Development Engineer Intern, Bangalore.\n"
                "Java, SQL, Docker. 2027 graduates. Apply by 25/09/2026.\n"
                "https://jobs.lever.co/zeta/abc-123\n"
                "https://mail.zeta.tech/unsubscribe?x=9"}
p = extract.parse_regex(item)
check("parses a structured alert", p is not None)
check("title clean", p and "Software Development Engineer Intern" in p["title"], p and p["title"])
check("company clean", p and p["company"] == "Zeta", p and p["company"])
check("prefers the ats link over the unsubscribe link",
      p and "lever.co" in p["apply_url"], p and p["apply_url"])
check("classifies the ats", p and p["apply_kind"] == "lever", p and p["apply_kind"])
check("role family", p and p["role_family"] == "sde", p and p["role_family"])

dup = {"subject": "Opening for Software Development Engineer Intern at Zeta",
       "sender": "x@y.com", "body": "Zeta is hiring. Bangalore."}
p2 = extract.parse_regex(dup)
check("same job from a different sender hashes the same",
      p2 and extract.dedupe_hash(p["company"], p["title"], "") ==
      extract.dedupe_hash(p2["company"], p2["title"], ""),
      f"{p2 and p2['company']}/{p2 and p2['title']}")

noise = {"subject": "Your weekly digest", "sender": "news@medium.com",
         "body": "Five stories about engineering you might like."}
check("newsletter produces nothing", extract.parse_regex(noise) is None)

multi = {"subject": "New job: ML Intern at Sarvam AI", "sender": "no-reply@linkedin.com",
         "body": "Sarvam AI is hiring a Machine Learning Intern in Bengaluru.\n"
                 "https://www.linkedin.com/jobs/view/412/\n"
                 "https://jobs.ashbyhq.com/sarvam/ml-intern"}
p3 = extract.parse_regex(multi)
check("company not duplicated across a line break",
      p3 and p3["company"] == "Sarvam AI", p3 and p3["company"])
check("ashby beats linkedin as the apply link",
      p3 and "ashbyhq" in p3["apply_url"], p3 and p3["apply_url"])


# ------------------------------------------------------------------ scoring
section("scoring")

s, why, _ = score.score_job({"title": "Senior Backend Engineer", "description": "5+ years",
                             "role_family": "sde"})
check("senior title scores zero", s == 0.0, f"{s} {why}")

s, why, _ = score.score_job({"title": "Software Engineer Intern",
                             "description": "3+ years of experience required",
                             "role_family": "sde"})
check("3+ years scores near zero", s <= 0.1, f"{s} {why}")

s, why, _ = score.score_job({"title": "SDE Intern", "description": "Python, SQL, Docker. Internship.",
                             "role_family": "sde", "location": "Bangalore",
                             "apply_kind": "greenhouse"})
check("good intern role scores high", s >= 0.75, f"{s} {why}")
check("score carries its own reason", bool(why) and why != "no signals", why)

s, why, _ = score.score_job({"title": "SDE Intern", "description": "Python",
                             "role_family": "sde", "deadline": "01/01/2020"})
check("expired deadline scores zero", s == 0.0, f"{s} {why}")

check("deadline parser handles dd/mm/yyyy", str(score.parse_deadline("25/09/2026")) == "2026-09-25")
check("deadline parser handles '30 September 2026'",
      str(score.parse_deadline("30 September 2026")) == "2026-09-30")


# ------------------------------------------------------------------ form rules
section("form field matching")

CASES = [("Full Name *", "full_name"), ("First Name", "first_name"),
         ("Email Address", "email"), ("Mobile Number", "phone"),
         ("CGPA (out of 10)", "cgpa"), ("Class X Percentage", "tenth_percentage"),
         ("12th Marks (%)", "twelfth_percentage"), ("College/University", "college"),
         ("Year of Passing", "grad_year"), ("Upload your Resume", "resume_file"),
         ("Current Company", "__skip__"), ("Father's Name", "__skip__"),
         ("Parent/Guardian Name", "__skip__"), ("Password", "__skip__"),
         ("Cover Letter", "__essay__"), ("Favourite colour", None)]
bad = [(l, rules.match({"label": l}), w) for l, w in CASES if rules.match({"label": l}) != w]
check(f"all {len(CASES)} field patterns", not bad, str(bad[:3]))
check("select option matching, exact",
      rules.choose_option([{"value": "2027", "text": "2027"}], "2027") == "2027")
check("select option matching, yes-ish",
      rules.choose_option([{"value": "y", "text": "Yes"}, {"value": "n", "text": "No"}],
                          "Yes, anywhere in India") == "y")


# ------------------------------------------------------------------ the gate
section("send gate")

base = {"id": 1, "status": "approved", "company_id": 999, "to_email": "a@b.com",
        "verified": 1, "subject": "s", "body": "b", "resume_path": None}


def blocked(app, label):
    try:
        send.check(app)
        return False
    except send.Blocked:
        return True


check("unapproved is blocked", blocked({**base, "status": "needs_review"}, ""))
check("draft status is blocked", blocked({**base, "status": "draft"}, ""))
check("unverified contact is blocked", blocked({**base, "verified": 0}, ""))
check("missing address is blocked", blocked({**base, "to_email": ""}, ""))
check("a clean approved row passes", not blocked(base, ""))

# fill the day's cap and confirm it stops
cap = int(__import__("core.config", fromlist=["config"]).config()["limits"]["emails_per_day"])
with db.tx() as c:
    for i in range(cap):
        c.execute("INSERT INTO send_log (day, to_email) VALUES"
                  " (date('now','localtime'), ?)", (f"x{i}@y.com",))
check(f"daily cap of {cap} blocks the next one", blocked(base, ""))
check("remaining_today reports zero", send.remaining_today() == 0, send.remaining_today())
with db.tx() as c:
    c.execute("DELETE FROM send_log")
check("cap clears when the log clears", not blocked(base, ""))


# ------------------------------------------------------------------ followups
section("follow ups")

from core import track                                          # noqa: E402

with db.tx() as _c:
    _c.execute("INSERT INTO companies (name,norm_name) VALUES ('Acme','acme')")
    _cid = _c.execute("SELECT id FROM companies WHERE norm_name='acme'").fetchone()["id"]
    _c.execute("INSERT INTO jobs (company_id,title,role_family,dedupe_hash,fit_score)"
               " VALUES (?,'SDE Intern','sde','fu_hash',0.9)", (_cid,))
    _jid = _c.execute("SELECT id FROM jobs WHERE dedupe_hash='fu_hash'").fetchone()["id"]
    _c.execute("INSERT INTO contacts (company_id,name,email,source,verified)"
               " VALUES (?,'Dev','hire@acme.com','posting',1)", (_cid,))
    _ct = _c.execute("SELECT id FROM contacts WHERE email='hire@acme.com'").fetchone()["id"]
    _c.execute("INSERT INTO drafts (job_id,contact_id,subject,body) VALUES (?,?,'s','b')",
               (_jid, _ct))
    _d = _c.execute("SELECT id FROM drafts ORDER BY id DESC LIMIT 1").fetchone()["id"]
    _c.execute("INSERT INTO applications (job_id,draft_id,channel,status,thread_id,"
               "submitted_at) VALUES (?,?,'email','submitted','thr_1',"
               "datetime('now','-9 days'))", (_jid, _d))

due = track.due_followups()
check("a mail sent 9 days ago is due a follow up", len(due) == 1, str(len(due)))
fu = track.build_followup(due[0]) if due else {}
check("the follow up subject threads with Re:",
      fu.get("subject", "").startswith("Re:"), fu.get("subject", ""))
check("it greets by name", "Dev" in fu.get("body", ""), fu.get("body", "")[:30])
check("and names the role", "SDE Intern" in fu.get("body", ""))

with db.tx() as _c:
    _c.execute("INSERT INTO events (entity,entity_id,kind) VALUES ('application',?,"
               "'followup')", (due[0]["id"],))
check("once queued it is never due again", not track.due_followups())

with db.tx() as _c:
    _c.execute("UPDATE applications SET submitted_at=datetime('now','-2 days')"
               " WHERE thread_id='thr_1'")
    _c.execute("DELETE FROM events WHERE kind='followup'")
check("a mail sent 2 days ago is not due yet", not track.due_followups())

_fu_app = {"id": 1, "status": "approved", "company_id": _cid, "to_email": "hire@acme.com",
           "verified": 1, "channel": "followup", "thread_id": "thr_1"}
with db.tx() as _c:
    _aid = _c.execute("SELECT id FROM applications WHERE thread_id='thr_1'").fetchone()["id"]
    _c.execute("INSERT INTO send_log (day,to_email,application_id)"
               " VALUES (date('now','localtime'),'hire@acme.com',?)", (_aid,))
_fu_app["id"] = _aid
check("a follow up is not blocked by the company cooldown",
      not blocked(_fu_app, ""))
check("but a fresh cold mail to that company still is",
      blocked({**_fu_app, "channel": "email"}, ""))
check("cooldown is attributed through the application, not the address",
      send.company_on_cooldown(_cid) == 1, send.company_on_cooldown(_cid))
with db.tx() as _c:
    _c.execute("DELETE FROM send_log")

_m = send.compose("a@b.com", "Re: x", "body", None, in_reply_to="<msg-1@mail>")
check("a follow up carries In-Reply-To and References",
      _m["In-Reply-To"] == "<msg-1@mail>" and _m["References"] == "<msg-1@mail>")
_m2 = send.compose("a@b.com", "x", "body", None)
check("a first contact carries neither", not _m2["In-Reply-To"])


# ------------------------------------------------------------------ drafting
section("drafting")

job = {"id": 1, "title": "SDE Intern", "company_name": "Zeta", "role_family": "sde",
       "skills_json": '["python","sql","docker"]', "description": "Python and SQL"}
d = draft.build(job, {"name": "Ananya Rao", "email": "a@b.com"}, use_llm=False)
check("draft has a subject naming the role", "SDE Intern" in d["subject"], d["subject"])
check("draft greets by first name", "Ananya" in d["body"], d["body"][:40])
check("no FILL placeholder reaches the body", "FILL" not in d["body"])
check("no ai filler phrases", not any(
    x in d["body"].lower() for x in ("excited", "passionate", "hope this finds",
                                     "i came across")), d["body"][:80])
d2 = draft.build(job, None, use_llm=False)
check("anonymous greeting when there is no name",
      d2["body"].startswith(("Hi there", "Hello,")), d2["body"][:20])


# ------------------------------------------------------------------ resume pick
section("resume selection")

_real_custom = pick.CUSTOM_DIR
pick.CUSTOM_DIR = TMP / "custom"
pick.CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
(pick.CUSTOM_DIR / "1.pdf").write_bytes(b"%PDF-1.4\n")
r = pick.resolve({"id": 1, "role_family": "sde", "description": "python"})
check("a hand made resume wins over every mode", r["mode"] == "custom", r["mode"])
pick.CUSTOM_DIR = _real_custom
check("the test never wrote into the real assets tree",
      not (ROOT / "assets" / "custom" / "1.pdf").exists())
r = pick.resolve({"id": 424242, "role_family": "sde", "description": "python"})
check("falls back to the configured mode", r["mode"] in ("variant", "fixed", "tailored"),
      r["mode"])
check("reports a missing default rather than attaching silently",
      "missing" in r or Path(r["path"]).exists(), str(r))


# ------------------------------------------------------------------ config
section("safety configuration")

cfg = __import__("core.config", fromlist=["config"]).config()
check("never_auto_submit is on", cfg["safety"]["never_auto_submit"] is True)
check("dry_run defaults on", cfg["safety"]["dry_run"] is True)
check("daily cap is sane", 1 <= int(cfg["limits"]["emails_per_day"]) <= 30,
      cfg["limits"]["emails_per_day"])
check("verified contacts required", cfg["safety"]["require_verified_contact"] is True)

src = (ROOT / "fill" / "filler.py").read_text() + (ROOT / "fill.py").read_text()
check("no submit click anywhere in the filler",
      not any(k in src for k in ('click("button[type=submit', "type=\\\"submit",
                                 '"Submit"', "'Submit'")),
      "found something that looks like a submit click")


# ------------------------------------------------------------------ recovery
section("database recovery")

# the exact shape of the bug that hit in production: an interrupted first init
# leaves the file present with zero tables, and every caller dies on it
import sqlite3                                                 # noqa: E402
EMPTY = TMP / "empty.db"
sqlite3.connect(EMPTY).close()
check("an empty db file has no tables",
      not sqlite3.connect(EMPTY).execute(
          "SELECT name FROM sqlite_master WHERE type='table'").fetchall())

_saved, db.DB_PATH, db._schema_checked = db.DB_PATH, EMPTY, False
try:
    n = len(db.table_names())
    check("connecting to an empty db builds the schema", n >= 12, f"{n} tables")
    check("and it is usable straight away", db.counts()["jobs"] == 0)
finally:
    db.DB_PATH, db._schema_checked = _saved, False

check("doctor can list tables", len(db.table_names()) >= 12, str(len(db.table_names())))


# ------------------------------------------------------------------ uploads
section("file uploads")

from web import files as fl                                    # noqa: E402


def rejects(fn, *a):
    try:
        fn(*a)
        return False
    except fl.Rejected:
        return True


REAL_PDF = b"%PDF-1.4\n" + b"% padding to look like a real document\n" * 30
check("a pdf that is not a pdf is refused",
      rejects(fl._pdf, b"this is plainly not a pdf" * 80))
check("a two byte pdf is refused", rejects(fl._pdf, b"%PDF"))
check("a real pdf passes", not rejects(fl._pdf, REAL_PDF))

import json as _json                                           # noqa: E402
check("a web oauth client is refused with the reason",
      rejects(fl._credentials, _json.dumps({"web": {"client_id": "x"}}).encode()))
check("a credentials file missing client_secret is refused",
      rejects(fl._credentials, _json.dumps(
          {"installed": {"client_id": "x", "auth_uri": "a", "token_uri": "t"}}).encode()))
check("non json is refused", rejects(fl._credentials, b"not json at all"))
check("a proper desktop client passes",
      not rejects(fl._credentials, _json.dumps({"installed": {
          "client_id": "1.apps.googleusercontent.com", "client_secret": "s",
          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
          "token_uri": "https://oauth2.googleapis.com/token"}}).encode()))

check("a custom resume needs a numeric job id",
      rejects(fl.save_custom, "not-a-number", REAL_PDF))
check("uploads outside assets and secrets are refused",
      rejects(fl.retire, "../../etc/passwd") or rejects(fl.retire, "core/db.py"))
check("every upload slot names a real destination",
      all(("/" in v[0] and v[0].split("/")[0] in ("assets", "secrets"))
          for v in fl.SLOTS.values()))


# ------------------------------------------------------------------ oauth
section("gmail connection")

from web import gmail_oauth as go                              # noqa: E402

check("read and send are separate scopes",
      go.CONNECTIONS["read"]["scopes"] != go.CONNECTIONS["send"]["scopes"])
check("read is readonly only",
      go.CONNECTIONS["read"]["scopes"] == [
          "https://www.googleapis.com/auth/gmail.readonly"])
check("send is send only",
      go.CONNECTIONS["send"]["scopes"] == [
          "https://www.googleapis.com/auth/gmail.send"])
check("read and send use different token files",
      go.CONNECTIONS["read"]["token"] != go.CONNECTIONS["send"]["token"])
check("a forged callback state is refused",
      _refuse(lambda: go.finish(
          "never-issued", "http://127.0.0.1:8000/gmail/callback?code=x")))
check("an unknown connection name is refused",
      _refuse(lambda: go.start("admin", "http://127.0.0.1:8000/x")))

# oauthlib blocks plain http outright. RFC 8252 allows it on loopback for
# native apps, and Google relies on that, so the block is relaxed there only.
import os as _os                                               # noqa: E402
_os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
for host in ("http://127.0.0.1:8000/gmail/callback",
             "http://localhost:8000/gmail/callback"):
    go._allow_loopback_http(host)
check("loopback http is allowed",
      _os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1")
check("and the scope check is relaxed so google adding openid is not fatal",
      _os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE") == "1")
check("a remote host is still refused",
      _refuse(lambda: go._allow_loopback_http("http://example.com/cb")))
check("https elsewhere is refused too, this is loopback only",
      _refuse(lambda: go._allow_loopback_http("https://jobbot.example.com/cb")))


# ------------------------------------------------------------------ profile
section("profile form")

from web import profileform as pf                              # noqa: E402

src = pf.read()
edited = pf.set_scalar(src, "identity", "phone", "+919876543210")
edited = pf.set_scalar(edited, "education", "tenth_percentage", "94.2")
import yaml as _yaml                                           # noqa: E402
pd = _yaml.safe_load(edited)
check("a form edit sets the value", pd["identity"]["phone"] == "+919876543210")
check("numbers stay strings, not floats",
      isinstance(pd["education"]["tenth_percentage"], str),
      type(pd["education"]["tenth_percentage"]).__name__)
_before = _yaml.safe_load(src)["education"]["cgpa"]
check("untouched fields survive", pd["education"]["cgpa"] == _before,
      f"{_before} -> {pd['education']['cgpa']}")
check("the file does not get shorter",
      len(edited.split("\n")) >= len(src.split("\n")) - 1)
check("an unknown field is refused rather than appended",
      _refuse(lambda: pf.set_scalar(src, "identity", "not_a_field", "x")))

ev_edited = pf.set_evidence(src, "internship", "A real sentence about real work.")
ed = _yaml.safe_load(ev_edited)
evs = {e["id"]: e["line"] for e in ed["evidence"]}
check("an evidence line can be replaced",
      "A real sentence" in evs["internship"], evs["internship"][:40])
_src_evs = {e["id"]: e["line"] for e in _yaml.safe_load(src)["evidence"]}
_others = [i for i in _src_evs if i != "internship"]
check("replacing one evidence line leaves the others",
      all(evs[i] == _src_evs[i] for i in _others), f"{len(_others)} others")
check("evidence ids all survive the edit",
      len(ed["evidence"]) == len(_yaml.safe_load(src)["evidence"]))
check("an unknown evidence id is refused",
      _refuse(lambda: pf.set_evidence(src, "no_such_id", "x")))


# ------------------------------------------------------------------ sharing
section("safe to publish")

import importlib.util as _ilu                                   # noqa: E402
_spec = _ilu.spec_from_file_location("pf", ROOT / "scripts" / "preflight.py")
pf = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(pf)


def flagged(text):
    return next((why for rx, why in pf.PATTERNS if rx.search(text)), None)


LEAKS = [
    ('SEARXNG_SECRET=8f3a91c04be27d5619ab', "a generated env secret"),
    ('N8N_RUNNERS_AUTH_TOKEN=abc123def456ghi789', "an env auth token"),
    ('SANDBOX_API_KEY=pk_live_9f8e7d6c5b4a', "an env api key"),
    ('{"installed":{"client_secret":"GOCSPX-abc123def456"}}', "client secret"),
    ('{"refresh_token":"1//0gABCdefGHIjklMNOpqrSTUvwxYZ01234"}', "refresh token"),
    ('ya29.a0AfH6SMBxxxxxxxxxxxxxxxxxxxxxxxxxx', "access token"),
    ('someone@gmail.com', "personal email"),
    ('+91 98765 43210', "phone, spaced"),
    ('+919876543210', "phone, joined"),
    ('9876543210', "phone, bare"),
    ('sk-abcdefghijklmnopqrstuvwxyz123', "api key"),
    ('-----BEGIN RSA PRIVATE KEY-----', "private key"),
]
missed = [d for s, d in LEAKS if not flagged(s)]
check(f"the leak check catches all {len(LEAKS)} shapes", not missed, str(missed))

CLEAN = [
    ('creds.refresh_token = None', "attribute access"),
    ('if not flow.credentials.refresh_token:', "attribute access"),
    ('cgpa 9.11', "a gpa"),
    ('order id 1234512345', "a long number"),
    ('+91 00000 00000', "an obvious placeholder"),
    ('graduating 2027', "a year"),
    ('N8N_VERSION=1.115.2', "a version pin"),
    ('MODE=regular', "an ordinary setting"),
    ('# SECRET_KEY= not set', "a commented out blank"),
]
false = [d for s, d in CLEAN if flagged(s)]
check("and does not flag ordinary code and text", not false, str(false))

check(".env files are refused outright",
      any(f == ".env" for f, _ in pf.FORBIDDEN))
for name in ("profile.yaml", "resume.yaml", "secrets/", "data/", ".env"):
    check(f"{name} is gitignored",
          name.rstrip("/") in (ROOT / ".gitignore").read_text())
check("pdfs are gitignored", "*.pdf" in (ROOT / ".gitignore").read_text())
check("the templates are not gitignored",
      "profile.example.yaml" not in (ROOT / ".gitignore").read_text())
check("both templates exist",
      (ROOT / "profile.example.yaml").exists() and (ROOT / "resume.example.yaml").exists())

_ex = _yaml.safe_load((ROOT / "profile.example.yaml").read_text())
_filled = [f"{s}.{k}" for s in ("identity", "education")
           for k, v in (_ex.get(s) or {}).items()
           if v and k not in ("country",)]
check("the example profile carries nobody's details", not _filled, str(_filled))
check("the example still has the sections the code reads",
      all(s in _ex for s in ("identity", "education", "logistics", "evidence")))


# ------------------------------------------------------------------ dashboard
section("dashboard guards")

from web import editor as ed                                   # noqa: E402

cfg_text = (ROOT / "config.yaml").read_text()

ok, err = ed.validate("config", cfg_text.replace("emails_per_day: 20",
                                                 "emails_per_day: 500"))
check("editor refuses a 500 a day cap", not ok, err[:60])

ok, err = ed.validate("config", cfg_text.replace("never_auto_submit: true",
                                                 "never_auto_submit: false"))
check("editor refuses turning off never_auto_submit", not ok, err[:60])

ok, err = ed.validate("config", cfg_text.replace("min_gap_seconds: 90",
                                                 "min_gap_seconds: 1"))
check("editor refuses a 1 second send gap", not ok, err[:60])

ok, err = ed.validate("config", "limits:\n  a: [1,2\nsafety: {")
check("editor refuses broken yaml", not ok, err[:40])

ok, err = ed.validate("config", "limits:\n  emails_per_day: 20\n")
check("editor refuses a config missing sections", not ok, err[:50])

ok, err = ed.validate("config", cfg_text)
check("editor accepts the real config", ok, err)

ok, err = ed.validate("rules", "def match(f):\n    return None\n")
check("editor refuses rules.py without RULES", not ok, err[:50])

ok, err = ed.validate("rules", (ROOT / "fill" / "rules.py").read_text())
check("editor accepts the real rules.py", ok, err)

ok, err = ed.validate("profile", "identity:\n  full_name: x\n")
check("editor refuses a profile with no education", not ok, err[:40])

check("only four files are editable from the browser",
      set(ed.EDITABLE) == {"config", "profile", "resume", "rules"},
      str(set(ed.EDITABLE)))

from web import runner as rn                                   # noqa: E402
check("every dashboard button maps to a real cli command",
      all(isinstance(v[0], list) and v[0][0].endswith((".py",))
          for v in rn.JOBS.values()))
check("send_live is the only job flagged as world-changing",
      [k for k, v in rn.JOBS.items() if v[2]] == ["send_live"],
      str([k for k, v in rn.JOBS.items() if v[2]]))

app_src = (ROOT / "web" / "app.py").read_text()
check("the dashboard never sends by itself",
      "gmail" not in app_src.lower().replace("gmail.readonly", "")
      .replace("gmail.send", "") or "messages().send" not in app_src,
      "found a send call in the web layer")
check("the dashboard shells out rather than reimplementing",
      "runner.start" in app_src and "send_one" not in app_src)

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'=' * 52}")
print(f"  {PASS} passed, {FAIL} failed")
if FAILURES:
    print("\n  failures:")
    for f in FAILURES:
        print(f"    {f}")
print("=" * 52)
sys.exit(1 if FAIL else 0)
