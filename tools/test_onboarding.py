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
        second_code, second_output = self.run_generator("--replace")

        self.assertEqual(0, code)
        self.assertEqual(0, second_code)
        self.assertIn("onboarding replaced", output)
        self.assertIn("onboarding replaced", second_output)
        with open(self.index, encoding="utf-8") as fh:
            html = fh.read()
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


if __name__ == "__main__":
    unittest.main()
