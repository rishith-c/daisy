"""
The Garden index — verified solutions, shared as a git repository.

The commons (commons/) is what this machine knows. Garden is what everyone
knows. The difference is not scale, it is review: a solution leaving one
machine for a shared index has to be reviewable by a person who did not run it,
so Garden is a git repo and publishing is a pull request.

That choice buys four things a hosted service would have to rebuild:

    a review gate      a PR is the human-in-the-loop, already understood
    attribution        commits carry an author; nobody has to be trusted
    distribution       fork it, clone it, run it offline, no API key
    auth               `gh` is already signed in

An entry is a directory under solutions/, holding the manifest, the
verification report, and the artifact. The manifest is the searchable part; it
carries the gate signature, so a lookup can ask "has anyone fixed THIS gate"
rather than "does anyone have something with similar words".

    python3 -m garden.cli search --gate physics.bend
    python3 -m garden.cli publish --id <commons id>

Zero third-party dependencies.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CLONE = os.path.join(ROOT, ".garden")
SOLUTIONS = "solutions"
DEFAULT_REMOTE = os.environ.get("GARDEN_REPO", "")   # e.g. rishith-c/garden
API = os.environ.get("GARDEN_API", "https://garden-taupe-three.vercel.app")


def api_search(gates: list, base: str = None, timeout: float = 6.0) -> list[dict]:
    """Ask Garden's own API for solutions to a gate.

    Deliberately not the git host's API. Garden serves pre-sharded static JSON
    with no key and no rate limit; the alternative throttles anonymous callers
    at 60 requests an hour, which is a ceiling on exactly the traffic this is
    for. One request per gate, ~1.6 KB, CDN-cached.

    A 404 is a real answer and returns []: nobody has published a verified fix
    for that gate. It is not an error to retry, and treating it as one would
    turn "this is new work" into "the network is flaky".
    """
    base = (base or API).rstrip("/")
    out, seen = [], set()
    for g in gates or []:
        url = "%s/api/v1/gate/%s.json" % (base, urllib.parse.quote(g, safe=""))
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            continue
        except Exception:
            continue          # offline is not the same as absent; caller falls back
        for s in data.get("solutions", []):
            if s.get("id") in seen:
                continue
            seen.add(s.get("id"))
            out.append(s)
    return out


def clone_path() -> str:
    return os.environ.get("GARDEN_CLONE", DEFAULT_CLONE)


def slug(text: str, n: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n] or "solution").rstrip("-")


def ensure_local(path: str = None) -> str:
    """A Garden that exists locally even before a remote does.

    Publishing to a repo that has not been created yet should not be a hard
    error — the local index is useful on its own, and the PR is a later step.
    """
    path = path or clone_path()
    os.makedirs(os.path.join(path, SOLUTIONS), exist_ok=True)
    if not os.path.isdir(os.path.join(path, ".git")):
        subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True)
        readme = os.path.join(path, "README.md")
        if not os.path.exists(readme):
            open(readme, "w", encoding="utf-8").write(
                "# Garden\n\nVerified solutions, shared between coding agents.\n\n"
                "Every entry here passed every gate that certified it — the gate\n"
                "signature is in each manifest. Nothing is admitted on similarity\n"
                "or popularity.\n\n## Looking something up\n\n"
                "```bash\npython3 -m garden.cli search --gate physics.bend\n```\n")
            subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", "Garden: initial index"],
                           cwd=path, capture_output=True)
    return path


def entries(path: str = None) -> list[dict]:
    path = path or clone_path()
    base = os.path.join(path, SOLUTIONS)
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        mf = os.path.join(base, name, "manifest.json")
        if not os.path.exists(mf):
            continue
        try:
            d = json.load(open(mf, encoding="utf-8"))
        except ValueError:
            continue
        d["_dir"] = os.path.join(base, name)
        d["_slug"] = name
        out.append(d)
    return out


def search(gates: list = None, text: str = "", kind: str = None,
           path: str = None, limit: int = 10) -> list[dict]:
    """Look up by gate signature first, text second.

    Same ordering as the commons and for the same measured reason: an agent
    that just watched physics.bend go red is asking a far more specific
    question than its prose suggests, and the gate name is the specific part.
    """
    want = set(gates or [])
    words = set(w for w in re.split(r"\W+", (text or "").lower()) if len(w) > 3)
    hits = []
    for e in entries(path):
        if kind and e.get("kind") != kind:
            continue
        have = set(g.split("=")[0] for g in (e.get("verified", {})
                                             .get("gate_signature", "").split("|")) if g)
        cover = (len(want & have) / len(want)) if want else 0.0
        blob = (e.get("title", "") + " " + e.get("recipe", "")).lower()
        lex = (len(words & set(re.split(r"\W+", blob))) / len(words)) if words else 0.0
        score = 0.72 * cover + 0.28 * lex if want else lex
        if score <= 0:
            continue
        hits.append(dict(e, score=round(score, 4), matched_gates=sorted(want & have)))
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


def git(path: str, *args, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=path, capture_output=True,
                          text=True, timeout=60, check=check)


def has_remote(path: str = None) -> str:
    path = path or clone_path()
    r = git(path, "remote", "get-url", "origin")
    return r.stdout.strip() if r.returncode == 0 else ""
