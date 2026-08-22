#!/usr/bin/env python3
"""Source-level contract for Daisy's tiny native/web trust boundary."""

from pathlib import Path

SOURCE = Path(__file__).with_name("main.swift").read_text()
failed = []


def check(name, condition):
    if condition:
        print("  ok  ", name)
    else:
        print("  FAIL", name)
        failed.append(name)


print("Daisy native bridge — contract")
check("accepts the original agent command", 'case "agents"' in SOURCE)
check("accepts the onboarding agent command", 'case "onboarding.agents"' in SOURCE)
check("has an explicit Garden status command", '"garden.status"' in SOURCE)
check("has an explicit Garden pair command", 'case "garden.pair"' in SOURCE)
check("opens Garden outside the web view", 'case "garden.open"' in SOURCE and "NSWorkspace.shared.open" in SOURCE)
check("pins browser auth to the HTTPS Garden origin", 'https://garden-taupe-three.vercel.app' in SOURCE and 'url.scheme == "https"' in SOURCE)
check("invokes the onboarding agent callback", "window.__daisyOnboarding" in SOURCE)
check("onboarding probes real executors as JSON", '["python3", "labctl.py", "agents", "--json"]' in SOURCE)
check("reports a governed Daisy Chain topology", 'case "chain.status"' in SOURCE and '["python3", "labctl.py", "chain", "--json"]' in SOURCE)
check("runs a real governed Daisy Chain goal", 'case "chain.run"' in SOURCE and '["python3", "labctl.py", "run", "--brief", goal, "--lane", "crew", "--daisy-chain", "--json"]' in SOURCE)
check("bounds nonempty Daisy Chain goals", 'goal.count <= 12000' in SOURCE and '!goal.isEmpty' in SOURCE)
check("invokes the Daisy Chain run callback", "window.__daisyChainRun" in SOURCE)
check("runs one real selected local model", all(part in SOURCE for part in (
    'case "agent.run"', '["python3", "labctl.py", "agent"', '"--name", vendor',
    '"--model", model', '"--effort", effort', '"--provider", provider',
    '"--prompt", goal', '"--json"]')))
check("allowlists agent vendors and bounded model ids", 'Set(["claude", "codex", "opencode"])' in SOURCE and 'model.count <= 160' in SOURCE)
check("invokes the single-agent callback", "window.__daisyAgentRun" in SOURCE)
check("supports a first-run reset", 'case "app.reset"' in SOURCE and '["python3", "-m", "garden.link", "unlink"]' in SOURCE)
check("invokes the first-run reset callback", "window.__daisyReset" in SOURCE)
check("invokes the Garden status callback", "window.__daisyGardenStatus" in SOURCE)
check("invokes the Garden pairing callback", "window.__daisyGardenPair" in SOURCE)
check("passes the pairing code as one process argument", '["python3", "-m", "garden.link", "pair", "--code", code]' in SOURCE)
check("uses Process argument arrays", "proc.arguments = arguments" in SOURCE)
check("never invokes a shell", "/bin/sh" not in SOURCE and "-c\"" not in SOURCE)

print(f"\n{22-len(failed)} passed, {len(failed)} failed")
raise SystemExit(1 if failed else 0)
