"""
The shape of a Garden entry that an agent can actually act on.

A published solution used to carry a title, a one-line recipe and a gate
signature. That is enough to *find* the fix and nowhere near enough to *apply*
it. An agent that retrieves "invert sigma = 6M/(b t^2) for t" still has to
rediscover which file, which parameter, what the load case was, what to run to
confirm it worked, and — most importantly — whether the fix applies to its
situation at all.

So an entry is a runbook, not a note. Six blocks, each answering a question the
next agent will otherwise burn tokens re-deriving:

    problem        what was observed, and what detected it
    diagnosis      why it happened, and how to confirm it is this and not
                   something that looks like it
    fix            the actual change: steps, parameters with types, the code
    verification   what proves it worked — the load case, the command, the
                   expected result
    applicability  when this applies and, more usefully, when it does NOT.
                   A solution index without negative conditions is a trap:
                   the failure mode of reuse is applying a correct fix to the
                   wrong problem
    provenance     who produced it, on what, and what it cost

`applicability.does_not_apply_when` is the block that earns its place. Every
other field helps an agent go faster; that one stops it going confidently in
the wrong direction, which is the expensive mistake.

The `fix.steps` list is machine-shaped on purpose — each step has a `kind`
(`edit`, `run`, `check`), a target and a value, so an agent can execute it
rather than parse prose about it. Prose stays in `summary` for the human
reading the page.

Zero third-party dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

SCHEMA = "garden.solution/2"


@dataclass
class Step:
    kind: str                       # edit | run | check | note
    target: str = ""                # file, command, or gate name
    value: str = ""                 # new value, arguments, or expected result
    why: str = ""

    def d(self):
        return asdict(self)


@dataclass
class Detail:
    # -- problem ------------------------------------------------------------
    symptom: str = ""               # what an operator actually sees
    context: str = ""               # the situation it occurs in
    detected_by: str = ""           # the gate or check that caught it
    severity: str = "blocking"      # blocking | degraded | cosmetic

    # -- diagnosis ----------------------------------------------------------
    root_cause: str = ""
    why_it_happens: str = ""
    confirm_with: str = ""          # how to be sure it is this

    # -- fix ----------------------------------------------------------------
    fix_summary: str = ""
    steps: list = field(default_factory=list)      # [Step]
    parameters: dict = field(default_factory=dict) # name -> {type, from, to, unit}
    code: str = ""                  # the essential change, runnable or near it

    # -- verification -------------------------------------------------------
    load_case: dict = field(default_factory=dict)
    verify_commands: list = field(default_factory=list)
    expected: str = ""

    # -- applicability ------------------------------------------------------
    applies_when: list = field(default_factory=list)
    does_not_apply_when: list = field(default_factory=list)
    preconditions: list = field(default_factory=list)

    # -- relations ----------------------------------------------------------
    related_gates: list = field(default_factory=list)
    tags: list = field(default_factory=list)

    def to_manifest(self) -> dict:
        """Nested, because the blocks are read independently.

        An agent deciding *whether* to use a solution reads `applicability` and
        stops. One that has decided reads `fix`. Flattening these into one bag
        of keys makes every consumer read all of it.
        """
        return {
            "schema": SCHEMA,
            "problem": {
                "symptom": self.symptom,
                "context": self.context,
                "detected_by": self.detected_by,
                "severity": self.severity,
            },
            "diagnosis": {
                "root_cause": self.root_cause,
                "why_it_happens": self.why_it_happens,
                "confirm_with": self.confirm_with,
            },
            "fix": {
                "summary": self.fix_summary,
                "steps": [s.d() if isinstance(s, Step) else s for s in self.steps],
                "parameters": self.parameters,
                "code": self.code,
            },
            "verification": {
                "load_case": self.load_case,
                "commands": self.verify_commands,
                "expected": self.expected,
            },
            "applicability": {
                "applies_when": self.applies_when,
                "does_not_apply_when": self.does_not_apply_when,
                "preconditions": self.preconditions,
            },
            "related": {"gates": self.related_gates, "tags": self.tags},
        }


def completeness(d: dict) -> dict:
    """How much of the runbook is actually filled in.

    Published as a number on every entry, because an index that shows a thin
    entry and a complete one identically teaches agents that thin is fine.
    """
    checks = {
        "symptom": bool(d.get("problem", {}).get("symptom")),
        "detected_by": bool(d.get("problem", {}).get("detected_by")),
        "root_cause": bool(d.get("diagnosis", {}).get("root_cause")),
        "confirm_with": bool(d.get("diagnosis", {}).get("confirm_with")),
        "steps": len(d.get("fix", {}).get("steps") or []) > 0,
        "parameters": bool(d.get("fix", {}).get("parameters")),
        "verify": len(d.get("verification", {}).get("commands") or []) > 0,
        "expected": bool(d.get("verification", {}).get("expected")),
        "applies_when": len(d.get("applicability", {}).get("applies_when") or []) > 0,
        # weighted double: the block that prevents misapplication
        "does_not_apply_when": len(d.get("applicability", {}).get("does_not_apply_when") or []) > 0,
    }
    have = sum(1 for v in checks.values() if v)
    return {"score": round(have / len(checks), 2), "have": have,
            "of": len(checks),
            "missing": sorted(k for k, v in checks.items() if not v)}
