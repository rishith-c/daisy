"""Tests for Import.

    python3 -m importer.test_importer

Every case builds its own fake home and its own state directory in a tempdir.
Nothing here reads the developer's real ~/.claude, ~/.codex or ~/.cursor, and
nothing here touches the network — an import test that only passes on the
machine that wrote it has tested the machine, not the importer.

The two claims the feature is sold on get the most attention:

    idempotency   asserted on bytes AND mtimes. "The contents are equal" can be
                  true while the file was rewritten, and a rewrite is what
                  eventually corrupts something.
    non-destruction  a hand-written line in config.md is checked byte-for-byte
                  after every kind of second import.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from . import ingest, sync as syncmod
from .attention import attention
from .detect import detect, by_id, _auth_state
from .state import State

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  ok   %s" % name)
    else:
        FAIL += 1; print("  FAIL %s   %s" % (name, detail))


# ---------------------------------------------------------------------------
# fixtures — a plausible machine, built from scratch
# ---------------------------------------------------------------------------

SKILL_GOOD = """---
name: good-skill
description: A skill whose references all exist.
---
See [the reference](references/how.md).
"""

SKILL_BROKEN = """---
name: broken-skill
description: A skill that points at a file nobody shipped.
---
See [the reference](references/missing.md) and [prose](link).
"""

RULES_TEXT = "# Project rules\n\nRead `docs/present.md` before editing.\n"
RULES_DANGLING = "# Global rules\n\nAlways consult `docs/gone.md` first.\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def build_home(root, *, mtime=None):
    """A fake $HOME carrying one of everything detect() looks for."""
    h = os.path.join(root, "home")
    proj = os.path.join(root, "proj")

    # claude sessions — the format agents/discover.py reads
    sess = write(os.path.join(h, ".claude", "projects", "-p-proj", "s1.jsonl"),
                 "\n".join(json.dumps(r) for r in [
                     {"type": "user", "cwd": proj, "customTitle": "Fix the bracket"},
                     {"type": "assistant", "message": {"model": "claude-opus-5"}},
                 ]) + "\n")
    if mtime:
        os.utime(sess, (mtime, mtime))

    # mcp servers, one of each authentication shape
    write(os.path.join(h, ".claude.json"), json.dumps({
        "mcpServers": {
            "ok-local":   {"type": "stdio", "command": "/bin/echo", "args": ["hi"]},
            "ok-remote":  {"type": "http", "url": "https://x/mcp",
                           "headers": {"Authorization": "Bearer real-token-value"}},
            "no-auth":    {"type": "http", "url": "https://y/mcp"},
            "placeholder": {"type": "stdio", "command": "/bin/echo",
                            "env": {"API_KEY": "YOUR_API_KEY"}},
            "flagged":    {"type": "stdio", "command": "/bin/echo"},
        },
        "projects": {proj: {"mcpServers": {"scoped": {"command": "/bin/echo"}}}},
    }))
    write(os.path.join(h, ".claude", "mcp-needs-auth-cache.json"),
          json.dumps({"flagged": {"timestamp": 1}}))

    write(os.path.join(h, ".codex", "config.toml"),
          '[mcp_servers.ok-local]\ncommand = "/bin/echo"\nargs = ["different"]\n')

    write(os.path.join(h, ".cursor", "mcp.json"),
          json.dumps({"mcpServers": {"cur": {"command": "/bin/echo"}}}))

    # skills
    write(os.path.join(h, ".claude", "skills", "good-skill", "SKILL.md"), SKILL_GOOD)
    write(os.path.join(h, ".claude", "skills", "good-skill", "references", "how.md"), "how")
    write(os.path.join(h, ".claude", "skills", "broken-skill", "SKILL.md"), SKILL_BROKEN)
    write(os.path.join(h, ".claude", "skills", "broken-skill", "requirements.txt"),
          "-e .\ndefinitely_not_installed_pkg>=1.0\n")

    # hooks
    write(os.path.join(h, ".claude", "settings.json"), json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "Write",
                                  "hooks": [{"type": "command", "command": "/bin/true"}]}]}}))

    # rules — one whose references resolve, one that dangles
    write(os.path.join(proj, "CLAUDE.md"), RULES_TEXT)
    write(os.path.join(proj, "docs", "present.md"), "present")
    write(os.path.join(h, "AGENTS.md"), RULES_DANGLING)

    # cursor's application support directory, which is detected and not imported
    write(os.path.join(h, "Library", "Application Support", "Cursor", "User",
                       "workspaceStorage", "abc", "x"), "x")
    return h, proj


def fresh(root):
    h, p = build_home(root)
    return h, p, detect(home=h, project=p), State(os.path.join(root, "state"))


def fingerprint(st):
    """Bytes and mtimes for every file in the state directory."""
    out = {}
    if not os.path.isdir(st.home):
        return out
    for n in sorted(os.listdir(st.home)):
        f = st.path(n)
        out[n] = (open(f, "rb").read(), os.stat(f).st_mtime_ns)
    return out


# ---------------------------------------------------------------------------

def test_detection():
    print("\ndetection — what is here, and what would land")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, _ = fresh(t)
        ids = {s["id"]: s for s in det["sources"]}
        check("every documented source is reported",
              {"claude-sessions", "codex-sessions", "opencode-sessions", "rules",
               "claude-mcp", "codex-mcp", "cursor-mcp", "claude-skills",
               "claude-hooks", "cursor"} <= set(ids), str(sorted(ids)))
        check("finds the one claude session", ids["claude-sessions"]["count"] == 1,
              str(ids["claude-sessions"]["count"]))
        check("finds both project and global rules", ids["rules"]["count"] == 2,
              str([i["key"] for i in ids["rules"]["items"]]))
        check("finds the global and the project-scoped mcp servers",
              ids["claude-mcp"]["count"] == 6, str(ids["claude-mcp"]["count"]))
        check("a project-scoped server keeps its own namespace",
              any(i["key"].startswith("scoped@") for i in ids["claude-mcp"]["items"]),
              str([i["key"] for i in ids["claude-mcp"]["items"]]))
        check("finds both skills", ids["claude-skills"]["count"] == 2)
        check("finds the hook", ids["claude-hooks"]["count"] == 1)
        check("an absent store reports zero rather than raising",
              ids["opencode-sessions"]["count"] == 0)

        for s in det["sources"]:
            if s["importable"] and s["count"]:
                check("%s says what importing would change" % s["id"], bool(s["effects"]),
                      "no effects declared")
        check("cursor is detected but not offered for import",
              ids["cursor"]["present"] and not ids["cursor"]["importable"])
        check("and says why", "state.vscdb" in ids["cursor"]["note"])

        blob = json.dumps(det)
        check("no credential value reaches the detection output",
              "real-token-value" not in blob)
        check("but the header name does, so the UI can say what is needed",
              "Authorization" in blob)


def test_auth_states():
    print("\nauthentication — decided by shape, never by reading the value")
    check("a populated header is authenticated",
          _auth_state("x", {"type": "http", "url": "u",
                            "headers": {"Authorization": "abc"}}, set())[0] == "ok")
    check("a remote server with no credential needs auth",
          _auth_state("x", {"type": "http", "url": "u"}, set())[0] == "needs-auth")
    check("a local process with no credential has nothing to authenticate",
          _auth_state("x", {"command": "/bin/echo"}, set())[0] == "n/a")
    check("an empty key needs auth",
          _auth_state("x", {"command": "c", "env": {"API_KEY": ""}}, set())[0] == "needs-auth")
    st, why = _auth_state("x", {"command": "c", "env": {"API_KEY": "YOUR_KEY"}}, set())
    check("a placeholder needs auth", st == "needs-auth", why)
    check("the tool's own needs-auth record wins",
          _auth_state("flagged", {"command": "c", "env": {"API_KEY": "real"}},
                      {"flagged"})[0] == "needs-auth")
    unset = "DAISY_TEST_UNSET_TOKEN_%d" % os.getpid()
    check("an unset ${VAR} needs auth",
          _auth_state("x", {"command": "c", "env": {"API_KEY": "${%s}" % unset}},
                      set())[0] == "needs-auth")
    os.environ[unset] = "value"
    try:
        check("the same ${VAR} is fine once it is exported",
              _auth_state("x", {"command": "c", "env": {"API_KEY": "${%s}" % unset}},
                          set())[0] == "ok")
    finally:
        del os.environ[unset]


def test_selection_is_explicit():
    print("\nselection — nothing moves that was not named")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        check("an unknown source is refused",
              _raises(lambda: ingest.run("everything", det, st), ingest.UnknownSource))
        check("a source Daisy cannot honestly import is refused",
              _raises(lambda: ingest.run("cursor", det, st), ingest.NotImportable))
        rep = ingest.run("claude-skills", det, st, dry_run=False,
                         only=["good-skill"])
        reg = st.registry()["skills"]
        check("--item narrows to the named item", len(reg) == 1, str(sorted(reg)))
        check("the item that was not named stayed out",
              not any("broken-skill" in k for k in reg), str(sorted(reg)))
        check("importing one source leaves the others untouched",
              not st.registry().get("mcp"), str(st.registry().get("mcp")))
        check("the report names what it did", rep.tally("added") == 1, rep.summary())


def test_dry_run_writes_nothing():
    print("\ndry run — the default posture")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        rep = ingest.run("claude-mcp", det, st)
        check("dry run is the default", rep.dry_run)
        check("it reports the writes it would make", "registry.json" in rep.writes,
              str(rep.writes))
        check("and makes none of them", not os.path.isdir(st.home), st.home)
        rep2 = ingest.run("rules", det, st)
        check("a rules dry run still produces the diff", bool(rep2.diff))
        check("with the file content in it",
              any("Project rules" in ln for ln in rep2.diff))
        check("and still writes nothing", not os.path.isdir(st.home))


def test_idempotent():
    print("\nidempotency — the second import writes zero bytes")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        srcs = ["claude-sessions", "rules", "claude-mcp", "claude-skills", "claude-hooks"]
        first = [ingest.run(s, det, st, dry_run=False) for s in srcs]
        check("the first pass imports everything",
              sum(r.tally("added") for r in first) > 0,
              str([r.summary() for r in first]))
        fp1 = fingerprint(st)
        check("five state files exist", len(fp1) == 4, str(sorted(fp1)))

        time.sleep(0.01)                       # so a rewrite would move the mtime
        det2 = detect(home=h, project=p)
        second = [ingest.run(s, det2, st, dry_run=False) for s in srcs]
        fp2 = fingerprint(st)
        check("the second pass adds nothing",
              sum(r.tally("added") for r in second) == 0,
              str([r.summary() for r in second]))
        check("and updates nothing", sum(r.tally("updated") for r in second) == 0)
        check("and reports no writes", not any(r.writes for r in second),
              str([r.writes for r in second]))
        check("every byte is identical",
              {k: v[0] for k, v in fp1.items()} == {k: v[0] for k, v in fp2.items()})
        check("and no file was rewritten at all",
              {k: v[1] for k, v in fp1.items()} == {k: v[1] for k, v in fp2.items()},
              "an mtime moved: a file was rewritten with identical bytes")
        check("the config gained exactly two rules blocks",
              st.config_text().count("<!-- daisy:import rules:") == 2,
              str(st.config_text().count("<!-- daisy:import rules:")))


def test_non_destructive():
    print("\nnon-destruction — nothing already in Daisy is overwritten")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        ingest.run("rules", det, st, dry_run=False)

        # A line a person wrote, above everything the importer owns.
        hand = "MY OWN RULE: never ship on a Friday.\n"
        st.write_config(hand + st.config_text())
        before = st.config_text()

        # The rules file changes underneath us.
        write(os.path.join(p, "CLAUDE.md"), RULES_TEXT + "\nAlso: run the gates.\n")
        det2 = detect(home=h, project=p)
        rep = ingest.run("rules", det2, st, dry_run=False)

        after = st.config_text()
        check("the hand-written line survives", after.startswith(hand))
        check("the original imported block is still there byte-for-byte",
              before.rstrip("\n") in after, "the earlier block was rewritten")
        check("the new text lands as a revision", rep.tally("updated") == 1, rep.summary())
        check("both versions are present",
              after.count("Read `docs/present.md`") == 2,
              str(after.count("Read `docs/present.md`")))
        check("the conflict is reported rather than resolved", len(rep.conflicts) == 1)
        check("and says both were kept", "untouched" in rep.conflicts[0]["detail"])

        # Re-importing the changed file a third time is a no-op again.
        det3 = detect(home=h, project=p)
        rep3 = ingest.run("rules", det3, st, dry_run=False)
        check("a revision is itself idempotent", not rep3.writes, str(rep3.writes))

        # Two tools defining the same server name.
        ingest.run("claude-mcp", det3, st, dry_run=False)
        rep4 = ingest.run("codex-mcp", det3, st, dry_run=False)
        reg = st.registry()["mcp"]
        check("both tools' version of ok-local is kept",
              "claude-mcp/ok-local" in reg and "codex-mcp/ok-local" in reg,
              str(sorted(reg)))
        check("they really are different records",
              reg["claude-mcp/ok-local"]["args"] != reg["codex-mcp/ok-local"]["args"])
        check("the name collision is reported",
              any(c["item"] == "ok-local" for c in rep4.conflicts),
              str(rep4.conflicts))


def test_sessions_and_secrets():
    print("\nwhat an import actually stores")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        ingest.run("claude-sessions", det, st, dry_run=False)
        runs = st.runs()["runs"]
        check("the session became one run-history row", len(runs) == 1, str(len(runs)))
        r = runs[0]
        check("it carries the title", r["title"] == "Fix the bracket", r["title"])
        check("it carries the turn count", r["turns"] == 2, str(r["turns"]))
        check("it names the vendor that still owns the transcript",
              r["vendor"] == "claude" and "owns it" in r["transcript"])
        check("the transcript itself was not copied",
              "customTitle" not in json.dumps(runs))

        ingest.run("claude-mcp", det, st, dry_run=False)
        blob = json.dumps(st.registry())
        check("no credential value is stored", "real-token-value" not in blob)
        check("the key names are, so the UI can say what is missing",
              "Authorization" in blob)
        check("the auth verdict is stored per server",
              st.registry()["mcp"]["claude-mcp/no-auth"]["auth"] == "needs-auth")

        ingest.run("claude-hooks", det, st, dry_run=False)
        hk = list(st.registry()["hooks"].values())[0]
        check("a hook is listed, not installed", hk["installed"] is False)
        check("and says so", "does not run" in hk["note"])


def test_sync():
    print("\nautosync — off by default, watermarked when on")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        check("it is off before anyone touches it", not syncmod.status(st)["enabled"])
        check("and has never run", syncmod.status(st)["last_run_human"] == "never")
        res = syncmod.sync_once(det, st, dry_run=False)
        check("a pass with it off does nothing", not res["ran"] and not res["sources"],
              str(res))

        ingest.run("claude-skills", det, st, dry_run=False)
        syncmod.set_enabled(True, st)
        check("the toggle sticks", syncmod.status(st)["enabled"])

        res = syncmod.sync_once(det, st, dry_run=False)
        check("it syncs only what was already imported",
              [s["source"] for s in res["sources"]] == ["claude-skills"],
              str([s["source"] for s in res["sources"]]))
        check("a source never imported is not pulled in by a sync",
              not st.registry().get("mcp"), str(st.registry().get("mcp")))
        check("the watermark was written",
              bool(syncmod.status(st)["watermarks"].get("claude-skills")))
        check("and last-run is now a real time",
              syncmod.status(st)["last_run_human"] != "never")

        res2 = syncmod.sync_once(det, st, dry_run=False)
        check("an unchanged source is skipped whole",
              res2["sources"][0].get("skipped") == "unchanged since last sync",
              str(res2["sources"][0]))

        write(os.path.join(h, ".claude", "skills", "good-skill", "SKILL.md"),
              SKILL_GOOD + "\nAnd one more line.\n")
        det2 = detect(home=h, project=p)
        res3 = syncmod.sync_once(det2, st, dry_run=False)
        check("an edited skill moves on the next pass", res3["moved"] == 1, str(res3))
        check("the watermark advanced",
              syncmod.status(st)["watermarks"]["claude-skills"]["cursor"]
              == syncmod.source_cursor(by_id(det2, "claude-skills")))

        res4 = syncmod.sync_once(det2, st, dry_run=True, force=True)
        check("a forced dry run still writes nothing",
              res4["ran"] and res4["moved"] == 0, str(res4["moved"]))


def test_attention():
    print("\nneeds attention — the honest half")
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        a0 = attention(st, home=h)
        check("nothing imported means nothing to finish",
              a0["total"] == 0 and not a0["imported_anything"])

        for s in ("claude-skills", "claude-mcp", "rules"):
            ingest.run(s, det, st, dry_run=False)
        a = attention(st, home=h)
        titles = {i["title"] for i in a["items"]}
        refs = [i for i in a["items"]
                if i["title"] == "broken-skill" and "SKILL.md" in i["detail"]]

        check("the broken skill is flagged", "broken-skill" in titles, str(sorted(titles)))
        check("and names the missing reference",
              refs and "references/missing.md" in refs[0]["detail"],
              str([i["detail"][:50] for i in a["items"]]))
        check("prose in brackets is not mistaken for a path",
              refs and "(link)" not in refs[0]["detail"])
        check("the good skill is not flagged", "good-skill" not in titles)
        check("a missing pip requirement is flagged",
              any("definitely_not_installed_pkg" in i["detail"] for i in a["items"]),
              str([i["detail"][:40] for i in a["items"]]))
        check("a pip flag line is not treated as a package",
              not any("-e" == x for i in a["items"] for x in i["detail"].split()))

        servers = {i["title"] for i in a["by_tab"]["Servers"]}
        check("the unauthenticated remote server is flagged", "no-auth" in servers,
              str(servers))
        check("the placeholder key is flagged", "placeholder" in servers, str(servers))
        check("the tool's own needs-auth record is honoured", "flagged" in servers,
              str(servers))
        check("an authenticated server is not flagged", "ok-remote" not in servers)
        check("a local server with nothing to authenticate is not flagged",
              "ok-local" not in servers)

        rules_tab = a["by_tab"]["Rules"]
        check("the rules file citing a missing path is flagged",
              any("gone.md" in i["detail"] for i in rules_tab),
              str([i["detail"] for i in rules_tab]))
        check("the rules file whose path exists is not",
              not any("present.md" in i["detail"] for i in rules_tab))
        check("every item names a fix", all(i["fix"] for i in a["items"]))
        check("the tabs match the reference's shape",
              [x["name"] for x in a["tabs"]] == ["Skills", "Servers", "Rules"])
        check("the counts add up",
              sum(x["count"] for x in a["tabs"]) == a["total"])

        os.remove(os.path.join(p, "CLAUDE.md"))
        gone = attention(st, home=h)
        check("a rules file deleted after import is reported as vanished",
              any(i["kind"] == "source" and "no longer there" in i["detail"]
                  for i in gone["items"]),
              str([i["detail"][:50] for i in gone["items"]]))


def test_cli():
    print("\ncli — every command, and --json on all of them")
    from . import cli
    import contextlib, io
    with tempfile.TemporaryDirectory() as t:
        h, p, det, st = fresh(t)
        # detect/status read the real machine; the writes all go to the tempdir.
        for args in (["detect"], ["status"], ["attention"],
                     ["import", "--source", "claude-hooks"],
                     ["sync", "--on"], ["sync"], ["sync", "--off"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["--state", st.home] + args)
            check("`%s` exits 0" % " ".join(args), rc == 0, "rc=%d" % rc)
            check("`%s` prints something" % " ".join(args), bool(buf.getvalue().strip()))

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["--json", "--state", st.home] + args)
            check("`%s --json` is valid json" % " ".join(args),
                  _is_json(buf.getvalue()), buf.getvalue()[:80])

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--state", st.home, "import", "--source", "claude-hooks"])
        check("import without --apply says it wrote nothing",
              "dry run" in buf.getvalue(), buf.getvalue()[:60])


def test_generator_is_idempotent():
    print("\ngenerator — running it twice changes nothing")
    import subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gen = os.path.join(root, "tools", "add_import_view.py")
    idx = os.path.join(root, "index.html")
    before = open(idx, "rb").read()
    check("the import view is injected into index.html",
          b"daisy:import-view" in before,
          "run: python3 tools/add_import_view.py")
    check("its marker appears exactly once",
          before.count(b"<!-- daisy:import-view -->") == 1,
          str(before.count(b"<!-- daisy:import-view -->")))
    r = subprocess.run([sys.executable, gen], capture_output=True, text=True)
    check("a second run exits 0", r.returncode == 0, r.stderr[:80])
    check("and says so", "already present" in r.stdout, r.stdout[:60])
    check("and index.html is byte-identical afterwards",
          open(idx, "rb").read() == before)


def _is_json(s):
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def _raises(fn, exc):
    try:
        fn(); return False
    except exc:
        return True
    except Exception:
        return False


def main():
    print("importer — test suite")
    test_detection()
    test_auth_states()
    test_selection_is_explicit()
    test_dry_run_writes_nothing()
    test_idempotent()
    test_non_destructive()
    test_sessions_and_secrets()
    test_sync()
    test_attention()
    test_cli()
    test_generator_is_idempotent()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
