import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from swarmbox.cli import main
from swarmbox.init_service import scaffold


class InitCliTests(unittest.TestCase):
    def test_scaffold_creates_all_core_files(self):
        with tempfile.TemporaryDirectory() as td:
            main_file = scaffold(
                td,
                agent_id="codex-cli",
                model="gpt-5.4-codex",
                template_name="parallel-planner-with-review",
                backlog_manager_name="github-issues",
                sandbox_provider_name="podman",
                create_label=False,
            )
            config = Path(td) / ".swarmbox"
            self.assertEqual(main_file, "main.py")
            self.assertTrue((config / "Containerfile").exists())
            self.assertTrue((config / "main.py").exists())
            self.assertTrue((config / "plan-prompt.md").exists())
            self.assertTrue((config / "review-prompt.md").exists())
            self.assertTrue((config / "swarmbox.json").exists())
            self.assertIn("codex", (config / "main.py").read_text(encoding="utf-8"))

    def test_cli_init_interactive_accepts_defaults_without_building(self):
        with tempfile.TemporaryDirectory() as td:
            answers = StringIO("\n\n\n\n\n\n\n")
            with patch("sys.stdin", answers), redirect_stdout(StringIO()):
                self.assertEqual(main(["init", "--cwd", td, "--interactive"]), 0)
            config = Path(td) / ".swarmbox"
            self.assertTrue((config / "Dockerfile").exists())
            self.assertTrue((config / "prompt.md").exists())

    def test_cli_templates_list_runs(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["templates", "list"]), 0)
        self.assertIn("parallel-planner-with-review", output.getvalue())

    def test_cli_agents_list_runs(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["agents", "list"]), 0)


if __name__ == "__main__":
    unittest.main()
