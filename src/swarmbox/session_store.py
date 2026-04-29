import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


def encode_project_path(cwd: str) -> str:
    normalized = cwd
    if not re.match(r"^([A-Za-z]:[\\/])?$", normalized) and normalized != "/":
        normalized = re.sub(r"[\\/]+$", "", normalized)
    normalized = re.sub(r"^([A-Za-z]):", r"\1", normalized)
    return re.sub(r"[\\/]", "-", normalized)


class SessionStore(Protocol):
    cwd: str

    def session_file_path(self, session_id: str) -> str:
        ...

    def read_session(self, session_id: str) -> str:
        ...

    def write_session(self, session_id: str, content: str) -> None:
        ...


@dataclass
class HostSessionStore:
    cwd: str
    projects_dir: str

    def session_file_path(self, session_id: str) -> str:
        return str(Path(self.projects_dir) / encode_project_path(self.cwd) / ("%s.jsonl" % session_id))

    def read_session(self, session_id: str) -> str:
        return Path(self.session_file_path(session_id)).read_text(encoding="utf-8")

    def write_session(self, session_id: str, content: str) -> None:
        path = Path(self.session_file_path(session_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@dataclass
class SandboxSessionStore:
    cwd: str
    handle: object
    projects_dir: str

    def session_file_path(self, session_id: str) -> str:
        return str(Path(self.projects_dir) / encode_project_path(self.cwd) / ("%s.jsonl" % session_id))

    def read_session(self, session_id: str) -> str:
        sandbox_path = self.session_file_path(session_id)
        with tempfile.NamedTemporaryFile(prefix="swarmbox-session-", suffix=".jsonl", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            self.handle.copy_file_out(sandbox_path, tmp_path)
            return Path(tmp_path).read_text(encoding="utf-8")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def write_session(self, session_id: str, content: str) -> None:
        sandbox_path = self.session_file_path(session_id)
        with tempfile.NamedTemporaryFile(prefix="swarmbox-session-", suffix=".jsonl", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(content.encode("utf-8"))
        try:
            self.handle.exec("mkdir -p %s" % json.dumps(str(Path(sandbox_path).parent)))
            self.handle.copy_file_in(tmp_path, sandbox_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


def host_session_store(cwd: str, projects_dir: str = None) -> HostSessionStore:
    base = projects_dir or str(Path(os.environ.get("HOME", "~")) / ".claude" / "projects")
    return HostSessionStore(cwd=cwd, projects_dir=base)


def sandbox_session_store(cwd: str, handle, projects_dir: str) -> SandboxSessionStore:
    return SandboxSessionStore(cwd=cwd, handle=handle, projects_dir=projects_dir)


def transfer_session(from_store: SessionStore, to_store: SessionStore, session_id: str) -> None:
    content = from_store.read_session(session_id)
    if content == "":
        to_store.write_session(session_id, "")
        return
    lines = []
    for line in content.split("\n"):
        if line == "":
            lines.append(line)
            continue
        entry = json.loads(line)
        if isinstance(entry, dict) and entry.get("cwd") == from_store.cwd:
            entry["cwd"] = to_store.cwd
        lines.append(json.dumps(entry, separators=(",", ":")))
    to_store.write_session(session_id, "\n".join(lines))
