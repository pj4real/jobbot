"""Tailored resume selection.

The engine SELECTS and ORDERS bullets from resume.yaml. It never writes new
ones. That is not a limitation to work around, it is the safety property: a
resume assembled from a fixed bank cannot claim something you did not do, and
every rendered file is logged with the exact bullet ids so you can reconstruct
what any company received.

Scoring is deterministic. The LLM's only job is breaking ties and picking the
tagline, and its output is validated against ids that already exist.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import config, resume as resume_bank, profile, path
from . import render, db

# ---------------------------------------------------------------- keywords

# maps words that appear in postings to the tags used in resume.yaml
VOCAB = {
    "python": "python", "java": "java", "c++": "cpp", "cpp": "cpp",
    "typescript": "typescript", "javascript": "typescript", "sql": "sql",
    "machine learning": "ml", "ml": "ml", "deep learning": "ml",
    "data science": "data", "data": "data", "analytics": "data",
    "pandas": "data", "numpy": "data", "scikit": "ml", "xgboost": "ml",
    "backend": "backend", "api": "api", "rest": "api", "microservice": "backend",
    "frontend": "web", "react": "web", "full stack": "fullstack",
    "fullstack": "fullstack", "web": "web",
    "security": "security", "cybersecurity": "security", "network": "network",
    "cryptography": "crypto", "encryption": "crypto", "tls": "security",
    "algorithm": "algorithms", "data structure": "algorithms",
    "problem solving": "algorithms", "competitive": "cp",
    "docker": "devops", "kubernetes": "devops", "linux": "devops",
    "infrastructure": "infra", "nginx": "infra",
    "product": "product", "roadmap": "product", "stakeholder": "product",
    "compiler": "compilers", "ai": "ai", "nlp": "ai", "speech": "speech",
    "mobile": "mobile", "android": "mobile",
    "leadership": "leadership", "mentor": "leadership",
}


def keywords(text: str) -> set[str]:
    """Deterministic. No model call, so this costs nothing and never drifts."""
    low = (text or "").lower()
    found = set()
    for phrase, tag in VOCAB.items():
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low):
            found.add(tag)
    return found


# ---------------------------------------------------------------- scoring

@dataclass
class Item:
    id: str
    text: str
    tags: list = field(default_factory=list)
    weight: int = 3
    lines: int = 1
    parent: str = ""

    def score(self, want: set[str]) -> float:
        if not want:
            return self.weight / 5.0
        overlap = len(want & set(self.tags))
        # overlap dominates, your own weight breaks ties between equal matches
        return overlap * 2.0 + (self.weight / 5.0)


def _items(section, parent="") -> list[Item]:
    out = []
    for b in section or []:
        out.append(Item(
            id=b["id"], text=b["text"], tags=b.get("tags", []),
            weight=int(b.get("weight", 3)), lines=int(b.get("lines", 1)),
            parent=parent,
        ))
    return out


# ---------------------------------------------------------------- selection

def select(jd_text: str, budget_lines: int = 34) -> dict:
    """Pick what goes on the page. Returns a render context plus an audit dict."""
    bank = resume_bank()
    want = keywords(jd_text)

    # -- tagline
    pool = bank["header"].get("tagline_pool", [])
    tagline = max(pool, key=lambda t: len(want & set(t.get("tags", []))) if want else 0,
                  default=None) if pool else None
    chosen_ids = [tagline["id"]] if tagline else []

    # -- experience: every role stays, its bullets get ranked and trimmed
    experience = []
    for x in bank.get("experience", []) or []:
        ranked = sorted(_items(x.get("bullets"), x.get("org", "")),
                        key=lambda i: -i.score(want))
        keep = [i for i in ranked if "FILL" not in i.text][:3]
        if not keep:
            continue          # never render a FILL placeholder into a real pdf
        chosen_ids += [i.id for i in keep]
        experience.append({
            "org": x.get("org", ""), "role": x.get("role", ""),
            "dates": x.get("dates", ""), "location": x.get("location", ""),
            "bullets": [i.text for i in keep],
        })

    # -- projects: ranked as whole units, then their bullets ranked inside
    scored_projects = []
    for p in bank.get("projects", []) or []:
        bullets = _items(p.get("bullets"), p.get("id", ""))
        usable = [b for b in bullets if "FILL" not in b.text]
        if not usable:
            continue                      # a project with no real bullet is not shown
        proj_tags = set(p.get("tags", []))
        proj_score = (len(want & proj_tags) * 2.0 if want else 0) + p.get("weight", 3) / 5.0
        scored_projects.append((proj_score, p, sorted(usable, key=lambda i: -i.score(want))))
    scored_projects.sort(key=lambda t: -t[0])

    projects, used_lines = [], 0
    max_projects = int(config().get("resume", {}).get("max_projects", 4))
    for _, p, bullets in scored_projects[:max_projects]:
        keep = bullets[:2]
        used_lines += sum(b.lines for b in keep) + 1
        chosen_ids += [b.id for b in keep]
        projects.append({
            "name": p.get("name", ""),
            "stack": ", ".join(p.get("stack", []) or []),
            "link": p.get("link") or "",
            "bullets": [b.text for b in keep],
        })

    # -- skills: groups kept, items reordered so jd matches surface first
    skills = []
    for g in bank.get("skills", []) or []:
        items = sorted(g.get("items", []),
                       key=lambda s: -(1 if want & set(s.get("tags", [])) else 0))
        skills.append({"group": g["group"], "items": [s["text"] for s in items]})

    # -- achievements
    ach = sorted(_items(bank.get("achievements")), key=lambda i: -i.score(want))
    ach = [a for a in ach if "FILL" not in a.text][:3]
    chosen_ids += [a.id for a in ach]

    # -- education
    education = []
    for e in bank.get("education", []) or []:
        cw = sorted(_items(e.get("coursework")), key=lambda i: -i.score(want))
        education.append({
            "institution": e.get("institution", ""), "degree": e.get("degree", ""),
            "dates": e.get("dates", ""), "detail": e.get("detail", ""),
            "coursework": cw[0].text if cw else "",
        })

    ident = profile()["identity"]

    def short(u: str) -> str:
        return re.sub(r"^https?://(www\.)?", "", u or "")

    contact = [ident["email"], ident.get("phone", ""),
               short(ident.get("github", "")), short(ident.get("linkedin", ""))]

    coverage = 0.0
    if want:
        shown = set()
        for pj in scored_projects[:max_projects]:
            shown |= set(pj[1].get("tags", []))
        for g in bank.get("skills", []) or []:
            for s in g.get("items", []):
                shown |= set(s.get("tags", []))
        coverage = len(want & shown) / len(want)

    return {
        "context": {
            "header": {
                "name": bank["header"]["name"],
                "tagline": render.tex_escape(tagline["text"]) if tagline else "",
                "contact": [render.tex_escape(c) for c in contact if c],
            },
            "education": [{k: render.tex_escape(v) for k, v in e.items()} for e in education],
            "experience": [{**x,
                            "org": render.tex_escape(x["org"]),
                            "role": render.tex_escape(x["role"]),
                            "dates": render.tex_escape(x["dates"]),
                            "location": render.tex_escape(x["location"]),
                            "bullets": [render.tex_escape(b) for b in x["bullets"]]}
                           for x in experience],
            "projects": [{**p,
                          "name": render.tex_escape(p["name"]),
                          "stack": render.tex_escape(p["stack"]),
                          "link": p["link"],
                          "bullets": [render.tex_escape(b) for b in p["bullets"]]}
                         for p in projects],
            "skills": [{"group": render.tex_escape(g["group"]),
                        "items": [render.tex_escape(i) for i in g["items"]]}
                       for g in skills],
            "achievements": [render.tex_escape(a.text) for a in ach],
            "opts": {"font_size": 10, "margin": 0.55, "section_before": 0.8, "item_sep": 0.05},
            "meta": {"job_title": "", "company": "", "bullet_ids": chosen_ids},
        },
        "bullet_ids": chosen_ids,
        "keywords": sorted(want),
        "coverage": round(coverage, 2),
    }


# ---------------------------------------------------------------- build

SHRINK = [
    {"font_size": 10, "margin": 0.55, "section_before": 0.8,  "item_sep": 0.05},
    {"font_size": 10, "margin": 0.45, "section_before": 0.6,  "item_sep": 0.02},
    {"font_size": 9,  "margin": 0.45, "section_before": 0.5,  "item_sep": 0.0},
]


def build(jd_text: str, out_pdf, job_title: str = "", company: str = "",
          max_pages: int = 1) -> dict:
    """Select, render, and shrink until it fits. Returns the audit record."""
    sel = select(jd_text)
    ctx = sel["context"]
    ctx["meta"]["job_title"] = job_title
    ctx["meta"]["company"] = company

    out_pdf = Path(out_pdf)
    last = None
    for opts in SHRINK:
        ctx["opts"] = opts
        tex = render.to_tex(ctx)
        last = render.to_pdf(tex, out_pdf)
        if render.page_count(last) <= max_pages:
            break
    else:
        # still too long: drop the weakest project and try once more
        if len(ctx["projects"]) > 1:
            ctx["projects"] = ctx["projects"][:-1]
            last = render.to_pdf(render.to_tex(ctx), out_pdf)

    return {
        "path": str(last),
        "pages": render.page_count(last),
        "bullet_ids": sel["bullet_ids"],
        "keywords": sel["keywords"],
        "coverage": sel["coverage"],
        "tagline": ctx["header"]["tagline"],
    }


def record(job_id: int, audit: dict) -> int:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO resumes (job_id, path, bullet_ids, headline, skills_shown, coverage)"
            " VALUES (?,?,?,?,?,?)",
            (job_id, audit["path"], json.dumps(audit["bullet_ids"]),
             audit.get("tagline", ""), json.dumps(audit["keywords"]), audit["coverage"]),
        )
        return cur.lastrowid
