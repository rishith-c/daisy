import tempfile
import unittest
from pathlib import Path

from lab.executors import Executor, _candidates, _claude_candidate, summarize_models, select


class SelectedModelCommandTests(unittest.TestCase):
    def test_selected_agent_can_start_without_a_second_model_round_trip(self):
        ex, reason = select("codex", candidates=[
            Executor("codex", "/usr/bin/true", ["/usr/bin/true", "{prompt}"])
        ])

        self.assertEqual("codex", ex.name)
        self.assertEqual("", reason)

    def test_codex_automatic_review_does_not_combine_conflicting_cli_flags(self):
        codex = next(ex for ex in _candidates() if ex.name == "codex")

        self.assertIn("--approve-for-me", codex.argv)
        self.assertNotIn("--sandbox", codex.argv)
    def test_agent_summary_is_usable_when_any_exact_model_answers(self):
        measured = [
            Executor("claude", "claude", [], ok=False, detail="404", model="opus"),
            Executor("claude", "claude", [], ok=True, detail="responded",
                     model="claude-fable-5", probe_ms=12),
            Executor("codex", "codex", [], ok=True, detail="responded",
                     model="gpt-5.6-sol", probe_ms=8),
        ]

        summary = summarize_models(measured)

        self.assertEqual(["claude", "codex"], [row.name for row in summary])
        self.assertTrue(summary[0].ok)
        self.assertEqual("1/2 selectable models responded", summary[0].detail)
        self.assertEqual(12, summary[0].probe_ms)

    def test_claude_uses_a_supported_nvm_node_instead_of_an_incompatible_global_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            older = Path(tmp, ".nvm/versions/node/v22.16.0/bin/node")
            incompatible = Path(tmp, ".nvm/versions/node/v26.7.0/bin/node")
            older.parent.mkdir(parents=True); older.touch()
            incompatible.parent.mkdir(parents=True); incompatible.touch()

            ex = _claude_candidate(
                home=tmp, which=lambda name: "/opt/homebrew/bin/claude" if name == "claude" else None)

        self.assertEqual(str(older), ex.binary)
        self.assertEqual([str(older), "/opt/homebrew/bin/claude", "-p"], ex.argv[:3])

    def test_claude_resolves_the_nvm_cli_when_a_gui_app_has_no_shell_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp, ".nvm/versions/node/v22.16.0/bin/node")
            claude = node.with_name("claude")
            node.parent.mkdir(parents=True)
            node.touch()
            claude.touch()

            ex = _claude_candidate(home=tmp, which=lambda _name: None)

        self.assertEqual(str(node), ex.binary)
        self.assertEqual([str(node), str(claude), "-p"], ex.argv[:3])

    def test_claude_does_not_feed_a_standalone_binary_to_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = Path(tmp, ".nvm/versions/node/v22.16.0/bin/node")
            nvm_cli = node.with_name("claude")
            standalone = Path(tmp, ".local/bin/claude")
            node.parent.mkdir(parents=True)
            standalone.parent.mkdir(parents=True)
            node.touch()
            nvm_cli.touch()
            standalone.touch()

            ex = _claude_candidate(
                home=tmp,
                which=lambda name: str(standalone) if name == "claude" else None,
            )

        self.assertEqual([str(node), str(nvm_cli), "-p"], ex.argv[:3])

    def test_claude_receives_the_exact_model_without_an_unsupported_effort_flag(self):
        ex = Executor("claude", "claude",
                      ["claude", "-p", "{prompt}", "--output-format", "text"])
        command = ex.command("hello", model="opus", effort="automatic")

        self.assertEqual(["claude", "-p", "--model", "opus",
                          "hello", "--output-format", "text"], command)

    def test_codex_receives_the_exact_selected_model_and_reasoning_effort(self):
        ex = Executor("codex", "codex",
                      ["codex", "exec", "--skip-git-repo-check", "{prompt}"])
        command = ex.command("hello", model="gpt-5.6-terra", effort="xhigh")

        self.assertEqual(["codex", "exec", "--skip-git-repo-check", "--model",
                          "gpt-5.6-terra", "-c", 'model_reasoning_effort="xhigh"',
                          "hello"], command)

    def test_codex_fast_mode_maps_to_its_real_service_tier(self):
        ex = Executor("codex", "codex",
                      ["codex", "exec", "--skip-git-repo-check", "{prompt}"])
        command = ex.command("hello", model="gpt-5.6-sol", effort="high",
                             speed="fast")

        self.assertIn('service_tier="fast"', command)

    def test_opencode_uses_provider_model_and_variant(self):
        ex = Executor("opencode", "opencode", ["opencode", "run", "{prompt}"])
        command = ex.command("hello", model="stealth/ox-alpha",
                             provider="openrouter", effort="high")

        self.assertEqual(["opencode", "run", "--model",
                          "openrouter/stealth/ox-alpha", "--variant", "high", "hello"],
                         command)

    def test_opencode_automatic_effort_does_not_invent_a_variant(self):
        ex = Executor("opencode", "opencode", ["opencode", "run", "{prompt}"])
        command = ex.command("hello", model="plain", provider="local",
                             effort="automatic")

        self.assertEqual(["opencode", "run", "--model", "local/plain", "hello"],
                         command)


if __name__ == "__main__":
    unittest.main()
