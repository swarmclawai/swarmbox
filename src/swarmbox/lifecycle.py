import json
import subprocess
import time
from typing import Callable, List, Optional

from .cancellation import throw_if_cancelled
from .errors import ExecError, SyncError
from .models import Commit, Hooks
from .worktree import commits_since, current_branch, current_head


def run_host_hooks(hooks, cwd: str, signal: object = None) -> None:
    for hook in hooks or []:
        throw_if_cancelled(signal)
        proc = subprocess.Popen(
            hook.command,
            cwd=cwd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = None if hook.timeout_ms is None else time.monotonic() + hook.timeout_ms / 1000
        while proc.poll() is None:
            throw_if_cancelled(signal)
            if deadline is not None and time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise ExecError(hook.command, "Host hook timed out: %s" % hook.command)
            time.sleep(0.05)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            raise ExecError(hook.command, "Host hook failed: %s\n%s%s" % (hook.command, stderr, stdout))


def exec_ok(
    sandbox,
    command: str,
    cwd: Optional[str] = None,
    sudo: bool = False,
    timeout_ms: Optional[int] = None,
    signal: object = None,
):
    throw_if_cancelled(signal)
    try:
        result = sandbox.exec(
            command,
            cwd=cwd,
            sudo=sudo,
            idle_timeout_seconds=(timeout_ms / 1000) if timeout_ms else None,
            signal=signal,
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        result = sandbox.exec(command, cwd=cwd, sudo=sudo)
    if result.exit_code != 0:
        raise ExecError(command, "Command failed (exit %s): %s\n%s" % (result.exit_code, command, result.stderr))
    return result


def _git_config_value(repo: str, key: str) -> str:
    proc = subprocess.run(
        ["git", "config", key],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def with_sandbox_lifecycle(
    *,
    host_repo_dir: str,
    sandbox,
    sandbox_repo_dir: str,
    host_worktree_path: str,
    hooks: Optional[Hooks],
    branch: Optional[str],
    apply_to_host: Optional[Callable[[], None]],
    work: Callable[[str], object],
    signal: object = None,
) -> object:
    throw_if_cancelled(signal)
    hooks = hooks or Hooks()
    host_current_branch = None if branch else current_branch(host_repo_dir)
    host_git_name = _git_config_value(host_repo_dir, "user.name")
    host_git_email = _git_config_value(host_repo_dir, "user.email")

    exec_ok(sandbox, 'git config --global --add safe.directory "%s"' % sandbox_repo_dir, signal=signal)
    if host_git_name:
        exec_ok(sandbox, "git config --global user.name %s" % json.dumps(host_git_name), signal=signal)
    if host_git_email:
        exec_ok(sandbox, "git config --global user.email %s" % json.dumps(host_git_email), signal=signal)
    resolved_branch = exec_ok(
        sandbox,
        "git rev-parse --abbrev-ref HEAD",
        cwd=sandbox_repo_dir,
        signal=signal,
    ).stdout.strip()

    for hook in hooks.sandbox.on_sandbox_ready:
        exec_ok(
            sandbox,
            hook.command,
            cwd=sandbox_repo_dir,
            sudo=hook.sudo,
            timeout_ms=hook.timeout_ms,
            signal=signal,
        )
    run_host_hooks(hooks.host.on_sandbox_ready, host_worktree_path, signal=signal)

    base_head = current_head(host_worktree_path)
    throw_if_cancelled(signal)
    result = work(base_head)
    throw_if_cancelled(signal)

    if apply_to_host:
        apply_to_host()

    commits: List[Commit]
    final_branch: str
    target_branch = branch or resolved_branch
    if host_current_branch is not None:
        count = subprocess.run(
            ["git", "rev-list", "%s..HEAD" % base_head, "--count"],
            cwd=host_worktree_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        has_new_commits = count.returncode == 0 and int((count.stdout or "0").strip() or "0") > 0
        exec_ok(sandbox, "git checkout --detach", cwd=sandbox_repo_dir, signal=signal)
        if has_new_commits:
            proc = subprocess.run(
                ["git", "merge", resolved_branch],
                cwd=host_repo_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if proc.returncode != 0:
                raise SyncError(
                    "Merge of '%s' onto '%s' failed. Temporary branch preserved.\n%s"
                    % (resolved_branch, host_current_branch, proc.stderr)
                )
        subprocess.run(["git", "branch", "-D", resolved_branch], cwd=host_repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        commits = [Commit(sha) for sha in commits_since(host_repo_dir, base_head)]
        final_branch = host_current_branch
    else:
        commits = [Commit(sha) for sha in commits_since(host_repo_dir, base_head, "refs/heads/%s" % target_branch)]
        final_branch = target_branch

    return result, final_branch, commits
