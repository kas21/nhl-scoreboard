"""Over-the-air updates from the git remote: check, pull, reinstall, restart.

Runs git in the install directory (must be a checkout). All work happens on a
worker thread; ``state()`` is what the UI polls.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


class Updater:
    def __init__(self, root: Path = ROOT, branch: str = "main", restart: Callable[[], None] | None = None, python: str | None = None) -> None:
        self.root = Path(root)
        self.branch = branch
        self._restart = restart
        self._python = python or sys.executable
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"available": False, "checking": False, "updating": False, "behind": 0,
                                       "current": None, "latest": None, "latest_message": None, "checked_at": None,
                                       "log": [], "error": None, "is_checkout": self.is_checkout}

    # -- helpers --------------------------------------------------------------

    @property
    def is_checkout(self) -> bool:
        return shutil.which("git") is not None and (self.root / ".git").exists()

    def _git(self, *args: str, timeout: int = 120) -> str:
        # the service may run as root over a checkout owned by the login user: tell git that's fine
        r = subprocess.run(["git", "-c", f"safe.directory={self.root}", "-C", str(self.root), *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f"git {' '.join(args)} failed")
        return r.stdout.strip()

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state.update(kw)

    def _logline(self, msg: str) -> None:
        log.info("update: %s", msg)
        with self._lock:
            self._state["log"] = [*self._state["log"][-40:], f"{time.strftime('%H:%M:%S')} {msg}"]

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    # -- operations -----------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """Fetch and compare HEAD to origin/<branch>. Safe to call often."""
        if not self.is_checkout:
            self._set(error="not a git checkout; reinstall with scripts/install.sh to enable updates")
            return self.state()
        self._set(checking=True, error=None)
        try:
            self._git("fetch", "--quiet", "origin", self.branch, timeout=60)
            current = self._git("rev-parse", "--short", "HEAD")
            latest = self._git("rev-parse", "--short", f"origin/{self.branch}")
            behind = int(self._git("rev-list", "--count", f"HEAD..origin/{self.branch}") or 0)
            message = self._git("log", "-1", "--format=%s", f"origin/{self.branch}") if behind else None
            self._set(current=current, latest=latest, behind=behind, available=behind > 0, latest_message=message,
                      checked_at=time.time())
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            self._set(error=str(exc)[:300])
        finally:
            self._set(checking=False)
        return self.state()

    def update(self) -> bool:
        """Start an update on a worker thread. Returns False if one is already running."""
        with self._lock:
            if self._state["updating"]:
                return False
            self._state.update(updating=True, error=None, log=[])
        threading.Thread(target=self._run_update, name="updater", daemon=True).start()
        return True

    def _run_update(self) -> None:
        try:
            self._logline("fetching")
            self._git("fetch", "--quiet", "origin", self.branch, timeout=120)
            before = self._git("rev-parse", "HEAD")
            self._logline("pulling (fast-forward only)")
            self._git("merge", "--ff-only", f"origin/{self.branch}", timeout=120)
            after = self._git("rev-parse", "HEAD")
            changed = self._git("diff", "--name-only", before, after).splitlines() if before != after else []
            if any(f in ("pyproject.toml", "uv.lock") for f in changed) or before == after:
                self._logline("installing dependencies")
                r = subprocess.run([self._python, "-m", "pip", "install", "-q", "-e", str(self.root)], capture_output=True, text=True, timeout=900)
                if r.returncode != 0:
                    raise RuntimeError((r.stderr or r.stdout)[-400:])
            self._logline(f"updated {before[:7]} -> {after[:7]} ({len(changed)} files)")
            self._set(current=after[:7], latest=after[:7], behind=0, available=False)
            if self._restart:
                self._logline("restarting")
                self._restart()
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            self._logline(f"failed: {str(exc)[:300]}")
            self._set(error=str(exc)[:300])
        finally:
            self._set(updating=False)
