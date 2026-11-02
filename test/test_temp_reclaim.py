"""``temp_reclaim``: a temp tree is removed only when every check proves it may go.

Every tree lives under the test's own ``tmp_path``, which stands in for ``/tmp``
through the ``roots`` argument, and the process table is injected, so nothing
here reads or touches the real system temp roots. The one test that reads the
real ``/proc`` looks only for a child it started and reaps itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kiro_crew import temp_reclaim as tr

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="temp_reclaim is Linux only"
)

NOW = time.time()
QUIET = tr.ProcessView(uses=(), sockets=(), unreadable=0)


def _age(tree: Path, seconds: float = 2 * tr.IDLE_SECONDS) -> None:
    """Backdate every entry under *tree*, *tree* included, by *seconds*."""
    stamp = NOW - seconds
    for dirpath, dirnames, filenames in os.walk(tree):
        for name in dirnames + filenames:
            os.utime(os.path.join(dirpath, name), (stamp, stamp), follow_symlinks=False)
    os.utime(tree, (stamp, stamp))


def _home(root: Path, name: str = "kc-home") -> Path:
    tree = root / name
    (tree / ".cache" / "kirocrew" / "pycache").mkdir(parents=True)
    (tree / ".cache" / "kirocrew" / "pycache" / "x.pyc").write_bytes(b"x")
    _age(tree)
    return tree


def _run(path: Path | str, root: Path, view: tr.ProcessView = QUIET, **kw) -> tr.Outcome:
    """One check-and-remove whose removal re-reads the same injected *view*."""
    kw.setdefault("reread", lambda: view)
    return tr.reclaim_one(str(path), view, now=NOW, roots=(str(root),), **kw)


def test_an_idle_unused_tree_is_removed(tmp_path):
    tree = _home(tmp_path)
    assert _run(tree, tmp_path) == tr.Outcome(str(tree), tr.REMOVED)
    assert not tree.exists()


def test_a_dry_run_reports_and_keeps(tmp_path):
    tree = _home(tmp_path)
    assert _run(tree, tmp_path, dry_run=True).outcome == tr.WOULD_REMOVE
    assert tree.is_dir()


def test_an_idle_regular_file_is_removed(tmp_path):
    leftover = tmp_path / "venv.log"
    leftover.write_text("log")
    _age(leftover)
    assert _run(leftover, tmp_path).outcome == tr.REMOVED
    assert not leftover.exists()


def test_a_recent_write_anywhere_keeps_the_tree(tmp_path):
    tree = _home(tmp_path)
    deep = tree / ".cache" / "kirocrew" / "pycache" / "x.pyc"
    os.utime(deep, (NOW - 60, NOW - 60))
    outcome = _run(tree, tmp_path)
    assert (outcome.outcome, outcome.reason.split(":")[0]) == (tr.KEPT, "recent")
    assert tree.is_dir()


def test_a_repository_inside_keeps_the_tree(tmp_path):
    tree = _home(tmp_path)
    (tree / "work" / ".git").mkdir(parents=True)
    (tree / "work" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    _age(tree)
    assert _run(tree, tmp_path).reason.startswith("holds-repository")
    assert tree.is_dir()


def test_uvs_own_git_under_cache_uv_does_not_count(tmp_path):
    tree = _home(tmp_path)
    (tree / ".cache" / "uv" / "sdists-v10" / ".git").mkdir(parents=True)
    (tree / ".cache" / "uv" / "sdists-v10" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    _age(tree)
    assert _run(tree, tmp_path).outcome == tr.REMOVED


def test_a_clone_elsewhere_under_cache_still_counts(tmp_path):
    tree = _home(tmp_path)
    (tree / ".cache" / "clone" / ".git").mkdir(parents=True)
    (tree / ".cache" / "clone" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    _age(tree)
    assert _run(tree, tmp_path).reason.startswith("holds-repository")


def test_a_repository_above_the_target_keeps_it(tmp_path):
    outer = tmp_path / "checkout"
    (outer / ".git").mkdir(parents=True)
    tree = _home(outer, "build")
    assert _run(tree, tmp_path).reason.startswith("holds-repository")


@pytest.mark.parametrize(
    "use",
    [
        "cwd",  # a working directory inside the tree
        "env",  # only named on PYTHONPATH
    ],
)
def test_a_process_naming_the_tree_keeps_it(tmp_path, use):
    tree = _home(tmp_path)
    if use == "cwd":
        uses = ((4242, "pytest", str(tree / ".cache")),)
    else:
        env = f"PYTHONPATH=/opt/x:{tree}/deps\0HOME=/home/me\0".encode()
        uses = tuple((4242, "pytest", p) for p in tr._env_paths(env))
    view = tr.ProcessView(uses=uses, sockets=(), unreadable=0)
    assert _run(tree, tmp_path, view).reason == "in-use: pid 4242 (pytest)"
    assert tree.is_dir()


def test_a_bound_socket_inside_keeps_the_tree(tmp_path):
    tree = _home(tmp_path)
    view = tr.ProcessView(uses=(), sockets=(str(tree / "gw.sock"),), unreadable=0)
    assert _run(tree, tmp_path, view).reason.startswith("in-use: socket")


def test_a_sibling_name_prefix_is_not_use(tmp_path):
    tree = _home(tmp_path, "kc-home")
    view = tr.ProcessView(uses=((1, "sh", str(tmp_path / "kc-home2")),), sockets=(), unreadable=0)
    assert _run(tree, tmp_path, view).outcome == tr.REMOVED


@pytest.mark.parametrize(
    ("view", "reason"),
    [
        (tr.ProcessView(uses=(), sockets=(), unreadable=3), "unprovable: 3 processes unreadable"),
        (
            tr.ProcessView(uses=(), sockets=(), unreadable=1, unreadable_names=("ssh-agent 42",)),
            "unprovable: 1 process unreadable (ssh-agent 42)",
        ),
        (
            tr.ProcessView(uses=(), sockets=(), unreadable=0, available=False),
            "unprovable: no /proc",
        ),
    ],
)
def test_a_process_table_it_cannot_read_keeps_everything(tmp_path, view, reason):
    tree = _home(tmp_path)
    assert _run(tree, tmp_path, view).reason.startswith(reason)
    assert tree.is_dir()


def test_a_run_that_starts_before_the_removal_keeps_the_tree(tmp_path):
    tree = _home(tmp_path)
    late = tr.ProcessView(uses=((77, "pytest", str(tree)),), sockets=(), unreadable=0)
    outcome = _run(tree, tmp_path, reread=lambda: late)
    assert outcome == tr.Outcome(str(tree), tr.KEPT, "in-use: pid 77 (pytest)")
    assert (tree / ".cache" / "kirocrew" / "pycache" / "x.pyc").is_file()


def test_a_trailing_slash_does_not_follow_a_link(tmp_path):
    target = _home(tmp_path, "real")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert _run(f"{link}/", tmp_path).reason == "not a plain directory or file"
    assert target.is_dir()


# Fake pids above the kernel's largest pid_max (4194304), so none is this process.
_FAKE_PIDS = (9_000_001, 9_000_002, 9_000_003)


def _fake_proc(proc: Path, pid: int, *, uid: int, comm: str, env: bytes = b"", readable=True):
    entry = proc / str(pid)
    entry.mkdir(parents=True)
    (entry / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    (entry / "comm").write_text(f"{comm}\n")
    for link in ("cwd", "root", "exe"):
        (entry / link).symlink_to("/")
    if readable:
        (entry / "fd").mkdir()
    else:
        # listdir on a file fails the way EACCES does on a process it may not inspect.
        (entry / "fd").write_text("")
    (entry / "maps").write_text("")
    (entry / "environ").write_bytes(env)


def test_reading_a_process_table(tmp_path):
    proc, home = tmp_path / "proc", tmp_path / "home"
    (proc / "self").mkdir(parents=True)
    (proc / "net").mkdir()
    home.mkdir()
    (proc / "net" / "unix").write_text(
        "Num RefCount Protocol Flags Type St Inode Path\n"
        f"0000: 00000002 00000000 00010000 0001 01 1 {home}/gate way.sock\n"
    )
    me = os.getuid()
    mine, other, sealed = _FAKE_PIDS
    _fake_proc(proc, mine, uid=me, comm="pytest", env=f"HOME={home}/./\0LANG=C\0".encode())
    _fake_proc(proc, other, uid=me + 1, comm="other", env=f"HOME={home}\0".encode())
    _fake_proc(proc, sealed, uid=me, comm="ssh-agent", readable=False)
    view = tr.read_processes(proc, uid=me)
    assert (mine, "pytest", os.path.realpath(home)) in view.uses
    assert {pid for pid, _comm, _path in view.uses} == {mine}
    assert (view.unreadable, view.unreadable_names) == (1, (f"ssh-agent {sealed}",))
    assert view.sockets == (f"{home}/gate way.sock",)


def test_outside_the_temp_roots_is_kept(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    tree = _home(tmp_path, "elsewhere")
    assert _run(tree, root).reason.startswith("outside-scope")
    assert _run(root, root).reason.startswith("outside-scope")


def test_a_link_is_never_followed(tmp_path):
    target = _home(tmp_path, "real")
    link = tmp_path / "link"
    link.symlink_to(target)
    assert _run(link, tmp_path).reason == "not a plain directory or file"
    assert target.is_dir()


def test_a_tree_swapped_after_the_checks_is_not_removed(tmp_path):
    tree = _home(tmp_path)
    other = _home(tmp_path, "other")
    outcome = tr._remove(tree, os.lstat(other), now=NOW, reread=lambda: QUIET)
    assert outcome.outcome == tr.KEPT
    assert tree.is_dir() and other.is_dir()


def test_reading_proc_finds_a_child_working_in_the_tree(tmp_path):
    tree = _home(tmp_path)
    # Popen returns only once the child has exec'd, and the chdir happens before
    # exec, so one read already sees the working directory: no wait is needed.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tree)
    try:
        view = tr.read_processes()
        assert tr._use_of(str(tree), view).startswith(f"in-use: pid {child.pid}")
    finally:
        child.kill()
        child.wait()


@pytest.mark.parametrize(
    ("outcomes", "code"),
    [
        ([tr.Outcome("/tmp/a", tr.REMOVED), tr.Outcome("/tmp/b", tr.WOULD_REMOVE)], 0),
        ([tr.Outcome("/tmp/a", tr.REMOVED), tr.Outcome("/tmp/b", tr.KEPT, "recent: 2 min")], 1),
    ],
)
def test_the_command_exits_one_when_anything_was_kept(monkeypatch, capsys, outcomes, code):
    monkeypatch.setattr(tr, "reclaim", lambda paths, dry_run: outcomes)
    with pytest.raises(SystemExit) as stop:
        tr.run_command(["/tmp/a", "/tmp/b"], dry_run=False, as_json=False)
    assert stop.value.code == code
    assert "/tmp/b" in capsys.readouterr().out
