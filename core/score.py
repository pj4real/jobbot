"""Fit scoring. Fully deterministic, no model call.

The score decides what surfaces at the top of your queue, so it has to be
explainable. Every job carries the sentence that explains its own score, which
is what lets you tell "the scorer is wrong" apart from "this job is wrong".
"""
from __future__ import annotations
import re
from datetime import datetime, date

from .config import config

SKILL_VOCAB = {
    "python": "python", "java": "java", "c++": "cpp", "golang": "go", "go": "go",
    "typescript": "typescript", "javascript": "javascript", "react": "react",
    "node": "node", "sql": "sql", "postgres": "sql", "mongodb": "nosql",
    "machine learning": "ml", "deep learning": "ml", "pytorch": "ml",
    "tensorflow": "ml", "scikit": "ml", "pandas": "data", "numpy": "data",
    "docker": "docker", "kubernetes": "k8s", "aws": "cloud", "gcp": "cloud",
    "azure": "cloud", "linux": "linux", "git": "git", "rest": "api",
    "microservice": "backend", "security": "security", "cryptography": "crypto",
    "data structures": "dsa", "algorithms": "dsa", "system design": "sysdesign",
    "nlp": "nlp", "llm": "llm", "spark": "bigdata", "kafka": "bigdata",
}

INTERN_RE = re.compile(r"\bintern(ship)?\b|\btrainee\b|\bgraduate\b|\bcampus\b|"
                       r"\bfresher\b|\bentry[\s-]?level\b|\bnew grad\b", re.I)
YEARS_RE = re.compile(r"(\d+)\+?\s*(?:-\s*\d+\s*)?year", re.I)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_deadline(s: str):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{1,2})[\s/-]([A-Za-z]{3,9})[\s/-](\d{2,4})", s)
    if m:
        mon = MONTHS.get(m.group(2)[:3].lower())
        if mon:
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, mon, int(m.group(1)))
    m = re.match(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s*(\d{4})", s)
    if m:
        mon = MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return date(int(m.group(3)), mon, int(m.group(2)))
    return None


def extract_skills(text: str) -> list[str]:
    low = (text or "").lower()
    found = set()
    for phrase, tag in SKILL_VOCAB.items():
        if re.search(r"(?<![a-z+])" + re.escape(phrase) + r"(?![a-z])", low):
            found.add(tag)
    return sorted(found)


def score_job(job: dict) -> tuple[float, str, list[str]]:
    """Returns (score 0..1, one-sentence reason, skills). Additive, capped."""
    cfg = config().get("scoring", {})
    title = job.get("title", "") or ""
    body = job.get("description", "") or ""
    text = f"{title}\n{body}"
    skills = extract_skills(text)
    notes: list[str] = []
    score = 0.35                                  # neutral starting point

    # hard excludes kill it outright
    for bad in cfg.get("hard_excludes", []) or []:
        if re.search(re.escape(str(bad)), title, re.I):
            return 0.0, f"excluded: title matches '{bad}'", skills

    # experience requirement
    yrs = YEARS_RE.search(body)
    if yrs and int(yrs.group(1)) >= 3:
        return 0.05, f"wants {yrs.group(1)}+ years experience", skills
    if yrs and int(yrs.group(1)) >= 2:
        score -= 0.15
        notes.append(f"{yrs.group(1)}y experience asked")

    # role family
    fams = set(cfg.get("role_families", []) or [])
    fam = (job.get("role_family") or "other").lower()
    if fam in fams:
        score += 0.20
        notes.append(f"role {fam}")
    elif fam == "other":
        score -= 0.10
        notes.append("role unclear")

    # intern or entry level language
    if INTERN_RE.search(text):
        score += 0.15
        notes.append("intern/entry level")

    # location
    prefs = [p.lower() for p in (cfg.get("preferred_locations", []) or [])]
    loc = (job.get("location") or "").lower()
    if loc and any(p in loc or loc in p for p in prefs):
        score += 0.12
        notes.append(f"location {job['location']}")
    elif not loc:
        notes.append("location unknown")

    # a working apply route is most of the value
    kind = job.get("apply_kind") or "unknown"
    if kind in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters"):
        score += 0.15
        notes.append(f"{kind} form")
    elif kind == "gform":
        score += 0.12
        notes.append("google form")
    elif job.get("apply_email"):
        score += 0.10
        notes.append("email application")
    elif kind == "unknown" and not job.get("apply_url"):
        score -= 0.20
        notes.append("no apply link found")

    # skill overlap with what you actually have
    mine = {"python", "java", "cpp", "typescript", "sql", "ml", "data", "dsa",
            "security", "crypto", "docker", "linux", "git", "api", "backend"}
    hits = len(mine & set(skills))
    if hits:
        score += min(0.15, hits * 0.03)
        notes.append(f"{hits} skills match")

    # deadline
    dl = parse_deadline(job.get("deadline", ""))
    if dl:
        days = (dl - date.today()).days
        if days < 0:
            return 0.0, f"deadline passed ({dl})", skills
        if days <= 3:
            score += 0.05
            notes.append(f"closes in {days}d")

    score = max(0.0, min(1.0, round(score, 2)))
    return score, "; ".join(notes) or "no signals", skills


def rescore_all() -> int:
    """Rerun scoring over every stored job. Use after editing config.yaml."""
    import json
    from . import db
    n = 0
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT j.*, co.name company_name FROM jobs j"
            " LEFT JOIN companies co ON co.id=j.company_id")]
        for r in rows:
            s, why, skills = score_job(r)
            c.execute("UPDATE jobs SET fit_score=?, fit_reason=?, skills_json=? WHERE id=?",
                      (s, why, json.dumps(skills), r["id"]))
            n += 1
    return n
