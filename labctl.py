#!/usr/bin/env python3
"""labctl — the Daisy orchestrator.

    python3 labctl.py agents
    python3 labctl.py run --brief "a mounting bracket for the SR-11 sensor"
    python3 labctl.py run --brief "..." --lane hardware --lane scrape
    python3 labctl.py run --brief "..." --fixture vendor_v2.html
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lab import executors                      # noqa: E402
from agents.models import inventory            # noqa: E402
from lab.chain import topology                 # noqa: E402
from lab.run import execute                    # noqa: E402


def project_cwd(value):
    """Resolve one explicit project folder without changing process cwd."""
    if not value:
        return os.getcwd()
    resolved = os.path.realpath(os.path.expanduser(value))
    if not os.path.isdir(resolved):
        raise ValueError("project is not an existing directory")
    return resolved


def main(argv=None):
    ap = argparse.ArgumentParser(prog="labctl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    agents_parser = sub.add_parser("agents", help="which coding agents can actually be driven")
    agents_parser.add_argument("--json", action="store_true")
    chain_parser = sub.add_parser("chain", help="build a governed org from the agents this Mac can drive")
    chain_parser.add_argument("--json", action="store_true")
    chain_parser.add_argument("--goal", help="run the goal through the governed chain")
    chain_parser.add_argument("--project")
    agent_parser = sub.add_parser("agent", help="run one exact locally selectable model")
    agent_parser.add_argument("--name", required=True,
                              choices=("claude", "codex", "opencode"))
    agent_parser.add_argument("--model", required=True)
    agent_parser.add_argument("--effort", default="")
    agent_parser.add_argument("--speed", default="standard")
    agent_parser.add_argument("--provider", default="")
    agent_parser.add_argument("--project")
    agent_parser.add_argument("--prompt", required=True)
    agent_parser.add_argument("--json", action="store_true")
    r = sub.add_parser("run", help="take a brief through the factory")
    r.add_argument("--brief", required=True)
    r.add_argument("--run-id")
    r.add_argument("--lane", action="append",
               choices=("hardware", "scrape", "software", "crew"))
    r.add_argument("--crew", action="append",
               choices=("claude", "codex", "opencode"),
               help="agents for the crew lane (default: claude, codex)")
    r.add_argument("--agent", default="auto", choices=("auto", "claude", "codex", "opencode"))
    r.add_argument("--fixture", default=None,
                   help="force an offline Bright Data fixture; omit for live Studio when configured")
    r.add_argument("--garden", action="store_true",
                   help="publish what passes to the shared Garden index "
                        "(prepares a PR branch; never pushes without --live)")
    r.add_argument("--json", action="store_true")
    r.add_argument("--daisy-chain", action="store_true",
                   help="probe all local agents and run them under one Port plan and gate contract")
    r.add_argument("--project", help="folder the selected agents may inspect and edit")
    a = ap.parse_args(argv)

    if a.cmd == "agents":
        measured = executors.summarize_models(
            executors.available_models(inventory(), cwd=os.getcwd()))
        if a.json:
            print(json.dumps({"agents": [
                {"name": e.name, "ok": e.ok, "detail": e.detail, "probe_ms": e.probe_ms}
                for e in measured
            ]}, indent=1))
            return 0
        print("%-10s %-7s %-10s %s" % ("agent", "usable", "probe", "detail"))
        print("-" * 78)
        for e in measured:
            print("%-10s %-7s %-10s %s" % (
                e.name, "yes" if e.ok else "no",
                ("%.0f ms" % e.probe_ms) if e.probe_ms else "-", e.detail))
        return 0

    if a.cmd == "chain":
        cwd = project_cwd(a.project)
        if a.goal:
            summary = execute(a.goal, lanes=("crew",), quiet=a.json,
                              daisy_chain=True, project_dir=cwd)
            if a.json:
                print(json.dumps(summary, indent=1, default=str))
            else:
                print("Daisy Chain run %s — %d gates, %d failed" % (
                    summary["run"], summary["gates"]["total"], summary["gates"]["failed"]))
            return summary["gates"]["failed"]
        chain = topology(cwd=cwd)
        if a.json:
            print(json.dumps(chain, indent=1))
        else:
            print("Daisy Chain — %s" % ("ready" if chain["ready"] else "not ready"))
            for node in chain["nodes"]:
                print("  %-11s %-10s %s" % (node["role"], node["agent"],
                                             node["model"] or "tool default"))
            if chain["why"]:
                print("  %s" % chain["why"])
        return 0 if chain["ready"] else 2

    if a.cmd == "agent":
        cwd = project_cwd(a.project)
        available_models = [row for row in inventory().get("models", [])
                            if row.get("vendor") == a.name
                            and row.get("id") == a.model
                            and (row.get("provider") or "") == a.provider]
        if not available_models:
            result = {"agent": a.name, "model": a.model, "ok": False,
                      "reason": "model is not in this Mac's selectable inventory",
                      "stdout": "", "stderr": "", "ms": 0}
        else:
            selected = available_models[0]
            valid_efforts = selected.get("efforts") or []
            valid_speeds = selected.get("speeds") or []
            if a.effort and a.effort not in valid_efforts:
                result = {"agent": a.name, "model": a.model, "ok": False,
                          "reason": "effort is not supported by this model",
                          "stdout": "", "stderr": "", "ms": 0}
            elif a.speed and a.speed not in valid_speeds:
                result = {"agent": a.name, "model": a.model, "ok": False,
                          "reason": "speed is not supported by this model",
                          "stdout": "", "stderr": "", "ms": 0}
            else:
                ex, unavailable = executors.select(a.name)
                if not ex:
                    result = {"agent": a.name, "model": a.model, "ok": False,
                              "reason": unavailable or "agent unavailable",
                              "stdout": "", "stderr": "", "ms": 0}
                else:
                    result = executors.run(ex, a.prompt, cwd=cwd, model=a.model,
                                           effort=a.effort, provider=a.provider,
                                           speed=a.speed)
                    result.update({"model": a.model, "effort": a.effort,
                                   "speed": a.speed, "provider": a.provider,
                                   "project": cwd})
        if a.json:
            print(json.dumps(result, indent=1))
        else:
            print(result.get("stdout") or result.get("reason") or "no output")
        return 0 if result.get("ok") else 2

    lanes = tuple(a.lane or ("hardware", "scrape", "software"))
    if a.daisy_chain and "crew" not in lanes:
        lanes += ("crew",)
    s = execute(a.brief, a.run_id, lanes,
                a.agent, a.fixture, quiet=a.json, crew=a.crew, to_garden=a.garden,
                daisy_chain=a.daisy_chain, project_dir=project_cwd(a.project))
    if a.json:
        print(json.dumps(s, indent=1, default=str))
    else:
        print()
        print("run %s — %d gates, %d failed, %.1fs"
              % (s["run"], s["gates"]["total"], s["gates"]["failed"], s["duration_s"]))
        if s["blocked_lanes"]:
            print("  blocked lanes: %s" % ", ".join(s["blocked_lanes"]))
        for g in s.get("garden", []):
            print("  garden: %s %s %s" % (g["lane"], g["mode"], g.get("branch") or g.get("why","")[:50]))
        if s["admitted_to_commons"]:
            print("  admitted to the commons: %s"
                  % ", ".join(x["lane"] for x in s["admitted_to_commons"]))
        print("  artifacts: %s" % os.path.relpath(s["artifacts_dir"], os.getcwd()))
    return s["gates"]["failed"]


if __name__ == "__main__":
    raise SystemExit(main())
