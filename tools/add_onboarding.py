#!/usr/bin/env python3
"""Inject the first-run experience into index.html from onboarding.html.

WHY THREE SCREENS AND NOT FIVE
------------------------------
Apple's HIG asks for a flow that is "fast, fun, and optional", says to teach
through interactivity rather than instruction, and says that if someone skips
it on first launch you must not show it again. The usability literature on
multi-slide walkthroughs is blunter: people hunt for Skip the moment a carousel
appears, and a tour that describes features without letting anyone touch them
reads as an instruction manual. So a screen only earns its place if it *does*
something a later screen cannot undo cheaply.

Five things had to land. They collapse into four short screens:

  1. what Daisy is  +  gates are on      -> one screen. The honest one-sentence
                                            answer to "what is this" IS the gate
                                            claim, so stating it twice would be
                                            padding. The three gates are shown
                                            as named files with the real bug
                                            each one caught.
  2. which agents this machine can drive -> one screen. The only screen that
                                            does real work: the answer differs
                                            per machine and changes what Daisy
                                            can run, so it cannot be deferred
                                            into Settings without the first run
                                            failing halfway.
  3. account / publishing choice         -> one screen. Daisy stays complete
                                            without an account. Garden pairing
                                            leaves the app for the trusted system
                                            browser and returns only a scoped
                                            device credential.
  4. appearance + first run              -> one screen. The only preference is
                                            already at its safe default, so
                                            Return starts the first run.

Rejected: a five-panel carousel; a coach-mark tour over the live UI (untimely —
nothing has been run yet); a mandatory account; any request
for permission Daisy does not yet need; and faking green agent ticks in a
browser that cannot open a shell.

HOW THIS WORKS
--------------
onboarding.html is the source of truth. It carries three delimited sections —
css, html, js — which are sliced out and inserted at four stable anchors in
index.html. index.html is never edited by hand.

    python3 tools/add_onboarding.py          # inject (idempotent)
    python3 tools/add_onboarding.py --demo   # print the re-show command

Running it twice is a no-op. Pass ``--replace`` to refresh the three generated
blocks after changing onboarding.html.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "index.html")
SRC = os.path.join(ROOT, "onboarding.html")

GUARD = "daisy:onboarding"

# A first run you can only see once is untestable and undemoable, so there are
# four ways back in. This is the one that needs no state to be cleared first.
DEMO_CMD = 'open "file://%s?onboarding=1"' % IDX

# The command-palette entry, so the flow is reachable from inside the app too.
PALETTE_ANCHOR = "    { l: 'Copy daisy-theme-v1 JSON', h: '', f: copyTheme },"
PALETTE_LINE = ("    { l: 'Show onboarding', h: '⇧⌘O', "
                "f: function () { if (window.daisyShowOnboarding) window.daisyShowOnboarding(); } },")


def section(src, name):
    """Slice one <!-- daisy:onboarding:NAME --> ... <!-- /... --> block."""
    m = re.search(r"<!--\s*daisy:onboarding:%s\s*-->\n(.*?)\n<!--\s*/daisy:onboarding:%s\s*-->"
                  % (name, name), src, re.S)
    if not m:
        raise SystemExit("onboarding.html: section %r not found" % name)
    return m.group(1).rstrip("\n")


def replace_existing(html, css, markup, js):
    """Refresh only the three generated blocks in an already-injected app."""
    replacements = (
        (r'<style>\n/\* ={20,}\n   daisy:onboarding — first run.*?</style>', css, "css"),
        (r'<div class="obv" id="obv".*?</div>\s*(?=<div class="toast")',
         markup + "\n\n", "markup"),
        (r'<script>\n/\* daisy:onboarding — first run controller\..*?</script>'
         r'(?=\n\n<script>\n/\* ---------------- import view)', js, "script"),
    )
    for pattern, block, name in replacements:
        # A replacement function keeps JavaScript escapes such as ``\b``
        # literal. Passing the block directly makes re.sub interpret them.
        html, count = re.subn(pattern, lambda _match, value=block: value,
                              html, count=1, flags=re.S)
        if count != 1:
            raise SystemExit("existing onboarding %s block was not found exactly once" % name)
    return html


def main(argv):
    if "--demo" in argv:
        print(DEMO_CMD)
        return 0

    with open(IDX, encoding="utf-8") as fh:
        h = fh.read()
    if GUARD in h:
        if "--replace" not in argv:
            print("already present")
            return 0
        with open(SRC, encoding="utf-8") as fh:
            src = fh.read()
        css, html, js = (section(src, n) for n in ("css", "html", "js"))
        h = replace_existing(h, css, html, js)
        with open(IDX, "w", encoding="utf-8") as fh:
            fh.write(h)
        print("onboarding replaced")
        return 0

    with open(SRC, encoding="utf-8") as fh:
        src = fh.read()
    css, html, js = (section(src, n) for n in ("css", "html", "js"))

    # Validate every anchor before writing anything, so a partial injection is
    # not a state this script can produce.
    for anchor, what in (("</style>", "app stylesheet"),
                         ("</main>", "main element"),
                         ("</script>", "app script"),
                         (PALETTE_ANCHOR, "command palette")):
        n = h.count(anchor)
        if n != 1:
            raise SystemExit("anchor %r for the %s appears %d times, expected 1" % (anchor, what, n))

    # Order matters: each inserted block contains the closing tag of a *later*
    # anchor, never of an earlier one, so every count stays 1 when it is used.
    h = h.replace("</style>", "</style>\n\n" + css, 1)
    h = h.replace("</main>", "</main>\n\n" + html, 1)
    h = h.replace(PALETTE_ANCHOR, PALETTE_ANCHOR + "\n" + PALETTE_LINE, 1)
    h = h.replace("</script>", "</script>\n\n" + js, 1)

    with open(IDX, "w", encoding="utf-8") as fh:
        fh.write(h)
    print("onboarding injected — %d lines of css, %d of markup, %d of script"
          % (css.count("\n") + 1, html.count("\n") + 1, js.count("\n") + 1))
    print("re-show it with:  %s" % DEMO_CMD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
