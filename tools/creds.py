#!/usr/bin/env python3
"""
Which credentials are present, and whether they actually work.

Reports presence and a fingerprint, never a value. A tool that prints a secret
to prove the secret is set has defeated the reason the secret was a secret, and
this output is the kind that ends up in a terminal on a projector.

The distinction it draws is the useful one: *configured* and *working* are not
the same, and a dashboard that conflates them sends you debugging the wrong
half. A key can be present and revoked; an endpoint can be reachable and
rejecting.

    python3 tools/creds.py
    ./tools/withenv.sh python3 tools/creds.py     # with .env.local loaded
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

SPEC = [
    ("Bright Data", ["BRIGHTDATA_API_KEY", "BRD_API_KEY"],
     "https://api.brightdata.com/status", "bearer"),
    ("Port", ["PORT_CLIENT_ID"], None, None),
    ("Port secret", ["PORT_CLIENT_SECRET"], None, None),
    ("SigNoz", ["SIGNOZ_INGESTION_KEY"], None, None),
    ("SigNoz endpoint", ["SIGNOZ_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"], None, None),
]


def fingerprint(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:8]


def probe_bright(key: str) -> str:
    req = urllib.request.Request("https://api.brightdata.com/status",
                                 headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return "live (%d)" % r.status
    except urllib.error.HTTPError as e:
        return "rejected (%d)" % e.code
    except Exception as exc:
        return "unreachable: %s" % str(exc)[:40]


def probe_port(cid: str, secret: str) -> str:
    body = json.dumps({"clientId": cid, "clientSecret": secret}).encode()
    req = urllib.request.Request("https://api.getport.io/v1/auth/access_token",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
            return "live — token %s" % ("received" if d.get("accessToken") else "missing")
    except urllib.error.HTTPError as e:
        return "rejected (%d)" % e.code
    except Exception as exc:
        return "unreachable: %s" % str(exc)[:40]


def main() -> int:
    print("%-18s %-9s %-10s %s" % ("service", "present", "fp", "status"))
    print("-" * 62)
    got = {}
    for name, keys, _, _ in SPEC:
        val = ""
        for k in keys:
            if os.environ.get(k):
                val = os.environ[k]; break
        got[name] = val
        print("%-18s %-9s %-10s %s" % (name, "yes" if val else "no",
                                       fingerprint(val) if val else "-", ""))
    print()
    if got.get("Bright Data"):
        print("  Bright Data  ->", probe_bright(got["Bright Data"]))
    if got.get("Port") and got.get("Port secret"):
        print("  Port         ->", probe_port(got["Port"], got["Port secret"]))
    if not any(got.values()):
        print("  nothing configured — everything runs offline and says so")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
