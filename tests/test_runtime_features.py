import sys
import tempfile
import time
import unittest
from pathlib import Path
import subprocess

from swarmbox.cancellation import CancellationToken, CancelledError
from swarmbox.copying import copy_to_worktree
from swarmbox.errors import AgentIdleTimeoutError, CopyError, SandboxStartTimeoutError
from swarmbox.models import AgentProvider, ExecResult, PrintCommand, Timeouts
from swarmbox.orchestrator import OrchestrateOptions, orchestrate
from swarmbox.sandbox import SandboxProvider
from swarmbox.session_paths import default_session_paths_layer, session_paths_layer
from swarmbox.streaming import TextDeltaBuffer
from swarmbox.sync import build_recovery_message


class FakeSandbox:
    worktree_path = "/sandbox/workspace"

    def __init__(self):
        self.commands = []

    def exec(self, command, on_line=None, cwd=None, sudo=False, stdin=None):
        self.commands.append((command, cwd, sudo, stdin))
        if "rev-parse --abbrev-ref" in command:
            return ExecResult("swarmbox/temp\n", "", 0)
        if command.startswith("git config"):
            return ExecResult("", "", 0)
        if command.startswith("git checkout"):
            return ExecResult("", "", 0)
        if on_line:
            on_line('{"type":"response.output_text.delta","delta":"Hello. "}')
            on_line('{"type":"tool_call","name":"edit","args":{"path":"a.py"}}')
        return ExecResult("Hello. <promise>COMPLETE</promise>", "", 0)

    def close(self):
        pass


class RuntimeFeatureTests(unittest.TestCase):
    def test_text_delta_buffer_flushes_readable_chunks(self):
        chunks = []
        buffer = TextDeltaBuffer(chunks.append, debounce_seconds=0.01)
        buffer.write("Hello")
        buffer.write(". ")
        self.assertEqual(chunks, ["Hello. "])
        buffer.write("tail")
        buffer.flush()
        self.assertEqual(chunks[-1], "tail")

    def test_file_logging_callback_receives_stream_events(self):
        events = []
        provider = AgentProvider(
            "fake",
            lambda options: PrintCommand(command="agent", stdin=options.prompt),
            parse_stream_line_fn=lambda line: __import__("swarmbox.agents").agents._parse_codex_line(line),
        )
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            Path(repo, "README.md").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            result = orchestrate(
                OrchestrateOptions(
                    host_repo_dir=repo,
                    sandbox=FakeSandbox(),
                    sandbox_repo_dir="/sandbox/workspace",
                    host_worktree_path=repo,
                    apply_to_host=None,
                    prompt="do it",
                    provider=provider,
                    branch=None,
                    iterations=1,
                    log=lambda message: None,
                    on_agent_stream_event=events.append,
                )
            )
        self.assertEqual(result.completion_signal, "<promise>COMPLETE</promise>")
        self.assertEqual([e.type for e in events], ["text", "tool_call"])
        self.assertEqual(events[0].message, "Hello. ")
        self.assertEqual(events[1].name, "edit")

    def test_idle_timeout_kills_quiet_local_process(self):
        from swarmbox.sandbox import LocalHandle

        handle = LocalHandle(worktree_path=tempfile.gettempdir())
        started = time.monotonic()
        with self.assertRaises(AgentIdleTimeoutError):
            handle.exec(
                "%s -c 'import time; time.sleep(2)'"
                % sys.executable,
                idle_timeout_seconds=0.1,
            )
        self.assertLess(time.monotonic() - started, 1.5)

    def test_pre_cancelled_signal_stops_local_process(self):
        from swarmbox.sandbox import LocalHandle

        token = CancellationToken()
        token.cancel("stop")
        handle = LocalHandle(worktree_path=tempfile.gettempdir())
        with self.assertRaises(CancelledError):
            handle.exec("echo should-not-run", signal=token)

    def test_copy_to_worktree_timeout_is_enforced(self):
        with tempfile.TemporaryDirectory() as host, tempfile.TemporaryDirectory() as worktree:
            Path(host, "a.txt").write_text("a", encoding="utf-8")
            with self.assertRaises(CopyError):
                copy_to_worktree(["a.txt"], host, worktree, timeout_ms=0)

    def test_recovery_message_formats_remaining_steps(self):
        message = build_recovery_message(
            ".swarmbox/patches/failed",
            failed_step="diff",
            has_commits=True,
            has_diff=True,
            has_untracked=True,
            branch="swarmbox/test",
        )
        self.assertIn("Patch application failed at step 2", message)
        self.assertIn("git apply ../../.swarmbox/patches/failed/changes.patch", message)
        self.assertIn("cp -r ../../.swarmbox/patches/failed/untracked/* .", message)

    def test_session_paths_are_configurable(self):
        explicit = session_paths_layer("/host/projects", "/sandbox/projects")
        self.assertEqual(explicit.host_projects_dir, "/host/projects")
        default = default_session_paths_layer()
        self.assertTrue(default.host_projects_dir.endswith(".claude/projects"))

    def test_sandbox_start_timeout_is_passed_to_provider_factory(self):
        seen = {}

        def factory(worktree_path, env, timeout_ms=None):
            seen["worktree_path"] = worktree_path
            seen["env"] = env
            seen["timeout_ms"] = timeout_ms
            return FakeSandbox()

        provider = SandboxProvider("none", "fake", factory=factory)
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            Path(repo, "README.md").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            from swarmbox import create_sandbox

            sandbox = create_sandbox(
                branch="swarmbox/timeout",
                sandbox=provider,
                cwd=repo,
                timeouts=Timeouts(sandbox_start_ms=123),
            )
            sandbox.close()
        self.assertEqual(seen["timeout_ms"], 123)

    def test_sandbox_provider_create_enforces_start_timeout(self):
        def factory():
            time.sleep(0.2)
            return FakeSandbox()

        provider = SandboxProvider("isolated", "slow", factory=factory)
        started = time.monotonic()
        with self.assertRaises(SandboxStartTimeoutError):
            provider.create(timeout_ms=1)
        self.assertLess(time.monotonic() - started, 0.15)


if __name__ == "__main__":
    unittest.main()
