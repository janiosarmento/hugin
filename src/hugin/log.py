"""Error log: tracebacks that the UI only shows as one short line."""

import traceback
from datetime import datetime

from hugin.engines import CONFIG_DIR

LOG_PATH = CONFIG_DIR / "hugin.log"
MAX_LOG_BYTES = 1_000_000


def log_exception(context: str) -> None:
    """Append the traceback of the exception being handled to ~/.hugin/hugin.log.

    Call it from inside an `except` block. Never raises: logging must not
    turn one failure into two. The file is rotated once to `hugin.log.1`
    when it passes 1 MB.
    """
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} {context}\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except OSError:
        pass
