"""
Who is in the room — and whether there is a room at all.

Duo's only claim is that two frontier models changed each other's minds. The
claim is worth nothing unless two of them actually ran, so the most important
thing this module does is refuse. If one participant cannot be driven, there is
no Duo run: not a degraded one, not a solo one wearing the same document
format. A one-model spec that comes out of the same template as a two-model
spec is indistinguishable from it on the page, and that is precisely the lie
this feature exists to avoid telling.

Availability is a probe, never a config value. lab/executors.py already owns
process handling and the per-CLI failure diagnosis; this module decides who to
probe and what the answer means.

    IS      participant selection, one usability probe each, and a single
            honest verdict on whether a Duo is possible
    IS NOT  a scheduler, a router, a fallback, a retry loop, or a place that
            repairs credentials. A stale login is fixed by a human running the
            CLI once, and pretending otherwise wastes ten minutes per run.

Zero third-party dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab import executors

DEFAULT_PAIR = ("claude", "codex")

# What each CLI's own configuration currently selects. Recorded so the
# transcript names something, not pinned: Duo passes no model flag, so
# asserting a model id it did not set would be a guess dressed as a fact.
CONFIGURED_MODEL = {
    "claude": "claude-opus-5",
    "codex": "gpt-5.6-sol",
    "opencode": "per-session",
}


@dataclass
class Participant:
    name: str
    model: str = ""
    ok: bool = False
    detail: str = ""
    probe_ms: float = 0.0
    executor: object = None

    def label(self) -> str:
        return "%s (%s)" % (self.name, self.model) if self.model else self.name


@dataclass
class Pairing:
    """Two candidates and one verdict. `ok` is the only thing callers may act on."""
    participants: list = field(default_factory=list)
    ok: bool = False
    why: str = ""

    def names(self) -> list:
        return [p.name for p in self.participants]

    def usable(self) -> list:
        return [p for p in self.participants if p.ok]

    def blocked(self) -> list:
        return [p for p in self.participants if not p.ok]


def parse_models(spec: str) -> list:
    """'claude,codex' -> ['claude', 'codex']. Order is preserved and meaningful."""
    return [s.strip() for s in (spec or "").split(",") if s.strip()]


def select(names=DEFAULT_PAIR, cwd: str = None, prober=None) -> Pairing:
    """Probe the requested pair and say plainly whether a Duo can happen.

    `prober` exists so the test suite can drive this without spawning a CLI;
    the default is the real probe in lab/executors.py.
    """
    names = list(names)
    probe = prober or executors.available

    if len(names) != 2:
        return Pairing([Participant(n, CONFIGURED_MODEL.get(n, "")) for n in names],
                       False,
                       "a Duo is exactly two participants, %d requested (%s)"
                       % (len(names), ", ".join(names) or "none"))
    if names[0] == names[1]:
        return Pairing([Participant(names[0], CONFIGURED_MODEL.get(names[0], ""))],
                       False,
                       "%s cannot critique itself — a model shown its own draft "
                       "edits it, which is the anchoring this protocol exists to "
                       "prevent" % names[0])

    probed = {ex.name: ex for ex in probe(names, cwd=cwd)}
    people = []
    for n in names:
        ex = probed.get(n)
        if ex is None:
            people.append(Participant(n, CONFIGURED_MODEL.get(n, ""), False,
                                      "no executor named %r on this machine" % n))
            continue
        people.append(Participant(n, CONFIGURED_MODEL.get(n, ""), ex.ok, ex.detail,
                                  getattr(ex, "probe_ms", 0.0), ex))

    dead = [p for p in people if not p.ok]
    if not dead:
        return Pairing(people, True, "")
    return Pairing(people, False,
                   "not a Duo — " + "; ".join("%s: %s" % (p.name, p.detail) for p in dead)
                   + ". Refusing to run one model and call the result a collaboration.")
