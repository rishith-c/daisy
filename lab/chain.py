"""Build Daisy's local multi-agent org from what this Mac can really run.

The topology is deliberately small. A coordinator assigns work, specialists
produce against one shared contract, and the final peer is the reviewer. Port
owns the plan and approvals; deterministic gates own the verdict. No model can
mark its own work verified, expand its permissions, or silently add a vendor.
"""

from __future__ import annotations

from agents.models import inventory
from lab import executors


def _model_for(vendor: str, model_inventory: dict) -> str:
    models = [m for m in model_inventory.get("models", [])
              if m.get("vendor") == vendor]
    for model in models:
        if model.get("current"):
            return str(model.get("id") or "")
    return str(models[0].get("id") or "") if models else ""


def topology(probes=None, model_inventory=None) -> dict:
    """Return an auditable role graph; never call an unprobed agent usable."""
    probes = list(probes if probes is not None else executors.available())
    model_inventory = model_inventory if model_inventory is not None else inventory()
    usable = [probe for probe in probes if probe.ok]
    agents = [probe.name for probe in usable]
    nodes = []
    for index, probe in enumerate(usable):
        role = "coordinator" if index == 0 else (
            "reviewer" if index == len(usable) - 1 else "specialist")
        nodes.append({
            "agent": probe.name,
            "model": _model_for(probe.name, model_inventory),
            "role": role,
            "reports_to": "" if index == 0 else usable[0].name,
            "probe_ms": probe.probe_ms,
        })
    ready = len(nodes) >= 2
    return {
        "ready": ready,
        "agents": agents,
        "nodes": nodes,
        "why": "" if ready else "Daisy Chain needs at least two agents that answer a live probe.",
        "control": {
            "assignment": "coordinator",
            "review": "peer",
            "verdict": "deterministic_gates",
            "plan": "port_plan_hash",
            "trace": "signoz_otlp",
        },
    }

