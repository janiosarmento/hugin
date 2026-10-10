"""Leitura e escrita do state file de processamento."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from hugin.fsutil import atomic_write_text, quarantine

STATE_DIR = Path.home() / ".hugin" / "state"


def _state_path(directory: Path) -> Path:
    dir_hash = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:16]
    return STATE_DIR / f"{dir_hash}.json"


def load_state(directory: Path) -> dict:
    path = _state_path(directory)
    if not path.exists():
        return {"directory": str(directory.resolve()), "posts": {}}

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        quarantine(path)  # corrupt state must not stop Hugin from starting
        return {"directory": str(directory.resolve()), "posts": {}}


def save_state(directory: Path, state: dict) -> None:
    atomic_write_text(_state_path(directory), json.dumps(state, indent=2))


def mark_processed(state: dict, filename: str) -> None:
    state["posts"][filename] = {
        "last_processed": datetime.now().isoformat(timespec="seconds"),
    }


def get_last_processed(state: dict, filename: str) -> datetime | None:
    entry = state["posts"].get(filename)
    if entry is None:
        return None
    return datetime.fromisoformat(entry["last_processed"])


def get_last_post(state: dict) -> str | None:
    return state.get("last_post")


def set_last_post(state: dict, filename: str) -> None:
    state["last_post"] = filename
