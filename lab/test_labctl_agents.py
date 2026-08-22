import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import labctl
from lab.executors import Executor


class AgentsCommandTests(unittest.TestCase):
    def test_json_reports_probe_results_for_onboarding(self):
        measured = [
            Executor("codex", "codex", [], ok=True,
                     detail="responded to the probe", probe_ms=42.5),
            Executor("claude", "claude", [], ok=False,
                     detail="rate limited", probe_ms=18.0),
        ]
        output = io.StringIO()

        with patch("labctl.executors.available", return_value=measured), redirect_stdout(output):
            self.assertEqual(labctl.main(["agents", "--json"]), 0)

        self.assertEqual(json.loads(output.getvalue()), {
            "agents": [
                {"name": "codex", "ok": True, "detail": "responded to the probe", "probe_ms": 42.5},
                {"name": "claude", "ok": False, "detail": "rate limited", "probe_ms": 18.0},
            ]
        })

    def test_daisy_chain_adds_the_governed_crew_lane(self):
        output = io.StringIO()
        summary = {"gates": {"failed": 0}}
        with patch("labctl.execute", return_value=summary) as execute, redirect_stdout(output):
            self.assertEqual(labctl.main(["run", "--brief", "ship it", "--daisy-chain", "--json"]), 0)

        args, kwargs = execute.call_args
        self.assertIn("crew", args[2])
        self.assertTrue(kwargs["daisy_chain"])


if __name__ == "__main__":
    unittest.main()
