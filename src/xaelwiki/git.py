"""Git-backed versioning: auto-commit, push, log, revert."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("xaelwiki.git")


class GitError(Exception):
    pass


class GitRepo:
    def __init__(self, root: Path, auto_push: bool = True, auto_pull: bool = True):
        self.root = Path(root)
        self.auto_push = auto_push
        self.auto_pull = auto_pull

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )

    def is_repo(self) -> bool:
        return (self.root / ".git").exists()

    def ensure(self) -> None:
        if not self.is_repo():
            result = self._run("init", "-b", "main")
            if result.returncode:
                raise GitError(result.stderr.strip() or "git init failed")
            self._run("config", "user.name", "xaelwiki")
            self._run("config", "user.email", "xaelwiki@local")
        self.root.joinpath("notes").mkdir(parents=True, exist_ok=True)

    def has_remote(self) -> bool:
        return bool(self._run("remote").stdout.strip())

    def sync_before(self) -> str | None:
        """Pull remote changes before a mutation. Returns a warning or None."""
        if not (self.auto_pull and self.has_remote()):
            return None
        result = self._run("pull", "--rebase")
        if result.returncode:
            return f"pull failed, manual review may be needed: {result.stderr.strip()}"
        return None

    def commit(self, message: str) -> bool:
        if self.root.joinpath("notes").exists():
            add = self._run("add", "-A", "--", "notes")
        else:
            add = self._run("add", "-A")
        if add.returncode:
            raise GitError(add.stderr.strip() or "git add failed")
        result = self._run("commit", "-m", message)
        if result.returncode:
            if "nothing to commit" in (result.stdout + result.stderr):
                return False
            raise GitError(result.stderr.strip() or "git commit failed")
        return True

    def push(self) -> str | None:
        if not (self.auto_push and self.has_remote()):
            return None
        result = self._run("push")
        if result.returncode:
            return f"push failed: {result.stderr.strip()}"
        return None

    def mutate(self, message: str) -> dict:
        self.ensure()
        warning = self.sync_before()
        committed = self.commit(message)
        push_warning = self.push() if committed else None
        return {
            "committed": committed,
            "warning": warning or push_warning,
        }

    def log(self, n: int = 15) -> list[dict]:
        self.ensure()
        result = self._run("log", f"-n{n}", "--format=%h|%s")
        entries = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            short_hash, _, subject = line.partition("|")
            entries.append({"hash": short_hash, "subject": subject})
        return entries

    def revert(self, steps: int = 1) -> dict:
        self.ensure()
        if steps < 1:
            steps = 1
        count_result = self._run("rev-list", "--count", "HEAD")
        try:
            count = int(count_result.stdout.strip() or "0")
        except ValueError:
            count = 0
        if count == 0:
            return {"reverted": [], "warning": "no commits to revert"}
        steps = min(steps, count)
        hashes = self._run("log", f"-n{steps}", "--format=%H").stdout.strip().splitlines()

        # Restore the notes/ tree to the state before the reverted commits, as
        # a single commit. Avoids the conflict-prone sequential `git revert`.
        if steps < count:
            base = self._run("rev-parse", f"HEAD~{steps}").stdout.strip()
            clear = self._run("rm", "-r", "-q", "--ignore-unmatch", "--", "notes")
            if clear.returncode:
                raise GitError(clear.stderr.strip() or "git rm failed")
            restore = self._run("checkout", base, "--", "notes")
            if restore.returncode:
                raise GitError(restore.stderr.strip() or "git checkout failed")
        else:
            clear = self._run("rm", "-r", "-q", "--ignore-unmatch", "--", "notes")
            if clear.returncode:
                raise GitError(clear.stderr.strip() or "git rm failed")

        if not self.commit(f"xael: undo {steps} change(s)"):
            raise GitError("revert produced no change")
        warning = self.push()
        return {
            "reverted": [h[:8] for h in hashes],
            "warning": warning,
        }
