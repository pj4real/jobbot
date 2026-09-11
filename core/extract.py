"""raw_items -> jobs.

Two layers, in this order and never the other way round:

  v1  regex over the text. Costs nothing, runs in milliseconds, and handles the
      structured alert mails (LinkedIn, Naukri, Instahyre, company newsletters)
      that make up most of a job feed.
  v2  one batched claude call for whatever v1 could not parse. Batched because
      the CLI is slow and your plan usage is the scarce resource here.

Anything still unparsed after both is left in raw_items, not deleted. You can
look at it, add a regex, and rerun.
"""
from __future__ import annotations
import re
import hashlib
import json
from urllib.parse import urlparse

from . import db
from .config import config

# ------------------------------------------------------------------ patterns

ATS_HOSTS = {
    "boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby",
    "docs.google.com": "gform", "forms.gle": "gform",
    "myworkdayjobs.com": "workday", "taleo.net": "taleo",
    "smartrecruiters.com": "smartrecruiters", "workable.com": "workable",
    "zohorecruit.com": "zoho", "keka.com": "keka", "darwinbox.in": "darwinbox",
    "instahyre.com": "instahyre", "naukri.com": "naukri",
    "linkedin.com": "linkedin", "wellfound.com": "wellfound",
    "unstop.com": "unstop", "hirist.tech": "hirist", "cutshort.io": "cutshort",
}

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

TITLE_RE = [
    re.compile(r"(?:hiring|opening|position|role|vacancy)\s*(?:for|:|-)\s*(.{4,70})", re.I),
    re.compile(r"^\s*(?:job\s*title|position|role)\s*[:\-]\s*(.{4,70})$", re.I | re.M),
    re.compile(r"\b((?:senior|junior|lead|associate)?\s*"
               r"(?:software|backend|frontend|full[\s-]?stack|data|ml|machine learning|"
               r"devops|qa|product|research|security)\s*"
               r"(?:development\s*)?(?:engineer|developer|intern|scientist|analyst|manager)"
               r"(?:\s*intern(?:ship)?)?)\b", re.I),
    re.compile(r"\b(SDE\s*-?\s*(?:intern|[I1-3]+)|SWE\s*intern|"
               r"software\s*engineer(?:ing)?\s*intern)\b", re.I),
]

COMPANY_RE = [
    # [ \t]+ not \s+ : a company name never spans a line break, and \s+ made
    # "at Sarvam AI\nSarvam AI is hiring" capture the name twice.
    re.compile(r"\bat[ \t]+([A-Z][\w&.\-]*(?:[ \t]+[A-Z][\w&.\-]*){0,3})"
               r"(?=[ \t]+is[ \t]+hiring|[ \t]*[,.\n]|$)"),
    re.compile(r"^[ \t]*(?:company|organisation|organization)[ \t]*[:\-][ \t]*(.{2,50})$",
               re.I | re.M),
    re.compile(r"\b([A-Z][\w&.\-]*(?:[ \t]+[A-Z][\w&.\-]*){0,2})[ \t]+is[ \t]+"
               r"(?:hiring|looking for)"),
]

LOCATION_RE = re.compile(
    r"\b(remote|work from home|wfh|hybrid|bangalore|bengaluru|hyderabad|pune|chennai|"
    r"mumbai|delhi|gurgaon|gurugram|noida|kolkata|ahmedabad|jaipur|indore|kochi|"
    r"coimbatore|vellore|trivandrum|bhubaneswar|chandigarh)\b", re.I)

DEADLINE_RE = re.compile(
    r"(?:deadline|last date|apply by|closes on|before)\s*[:\-]?\s*"
    r"(\d{1,2}[\s/-][A-Za-z]{3,9}[\s/-]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s*\d{4})", re.I)

ROLE_FAMILY = [
    (re.compile(r"\b(ml|machine learning|deep learning|ai|nlp|computer vision|"
                r"data scien|research (engineer|intern))\b", re.I), "ml"),
    (re.compile(r"\b(data (analyst|engineer)|analytics|business intelligence)\b", re.I), "data"),
    (re.compile(r"\b(product manage|associate product|apm|product intern)\b", re.I), "product"),
    (re.compile(r"\b(sde|swe|software|backend|frontend|full[\s-]?stack|developer|"
                r"engineer)\b", re.I), "sde"),
]

NOISE = re.compile(r"^(re|fwd|fw)\s*:\s*", re.I)


# ------------------------------------------------------------------ helpers

def norm_company(name: str) -> str:
    n = re.sub(r"\b(pvt|private|ltd|limited|inc|llc|llp|technologies|technology|"
               r"labs|solutions|systems|india)\b", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", n)[:40]


def dedupe_hash(company: str, title: str, location: str) -> str:
    key = f"{norm_company(company)}|{re.sub(r'[^a-z0-9]+','',(title or '').lower())[:40]}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def classify_url(url: str) -> str:
    from .urls import canonical
    return canonical(url)[2] or "unknown"


def pick_apply_url(text: str) -> tuple[str, str]:
    """Best link in the mail, cleaned of tracking.

    Ranked by what you can actually do with it: a real form beats a posting
    page beats nothing. Alert digests and search pages are discarded, because
    a first real scan showed most LinkedIn links in an alert mail are exactly
    that and the filler was being launched at them.
    """
    from .urls import canonical

    best, best_kind, best_rank = "", "unknown", -1
    for raw in URL_RE.findall(text):
        low = raw.lower()
        if any(x in low for x in ("unsubscribe", "mailto:", "privacy",
                                  "notification-settings", "twitter.com",
                                  "facebook.com", "instagram.com")):
            continue
        clean, kind, platform = canonical(raw)
        if kind == "noise":
            continue
        rank = {"form": 2, "posting": 1}.get(kind, 0)
        if platform == "unknown" and kind == "form":
            rank = 2                      # a company careers page is worth most
        if rank > best_rank:
            best, best_kind, best_rank = clean, platform, rank
    return best, best_kind


def first(patterns, text: str) -> str:
    for rx in patterns:
        m = rx.search(text)
        if m:
            v = re.sub(r"\s+", " ", m.group(1)).strip(" -:,.")
            if 2 < len(v) < 80:
                return v
    return ""


TITLE_TAIL = re.compile(
    r"\s+(?:at|@|with|for)\s+[A-Z].*$|\s*[|(\[].*$|\s*[-\u2013]\s*(?:remote|hybrid|"
    r"on ?site|full[\s-]?time|part[\s-]?time|india|bangalore|bengaluru|hyderabad|"
    r"pune|chennai|mumbai|delhi|noida|gurgaon).*$", re.I)


def clean_title(t: str) -> str:
    """Strip the trailing ' at Company' and location clauses alert subjects add."""
    t = TITLE_TAIL.sub("", t).strip(" -:,.|")
    return re.sub(r"\s{2,}", " ", t)


def role_family(title: str, body: str) -> str:
    for rx, fam in ROLE_FAMILY:
        if rx.search(title) or rx.search(body[:1200]):
            return fam
    return "other"


# ------------------------------------------------------------------ v1

def parse_regex(item: dict) -> dict | None:
    subject = NOISE.sub("", item.get("subject") or "")
    body = item.get("body") or ""
    text = f"{subject}\n{body}"

    title = first(TITLE_RE, subject) or first(TITLE_RE, body)
    if not title:
        return None
    title = clean_title(title)

    company = first(COMPANY_RE, text)
    if not company:
        sender = item.get("sender") or ""
        m = re.search(r"@([\w-]+)\.", sender)
        if m and m.group(1).lower() not in ("gmail", "linkedin", "naukri", "yahoo",
                                            "outlook", "hotmail", "indeed", "glassdoor"):
            company = m.group(1).replace("-", " ").title()
    if not company:
        return None

    url, kind = pick_apply_url(text)
    loc = LOCATION_RE.search(text)
    dl = DEADLINE_RE.search(text)
    emails = [e for e in EMAIL_RE.findall(text)
              if not re.search(r"noreply|no-reply|donotreply|notifications?@|"
                               r"support@|info@mail|bounce", e, re.I)]

    return {
        "title": title[:120],
        "company": company[:80],
        "location": (loc.group(1).title() if loc else ""),
        "apply_url": url,
        "apply_kind": kind,
        "apply_email": emails[0] if emails else "",
        "deadline": (dl.group(1) if dl else ""),
        "role_family": role_family(title, body),
        "description": body[:6000],
        "via": "regex",
    }


# ------------------------------------------------------------------ v2

PROMPT = """You are parsing job-alert emails into structured records.

For EACH numbered item below, output one object. If an item is not a real job
posting (newsletter, rejection, event invite, marketing), set "skip": true and
nothing else for it.

Fields: id (echo the number), title, company, location, apply_url, apply_email,
deadline, role_family (one of sde, ml, data, product, other), skip.

Use "" for anything not stated. Never invent a company name or a URL.

Output a JSON array, one object per item, same order.

ITEMS:
{items}"""


def parse_llm(items: list[dict]) -> dict[int, dict]:
    from .llm import ask_json, LLMError
    blob = "\n\n".join(
        f"--- {i} ---\nSUBJECT: {(it.get('subject') or '')[:200]}\n"
        f"FROM: {(it.get('sender') or '')[:120]}\n"
        f"BODY:\n{(it.get('body') or '')[:2500]}"
        for i, it in enumerate(items)
    )
    try:
        rows = ask_json(PROMPT.format(items=blob), task="extract")
    except LLMError as e:
        print(f"  ! llm extract failed, keeping items for retry: {e}")
        return {}

    out: dict[int, dict] = {}
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict) or r.get("skip"):
            continue
        try:
            idx = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if not r.get("title") or not r.get("company"):
            continue
        src = items[idx] if 0 <= idx < len(items) else {}
        url = r.get("apply_url") or ""
        out[idx] = {
            "title": str(r["title"])[:120],
            "company": str(r["company"])[:80],
            "location": str(r.get("location") or "")[:60],
            "apply_url": url,
            "apply_kind": classify_url(url) if url else "unknown",
            "apply_email": str(r.get("apply_email") or "")[:120],
            "deadline": str(r.get("deadline") or "")[:40],
            "role_family": (r.get("role_family") or "other").lower(),
            "description": (src.get("body") or "")[:6000],
            "via": "llm",
        }
    return out


# ------------------------------------------------------------------ persist

def upsert_company(conn, name: str) -> int:
    norm = norm_company(name)
    conn.execute("INSERT OR IGNORE INTO companies (name, norm_name) VALUES (?,?)",
                 (name, norm))
    return conn.execute("SELECT id FROM companies WHERE norm_name=?", (norm,)).fetchone()["id"]


def save(parsed: dict, raw_id: int) -> int | None:
    from .score import score_job
    fit, reason, skills = score_job(parsed)
    h = dedupe_hash(parsed["company"], parsed["title"], parsed.get("location", ""))
    with db.tx() as c:
        cid = upsert_company(c, parsed["company"])
        cur = c.execute(
            "INSERT OR IGNORE INTO jobs (company_id, title, role_family, location,"
            " apply_url, apply_kind, apply_email, deadline, description, skills_json,"
            " fit_score, fit_reason, raw_item_id, dedupe_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, parsed["title"], parsed.get("role_family"), parsed.get("location"),
             parsed.get("apply_url"), parsed.get("apply_kind"), parsed.get("apply_email"),
             parsed.get("deadline"), parsed.get("description"), json.dumps(skills),
             fit, reason, raw_id, h),
        )
        if cur.rowcount == 0:
            return None
        job_id = cur.lastrowid
        if parsed.get("apply_email"):
            c.execute("INSERT OR IGNORE INTO contacts (company_id, email, source, verified)"
                      " VALUES (?,?,?,1)", (cid, parsed["apply_email"], "posting"))
        return job_id


def run(limit: int = 200, use_llm: bool = True, verbose: bool = True) -> dict:
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM raw_items WHERE processed=0 ORDER BY id LIMIT ?", (limit,)
        )]
    if not rows:
        return {"pending": 0, "new_jobs": 0, "duplicates": 0, "unparsed": 0}

    stats = {"pending": len(rows), "new_jobs": 0, "duplicates": 0,
             "unparsed": 0, "by_regex": 0, "by_llm": 0}
    leftovers: list[dict] = []

    for r in rows:
        p = parse_regex(r)
        if p:
            jid = save(p, r["id"])
            stats["new_jobs" if jid else "duplicates"] += 1
            stats["by_regex"] += 1
            with db.tx() as c:
                c.execute("UPDATE raw_items SET processed=1 WHERE id=?", (r["id"],))
        else:
            leftovers.append(r)

    if leftovers and use_llm:
        from .llm import available
        if available():
            batch = int(config().get("llm", {}).get("batch_size", 15))
            for i in range(0, len(leftovers), batch):
                chunk = leftovers[i:i + batch]
                if verbose:
                    print(f"  llm batch {i//batch + 1}: {len(chunk)} items")
                got = parse_llm(chunk)
                for j, item in enumerate(chunk):
                    if j in got:
                        jid = save(got[j], item["id"])
                        stats["new_jobs" if jid else "duplicates"] += 1
                        stats["by_llm"] += 1
                    else:
                        stats["unparsed"] += 1
                    with db.tx() as c:
                        c.execute("UPDATE raw_items SET processed=1 WHERE id=?", (item["id"],))
        else:
            stats["unparsed"] += len(leftovers)
    else:
        stats["unparsed"] += len(leftovers)

    db.log("extract", None, "run", **stats)
    return stats
