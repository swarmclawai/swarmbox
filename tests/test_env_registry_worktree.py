import os
import tempfile
import unittest
from pathlib import Path

from swarmbox.agents import AGENT_REGISTRY, claude_code, codex, command_agent, pi
from swarmbox.env import resolve_env
from swarmbox.worktree import generate_temp_branch_name, sanitize_name


class EnvRegistryWorktreeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
