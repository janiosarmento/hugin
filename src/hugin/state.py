"""Leitura e escrita do state file de processamento."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

STATE_DIR = Path.home() / ".hugin" / "state"


def _state_path(directory: Path) -> Path:
    dir_hash = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:16]
    return STATE_DIR / f"{dir_hash}.json"


def load_state(directory: Path) -> dict:
    path = _state_path(directory)
    if not path.exists():
        return {"directory": str(directory.resolve()), "posts": {}}

    with open(path) as f:
        return json.load(f)


def save_state(directory: Path, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(directory)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def mark_processed(state: dict, filename: str) -> None:
    state["posts"][filename] = {
        "last_processed": datetime.now().isoformat(timespec="seconds"),
    }


def get_last_processed(state: dict, filename: str) -> datetime | None:
    entry = state["posts"].get(filename)
    if entry is None:
        return None
    return datetime.fromisoformat(entry["last_processed"])
