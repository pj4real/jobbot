"""The deterministic matcher. This is v1 of the mapper and it does most of the work.

Order matters: the first pattern that matches wins, so specific patterns sit
above general ones. "current company" must be tested before "company", and
"parent name" before "name", or every form gets your own name in the wrong box.
"""
from __future__ import annotations
import re

# (pattern, profile key). Tested against the normalized label, which is the
# label text plus name plus id plus placeholder, lowercased.
RULES: list[tuple[str, str]] = [
    # --- negatives first: match, then deliberately fill nothing ---
    (r"parent|guardian|father|mother|spouse|referee|reference name", "__skip__"),
    (r"current (company|employer|organis|organiz)", "__skip__"),
    (r"password|captcha|otp|verification code", "__skip__"),

    # --- name ---
    (r"\bfirst[\s_-]*name\b|\bgiven[\s_-]*name\b|\bfname\b", "first_name"),
    (r"\blast[\s_-]*name\b|\bsurname\b|\bfamily[\s_-]*name\b|\blname\b", "last_name"),
    (r"\bmiddle[\s_-]*name\b", "__skip__"),
    (r"\bfull[\s_-]*name\b|\bcandidate[\s_-]*name\b|\byour name\b|\bapplicant name\b|^name$",
     "full_name"),

    # --- contact ---
    (r"e[\s_-]?mail|email address", "email"),
    (r"mobile|phone|contact[\s_-]*(no|number)|whatsapp", "phone"),

    # --- links ---
    (r"github", "github"),
    (r"linked[\s_-]?in", "linkedin"),
    (r"portfolio|personal (site|website)|website|leetcode|codeforces", "portfolio"),

    # --- education ---
    (r"\bcgpa\b|\bgpa\b|aggregate|percentage.*(ug|grad|degree)|current percentage", "cgpa"),
    (r"10th|tenth|class x\b|sslc|matriculation", "tenth_percentage"),
    (r"12th|twelfth|class xii|hsc|intermediate|senior secondary", "twelfth_percentage"),
    (r"college|university|institute|school name|campus", "college"),
    (r"branch|stream|specialis|specializ|major|discipline", "branch"),
    (r"degree|qualification|course", "degree"),
    (r"(grad|passing|completion).*(year|out)|year of (passing|graduation)|batch", "grad_year"),
    (r"registration|roll[\s_-]*(no|number)|enrol|student id|\breg no\b", "reg_no"),

    # --- location ---
    (r"current (location|city)|city|town|present address", "city"),
    (r"\bstate\b|province", "state"),
    (r"country|nationality", "country"),

    # --- logistics ---
    (r"notice[\s_-]*period", "notice_period"),
    (r"reloc", "willing_to_relocate"),
    (r"work (authoriz|authoris|permit)|sponsor|visa|legally.*work", "work_authorization"),
    (r"current.*(ctc|salary|compensation)", "current_ctc"),
    (r"expected.*(ctc|salary|compensation)", "expected_ctc"),
    (r"available|start date|joining", "availability"),

    # --- files ---
    (r"resume|\bcv\b|upload.*(resume|cv)", "resume_file"),
    (r"cover[\s_-]*letter", "__essay__"),
    (r"transcript|marksheet", "__skip__"),

    # --- open questions: route to the answer bank or to you ---
    (r"why.*(join|company|us|interest|excites)", "why_this_company"),
    (r"why.*(role|position|this job)", "why_this_role"),
    (r"strength|tell us about your", "strengths"),
    (r"weakness|improve", "weakness"),
    (r"leadership|team.*lead", "leadership"),
]

COMPILED = [(re.compile(p, re.I), key) for p, key in RULES]

# these keys come from profile.answer_bank rather than the flat identity map
BANK_KEYS = {"why_this_company", "why_this_role", "strengths", "weakness",
             "leadership", "relocate", "availability"}

# fields you should always eyeball rather than let a script answer
ALWAYS_HUMAN = {"__essay__"}


def normalize(field: dict) -> str:
    return " ".join(filter(None, [
        field.get("label", ""), field.get("name", ""),
        field.get("id", ""), field.get("placeholder", ""), field.get("aria", ""),
    ])).replace("_", " ").lower()


def match(field: dict) -> str | None:
    """Returns a profile key, a sentinel like __skip__, or None for unmapped."""
    text = normalize(field)
    if not text:
        return None
    for rx, key in COMPILED:
        if rx.search(text):
            return key
    return None


YES = re.compile(r"^\s*(yes|y|true|i (agree|confirm)|available|indian)\b", re.I)
NO = re.compile(r"^\s*(no|n|false)\b", re.I)


def choose_option(options: list[dict], want: str) -> str | None:
    """Pick the option whose text best matches the profile value. Falls back to
    a yes-ish option for the endless 'are you willing to relocate' selects."""
    if not options:
        return None
    want_l = (want or "").strip().lower()

    for o in options:                                   # exact
        if o["text"].strip().lower() == want_l:
            return o["value"]
    for o in options:                                   # substring, both ways
        t = o["text"].strip().lower()
        if t and (t in want_l or want_l in t):
            return o["value"]
    if YES.match(want_l):
        for o in options:
            if YES.match(o["text"]):
                return o["value"]
    if NO.match(want_l):
        for o in options:
            if NO.match(o["text"]):
                return o["value"]
    return None
