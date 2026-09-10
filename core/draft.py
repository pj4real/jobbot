"""Write the outreach mail.

v1 is a template with one hand-picked evidence line. v2 lets the model write
the opening paragraph, but only from your evidence block, never from the void.

Every constraint here exists because of how cold mail actually fails:
  - plain text, no html, no tracking pixel  -> spam filters
  - short, one specific claim              -> nobody reads paragraph three
  - a real subject naming the role         -> gets opened
  - evidence only from profile.yaml        -> nothing you cannot defend
"""
from __future__ import annotations
import json
import random
import re

from .config import profile, config
from . import db

NAMED_GREETING = ["Hi {name},", "Hello {name},"]
ANON_GREETING = ["Hi there,", "Hello,"]


def _evidence(job: dict, k: int = 2) -> list[dict]:
    """Pick evidence lines whose tags overlap the posting's skills."""
    # a placeholder line must never reach a recruiter. same rule as the resume
    # renderer: unfinished content is dropped, not shipped.
    ev = [e for e in (profile().get("evidence") or [])
          if "FILL" not in (e.get("line") or "")]
    if not ev:
        return []
    want = set(json.loads(job.get("skills_json") or "[]"))
    fam = (job.get("role_family") or "").lower()
    want.add(fam)
    scored = sorted(ev, key=lambda e: -len(want & set(e.get("tags", []))))
    return scored[:k]


def subject_line(job: dict) -> str:
    ident = profile()["identity"]
    edu = profile()["education"]
    return f"{job['title']} - {ident['full_name']}, {edu['college'].split(',')[0]} {edu['grad_year']}"


def template_body(job: dict, contact_name: str = "") -> str:
    ident = profile()["identity"]
    edu = profile()["education"]
    ev = _evidence(job)
    lines = [
        (random.choice(NAMED_GREETING).format(name=contact_name)
         if contact_name else random.choice(ANON_GREETING)),
        "",
        f"I'm applying for the {job['title']} role at {job['company_name']}.",
        "",
        f"I'm a {edu['degree']} student at {edu['college']}, graduating "
        f"{edu.get('grad_month','')} {edu['grad_year']}.",
    ]
    if ev:
        for e in ev:
            lines += ["", " ".join(e["line"].split())]
    else:
        lines += ["", "[no usable evidence lines in profile.yaml. Fill the FILL "
                      "entries under `evidence:` before sending this.]"]
    lines += [
        "",
        "My resume is attached. Happy to talk whenever suits you.",
        "",
        "Thanks,",
        ident["full_name"],
        f"{ident.get('github','')}  |  {ident.get('linkedin','')}".strip(" |"),
    ]
    return "\n".join(lines)


LLM_PROMPT = """Write the opening two sentences of a cold job application email.

CANDIDATE FACTS, the only things you may state:
{facts}

THE POSTING:
{posting}

Rules, all of them hard:
- Two sentences, under 55 words total.
- State one concrete thing from CANDIDATE FACTS that connects to this posting.
- Never claim experience, a metric, a company, or a technology not in the facts.
- No "I am excited", "I am passionate", "I came across", "I hope this finds you".
- Plain sentences. No adjectives you would not say out loud.
- Output the two sentences only. No greeting, no signoff, no quotes."""


def llm_opening(job: dict) -> str | None:
    from .llm import ask, LLMError
    ev = _evidence(job, k=4)
    if not ev:
        return None
    edu = profile()["education"]
    facts = "\n".join(
        [f"- {edu['degree']} at {edu['college']}, graduating {edu['grad_year']}, CGPA {edu['cgpa']}"]
        + [f"- {e['line'].strip()}" for e in ev])
    posting = f"{job['title']} at {job['company_name']}\n{(job.get('description') or '')[:1200]}"
    try:
        out = ask(LLM_PROMPT.format(facts=facts, posting=posting), task="draft").strip()
    except LLMError:
        return None
    out = re.sub(r'^["\']|["\']$', "", out).strip()
    banned = ("excited", "passionate", "i came across", "hope this finds",
              "reaching out", "thrilled", "delve")
    if any(b in out.lower() for b in banned) or len(out.split()) > 75:
        return None
    return out


def build(job: dict, contact: dict | None, use_llm: bool = True) -> dict:
    name = ""
    if contact and contact.get("name"):
        name = contact["name"].split()[0]
    body = template_body(job, name)
    ev_ids = [e["id"] for e in _evidence(job)]

    if use_llm:
        opening = llm_opening(job)
        if opening:
            lines = body.split("\n")
            # replace the two template sentences with the generated opening
            body = "\n".join(lines[:2] + [opening, ""] + lines[6:])
    return {"subject": subject_line(job), "body": body, "evidence_ids": ev_ids}


def save(job_id: int, contact_id: int | None, d: dict) -> int:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO drafts (job_id, contact_id, subject, body, evidence_ids)"
            " VALUES (?,?,?,?,?)",
            (job_id, contact_id, d["subject"], d["body"], json.dumps(d["evidence_ids"])))
        return cur.lastrowid
