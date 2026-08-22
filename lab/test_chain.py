import unittest

from lab.chain import topology
from lab.executors import Executor


class DaisyChainTopologyTests(unittest.TestCase):
    def test_uses_only_agents_that_really_answered_and_their_available_models(self):
        probes = [
            Executor("claude", "claude", [], ok=False, detail="rate limited"),
            Executor("codex", "codex", [], ok=True, detail="responded"),
            Executor("opencode", "opencode", [], ok=True, detail="responded"),
        ]
        inventory = {"models": [
            {"vendor": "claude", "id": "opus", "current": True},
            {"vendor": "codex", "id": "gpt-5.6-sol", "current": True},
            {"vendor": "opencode", "id": "ox-alpha", "current": False},
        ]}

        chain = topology(probes=probes, model_inventory=inventory)

        self.assertTrue(chain["ready"])
        self.assertEqual(["codex", "opencode"], chain["agents"])
        self.assertEqual(["gpt-5.6-sol", "ox-alpha"], [n["model"] for n in chain["nodes"]])
        self.assertEqual("coordinator", chain["nodes"][0]["role"])
        self.assertEqual("reviewer", chain["nodes"][1]["role"])
        self.assertEqual("codex", chain["nodes"][1]["reports_to"])
        self.assertNotIn("claude", str(chain["nodes"]))

    def test_refuses_to_call_one_agent_a_chain(self):
        probes = [Executor("codex", "codex", [], ok=True, detail="responded")]
        chain = topology(probes=probes, model_inventory={"models": []})

        self.assertFalse(chain["ready"])
        self.assertIn("at least two", chain["why"])


if __name__ == "__main__":
    unittest.main()
