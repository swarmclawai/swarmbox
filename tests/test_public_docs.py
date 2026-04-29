from pathlib import Path
import unittest


class PublicDocsTests(unittest.TestCase):
    def test_readme_uses_standalone_swarmbox_framing(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("SwarmBox runs coding agents", readme)
        self.assertIn("Supported Agents", readme)
        self.assertIn("Sandbox Providers", readme)
        self.assertIn("Development", readme)

    def test_public_api_exports_customization_helpers(self):
        import swarmbox

        for name in [
            "AgentCommandOptions",
            "AgentMetadata",
            "CwdError",
            "HostSessionStore",
            "IterationUsage",
            "MountConfig",
            "ParsedStreamEvent",
            "PromptArgError",
            "PromptArgs",
            "ResolvedPrompt",
            "SandboxHandle",
            "SandboxProvider",
            "SandboxSessionStore",
            "SessionStore",
            "create_bind_mount_sandbox_provider",
            "create_isolated_sandbox_provider",
            "find_missing_prompt_arg_keys",
            "resolve_prompt",
            "substitute_prompt_args",
        ]:
            self.assertTrue(hasattr(swarmbox, name), name)


if __name__ == "__main__":
    unittest.main()
