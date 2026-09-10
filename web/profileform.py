"""Editing profile.yaml through a form, without destroying the file.

Round-tripping YAML through a parser and dumping it back would work and would
also silently delete every comment in the file, and profile.yaml is mostly
comments explaining what each field is for. So these edits are surgical: find
the one line (or the one block) that changes, replace it, leave the rest byte
for byte identical.

Every write is validated by re-parsing before it lands, so a bad edit fails
loudly instead of leaving you with a broken profile.
"""
from __future__ import annotations
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "profile.yaml"

# section -> [(key, label, placeholder, help)]
FIELDS = {
    "identity": [
        ("full_name", "Full name", "", ""),
        ("first_name", "First name", "", ""),
        ("last_name", "Last name", "", ""),
        ("email", "Email", "", ""),
        ("phone", "Phone", "+91 00000 00000",
         "With the country code. Portals reject bare 10 digit numbers often enough."),
        ("city", "City", "", ""),
        ("state", "State", "", ""),
        ("github", "GitHub", "https://github.com/...", ""),
        ("linkedin", "LinkedIn", "https://linkedin.com/in/...", ""),
        ("portfolio", "Portfolio", "", "Optional. Left blank is fine."),
    ],
    "education": [
        ("college", "College", "", ""),
        ("degree", "Degree", "", ""),
        ("branch", "Branch", "", ""),
        ("cgpa", "CGPA", "8.5", ""),
        ("grad_year", "Graduation year", "2027", ""),
        ("grad_month", "Graduation month", "July", ""),
        ("reg_no", "Registration number", "", ""),
        ("tenth_percentage", "Class X percentage", "92.0",
         "Indian portals ask for this constantly. A blank stops the filler mid form."),
        ("twelfth_percentage", "Class XII percentage", "93.0",
         "Same. Worth filling even though it feels irrelevant."),
    ],
    "logistics": [
        ("notice_period", "Notice period", "", ""),
        ("willing_to_relocate", "Willing to relocate", "Yes", ""),
        ("work_authorization", "Work authorisation", "", ""),
        ("expected_ctc", "Expected CTC", "", ""),
    ],
}

OPTIONAL = {"portfolio", "gender"}


def read() -> str:
    return PROFILE.read_text(encoding="utf-8")


def parsed() -> dict:
    return yaml.safe_load(read()) or {}


def _quote(v: str) -> str:
    v = (v or "").strip()
    if v == "":
        return '""'
    # quote everything. it is never wrong and it stops 9.11, 2027, Yes and No
    # from being read back as floats, ints and booleans.
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_scalar(text: str, section: str, key: str, value: str) -> str:
    lines = text.split("\n")
    inside = False
    for i, ln in enumerate(lines):
        if re.match(rf"^{re.escape(section)}:\s*(#.*)?$", ln):
            inside = True
            continue
        if inside:
            # a new top level key ends the section
            if ln.strip() and not ln[0].isspace() and not ln.lstrip().startswith("#"):
                break
            m = re.match(rf"^(\s+){re.escape(key)}:(\s*)(.*)$", ln)
            if m:
                lines[i] = f"{m.group(1)}{key}: {_quote(value)}"
                return "\n".join(lines)
    raise KeyError(f"{section}.{key} not found in profile.yaml")


def evidence_items() -> list[dict]:
    ev = parsed().get("evidence") or []
    return [{"id": e.get("id", ""), "tags": ", ".join(e.get("tags", []) or []),
             "line": " ".join((e.get("line") or "").split()),
             "placeholder": "FILL" in (e.get("line") or "")} for e in ev]


def set_evidence(text: str, ev_id: str, new_line: str) -> str:
    """Replace one evidence entry's `line:` block, keeping everything else."""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^\s*-\s+id:\s*{re.escape(ev_id)}\s*$", ln):
            start = i
            break
    if start is None:
        raise KeyError(f"evidence id '{ev_id}' not found")

    item_indent = len(lines[start]) - len(lines[start].lstrip())
    line_at = None
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= item_indent:
            break                                   # next item or next section
        if re.match(r"^\s*line:", ln):
            line_at = i
            break
    if line_at is None:
        raise KeyError(f"evidence '{ev_id}' has no line: field")

    key_indent = len(lines[line_at]) - len(lines[line_at].lstrip())
    end = line_at + 1
    while end < len(lines):
        ln = lines[end]
        if not ln.strip():
            end += 1
            continue
        if (len(ln) - len(ln.lstrip())) <= key_indent:
            break
        end += 1

    body = " ".join((new_line or "").split())
    pad = " " * key_indent
    inner = " " * (key_indent + 2)
    if not body:
        block = [f"{pad}line: \"\""]
    else:
        wrapped, cur = [], inner
        for word in body.split():
            if len(cur) + len(word) + 1 > 78 and cur.strip():
                wrapped.append(cur.rstrip())
                cur = inner
            cur += word + " "
        wrapped.append(cur.rstrip())
        block = [f"{pad}line: >"] + wrapped

    return "\n".join(lines[:line_at] + block + lines[end:])


def save(text: str) -> tuple[bool, str]:
    """Validate, back up, write. Same contract as the raw editor."""
    from web import editor
    return editor.save("profile", text)


def blanks() -> list[str]:
    d = parsed()
    out = []
    for section, fields in FIELDS.items():
        for key, label, _, _ in fields:
            if key in OPTIONAL:
                continue
            v = (d.get(section) or {}).get(key)
            if v is None or str(v).strip() == "":
                out.append(f"{section}.{key}")
    return out
