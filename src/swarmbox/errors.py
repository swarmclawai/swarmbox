class SwarmBoxError(Exception):
    """Base class for SwarmBox errors."""


class CwdError(SwarmBoxError):
    pass


class PromptError(SwarmBoxError):
    pass


class PromptArgError(PromptError):
    pass


class PromptExpansionTimeoutError(PromptError):
    pass


class WorktreeError(SwarmBoxError):
    pass


class WorktreeTimeoutError(WorktreeError):
    pass


class SandboxError(SwarmBoxError):
    pass


class SandboxStartTimeoutError(SandboxError):
    def __init__(self, message: str, timeout_ms: int):
        super().__init__(message)
        self.timeout_ms = timeout_ms


class ExecError(SandboxError):
    def __init__(self, command: str, message: str):
        super().__init__(message)
        self.command = command


class HookTimeoutError(SandboxError):
    pass


class AgentError(SandboxError):
    def __init__(self, message: str, preserved_worktree_path=None):
        super().__init__(message)
        self.preserved_worktree_path = preserved_worktree_path


class AgentIdleTimeoutError(AgentError):
    def __init__(self, message: str, timeout_ms: int, preserved_worktree_path=None):
        super().__init__(message, preserved_worktree_path=preserved_worktree_path)
        self.timeout_ms = timeout_ms


class SessionCaptureError(SandboxError):
    def __init__(self, message: str, session_id: str):
        super().__init__(message)
        self.session_id = session_id


class CopyError(SandboxError):
    pass


class SyncError(SandboxError):
    pass


class ContainerError(SandboxError):
    pass


class DockerError(ContainerError):
    pass


class PodmanError(ContainerError):
    pass


class InitError(SwarmBoxError):
    pass
