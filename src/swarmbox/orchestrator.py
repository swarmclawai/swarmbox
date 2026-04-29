from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .cancellation import throw_if_cancelled
from .errors import AgentError
from .lifecycle import with_sandbox_lifecycle
from .models import AgentProvider, Commit, IterationResult, RunResult
from .prompts import preprocess_prompt
from .session_paths import SessionPaths, default_session_paths_layer
from .session_store import host_session_store, sandbox_session_store, transfer_session
from .streaming import AgentStreamEmitter, TextDeltaBuffer

DEFAULT_COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"
DEFAULT_IDLE_TIMEOUT_SECONDS = 600


@dataclass
class OrchestrateOptions:
    host_repo_dir: str
    sandbox: object
    sandbox_repo_dir: str
    host_worktree_path: str
    apply_to_host: Optional[Callable[[], None]]
    prompt: str
    provider: AgentProvider
    branch: Optional[str] = None
    iterations: int = 1
    completion_signal: Optional[Sequence[str]] = None
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    hooks: object = None
    name: Optional[str] = None
    log: Optional[Callable[[str], None]] = None
    resume_session: Optional[str] = None
    skip_prompt_expansion: bool = False
    on_agent_stream_event: Optional[Callable[[object], None]] = None
    signal: object = None
    session_paths: Optional[SessionPaths] = None


def _signals(value) -> List[str]:
    if value is None:
        return [DEFAULT_COMPLETION_SIGNAL]
    if isinstance(value, str):
        return [value]
    return list(value)


def invoke_agent(
    sandbox,
    sandbox_repo_dir: str,
    prompt: str,
    provider: AgentProvider,
    on_text: Callable[[str], None],
    on_tool_call: Callable[[str, str], None],
    resume_session: Optional[str] = None,
    idle_timeout_seconds: Optional[float] = None,
    signal: object = None,
) -> tuple:
    throw_if_cancelled(signal)
    result_text = ""
    session_id = None
    command = provider.build_print_command(prompt=prompt, resume_session=resume_session)
    text_buffer = TextDeltaBuffer(on_text)

    def on_line(line: str) -> None:
        nonlocal result_text, session_id
        throw_if_cancelled(signal)
        parsed_any = False
        for event in provider.parse_stream_line(line):
            parsed_any = True
            if event.type == "text" and event.text:
                text_buffer.write(event.text)
            elif event.type == "result" and event.result is not None:
                result_text = event.result
            elif event.type == "tool_call":
                text_buffer.flush()
                on_tool_call(event.name or "tool", event.args or "")
            elif event.type == "session_id":
                session_id = event.session_id
        if not parsed_any:
            text_buffer.write(line)

    try:
        result = sandbox.exec(
            command.command,
            on_line=on_line,
            cwd=sandbox_repo_dir,
            stdin=command.stdin,
            idle_timeout_seconds=idle_timeout_seconds,
            signal=signal,
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        result = sandbox.exec(
            command.command,
            on_line=on_line,
            cwd=sandbox_repo_dir,
            stdin=command.stdin,
        )
    text_buffer.dispose()
    if result.exit_code != 0:
        detail = result.stderr or result_text or "\n".join(result.stdout.splitlines()[-20:])
        raise AgentError("%s exited with code %s:\n%s" % (provider.name, result.exit_code, detail))
    return result_text or result.stdout, session_id


def orchestrate(options: OrchestrateOptions) -> RunResult:
    throw_if_cancelled(options.signal)
    completion_signals = _signals(options.completion_signal)
    all_iterations: List[IterationResult] = []
    all_commits: List[Commit] = []
    all_stdout = ""
    completion = None
    final_branch = ""

    log = options.log or (lambda msg: None)
    emitter = AgentStreamEmitter(options.on_agent_stream_event)
    session_paths = options.session_paths or default_session_paths_layer()

    for index in range(1, options.iterations + 1):
        throw_if_cancelled(options.signal)
        log("Iteration %s/%s" % (index, options.iterations))

        def work(base_head):
            del base_head
            throw_if_cancelled(options.signal)
            resume = options.resume_session if index == 1 else None
            if resume and hasattr(options.sandbox, "copy_file_in"):
                h_store = host_session_store(options.host_repo_dir, session_paths.host_projects_dir)
                s_store = sandbox_session_store(
                    options.sandbox_repo_dir,
                    options.sandbox,
                    session_paths.sandbox_projects_dir,
                )
                transfer_session(h_store, s_store, resume)

            full_prompt = (
                options.prompt
                if options.skip_prompt_expansion
                else preprocess_prompt(options.prompt, options.sandbox, options.sandbox_repo_dir)
            )
            chunks: List[str] = []
            tool_calls: List[str] = []

            def emit_text(text: str) -> None:
                chunks.append(text)
                log(text)
                emitter.emit_text(text, index)

            def emit_tool_call(name: str, args: str) -> None:
                tool_calls.append("%s %s" % (name, args))
                log("%s %s" % (name, args))
                emitter.emit_tool_call(name, args, index)

            output, session_id = invoke_agent(
                options.sandbox,
                options.sandbox_repo_dir,
                full_prompt,
                options.provider,
                on_text=emit_text,
                on_tool_call=emit_tool_call,
                resume_session=resume,
                idle_timeout_seconds=options.idle_timeout_seconds,
                signal=options.signal,
            )
            usage = None
            session_file = None
            if options.provider.capture_sessions and session_id and hasattr(options.sandbox, "copy_file_out"):
                h_store = host_session_store(options.host_repo_dir, session_paths.host_projects_dir)
                s_store = sandbox_session_store(
                    options.sandbox_repo_dir,
                    options.sandbox,
                    session_paths.sandbox_projects_dir,
                )
                transfer_session(s_store, h_store, session_id)
                session_file = h_store.session_file_path(session_id)
                try:
                    usage = options.provider.parse_session_usage(Path(session_file).read_text(encoding="utf-8"))
                except Exception:
                    usage = None
            return output, IterationResult(session_id=session_id, session_file_path=session_file, usage=usage)

        (iteration_output, iteration_result), branch, commits = with_sandbox_lifecycle(
            host_repo_dir=options.host_repo_dir,
            sandbox=options.sandbox,
            sandbox_repo_dir=options.sandbox_repo_dir,
            host_worktree_path=options.host_worktree_path,
            hooks=options.hooks,
            branch=options.branch,
            apply_to_host=options.apply_to_host,
            work=work,
            signal=options.signal,
        )
        final_branch = branch
        all_stdout += iteration_output
        all_commits.extend(commits)
        all_iterations.append(iteration_result)
        matched = next((signal for signal in completion_signals if signal in iteration_output), None)
        if matched is not None:
            completion = matched
            break

    return RunResult(
        iterations=all_iterations,
        completion_signal=completion,
        stdout=all_stdout,
        commits=all_commits,
        branch=final_branch,
    )
