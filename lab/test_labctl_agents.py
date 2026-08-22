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
                     detail="responded to the probe", probe_ms=42.5,
                     model="gpt-5.6-sol"),
            Executor("codex", "codex", [], ok=False,
                     detail="not entitled", probe_ms=20.0,
                     model="gpt-5.6-terra"),
            Executor("claude", "claude", [], ok=False,
                     detail="rate limited", probe_ms=18.0, model="opus"),
        ]
        output = io.StringIO()

        with patch("labctl.executors.available_models", return_value=measured), redirect_stdout(output):
            self.assertEqual(labctl.main(["agents", "--json"]), 0)

        self.assertEqual(json.loads(output.getvalue()), {
            "agents": [
                {"name": "codex", "ok": True,
                 "detail": "1/2 selectable models responded", "probe_ms": 42.5},
                {"name": "claude", "ok": False,
                 "detail": "0/1 selectable models responded — rate limited", "probe_ms": 18.0},
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
