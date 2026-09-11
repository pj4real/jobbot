"""Resolve LinkedIn job links to where Apply actually goes.

A job alert email gives you a tracking link and a one line summary. The link
goes to a posting page, not a form, which is why the filler was useless on
them. But a large share of those postings carry an "Apply on company website"
button pointing at Greenhouse, Lever, Ashby or Workday, and those the filler
handles today. Finding that destination is the whole job of this module.

Deliberate limits, because this is browser automation against a site whose
terms do not allow it and whose penalty lands on the account recruiters use to
find you:

  it only visits links you were already sent, never a search or a feed
  it runs in the browser profile you are already signed into
  a small daily cap, randomised human pauses, a visible window
  it reads. It never clicks Apply, and it never submits anything

That shape looks like you opening your own job alerts, because it nearly is.
It is still automated access, and the risk is not zero. `resolve_cap` in
config.yaml is the dial.
"""
from __future__ import annotations
import random
import re
import time
from pathlib import Path

from core import db
from core.config import config, path
from core.urls import canonical

PROFILE_DIR = path("data", "browser")

JOB_ID = re.compile(r"/jobs/view/(\d+)")

# what the apply control looks like, in rough order of how much we want it
APPLY_SELECTORS = [
    "a.jobs-apply-button",                       # external, anchor form
    "button.jobs-apply-button",                  # easy apply
    "[data-job-id] a[href*='http']:has-text('Apply')",
    "a:has-text('Apply on company website')",
    "button:has-text('Easy Apply')",
    "a:has-text('Apply')",
]

DESC_SELECTORS = [
    "div.jobs-description__content",
    "div.show-more-less-html__markup",
    "section.description",
    "[class*='jobs-box__html-content']",
    "div.description__text",
]

META_SELECTORS = {
    "company": ["a.topcard__org-name-link", ".job-details-jobs-unified-top-card__company-name",
                "span.topcard__flavor", "[class*='company-name']"],
    "location": [".topcard__flavor--bullet",
                 ".job-details-jobs-unified-top-card__primary-description-container",
                 "[class*='bullet']"],
    "title": ["h1.topcard__title", "h1.top-card-layout__title",
              ".job-details-jobs-unified-top-card__job-title", "h1"],
}


class NotSignedIn(Exception):
    pass


def _first_text(page, selectors, limit=4000) -> str:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count():
                t = loc.inner_text(timeout=2500).strip()
                if t:
                    return t[:limit]
        except Exception:
            continue
    return ""


def _apply_target(page) -> tuple[str, str]:
    """Returns (kind, url). kind is external, easy_apply or unknown."""
    for sel in APPLY_SELECTORS:
        try:
            loc = page.locator(sel).first
            if not loc.count():
                continue
            label = (loc.inner_text(timeout=1500) or "").strip().lower()
            href = loc.get_attribute("href") or ""
            if href and "linkedin.com" not in href:
                return "external", href
            if "easy apply" in label:
                return "easy_apply", ""
            if href and "/jobs/view/" in href:
                continue
        except Exception:
            continue

    # The visible button often carries no href at all: LinkedIn fetches the
    # destination on click, which we will not do. But the page ships the answer
    # as typed JSON in <code> blocks, and two type markers settle it.
    try:
        html = page.content()
    except Exception:
        return "unknown", ""

    for pat in (r'"companyApplyUrl"\s*:\s*"([^"]+)"',
                r'"applyUrl"\s*:\s*"([^"]+)"',
                r'"companyApplyUrl\\?"\s*:\s*\\?"([^"\\]+)'):
        m = re.search(pat, html)
        if m:
            return "external", _unescape(m.group(1))

    if re.search(r"OffsiteApply", html):
        # offsite, but the url did not survive. Still better than unknown: the
        # posting page will hand it over when you click Apply yourself.
        return "offsite", ""
    if re.search(r"ComplexOnsiteApply|SimpleOnsiteApply|easyApplyUrl", html):
        return "easy_apply", ""
    return "unknown", ""


def _unescape(u: str) -> str:
    return (u.replace("\\u0026", "&").replace("\\/", "/")
             .replace("&amp;", "&").replace("\\", ""))


def _signed_in(page) -> bool:
    u = page.url.lower()
    if "/authwall" in u or "/checkpoint" in u or "linkedin.com/login" in u:
        return False
    try:
        if page.locator("form.login__form, input#username").first.count():
            return False
    except Exception:
        pass
    return True


def pending(limit: int) -> list[dict]:
    """LinkedIn jobs we have not resolved yet, best fit first."""
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT j.id, j.apply_url, j.title, co.name company"
            "  FROM jobs j LEFT JOIN companies co ON co.id = j.company_id"
            " WHERE j.apply_kind = 'linkedin'"
            "   AND j.apply_url LIKE '%/jobs/view/%'"
            "   AND j.resolved_at IS NULL"
            " ORDER BY j.fit_score DESC, j.id DESC LIMIT ?", (limit,))]


def _retitle_company(job_id: int, name: str) -> None:
    """The page knows the real employer; the alert email often did not."""
    from core.extract import norm_company, upsert_company
    clean = re.sub(r"\s+", " ", name.splitlines()[0]).strip(" ·-|")[:80]
    if not clean or len(clean) < 2:
        return
    with db.tx() as c:
        cid = upsert_company(c, clean)
        c.execute("UPDATE jobs SET company_id=? WHERE id=?", (cid, job_id))


EVIDENCE_DIR = path("data", "linkedin")


def _keep_evidence(page, job_id: int) -> None:
    """A posting whose apply route we could not read is a bug report. Keep the
    page so it can be fixed from what was actually there."""
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        (EVIDENCE_DIR / f"{job_id}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(EVIDENCE_DIR / f"{job_id}.png"), full_page=False)
    except Exception:
        pass


def resolve(limit: int | None = None, headless: bool = False,
            verbose: bool = True) -> dict:
    cfg = config().get("sources", {}).get("linkedin", {}) or {}
    cap = int(limit or cfg.get("resolve_cap", 15))
    gap = float(cfg.get("gap_seconds", 6))

    jobs = pending(cap)
    stats = {"looked_at": 0, "external": 0, "easy_apply": 0, "offsite": 0,
             "unknown": 0, "described": 0, "failed": 0, "signed_out": False}
    if not jobs:
        return stats

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("pip install playwright && playwright install chromium")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless,
            viewport={"width": 1340, "height": 940},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for i, job in enumerate(jobs):
            url = canonical(job["apply_url"])[0]
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(int(random.uniform(1400, 2600)))
            except Exception as e:
                stats["failed"] += 1
                if verbose:
                    print(f"  ! {job['id']}: {str(e).splitlines()[0][:80]}")
                continue

            if not _signed_in(page):
                stats["signed_out"] = True
                print("\n  LinkedIn is showing a sign-in wall.")
                print("  Sign in in the window that just opened, then run this again.")
                print("  The session is kept in data/browser, so it is a one time thing.")
                page.wait_for_timeout(90000)
                if not _signed_in(page):
                    break
                stats["signed_out"] = False

            stats["looked_at"] += 1
            desc = _first_text(page, DESC_SELECTORS)
            kind, target = _apply_target(page)
            title = _first_text(page, META_SELECTORS["title"], 160)
            company = _first_text(page, META_SELECTORS["company"], 120)

            fields, args = [], []
            if desc and len(desc) > 200:
                fields.append("description=?")
                args.append(desc)
                stats["described"] += 1
            if title and len(title) > 3:
                fields.append("title=?")
                args.append(title.splitlines()[0][:120])

            if company and len(company) > 1:
                _retitle_company(job["id"], company)

            if kind == "external" and target:
                clean, tkind, platform = canonical(target)
                if tkind == "form":
                    fields += ["apply_url=?", "apply_kind=?"]
                    args += [clean, platform]
                    stats["external"] += 1
                    if verbose:
                        print(f"  {job['id']:>4}  {(company or job['company'] or '?')[:22]:<24}"
                              f" -> {platform} form")
                else:
                    stats["unknown"] += 1
            elif kind == "offsite":
                fields.append("apply_kind=?")
                args.append("linkedin_offsite")
                stats["offsite"] = stats.get("offsite", 0) + 1
                if verbose:
                    print(f"  {job['id']:>4}  {(company or job['company'] or '?')[:22]:<24}"
                          f" -> applies on the company site, open it to see where")
            elif kind == "easy_apply":
                fields.append("apply_kind=?")
                args.append("linkedin_easy")
                stats["easy_apply"] += 1
                if verbose:
                    print(f"  {job['id']:>4}  {(company or job['company'] or '?')[:22]:<24}"
                          f" -> easy apply, yours to click")
            else:
                stats["unknown"] += 1
                _keep_evidence(page, job["id"])

            fields.append("resolved_at=datetime('now')")
            args.append(job["id"])
            with db.tx() as c:
                c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", args)
            from core import board
            board.record(job["id"], "resolved", kind)

            if i < len(jobs) - 1:
                time.sleep(random.uniform(gap, gap * 2.2))

        ctx.close()

    # the real description is far better than the alert summary, so the score
    # it was given on arrival is worth redoing
    if stats["described"]:
        from core.score import rescore_all
        rescore_all()

    db.log("source", None, "linkedin_resolve", **stats)
    return stats
