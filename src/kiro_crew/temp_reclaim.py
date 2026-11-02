"""Remove a temp tree from ``/tmp`` or ``/var/tmp`` once it is provably nobody's work.

``kirocrew tmp reclaim`` is the person's way to clean up what agent sessions left
in the system temp roots -- mostly fake ``HOME`` directories for test runs, each
carrying its own uv, npm and bytecode caches. An agent cannot do it from its own
shell: the deny rules refuse ``rm -rf`` on an absolute path, and the sandbox
shows it only the few processes inside its own user namespace, so nothing it
runs can tell whether a sibling session still uses a tree. The design record is
``docs/request-for-change/rfc-agent-temp-reclaim.md``; this module is its
Phase 1, the routine and the command. No agent surface reaches it yet.

REFUSE-SAFE IS THE WHOLE DESIGN. A path is removed only when every check below
proves it may go, and every doubt keeps it:

1. It is a plain directory or regular file, never a link, and not a temp root.
2. It sits inside a temp root, and both it and the root's child holding it are
   owned by this user.
3. Nothing anywhere in it was written in the last :data:`IDLE_SECONDS`.
4. It holds no git repository, and no ancestor below the temp root holds one.
5. No process uses it: no working directory, root, executable, open file,
   mapped file, bound socket, or ``HOME``/``TMPDIR``/``TMP``/``TEMP``/
   ``PYTHONPATH``/``XDG_*``/``*_CACHE_DIR`` value inside it. A process of this user whose ``/proc``
   entries cannot be read makes every tree unprovable, because it could be
   the one using it.
6. The removal is pinned to the inode the checks saw
   (:func:`pinned_fs.remove_tree_pinned`), and inside that pin it reads the
   process table and the tree's mtimes again, so a swap, a write or a run
   started after the checks removes nothing.

Linux only: the in-use check reads ``/proc``, and without it nothing is proven.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from kiro_crew import pinned_fs
from kiro_crew.agent_scratch import (
    _is_git_directory,
    _is_plain_dir,
    _regenerable,
    _search,
    _searched_venv,
    _tree_newest_mtime,
)

#: The roots a person's leftovers live in. Anything outside them is kept.
TEMP_ROOTS: tuple[str, ...] = ("/tmp", "/var/tmp")
#: A tree written more recently than this is kept as ``recent``.
IDLE_SECONDS = 30 * 60
#: The whole run stops starting new paths after this many seconds.
DEADLINE_SECONDS = 120.0
#: Environment variables whose value names a directory the process works in.
_ENV_PATH_KEYS = frozenset({"HOME", "TMPDIR", "TMP", "TEMP", "PYTHONPATH"})

REMOVED = "removed"
KEPT = "kept"
WOULD_REMOVE = "would-remove"


@dataclass(frozen=True)
class Outcome:
    """What happened to one requested path."""

    path: str
    outcome: str
    reason: str = ""


@dataclass(frozen=True)
class ProcessView:
    """Every path the user's live processes name, read once per run.

    ``uses`` holds ``(pid, comm, path)`` for each working directory, root,
    executable, open or mapped file and path-valued environment entry.
    ``sockets`` holds the bound AF_UNIX paths, which ``/proc/net/unix`` does not
    attribute to a pid. ``unreadable`` counts this user's processes whose entries
    could not be read; any of them makes every tree unprovable.
    """

    uses: tuple[tuple[int, str, str], ...]
    sockets: tuple[str, ...]
    unreadable: int
    available: bool = True
    #: ``"<comm> <pid>"`` for each unreadable process, so a refusal can name them.
    unreadable_names: tuple[str, ...] = ()


def _env_paths(raw: bytes) -> list[str]:
    """The path values in one ``/proc/<pid>/environ`` blob, resolved.

    Resolved because an environment value is whatever the parent wrote:
    ``/tmp//home/.``, or a link that points into the tree, must still match.
    """
    out: list[str] = []
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if not sep or not value:
            continue
        name = key.decode("utf-8", "replace")
        if name in _ENV_PATH_KEYS or name.startswith("XDG_") or name.endswith("_CACHE_DIR"):
            for part in value.decode("utf-8", "replace").split(os.pathsep):
                if part.startswith("/"):
                    out.append(os.path.realpath(part))
    return out


def _status_uids(proc: Path) -> tuple[int, int]:
    """The real and effective uid from ``/proc/<pid>/status``.

    Not the owner of ``/proc/<pid>``: the kernel hands that to root for any
    process that is not dumpable (ssh-agent, gpg-agent, a setuid program), which
    would make this user's own process look like someone else's and skip it.
    ``status`` is world-readable and always names the real owner.
    """
    for line in (proc / "status").read_text(errors="replace").splitlines():
        if line.startswith("Uid:"):
            fields = line.split()
            return int(fields[1]), int(fields[2])
    raise ProcessLookupError(str(proc))


def _map_paths(raw: str) -> list[str]:
    """The file paths in one ``/proc/<pid>/maps`` text (column six, absolute only)."""
    out: list[str] = []
    for line in raw.splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and fields[5].startswith("/"):
            out.append(fields[5].removesuffix(" (deleted)"))
    return out


def _process_paths(proc: Path) -> list[str]:
    """Every path one process names. Raises ``PermissionError`` when it cannot be read."""
    paths: list[str] = []
    for link in ("cwd", "root", "exe"):
        try:
            paths.append(os.readlink(proc / link).removesuffix(" (deleted)"))
        except FileNotFoundError:
            continue
    for fd in os.listdir(proc / "fd"):
        try:
            paths.append(os.readlink(proc / "fd" / fd).removesuffix(" (deleted)"))
        except FileNotFoundError:
            continue
    paths.extend(_map_paths((proc / "maps").read_text(errors="replace")))
    paths.extend(_env_paths((proc / "environ").read_bytes()))
    return paths


def _socket_paths(proc_root: Path) -> list[str]:
    """Bound AF_UNIX paths from ``/proc/net/unix`` (abstract sockets are skipped)."""
    try:
        lines = (proc_root / "net" / "unix").read_text(errors="replace").splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in lines:
        # The path is the eighth column and may hold spaces, so it takes the rest.
        fields = line.split(None, 7)
        if len(fields) == 8 and fields[7].startswith("/"):
            out.append(fields[7])
    return out


def read_processes(proc_root: Path = Path("/proc"), uid: int | None = None) -> ProcessView:
    """Read what every live process of *uid* (default: this user) names.

    A process counts as this user's when its real or effective uid is *uid*.
    This process itself is skipped: running the command from inside a tree, or
    with ``HOME`` pointing into it, says nothing about whether anything else
    uses it. A process of another uid is skipped, because the ownership check
    already limits removal to this user's own trees. A process that vanishes
    mid-read is skipped; one of this user's that cannot be read is counted and
    named, and makes every tree unprovable.
    """
    if not (proc_root / "self").exists():
        return ProcessView(uses=(), sockets=(), unreadable=0, available=False)
    uid = os.getuid() if uid is None else uid
    me = os.getpid()
    uses: list[tuple[int, str, str]] = []
    unreadable: list[str] = []
    for name in os.listdir(proc_root):
        if not name.isdigit() or int(name) == me:
            continue
        proc = proc_root / name
        try:
            if uid not in _status_uids(proc):
                continue
            comm = (proc / "comm").read_text(errors="replace").strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            unreadable.append(f"? {name}")
            continue
        try:
            paths = _process_paths(proc)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            unreadable.append(f"{comm} {name}")
            continue
        uses.extend((int(name), comm, p) for p in paths)
    return ProcessView(
        uses=tuple(uses),
        sockets=tuple(_socket_paths(proc_root)),
        unreadable=len(unreadable),
        unreadable_names=tuple(sorted(unreadable)),
    )


def _inside(path: str, tree: str) -> bool:
    return path == tree or path.startswith(tree.rstrip("/") + "/")


def _temp_root(resolved: Path, roots: Sequence[str]) -> Path | None:
    """The temp root *resolved* sits strictly inside, or ``None``."""
    for root in roots:
        real = Path(os.path.realpath(root))
        if resolved != real and _inside(str(resolved), str(real)):
            return real
    return None


def _holds_repository(tree: Path) -> bool:
    """:func:`agent_scratch.holds_git_repository`, with a top-level ``.cache/uv`` skipped.

    uv keeps a ``.git`` inside ``.cache/uv/sdists-v10``, so every fake ``HOME``
    that ran uv would read as a repository and could never be reclaimed. Only
    ``.cache/uv`` is skipped: a clone anywhere else under ``.cache`` is still
    found. Below the first two levels the sweep's own search runs unchanged
    (:func:`agent_scratch._search`), and every doubt answers True, as it does there.
    """
    try:
        if _level_holds_repository(tree, skip=None):
            return True
        cache = tree / ".cache"
        return _is_plain_dir(cache) and _level_holds_repository(cache, skip="uv")
    except OSError:
        return True


def _level_holds_repository(directory: Path, *, skip: str | None) -> bool:
    """One level of the search: *directory* itself, then each child the sweep searches.

    ``.cache`` is never searched from the top level here, because the caller
    searches it on its own with ``uv`` skipped. Raises ``OSError`` on an
    unreadable directory; the caller turns that into True.
    """
    if _is_git_directory(directory):
        return True
    for entry in os.scandir(directory):
        child = directory / entry.name
        if entry.name == ".git":
            return True
        if entry.name == skip or (skip is None and entry.name == ".cache"):
            continue
        if not _is_plain_dir(child):
            continue
        if _regenerable(child, top_level=False) and not _searched_venv(child, top_level=False):
            continue
        if _search(child, basetemp_at_top=False):
            return True
    return False


def _ancestor_repository(resolved: Path, root: Path) -> bool:
    """Whether a directory between *root* and *resolved* holds a ``.git`` entry."""
    for parent in resolved.parents:
        if parent == root:
            return False
        if os.path.lexists(parent / ".git"):
            return True
    return False


def _user(path: Path, uid: int) -> bool:
    try:
        return os.lstat(path).st_uid == uid
    except OSError:
        return False


def _use_of(tree: str, view: ProcessView) -> str:
    """The first process or socket using *tree*, as a reason, or ``""``."""
    for pid, comm, path in view.uses:
        if _inside(path, tree):
            return f"in-use: pid {pid} ({comm})"
    for sock in view.sockets:
        if _inside(sock, tree):
            return f"in-use: socket {sock}"
    return ""


def _unproven(view: ProcessView) -> str:
    """Why *view* cannot prove a tree unused, or ``""`` when it can."""
    if not view.available:
        return "unprovable: no /proc to check process use"
    if view.unreadable:
        names = ", ".join(view.unreadable_names[:3])
        more = ", ..." if view.unreadable > 3 else ""
        detail = f" ({names}{more})" if names else ""
        noun = "process" if view.unreadable == 1 else "processes"
        return f"unprovable: {view.unreadable} {noun} unreadable{detail}"
    return ""


def _remove(
    resolved: Path,
    info: os.stat_result,
    *,
    now: float,
    reread: Callable[[], ProcessView],
) -> Outcome:
    """Remove *resolved*, pinned to the ``(st_dev, st_ino, st_uid)`` the checks saw.

    The process table the checks used was read once for the whole run, so a
    directory's removal re-reads it, and re-walks the tree's mtimes, inside the
    pinned approval: a run started in the tree since then keeps it.
    """
    if stat.S_ISREG(info.st_mode):
        ok = pinned_fs.unlink_verified_by_name(
            resolved.parent, resolved.name, (info.st_dev, info.st_ino)
        )
        return (
            Outcome(str(resolved), REMOVED)
            if ok
            else Outcome(str(resolved), KEPT, "changed: not the file checked")
        )

    def _approve(root_fd: int, _tree: object) -> str | None:
        opened = os.fstat(root_fd)
        same = (opened.st_dev, opened.st_ino, opened.st_uid) == (
            info.st_dev,
            info.st_ino,
            info.st_uid,
        )
        if not same:
            return "not the directory checked"
        if now - _tree_newest_mtime(resolved, fallback=float("inf")) < IDLE_SECONDS:
            return "written since the check"
        fresh = reread()
        return _unproven(fresh) or _use_of(str(resolved), fresh) or None

    try:
        result = pinned_fs.remove_tree_pinned(
            str(resolved), what="temp tree", approve=_approve, refusal=pinned_fs.PinnedPathRefusal
        )
    except pinned_fs.PinnedPathRefusal as exc:
        return Outcome(str(resolved), KEPT, f"refused: {exc}")
    if result.removed:
        return Outcome(str(resolved), REMOVED)
    codes = {
        pinned_fs.REMOVAL_FAILED,
        pinned_fs.REMOVAL_IDENTITY_CHANGED,
        pinned_fs.REMOVAL_STAGE_FAILED,
        pinned_fs.REMOVAL_UNVERIFIABLE,
    }
    if result.reason not in codes:
        # The approval's own reason: nothing was touched.
        return Outcome(str(resolved), KEPT, result.reason or "refused")
    where = f", left at {resolved.parent / result.staged_name}" if result.staged_name else ""
    if result.reason == pinned_fs.REMOVAL_FAILED and result.survivors:
        return Outcome(str(resolved), KEPT, f"partial: {result.survivors} left{where}")
    return Outcome(str(resolved), KEPT, f"refused: {result.reason}{where}")


def reclaim_one(
    path: str,
    view: ProcessView,
    *,
    dry_run: bool = False,
    now: float | None = None,
    roots: Sequence[str] = TEMP_ROOTS,
    uid: int | None = None,
    reread: Callable[[], ProcessView] = read_processes,
) -> Outcome:
    """Check one requested path and remove it when every check passes (see the module doc).

    *view* is the process table read once for the run; *reread* reads it again
    inside the removal's pinned approval.
    """
    uid = os.getuid() if uid is None else uid
    now = time.time() if now is None else now
    # A trailing slash makes ``lstat`` follow a link the name ends in, so it goes.
    path = path.rstrip("/") or "/"
    try:
        info = os.lstat(path)
    except OSError as exc:
        return Outcome(path, KEPT, f"missing: {exc.strerror or exc}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        return Outcome(path, KEPT, "not a plain directory or file")
    resolved = Path(os.path.realpath(path))
    root = _temp_root(resolved, roots)
    if root is None:
        return Outcome(str(resolved), KEPT, "outside-scope: not inside " + " or ".join(roots))
    top = root / resolved.relative_to(root).parts[0]
    if not (_user(resolved, uid) and _user(top, uid)):
        return Outcome(str(resolved), KEPT, "outside-scope: not owned by you")
    is_dir = stat.S_ISDIR(info.st_mode)
    # An unreadable entry answers +inf, so a tree that cannot be walked reads as recent.
    newest = _tree_newest_mtime(resolved, fallback=float("inf")) if is_dir else info.st_mtime
    if newest == float("inf"):
        return Outcome(str(resolved), KEPT, "recent: part of it could not be read")
    if now - newest < IDLE_SECONDS:
        return Outcome(
            str(resolved), KEPT, f"recent: written {int(max(now - newest, 0) // 60)} min ago"
        )
    if _ancestor_repository(resolved, root) or (is_dir and _holds_repository(resolved)):
        return Outcome(
            str(resolved), KEPT, "holds-repository: use git worktree remove, or move the work"
        )
    blocked = _unproven(view) or _use_of(str(resolved), view)
    if blocked:
        return Outcome(str(resolved), KEPT, blocked)
    if dry_run:
        return Outcome(str(resolved), WOULD_REMOVE)
    return _remove(resolved, info, now=now, reread=reread)


def reclaim(
    paths: Iterable[str], *, dry_run: bool = False, view: ProcessView | None = None
) -> list[Outcome]:
    """Run :func:`reclaim_one` over *paths*, reading the process table once."""
    view = read_processes() if view is None else view
    started = time.monotonic()
    out = []
    for path in paths:
        if time.monotonic() - started > DEADLINE_SECONDS:
            out.append(Outcome(path, KEPT, "timeout: not checked"))
            continue
        out.append(reclaim_one(path, view, dry_run=dry_run))
    return out


def run_command(paths: Sequence[str], *, dry_run: bool, as_json: bool) -> None:
    """``kirocrew tmp reclaim``: print one line per path; exit 0 only when none was kept."""
    if not sys.platform.startswith("linux"):
        print("kirocrew tmp reclaim needs /proc and runs on Linux only.", file=sys.stderr)
        sys.exit(2)
    outcomes = reclaim(paths, dry_run=dry_run)
    if as_json:
        print(json.dumps([asdict(o) for o in outcomes], indent=2))
    else:
        for o in outcomes:
            print(f"{o.outcome:<13} {o.path}" + (f"  ({o.reason})" if o.reason else ""))
        if any(o.reason.startswith("unprovable: ") and "unreadable" in o.reason for o in outcomes):
            print(
                "\nSome of your processes could not be inspected, so no tree is proven unused. "
                "Run this from your own terminal, outside any agent sandbox.",
                file=sys.stderr,
            )
    sys.exit(1 if any(o.outcome == KEPT for o in outcomes) else 0)
