"""Cleaning up the links job alerts actually contain.

A first real scan makes one thing obvious: almost nothing in a job alert email
is an application form. LinkedIn and Indeed wrap every link in their own
click-tracking, and what is on the other side is a posting page you read and
then click Apply on, which is a different thing from a form you can fill.

Conflating the two is why the filler was launched at a LinkedIn alerts digest.
So links get sorted into three kinds:

  form      an application form. The filler works here.
  posting   a page about one job. Open it, read it, click Apply yourself.
  noise     a digest, a search, a feed. Not a job at all.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse, parse_qs, urlencode

# a real application form, where filling in fields is the point
FORM_HOSTS = {
    "boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby",
    "docs.google.com": "gform", "forms.gle": "gform",
    "myworkdayjobs.com": "workday", "taleo.net": "taleo",
    "smartrecruiters.com": "smartrecruiters", "jobs.workable.com": "workable",
    "zohorecruit.com": "zoho", "keka.com": "keka", "darwinbox.in": "darwinbox",
    "freshteam.com": "freshteam", "recruitee.com": "recruitee",
}

# a page about a job that you still have to click Apply on
POSTING_HOSTS = {
    "linkedin.com": "linkedin", "indeed.com": "indeed",
    "naukri.com": "naukri", "instahyre.com": "instahyre",
    "unstop.com": "unstop", "wellfound.com": "wellfound",
    "hirist.tech": "hirist", "cutshort.io": "cutshort",
    "glassdoor.co.in": "glassdoor", "internshala.com": "internshala",
    "foundit.in": "foundit", "shine.com": "shine",
}

# never a specific job
NOISE = re.compile(
    r"/jobs/alerts|/jobs/search|/jobs/collections|/feed|/notifications|"
    r"/unsubscribe|/settings|/mynetwork|/premium|/subscriptions|"
    r"/email[_-]?preference|/manage[_-]?alerts|/profile/", re.I)

# tracking params worth throwing away. They make links unreadable, they expire,
# and two links to the same job look different because of them.
JUNK_PARAMS = {
    "lipi", "lici", "trackingid", "trk", "trkinfo", "refid", "midtoken",
    "midsig", "eid", "otpToken", "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "from", "qd", "tk", "rq", "xkcb", "xpse",
    "alid", "camk", "sal", "acatk", "pub", "vjs", "spa", "hidesmb",
}

LINKEDIN_JOB = re.compile(r"/jobs/view/(\d+)")
INDEED_JK = re.compile(r"[?&]jk=([0-9a-f]+)", re.I)


def canonical(url: str) -> tuple[str, str, str]:
    """Returns (clean_url, kind, platform).

    kind is form, posting or noise. A noise link is not worth keeping.
    """
    if not url:
        return "", "noise", ""
    u = url.strip().rstrip(".,);]")
    host = (urlparse(u).hostname or "").lower()

    # LinkedIn: /comm/ is their email click path. The job id is what matters.
    if "linkedin.com" in host:
        m = LINKEDIN_JOB.search(u)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}/", "posting", "linkedin"
        return u, "noise", "linkedin"

    # Indeed: several redirect shapes, all carrying a jk= job key
    if "indeed.com" in host:
        m = INDEED_JK.search(u)
        if m:
            return f"https://in.indeed.com/viewjob?jk={m.group(1)}", "posting", "indeed"
        if "/rc/clk" in u or "cts.indeed" in u or "/pagead/" in u:
            # a redirect whose target we cannot read without following it
            return u, "posting", "indeed"
        return u, "noise", "indeed"

    if NOISE.search(u):
        return u, "noise", host

    for frag, name in FORM_HOSTS.items():
        if host.endswith(frag) or frag in host:
            return _strip_junk(u), "form", name

    for frag, name in POSTING_HOSTS.items():
        if host.endswith(frag) or frag in host:
            return _strip_junk(u), "posting", name

    # an unknown host with a jobs-ish path is most likely a company careers page
    if re.search(r"/(careers?|jobs?|opening|vacanc|apply|recruit)", u, re.I):
        return _strip_junk(u), "form", "unknown"
    return _strip_junk(u), "posting", "unknown"


def _strip_junk(u: str) -> str:
    p = urlparse(u)
    if not p.query:
        return u
    keep = {k: v for k, v in parse_qs(p.query, keep_blank_values=True).items()
            if k.lower() not in JUNK_PARAMS}
    q = urlencode(keep, doseq=True)
    return p._replace(query=q, fragment="").geturl()


def fillable(url: str) -> tuple[bool, str]:
    """Is opening the form filler on this link worth anyone's time?"""
    clean, kind, platform = canonical(url)
    if kind == "noise":
        return False, ("That link is a job-alert digest or a search page, not a "
                       "posting. There is nothing on it to fill in.")
    if platform == "linkedin":
        return False, ("This is a LinkedIn posting page, not a form. Open it and "
                       "use Easy Apply yourself: automating LinkedIn's own apply "
                       "flow breaks their terms and gets accounts restricted, "
                       "which is not a trade worth making during placement season.")
    if kind == "posting":
        return False, (f"This is a {platform} posting page rather than an "
                       f"application form. Open it, and when it sends you to the "
                       f"company's own form, fill that URL instead.")
    return True, ""
