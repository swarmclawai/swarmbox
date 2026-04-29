import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .errors import CwdError, WorktreeError


def resolve_cwd(cwd: Optional[str] = None) -> str:
    path = Path(cwd or os.getcwd())
    if not path.is_absolute():
        path = Path(os.getcwd()) / path
    if not path.exists() or not path.is_dir():
        raise CwdError("cwd does not exist or is not a directory: %s" % path)
    return str(path.resolve())


def sanitize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "-", name.lower())


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def generate_temp_branch_name(name: Optional[str] = None) -> str:
    if name:
        return "swarmbox/%s/%s" % (sanitize_name(name), _timestamp())
    return "swarmbox/%s" % _timestamp()


def _run_git(repo_dir: str, args: List[str]) -> str:
    proc = subprocess.run(
        ["git"] + args,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise WorktreeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


def current_branch(repo_dir: str) -> str:
    return _run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def current_head(repo_dir: str) -> str:
    return _run_git(repo_dir, ["rev-parse", "HEAD"]).strip()


def has_uncommitted_changes(path: str) -> bool:
    return bool(_run_git(path, ["status", "--porcelain"]).strip())


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    branch: str


def _worktrees_dir(repo_dir: str, config_dir: str) -> Path:
    return Path(repo_dir) / config_dir / "worktrees"


def list_worktrees(repo_dir: str):
    output = _run_git(repo_dir, ["worktree", "list", "--porcelain"])
    entries = []
    path = None
    branch = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            if path is not None:
                entries.append((path, branch))
            path = line[len("worktree ") :].strip()
            branch = None
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/") :].strip()
    if path is not None:
        entries.append((path, branch))
    return entries


def prune_stale(repo_dir: str, config_dir: str = ".swarmbox") -> None:
    _run_git(repo_dir, ["worktree", "prune"])
    root = _worktrees_dir(repo_dir, config_dir)
    if not root.exists():
        return
    active = {str(Path(path).resolve()) for path, _ in list_worktrees(repo_dir)}
    for child in root.iterdir():
        if child.is_dir() and str(child.resolve()) not in active:
            shutil.rmtree(str(child), ignore_errors=True)


def create_worktree_info(
    repo_dir: str,
    branch: Optional[str] = None,
    base_branch: Optional[str] = None,
    name: Optional[str] = None,
    config_dir: str = ".swarmbox",
) -> WorktreeInfo:
    repo_dir = resolve_cwd(repo_dir)
    worktrees_dir = _worktrees_dir(repo_dir, config_dir)
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    if branch:
        resolved_branch = branch
        worktree_name = branch.replace("/", "-")
    else:
        resolved_branch = generate_temp_branch_name(name)
        worktree_name = resolved_branch.replace("/", "-")
    path = worktrees_dir / worktree_name

    if branch:
        for existing_path, existing_branch in list_worktrees(repo_dir):
            if existing_branch == branch or str(Path(existing_path)) == str(path):
                if str(Path(existing_path).resolve()).startswith(str(worktrees_dir.resolve())):
                    return WorktreeInfo(existing_path, branch)
                raise WorktreeError(
                    "Branch '%s' is already checked out in worktree at '%s'" % (branch, existing_path)
                )

    no_lock_flags = [
        "-c",
        "branch.autoSetupMerge=false",
        "-c",
        "push.autoSetupRemote=false",
    ]
    if branch:
        try:
            _run_git(repo_dir, no_lock_flags + ["worktree", "add", str(path), branch])
        except WorktreeError:
            _run_git(
                repo_dir,
                no_lock_flags + ["worktree", "add", "-b", branch, str(path), base_branch or "HEAD"],
            )
    else:
        _run_git(repo_dir, no_lock_flags + ["worktree", "add", "-b", resolved_branch, str(path), "HEAD"])
    return WorktreeInfo(str(path), resolved_branch)


def remove_worktree(worktree_path: str) -> None:
    path = Path(worktree_path)
    repo_dir = path.parent.parent.parent
    _run_git(str(repo_dir), ["worktree", "remove", "--force", str(path)])


def commits_since(repo_dir: str, base_head: str, ref: str = "HEAD") -> List[str]:
    output = _run_git(repo_dir, ["rev-list", "%s..%s" % (base_head, ref), "--reverse"]).strip()
    if not output:
        return []
    return output.splitlines()
