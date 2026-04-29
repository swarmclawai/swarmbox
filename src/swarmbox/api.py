from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from .cancellation import throw_if_cancelled
from .copying import copy_to_worktree as copy_paths_to_worktree
from .env import resolve_env
from .lifecycle import run_host_hooks
from .models import (
    BranchStrategy,
    CloseResult,
    Hooks,
    InteractiveResult,
    RunResult,
    Timeouts,
    branch as branch_strategy,
    head,
    merge_to_head,
)
from .mounts import SANDBOX_REPO_DIR, MountConfig, resolve_git_mounts
from .orchestrator import OrchestrateOptions, orchestrate
from .prompts import collect_missing_prompt_args, resolve_prompt
from .sandbox import SandboxProvider, no_sandbox
from .sync import sync_in, sync_out
from .worktree import (
    create_worktree_info,
    current_branch,
    has_uncommitted_changes,
    prune_stale,
    remove_worktree,
    resolve_cwd,
)


def _merge_env(host_repo_dir: str, agent_env, sandbox_env, explicit_env=None):
    result = {}
    result.update(resolve_env(host_repo_dir))
    result.update(agent_env or {})
    result.update(sandbox_env or {})
    result.update(explicit_env or {})
    return result


def _default_branch_strategy(provider: SandboxProvider) -> BranchStrategy:
    return merge_to_head() if provider.tag == "isolated" else head()


def _create_handle(
    sandbox: SandboxProvider,
    host_repo_dir: str,
    worktree_path: str,
    env,
    copy_paths: Optional[Sequence[str]] = None,
    timeouts: Optional[Timeouts] = None,
    signal: object = None,
):
    throw_if_cancelled(signal)
    if sandbox.tag == "none":
        return sandbox.create(worktree_path=worktree_path, env=env), worktree_path, None
    if sandbox.tag == "isolated":
        handle = sandbox.create(env=env)
        sync_in(worktree_path, handle, timeout_ms=timeouts.sync_in_ms if timeouts else None, signal=signal)
        for relative in copy_paths or []:
            throw_if_cancelled(signal)
            host_path = Path(worktree_path) / relative
            if host_path.exists():
                handle.copy_in(str(host_path), str(Path(handle.worktree_path) / relative))
        return handle, handle.worktree_path, lambda: sync_out(
            worktree_path,
            handle,
            timeout_ms=timeouts.sync_out_ms if timeouts else None,
            signal=signal,
        )
    git_mounts = resolve_git_mounts(str(Path(host_repo_dir) / ".git"))
    internal_mounts = [
        MountConfig(str(Path(worktree_path).resolve()), SANDBOX_REPO_DIR),
        *git_mounts,
    ]
    handle = sandbox.create(
        worktree_path=SANDBOX_REPO_DIR,
        host_repo_path=host_repo_dir,
        internal_mounts=internal_mounts,
        env=env,
    )
    return handle, handle.worktree_path, None


def build_log_filename(resolved_branch: str, target_branch: str = None, name: str = None) -> str:
    def sanitize(value: str) -> str:
        return "".join("-" if c in '/\\:*?"<>|' else c for c in value)

    suffix = ("-" + "".join(c if c.isalnum() or c in "_.-" else "-" for c in name.lower())) if name else ""
    if target_branch:
        return "%s-%s%s.log" % (sanitize(target_branch), sanitize(resolved_branch), suffix)
    return "%s%s.log" % (sanitize(resolved_branch), suffix)


def run(
    *,
    agent,
    sandbox: SandboxProvider,
    cwd: str = None,
    prompt: str = None,
    prompt_file: str = None,
    prompt_args=None,
    max_iterations: int = 1,
    hooks: Optional[Hooks] = None,
    logging=None,
    completion_signal=None,
    idle_timeout_seconds: int = 600,
    name: str = None,
    copy_to_worktree: Optional[Sequence[str]] = None,
    branch_strategy: Optional[BranchStrategy] = None,
    resume_session: str = None,
    signal: object = None,
    timeouts: Optional[Timeouts] = None,
    session_paths=None,
    config_dir: str = ".swarmbox",
) -> RunResult:
    throw_if_cancelled(signal)
    host_repo_dir = resolve_cwd(cwd)
    strategy = branch_strategy or _default_branch_strategy(sandbox)
    if strategy.type == "head" and sandbox.tag == "isolated":
        raise ValueError("head branch strategy is not supported with isolated providers")
    if strategy.type == "head" and copy_to_worktree:
        raise ValueError("copy_to_worktree is not supported with head branch strategy")
    if resume_session and max_iterations > 1:
        raise ValueError("resume_session is incompatible with max_iterations > 1")

    current_host_branch = current_branch(host_repo_dir)
    branch = strategy.branch if strategy.type == "branch" else None
    lifecycle_branch = current_host_branch if strategy.type == "head" else branch
    worktree = None
    provider_handle = None
    log_handle = None
    preserved_path = None
    try:
        if strategy.type == "head":
            worktree_path = host_repo_dir
            resolved_branch = current_host_branch
            if hooks:
                run_host_hooks(hooks.host.on_worktree_ready, worktree_path, signal=signal)
        else:
            prune_stale(host_repo_dir, config_dir=config_dir)
            worktree = create_worktree_info(
                host_repo_dir,
                branch=branch,
                base_branch=strategy.base_branch,
                name=name,
                config_dir=config_dir,
            )
            worktree_path = worktree.path
            resolved_branch = worktree.branch
            if copy_to_worktree and sandbox.tag != "isolated":
                copy_paths_to_worktree(
                    copy_to_worktree,
                    host_repo_dir,
                    worktree_path,
                    timeout_ms=timeouts.copy_to_worktree_ms if timeouts else None,
                )
            if hooks:
                run_host_hooks(hooks.host.on_worktree_ready, worktree_path, signal=signal)

        builtin_args = {"SOURCE_BRANCH": resolved_branch, "TARGET_BRANCH": current_host_branch}
        resolved = resolve_prompt(prompt=prompt, prompt_file=prompt_file, prompt_args=prompt_args, builtin_args=builtin_args)
        env = _merge_env(host_repo_dir, agent.env, sandbox.env)
        provider_handle, sandbox_repo_dir, apply_to_host = _create_handle(
            sandbox,
            host_repo_dir,
            worktree_path,
            env,
            copy_paths=copy_to_worktree if sandbox.tag == "isolated" else None,
            timeouts=timeouts,
            signal=signal,
        )

        log_file_path = None
        stream_callback = None
        if logging is None or (isinstance(logging, dict) and logging.get("type") == "file"):
            log_dir = Path(host_repo_dir) / config_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file_path = logging.get("path") if isinstance(logging, dict) and logging.get("path") else str(log_dir / build_log_filename(resolved_branch, current_host_branch if strategy.type == "merge-to-head" else None, name))
            if isinstance(logging, dict):
                stream_callback = logging.get("on_agent_stream_event") or logging.get("onAgentStreamEvent")
            log_handle = open(log_file_path, "a", encoding="utf-8")

            def log(message):
                log_handle.write(str(message) + "\n")
                log_handle.flush()
        else:
            log = print

        result = orchestrate(
            OrchestrateOptions(
                host_repo_dir=host_repo_dir,
                sandbox=provider_handle,
                sandbox_repo_dir=sandbox_repo_dir,
                host_worktree_path=worktree_path,
                apply_to_host=apply_to_host,
                prompt=resolved.text,
                provider=agent,
                branch=lifecycle_branch,
                iterations=max_iterations,
                completion_signal=completion_signal,
                idle_timeout_seconds=idle_timeout_seconds,
                hooks=hooks,
                name=name,
                log=log,
                resume_session=resume_session,
                skip_prompt_expansion=resolved.source == "inline",
                on_agent_stream_event=stream_callback,
                signal=signal,
                session_paths=session_paths,
            )
        )
        final_result = replace(result, log_file_path=log_file_path)
    finally:
        if log_handle:
            log_handle.close()
        if provider_handle:
            provider_handle.close()
        if worktree and Path(worktree.path).exists():
            if has_uncommitted_changes(worktree.path):
                preserved_path = worktree.path
            else:
                remove_worktree(worktree.path)
    return replace(final_result, preserved_worktree_path=preserved_path)


def interactive(
    *,
    agent,
    sandbox: SandboxProvider = None,
    cwd: str = None,
    prompt: str = None,
    prompt_file: str = None,
    prompt_args=None,
    prompt_input=None,
    name: str = None,
    branch_strategy: Optional[BranchStrategy] = None,
    hooks: Optional[Hooks] = None,
    copy_to_worktree: Optional[Sequence[str]] = None,
    env=None,
    signal: object = None,
    timeouts: Optional[Timeouts] = None,
    config_dir: str = ".swarmbox",
) -> InteractiveResult:
    throw_if_cancelled(signal)
    sandbox = sandbox or no_sandbox()
    host_repo_dir = resolve_cwd(cwd)
    strategy = branch_strategy or _default_branch_strategy(sandbox)
    if strategy.type == "head" and sandbox.tag == "isolated":
        raise ValueError("head branch strategy is not supported with isolated providers")
    if strategy.type == "head" and copy_to_worktree:
        raise ValueError("copy_to_worktree is not supported with head branch strategy")
    current_host_branch = current_branch(host_repo_dir)
    worktree = None
    handle = None
    try:
        if strategy.type == "head":
            worktree_path = host_repo_dir
            resolved_branch = current_host_branch
        else:
            worktree = create_worktree_info(
                host_repo_dir,
                branch=strategy.branch if strategy.type == "branch" else None,
                base_branch=strategy.base_branch,
                name=name,
                config_dir=config_dir,
            )
            worktree_path = worktree.path
            resolved_branch = worktree.branch
            if copy_to_worktree and sandbox.tag != "isolated":
                copy_paths_to_worktree(
                    copy_to_worktree,
                    host_repo_dir,
                    worktree_path,
                    timeout_ms=timeouts.copy_to_worktree_ms if timeouts else None,
                )
        if hooks:
            run_host_hooks(hooks.host.on_worktree_ready, worktree_path, signal=signal)
        if prompt_file is not None:
            raw_prompt = Path(prompt_file).read_text(encoding="utf-8")
            prompt_args = collect_missing_prompt_args(raw_prompt, prompt_args, prompt_input)
        resolved = resolve_prompt(
            prompt=prompt,
            prompt_file=prompt_file,
            prompt_args=prompt_args,
            builtin_args={"SOURCE_BRANCH": resolved_branch, "TARGET_BRANCH": current_host_branch},
            allow_empty=True,
        )
        merged_env = _merge_env(host_repo_dir, agent.env, sandbox.env, env)
        handle, sandbox_repo_dir, apply_to_host = _create_handle(
            sandbox,
            host_repo_dir,
            worktree_path,
            merged_env,
            copy_paths=copy_to_worktree if sandbox.tag == "isolated" else None,
            timeouts=timeouts,
            signal=signal,
        )
        args = agent.build_interactive_args(resolved.text, dangerously_skip_permissions=sandbox.tag != "none")
        exit_code = handle.interactive_exec(args, cwd=sandbox_repo_dir, signal=signal)
        if apply_to_host:
            apply_to_host()
        commits = []
        return InteractiveResult(commits=commits, branch=resolved_branch, exit_code=exit_code)
    finally:
        if handle:
            handle.close()
        if worktree and Path(worktree.path).exists() and not has_uncommitted_changes(worktree.path):
            remove_worktree(worktree.path)


class Sandbox:
    def __init__(self, branch: str, worktree_path: str, host_repo_dir: str, provider, handle, sandbox_repo_dir: str, apply_to_host, hooks=None):
        self.branch = branch
        self.worktree_path = worktree_path
        self._host_repo_dir = host_repo_dir
        self._provider = provider
        self._handle = handle
        self._sandbox_repo_dir = sandbox_repo_dir
        self._apply_to_host = apply_to_host
        self._hooks = hooks
        self._closed = False

    def run(self, **options) -> RunResult:
        agent = options.pop("agent")
        resolved = resolve_prompt(
            prompt=options.pop("prompt", None),
            prompt_file=options.pop("prompt_file", None),
            prompt_args=options.pop("prompt_args", None),
            builtin_args={"SOURCE_BRANCH": self.branch, "TARGET_BRANCH": current_branch(self._host_repo_dir)},
        )
        return orchestrate(
            OrchestrateOptions(
                host_repo_dir=self._host_repo_dir,
                sandbox=self._handle,
                sandbox_repo_dir=self._sandbox_repo_dir,
                host_worktree_path=self.worktree_path,
                apply_to_host=self._apply_to_host,
                prompt=resolved.text,
                provider=agent,
                branch=self.branch,
                iterations=options.pop("max_iterations", 1),
                completion_signal=options.pop("completion_signal", None),
                idle_timeout_seconds=options.pop("idle_timeout_seconds", 600),
                hooks=options.pop("hooks", self._hooks),
                name=options.pop("name", None),
                log=options.pop("log", print),
                resume_session=options.pop("resume_session", None),
                skip_prompt_expansion=resolved.source == "inline",
                on_agent_stream_event=options.pop("on_agent_stream_event", None),
                signal=options.pop("signal", None),
                session_paths=options.pop("session_paths", None),
            )
        )

    def interactive(self, **options) -> InteractiveResult:
        agent = options.pop("agent")
        prompt_file = options.pop("prompt_file", None)
        prompt_args = options.pop("prompt_args", None)
        if prompt_file is not None:
            raw_prompt = Path(prompt_file).read_text(encoding="utf-8")
            prompt_args = collect_missing_prompt_args(
                raw_prompt,
                prompt_args,
                options.pop("prompt_input", None),
            )
        resolved = resolve_prompt(
            prompt=options.pop("prompt", None),
            prompt_file=prompt_file,
            prompt_args=prompt_args,
            builtin_args={"SOURCE_BRANCH": self.branch, "TARGET_BRANCH": current_branch(self._host_repo_dir)},
            allow_empty=True,
        )
        args = agent.build_interactive_args(resolved.text, dangerously_skip_permissions=True)
        exit_code = self._handle.interactive_exec(
            args,
            cwd=self._sandbox_repo_dir,
            signal=options.pop("signal", None),
        )
        if self._apply_to_host:
            self._apply_to_host()
        return InteractiveResult(commits=[], branch=self.branch, exit_code=exit_code)

    def close(self) -> CloseResult:
        if self._closed:
            return CloseResult()
        self._closed = True
        self._handle.close()
        if has_uncommitted_changes(self.worktree_path):
            return CloseResult(preserved_worktree_path=self.worktree_path)
        remove_worktree(self.worktree_path)
        return CloseResult()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def create_sandbox(
    *,
    branch: str,
    sandbox: SandboxProvider,
    cwd: str = None,
    base_branch: str = None,
    hooks: Optional[Hooks] = None,
    copy_to_worktree: Optional[Sequence[str]] = None,
    signal: object = None,
    timeouts: Optional[Timeouts] = None,
    config_dir: str = ".swarmbox",
) -> Sandbox:
    throw_if_cancelled(signal)
    host_repo_dir = resolve_cwd(cwd)
    info = create_worktree_info(host_repo_dir, branch=branch, base_branch=base_branch, config_dir=config_dir)
    if copy_to_worktree and sandbox.tag != "isolated":
        copy_paths_to_worktree(
            copy_to_worktree,
            host_repo_dir,
            info.path,
            timeout_ms=timeouts.copy_to_worktree_ms if timeouts else None,
        )
    if hooks:
        run_host_hooks(hooks.host.on_worktree_ready, info.path, signal=signal)
    env = _merge_env(host_repo_dir, {}, sandbox.env)
    handle, sandbox_repo_dir, apply_to_host = _create_handle(
        sandbox,
        host_repo_dir,
        info.path,
        env,
        copy_paths=copy_to_worktree if sandbox.tag == "isolated" else None,
        timeouts=timeouts,
        signal=signal,
    )
    return Sandbox(branch=info.branch, worktree_path=info.path, host_repo_dir=host_repo_dir, provider=sandbox, handle=handle, sandbox_repo_dir=sandbox_repo_dir, apply_to_host=apply_to_host, hooks=hooks)


class Worktree:
    def __init__(self, branch: str, worktree_path: str, host_repo_dir: str):
        self.branch = branch
        self.worktree_path = worktree_path
        self._host_repo_dir = host_repo_dir

    def run(self, **options) -> RunResult:
        options.setdefault("cwd", self._host_repo_dir)
        options["branch_strategy"] = branch_strategy(self.branch)
        return run(**options)

    def interactive(self, **options) -> InteractiveResult:
        options.setdefault("cwd", self._host_repo_dir)
        options["branch_strategy"] = branch_strategy(self.branch)
        return interactive(**options)

    def create_sandbox(self, **options) -> Sandbox:
        options.setdefault("cwd", self._host_repo_dir)
        options["branch"] = self.branch
        return create_sandbox(**options)

    def close(self) -> CloseResult:
        if has_uncommitted_changes(self.worktree_path):
            return CloseResult(preserved_worktree_path=self.worktree_path)
        remove_worktree(self.worktree_path)
        return CloseResult()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def create_worktree(
    *,
    branch_strategy: BranchStrategy,
    cwd: str = None,
    copy_to_worktree: Optional[Sequence[str]] = None,
    hooks: Optional[Hooks] = None,
    signal: object = None,
    timeouts: Optional[Timeouts] = None,
    config_dir: str = ".swarmbox",
) -> Worktree:
    throw_if_cancelled(signal)
    if branch_strategy.type == "head":
        raise ValueError("create_worktree only supports branch and merge-to-head strategies")
    host_repo_dir = resolve_cwd(cwd)
    info = create_worktree_info(
        host_repo_dir,
        branch=branch_strategy.branch if branch_strategy.type == "branch" else None,
        base_branch=branch_strategy.base_branch,
        config_dir=config_dir,
    )
    if copy_to_worktree:
        copy_paths_to_worktree(
            copy_to_worktree,
            host_repo_dir,
            info.path,
            timeout_ms=timeouts.copy_to_worktree_ms if timeouts else None,
        )
    if hooks:
        run_host_hooks(hooks.host.on_worktree_ready, info.path, signal=signal)
    return Worktree(info.branch, info.path, host_repo_dir)


async def arun(**kwargs):
    return run(**kwargs)


async def ainteractive(**kwargs):
    return interactive(**kwargs)


async def acreate_sandbox(**kwargs):
    return create_sandbox(**kwargs)


async def acreate_worktree(**kwargs):
    return create_worktree(**kwargs)
