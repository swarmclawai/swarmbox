import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

SANDBOX_REPO_DIR = "/home/agent/workspace"
PARENT_GIT_SANDBOX_DIR = "/.swarmbox-parent-git"


@dataclass(frozen=True)
class MountConfig:
    host_path: str
    sandbox_path: str
    readonly: bool = False


def default_image_name(repo_dir: str) -> str:
    name = re.sub(r"[^a-z0-9_.-]", "-", Path(repo_dir).name.lower())
    return "swarmbox:%s" % (name or "local")


def expand_tilde(path: str, home_dir: Optional[str] = None) -> str:
    home = home_dir or str(Path.home())
    if path == "~":
        return home
    if path.startswith("~/") or path.startswith("~\\"):
        return home + "/" + path[2:]
    return path


def resolve_host_path(host_path: str) -> str:
    expanded = expand_tilde(host_path)
    return str(Path(expanded).resolve()) if not Path(expanded).is_absolute() else expanded


def resolve_sandbox_path(sandbox_path: str, sandbox_homedir: Optional[str] = None) -> str:
    has_tilde = sandbox_path == "~" or sandbox_path.startswith("~/") or sandbox_path.startswith("~\\")
    if has_tilde and sandbox_homedir is None:
        raise ValueError('sandbox_path "%s" contains a tilde but provider has no sandbox_homedir' % sandbox_path)
    expanded = expand_tilde(sandbox_path, sandbox_homedir) if has_tilde else sandbox_path
    if expanded.startswith("/"):
        return expanded
    return str(Path(SANDBOX_REPO_DIR) / expanded)


def resolve_user_mounts(
    mounts: Iterable[MountConfig],
    sandbox_homedir: Optional[str] = None,
) -> List[MountConfig]:
    result = []
    for mount in mounts:
        host = resolve_host_path(mount.host_path)
        if not Path(host).exists():
            raise FileNotFoundError("Mount host_path does not exist: %s" % mount.host_path)
        result.append(
            MountConfig(
                host_path=host,
                sandbox_path=resolve_sandbox_path(mount.sandbox_path, sandbox_homedir),
                readonly=mount.readonly,
            )
        )
    return result


def resolve_git_mounts(git_path: str) -> List[MountConfig]:
    path = Path(git_path)
    if path.is_dir():
        return [MountConfig(str(path), str(path))]
    content = path.read_text(encoding="utf-8").strip()
    match = re.match(r"^gitdir:\s*(.+)$", content)
    if not match:
        return [MountConfig(str(path), str(path))]
    gitdir = Path(match.group(1))
    parent_git_dir = gitdir.parent.parent
    return [MountConfig(str(path), str(path)), MountConfig(str(parent_git_dir), str(parent_git_dir))]


def volume_arg(mount: MountConfig, extra: Optional[str] = None) -> str:
    suffixes = []
    if mount.readonly:
        suffixes.append("ro")
    if extra:
        suffixes.append(extra)
    suffix = (":" + ",".join(suffixes)) if suffixes else ""
    return "%s:%s%s" % (mount.host_path, mount.sandbox_path, suffix)
