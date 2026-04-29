from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True)
class PrintCommand:
    command: str
    stdin: Optional[str] = None


@dataclass(frozen=True)
class AgentCommandOptions:
    prompt: str
    dangerously_skip_permissions: bool = True
    resume_session: Optional[str] = None


@dataclass(frozen=True)
class ParsedStreamEvent:
    type: str
    text: Optional[str] = None
    result: Optional[str] = None
    name: Optional[str] = None
    args: Optional[str] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class IterationUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    context_window: Optional[int] = None


@dataclass
class AgentProvider:
    name: str
    build_print_command_fn: Callable[[AgentCommandOptions], PrintCommand]
    env: Dict[str, str] = field(default_factory=dict)
    capture_sessions: bool = False
    build_interactive_args_fn: Optional[Callable[[AgentCommandOptions], Sequence[str]]] = None
    parse_stream_line_fn: Optional[Callable[[str], Iterable[ParsedStreamEvent]]] = None
    parse_session_usage_fn: Optional[Callable[[str], Optional[IterationUsage]]] = None

    def build_print_command(
        self,
        prompt: str,
        dangerously_skip_permissions: bool = True,
        resume_session: Optional[str] = None,
    ) -> PrintCommand:
        return self.build_print_command_fn(
            AgentCommandOptions(
                prompt=prompt,
                dangerously_skip_permissions=dangerously_skip_permissions,
                resume_session=resume_session,
            )
        )

    def build_interactive_args(
        self,
        prompt: str,
        dangerously_skip_permissions: bool = True,
        resume_session: Optional[str] = None,
    ) -> Sequence[str]:
        if not self.build_interactive_args_fn:
            raise ValueError('Agent provider "%s" does not support interactive sessions' % self.name)
        return self.build_interactive_args_fn(
            AgentCommandOptions(
                prompt=prompt,
                dangerously_skip_permissions=dangerously_skip_permissions,
                resume_session=resume_session,
            )
        )

    def parse_stream_line(self, line: str) -> Iterable[ParsedStreamEvent]:
        if not self.parse_stream_line_fn:
            return []
        return self.parse_stream_line_fn(line)

    def parse_session_usage(self, content: str) -> Optional[IterationUsage]:
        if not self.parse_session_usage_fn:
            return None
        return self.parse_session_usage_fn(content)


@dataclass(frozen=True)
class AgentMetadata:
    id: str
    display_name: str
    binary_name: str
    capability: str
    description: str
    default_model: str
    generic: bool
    setup_badge: str = "CLI"
    optional_api_key: bool = True
    auth_backend: Optional[str] = None
    model_library_url: Optional[str] = None


class SandboxHandle(Protocol):
    worktree_path: str

    def exec(
        self,
        command: str,
        on_line: Optional[Callable[[str], None]] = None,
        cwd: Optional[str] = None,
        sudo: bool = False,
        stdin: Optional[str] = None,
        idle_timeout_seconds: Optional[float] = None,
        signal: object = None,
    ) -> ExecResult:
        ...

    def interactive_exec(
        self,
        args: Sequence[str],
        cwd: Optional[str] = None,
        signal: object = None,
    ) -> int:
        ...

    def copy_in(self, host_path: str, sandbox_path: str) -> None:
        ...

    def copy_file_in(self, host_path: str, sandbox_path: str) -> None:
        ...

    def copy_file_out(self, sandbox_path: str, host_path: str) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class HookCommand:
    command: str
    sudo: bool = False
    timeout_ms: Optional[int] = None


@dataclass(frozen=True)
class HostHooks:
    on_worktree_ready: Sequence[HookCommand] = ()
    on_sandbox_ready: Sequence[HookCommand] = ()


@dataclass(frozen=True)
class SandboxHooks:
    on_sandbox_ready: Sequence[HookCommand] = ()


@dataclass(frozen=True)
class Hooks:
    host: HostHooks = field(default_factory=HostHooks)
    sandbox: SandboxHooks = field(default_factory=SandboxHooks)


@dataclass(frozen=True)
class Timeouts:
    copy_to_worktree_ms: Optional[int] = None
    sandbox_start_ms: Optional[int] = None
    sync_in_ms: Optional[int] = None
    sync_out_ms: Optional[int] = None


@dataclass(frozen=True)
class BranchStrategy:
    type: str
    branch: Optional[str] = None
    base_branch: Optional[str] = None


@dataclass(frozen=True)
class Commit:
    sha: str


@dataclass(frozen=True)
class IterationResult:
    session_id: Optional[str] = None
    session_file_path: Optional[str] = None
    usage: Optional[IterationUsage] = None


@dataclass(frozen=True)
class RunResult:
    iterations: List[IterationResult]
    completion_signal: Optional[str]
    stdout: str
    commits: List[Commit]
    branch: str
    log_file_path: Optional[str] = None
    preserved_worktree_path: Optional[str] = None


@dataclass(frozen=True)
class InteractiveResult:
    commits: List[Commit]
    branch: str
    exit_code: int
    preserved_worktree_path: Optional[str] = None


@dataclass(frozen=True)
class CloseResult:
    preserved_worktree_path: Optional[str] = None


def head() -> BranchStrategy:
    return BranchStrategy(type="head")


def merge_to_head() -> BranchStrategy:
    return BranchStrategy(type="merge-to-head")


def branch(name: str, base_branch: Optional[str] = None) -> BranchStrategy:
    return BranchStrategy(type="branch", branch=name, base_branch=base_branch)
