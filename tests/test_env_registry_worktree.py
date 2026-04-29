import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from swarmbox import branch, create_sandbox, create_worktree, no_sandbox
from swarmbox.agents import AGENT_REGISTRY, claude_code, codex, command_agent, pi
from swarmbox.env import resolve_env
from swarmbox.models import AgentProvider, PrintCommand
from swarmbox.worktree import generate_temp_branch_name, sanitize_name


class EnvRegistryWorktreeTests(unittest.TestCase):
    def _init_repo(self, td: str) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=td, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=td, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=td, check=True)
        Path(td, "README.md").write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=td, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=td, check=True, stdout=subprocess.DEVNULL)

    def test_env_file_falls_back_to_process_env_for_declared_keys(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".swarmbox"
            config.mkdir()
            (config / ".env").write_text("A=\nB=local\n# ignored\nC='quoted'\n", encoding="utf-8")
            old = os.environ.get("A")
            os.environ["A"] = "from-env"
            try:
                self.assertEqual(resolve_env(td), {"A": "from-env", "B": "local", "C": "quoted"})
            finally:
                if old is None:
                    os.environ.pop("A", None)
                else:
                    os.environ["A"] = old

    def test_registry_contains_swarmclaw_and_swarmvault_agents(self):
        self.assertIn("codex-cli", AGENT_REGISTRY)
        self.assertIn("claude-cli", AGENT_REGISTRY)
        self.assertIn("pi", AGENT_REGISTRY)
        self.assertIn("windsurf-cli", AGENT_REGISTRY)
        self.assertIn("qwen-code-cli", AGENT_REGISTRY)
        self.assertIn("antigravity-cli", AGENT_REGISTRY)
        self.assertIn("vscode-copilot-chat-cli", AGENT_REGISTRY)

    def test_codex_command_uses_stdin_to_avoid_argv_limits(self):
        cmd = codex("gpt-5.4-codex").build_print_command(prompt="hello")
        self.assertIn("codex exec", cmd.command)
        self.assertEqual(cmd.stdin, "hello")

    def test_generic_command_agent_formats_prompt_and_model(self):
        agent = command_agent(
            name="example",
            command_template=["agent", "run", "--model", "{model}", "{prompt}"],
            model="m1",
        )
        cmd = agent.build_print_command(prompt="do it")
        self.assertEqual(cmd.command, "agent run --model m1 'do it'")

    def test_claude_respects_permission_flag_and_parses_tool_commands(self):
        agent = claude_code("claude-sonnet-4-6")
        safe = agent.build_print_command(prompt="hello", dangerously_skip_permissions=False)
        self.assertNotIn("--dangerously-skip-permissions", safe.command)
        events = list(
            agent.parse_stream_line(
                '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"npm test"}}]}}'
            )
        )
        self.assertEqual(events[0].type, "tool_call")
        self.assertEqual(events[0].args, "npm test")

    def test_pi_parses_stream_events(self):
        agent = pi("claude-sonnet-4-6")
        text = list(
            agent.parse_stream_line(
                '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hello"}}'
            )
        )
        tool = list(
            agent.parse_stream_line(
                '{"type":"tool_execution_start","toolName":"Bash","args":{"command":"pytest"}}'
            )
        )
        result = list(
            agent.parse_stream_line(
                '{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"done"}]}]}'
            )
        )
        self.assertEqual(text[0].text, "Hello")
        self.assertEqual(tool[0].args, "pytest")
        self.assertEqual(result[0].result, "done")

    def test_branch_name_generation(self):
        self.assertEqual(sanitize_name("Ship It!"), "ship-it-")
        self.assertRegex(generate_temp_branch_name("Ship It!"), r"^swarmbox/ship-it-/\d{8}-\d{6}$")

    def test_owned_worktree_run_preserves_worktree_until_close(self):
        agent = AgentProvider(
            "noop",
            lambda options: PrintCommand("printf '<promise>COMPLETE</promise>'"),
        )
        with tempfile.TemporaryDirectory() as td:
            self._init_repo(td)
            worktree = create_worktree(branch_strategy=branch("swarmbox/owned-run"), cwd=td)
            worktree_path = Path(worktree.worktree_path)

            result = worktree.run(
                agent=agent,
                sandbox=no_sandbox(env={"HOME": td}),
                prompt="do it",
                logging={"type": "stdout"},
            )

            self.assertEqual(result.branch, "swarmbox/owned-run")
            self.assertTrue(worktree_path.exists())
            worktree.close()
            self.assertFalse(worktree_path.exists())

    def test_owned_worktree_sandbox_close_does_not_remove_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            self._init_repo(td)
            worktree = create_worktree(branch_strategy=branch("swarmbox/owned-sandbox"), cwd=td)
            worktree_path = Path(worktree.worktree_path)

            sandbox = worktree.create_sandbox(sandbox=no_sandbox(env={"HOME": td}))
            sandbox.close()

            self.assertTrue(worktree_path.exists())
            worktree.close()
            self.assertFalse(worktree_path.exists())

    def test_top_level_sandbox_close_removes_owned_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            self._init_repo(td)
            sandbox = create_sandbox(
                branch="swarmbox/top-level-sandbox",
                sandbox=no_sandbox(env={"HOME": td}),
                cwd=td,
            )
            worktree_path = Path(sandbox.worktree_path)

            sandbox.close()

            self.assertFalse(worktree_path.exists())


if __name__ == "__main__":
    unittest.main()
