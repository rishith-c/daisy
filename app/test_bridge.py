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
check("invokes the Garden status callback", "window.__daisyGardenStatus" in SOURCE)
check("invokes the Garden pairing callback", "window.__daisyGardenPair" in SOURCE)
check("passes the pairing code as one process argument", '["python3", "-m", "garden.link", "pair", "--code", code]' in SOURCE)
check("uses Process argument arrays", "proc.arguments = arguments" in SOURCE)
check("never invokes a shell", "/bin/sh" not in SOURCE and "-c\"" not in SOURCE)

print(f"\n{12-len(failed)} passed, {len(failed)} failed")
raise SystemExit(1 if failed else 0)
