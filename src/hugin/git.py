"""Git sync helpers shared by CLI startup and the in-app 'g' action."""

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LOCAL_TIMEOUT = 30  # seconds, for commands that touch no network
NETWORK_TIMEOUT = 120  # pull and push

# New files that must never be committed by an automatic sync
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*")


@dataclass
class SyncResult:
    success: bool
    needs_reload: bool
    lines: list[str] = field(default_factory=list)

    @property
    def output(self) -> str:
        return "\n".join(self.lines)


def _run(directory: Path, cmd: list[str], timeout: int = LOCAL_TIMEOUT) -> tuple[bool, str]:
    """Run git without ever waiting for a person: no credential prompts, a deadline."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
    try:
        r = subprocess.run(
            cmd, cwd=directory, capture_output=True, text=True, env=env,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(cmd[:2])} timed out after {timeout}s (network down, or git wanted credentials?)"
    except FileNotFoundError:
        return False, "git is not installed or not on PATH"
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def pending_changes(directory: Path) -> list[str]:
    """`git status --porcelain` lines (what a sync would commit); [] when clean or on error."""
    ok, out = _run(directory, ["git", "status", "--porcelain"])
    return out.splitlines() if ok and out else []


def secret_files(status_lines: list[str]) -> list[str]:
    """New (untracked or added) files in `status_lines` that look like secrets."""
    found = []
    for line in status_lines:
        code, path = line[:2], line[3:].strip().strip('"')
        if code != "??" and "A" not in code:
            continue
        name = Path(path).name
        if any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS):
            found.append(path)
    return found


def _operation_in_progress(directory: Path) -> str | None:
    """Name of an unfinished rebase/merge, if there is one."""
    for name, label in (
        ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
        ("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"),
    ):
        ok, path = _run(directory, ["git", "rev-parse", "--git-path", name])
        if ok and (directory / path).exists():
            return label
    return None


def git_sync(directory: Path) -> SyncResult:
    """Commit local changes, pull --rebase, push.

    Returns a SyncResult describing what happened.
    needs_reload is True only when the pull brought new remote commits.
    Refuses to start when a rebase/merge is already in progress (it is
    yours, not ours to abort) or when a new file looks like a secret.
    """
    result = SyncResult(success=True, needs_reload=False)

    def run(cmd: list[str], timeout: int = LOCAL_TIMEOUT) -> tuple[bool, str]:
        return _run(directory, cmd, timeout)

    unfinished = _operation_in_progress(directory)
    if unfinished:
        result.lines.append(
            f"A {unfinished} is already in progress in this repository.\n"
            "Finish or abort it yourself, then sync again."
        )
        result.success = False
        return result

    # Step 1: commit local changes if any
    ok, status_out = run(["git", "status", "--porcelain"])
    if not ok:
        result.lines.append(f"git status failed:\n{status_out}")
        result.success = False
        return result

    secrets = secret_files(status_out.splitlines())
    if secrets:
        result.lines.append(
            "Refusing to commit files that look like secrets:\n"
            + "\n".join(f"  {p}" for p in secrets)
            + "\nAdd them to .gitignore (or commit by hand if you really mean it)."
        )
        result.success = False
        return result

    if status_out:
        result.lines.append("Committing local changes...")
        ok, add_out = run(["git", "add", "-A"])
        if not ok:
            result.lines.append(f"git add failed:\n{add_out}")
            result.success = False
            return result

        ok, commit_out = run(
            ["git", "commit", "-m", f"Update posts [{datetime.now():%Y-%m-%d %H:%M}]"]
        )
        if not ok and "nothing to commit" not in commit_out:
            result.lines.append(f"git commit failed:\n{commit_out}")
            result.success = False
            return result

        result.lines.append(commit_out or "Nothing new to commit.")
    else:
        result.lines.append("Working tree clean — no local changes to commit.")

    # Step 2: pull --rebase
    result.lines.append("\nPulling (rebase)...")
    _, remote_before = run(["git", "rev-parse", "@{upstream}"])
    ok, pull_out = run(["git", "pull", "--rebase"], NETWORK_TIMEOUT)
    result.lines.append(pull_out)
    if not ok:
        # Only undo a rebase that this pull started; nothing was in progress before
        if _operation_in_progress(directory) == "rebase":
            run(["git", "rebase", "--abort"])
        result.success = False
        return result

    _, remote_after = run(["git", "rev-parse", "@{upstream}"])
    result.needs_reload = remote_before != remote_after

    # Step 3: push
    result.lines.append("\nPushing...")
    ok, push_out = run(["git", "push"], NETWORK_TIMEOUT)
    result.lines.append(push_out)
    if not ok:
        result.success = False

    return result
