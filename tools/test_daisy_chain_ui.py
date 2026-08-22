import importlib.util
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_generator():
    path = os.path.join(ROOT, "tools", "add_daisy_chain.py")
    spec = importlib.util.spec_from_file_location("add_daisy_chain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DaisyChainUIGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.generator = load_generator()
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
            self.source = fh.read()

    def test_upgrade_is_idempotent_and_every_new_run_is_blank(self):
        once = self.generator.upgrade(self.source)
        twice = self.generator.upgrade(once)

        self.assertEqual(once, twice)
        self.assertIn("function newBlankRun", once)
        self.assertIn("applyTheme();\n  clearDemoWorkspace();\n  newBlankRun(false);", once)
        self.assertIn("$('#tb-new').addEventListener('click', function () { show('run'); newBlankRun(); });", once)
        self.assertIn("$('#rt-new').addEventListener('click', function () { show('run'); newBlankRun(); });", once)
        self.assertIn("if (action === 'new') { show('run'); newBlankRun(); }", once)
        self.assertIn("show('run'); newBlankRun(); return;", once)

    def test_upgrade_wires_real_chain_run_and_reset_without_changing_app_typography(self):
        html = self.generator.upgrade(self.source)

        self.assertIn("cmd: 'chain.run', goal: v", html)
        self.assertIn("window.__daisyChainRun", html)
        self.assertIn('id="reset-daisy"', html)
        self.assertIn("window.daisyResetToOnboarding", html)
        self.assertIn("CEO → every available peer → review → gates", html)
        self.assertIn(".chain-copy { font-family: var(--sans);", html)
        self.assertIn(".chain-org {", html)
        self.assertIn("font-family: var(--mono);", html)

    def test_upgrade_removes_demo_chat_history_and_runs_the_selected_real_model(self):
        html = self.generator.upgrade(self.source)

        self.assertIn("function clearDemoWorkspace", html)
        self.assertIn("No runs yet", html)
        self.assertIn('id="run-empty"', html)
        self.assertIn('id="run-empty-mark"', html)
        self.assertIn("What should we build in Daisy?", html)
        self.assertIn("function showEmptyRun", html)
        self.assertIn("function hideEmptyRun", html)
        self.assertIn("hideEmptyRun();\n    var item = document.createElement('div');", html)
        self.assertIn("cmd: 'agent.run'", html)
        self.assertIn("model: selected.model.id", html)
        self.assertIn("provider: selected.model.provider || ''", html)
        self.assertIn("window.__daisyAgentRun", html)
        self.assertIn("LANES.run = defaultLane('codex');", html)
        self.assertNotIn("Run brief A — $30 fastener budget", html)
        self.assertNotIn("Run brief B — $18 ceiling", html)

    def test_upgrade_adds_a_live_sidebar_chain_map_not_a_demo_roster(self):
        html = self.generator.upgrade(self.source)

        self.assertIn('data-view="chain"', html)
        self.assertIn('id="view-chain"', html)
        self.assertIn('id="chain-map"', html)
        self.assertIn("function renderChainMap", html)
        self.assertIn("CHAIN_STATE.nodes", html)
        self.assertIn("cmd: 'chain.status'", html)
        self.assertIn("crew.organization", html)
        self.assertIn("CEO", html)
        self.assertIn("Port plan", html)


if __name__ == "__main__":
    unittest.main()
