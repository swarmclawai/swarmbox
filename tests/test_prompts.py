import tempfile
import unittest
from pathlib import Path

from swarmbox.prompts import (
    PromptArgError,
    collect_missing_prompt_args,
    find_missing_prompt_arg_keys,
    preprocess_prompt,
    resolve_prompt,
    substitute_prompt_args,
)


class FakeSandbox:
    def exec(self, command, **kwargs):
        return type("Result", (), {"stdout": "expanded\n", "stderr": "", "exit_code": 0})()


class PromptTests(unittest.TestCase):
    def test_prompt_file_substitution_includes_builtin_args(self):
        prompt = "from {{ SOURCE_BRANCH }} to {{TARGET_BRANCH}}: {{task}}"
        result = substitute_prompt_args(
            prompt,
            {"task": "ship"},
            builtin_args={"SOURCE_BRANCH": "feature/a", "TARGET_BRANCH": "main"},
        )
        self.assertEqual(result, "from feature/a to main: ship")

    def test_inline_prompt_rejects_args(self):
        with self.assertRaises(PromptArgError):
            resolve_prompt(prompt="literal {{value}}", prompt_args={"value": "x"})

    def test_missing_keys_ignore_builtins(self):
        missing = find_missing_prompt_arg_keys("{{SOURCE_BRANCH}} {{thing}}", {})
        self.assertEqual(missing, ["thing"])

    def test_collect_missing_prompt_args_prompts_in_order(self):
        values = iter(["LoginForm", "42"])
        args = collect_missing_prompt_args(
            "Fix {{COMPONENT}} issue {{ISSUE_NUM}} for {{COMPONENT}}",
            {"EXISTING": "kept"},
            lambda message: next(values),
        )
        self.assertEqual(
            args,
            {"EXISTING": "kept", "COMPONENT": "LoginForm", "ISSUE_NUM": "42"},
        )

    def test_shell_blocks_only_expand_after_substitution_marks_them(self):
        marked = substitute_prompt_args("hello !`echo hi` {{value}}", {"value": "!`rm -rf .`"})
        result = preprocess_prompt(marked, FakeSandbox(), "/repo")
        self.assertEqual(result, "hello expanded !`rm -rf .`")

    def test_resolve_prompt_file_is_relative_to_process_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "prompt.md"
            path.write_text("from file", encoding="utf-8")
            resolved = resolve_prompt(prompt_file=str(path))
            self.assertEqual(resolved.text, "from file")
            self.assertEqual(resolved.source, "file")


if __name__ == "__main__":
    unittest.main()
