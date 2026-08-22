"""
Duo from a terminal.

    python3 -m duo.cli run --brief "..." [--models claude,codex] [--rounds 4]
                           [--out spec.md] [--timeout 420] [--json]

The exit code is the answer, not the prose:

    0  four rounds ran, the spec was written, the gate is green
    2  not a Duo — a participant could not be driven, so nothing ran
    3  partial — a round failed, or --rounds stopped short of synthesis
    4  the spec was produced and duo.spec_complete rejected it

    IS      argument handling, progress on stderr, one JSON payload that the
            Duo view in index.html consumes verbatim
    IS NOT  a daemon, a queue, or a place that retries a failed round. It runs
            the protocol once and reports what happened.

Zero third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import models as duo_models
from . import rounds as duo_rounds
from . import spec as duo_spec
from .metrics import collaboration, summary


def _payload(pairing, tr, metrics, sp, findings) -> dict:
    return {
        "brief": tr.brief,
        "ok": bool(sp) and not findings and not tr.partial,
        "participants": [{"name": p.name, "model": p.model, "ok": p.ok,
                          "detail": p.detail, "probe_ms": p.probe_ms}
                         for p in pairing.participants],
        "synthesiser": tr.synthesiser,
        "rounds": {"requested": tr.rounds_requested, "completed": tr.rounds_completed},
        "turns": [{"round": t.round, "role": t.role, "author": t.author,
                   "subject": t.subject, "ok": t.ok, "ms": t.ms,
                   "why": t.why, "text": t.text} for t in tr.turns],
        "drafts": tr.drafts,
        "revised": tr.revised,
        "critiques": [{"n": c.n, "kind": c.kind, "point": c.point, "why": c.why,
                       "by": c.by, "against": c.against, "verdict": c.verdict,
                       "response": c.response} for c in tr.critiques],
        "metrics": metrics,
        "spec": ({"markdown": sp.render(), "sections": sp.sections,
                  "restored": len(sp.restored)} if sp else None),
        "gate": {"name": "duo.spec_complete", "ok": bool(sp) and not findings,
                 "findings": [{"check": f.check, "detail": f.detail} for f in findings]},
        "partial": tr.partial,
        "notes": tr.notes,
    }


def run(brief: str, model_spec: str = "claude,codex", rounds: int = 4,
        out: str = "", timeout: int = duo_rounds.ROUND_TIMEOUT, as_json: bool = False,
        cwd: str = None, runner=None, prober=None, log=None) -> tuple:
    """Returns (exit_code, payload). `runner`/`prober` are the test seams."""
    say = log if log is not None else (lambda s: print(s, file=sys.stderr))

    pairing = duo_models.select(duo_models.parse_models(model_spec), cwd=cwd, prober=prober)
    for p in pairing.participants:
        say("  %-9s %-16s %s" % (p.name, p.model, "ok" if p.ok else "UNUSABLE — " + p.detail))
    if not pairing.ok:
        payload = {"ok": False, "why": pairing.why, "brief": brief,
                   "participants": [{"name": p.name, "model": p.model, "ok": p.ok,
                                     "detail": p.detail, "probe_ms": p.probe_ms}
                                    for p in pairing.participants],
                   "spec": None, "metrics": None,
                   "gate": {"name": "duo.spec_complete", "ok": False, "findings": []}}
        say("\n" + pairing.why)
        return 2, payload

    tr = duo_rounds.run_duo(pairing, brief, rounds=rounds, timeout=timeout,
                            cwd=cwd, runner=runner, log=say)
    metrics = collaboration(tr)
    say("\n" + summary(metrics))

    sp, findings = None, []
    if tr.synthesis:
        sp = duo_spec.build(tr.synthesis, brief, tr.participants, tr.synthesiser, metrics)
        findings = sp.gate()
        say("\n" + duo_spec.report(findings, out or "(stdout)"))
        if out:
            open(out, "w", encoding="utf-8").write(sp.render())
            say("wrote %s" % out)

    for n in tr.notes:
        say("  note      %s" % n)

    payload = _payload(pairing, tr, metrics, sp, findings)
    if as_json:
        print(json.dumps(payload, indent=2))
    elif sp and not out:
        print(sp.render())

    if sp is None:
        if rounds < 4 and not tr.partial:
            say("\n--rounds %d stops before synthesis, so there is no spec." % rounds)
        return 3, payload
    if findings:
        return 4, payload
    return (3 if tr.partial else 0), payload


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="duo.cli", description=__doc__.strip().split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the four-round protocol against one brief")
    r.add_argument("--brief", required=True, help="the design question both models answer")
    r.add_argument("--models", default="claude,codex",
                   help="exactly two, comma separated (default: claude,codex)")
    r.add_argument("--rounds", type=int, default=4, choices=(1, 2, 3, 4),
                   help="stop after this round; 4 is the whole protocol")
    r.add_argument("--out", default="", help="write the spec to this file")
    r.add_argument("--timeout", type=int, default=duo_rounds.ROUND_TIMEOUT,
                   help="seconds allowed per model call")
    r.add_argument("--cwd", default=None, help="directory to run the CLIs in")
    r.add_argument("--json", action="store_true", help="emit the whole transcript as JSON")
    a = ap.parse_args(argv)

    code, _ = run(a.brief, a.models, a.rounds, a.out, a.timeout, a.json, a.cwd)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
