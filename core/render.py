"""LaTeX rendering. resume.yaml subset -> .tex -> .pdf, locally, in about a second.

Jinja's default {{ }} and {% %} collide with LaTeX braces, so the template uses
<< >> for variables and <% %> for blocks. Nothing else about it is unusual.

Engine preference: tectonic first. It is a single binary that fetches the
packages it needs on first run, which beats installing 6 GB of MacTeX for a
one page document. pdflatex is the fallback if you already have a TeX install.
"""
from __future__ import annotations
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import path

TEMPLATES = path("templates")

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_ESC_RE = re.compile("|".join(re.escape(k) for k in _ESCAPES))


def tex_escape(s) -> str:
    """Company names contain & and %. Job titles contain #. Escape or the
    compile dies on the one posting you actually wanted."""
    if s is None:
        return ""
    return _ESC_RE.sub(lambda m: _ESCAPES[m.group()], str(s))


def env() -> Environment:
    e = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        variable_start_string="<<", variable_end_string=">>",
        block_start_string="<%", block_end_string="%>",
        comment_start_string="<#", comment_end_string="#>",
        trim_blocks=False, lstrip_blocks=False,
        undefined=StrictUndefined,
        autoescape=False,
    )
    e.filters["tex"] = tex_escape
    return e


def engine() -> str | None:
    for e in ("tectonic", "pdflatex", "xelatex"):
        if shutil.which(e):
            return e
    return None


def to_tex(context: dict, template: str = "resume.tex.j2") -> str:
    return env().get_template(template).render(**context)


def to_pdf(tex_source: str, out_pdf: Path) -> Path:
    eng = engine()
    if not eng:
        raise SystemExit(
            "no LaTeX engine found. Install one:\n"
            "  brew install tectonic          (30 MB, fetches packages on demand)\n"
            "  brew install --cask basictex   (about 100 MB, full TeX Live subset)"
        )
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        tex = td / "resume.tex"
        tex.write_text(tex_source, encoding="utf-8")

        if eng == "tectonic":
            cmd = [eng, "-X", "compile", "--outdir", str(td), "--keep-logs", str(tex)]
        else:
            # twice, so \hfill positions settle
            cmd = [eng, "-interaction=nonstopmode", "-output-directory", str(td), str(tex)]

        runs = 1 if eng == "tectonic" else 2
        proc = None
        for _ in range(runs):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        produced = td / "resume.pdf"
        if not produced.exists():
            log = (td / "resume.log")
            tail = log.read_text(errors="replace")[-2500:] if log.exists() else ""
            errs = [l for l in tail.splitlines() if l.startswith("!")][:6]
            raise RuntimeError(
                f"{eng} produced no pdf.\n"
                + ("\n".join(errs) if errs else (proc.stderr or proc.stdout)[-1200:])
            )
        shutil.copy(produced, out_pdf)
    return out_pdf


def page_count(pdf: Path) -> int:
    """Cheap page count with no dependency: count /Type /Page objects."""
    data = Path(pdf).read_bytes()
    n = len(re.findall(rb"/Type\s*/Page[^s]", data))
    return max(n, 1)
