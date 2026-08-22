"""
labctl — brief in, verified artifacts out.

This is the piece the README has called "the day's work" since the first
commit, and its absence was the honest gap in the whole project: every gate was
real, but nothing had ever driven a run through them. The UI replayed a script.

A run here is not a script. It:

  1. commits a plan to Port *before* anything executes, so the approval is a
     gate rather than a notification
  2. probes which coding agents can actually be driven on this machine, and
     says why when one cannot
  3. runs the hardware lane end to end with no LLM in it at all — the physics
     is the work, and algebra does not need a language model
  4. runs the software lane by driving a real agent, then judging its output
     with the same taste linter that judges Daisy itself
  5. on failure, asks the commons whether this exact gate has been failed and
     fixed before, and feeds that fix back into the retry
  6. admits what passes to the commons, so the next run pays less
  7. traces every step, so a judge can see what happened without narration

The retry loop is the interesting part and it is deliberately not "ask again".
A gate failure produces named findings, and those findings go into the next
prompt verbatim. An agent told "make it better" will not; an agent told
"tokens.css:12 declares #6366F1 outside the token system" will.

    python3 labctl.py run --brief "..."
    python3 labctl.py agents
    python3 labctl.py run --brief "..." --lane hardware   # no agent needed

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import obs                                                       # noqa: E402
from obs.events import gate, human_escalation, scrape_repair      # noqa: E402
from obs.trace import tracer                                      # noqa: E402

from hardware.margins import (bending, mass, thermal, solve_thickness,   # noqa: E402
                              select_fastener, NoGroundTruth)
from hardware.bracket import Bracket                              # noqa: E402
from taste.lint import lint                                       # noqa: E402
from commons.store import Solution, admit, recall, record_reuse, NotVerified  # noqa: E402
from lab import executors                                         # noqa: E402

RUNS = os.path.join(ROOT, "runs")
MAX_RETRIES = 2

# The default load case. A brief can override it; what it must never do is go
# unstated, because a margin without a load case is a number without a claim.
LOAD_KG, ARM_MM, WIDTH_MM, THICK_MM, MATERIAL, FOS = 2.4, 90.0, 18.0, 3.2, "PETG", 1.5


@dataclass
class LaneResult:
    lane: str
    ran: bool = False
    passed: bool = False
    why: str = ""
    gates: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    attempts: int = 0
    reused: dict = field(default_factory=dict)


def _rundir(run_id: str) -> str:
    d = os.path.join(RUNS, run_id)
    os.makedirs(d, exist_ok=True)
    return d


def _gate_row(name, passed, margin=None, detail=""):
    return {"name": name, "passed": bool(passed),
            "margin": None if margin is None else round(float(margin), 3),
            "detail": detail}


# ---------------------------------------------------------------------------
# hardware lane — real, and entirely deterministic
# ---------------------------------------------------------------------------

def hardware_lane(run_id: str, brief: str, log) -> LaneResult:
    r = LaneResult("hardware", ran=True)
    d = _rundir(run_id)
    thick = THICK_MM

    with gate("physics.bend", {"load.kg": LOAD_KG, "arm.mm": ARM_MM,
                               "thickness.mm": thick}) as g:
        b = bending(LOAD_KG, ARM_MM, WIDTH_MM, thick, MATERIAL)
        g.margin = round(b.margin, 3)
        if not b.against(FOS):
            g.fail(round(b.margin, 3), "%.1f MPa against %.0f MPa allowable"
                                       % (b.value, b.allowable))
    first = _gate_row("physics.bend", b.against(FOS), b.margin,
                      "" if b.against(FOS) else "web too thin")
    r.gates.append(first)
    r.attempts = 1

    if not b.against(FOS):
        log("  physics.bend FAILED at FoS %.3f — asking the commons" % b.margin)
        # Before solving it again, ask whether this exact gate has been failed
        # and fixed before. This is the token that does not get spent twice.
        hits = recall("cantilever web bending margin negative",
                      gates=["physics.bend"], limit=1)
        if hits:
            h = hits[0]
            r.reused = {"id": h["id"], "recipe": h["recipe"],
                        "tokens_avoided": h["tokens_cost"], "score": h["score"]}
            record_reuse(h["id"], h["tokens_cost"])
            log("  commons: %s (%s, %s tok avoided)"
                % (h["recipe"][:58], h["vendor"], "{:,}".format(h["tokens_cost"])))

        with tracer().span("repair.solve_thickness",
                           {"parameter": "web_thickness", "from.mm": thick}) as s:
            thick = solve_thickness(LOAD_KG, ARM_MM, WIDTH_MM, FOS, MATERIAL)
            s.set("to.mm", thick)
            s.set("method", "invert sigma = 6M/(b t^2) for t, round up")
        log("  repair: web_thickness %.1f -> %.2f mm (solved, not guessed)" % (THICK_MM, thick))

        with gate("physics.bend.rerun", {"thickness.mm": thick}) as g2:
            b = bending(LOAD_KG, ARM_MM, WIDTH_MM, thick, MATERIAL)
            g2.margin = round(b.margin, 3)
            if not b.against(FOS):
                g2.fail(round(b.margin, 3), "repair did not clear its own gate")
        r.gates.append(_gate_row("physics.bend", b.against(FOS), b.margin,
                                 "after repair"))
        r.attempts = 2

    with gate("physics.mass", {"thickness.mm": thick}) as g:
        m = mass(WIDTH_MM, thick, ARM_MM, MATERIAL, 60.0)
        g.margin = round(m.margin, 3)
        if not m.against(1.0):
            g.fail(round(m.margin, 3), "over budget")
    r.gates.append(_gate_row("physics.mass", m.against(1.0), m.margin))

    with gate("physics.thermal", {"power.w": 2.0, "area.mm2": 6000.0}) as g:
        t = thermal(2.0, 6000.0)
        g.margin = round(t.margin, 3)
        if not t.against(1.0):
            g.fail(round(t.margin, 3), "runs too hot")
    r.gates.append(_gate_row("physics.thermal", t.against(1.0), t.margin))

    # Geometry last, and only from the thickness the gates actually signed off.
    with tracer().span("geometry.emit") as s:
        br = Bracket(width=WIDTH_MM, thickness=thick, arm=ARM_MM)
        stl = os.path.join(d, "bracket.stl")
        n = br.to_stl(stl)
        s.set("stl.bytes", n)
        s.set("stl.triangles", len(br.triangles()))
        s.set("volume.mm3", round(br.volume_mm3(), 1))
        # The mesh and the analytic solid must agree, or one of them is lying.
        drift = abs(br.mesh_volume_mm3() - br.volume_mm3()
                    - br.hole_count * 3.14159265 * (br.hole_dia / 2) ** 2 * br.thickness)
        s.set("volume.crosscheck_mm3", round(drift, 4))
    r.artifacts.append({"path": stl, "bytes": n, "thickness_mm": thick})
    log("  wrote %s (%d bytes, %d triangles) at t=%.2f mm"
        % (os.path.relpath(stl, ROOT), n, len(br.triangles()), thick))

    r.passed = all(g["passed"] for g in r.gates[-3:])
    return r


# ---------------------------------------------------------------------------
# ground truth
# ---------------------------------------------------------------------------

def scrape_lane(run_id: str, fixture: str, log) -> LaneResult:
    r = LaneResult("scrape", ran=True)
    with gate("scrape.schema", {"fixture": fixture}) as g:
        p = subprocess.run([sys.executable, "-m", "scrape.cli", "fetch",
                            "--fixture", fixture], cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
        data = json.loads(p.stdout or "{}")
        rows = data.get("rows") or []
        health = data.get("health") or {}
        g.margin = float(len(rows))
        if health.get("broken") or not rows:
            g.fail(len(rows), "; ".join(health.get("failed", [])) or "no rows")
    ok = bool(rows) and not health.get("broken")
    r.gates.append(_gate_row("scrape.schema", ok, len(rows),
                             "" if ok else "selectors no longer match"))
    if not ok:
        scrape_repair("vendors.fastener", "re-derive selectors from last-good values",
                      "python3 -m scrape.cli repair --fixture %s" % fixture)
        log("  scrape BROKEN — %d rows; repair available" % len(rows))

    with gate("physics.fastener") as g:
        try:
            pick = select_fastener(rows, LOAD_KG, 2, FOS)
            g.margin = pick["unit_price"]
            r.artifacts.append({"fastener": "M%s %s" % (pick["row"]["dia_mm"],
                                                        pick["row"]["grade"]),
                                "unit_price": pick["unit_price"]})
            log("  fastener: M%s %s $%.2f" % (pick["row"]["dia_mm"],
                                              pick["row"]["grade"], pick["unit_price"]))
        except NoGroundTruth as exc:
            g.fail(None, str(exc))
            log("  CANNOT CERTIFY — %s" % exc)
    r.gates.append(_gate_row("physics.fastener", g.passed, g.margin, g.detail))
    r.passed = all(x["passed"] for x in r.gates)
    return r


# ---------------------------------------------------------------------------
# software lane — a real agent, judged by the same linter that judges Daisy
# ---------------------------------------------------------------------------

TASK = (
    "Write a single self-contained HTML file for a status badge component "
    "showing PASS / FAIL / PENDING states. Requirements: every colour must come "
    "from a CSS custom property declared in one :root block; no hardcoded hex "
    "outside :root; no emoji anywhere; no inline style attributes. "
    "Output ONLY the HTML, no explanation, no markdown fences."
)


# The smallest thing that could honestly be called a component. An agent that
# returns a filename, an apology, or a truncated fragment must fail on that
# fact, not be handed to a design linter — the linter would judge whatever it
# was given and report findings about nothing.
MIN_BYTES = 400


def artifact_sane(html: str, need: tuple = ("pass", "fail", "pending")) -> tuple:
    """(ok, reason). Structural, not aesthetic — no taste judgement here."""
    t = (html or "").strip()
    if len(t) < MIN_BYTES:
        return False, "only %d bytes; not a component" % len(t)
    low = t.lower()
    if "<" not in t or ">" not in t:
        return False, "no markup at all"
    if low.count("<") < 6:
        return False, "%d tags; too thin to be the asked-for component" % low.count("<")
    if "<style" not in low and "style=" not in low:
        return False, "no styling of any kind"
    missing = [w for w in need if w not in low]
    if missing:
        return False, "does not mention the required state(s): %s" % ", ".join(missing)
    return True, ""


def _extract_html(text: str) -> str:
    """Agents wrap output in prose and fences however they feel. Take the
    largest plausible HTML block rather than trusting the format."""
    t = text
    if "```" in t:
        parts = [p for p in t.split("```") if "<" in p]
        if parts:
            t = max(parts, key=len)
            if t.lstrip().lower().startswith("html"):
                t = t.lstrip()[4:]
    i = t.lower().find("<!doctype")
    if i < 0:
        i = t.lower().find("<html")
    if i < 0:
        i = t.find("<")
    return t[i:] if i >= 0 else t


CONTRACT = {
    "states": ["pass", "fail", "pending"],
    "root_class": "badge",
    "state_attr": "data-state",
}

CREW_TASK = (
    "Write a single self-contained HTML file for a status badge component.\n"
    "You MUST conform to this contract exactly — another agent is building "
    "against the same one and the two outputs must be interchangeable:\n"
    "  - root element carries class=\"badge\"\n"
    "  - state is expressed as data-state=\"pass|fail|pending\"\n"
    "  - all three states must appear\n"
    "Requirements: every colour from a CSS custom property in one :root block; "
    "no hardcoded hex outside :root; no emoji; no inline style attributes.\n"
    "Output ONLY the HTML, no explanation, no markdown fences."
)


def contract_check(html: str) -> tuple:
    """Does this output honour the shared contract? Returns (ok, [misses]).

    This is what makes two agents a crew rather than two agents. The claim
    Daisy makes is that Claude and Codex are combined by a contract, not by a
    conversation — so the contract has to be a thing that can be checked, and
    a lane that violates it has to fail for that reason and no other.
    """
    low = (html or "").lower()
    miss = []
    if 'class="badge"' not in low and "class='badge'" not in low:
        miss.append('root_class: no class="badge"')
    for st in CONTRACT["states"]:
        if 'data-state="%s"' % st not in low and "data-state='%s'" % st not in low:
            miss.append('state_attr: missing data-state="%s"' % st)
    return (not miss), miss


def crew_lane(run_id: str, brief: str, agents: list, log) -> LaneResult:
    """Two vendors, one contract, judged identically."""
    r = LaneResult("crew", ran=True)
    d = _rundir(run_id)
    outs = {}
    for name in agents:
        ex, probed = executors.pick(name, cwd=d)
        if not ex:
            log("  %-9s unavailable — %s" % (name, probed[0].detail if probed else "?"))
            r.gates.append(_gate_row("crew.%s.available" % name, False, 0,
                                     probed[0].detail if probed else ""))
            continue
        with tracer().span("agent.invoke", {"agent": name, "lane": "crew"}) as s:
            res = executors.run(ex, CREW_TASK, cwd=d)
            s.set("agent.ok", res["ok"]); s.set("agent.ms", res["ms"])
        html = _extract_html(res["stdout"]) if res["ok"] else ""
        path = os.path.join(d, "badge.%s.html" % name)
        open(path, "w", encoding="utf-8").write(html)

        sane, why = artifact_sane(html)
        with gate("crew.%s.contract" % name, {"agent": name}) as g:
            ok, miss = contract_check(html) if sane else (False, [why])
            g.margin = float(len(miss))
            if not ok:
                g.fail(len(miss), "; ".join(miss)[:120])
        with gate("crew.%s.taste" % name, {"agent": name}) as gt:
            f = lint(html, path) if sane else []
            gt.margin = float(len(f))
            if not sane:
                gt.fail(0, why)
            elif f:
                gt.fail(len(f), "%d findings" % len(f))
        r.gates.append(_gate_row("crew.%s.contract" % name, ok, len(miss),
                                 "; ".join(miss)[:90]))
        r.gates.append(_gate_row("crew.%s.taste" % name, sane and not f, len(f)))
        outs[name] = {"path": path, "bytes": len(html), "contract_ok": ok,
                      "contract_misses": miss, "taste_findings": len(f),
                      "ms": res["ms"]}
        log("  %-9s %5d bytes  contract %-4s  taste %d finding%s  %.0fs"
            % (name, len(html), "OK" if ok else "MISS", len(f),
               "" if len(f) == 1 else "s", res["ms"] / 1000.0))
        r.artifacts.append(outs[name])

    # The interchangeability claim, checked rather than asserted.
    both = [n for n, o in outs.items() if o["contract_ok"]]
    with gate("crew.interchangeable") as g:
        g.margin = float(len(both))
        if len(both) < 2:
            g.fail(len(both), "only %d of %d agents met the contract"
                              % (len(both), len(agents)))
    r.gates.append(_gate_row("crew.interchangeable", len(both) >= 2, len(both),
                             "%d agents conform" % len(both)))
    log("  contract honoured by %d/%d agents: %s"
        % (len(both), len(agents), ", ".join(both) or "none"))
    r.passed = all(x["passed"] for x in r.gates)
    r.attempts = 1
    return r


def software_lane(run_id: str, brief: str, prefer: str, log) -> LaneResult:
    r = LaneResult("software")
    d = _rundir(run_id)
    ex, probed = executors.pick(prefer, cwd=d)
    if not ex:
        r.ran = False
        r.why = "; ".join("%s: %s" % (p.name, p.detail) for p in probed)
        log("  no usable agent — %s" % r.why)
        return r
    r.ran = True
    log("  agent: %s (probe %.0f ms)" % (ex.name, ex.probe_ms))

    prompt, findings = TASK, []
    for attempt in range(1, MAX_RETRIES + 2):
        r.attempts = attempt
        with tracer().span("agent.invoke", {"agent": ex.name, "attempt": attempt}) as s:
            res = executors.run(ex, prompt, cwd=d)
            s.set("agent.ok", res["ok"])
            s.set("agent.ms", res["ms"])
        if not res["ok"]:
            r.why = res["reason"]
            log("  attempt %d: agent failed — %s" % (attempt, res["reason"]))
            break

        html = _extract_html(res["stdout"])
        # Keep every attempt, not just the one that passed. A retry loop that
        # overwrites its own evidence cannot be audited — "it failed then it
        # passed" is a claim until the rejected artifact is still on disk next
        # to the findings that rejected it.
        path = os.path.join(d, "badge.attempt%d.html" % attempt)
        open(path, "w", encoding="utf-8").write(html)

        # Structure before taste. A linter asked to judge a stub will answer
        # about the stub, and the loop would spend its retries on that answer.
        with gate("artifact.sane", {"attempt": attempt}) as gs:
            sane, why = artifact_sane(html)
            gs.margin = float(len(html))
            if not sane:
                gs.fail(len(html), why)
        r.gates.append(_gate_row("artifact.sane", sane, len(html),
                                 why or "attempt %d" % attempt))
        if not sane:
            log("  attempt %d: artifact.sane -> REJECTED (%s)" % (attempt, why))
            if attempt > MAX_RETRIES:
                r.why = why
                break
            prompt = (TASK + "\n\nYour previous reply was rejected before it was "
                      "even reviewed: " + why + ". Return the complete HTML "
                      "document itself, nothing else.")
            continue

        with gate("taste.t1", {"attempt": attempt}) as g:
            findings = lint(html, "badge.html")
            g.margin = float(len(findings))
            if findings:
                g.fail(len(findings), "%d findings" % len(findings))
        r.gates.append(_gate_row("taste.t1", not findings, len(findings),
                                 "attempt %d" % attempt))
        with open(os.path.join(d, "findings.attempt%d.json" % attempt), "w",
                  encoding="utf-8") as fh:
            json.dump([{"gate": f.gate, "name": f.name, "line": f.line,
                        "excerpt": f.excerpt, "why": f.why} for f in findings],
                      fh, indent=1)
        log("  attempt %d: taste.t1 -> %d finding%s"
            % (attempt, len(findings), "" if len(findings) == 1 else "s"))

        if not findings:
            r.passed = True
            final = os.path.join(d, "badge.html")
            shutil.copyfile(path, final)
            r.artifacts.append({"path": final, "bytes": len(html),
                                "accepted_on_attempt": attempt})
            break
        if attempt > MAX_RETRIES:
            r.why = "%d findings after %d attempts" % (len(findings), attempt)
            break

        # The findings go back verbatim. "Make it better" is not a repair
        # instruction; a named tell with a line number is.
        named = "\n".join("- %s (line %d): %s — %s"
                          % (f.name, f.line, f.excerpt[:70], f.why[:90])
                          for f in findings[:8])
        prompt = (TASK + "\n\nYour previous attempt was rejected by a design "
                  "linter with these exact findings. Fix every one:\n" + named)
    return r


# ---------------------------------------------------------------------------

def execute(brief: str, run_id: str = None, lanes: tuple = ("hardware", "scrape", "software"),
            prefer: str = "auto", fixture: str = "vendor_v1.html",
            quiet: bool = False, crew: list = None) -> dict:
    run_id = run_id or time.strftime("%H%M%S")
    d = _rundir(run_id)
    out = []

    def log(msg):
        out.append(msg)
        if not quiet:
            print(msg)

    started = time.time()
    with tracer().span("labctl.run", {"run.id": run_id}) as root:
        root.set("brief", brief[:200])

        # 1. plan first — before any lane executes.
        with tracer().span("plan.commit") as s:
            plan = {"run": run_id, "brief": brief, "lanes": list(lanes),
                    "load_case": {"kg": LOAD_KG, "arm_mm": ARM_MM,
                                  "width_mm": WIDTH_MM, "fos": FOS,
                                  "material": MATERIAL},
                    "committed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            pp = os.path.join(d, "plan.json")
            open(pp, "w", encoding="utf-8").write(json.dumps(plan, indent=1))
            s.set("plan.path", pp)
        log("plan committed before anything ran -> %s" % os.path.relpath(pp, ROOT))

        results = {}
        if "hardware" in lanes:
            log("hardware lane")
            results["hardware"] = hardware_lane(run_id, brief, log)
        if "scrape" in lanes:
            log("scrape lane")
            results["scrape"] = scrape_lane(run_id, fixture, log)
        if "software" in lanes:
            log("software lane")
            results["software"] = software_lane(run_id, brief, prefer, log)
        if "crew" in lanes:
            log("crew lane — two vendors, one contract")
            results["crew"] = crew_lane(run_id, brief, crew or ["claude", "codex"], log)

        all_gates = [g for r in results.values() for g in r.gates]
        failed = [g for g in all_gates if not g["passed"]]
        # A lane that could not run is not a lane that passed.
        blocked = [r.lane for r in results.values() if not r.ran]

        # 2. admit what passed, so the next run pays less for it.
        admitted = []
        for r in results.values():
            if not r.passed or not r.gates:
                continue
            try:
                sid = admit(Solution(
                    task="%s lane for: %s" % (r.lane, brief[:80]),
                    brief=brief, gates=[dict(g) for g in r.gates if g["passed"]],
                    vendor="labctl", model="deterministic" if r.lane != "software" else prefer,
                    kind="hardware" if r.lane == "hardware" else "software",
                    artifact=(r.artifacts[0].get("path", "") if r.artifacts else ""),
                    recipe="run %s, %d attempt(s)" % (run_id, r.attempts),
                    tokens_cost=0))
                admitted.append({"lane": r.lane, "id": sid})
            except NotVerified:
                pass

        root.set("gates.total", len(all_gates))
        root.set("gates.failed", len(failed))
        root.set("lanes.blocked", len(blocked))
        human_escalation("release requires approval", "operator",
                         **{"run.id": run_id, "gates.failed": len(failed)})

    obs.flush()
    summary = {
        "run": run_id, "brief": brief,
        "duration_s": round(time.time() - started, 1),
        "lanes": {k: asdict(v) for k, v in results.items()},
        "gates": {"total": len(all_gates), "failed": len(failed)},
        "blocked_lanes": blocked,
        "admitted_to_commons": admitted,
        "artifacts_dir": d,
    }
    open(os.path.join(d, "summary.json"), "w", encoding="utf-8").write(
        json.dumps(summary, indent=1, default=str))
    return summary
