"""Config and profile loading. Everything the system knows lives in yaml."""
from __future__ import annotations
import os
import functools
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    p = ROOT / name
    if not p.exists():
        example = ROOT / name.replace(".yaml", ".example.yaml")
        if example.exists():
            raise SystemExit(
                f"No {name} yet. It is gitignored, so a fresh clone starts without\n"
                f"one. Run:  python run.py init\n"
                f"or:        cp {example.name} {name}")
        raise SystemExit(f"missing {name} at {p}")
    with p.open() as f:
        return yaml.safe_load(f) or {}


@functools.lru_cache(maxsize=1)
def config() -> dict:
    return _load("config.yaml")


@functools.lru_cache(maxsize=1)
def profile() -> dict:
    return _load("profile.yaml")


@functools.lru_cache(maxsize=1)
def resume() -> dict:
    return _load("resume.yaml")


def path(*parts) -> Path:
    return ROOT.joinpath(*parts)


def flat_profile() -> dict:
    """Flatten profile.yaml into the canonical key -> value map the form
    mapper fills from. Nested sections are merged, later keys never clobber
    earlier ones silently: a duplicate key is a config error."""
    out: dict[str, str] = {}
    for section in ("identity", "education", "logistics"):
        for k, v in (profile().get(section) or {}).items():
            if k in out:
                raise SystemExit(f"duplicate profile key: {k}")
            out[k] = "" if v is None else str(v)
    return out
