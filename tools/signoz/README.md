# Self-hosted SigNoz

SigNoz is Apache-2.0 and runs locally, so no cloud account and no ingestion key
is needed. Our exporter already speaks plain OTLP/HTTP to a keyless endpoint —
verified against a stand-in receiver: it posts to `/v1/traces`, `/v1/metrics`
and `/v1/logs` and sends no `signoz-ingestion-key` header when none is set.

## What it needs

Docker. It is not installed on this machine, and Docker Desktop is a ~1 GB
download — worth knowing before starting it on venue wifi.

```bash
brew install --cask docker && open -a Docker      # then wait for the whale icon
```

## Start it

```bash
git clone -b main https://github.com/SigNoz/signoz.git /tmp/signoz
cd /tmp/signoz/deploy/docker && docker compose up -d
```

First start pulls several images and takes a few minutes. When it is up:

- UI            http://localhost:3301
- OTLP/HTTP     http://localhost:4318   <- the one we post to
- OTLP/gRPC     http://localhost:4317

## Point Daisy at it

```bash
echo 'SIGNOZ_ENDPOINT=http://localhost:4318' >> .env.local
./tools/withenv.sh python3 -m obs.cli selftest      # prints the trace id
./tools/withenv.sh python3 -m obs.cli replay        # ships everything spooled
```

`replay` matters: every run so far spooled to `obs/spool/*.jsonl` rather than
being dropped, so the moment an endpoint exists the whole history goes up. The
offline path was built as a first-class path for exactly this.

Then in the UI, search the trace id from `selftest`, or open Traces and look
for service `daisy`.

## If Docker is not worth it today

The spool is not a degraded mode. `python3 -m obs.cli tail` renders the same
spans locally, and `tools/traced_verify.py` prints the tree. Nothing about the
instrumentation changes when the endpoint appears — only where it lands.
