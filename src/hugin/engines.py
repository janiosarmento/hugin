"""Reading and managing the registry of AI engines."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hugin.fsutil import atomic_write_text
from secrets_resolver import get_secret
from secrets_resolver.exceptions import SecretsResolverError
import json
from urllib.parse import urlparse

CONFIG_DIR = Path.home() / ".hugin"
ENGINES_FILE = CONFIG_DIR / "engines.toml"

DEFAULT_ENGINES = """\
[openai]
url = "https://api.openai.com/v1"
model = "gpt-4o"
timeout = 30

[deepseek]
url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
timeout = 30

[groq]
url = "https://api.groq.com/openai/v1"
model = "llama-3.3-70b-versatile"
timeout = 30

[local]
url = "http://localhost:5555/v1"
model = "apple-ai"
timeout = 120
"""

DEFAULT_TIMEOUT = 30

# Section once written by Hugin for a retired writing service; stripped from
# existing files on load.
RETIRED_SECTION = "fulcrum_echo"

# A TOML table header line, e.g. `[name]` or `[[name]]  # comment`
TABLE_HEADER = re.compile(r'^\s*\[\[?([A-Za-z0-9_.\-" ]+)\]\]?\s*(?:#.*)?$')


class MaskedKey(str):
    """API key that behaves as a plain str but never prints in full.

    repr()/pretty-printers (tracebacks, logs) show only a short prefix.
    """

    def __repr__(self) -> str:
        shown = min(8, len(self) // 2)
        return f"'{self[:shown]}…({len(self)} chars)'"


@dataclass
class Engine:
    id: str
    url: str
    model: str
    timeout: int
    api_key: str | None

    @property
    def is_local(self) -> bool:
        host = urlparse(self.url).hostname or ""
        if host in ("localhost", "127.0.0.1"):
            return True
        # Redes privadas (RFC 1918)
        if host.startswith(("192.168.", "10.", "172.16.", "172.17.",
                            "172.18.", "172.19.", "172.2", "172.30.",
                            "172.31.")):
            return True
        return False

    @property
    def available(self) -> bool:
        return self.is_local or bool(self.api_key)


def strip_section(text: str, name: str) -> str:
    """`text` without the [name] table (header to the next table or EOF)."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        header = TABLE_HEADER.match(line)
        if header:
            skipping = header.group(1).strip() == name
        if not skipping:
            out.append(line)
    return "".join(out).rstrip("\n") + "\n"


def _ensure_engines_file() -> Path:
    if not ENGINES_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text(ENGINES_FILE, DEFAULT_ENGINES)
    else:
        text = ENGINES_FILE.read_text(encoding="utf-8")
        if f"[{RETIRED_SECTION}]" in text:
            atomic_write_text(ENGINES_FILE, strip_section(text, RETIRED_SECTION))
    return ENGINES_FILE


def _get_api_key(engine_id: str, secret: str | None = None) -> str | None:
    """Retrieve API key from the secrets vault.

    Looks up `secret` path (default: "{engine_id}.api_key") via secrets-resolver.
    Returns None if the secret doesn't exist or the vault is not configured.
    """

    path = secret or f"{engine_id}.api_key"
    try:
        value = get_secret(path)
        return MaskedKey(value) if value else value
    except SecretsResolverError:
        return None


def load_engines() -> list[Engine]:
    path = _ensure_engines_file()
    with open(path, "rb") as f:
        data = tomllib.load(f)

    engines = []
    for engine_id, config in data.items():
        # Sections without a url are not engines.
        if "url" not in config:
            continue
        engines.append(Engine(
            id=engine_id,
            url=config["url"],
            model=config["model"],
            timeout=config.get("timeout", DEFAULT_TIMEOUT),
            api_key=_get_api_key(engine_id, config.get("secret")),
        ))
    return engines


LAST_ENGINE_FILE = CONFIG_DIR / "last_engine.json"


def save_last_engine(engine: Engine) -> None:
    atomic_write_text(LAST_ENGINE_FILE, json.dumps({
        "engine": engine.id,
        "model": engine.model,
    }))


def _load_last_engine() -> tuple[str, str] | None:
    if not LAST_ENGINE_FILE.exists():
        return None
    try:
        data = json.loads(LAST_ENGINE_FILE.read_text(encoding="utf-8"))
        return data["engine"], data["model"]
    except (json.JSONDecodeError, KeyError):
        return None


def get_engine(engine_id: str | None = None) -> Engine:
    engines = load_engines()
    if not engines:
        raise SystemExit("No engine configured in ~/.hugin/engines.toml")

    if engine_id is None:
        # Try last used engine
        last = _load_last_engine()
        if last:
            last_id, last_model = last
            for engine in engines:
                if engine.id == last_id:
                    engine.model = last_model
                    return engine
        return engines[0]

    for engine in engines:
        if engine.id == engine_id:
            return engine

    available = ", ".join(e.id for e in engines)
    raise SystemExit(f"Engine '{engine_id}' not found. Available: {available}")
