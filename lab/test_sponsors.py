import json
import os
import tempfile
import unittest
from unittest import mock

from port import factory as port_factory
from port.client import PortClient

from . import run


class SponsorLoopTests(unittest.TestCase):
    def test_fastener_gate_records_physics_margin_not_purchase_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_runs = run.RUNS
            run.RUNS = os.path.join(tmp, "runs")
            try:
                result = run.scrape_lane("judge-fastener", "vendor_v1.html", lambda _: None)
            finally:
                run.RUNS = old_runs

        fastener = next(gate for gate in result.gates if gate["name"] == "physics.fastener")
        freshness = next(gate for gate in result.gates if gate["name"] == "scrape.freshness")
        self.assertGreaterEqual(fastener["margin"], 1.5)
        self.assertEqual(0.09, result.artifacts[0]["unit_price"])
        self.assertTrue(freshness["passed"])
        self.assertLessEqual(freshness["margin"], 900)

    def test_port_plan_is_committed_before_an_empty_run_can_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_runs = run.RUNS
            run.RUNS = os.path.join(tmp, "runs")
            try:
                client = PortClient("", "", run_id="judge-01",
                                    spool=os.path.join(tmp, "port.jsonl"), force_dry=True)
                result = run.execute(
                    "Verify the sponsor handoff",
                    run_id="judge-01",
                    lanes=(),
                    quiet=True,
                    port_client=client,
                )
            finally:
                run.RUNS = old_runs

            self.assertEqual("dry", result["governance"]["mode"])
            self.assertTrue(result["governance"]["plan_sha"])
            self.assertEqual("awaiting_approval", result["governance"]["status"])
            status = port_factory.status(client, "judge-01")
            self.assertEqual(result["governance"]["plan_sha"], status["properties"]["plan_sha"])

            with open(client.spool, encoding="utf-8") as fh:
                records = [json.loads(line) for line in fh if line.strip()]
            run_writes = [record for record in records
                          if record["method"] in ("POST", "PATCH")
                          and "/blueprints/daisy_run/entities" in record["path"]]
            self.assertTrue(run_writes)

    def test_port_plan_is_committed_before_daisy_chain_invokes_its_ceo(self):
        order = []
        topology = {
            "ready": True,
            "agents": ["claude", "codex"],
            "nodes": [
                {"agent": "claude", "role": "ceo", "reports_to": ""},
                {"agent": "codex", "role": "reviewer", "reports_to": "claude"},
            ],
            "why": "",
        }
        chain_result = {
            "passed": False,
            "ceo": "claude:opus", "reviewer": "codex:gpt-5.6-terra",
            "gates": [{"name": "chain.review", "passed": False,
                       "margin": 0, "detail": "review blocked"}],
            "assignments": [{"agent": "codex:gpt-5.6-terra", "task": "review"}],
            "workers": {"codex:gpt-5.6-terra": {"ok": False, "ms": 12,
                                                   "reason": "blocked"}},
            "synthesis": "", "review": {"passed": False, "findings": ["blocked"]},
        }
        original_commit = run.port_factory.commit_plan

        def commit(*args, **kwargs):
            order.append("port")
            return original_commit(*args, **kwargs)

        def chain(*args, **kwargs):
            order.append("chain")
            return chain_result

        with tempfile.TemporaryDirectory() as tmp:
            old_runs = run.RUNS
            run.RUNS = os.path.join(tmp, "runs")
            client = PortClient("", "", run_id="chain-order",
                                spool=os.path.join(tmp, "port.jsonl"), force_dry=True)
            try:
                with mock.patch.object(run, "chain_topology", return_value=topology), \
                     mock.patch.object(run, "chain_orchestrate", side_effect=chain), \
                     mock.patch.object(run.port_factory, "commit_plan", side_effect=commit):
                    result = run.execute(
                        "Coordinate every local agent",
                        run_id="chain-order",
                        lanes=("crew",),
                        quiet=True,
                        port_client=client,
                        daisy_chain=True,
                    )
            finally:
                run.RUNS = old_runs

        self.assertEqual(["port", "chain"], order[:2])
        self.assertEqual("awaiting_approval", result["governance"]["status"])
        org = result["lanes"]["crew"]["organization"]
        self.assertEqual("claude:opus", org["ceo"])
        self.assertEqual("review", org["assignments"][0]["task"])
        self.assertEqual(False, org["workers"]["codex:gpt-5.6-terra"]["ok"])


if __name__ == "__main__":
    unittest.main()
