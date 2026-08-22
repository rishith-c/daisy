"""
The factory's data model, as declarations rather than code.

WHY this file exists
--------------------
Port's Context Lake only knows what you model in it. If the blueprints live
inside the orchestrator as ad-hoc dicts, then the shape of a run is whatever
the last person to touch labctl happened to send, and the catalog degrades into
a log with extra steps. Declaring the model in one file makes it reviewable in
a diff, testable without a network, and — the part that matters on stage — the
same object that gets POSTed is the object the test suite asserts against.

Seven blueprints for the run itself:

    Brief     what a human asked for, plus the goals, choices and risks
    Run       one invocation of the factory, and the scalars its scorecard reads
    Lane      one agent in one worktree
    Gate      one deterministic verification outcome
    Repair    what fixed a failed gate, and whether it was solved or guessed
    Approval  a human decision, bound to the plan hash it was given
    Artifact  a file the run produced

and one more, Service, because a workspace that does not say what it depends on
cannot tell you what breaks when a dependency does. Each service carries its
failure mode and its fallback, so the risk register is part of the catalog
rather than a slide.

The scorecard on Run encodes the factory's real thresholds — FoS 1.5, contrast
4.5:1, zero taste findings, scrape inside its TTL — and one rule that no
software gate can substitute for: a human approved it.

What this file deliberately does NOT do:

    - it does not own the thresholds. FOS_MIN and CONTRAST_MIN are the numbers
      hardware/ and taste/ already enforce; SCRAPE_TTL_S is the ttl in
      CLAUDE.md. They are restated here because a scorecard rule can only
      compare against a literal, and the constants are exported so the factory
      reads the same number rather than a second copy of it.
    - it does not compute anything about a run. Run's scalars are written by
      factory.summarise() from recorded gates; nothing here inspects a build.
    - the local evaluate() is not a reimplementation of Port. Port is the
      authority whenever Port is reachable. evaluate() exists so the rules are
      testable offline and so a spooled run can still show a verdict instead of
      a shrug.

Zero third-party dependencies.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# thresholds — the same numbers the deterministic gates already enforce
# ---------------------------------------------------------------------------

FOS_MIN = 1.5            # hardware/margins.py — factor of safety on every physics gate
CONTRAST_MIN = 4.5       # taste/contrast.py — WCAG AA for normal text, both themes
TASTE_MAX_FINDINGS = 0   # taste/lint.py — named tells, not a score
SCRAPE_TTL_S = 900       # CLAUDE.md c_bolt_table ttl = 15m; past it, no certification

# Blueprint identifiers are prefixed because a Port org is shared. An
# unprefixed `run` blueprint is the kind of thing that collides at 2am.
BP_BRIEF = "daisy_brief"
BP_RUN = "daisy_run"
BP_LANE = "daisy_lane"
BP_GATE = "daisy_gate"
BP_REPAIR = "daisy_repair"
BP_APPROVAL = "daisy_approval"
BP_ARTIFACT = "daisy_artifact"
BP_SERVICE = "daisy_service"

SCORECARD_ID = "release_readiness"
ACTION_ID = "daisy_approve_plan"

# Icons are cosmetic and are the first thing to drop if Port rejects a name;
# they live here so that is one edit rather than eight.
ICONS = {
    BP_BRIEF: "Book", BP_RUN: "Deployment", BP_LANE: "Microservice",
    BP_GATE: "Lock", BP_REPAIR: "Bug", BP_APPROVAL: "User",
    BP_ARTIFACT: "Package", BP_SERVICE: "Service",
}


def _s(title, **kw):
    d = {"type": "string", "title": title}
    d.update(kw)
    return d


def _n(title, **kw):
    d = {"type": "number", "title": title}
    d.update(kw)
    return d


def _b(title, **kw):
    d = {"type": "boolean", "title": title}
    d.update(kw)
    return d


def _arr(title, **kw):
    d = {"type": "array", "title": title, "items": {"type": "string"}}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# blueprints
# ---------------------------------------------------------------------------

BRIEF = {
    "identifier": BP_BRIEF,
    "title": "Brief",
    "icon": ICONS[BP_BRIEF],
    "description": "What a human asked for, and what would make it a success.",
    "schema": {
        "properties": {
            "text": _s("Ask", format="markdown",
                       description="The request verbatim. Never a paraphrase — the "
                                   "paraphrase is the plan, and the plan is reviewed."),
            "requester": _s("Requester"),
            "source": _s("Source", enum=["ui", "cli", "slack", "webhook"]),
            "submitted_at": _s("Submitted", format="date-time"),
            "goals": _arr("Goals"),
            "non_goals": _arr("Non-goals",
                              description="Stated so scope creep is a diff, not a debate."),
            "technical_choices": _arr("Technical choices"),
            "risks": _arr("Risk factors"),
            "acceptance": _arr("Acceptance criteria"),
        },
        "required": ["text"],
    },
    "relations": {},
}

RUN = {
    "identifier": BP_RUN,
    "title": "Run",
    "icon": ICONS[BP_RUN],
    "description": "One invocation of the factory. Committed before any agent starts.",
    "schema": {
        "properties": {
            "status": _s("Status", enum=["planned", "building", "gated", "awaiting_approval",
                                         "released", "blocked", "denied"]),
            "plan_sha": _s("Plan hash",
                           description="blake2b over the committed plan. The approval is "
                                       "bound to this value, so a plan edited after "
                                       "approval cannot be released."),
            "plan": _s("Plan", format="markdown"),
            "mode": _s("Governance mode", enum=["live", "dry"],
                       description="Whether Port was actually reached, or the run was "
                                   "spooled. Recorded so no report can imply the wrong one."),
            "started_at": _s("Started", format="date-time"),
            "released_at": _s("Released", format="date-time"),
            "fos_target": _n("Factor of safety target"),
            "retry_cap": _n("Retry cap"),
            # -- the four scalars the scorecard reads, plus the approval bit --
            "gates_run": _n("Gates run"),
            "gates_failed": _n("Gates failed"),
            "min_physics_fos": _n("Lowest physics FoS"),
            "min_contrast_ratio": _n("Lowest contrast ratio"),
            "taste_findings": _n("Taste findings"),
            "scrape_age_s": _n("Scrape age (s)"),
            "approved": _b("Approved"),
            "approved_by": _s("Approved by"),
            "audit_head": _s("Audit chain head"),
        },
        "required": ["status"],
    },
    "relations": {
        "brief": {"target": BP_BRIEF, "title": "Brief", "many": False, "required": True},
        "lanes": {"target": BP_LANE, "title": "Lanes", "many": True, "required": False},
        "services": {"target": BP_SERVICE, "title": "Services", "many": True, "required": False},
        # Not required, and that is a deliberate and slightly uncomfortable
        # choice: the Run entity has to exist BEFORE the approval does, because
        # committing the plan is what the human is approving. A schema
        # constraint here would make the plan uncommittable, so the requirement
        # lives on the release path (factory.release) and on the Gold rule
        # below, where it can actually be enforced.
        "approval": {"target": BP_APPROVAL, "title": "Approval", "many": False,
                     "required": False},
    },
}

LANE = {
    "identifier": BP_LANE,
    "title": "Lane",
    "icon": ICONS[BP_LANE],
    "description": "One agent, one worktree, one slice of the contract.",
    "schema": {
        "properties": {
            "agent": _s("Agent", enum=["claude", "codex", "human", "deterministic"]),
            "model": _s("Model"),
            "worktree": _s("Worktree"),
            "branch": _s("Branch"),
            "status": _s("Status", enum=["planned", "running", "passed", "failed", "skipped"]),
            "files_changed": _n("Files changed"),
            "insertions": _n("Insertions"),
            "deletions": _n("Deletions"),
            "started_at": _s("Started", format="date-time"),
        },
        "required": ["agent"],
    },
    "relations": {
        "gates": {"target": BP_GATE, "title": "Gates", "many": True, "required": False},
        "artifacts": {"target": BP_ARTIFACT, "title": "Artifacts", "many": True,
                      "required": False},
    },
}

GATE = {
    "identifier": BP_GATE,
    "title": "Gate",
    "icon": ICONS[BP_GATE],
    "description": "One deterministic verification outcome, with the formula behind it.",
    "schema": {
        "properties": {
            "name": _s("Gate", description="physics.bend, taste.t2, contract.conformance, ..."),
            "kind": _s("Kind", enum=["physics", "taste", "contract", "scrape", "smoke"]),
            "passed": _b("Passed"),
            "value": _n("Measured"),
            "allowable": _n("Allowable"),
            "margin": _n("Margin", description="Ratio to allowable. Below 1.0 is a failure."),
            "unit": _s("Unit"),
            "formula": _s("Formula",
                          description="Written down so a reviewer can redo the arithmetic "
                                      "by hand. A gate nobody can check is a vibe."),
            "detail": _s("Detail"),
            "deterministic": _b("Deterministic",
                                description="False means a model was in the decision path, "
                                            "which is worth knowing before you trust it."),
            "ran_at": _s("Ran at", format="date-time"),
            "duration_ms": _n("Duration (ms)"),
        },
        "required": ["name", "passed"],
    },
    "relations": {
        "repair": {"target": BP_REPAIR, "title": "Repair", "many": False, "required": False},
    },
}

REPAIR = {
    "identifier": BP_REPAIR,
    "title": "Repair",
    "icon": ICONS[BP_REPAIR],
    "description": "What fixed a failed gate — and whether it was solved or guessed.",
    "schema": {
        "properties": {
            "parameter": _s("Parameter"),
            "from_value": _s("From"),
            "to_value": _s("To"),
            "derivation": _s("Derivation",
                             description="The algebra, where there is algebra. "
                                         "t = sqrt(6M / (b * sigma_allow / FoS))"),
            "kind": _s("Kind", enum=["algebra", "resume-findings", "precedent", "human", "none"]),
            "applied": _b("Applied"),
            "precedent_run": _s("Cited precedent"),
            "precedent_score": _n("Precedent score"),
        },
        "required": ["kind"],
    },
    "relations": {},
}

APPROVAL = {
    "identifier": BP_APPROVAL,
    "title": "Approval",
    "icon": ICONS[BP_APPROVAL],
    "description": "A human decision, bound to the exact plan hash it was shown.",
    "schema": {
        "properties": {
            "state": _s("State", enum=["pending", "granted", "denied", "timeout"]),
            "scope": _s("Scope", description="What the decision authorises: plan, release, heal."),
            "plan_sha": _s("Plan hash",
                           description="The plan as it stood when the decision was asked "
                                       "for. Release compares against this."),
            "summary": _s("Summary", format="markdown"),
            "requested_at": _s("Requested", format="date-time"),
            "requested_by": _s("Requested by"),
            "decided_at": _s("Decided", format="date-time"),
            "decided_by": _s("Decided by"),
            "reason": _s("Reason"),
            "action_run_id": _s("Port action run"),
        },
        "required": ["state"],
    },
    "relations": {},
}

ARTIFACT = {
    "identifier": BP_ARTIFACT,
    "title": "Artifact",
    "icon": ICONS[BP_ARTIFACT],
    "description": "A file the run produced, hashed so the release can be traced back to it.",
    "schema": {
        "properties": {
            "path": _s("Path"),
            "kind": _s("Kind", enum=["diff", "stl", "report", "contract", "screenshot", "bom"]),
            "bytes": _n("Bytes"),
            "sha256": _s("SHA-256"),
            "url": _s("URL", format="url"),
            "produced_at": _s("Produced", format="date-time"),
        },
        "required": ["path"],
    },
    "relations": {},
}

SERVICE = {
    "identifier": BP_SERVICE,
    "title": "Service",
    "icon": ICONS[BP_SERVICE],
    "description": "An external dependency, with what happens when it is not there.",
    "schema": {
        "properties": {
            "kind": _s("Kind", enum=["governance", "data", "observability", "agent",
                                     "scm", "runtime"]),
            "purpose": _s("Purpose"),
            "criticality": _s("Criticality", enum=["critical", "important", "optional"]),
            "failure_mode": _s("Failure mode",
                               description="What the factory does when this is unreachable. "
                                           "'It degrades gracefully' is not an answer."),
            "fallback": _s("Fallback"),
            "credentials": _s("Credentials", description="Which env vars it needs."),
            "docs_url": _s("Docs", format="url"),
        },
        "required": ["kind", "criticality"],
    },
    "relations": {},
}

# Port validates a relation's target at create time, so targets must exist
# first. This order is asserted by the test suite rather than trusted.
ORDER = [BP_BRIEF, BP_REPAIR, BP_GATE, BP_ARTIFACT, BP_LANE, BP_APPROVAL, BP_SERVICE, BP_RUN]
BLUEPRINTS = {b["identifier"]: b for b in
              (BRIEF, REPAIR, GATE, ARTIFACT, LANE, APPROVAL, SERVICE, RUN)}


# ---------------------------------------------------------------------------
# the scorecard — the factory's thresholds, in Port's language
# ---------------------------------------------------------------------------

def _rule(identifier, title, level, conditions, combinator="and", description=""):
    r = {"identifier": identifier, "title": title, "level": level,
         "query": {"combinator": combinator, "conditions": conditions}}
    if description:
        r["description"] = description
    return r


LEVELS = [
    {"title": "Basic", "color": "paleBlue"},
    {"title": "Bronze", "color": "bronze"},
    {"title": "Silver", "color": "silver"},
    {"title": "Gold", "color": "gold"},
]

SCORECARD = {
    "identifier": SCORECARD_ID,
    "title": "Release readiness",
    "levels": LEVELS,
    "rules": [
        # Bronze — the run is governed at all.
        _rule("plan_committed", "The plan was committed before anything ran", "Bronze",
              [{"property": "plan_sha", "operator": "isNotEmpty"}],
              description="No plan hash means no agent should ever have started."),
        _rule("gates_ran", "The deterministic gates ran", "Bronze",
              [{"property": "gates_run", "operator": ">=", "value": 1}]),
        _rule("no_failed_gates", "Every gate that ran, passed", "Bronze",
              [{"property": "gates_failed", "operator": "=", "value": 0}]),

        # Silver — the four thresholds, exactly as hardware/, taste/ and the
        # scraper config enforce them.
        _rule("physics_fos", "Physics margin at or above FoS %.1f" % FOS_MIN, "Silver",
              [{"property": "min_physics_fos", "operator": ">=", "value": FOS_MIN}],
              description="Closed-form bending, shear, mass and thermal. No model in the path."),
        _rule("wcag_contrast", "Every text pair at or above %.1f:1" % CONTRAST_MIN, "Silver",
              [{"property": "min_contrast_ratio", "operator": ">=", "value": CONTRAST_MIN}],
              description="WCAG AA for normal text, computed in both themes."),
        _rule("taste_clean", "Zero taste findings", "Silver",
              [{"property": "taste_findings", "operator": "=", "value": TASTE_MAX_FINDINGS}],
              description="Twenty named tells with file:line, not a score out of ten."),
        _rule("scrape_fresh", "Scraped ground truth inside its %d s TTL" % SCRAPE_TTL_S, "Silver",
              [{"property": "scrape_age_s", "operator": "<=", "value": SCRAPE_TTL_S}],
              description="Past the TTL the part cannot be certified: there is no "
                          "fallback table in the code path."),

        # Gold — the part no program can sign off.
        _rule("human_approved", "A human approved this exact plan", "Gold",
              [{"property": "approved", "operator": "=", "value": True},
               {"property": "approved_by", "operator": "isNotEmpty"}],
              description="Named, and bound to the plan hash. An unattributed approval "
                          "is an audit trail with a hole in it."),
    ],
}


# ---------------------------------------------------------------------------
# evaluating the scorecard locally
# ---------------------------------------------------------------------------

def _cmp(op, got, want):
    if op == "isEmpty":
        return got in (None, "", [], {})
    if op == "isNotEmpty":
        return got not in (None, "", [], {})
    if got is None:
        # Fail closed. A missing scalar means the gate that produces it never
        # ran, and "never ran" must never read as "passed".
        return False
    if op == "=":
        return got == want
    if op == "!=":
        return got != want
    if op in (">", ">=", "<", "<="):
        try:
            g, w = float(got), float(want)
        except (TypeError, ValueError):
            return False
        return {">": g > w, ">=": g >= w, "<": g < w, "<=": g <= w}[op]
    if op == "contains":
        return want in got
    if op in ("containsAny", "in"):
        return any(v in got for v in want) if op == "containsAny" else got in want
    raise ValueError("unsupported scorecard operator: %s" % op)


def evaluate(props: dict, scorecard: dict = SCORECARD) -> dict:
    """Score one Run's properties against the scorecard, offline.

    Port is the authority when Port is reachable; this exists so the rules are
    testable without a network and so a spooled run still gets a verdict.

    Levels are cumulative, the way Port treats them: a run reaches Gold only if
    every Bronze and Silver rule passes too. Skipping a level because its rules
    happen to be absent would be the single most dangerous bug in this file.
    """
    order = [lv["title"] for lv in scorecard.get("levels", LEVELS)]
    results = []
    for rule in scorecard["rules"]:
        q = rule["query"]
        outcomes = [_cmp(c["operator"], props.get(c["property"]), c.get("value"))
                    for c in q["conditions"]]
        ok = all(outcomes) if q.get("combinator", "and") == "and" else any(outcomes)
        results.append({"identifier": rule["identifier"], "title": rule["title"],
                        "level": rule["level"], "passed": ok})

    level = order[0]
    for name in order[1:]:
        if all(r["passed"] for r in results if r["level"] == name):
            level = name
        else:
            break
    failed = [r["identifier"] for r in results if not r["passed"]]
    return {
        "scorecard": scorecard["identifier"],
        "level": level,
        "gold": level == order[-1],
        "rules": results,
        "failed": failed,
        "passed": not failed,
    }


# ---------------------------------------------------------------------------
# the approval action — the human-facing half, in Port's own UI
# ---------------------------------------------------------------------------

APPROVE_ACTION = {
    "identifier": ACTION_ID,
    "title": "Approve run plan",
    "icon": ICONS[BP_APPROVAL],
    "description": "Release a planned run. Nothing merges without this.",
    "trigger": {
        "type": "self-service",
        "operation": "DAY-2",
        "blueprintIdentifier": BP_RUN,
        "userInputs": {
            "properties": {
                "reason": {"type": "string", "title": "Reason"},
                "plan_sha": {"type": "string", "title": "Plan hash being approved"},
            },
            "required": ["plan_sha"],
        },
    },
    "invocationMethod": {"type": "WEBHOOK", "url": "https://example.invalid/daisy/approve",
                         "agent": False, "synchronized": False},
    # ANY rather than ALL: one named human is a real gate; requiring a quorum
    # that does not exist at 2am is how approval gates get switched off.
    "requiredApproval": {"type": "ANY"},
    "approvalNotification": {"type": "email"},
    "publish": True,
}


# ---------------------------------------------------------------------------
# the service catalog — what this depends on, and what breaks without it
# ---------------------------------------------------------------------------

SERVICES = [
    {"identifier": "port", "title": "Port", "properties": {
        "kind": "governance", "criticality": "critical",
        "purpose": "Context Lake, scorecards, and the approval gate the release path reads.",
        "failure_mode": "The plan lookup fails, so no lane spawns. The run stops rather "
                        "than proceeding ungoverned.",
        "fallback": "Dry mode: requests spool to port/spool/*.jsonl and reads replay from "
                    "them. Clearly labelled, never presented as a live call.",
        "credentials": "PORT_CLIENT_ID, PORT_CLIENT_SECRET",
        "docs_url": "https://docs.port.io"}},
    {"identifier": "bright_data", "title": "Bright Data", "properties": {
        "kind": "data", "criticality": "critical",
        "purpose": "Vendor fastener rows and design-DNA reference pages — the ground "
                   "truth the physics gate selects against.",
        "failure_mode": "hardware.margins raises NoGroundTruth. The part is not certified; "
                        "there is deliberately no fallback table in the code path.",
        "fallback": "None, by design. A hardcoded price would turn a data pipeline into "
                    "a constant and nobody would notice.",
        "credentials": "BRIGHTDATA_API_TOKEN",
        "docs_url": "https://docs.brightdata.com"}},
    {"identifier": "signoz", "title": "SigNoz", "properties": {
        "kind": "observability", "criticality": "important",
        "purpose": "Spans per lane and the alert that escalates a stuck run to a human.",
        "failure_mode": "Escalation goes silent — a run can hang without anyone being told.",
        "fallback": "The gate results are still written to Port, so the run is auditable "
                    "after the fact; it just is not noticed in time.",
        "credentials": "SIGNOZ_INGESTION_KEY",
        "docs_url": "https://signoz.io/docs"}},
    {"identifier": "claude_code", "title": "Claude Code", "properties": {
        "kind": "agent", "criticality": "important",
        "purpose": "Frontend and hardware lanes, run headless in their own worktrees.",
        "failure_mode": "Those lanes fail their gates and the run blocks. It never "
                        "releases unbuilt work.",
        "fallback": "The Codex lane still runs; the contract gate catches the half-built "
                    "result rather than merging it.",
        "credentials": "ANTHROPIC_API_KEY (or an authenticated CLI)",
        "docs_url": "https://docs.claude.com/en/docs/claude-code"}},
    {"identifier": "codex", "title": "Codex", "properties": {
        "kind": "agent", "criticality": "important",
        "purpose": "API lane, pinned to the same api-contract.json as the frontend lane.",
        "failure_mode": "Same as above: the contract gate fails and the run blocks.",
        "fallback": "None needed — the gate is the fallback.",
        "credentials": "OPENAI_API_KEY (or an authenticated CLI)",
        "docs_url": "https://developers.openai.com/codex"}},
    {"identifier": "git", "title": "Git worktrees", "properties": {
        "kind": "scm", "criticality": "critical",
        "purpose": "One isolated worktree per lane. The orchestrator owns every commit; "
                   "no agent touches git.",
        "failure_mode": "Lanes cannot be isolated, so nothing starts.",
        "fallback": "None. Two agents in one tree is not a degraded mode, it is a "
                    "different and much worse product.",
        "credentials": "none",
        "docs_url": "https://git-scm.com/docs/git-worktree"}},
]

# The default brief: this project, described the way the catalog wants it.
PROJECT = {
    "text": "Ship Daisy: one brief in, verified software and verified hardware out, "
            "with every artifact passing gates a human can audit line by line.",
    "requester": "rishith",
    "source": "cli",
    "goals": [
        "One brief produces a plan, parallel agent lanes, gate results, and a release.",
        "Every gate is a program with a formula, not a model's opinion.",
        "No agent starts before the plan is committed to Port.",
        "Nothing releases without a named human approving that exact plan.",
        "Runs on a judge's laptop with no install and no network.",
    ],
    "non_goals": [
        "FEA, fatigue, buckling or impact analysis — the physics gate is statics.",
        "A hosted control plane. This is one repo and stdlib Python.",
        "Agents that can approve or merge their own work.",
    ],
    "technical_choices": [
        "Pure stdlib Python: urllib, sqlite3, hashlib. Zero third-party packages.",
        "Deterministic gates (closed-form margins, computed contrast) over LLM review.",
        "Gate-signature hybrid retrieval for precedent, so repeat failures are cited.",
        "Port for governance so the thing deciding to merge is not the thing that built it.",
        "Dry mode with a replayable spool, because venue wifi is a load-bearing assumption.",
    ],
    "risks": [
        "Venue network down — mitigated by dry mode; the spool records every request.",
        "Credentials absent at demo time — the client refuses to claim a live call.",
        "Scraped ground truth stale past its 15 minute TTL — certification is refused.",
        "An agent retrying blindly — retry cap 2, findings injected verbatim.",
        "Approval treated as a notification — release compares the plan hash and refuses drift.",
    ],
    "acceptance": [
        "./verify.sh exits 0.",
        "python3 -m port.test_port passes with no network and no credentials.",
        "A denied approval leaves the run blocked, not released.",
    ],
}
