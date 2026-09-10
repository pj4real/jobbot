"""Deciding whether an email is a job posting, cheaply.

Scanning a whole inbox instead of one label is the difference between finding
the openings a friend forwarded you and finding only the ones a job board sent.
But a mailbox is thousands of messages and most of them are not jobs, so the
naive version either costs a fortune in model calls or drowns you in noise.

Three stages, each only seeing what the one before could not settle:

  1. Gmail's own search        server side, nothing downloaded, free
  2. this module               regex and sender memory, milliseconds, free
  3. one batched model call    only for the genuinely ambiguous middle

Stage 2 is where the leverage is. Every sender you judge once is remembered, so
the second week of scanning is far cheaper than the first.
"""
from __future__ import annotations
import re

from . import db

# ---------------------------------------------------------------- stage 1

# Gmail does the first cut on its servers. Broad on purpose: a false positive
# costs one cheap regex pass, a false negative is an opening you never saw.
QUERY_TERMS = [
    "hiring", "we're hiring", "job", "jobs", "opening", "openings", "vacancy",
    "intern", "internship", "recruitment", "recruiting", "career", "careers",
    "opportunity", "apply now", "application", "shortlisted", "interview",
    "placement", "off-campus", "walk-in", "referral", "SDE", "resume",
]
EXCLUDE = ["in:chats", "in:trash", "in:spam"]


def gmail_query(days: int = 30, extra: str = "") -> str:
    terms = " OR ".join(f'"{t}"' if " " in t else t for t in QUERY_TERMS)
    q = f"newer_than:{days}d ({terms})"
    for e in EXCLUDE:
        q += f" -{e}"
    return f"{q} {extra}".strip()


# ---------------------------------------------------------------- stage 2

STRONG = re.compile(
    r"\b(we\s+are\s+hiring|we're\s+hiring|now\s+hiring|is\s+hiring|"
    r"job\s+(opening|description|posting)|apply\s+(now|here|before)|"
    r"(software|backend|frontend|data|ml|product)\s+(engineer|developer|intern|scientist)|"
    r"sde\s*-?\s*(intern|[i1-3])|internship\s+(opportunity|opening|program)|"
    r"campus\s+(hiring|drive|placement)|walk[\s-]?in\s+drive|"
    r"last\s+date\s+to\s+apply|application\s+deadline)\b", re.I)

APPLY_LINK = re.compile(
    r"(boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com|myworkdayjobs\.com|"
    r"smartrecruiters\.com|workable\.com|zohorecruit|darwinbox|keka\.com|"
    r"instahyre\.com|unstop\.com|hirist|cutshort\.io|wellfound\.com|"
    r"docs\.google\.com/forms|forms\.gle)", re.I)

# things that mention jobs without being one
NEGATIVE = re.compile(
    r"\b(unsubscribe\s+from\s+job\s+alerts|newsletter|digest|webinar|masterclass|"
    r"course|certification\s+program|bootcamp\s+admission|scholarship|"
    r"upgrade\s+your\s+plan|premium\s+membership|black\s+friday|"
    r"order\s+(confirmed|shipped|delivered)|invoice|receipt|payment\s+(received|due)|"
    r"otp|verification\s+code|password\s+reset|security\s+alert|"
    r"your\s+(subscription|statement|bill))\b", re.I)

# a reply about your own application is worth keeping, but it is not a posting
ABOUT_YOUR_APP = re.compile(
    r"\b(thank\s+you\s+for\s+applying|we\s+have\s+received\s+your\s+application|"
    r"your\s+application\s+(has\s+been|was|is)|application\s+received|"
    r"not\s+(moving|proceeding|shortlisted)|regret\s+to\s+inform)\b", re.I)

NOREPLY = re.compile(r"no-?reply|donotreply|notifications?@|mailer|bounce", re.I)

MARKETING_DOMAINS = re.compile(
    r"(medium\.com|substack\.com|quora\.com|pinterest|facebookmail|twitter\.com|"
    r"x\.com|youtube\.com|coursera|udemy|edx\.org|amazon\.(in|com)|swiggy|zomato|"
    r"flipkart|myntra|paytm|phonepe|uber\.com|ola|netflix|spotify)", re.I)


def address_of(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender or "")
    addr = (m.group(1) if m else (sender or "")).strip().lower()
    return addr if "@" in addr else ""


def domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def classify(subject: str, body: str, sender: str) -> tuple[str, float, str]:
    """Returns (verdict, confidence 0..1, reason).

    verdict is one of: job, not_job, unsure. Only 'unsure' costs a model call.
    """
    addr = address_of(sender)
    dom = domain_of(addr)
    text = f"{subject or ''}\n{(body or '')[:4000]}"

    remembered, decided_by = sender_verdict(addr)

    # a judgement you made by hand wins outright. Nothing below may overrule it,
    # because software that quietly ignores what you told it is worse than
    # software that guesses wrong.
    if decided_by == "user":
        if remembered == "not_job":
            return "not_job", 1.0, "you muted this sender"
        if remembered == "job":
            return "job", 1.0, "you marked this sender as one to watch"

    if remembered == "not_job":
        return "not_job", 0.9, "judged not a job sender before"
    if remembered == "job" and (STRONG.search(text) or APPLY_LINK.search(text)):
        return "job", 0.95, "known job sender and it reads like a posting"

    if MARKETING_DOMAINS.search(dom):
        return "not_job", 0.9, f"{dom} does not send job postings"
    if NEGATIVE.search(text) and not APPLY_LINK.search(text):
        return "not_job", 0.8, "reads as marketing or a transactional mail"
    if ABOUT_YOUR_APP.search(text) and not APPLY_LINK.search(text):
        return "not_job", 0.75, "this is about an application, not a new opening"

    score, why = 0.0, []
    if STRONG.search(text):
        score += 0.5
        why.append("says it is hiring")
    if APPLY_LINK.search(text):
        score += 0.35
        why.append("carries an application link")
    if re.search(r"\b(ctc|stipend|lpa|package|salary)\b", text, re.I):
        score += 0.1
        why.append("mentions pay")
    if re.search(r"\b(20\d\d)\s*(batch|graduates?|passouts?)\b", text, re.I):
        score += 0.15
        why.append("names a graduating batch")
    if re.search(r"\b(bangalore|bengaluru|hyderabad|pune|chennai|mumbai|noida|"
                 r"gurgaon|remote|hybrid)\b", text, re.I):
        score += 0.05
    if NOREPLY.search(addr):
        score -= 0.05

    if score >= 0.6:
        return "job", min(score, 0.95), ", ".join(why)
    if score <= 0.15:
        return "not_job", 1 - score, "no hiring language and no application link"
    return "unsure", score, ", ".join(why) or "some signals, not enough"


# ---------------------------------------------------------------- memory

def sender_verdict(addr: str) -> tuple[str | None, str | None]:
    if not addr:
        return None, None
    with db.tx() as c:
        r = c.execute("SELECT verdict, decided_by FROM senders WHERE address=?",
                      (addr,)).fetchone()
        return (r["verdict"], r["decided_by"]) if r else (None, None)


def remember(addr: str, verdict: str, reason: str = "", found: int = 0,
             by: str = "system") -> None:
    """One judgement, kept. This is what makes the second scan cheap.

    `by='user'` marks it as yours, which no heuristic may later overrule. The
    system never downgrades one of your decisions to its own.
    """
    if not addr:
        return
    with db.tx() as c:
        existing = c.execute("SELECT decided_by FROM senders WHERE address=?",
                             (addr,)).fetchone()
        if existing and existing["decided_by"] == "user" and by != "user":
            c.execute("UPDATE senders SET seen=seen+1 WHERE address=?", (addr,))
            return
        c.execute(
            "INSERT INTO senders (address, domain, verdict, reason, jobs_found,"
            "                     decided_by)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(address) DO UPDATE SET"
            "   verdict=excluded.verdict, reason=excluded.reason,"
            "   decided_by=excluded.decided_by,"
            "   seen=senders.seen+1, jobs_found=senders.jobs_found+excluded.jobs_found,"
            "   decided_at=datetime('now')",
            (addr, domain_of(addr), verdict, reason, found, by))


def note_seen(addr: str) -> None:
    if not addr:
        return
    with db.tx() as c:
        c.execute("INSERT INTO senders (address, domain, verdict, reason)"
                  " VALUES (?,?,'unsure','seen but not judged')"
                  " ON CONFLICT(address) DO UPDATE SET seen=senders.seen+1", 
                  (addr, domain_of(addr)))


def sender_stats() -> dict:
    with db.tx() as c:
        rows = {r["verdict"]: r["n"] for r in c.execute(
            "SELECT verdict, COUNT(*) n FROM senders GROUP BY verdict")}
    return {"job": rows.get("job", 0), "not_job": rows.get("not_job", 0),
            "unsure": rows.get("unsure", 0)}


def muted_senders(limit: int = 200) -> list[dict]:
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT address, domain, reason, seen, decided_by, decided_at"
            "  FROM senders WHERE verdict='not_job'"
            " ORDER BY decided_by='user' DESC, seen DESC LIMIT ?", (limit,))]


def mute(addr: str, reason: str = "you muted this sender") -> None:
    remember(addr, "not_job", reason, by="user")


def watch(addr: str, reason: str = "you marked this sender as one to watch") -> None:
    remember(addr, "job", reason, by="user")


def unmute(addr: str) -> None:
    """Back to being judged on its merits, and no longer your decision."""
    with db.tx() as c:
        c.execute("UPDATE senders SET verdict='unsure', decided_by='system',"
                  " reason='you unmuted it, judged on merit now' WHERE address=?",
                  (addr,))


# ---------------------------------------------------------------- stage 3

PROMPT = """Each numbered item below is an email. For each, say whether it is a
JOB POSTING the reader could apply to.

It IS a posting if it announces an opening, a hiring drive, or an internship,
whether from a company, a job board, a college placement cell, or a person
forwarding one.

It is NOT a posting if it is a newsletter, a course or bootcamp advertisement,
a reply about an application the reader already sent, an interview invitation,
a transactional mail, or marketing.

For each item output: {{"id": <number>, "job": true|false, "why": "<six words>"}}

Output a JSON array, same order, nothing else.

ITEMS:
{items}"""


def classify_llm(items: list[dict]) -> dict[int, bool]:
    """Only the ambiguous middle reaches here, batched."""
    from .llm import ask_json, LLMError
    blob = "\n\n".join(
        f"--- {i} ---\nFROM: {(it.get('sender') or '')[:100]}\n"
        f"SUBJECT: {(it.get('subject') or '')[:160]}\n"
        f"{(it.get('body') or '')[:900]}"
        for i, it in enumerate(items))
    try:
        rows = ask_json(PROMPT.format(items=blob), task="triage")
    except LLMError:
        # a model failure must not silently discard mail: keep the uncertain
        # ones rather than dropping them
        return {i: True for i in range(len(items))}
    out = {}
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and "id" in r:
                try:
                    out[int(r["id"])] = bool(r.get("job"))
                except (TypeError, ValueError):
                    pass
    return out
