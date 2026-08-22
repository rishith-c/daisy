import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import labctl
from lab.executors import Executor


class AgentsCommandTests(unittest.TestCase):
    def test_single_agent_uses_the_explicit_project_as_probe_and_run_cwd(self):
        output = io.StringIO()
        model = {"vendor": "codex", "id": "gpt-test", "provider": "",
                 "efforts": ["high"], "speeds": ["standard"]}
        executor = Executor("codex", "codex", [], ok=True)
        with tempfile.TemporaryDirectory() as project, \
             patch("labctl.inventory", return_value={"models": [model]}), \
             patch("labctl.executors.select", return_value=(executor, "")) as select, \
             patch("labctl.executors.run", return_value={"ok": True, "stdout": "done"}) as run, \
             redirect_stdout(output):
            code = labctl.main(["agent", "--name", "codex", "--model", "gpt-test",
                                "--effort", "high", "--speed", "standard",
                                "--provider", "", "--prompt", "ship it",
                                "--project", project, "--json"])

        self.assertEqual(0, code)
        self.assertEqual("codex", select.call_args.args[0])
        self.assertEqual(os.path.realpath(project), run.call_args.kwargs["cwd"])

    def test_run_passes_the_explicit_project_to_the_chain(self):
        output = io.StringIO()
        summary = {"gates": {"failed": 0}}
        with tempfile.TemporaryDirectory() as project, \
             patch("labctl.execute", return_value=summary) as execute, \
             redirect_stdout(output):
            code = labctl.main(["run", "--brief", "ship it", "--daisy-chain",
                                "--project", project, "--json"])

        self.assertEqual(0, code)
        self.assertEqual(os.path.realpath(project), execute.call_args.kwargs["project_dir"])

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
