import os
import shutil
import time
from pathlib import Path
from typing import Iterable, Optional

from .errors import CopyError


def copy_to_worktree(
    paths: Iterable[str],
    host_repo_dir: str,
    worktree_path: str,
    timeout_ms: Optional[int] = None,
) -> None:
    deadline = None if timeout_ms is None else time.monotonic() + (timeout_ms / 1000)

    def check_timeout() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise CopyError("Timed out copying paths into worktree after %sms" % timeout_ms)

    check_timeout()
    for relative in paths:
        check_timeout()
        src = Path(host_repo_dir) / relative
        if not src.exists():
            continue
        dest = Path(worktree_path) / relative
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(str(dest))
            check_timeout()
            shutil.copytree(str(src), str(dest), symlinks=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            check_timeout()
            shutil.copy2(str(src), str(dest))
        check_timeout()


def copy_tree(src: str, dest: str) -> None:
    src_path = Path(src)
    dest_path = Path(dest)
    if src_path.is_dir():
        if dest_path.exists():
            shutil.rmtree(str(dest_path))
        shutil.copytree(str(src_path), str(dest_path), symlinks=True)
    else:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src_path), str(dest_path))


def ensure_executable(path: str) -> None:
    p = Path(path)
    p.chmod(p.stat().st_mode | os.stat(str(p)).st_mode | 0o111)
