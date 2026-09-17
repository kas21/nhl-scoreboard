"""Over-the-air updates from the git remote: check, pull, reinstall, restart, roll back.

Runs git in the install directory (must be a checkout). All work happens on a
worker thread; ``state()`` is what the UI polls.

The commit an update moved away from is written to the data dir before the merge, so a
bad release has a way back: an install that fails puts the tree back there itself, and
``rollback()`` does it on request from the dashboard for a release that installed fine
but does not work.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..imagecache import DATA_ROOT

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_FILE = "update-previous"       # in the data dir: "<sha> <epoch>" of the commit the last update left


class Updater:
    def __init__(self, root: Path = ROOT, branch: str = "main", restart: Callable[[], None] | None = None,
                 python: str | None = None, allow_unowned: Callable[[], bool] = lambda: False,
                 state_dir: Path = DATA_ROOT) -> None:
        self.root = Path(root)
        self.branch = branch
        self._restart = restart
        self._python = python or sys.executable
        self._allow_unowned = allow_unowned
        self._state_dir = Path(state_dir)
        self._lock = threading.Lock()           # around _state
        self._git_lock = threading.Lock()       # one git operation at a time: check() must not fetch under a merge
        self._state: dict[str, Any] = {"available": False, "checking": False, "updating": False, "behind": 0,
                                       "current": None, "latest": None, "latest_message": None, "checked_at": None,
                                       "log": [], "error": None, "is_checkout": self.is_checkout,
                                       "previous": None, "previous_at": None}
        if self.is_checkout:
            try:
                self._state["current"] = self._git("rev-parse", "--short", "HEAD", timeout=10)
            except (RuntimeError, subprocess.SubprocessError, OSError):
                pass
            self._load_previous()

    # -- the way back -----------------------------------------------------------

    def _load_previous(self) -> None:
        try:
            sha, _, at = (self._state_dir / PREVIOUS_FILE).read_text().strip().partition(" ")
        except OSError:
            return
        if sha and self._commit_exists(sha) and sha != self._git_or_none("rev-parse", "HEAD"):
            self._set(previous=sha[:7], previous_at=float(at) if at else None)
            self._previous_full = sha

    _previous_full: str | None = None

    def _remember_previous(self, sha: str) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            (self._state_dir / PREVIOUS_FILE).write_text(f"{sha} {time.time():.0f}\n")
        except OSError as exc:
            log.warning("could not record the previous commit for rollback: %s", exc)
        self._previous_full = sha
        self._set(previous=sha[:7], previous_at=time.time())

    def _commit_exists(self, sha: str) -> bool:
        try:
            self._git("cat-file", "-e", f"{sha}^{{commit}}", timeout=10)
        except (RuntimeError, subprocess.SubprocessError, OSError):
            return False
        return True

    def _git_or_none(self, *args: str) -> str | None:
        try:
            return self._git(*args, timeout=10)
        except (RuntimeError, subprocess.SubprocessError, OSError):
            return None

    # -- helpers --------------------------------------------------------------

    @property
    def is_checkout(self) -> bool:
        return shutil.which("git") is not None and (self.root / ".git").exists()

    def unsafe_reason(self) -> str | None:
        """Why this checkout must not be pulled from, or None if it is fine to proceed.

        The service runs as root because the matrix driver needs GPIO, and updating means
        `git merge` followed by `pip install -e .` — so anyone who can write this tree can
        run code as root. The installer adds `safe.directory` to make root operate a
        checkout owned by the login user, which is exactly the arrangement git refuses by
        default; this puts the refusal back, on the operation that actually executes code
        rather than on every git command.

        The check covers `check()` too, not just `update()`: a repository's own config can
        name commands (`core.pager`, `diff.external`, `core.fsmonitor`) that git runs for
        *any* invocation, so a hostile tree is a foothold even when we only mean to fetch.
        """
        try:
            info = os.stat(self.root)
        except OSError as exc:
            return f"cannot inspect {self.root}: {exc}"
        euid = os.geteuid()
        # allow_unowned waives *this* check only: the operator is saying they are that other
        # user. It deliberately does not waive the writability check below, which is about
        # anyone on the box rather than one known account.
        if info.st_uid != euid and not self._allow_unowned():
            return (f"refusing to update: {self.root} is owned by uid {info.st_uid} but the "
                    f"service runs as uid {euid}, so that user could run code as this one. "
                    f"Chown the checkout, or set web.allow_unowned_checkout if that user is you")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return (f"refusing to update: {self.root} is group- or world-writable, so anyone "
                    f"on the box could run code as uid {euid}")
        return None

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
        unsafe = self.unsafe_reason()
        if unsafe:
            log.error("%s", unsafe)
            self._set(error=unsafe, available=False)
            return self.state()
        if not self._git_lock.acquire(blocking=False):      # an update is in progress: its fetch is fresher than ours
            return self.state()
        try:
            self._set(checking=True, error=None)
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
            self._git_lock.release()
        return self.state()

    def update(self) -> bool:
        """Start an update on a worker thread.

        Returns False if one is already running, or if the checkout is not one this
        process may safely execute from (see ``unsafe_reason``).
        """
        unsafe = self.unsafe_reason() if self.is_checkout else "not a git checkout"
        if unsafe:
            log.error("%s", unsafe)
            self._set(error=unsafe, updating=False)
            return False
        return self._start_worker(self._run_update)

    def rollback(self) -> bool:
        """Put the tree back on the commit the last update moved away from, reinstall, restart.

        For a release that installed fine but does not work on this box. The commit we
        leave becomes the new "previous", so the same button rolls forward again.
        """
        unsafe = self.unsafe_reason() if self.is_checkout else "not a git checkout"
        if unsafe:
            log.error("%s", unsafe)
            self._set(error=unsafe, updating=False)
            return False
        if not self._previous_full or not self._commit_exists(self._previous_full):
            self._set(error="nothing to roll back to")
            return False
        return self._start_worker(self._run_rollback)

    def _start_worker(self, target: Callable[[], None]) -> bool:
        with self._lock:
            if self._state["updating"]:
                return False
            self._state.update(updating=True, error=None, log=[])
        threading.Thread(target=target, name="updater", daemon=True).start()
        return True

    def _run_update(self) -> None:
        with self._git_lock:
            before = None
            try:
                self._logline("fetching")
                self._git("fetch", "--quiet", "origin", self.branch, timeout=120)
                before = self._git("rev-parse", "HEAD")
                after_remote = self._git("rev-parse", f"origin/{self.branch}")
                if after_remote != before:
                    self._remember_previous(before)          # written *before* the tree moves: the way back survives a crash
                self._logline("pulling (fast-forward only)")
                self._git("merge", "--ff-only", f"origin/{self.branch}", timeout=120)
                after = self._git("rev-parse", "HEAD")
                changed = self._git("diff", "--name-only", before, after).splitlines() if before != after else []
                if any(f in ("pyproject.toml", "uv.lock") for f in changed) or before == after:
                    self._install()
                self._logline(f"updated {before[:7]} -> {after[:7]} ({len(changed)} files)")
                self._set(current=after[:7], latest=after[:7], behind=0, available=False)
                self._finish()
            except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
                self._fail(exc, before)

    def _run_rollback(self) -> None:
        with self._git_lock:
            before = None
            target = self._previous_full or ""
            try:
                before = self._git("rev-parse", "HEAD")
                self._logline(f"rolling back {before[:7]} -> {target[:7]}")
                self._git("reset", "--hard", target, timeout=120)
                self._remember_previous(before)              # so the same button rolls forward again
                self._install()
                self._set(current=target[:7], available=True, behind=0)
                self._finish()
            except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
                self._fail(exc, None)

    def _install(self) -> None:
        """Reinstall the package (and its dependencies) into the service's venv.

        ``pip`` through the venv's Python is the installer scripts/install.sh sets up. A
        venv made by ``uv sync`` has no pip; there, ``uv sync --frozen`` is what honours
        uv.lock (pip re-resolves from pyproject's ranges and never reads the lock).
        """
        self._logline("installing dependencies")
        r = subprocess.run([self._python, "-m", "pip", "install", "-q", "-e", str(self.root)],
                           capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            return
        no_pip = "No module named pip" in (r.stderr or "")
        uv = shutil.which("uv")
        if no_pip and uv:
            self._logline("no pip in the venv; using uv sync")
            r = subprocess.run([uv, "sync", "--frozen", "--extra", "pi"], cwd=str(self.root),
                               capture_output=True, text=True, timeout=900)
            if r.returncode == 0:
                return
        raise RuntimeError((r.stderr or r.stdout)[-400:] or "install failed")

    def _finish(self) -> None:
        self._set(updating=False)
        if self._restart:
            self._logline("restarting")
            self._restart()

    def _fail(self, exc: Exception, before: str | None) -> None:
        """An update that failed after the tree moved is put back where it was: a half-applied
        release (new code, old dependencies) would otherwise be what the next restart runs."""
        self._logline(f"failed: {str(exc)[:300]}")
        error = str(exc)[:300]
        if before and self._git_or_none("rev-parse", "HEAD") != before:
            try:
                self._git("reset", "--hard", before, timeout=120)
                self._logline(f"rolled back to {before[:7]}")
                error += f" (rolled back to {before[:7]})"
                self._set(current=before[:7], available=True)
            except (RuntimeError, subprocess.SubprocessError, OSError) as exc2:
                self._logline(f"rollback failed too: {str(exc2)[:200]}")
        self._set(error=error, updating=False)
