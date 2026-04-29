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


if __name__ == "__main__":
    unittest.main()
