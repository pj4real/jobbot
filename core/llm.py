"""LLM access via the Claude Code CLI.

Your subscription has no API key, but the CLI authenticates against your plan
and runs headless, so every call here is a subprocess. That has two design
consequences baked into this module:

  1. Calls are slow, so callers batch (one call for twenty jobs, not twenty).
  2. Calls draw on your plan limits, so every response is cached by a hash of
     (task, prompt). Rerunning a stage costs nothing the second time.
"""
from __future__ import annotations
import subprocess
import hashlib
import json
import shutil
import re
import os
from .db import tx

CLI = os.environ.get("JOBBOT_CLAUDE_BIN", "claude")
TIMEOUT = int(os.environ.get("JOBBOT_LLM_TIMEOUT", "180"))


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which(CLI) is not None


def _key(task: str, prompt: str) -> str:
    return hashlib.sha256(f"{task}\x00{prompt}".encode()).hexdigest()


def _cached(key: str):
    with tx() as c:
        r = c.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return r["response"] if r else None


def _store(key: str, task: str, response: str) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO llm_cache (key, task, response) VALUES (?,?,?)",
            (key, task, response),
        )


def ask(prompt: str, task: str = "generic", use_cache: bool = True) -> str:
    """Run one prompt through the CLI. Returns the assistant text."""
    key = _key(task, prompt)
    if use_cache:
        hit = _cached(key)
        if hit is not None:
            return hit

    if not available():
        raise LLMError(
            f"'{CLI}' not on PATH. Install Claude Code, or set JOBBOT_CLAUDE_BIN "
            f"to its full path."
        )

    def _run(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude timed out after {TIMEOUT}s") from e

    proc = _run([CLI, "-p", prompt, "--output-format", "json"])
    if proc.returncode != 0:
        # some builds and sandboxes only accept the bare -p form
        proc = _run([CLI, "-p", prompt])

    if proc.returncode != 0:
        raise LLMError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:400]}")

    out = proc.stdout.strip()
    # --output-format json wraps the reply; older builds print plain text.
    try:
        payload = json.loads(out)
        text = payload.get("result", out) if isinstance(payload, dict) else out
    except json.JSONDecodeError:
        text = out

    if not text.strip():
        raise LLMError("claude returned an empty response")

    _store(key, task, text)
    return text


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def ask_json(prompt: str, task: str = "generic", use_cache: bool = True,
             retries: int = 1):
    """Same, but insists on parseable JSON. The model is told the contract in
    the prompt; this only handles the usual wrapping and one reprompt."""
    hint = "\n\nRespond with JSON only. No prose, no markdown fence."
    for attempt in range(retries + 1):
        raw = ask(prompt + hint, task=task, use_cache=use_cache and attempt == 0)
        candidate = raw.strip()
        m = _FENCE.search(candidate)
        if m:
            candidate = m.group(1).strip()
        else:
            # tolerate leading prose before the first { or [
            start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0]
                        or [-1])
            if start > 0:
                candidate = candidate[start:]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            if attempt >= retries:
                raise LLMError(f"could not parse JSON from: {raw[:300]}")
            use_cache = False
    raise LLMError("unreachable")


def cache_stats() -> dict:
    with tx() as c:
        rows = c.execute(
            "SELECT task, COUNT(*) n FROM llm_cache GROUP BY task ORDER BY n DESC"
        ).fetchall()
        return {r["task"]: r["n"] for r in rows}
