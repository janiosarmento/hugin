"""The Antigravity CLI (`agy`) as an LLM transport.

An engine with `transport = "agy"` in engines.toml sends each prompt through
the locally installed, logged-in `agy` instead of an HTTP API, so it runs on
the user's Google subscription. llm.call_llm() is the only caller of call();
nothing else in Hugin knows which transport an engine uses.

`agy` is an agent with tools, and prompts carry web pages and posts, so every
call runs in a fresh empty directory, sandboxed, with slash commands off, and
never with permissions skipped.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile

AGY = "agy"
# macOS caps argv plus the environment at 1 MiB; agy has no stdin input
MAX_PROMPT_BYTES = 900_000
# Kill the process this long after agy's own --print-timeout should have hit
KILL_GRACE = 30


class AgyError(ValueError):
    """agy failed or answered in an unknown shape.

    A ValueError, so every `except LLM_ERRORS` handles it like an API failure.
    """


def installed() -> bool:
    return shutil.which(AGY) is not None


async def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) of `agy args`, run in a fresh empty directory."""
    with tempfile.TemporaryDirectory(prefix="hugin-agy-") as cwd:
        try:
            proc = await asyncio.create_subprocess_exec(
                AGY, *args, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            raise AgyError(f"cannot run {AGY}: {e.strerror or e}") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _last_line(text: str) -> str:
    return next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")


def parse_output(code: int, out: str, err: str) -> str:
    """The response text of `agy --output-format json`, or AgyError."""
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        detail = _last_line(err) or _last_line(out) or f"exit code {code}"
        raise AgyError(f"{AGY} failed: {detail[:300]}")
    if data.get("status") != "SUCCESS" or code != 0:
        reason = data.get("finished_reason") or data.get("status") or f"exit code {code}"
        message = (data.get("error") or "").strip().splitlines()
        raise AgyError(f"{AGY} failed ({reason}): {message[0] if message else 'no details'}"[:300])
    text = data.get("response")
    if not isinstance(text, str):
        raise AgyError(f"{AGY} returned no text response")
    return text


async def call(model: str, prompt: str, system: str | None, timeout: int) -> str:
    """The model's answer to `prompt`. agy takes no system prompt: it goes first."""
    text = f"{system.strip()}\n\n{prompt}" if system else prompt
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise AgyError(f"prompt too long for {AGY} ({len(text):,} characters)")
    args = [
        f"--print={text}", "--model", model, "--output-format", "json",
        "--sandbox", "--disable-slash-commands", "--print-timeout", f"{timeout}s",
    ]
    try:
        code, out, err = await _run(args, timeout + KILL_GRACE)
    except asyncio.TimeoutError as e:
        raise AgyError(f"{AGY} did not answer in {timeout}s") from e
    return parse_output(code, out, err)


def parse_models(out: str) -> list[str]:
    """Model ids from `agy models` ("id<TAB>label" lines)."""
    return [ln.split("\t", 1)[0].strip() for ln in out.splitlines()
            if "\t" in ln and ln.split("\t", 1)[0].strip()]


async def list_models(timeout: int = 60) -> list[str]:
    try:
        code, out, err = await _run(["models"], timeout)
    except asyncio.TimeoutError as e:
        raise AgyError(f"{AGY} models did not answer in {timeout}s") from e
    if code != 0:
        raise AgyError(f"{AGY} models failed: {(_last_line(err) or _last_line(out))[:300]}")
    return parse_models(out)
