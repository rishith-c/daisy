import json
import threading
import unittest

from lab.chain import orchestrate, topology
from lab.executors import Executor


class DaisyChainTopologyTests(unittest.TestCase):
    def test_model_specific_probes_exclude_only_the_broken_model_not_its_vendor(self):
        good_claude = Executor("claude", "claude", [], ok=True, detail="responded",
                               model="claude-fable-5")
        bad_claude = Executor("claude", "claude", [], ok=False, detail="404",
                              model="opus")
        good_codex = Executor("codex", "codex", [], ok=True, detail="responded",
                              model="gpt-5.6-terra")
        inventory = {"models": [
            {"vendor": "claude", "id": "claude-fable-5", "current": False},
            {"vendor": "claude", "id": "opus", "current": True},
            {"vendor": "codex", "id": "gpt-5.6-terra", "current": True},
        ]}

        chain = topology(probes=[good_claude, bad_claude, good_codex],
                         model_inventory=inventory)

        self.assertEqual(["claude:claude-fable-5", "codex:gpt-5.6-terra"],
                         [node["id"] for node in chain["nodes"]])
        self.assertNotIn("claude:opus", str(chain["nodes"]))

    def test_one_ceo_coordinates_every_model_on_each_usable_agent(self):
        probes = [
            Executor("claude", "claude", [], ok=True, detail="responded"),
            Executor("codex", "codex", [], ok=True, detail="responded"),
            Executor("opencode", "opencode", [], ok=False, detail="not signed in"),
        ]
        inventory = {"models": [
            {"vendor": "claude", "id": "opus", "current": True,
             "effort": "high", "provider": ""},
            {"vendor": "claude", "id": "sonnet", "current": False,
             "efforts": ["low", "high"], "provider": ""},
            {"vendor": "codex", "id": "gpt-5.6-sol", "current": True,
             "effort": "xhigh", "provider": ""},
            {"vendor": "codex", "id": "gpt-5.6-terra", "current": False,
             "efforts": ["low", "medium", "high"], "provider": ""},
            {"vendor": "codex", "id": "gpt-5.6-luna", "current": False,
             "efforts": ["low", "medium"], "provider": ""},
            {"vendor": "opencode", "id": "ox-alpha", "current": True,
             "provider": "openrouter"},
        ]}

        chain = topology(probes=probes, model_inventory=inventory)

        self.assertTrue(chain["ready"])
        self.assertEqual(5, len(chain["nodes"]))
        self.assertEqual("claude:opus", chain["nodes"][0]["id"])
        self.assertEqual("ceo", chain["nodes"][0]["role"])
        self.assertEqual(1, sum(n["role"] == "ceo" for n in chain["nodes"]))
        self.assertEqual(4, sum(n["reports_to"] == "claude:opus"
                                for n in chain["nodes"]))
        self.assertEqual(5, len({n["id"] for n in chain["nodes"]}))
        self.assertNotIn("opencode", chain["agents"])
        self.assertNotIn("ox-alpha", str(chain["nodes"]))

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
        self.assertEqual("ceo", chain["nodes"][0]["role"])
        self.assertEqual("reviewer", chain["nodes"][1]["role"])
        self.assertEqual("codex:gpt-5.6-sol", chain["nodes"][1]["reports_to"])
        self.assertNotIn("claude", str(chain["nodes"]))
        self.assertEqual("ceo", chain["control"]["assignment"])

    def test_refuses_to_call_one_agent_a_chain(self):
        probes = [Executor("codex", "codex", [], ok=True, detail="responded")]
        chain = topology(probes=probes, model_inventory={"models": []})

        self.assertFalse(chain["ready"])
        self.assertIn("at least two", chain["why"])


class DaisyChainExecutionTests(unittest.TestCase):
    def setUp(self):
        self.chain = {
            "ready": True,
            "agents": ["claude", "codex", "opencode"],
            "nodes": [
                {"agent": "claude", "role": "ceo", "reports_to": ""},
                {"agent": "codex", "role": "specialist", "reports_to": "claude"},
                {"agent": "opencode", "role": "reviewer", "reports_to": "claude"},
            ],
            "why": "",
        }

    def test_ceo_assigns_every_peer_then_synthesizes_and_peer_reviews(self):
        calls = []
        lock = threading.Lock()

        def invoke(agent, prompt):
            with lock:
                calls.append((agent, prompt))
            if "CEO_PLAN" in prompt:
                text = json.dumps({"assignments": [
                    {"agent": "codex", "task": "Implement the requested change"},
                    {"agent": "opencode", "task": "Verify the requested change"},
                ]})
            elif "CEO_SYNTHESIS" in prompt:
                text = "All peer work was reconciled into one result."
            elif "PEER_REVIEW" in prompt:
                text = json.dumps({"passed": True, "findings": []})
            else:
                text = "completed by " + agent
            return {"agent": agent, "ok": True, "reason": "", "ms": 4,
                    "stdout": text, "stderr": ""}

        result = orchestrate("Ship a verified feature", self.chain, invoke=invoke)

        self.assertTrue(result["passed"])
        self.assertEqual("claude", result["ceo"])
        self.assertEqual("opencode", result["reviewer"])
        self.assertEqual({"codex", "opencode"}, set(result["workers"]))
        self.assertEqual(2, len(result["assignments"]))
        self.assertIn("reconciled", result["synthesis"])
        self.assertTrue(result["review"]["passed"])
        self.assertTrue(all(gate["passed"] for gate in result["gates"]))
        self.assertEqual(2, sum("WORKER_TASK" in prompt for _, prompt in calls))

    def test_worker_failure_is_a_named_failed_gate_and_cannot_be_verified(self):
        def invoke(agent, prompt):
            if "CEO_PLAN" in prompt:
                text = json.dumps({"assignments": [
                    {"agent": "codex", "task": "build"},
                    {"agent": "opencode", "task": "test"},
                ]})
                ok = True
            elif "WORKER_TASK" in prompt and agent == "codex":
                text, ok = "", False
            elif "PEER_REVIEW" in prompt:
                text, ok = json.dumps({"passed": True, "findings": []}), True
            else:
                text, ok = "output", True
            return {"agent": agent, "ok": ok, "reason": "exit 1" if not ok else "",
                    "ms": 3, "stdout": text, "stderr": ""}

        result = orchestrate("Ship safely", self.chain, invoke=invoke)

        self.assertFalse(result["passed"])
        failed = {gate["name"] for gate in result["gates"] if not gate["passed"]}
        self.assertIn("chain.worker.codex", failed)

    def test_malformed_ceo_plan_gets_one_repair_then_blocks(self):
        plan_calls = []

        def invoke(agent, prompt):
            if "CEO_PLAN" in prompt:
                plan_calls.append(prompt)
            return {"agent": agent, "ok": True, "reason": "", "ms": 2,
                    "stdout": "not json", "stderr": ""}

        result = orchestrate("Ship safely", self.chain, invoke=invoke)

        self.assertFalse(result["passed"])
        self.assertEqual(2, len(plan_calls))
        self.assertFalse(next(g for g in result["gates"]
                              if g["name"] == "chain.plan.coverage")["passed"])
        self.assertEqual({}, result["workers"])


if __name__ == "__main__":
    unittest.main()
