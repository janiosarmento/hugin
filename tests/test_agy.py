"""The agy transport: a fake `agy` executable stands in for the real CLI."""

import asyncio
import json
import os
import sys

import httpx
import pytest

import hugin.engines as engines_mod
import hugin.llm as llm
from hugin import agy
from hugin.drafting import LLM_ERRORS
from hugin.engines import TRANSPORT_AGY, Engine, load_engines

FAKE = """\
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_AGY_LOG"], "w") as f:
    json.dump({"args": args, "cwd": os.getcwd(), "files": os.listdir(".")}, f)
mode = os.environ.get("FAKE_AGY_MODE", "ok")
if args[:1] == ["models"]:
    print("Fetching available models...")
    print("gemini-4-neon-medium\\tGemini 4 Neon (Medium)")
    print("gemini-3.8-flash-low\\tGemini 3.8 Flash (Low)")
    sys.exit(0)
if mode == "hang":
    time.sleep(60)
if mode == "crash":
    print("panic: something broke", file=sys.stderr)
    sys.exit(2)
if mode == "error":
    print(json.dumps({"status": "ERROR", "response": "", "finished_reason": "error",
                      "error": "invalid model selection\\nAvailable models: ..."}))
    sys.exit(1)
prompt = next(a for a in args if a.startswith("--print="))[len("--print="):]
print(json.dumps({"status": "SUCCESS", "response": os.environ.get("FAKE_AGY_REPLY", "echo: " + prompt)}))
"""


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    script = tmp_path / "agy"
    script.write_text(f"#!{sys.executable}\n{FAKE}")
    script.chmod(0o755)
    log = tmp_path / "agy-call.json"
    monkeypatch.setattr(agy, "AGY", str(script))
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    return lambda: json.loads(log.read_text())


AGY_ENGINE = Engine("gemini-sub", "", "gemini-4-neon-medium", 5, None, transport=TRANSPORT_AGY)


def _ask(engine=AGY_ENGINE, prompt="hi", system=None):
    return asyncio.run(llm.call_llm(engine, prompt, system))


def test_call_sends_system_then_prompt_sandboxed_in_an_empty_dir(fake_agy):
    assert _ask(prompt="-starts with a dash", system="Be brief.") == "echo: Be brief.\n\n-starts with a dash"
    call = fake_agy()
    assert call["files"] == []  # a fresh empty working directory
    assert not os.path.exists(call["cwd"])  # removed afterwards
    args = call["args"]
    assert args[0] == "--print=Be brief.\n\n-starts with a dash"
    assert args[args.index("--model") + 1] == "gemini-4-neon-medium"
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--print-timeout") + 1] == "5s"
    assert "--sandbox" in args and "--disable-slash-commands" in args
    assert not any("skip-permissions" in a for a in args)


def test_call_llm_post_processing_applies_to_agy(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_REPLY", "<think>plan</think>\nAnswer")
    assert _ask() == "Answer"
    monkeypatch.setenv("FAKE_AGY_REPLY", " ".join(["same"] * 30))
    with pytest.raises(ValueError, match="repetition"):
        _ask()


@pytest.mark.parametrize("mode, message", [
    ("error", r"agy failed \(error\): invalid model selection$"),
    ("crash", r"agy failed: panic: something broke"),
])
def test_agy_failures_are_llm_errors(fake_agy, monkeypatch, mode, message):
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    with pytest.raises(LLM_ERRORS, match=message):
        _ask()


def test_hung_agy_is_killed(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "hang")
    monkeypatch.setattr(agy, "KILL_GRACE", 0)
    engine = Engine("g", "", "m", 1, None, transport=TRANSPORT_AGY)
    with pytest.raises(LLM_ERRORS, match="did not answer in 1s"):
        _ask(engine)


def test_missing_agy_and_oversized_prompts(monkeypatch):
    monkeypatch.setattr(agy, "AGY", "/nonexistent/agy")
    with pytest.raises(LLM_ERRORS, match="cannot run"):
        _ask()
    with pytest.raises(LLM_ERRORS, match="prompt too long"):
        _ask(prompt="x" * (agy.MAX_PROMPT_BYTES + 1))


def test_parse_output_rejects_unknown_shapes():
    with pytest.raises(agy.AgyError, match="no text"):
        agy.parse_output(0, json.dumps({"status": "SUCCESS"}), "")
    with pytest.raises(agy.AgyError, match="exit code 3"):
        agy.parse_output(3, "", "")
    with pytest.raises(agy.AgyError, match="print_timeout"):
        agy.parse_output(1, json.dumps({"status": "ERROR", "finished_reason": "print_timeout",
                                        "error": "print timeout after 2s"}), "")


def test_list_models(fake_agy):
    assert asyncio.run(agy.list_models()) == ["gemini-4-neon-medium", "gemini-3.8-flash-low"]
    assert fake_agy()["args"] == ["models"]


def test_api_transport_still_goes_over_http(monkeypatch):
    real = httpx.AsyncClient
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "<think>x</think>Hi"}}]})

    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(agy, "AGY", "/nonexistent/agy")  # never used
    assert _ask(Engine("t", "http://x/v1", "m", 5, None)) == "Hi"
    assert seen["url"] == "http://x/v1/chat/completions"


# --- configuration ----------------------------------------------------------


def _engines(monkeypatch, tmp_path, content):
    path = tmp_path / "engines.toml"
    path.write_text(content)
    monkeypatch.setattr(engines_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(engines_mod, "ENGINES_FILE", path)
    monkeypatch.setattr(engines_mod, "get_secret", lambda p: None)
    return {e.id: e for e in load_engines()}


def test_agy_engine_needs_no_url_or_key(monkeypatch, tmp_path, fake_agy):
    found = _engines(monkeypatch, tmp_path, (
        '[api]\nurl = "https://api.example/v1"\nmodel = "m"\n\n'
        '[gemini-sub]\ntransport = "agy"\nmodel = "gemini-4-neon-medium"\n\n'
        '[quick]\ntransport = "agy"\nmodel = "gemini-3.8-flash-low"\ntimeout = 90\n\n'
        '[odd]\ntransport = "carrier-pigeon"\nurl = "http://x/v1"\nmodel = "m"\n'
    ))
    assert list(found) == ["api", "gemini-sub", "quick"]
    sub = found["gemini-sub"]
    assert (sub.transport, sub.url, sub.api_key) == (TRANSPORT_AGY, "", None)
    assert sub.timeout == engines_mod.DEFAULT_AGY_TIMEOUT
    assert found["quick"].timeout == 90
    assert found["api"].transport == "api"
    assert sub.available  # the fake agy is "installed"
    assert not found["api"].available and "set-secret api.api_key" in found["api"].missing


def test_agy_engine_unavailable_without_the_cli(monkeypatch):
    monkeypatch.setattr(agy, "AGY", "/nonexistent/agy")
    assert not AGY_ENGINE.available
    assert "agy" in AGY_ENGINE.missing
