from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DaisyChainUITests(unittest.TestCase):
    def test_model_picker_exposes_truthful_opt_in_chain(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("Daisy Chain", html)
        self.assertIn('id="daisy-chain"', html)
        self.assertIn('role="switch"', html)
        self.assertIn("chain.status", html)
        self.assertIn("window.__daisyChainStatus", html)
        self.assertIn("daisy-chain-v1", html)


if __name__ == "__main__":
    unittest.main()
