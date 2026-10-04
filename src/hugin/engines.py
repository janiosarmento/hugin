"""Leitura e gerenciamento do cadastro de motores de AI."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

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

# Not an engine: names the Jano key that holds the echo.fulcrum.inc API key.
# Lives in engines.toml because the key is machine-wide, not per project.
FULCRUM_ECHO_SECTION = "fulcrum_echo"
DEFAULT_FULCRUM_ECHO_SECRET = "fulcrum_echo.key"
FULCRUM_ECHO_BLOCK = f"""\
[{FULCRUM_ECHO_SECTION}]
# Jano key holding the echo.fulcrum.inc API key (https://echo.fulcrum.inc/dev/)
secret = "{DEFAULT_FULCRUM_ECHO_SECRET}"
"""


@dataclass
class Engine:
    id: str
    url: str
    model: str
    timeout: int
    api_key: str | None

    @property
    def is_local(self) -> bool:
        from urllib.parse import urlparse
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


def _ensure_engines_file() -> Path:
    if not ENGINES_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ENGINES_FILE.write_text(DEFAULT_ENGINES + "\n" + FULCRUM_ECHO_BLOCK)
    else:
        text = ENGINES_FILE.read_text()
        if f"[{FULCRUM_ECHO_SECTION}]" not in text:
            sep = "" if text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
            ENGINES_FILE.write_text(text + sep + FULCRUM_ECHO_BLOCK)
    return ENGINES_FILE


def load_fulcrum_echo_secret() -> str:
    """Name of the Jano key that holds the echo.fulcrum.inc API key."""
    with open(_ensure_engines_file(), "rb") as f:
        data = tomllib.load(f)
    return data.get(FULCRUM_ECHO_SECTION, {}).get("secret") or DEFAULT_FULCRUM_ECHO_SECRET


def _get_api_key(engine_id: str, secret: str | None = None) -> str | None:
    """Retrieve API key from the secrets vault.

    Looks up `secret` path (default: "{engine_id}.api_key") via secrets-resolver.
    Returns None if the secret doesn't exist or the vault is not configured.
    """
    from secrets_resolver import get_secret
    from secrets_resolver.exceptions import SecretsResolverError

    path = secret or f"{engine_id}.api_key"
    try:
        return get_secret(path)
    except SecretsResolverError:
        return None


def load_engines() -> list[Engine]:
    path = _ensure_engines_file()
    with open(path, "rb") as f:
        data = tomllib.load(f)

    engines = []
    for engine_id, config in data.items():
        if engine_id == FULCRUM_ECHO_SECTION:
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
    import json
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LAST_ENGINE_FILE.write_text(json.dumps({
        "engine": engine.id,
        "model": engine.model,
    }))


def _load_last_engine() -> tuple[str, str] | None:
    import json
    if not LAST_ENGINE_FILE.exists():
        return None
    try:
        data = json.loads(LAST_ENGINE_FILE.read_text())
        return data["engine"], data["model"]
    except (json.JSONDecodeError, KeyError):
        return None


def get_engine(engine_id: str | None = None) -> Engine:
    engines = load_engines()
    if not engines:
        raise SystemExit("Nenhum motor configurado em ~/.hugin/engines.toml")

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
    raise SystemExit(f"Motor '{engine_id}' não encontrado. Disponíveis: {available}")
