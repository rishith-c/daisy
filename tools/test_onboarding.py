import contextlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_generator():
    path = os.path.join(ROOT, "tools", "add_onboarding.py")
    spec = importlib.util.spec_from_file_location("add_onboarding", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OnboardingGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.index = os.path.join(self.tmp.name, "index.html")
        self.source = os.path.join(self.tmp.name, "onboarding.html")
        shutil.copyfile(os.path.join(ROOT, "index.html"), self.index)
        shutil.copyfile(os.path.join(ROOT, "onboarding.html"), self.source)
        self.generator = load_generator()
        self.generator.IDX = self.index
        self.generator.SRC = self.source

    def run_generator(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.generator.main(list(args))
        return code, output.getvalue()

    def test_replace_refreshes_existing_onboarding_without_duplication(self):
        code, output = self.run_generator("--replace")
        with open(self.index, encoding="utf-8") as fh:
            first = fh.read()
        second_code, second_output = self.run_generator("--replace")

        self.assertEqual(0, code)
        self.assertEqual(0, second_code)
        self.assertIn("onboarding replaced", output)
        self.assertIn("onboarding replaced", second_output)
        with open(self.index, encoding="utf-8") as fh:
            html = fh.read()
        self.assertEqual(first, html)
        self.assertEqual(1, html.count('id="obv"'))
        self.assertEqual(1, html.count("{ l: 'Show onboarding'"))
        self.assertEqual(4, html.count('<section class="ob-p'))
        self.assertNotIn("\x08", html)
        self.assertIn(r"[?&#]onboarding\b", html)

    def test_generated_flow_exposes_local_and_browser_pairing_paths(self):
        self.run_generator("--replace")

        with open(self.index, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("Start locally", html)
        self.assertIn("Connect Garden", html)
        self.assertIn("garden.open", html)
        self.assertIn("garden.pair", html)
        self.assertIn("window.__daisyGardenPair", html)
        self.assertIn("window.__daisyGardenStatus", html)
        self.assertIn('maxlength="6"', html)
        self.assertIn("Not linked. Open Garden", html)

    def test_generated_flow_can_erase_daisy_state_and_return_to_onboarding(self):
        self.run_generator("--replace")

        with open(self.index, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("window.daisyResetToOnboarding", html)
        self.assertIn("window.__daisyReset", html)
        self.assertIn("localStorage.removeItem('daisy-onboarded')", html)
        self.assertIn("localStorage.removeItem('daisy-chain-v1')", html)
        self.assertIn("localStorage.removeItem('daisy-theme')", html)
        self.assertIn("postNative({ cmd: 'app.reset' })", html)
        self.assertIn("location.reload()", html)

    def test_first_screen_is_a_full_white_daisy_welcome(self):
        self.run_generator("--replace")

        with open(self.index, encoding="utf-8") as fh:
            html = fh.read()
        welcome = html.split('id="ob-p0"', 1)[1].split('</section>', 1)[0]
        self.assertIn('class="ob-p ob-welcome on"', html)
        self.assertIn('<h1 id="ob-h0">Daisy</h1>', welcome)
        self.assertIn('id="ob-start">Get started</button>', welcome)
        self.assertNotIn('ob-lede', welcome)
        self.assertNotIn('ob-gates', welcome)
        self.assertIn('background: #fff', html)
        self.assertIn('width: 100%; height: 100%', html)
        self.assertIn("startBtn.addEventListener('click'", html)

    def test_following_steps_are_centered_questions_on_the_white_canvas(self):
        self.run_generator("--replace")

        with open(self.index, encoding="utf-8") as fh:
            html = fh.read()
        self.assertEqual(3, html.count('class="ob-p ob-question"'))
        self.assertIn('Which agents are available on this Mac?', html)
        self.assertIn('Would you like to connect Garden?', html)
        self.assertIn('How should Daisy look?', html)
        self.assertIn('.ob-question {', html)
        self.assertIn('align-items: center; justify-content: center', html)
        self.assertNotIn('<div class="ob-top">', html)


if __name__ == "__main__":
    unittest.main()
