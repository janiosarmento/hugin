"""Git sync helpers shared by CLI startup and the in-app 'g' action."""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class SyncResult:
    success: bool
    needs_reload: bool
    lines: list[str] = field(default_factory=list)

    @property
    def output(self) -> str:
        return "\n".join(self.lines)


def git_sync(directory: Path) -> SyncResult:
    """Commit local changes, pull --rebase, push.

    Returns a SyncResult describing what happened.
    needs_reload is True only when the pull brought new remote commits.
    """
    result = SyncResult(success=True, needs_reload=False)

    def run(cmd: list[str]) -> tuple[bool, str]:
        r = subprocess.run(cmd, cwd=directory, capture_output=True, text=True)
        return r.returncode == 0, (r.stdout + r.stderr).strip()

    # Step 1: commit local changes if any
    ok, status_out = run(["git", "status", "--porcelain"])
    if not ok:
        result.lines.append(f"git status failed:\n{status_out}")
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
    ok, pull_out = run(["git", "pull", "--rebase"])
    result.lines.append(pull_out)
    if not ok:
        run(["git", "rebase", "--abort"])
        result.success = False
        return result

    _, remote_after = run(["git", "rev-parse", "@{upstream}"])
    result.needs_reload = remote_before != remote_after

    # Step 3: push
    result.lines.append("\nPushing...")
    ok, push_out = run(["git", "push"])
    result.lines.append(push_out)
    if not ok:
        result.success = False

    return result
