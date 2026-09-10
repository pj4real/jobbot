"""Which resume goes with which job.

Most SDE postings want the same resume. A few genuinely do not. This module's
whole job is telling those two cases apart and getting out of the way, so the
manual path is a deliberate exception and not the default workflow.

Resolution order, first match wins:

  1. assets/custom/<job_id>.pdf     you made this by hand. always wins.
  2. mode: fixed                    assets/resume_default.pdf, always
  3. mode: variant                  assets/resume_<role_family>.pdf
  4. mode: tailored                 built from resume.yaml per job

If the posting looks like an outlier, the job is flagged needs_manual and its
description is written to data/jd/ so you can hand that one file to a chat.
"""
from __future__ import annotations
import re
from pathlib import Path

from .config import config, path
from . import db

JD_DIR = path("data", "jd")
CUSTOM_DIR = path("assets", "custom")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "job"


def export_jd(job: dict) -> Path:
    """Write one posting to a text file you can hand to a chat verbatim."""
    JD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{job['id']:04d}-{_slug(job.get('company_name'))}-{_slug(job.get('title'))}.txt"
    p = JD_DIR / name
    p.write_text(
        f"# {job.get('title','')}\n"
        f"# {job.get('company_name','')}\n"
        f"# {job.get('location','')}\n"
        f"# apply: {job.get('apply_url','')}\n"
        f"# job_id: {job['id']}\n"
        f"# drop the resulting pdf at assets/custom/{job['id']}.pdf\n\n"
        f"{job.get('description','')}\n",
        encoding="utf-8",
    )
    return p


def deviates(job: dict, coverage: float) -> str | None:
    """Is this posting far enough from your standard resume to be worth a
    manual pass? Returns a reason, or None if the default is fine."""
    cfg = config().get("resume", {})
    floor = float(cfg.get("min_keyword_coverage", 0.4))
    families = set(config().get("scoring", {}).get("role_families", []))

    if coverage < floor:
        return f"keyword coverage {coverage:.2f} below {floor}"
    fam = (job.get("role_family") or "").lower()
    if fam and families and fam not in families:
        return f"role family '{fam}' is outside your usual set"
    return None


def resolve(job: dict) -> dict:
    """Returns {path, mode, reason, needs_manual}."""
    cfg = config().get("resume", {})
    mode = cfg.get("mode", "variant")

    custom = CUSTOM_DIR / f"{job['id']}.pdf"
    if custom.exists():
        return {"path": str(custom), "mode": "custom",
                "reason": "hand made, overrides everything", "needs_manual": False}

    default = path("assets", "resume_default.pdf")

    if mode == "fixed":
        return {"path": str(default), "mode": "fixed",
                "reason": "", "needs_manual": not default.exists(),
                "missing": not default.exists()}

    if mode == "variant":
        fam = (job.get("role_family") or "").lower()
        cand = path("assets", f"resume_{fam}.pdf") if fam else default
        chosen = cand if cand.exists() else default
        return {"path": str(chosen), "mode": "variant",
                "reason": f"role_family={fam or 'unknown'}",
                "needs_manual": not chosen.exists(),
                "missing": not chosen.exists()}

    # tailored
    from . import resume as tailor
    out = path("data", "resumes", f"{job['id']}.pdf")
    audit = tailor.build(
        job.get("description", ""), out,
        job_title=job.get("title", ""), company=job.get("company_name", ""),
    )
    reason = deviates(job, audit["coverage"])
    if reason:
        jd = export_jd(job)
        db.log("job", job["id"], "needs_manual_resume", reason=reason, jd_file=str(jd))
        return {"path": audit["path"], "mode": "tailored", "reason": reason,
                "needs_manual": True, "jd_file": str(jd), "audit": audit}
    return {"path": audit["path"], "mode": "tailored", "reason": "",
            "needs_manual": False, "audit": audit}
