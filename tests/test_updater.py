import subprocess
import time

from scoreboard.web.updater import Updater


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_repos(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git("config", "user.email", "t@t", cwd=work); git("config", "user.name", "t", cwd=work)
    (work / "a.txt").write_text("1"); git("add", "a.txt", cwd=work); git("commit", "-qm", "one", cwd=work); git("push", "-q", "origin", "main", cwd=work)
    install = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(origin), str(install)], check=True)
    return work, install


def test_check_and_update_from_remote(tmp_path):
    work, install = make_repos(tmp_path)
    restarted = []
    up = Updater(root=install, restart=lambda: restarted.append(1), python="true")   # 'true' stands in for pip
    assert up.is_checkout
    st = up.check()
    assert st["available"] is False and st["behind"] == 0 and st["error"] is None
    (work / "a.txt").write_text("2"); git("commit", "-qam", "two", cwd=work); git("push", "-q", "origin", "main", cwd=work)
    st = up.check()
    assert st["available"] and st["behind"] == 1 and st["latest_message"] == "two"
    assert up.update()
    for _ in range(100):
        time.sleep(0.05)
        if not up.state()["updating"]:
            break
    st = up.state()
    assert st["error"] is None and st["available"] is False and restarted == [1]
    assert git("rev-parse", "--short", "HEAD", cwd=install) == st["current"]


def test_not_a_checkout(tmp_path):
    up = Updater(root=tmp_path)
    assert not up.is_checkout and up.check()["error"]


# -- who is allowed to put code in the tree root runs -------------------------
#
# The service runs as root (the matrix driver needs GPIO) and the updater pulls, then
# `pip install -e .` — so whoever can write the checkout can run code as root. The
# installer adds `safe.directory` precisely so root will operate a checkout owned by the
# login user, which is the configuration git refuses by default and for good reason. And
# it is not only the pull: git config in a tree can name commands (core.pager, diff.external,
# core.fsmonitor) that run on *any* git invocation, so even the hourly check is a foothold.

def test_update_refuses_a_checkout_someone_less_privileged_can_write(tmp_path, monkeypatch):
    _, install = make_repos(tmp_path)
    up = Updater(root=install, python="true")
    assert up.check()["error"] is None                       # sane ownership: allowed

    monkeypatch.setattr("os.geteuid", lambda: 0)             # pretend we are the root service
    st = up.check()
    assert st["error"] and "owned by" in st["error"]
    assert up.update() is False
    assert up.state()["error"]


def test_update_refuses_a_world_writable_checkout(tmp_path):
    import os
    import stat as st_mod

    _, install = make_repos(tmp_path)
    up = Updater(root=install, python="true")
    assert up.check()["error"] is None
    os.chmod(install, os.stat(install).st_mode | st_mod.S_IWOTH)
    st = up.check()
    assert st["error"] and "writable" in st["error"]
    assert up.update() is False


def test_a_normal_checkout_is_untouched_by_the_check(tmp_path):
    """The guard must not get in the way of the ordinary case it is wrapped around."""
    work, install = make_repos(tmp_path)
    up = Updater(root=install, restart=lambda: None, python="true")
    (work / "a.txt").write_text("2"); git("commit", "-qam", "two", cwd=work); git("push", "-q", "origin", "main", cwd=work)
    assert up.check()["available"] is True
    assert up.update() is True


def test_an_operator_can_accept_the_risk_on_a_box_they_own(tmp_path, monkeypatch):
    """The bench Pi runs the service as root over a checkout in the login user's home,
    which is the very shape the guard refuses. On a single-user box that "escalation" is
    from someone who already has sudo, so it has to be possible to say so — explicitly,
    with the safe answer as the default."""
    work, install = make_repos(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    assert Updater(root=install, python="true").check()["error"]          # default: refused

    up = Updater(root=install, python="true", restart=lambda: None, allow_unowned=lambda: True)
    (work / "a.txt").write_text("2"); git("commit", "-qam", "two", cwd=work); git("push", "-q", "origin", "main", cwd=work)
    assert up.check()["error"] is None and up.check()["available"] is True
    assert up.update() is True


def test_accepting_an_unowned_checkout_does_not_also_accept_a_writable_one(tmp_path, monkeypatch):
    """The opt-out names one known user. A tree anyone on the box can write is a different
    claim, and saying yes to the first must not quietly say yes to the second."""
    import os
    import stat as st_mod

    _, install = make_repos(tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    up = Updater(root=install, python="true", allow_unowned=lambda: True)
    assert up.check()["error"] is None
    os.chmod(install, os.stat(install).st_mode | st_mod.S_IWOTH)
    assert "writable" in up.check()["error"]
    assert up.update() is False
