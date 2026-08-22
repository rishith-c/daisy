import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
