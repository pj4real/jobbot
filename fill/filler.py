"""Fill a form. Never submit one.

There is no code path in this module that clicks a submit button, and there
should never be. It fills, screenshots, and hands the tab back to you. That
single constraint is what makes the same code safe on LinkedIn and survivable
on Workday, and it is why nothing else here has to be defensive.
"""
from __future__ import annotations
import random
import time
from pathlib import Path

from . import rules
from .harvest import harvest, fingerprint


class Result:
    def __init__(self):
        self.filled: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str]] = []
        self.unmapped: list[dict] = []
        self.needs_human: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []

    def summary(self) -> str:
        return (f"filled {len(self.filled)}, skipped {len(self.skipped)}, "
                f"needs you {len(self.needs_human)}, unmapped {len(self.unmapped)}, "
                f"errors {len(self.errors)}")


def _human_type(locator, text: str) -> None:
    """Type rather than set .value. React and Angular forms ignore direct value
    assignment, and a few portals check for it."""
    locator.click(timeout=5000)
    locator.fill("")
    locator.type(text, delay=random.uniform(18, 55))


def _values(profile_flat: dict, answer_bank: dict, resume_path: str | None) -> dict:
    vals = dict(profile_flat)
    for k, v in (answer_bank or {}).items():
        if v:
            vals[k] = str(v).strip()
    if resume_path:
        vals["resume_file"] = resume_path
    return vals


def fill_page(page, profile_flat: dict, answer_bank: dict,
              resume_path: str | None = None, verbose: bool = True) -> Result:
    fields = harvest(page)
    vals = _values(profile_flat, answer_bank, resume_path)
    res = Result()
    res.fingerprint = fingerprint(fields)
    res.field_count = len(fields)

    for f in fields:
        key = rules.match(f)
        label = f.get("label") or f.get("name") or f.get("selector")

        if key is None:
            res.unmapped.append(f)
            continue
        if key == "__skip__":
            res.skipped.append((label, "matched a do-not-fill rule"))
            continue
        if key in rules.ALWAYS_HUMAN:
            res.needs_human.append((label, "write this one yourself"))
            continue
        if f["type"] == "combobox":
            res.needs_human.append((label, "custom dropdown widget"))
            continue

        value = vals.get(key, "")
        if not value:
            hint = ("write this per company" if key in rules.BANK_KEYS
                    else f"blank '{key}' in profile.yaml")
            res.needs_human.append((label, hint))
            continue

        try:
            frame = page.frames[f.get("frame", 0)]
            loc = frame.locator(f["selector"]).first

            if f["type"] == "file":
                p = Path(value)
                if not p.exists():
                    res.errors.append((label, f"file not found: {value}"))
                    continue
                loc.set_input_files(str(p))
                res.filled.append((label, p.name))

            elif f["tag"] == "select":
                choice = rules.choose_option(f.get("options", []), value)
                if choice is None:
                    res.needs_human.append((label, f"no option matches '{value}'"))
                    continue
                loc.select_option(choice)
                res.filled.append((label, choice))

            elif f["type"] in ("checkbox", "radio"):
                # only tick when the control's own value agrees with yours
                if rules.choose_option([{"value": f.get("value", ""),
                                         "text": f.get("value", "") or label}], value):
                    loc.check(timeout=4000)
                    res.filled.append((label, "checked"))
                else:
                    res.needs_human.append((label, "choose this one yourself"))

            else:
                _human_type(loc, value)
                res.filled.append((label, value[:60]))

            time.sleep(random.uniform(0.05, 0.18))

        except Exception as e:
            res.errors.append((label, str(e).split("\n")[0][:120]))

        if verbose and res.filled and res.filled[-1][0] == label:
            print(f"    {label[:52]:<54} {str(res.filled[-1][1])[:40]}")

    return res


def screenshot(page, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        page.screenshot(path=str(path))
    return path
