import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from .cancellation import throw_if_cancelled
from .errors import SyncError


def _host(command: List[str], cwd: str, timeout_ms: Optional[int] = None, signal: object = None) -> str:
    throw_if_cancelled(signal)
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=(timeout_ms / 1000) if timeout_ms else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise SyncError("Host command timed out: %s" % " ".join(command)) from exc
    throw_if_cancelled(signal)
    if proc.returncode != 0:
        raise SyncError("Host command failed: %s\n%s" % (" ".join(command), proc.stderr))
    return proc.stdout


def _sandbox_ok(handle, command: str, cwd: str = None, timeout_ms: Optional[int] = None, signal: object = None):
    throw_if_cancelled(signal)
    result = handle.exec(
        command,
        cwd=cwd,
        idle_timeout_seconds=(timeout_ms / 1000) if timeout_ms else None,
        signal=signal,
    )
    throw_if_cancelled(signal)
    if result.exit_code != 0:
        raise SyncError("Sandbox command failed: %s\n%s" % (command, result.stderr))
    return result


def sync_in(host_repo_dir: str, handle, timeout_ms: Optional[int] = None, signal: object = None) -> str:
    branch = _host(["git", "rev-parse", "--abbrev-ref", "HEAD"], host_repo_dir, timeout_ms, signal).strip()
    with tempfile.TemporaryDirectory(prefix="swarmbox-bundle-") as td:
        bundle = str(Path(td) / "repo.bundle")
        _host(["git", "bundle", "create", bundle, "--all"], host_repo_dir, timeout_ms, signal)
        sandbox_tmp = _sandbox_ok(handle, "mktemp -d -t swarmbox-XXXXXX", timeout_ms=timeout_ms, signal=signal).stdout.strip()
        sandbox_bundle = "%s/repo.bundle" % sandbox_tmp
        throw_if_cancelled(signal)
        handle.copy_in(bundle, sandbox_bundle)
        clone_path = "%s_clone" % handle.worktree_path
        _sandbox_ok(handle, 'git clone "%s" "%s"' % (sandbox_bundle, clone_path), timeout_ms=timeout_ms, signal=signal)
        _sandbox_ok(
            handle,
            'rm -rf "%s" && mv "%s" "%s"' % (handle.worktree_path, clone_path, handle.worktree_path),
            timeout_ms=timeout_ms,
            signal=signal,
        )
        _sandbox_ok(handle, 'git checkout "%s"' % branch, cwd=handle.worktree_path, timeout_ms=timeout_ms, signal=signal)
        _sandbox_ok(handle, 'rm -rf "%s"' % sandbox_tmp, timeout_ms=timeout_ms, signal=signal)
        host_head = _host(["git", "rev-parse", "HEAD"], host_repo_dir, timeout_ms, signal).strip()
        sandbox_head = _sandbox_ok(handle, "git rev-parse HEAD", cwd=handle.worktree_path, timeout_ms=timeout_ms, signal=signal).stdout.strip()
        if host_head != sandbox_head:
            raise SyncError("HEAD mismatch after sync-in: host=%s sandbox=%s" % (host_head, sandbox_head))
    return branch


def sync_out(host_repo_dir: str, handle, timeout_ms: Optional[int] = None, signal: object = None) -> None:
    worktree = handle.worktree_path
    host_head = _host(["git", "rev-parse", "HEAD"], host_repo_dir, timeout_ms, signal).strip()
    sandbox_head = _sandbox_ok(handle, "git rev-parse HEAD", cwd=worktree, timeout_ms=timeout_ms, signal=signal).stdout.strip()
    patches_root = Path(host_repo_dir) / ".swarmbox" / "patches"
    patches_root.mkdir(parents=True, exist_ok=True)
    patch_dir = tempfile.mkdtemp(prefix="swarmbox-patches-", dir=str(patches_root))
    patch_path = Path(patch_dir)
    keep_artifacts = False
    has_commits = False
    has_diff = False
    has_untracked = False

    def fail(step: str, exc: Exception) -> SyncError:
        nonlocal keep_artifacts
        keep_artifacts = True
        relative = str(patch_path.relative_to(host_repo_dir))
        recovery = build_recovery_message(relative, step, has_commits, has_diff, has_untracked)
        return SyncError("%s\n\n%s" % (exc, recovery))

    try:
        if host_head != sandbox_head:
            sandbox_patch_dir = _sandbox_ok(handle, "mktemp -d -t swarmbox-patches-XXXXXX", timeout_ms=timeout_ms, signal=signal).stdout.strip()
            _sandbox_ok(
                handle,
                'git format-patch "%s..HEAD" -o "%s"' % (host_head, sandbox_patch_dir),
                cwd=worktree,
                timeout_ms=timeout_ms,
                signal=signal,
            )
            listing = _sandbox_ok(handle, 'ls -1 "%s"' % sandbox_patch_dir, timeout_ms=timeout_ms, signal=signal).stdout.strip()
            for name in listing.splitlines():
                if not name:
                    continue
                throw_if_cancelled(signal)
                handle.copy_file_out("%s/%s" % (sandbox_patch_dir, name), str(patch_path / name))
            _sandbox_ok(handle, 'rm -rf "%s"' % sandbox_patch_dir, timeout_ms=timeout_ms, signal=signal)
            patches = sorted(str(p) for p in patch_path.glob("*.patch") if "diff --git" in p.read_text(errors="ignore"))
            if patches:
                has_commits = True
                subprocess.run(["git", "am", "--abort"], cwd=host_repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    _host(["git", "am", "--3way"] + patches, host_repo_dir, timeout_ms, signal)
                except SyncError as exc:
                    raise fail("commits", exc) from exc

        diff = handle.exec("git diff HEAD", cwd=worktree, idle_timeout_seconds=(timeout_ms / 1000) if timeout_ms else None, signal=signal)
        if diff.exit_code == 0 and diff.stdout.strip():
            has_diff = True
            diff_file = patch_path / "changes.patch"
            diff_file.write_text(diff.stdout, encoding="utf-8")
            try:
                _host(["git", "apply", str(diff_file)], host_repo_dir, timeout_ms, signal)
            except SyncError as exc:
                raise fail("diff", exc) from exc

        untracked = handle.exec(
            "git ls-files --others --exclude-standard",
            cwd=worktree,
            idle_timeout_seconds=(timeout_ms / 1000) if timeout_ms else None,
            signal=signal,
        )
        if untracked.exit_code == 0 and untracked.stdout.strip():
            has_untracked = True
            untracked_dir = patch_path / "untracked"
            for rel in untracked.stdout.strip().splitlines():
                throw_if_cancelled(signal)
                target = untracked_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                handle.copy_file_out("%s/%s" % (worktree, rel), str(target))
            for item in untracked_dir.rglob("*"):
                if item.is_file():
                    dest = Path(host_repo_dir) / item.relative_to(untracked_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(item), str(dest))
    finally:
        if not keep_artifacts:
            shutil.rmtree(patch_dir, ignore_errors=True)


def build_recovery_message(
    patch_dir: str,
    failed_step: str,
    has_commits: bool,
    has_diff: bool,
    has_untracked: bool,
    branch: str = None,
) -> str:
    steps = []
    if has_commits:
        steps.append(("commits", "committed changes"))
    if has_diff:
        steps.append(("diff", "uncommitted changes"))
    if has_untracked:
        steps.append(("untracked", "untracked files"))
    index = [key for key, _ in steps].index(failed_step)
    lines = ["Patch application failed at step %s (%s)." % (index + 1, steps[index][1]), ""]
    prefix = "../../" if branch else ""
    if branch:
        lines.extend(
            [
                "Set up worktree, then resolve:",
                "  git worktree add .swarmbox/worktree %s && \\" % branch,
                "  cd .swarmbox/worktree",
                "",
            ]
        )
    if failed_step == "commits":
        lines.append("Resolve conflicts, then continue with:")
        lines.append("  git am --continue")
    else:
        lines.append("Run the remaining steps:")
    for key, _ in steps[index:]:
        if key == "commits":
            lines.append("  git am --3way %s%s/*.patch" % (prefix, patch_dir))
        elif key == "diff":
            lines.append("  git apply %s%s/changes.patch" % (prefix, patch_dir))
        else:
            lines.append("  cp -r %s%s/untracked/* ." % (prefix, patch_dir))
    return "\n".join(lines)
