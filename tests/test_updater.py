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
