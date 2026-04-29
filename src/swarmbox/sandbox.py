import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from .cancellation import throw_if_cancelled
from .copying import copy_tree
from .errors import AgentIdleTimeoutError, ContainerError, SandboxError
from .models import ExecResult
from .mounts import MountConfig, default_image_name, resolve_user_mounts, volume_arg
from .terminal_cleanup import register_container, unregister_container


def _streaming_process(
    args: Sequence[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    stdin: Optional[str] = None,
    on_line: Optional[Callable[[str], None]] = None,
    stdio_passthrough: bool = False,
    idle_timeout_seconds: Optional[float] = None,
    signal: object = None,
) -> ExecResult:
    throw_if_cancelled(signal)
    if stdio_passthrough:
        proc = subprocess.Popen(args, cwd=cwd, env=env)
        while proc.poll() is None:
            try:
                throw_if_cancelled(signal)
            except BaseException:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise
            time.sleep(0.05)
        code = proc.returncode
        return ExecResult("", "", int(code))
    proc = subprocess.Popen(
        list(args),
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        if stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass

        output_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()

        def reader(kind: str, stream) -> None:
            try:
                for line in stream:
                    output_queue.put((kind, line))
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        threads: List[threading.Thread] = []
        if proc.stdout is not None:
            thread = threading.Thread(target=reader, args=("stdout", proc.stdout), daemon=True)
            thread.start()
            threads.append(thread)
        if proc.stderr is not None:
            thread = threading.Thread(target=reader, args=("stderr", proc.stderr), daemon=True)
            thread.start()
            threads.append(thread)

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        last_output = time.monotonic()

        while proc.poll() is None or not output_queue.empty():
            throw_if_cancelled(signal)
            if idle_timeout_seconds is not None and time.monotonic() - last_output >= idle_timeout_seconds:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise AgentIdleTimeoutError(
                    "Agent produced no output for %.3f seconds" % idle_timeout_seconds,
                    timeout_ms=int(idle_timeout_seconds * 1000),
                )
            try:
                kind, line = output_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            last_output = time.monotonic()
            if kind == "stdout":
                text = line.rstrip("\n")
                stdout_lines.append(text if on_line else line)
                if on_line:
                    on_line(text)
            else:
                stderr_lines.append(line)

        code = proc.wait()
        for thread in threads:
            thread.join(timeout=0.1)
        stdout = "\n".join(stdout_lines) if on_line else "".join(stdout_lines)
        return ExecResult(stdout=stdout, stderr="".join(stderr_lines), exit_code=code)
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise


@dataclass
class LocalHandle:
    worktree_path: str
    env: Dict[str, str] = field(default_factory=dict)

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
        del sudo
        effective_env = os.environ.copy()
        effective_env.update(self.env)
        return _streaming_process(
            ["sh", "-c", command],
            cwd=cwd or self.worktree_path,
            env=effective_env,
            stdin=stdin,
            on_line=on_line,
            idle_timeout_seconds=idle_timeout_seconds,
            signal=signal,
        )

    def interactive_exec(
        self,
        args: Sequence[str],
        cwd: Optional[str] = None,
        signal: object = None,
    ) -> int:
        effective_env = os.environ.copy()
        effective_env.update(self.env)
        return _streaming_process(
            list(args),
            cwd=cwd or self.worktree_path,
            env=effective_env,
            stdio_passthrough=True,
            signal=signal,
        ).exit_code

    def copy_file_in(self, host_path: str, sandbox_path: str) -> None:
        copy_tree(host_path, sandbox_path)

    def copy_file_out(self, sandbox_path: str, host_path: str) -> None:
        copy_tree(sandbox_path, host_path)

    def copy_in(self, host_path: str, sandbox_path: str) -> None:
        copy_tree(host_path, sandbox_path)

    def close(self) -> None:
        pass


@dataclass
class ContainerHandle:
    runtime: str
    container_name: str
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
        effective = "sudo %s" % command if sudo else command
        args = [self.runtime, "exec"]
        if stdin is not None:
            args.append("-i")
        if cwd:
            args.extend(["-w", cwd])
        args.extend([self.container_name, "sh", "-c", effective])
        return _streaming_process(
            args,
            stdin=stdin,
            on_line=on_line,
            idle_timeout_seconds=idle_timeout_seconds,
            signal=signal,
        )

    def interactive_exec(
        self,
        args: Sequence[str],
        cwd: Optional[str] = None,
        signal: object = None,
    ) -> int:
        exec_args = [self.runtime, "exec", "-it"]
        if cwd:
            exec_args.extend(["-w", cwd])
        exec_args.extend([self.container_name])
        exec_args.extend(args)
        return _streaming_process(exec_args, stdio_passthrough=True, signal=signal).exit_code

    def copy_file_in(self, host_path: str, sandbox_path: str) -> None:
        subprocess.check_call([self.runtime, "cp", host_path, "%s:%s" % (self.container_name, sandbox_path)])

    def copy_in(self, host_path: str, sandbox_path: str) -> None:
        self.copy_file_in(host_path, sandbox_path)

    def copy_file_out(self, sandbox_path: str, host_path: str) -> None:
        subprocess.check_call([self.runtime, "cp", "%s:%s" % (self.container_name, sandbox_path), host_path])

    def close(self) -> None:
        subprocess.call([self.runtime, "rm", "-f", self.container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unregister_container(self.runtime, self.container_name)


@dataclass
class SandboxProvider:
    tag: str
    name: str
    env: Dict[str, str] = field(default_factory=dict)
    sandbox_homedir: Optional[str] = None
    factory: Optional[Callable[..., object]] = None

    def create(self, **kwargs):
        if not self.factory:
            raise SandboxError("Sandbox provider %s has no factory" % self.name)
        return self.factory(**kwargs)


def create_bind_mount_sandbox_provider(
    name: str,
    create: Callable[..., object],
    env: Optional[Dict[str, str]] = None,
    sandbox_homedir: Optional[str] = None,
) -> SandboxProvider:
    return SandboxProvider(
        tag="bind-mount",
        name=name,
        env=env or {},
        sandbox_homedir=sandbox_homedir,
        factory=create,
    )


def create_isolated_sandbox_provider(
    name: str,
    create: Callable[..., object],
    env: Optional[Dict[str, str]] = None,
) -> SandboxProvider:
    return SandboxProvider(tag="isolated", name=name, env=env or {}, factory=create)


def no_sandbox(env: Optional[Dict[str, str]] = None) -> SandboxProvider:
    def create(worktree_path: str, **kwargs):
        create_env = kwargs.get("env", {})
        merged = {}
        merged.update(env or {})
        merged.update(create_env)
        return LocalHandle(worktree_path=worktree_path, env=merged)

    return SandboxProvider(tag="none", name="no-sandbox", env=env or {}, factory=create)


def _check_runtime(runtime: str) -> None:
    if shutil.which(runtime) is None:
        raise ContainerError("%s binary not found on PATH" % runtime)


def _container_provider(
    runtime: str,
    image_name: Optional[str] = None,
    mounts: Optional[Iterable[MountConfig]] = None,
    env: Optional[Dict[str, str]] = None,
    network: Optional[Iterable[str]] = None,
    selinux_label: Optional[str] = None,
) -> SandboxProvider:
    sandbox_homedir = "/home/agent"
    user_mounts = resolve_user_mounts(mounts or [], sandbox_homedir)

    def create(worktree_path: str, host_repo_path: str, internal_mounts, env: Dict[str, str]):
        _check_runtime(runtime)
        name = "swarmbox-%s" % uuid.uuid4()
        all_mounts = list(internal_mounts) + user_mounts
        volume_args = []
        for mount in all_mounts:
            extra = selinux_label if runtime == "podman" and selinux_label else None
            volume_args.extend(["-v", volume_arg(mount, extra=extra)])
        env_args = []
        merged_env = dict(env)
        merged_env["HOME"] = sandbox_homedir
        for key, value in merged_env.items():
            env_args.extend(["-e", "%s=%s" % (key, value)])
        network_args: List[str] = []
        for net in network or []:
            network_args.extend(["--network", net])
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        run_args = [
            runtime,
            "run",
            "-d",
            "--name",
            name,
            "--user",
            "%s:%s" % (uid, gid),
            "-w",
            worktree_path,
        ]
        run_args.extend(network_args)
        run_args.extend(env_args)
        run_args.extend(volume_args)
        if runtime == "podman":
            run_args.extend(["--entrypoint", "sleep"])
        run_args.append(image_name or default_image_name(host_repo_path))
        if runtime == "podman":
            run_args.append("infinity")
        subprocess.check_call(run_args)
        register_container(runtime, name)
        return ContainerHandle(runtime=runtime, container_name=name, worktree_path=worktree_path)

    return create_bind_mount_sandbox_provider(
        name=runtime,
        create=create,
        env=env or {},
        sandbox_homedir=sandbox_homedir,
    )


def docker(
    image_name: Optional[str] = None,
    mounts: Optional[Iterable[MountConfig]] = None,
    env: Optional[Dict[str, str]] = None,
    network: Optional[Iterable[str]] = None,
) -> SandboxProvider:
    return _container_provider("docker", image_name=image_name, mounts=mounts, env=env, network=network)


def podman(
    image_name: Optional[str] = None,
    mounts: Optional[Iterable[MountConfig]] = None,
    env: Optional[Dict[str, str]] = None,
    network: Optional[Iterable[str]] = None,
    selinux_label: str = "z",
) -> SandboxProvider:
    return _container_provider(
        "podman",
        image_name=image_name,
        mounts=mounts,
        env=env,
        network=network,
        selinux_label=selinux_label,
    )


def vercel(**options) -> SandboxProvider:
    def create(env: Dict[str, str]):
        try:
            from vercel.sandbox import Sandbox  # type: ignore
        except Exception as exc:
            raise SandboxError("Install SwarmBox with the 'vercel' extra to use vercel()") from exc
        params = dict(options)
        params["env"] = env
        sandbox = Sandbox.create(params)

        class VercelHandle:
            worktree_path = "/vercel/sandbox/workspace"

            def exec(self, command, on_line=None, cwd=None, sudo=False, stdin=None, idle_timeout_seconds=None, signal=None):
                del stdin
                throw_if_cancelled(signal)
                result = sandbox.run_command(cmd="sh", args=["-c", command], cwd=cwd or self.worktree_path, sudo=sudo)
                stdout = result.stdout()
                stderr = result.stderr()
                if on_line:
                    for line in stdout.splitlines():
                        throw_if_cancelled(signal)
                        on_line(line)
                return ExecResult(stdout, stderr, result.exit_code)

            def copy_in(self, host_path, sandbox_path):
                if hasattr(sandbox, "fs") and hasattr(sandbox.fs, "upload_file"):
                    sandbox.fs.upload_file(host_path, sandbox_path)
                    return
                if hasattr(sandbox, "upload_file"):
                    sandbox.upload_file(host_path, sandbox_path)
                    return
                raise SandboxError("The installed Vercel SDK does not expose file upload support")

            def copy_file_in(self, host_path, sandbox_path):
                self.copy_in(host_path, sandbox_path)

            def copy_file_out(self, sandbox_path, host_path):
                if hasattr(sandbox, "fs") and hasattr(sandbox.fs, "download_file"):
                    sandbox.fs.download_file(sandbox_path, host_path)
                    return
                if hasattr(sandbox, "download_file"):
                    sandbox.download_file(sandbox_path, host_path)
                    return
                raise SandboxError("The installed Vercel SDK does not expose file download support")

            def interactive_exec(self, args, cwd=None, signal=None):
                command = shlex.join(args)
                return self.exec(command, cwd=cwd, signal=signal).exit_code

            def close(self):
                sandbox.stop()

        return VercelHandle()

    return create_isolated_sandbox_provider("vercel", create=create, env=options.get("env"))


def daytona(**options) -> SandboxProvider:
    def create(env: Dict[str, str]):
        del env
        try:
            from daytona_sdk import Daytona  # type: ignore
        except Exception as exc:
            raise SandboxError("Install SwarmBox with the 'daytona' extra to use daytona()") from exc
        client = Daytona(api_key=options.get("api_key"), api_url=options.get("api_url"), target=options.get("target"))
        sandbox = client.create(options.get("create"))
        workdir = sandbox.get_work_dir() or sandbox.get_user_home_dir() or "/home/daytona"

        class DaytonaHandle:
            worktree_path = workdir

            def exec(self, command, on_line=None, cwd=None, sudo=False, stdin=None, idle_timeout_seconds=None, signal=None):
                del stdin
                throw_if_cancelled(signal)
                effective = "sudo %s" % command if sudo else command
                response = sandbox.process.execute_command(effective, cwd or self.worktree_path)
                stdout = response.result
                if on_line:
                    for line in stdout.splitlines():
                        throw_if_cancelled(signal)
                        on_line(line)
                return ExecResult(stdout, "", response.exit_code)

            def copy_in(self, host_path, sandbox_path):
                sandbox.fs.upload_file(host_path, sandbox_path)

            def copy_file_in(self, host_path, sandbox_path):
                self.copy_in(host_path, sandbox_path)

            def copy_file_out(self, sandbox_path, host_path):
                sandbox.fs.download_file(sandbox_path, host_path)

            def interactive_exec(self, args, cwd=None, signal=None):
                command = shlex.join(args)
                return self.exec(command, cwd=cwd, signal=signal).exit_code

            def close(self):
                client.delete(sandbox)

        return DaytonaHandle()

    return create_isolated_sandbox_provider("daytona", create=create, env=options.get("env"))
