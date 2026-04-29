from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionPaths:
    host_projects_dir: str
    sandbox_projects_dir: str


def session_paths_layer(host_projects_dir: str, sandbox_projects_dir: str) -> SessionPaths:
    return SessionPaths(host_projects_dir=host_projects_dir, sandbox_projects_dir=sandbox_projects_dir)


def default_session_paths_layer() -> SessionPaths:
    home = os.environ.get("HOME", "~")
    return SessionPaths(
        host_projects_dir=str(Path(home) / ".claude" / "projects"),
        sandbox_projects_dir="/home/agent/.claude/projects",
    )
