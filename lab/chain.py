"""Build and run Daisy's local multi-agent org from what this Mac can drive.

The topology is deliberately small. A CEO assigns work, specialists produce
against one shared goal, and the final peer is the reviewer. Port
owns the plan and approvals; deterministic gates own the verdict. No model can
mark its own work verified, expand its permissions, or silently add a vendor.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.models import inventory
from lab import executors


MAX_GOAL_CHARS = 12000
MAX_TASK_CHARS = 8000


def _node_id(model: dict) -> str:
    route = "%s/%s" % (model.get("provider"), model.get("id")) \
        if model.get("provider") else str(model.get("id") or "")
    return "%s:%s" % (model.get("vendor") or "agent", route)


def _effort_for(model: dict) -> str:
    if model.get("effort"):
        return str(model["effort"])
    efforts = list(model.get("efforts") or [])
    return "medium" if "medium" in efforts else (str(efforts[0]) if efforts else "")


def _identity(node: dict) -> str:
    return str(node.get("id") or node.get("agent") or "")


def topology(probes=None, model_inventory=None, cwd: str | None = None) -> dict:
    """Return an auditable role graph; never call an unprobed agent usable."""
    model_inventory = model_inventory if model_inventory is not None else inventory()
    probes = list(probes if probes is not None
                  else executors.available_models(model_inventory, cwd=cwd))
    usable = [probe for probe in probes if probe.ok]
    agents = list(dict.fromkeys(probe.name for probe in usable))
    models = list(model_inventory.get("models") or [])
    nodes = []
    exact_probes = any(probe.model for probe in probes)
    if exact_probes:
        for probe in usable:
            matched = [model for model in models
                       if model.get("vendor") == probe.name
                       and model.get("id") == probe.model
                       and str(model.get("provider") or "") == str(probe.provider or "")]
            for model in matched[:1]:
                nodes.append({
                    "id": _node_id(model),
                    "agent": probe.name,
                    "model": str(model.get("id") or ""),
                    "provider": str(model.get("provider") or ""),
                    "effort": _effort_for(model),
                    "role": "specialist",
                    "reports_to": "",
                    "probe_ms": probe.probe_ms,
                })
    else:
        for probe in usable:
            vendor_models = [model for model in models
                             if model.get("vendor") == probe.name and model.get("id")]
            vendor_models.sort(key=lambda model: not bool(model.get("current")))
            for model in vendor_models:
                nodes.append({
                    "id": _node_id(model),
                    "agent": probe.name,
                    "model": str(model.get("id") or ""),
                    "provider": str(model.get("provider") or ""),
                    "effort": _effort_for(model),
                    "role": "specialist",
                    "reports_to": "",
                    "probe_ms": probe.probe_ms,
                })
    if nodes:
        ceo = nodes[0]["id"]
        for index, node in enumerate(nodes):
            node["role"] = "ceo" if index == 0 else (
                "reviewer" if index == len(nodes) - 1 else "specialist")
            node["reports_to"] = "" if index == 0 else ceo
    ready = len(nodes) >= 2
    return {
        "ready": ready,
        "agents": agents,
        "nodes": nodes,
        "why": "" if ready else "Daisy Chain needs at least two models behind agents that answer a live probe.",
        "control": {
            "assignment": "ceo",
            "review": "peer",
            "verdict": "deterministic_gates",
            "plan": "port_plan_hash",
            "trace": "signoz_otlp",
        },
    }


def _gate(name: str, passed: bool, detail: str = "", margin: float = 0) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail,
            "margin": float(margin)}


def _json_object(text: str) -> dict | None:
    """Read one JSON object from a model reply without accepting prose as data."""
    raw = (text or "").strip()
    if "```" in raw:
        blocks = [part for part in raw.split("```") if "{" in part]
        if blocks:
            raw = max(blocks, key=len).lstrip()
            if raw.lower().startswith("json"):
                raw = raw[4:].lstrip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(raw[start:end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _assignments(payload: dict | None, peers: list[str]) -> tuple[list[dict], str]:
    rows = payload.get("assignments") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [], "CEO did not return an assignments array"
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            return [], "every assignment must be an object"
        agent, task = row.get("agent"), row.get("task")
        if agent not in peers or not isinstance(task, str) or not task.strip():
            return [], "every peer needs one named, nonempty task"
        clean.append({"agent": agent, "task": task.strip()[:MAX_TASK_CHARS]})
    names = [row["agent"] for row in clean]
    if sorted(names) != sorted(peers) or len(set(names)) != len(peers):
        return [], "CEO plan must assign every peer exactly once"
    return clean, ""


def _native_invoke(node: dict, prompt: str, cwd: str | None) -> dict:
    name = str(node.get("agent") or "")
    ex, probed = executors.pick(
        name, cwd=cwd, model=str(node.get("model") or ""),
        provider=str(node.get("provider") or ""))
    if not ex:
        reason = probed[0].detail if probed else "agent did not answer its probe"
        return {"agent": _identity(node), "ok": False, "reason": reason, "ms": 0,
                "stdout": "", "stderr": ""}
    response = executors.run(
        ex, prompt, cwd=cwd, model=str(node.get("model") or ""),
        effort=str(node.get("effort") or ""),
        provider=str(node.get("provider") or ""))
    response["agent"] = _identity(node)
    return response


def _gate_key(identity: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", identity).strip("_") or "unknown"


def orchestrate(goal: str, chain: dict, cwd: str | None = None, invoke=None) -> dict:
    """Run CEO -> all peers -> CEO synthesis -> independent peer review.

    ``invoke`` is the sole test seam. Production always resolves the named
    executor through a fresh live probe before calling its real CLI adapter.
    """
    goal = (goal or "").strip()[:MAX_GOAL_CHARS]
    nodes = list((chain or {}).get("nodes") or [])
    ready = bool((chain or {}).get("ready")) and len(nodes) >= 2 and bool(goal)
    ceo_node = nodes[0] if nodes else {}
    ceo = _identity(ceo_node)
    peer_nodes = [node for node in nodes[1:] if _identity(node)]
    peers = [_identity(node) for node in peer_nodes]
    node_by_id = {_identity(node): node for node in nodes}
    reviewer = peers[-1] if peers else ""

    def call(identity, prompt):
        if invoke:
            return invoke(identity, prompt)
        return _native_invoke(node_by_id[identity], prompt, cwd)
    result = {
        "ready": ready, "passed": False, "goal": goal, "ceo": ceo,
        "reviewer": reviewer, "assignments": [], "workers": {},
        "synthesis": "", "review": {}, "plan_attempts": 0, "gates": [],
    }
    if not ready:
        result["gates"].append(_gate(
            "chain.ready", False,
            (chain or {}).get("why") or "A nonempty goal and two usable agents are required."))
        return result

    roster = ", ".join(peers)
    plan_prompt = (
        "CEO_PLAN\nYou are the CEO of a local coding-agent team. Decompose the "
        "goal into exactly one concrete, bounded assignment for every peer. "
        "Do not do their work and do not add agents. Return only JSON in this "
        "shape: {\"assignments\":[{\"agent\":\"name\",\"task\":\"work\"}]}.\n"
        "GOAL:\n%s\nPEERS: %s" % (goal, roster))
    assignments, plan_why = [], ""
    for attempt in range(2):
        result["plan_attempts"] = attempt + 1
        response = call(ceo, plan_prompt if attempt == 0 else (
            "CEO_PLAN_REPAIR\nYour previous plan was rejected: %s. Return only "
            "the required JSON with every peer exactly once.\nGOAL:\n%s\nPEERS: %s"
            % (plan_why, goal, roster)))
        if not response.get("ok"):
            plan_why = response.get("reason") or "CEO invocation failed"
            continue
        assignments, plan_why = _assignments(_json_object(response.get("stdout", "")), peers)
        if assignments:
            break
    result["assignments"] = assignments
    plan_ok = bool(assignments)
    result["gates"].append(_gate("chain.plan.coverage", plan_ok, plan_why,
                                  len(assignments)))
    if not plan_ok:
        return result

    def run_worker(row):
        prompt = (
            "WORKER_TASK\nYou are %s, reporting to CEO %s. Complete only your "
            "assignment. State what you changed or proved, commands/tests run, "
            "and any blocker. Do not claim final verification.\nGOAL:\n%s\n"
            "ASSIGNMENT:\n%s" % (row["agent"], ceo, goal, row["task"]))
        return row["agent"], call(row["agent"], prompt)

    with ThreadPoolExecutor(max_workers=len(assignments)) as pool:
        futures = [pool.submit(run_worker, row) for row in assignments]
        for future in as_completed(futures):
            agent, response = future.result()
            result["workers"][agent] = response
    for peer in peers:
        response = result["workers"].get(peer) or {}
        ok = bool(response.get("ok") and (response.get("stdout") or "").strip())
        detail = "" if ok else (response.get("reason") or "peer returned no usable result")
        result["gates"].append(_gate("chain.worker.%s" % _gate_key(peer), ok, detail,
                                      response.get("ms") or 0))

    evidence = "\n\n".join(
        "PEER %s (%s):\n%s" % (
            peer, "ok" if result["workers"].get(peer, {}).get("ok") else "failed",
            (result["workers"].get(peer, {}).get("stdout") or
             result["workers"].get(peer, {}).get("reason") or "no result")[:16000])
        for peer in peers)
    synthesis_response = call(ceo, (
        "CEO_SYNTHESIS\nReconcile the peer results into one concise outcome for "
        "the original goal. Preserve blockers and test evidence. You may not "
        "declare the work verified; a peer and deterministic gates decide that.\n"
        "GOAL:\n%s\nRESULTS:\n%s" % (goal, evidence)))
    synthesis = (synthesis_response.get("stdout") or "").strip()
    result["synthesis"] = synthesis
    synthesis_ok = bool(synthesis_response.get("ok") and synthesis)
    result["gates"].append(_gate(
        "chain.synthesis", synthesis_ok,
        "" if synthesis_ok else (synthesis_response.get("reason") or "CEO returned no synthesis")))

    review_response = call(reviewer, (
        "PEER_REVIEW\nIndependently review the CEO synthesis against the goal and "
        "the peer evidence. Return only JSON: "
        "{\"passed\":true|false,\"findings\":[\"specific issue\"]}. "
        "A failed worker or unsupported claim must fail review.\nGOAL:\n%s\n"
        "PEER RESULTS:\n%s\nCEO SYNTHESIS:\n%s" % (goal, evidence, synthesis[:16000])))
    review = _json_object(review_response.get("stdout", "")) if review_response.get("ok") else None
    findings = review.get("findings") if isinstance(review, dict) else None
    review_ok = bool(isinstance(review, dict) and review.get("passed") is True
                     and isinstance(findings, list))
    result["review"] = review or {
        "passed": False,
        "findings": [review_response.get("reason") or "reviewer returned unreadable JSON"],
    }
    result["gates"].append(_gate(
        "chain.review", review_ok,
        "" if review_ok else "; ".join(str(item) for item in result["review"].get("findings", []))[:240]))
    result["passed"] = all(item["passed"] for item in result["gates"])
    return result
