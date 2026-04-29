import json
from dataclasses import dataclass, field
from typing import Dict, Optional

from .errors import AgentIdleTimeoutError, SwarmBoxError
from .models import RunResult


@dataclass(frozen=True)
class TaskLogEvent:
    type: str
    message: str
    iteration: Optional[int] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "type": self.type,
            "message": self.message,
            "iteration": self.iteration,
            "metadata": self.metadata,
        }
        return json.dumps({key: value for key, value in payload.items() if value not in (None, {})})


def format_run_summary(result: RunResult) -> str:
    lines = [
        "SwarmBox run complete",
        "Branch: %s" % result.branch,
        "Commits: %s" % len(result.commits),
    ]
    if result.completion_signal:
        lines.append("Completion signal: %s" % result.completion_signal)
    if result.log_file_path:
        lines.append("Log: %s" % result.log_file_path)
    if result.preserved_worktree_path:
        lines.append("Preserved worktree: %s" % result.preserved_worktree_path)
    return "\n".join(lines)


def format_error(exc: BaseException) -> str:
    if isinstance(exc, AgentIdleTimeoutError):
        return "SwarmBox agent idle timeout after %sms: %s" % (exc.timeout_ms, exc)
    if isinstance(exc, SwarmBoxError):
        return "SwarmBox error: %s" % exc
    return "%s: %s" % (exc.__class__.__name__, exc)
