# KonstanzGo Live — backend

The live-delay server of the [KonstanzGo](https://github.com/42778312/KonstanzGo) iOS app. It's the only thing that
asks EFA-BW for real-time data. It sweeps a handful of hub stops at most once per second, matches what it sees to the
timetable, and serves one small shared snapshot that every phone (and Cloudflare's cache) reads. However many people
use the app, EFA-BW sees one polite client.

```
EFA-BW  ◄──  this server (≤ 1 request/s, one instance)  ◄──  Cloudflare cache  ◄──  phones
```

- **Swift 6.2 + Vapor 4**, one container, ~110 MB image, ~30 MB RAM.
- **No accounts, no personal data, no IPs logged.** The app keeps "Data Not Collected".
- **Offline-first app:** if this server is down, the app shows the timetable. Nothing breaks.

## API (v1)

| Endpoint | What | Cache |
|---|---|---|
| `GET /v1/live/snapshot` | Delays and cancellations of every running city bus, ~450 bytes gzipped. `ETag` / `304`. | 5 s client, 10 s edge |
| `GET /v1/stops/{gid}/departures?limit=20` | One stop area (`de:08335:11918`), live where known. `404` for an unknown stop. | 20 s edge |
| `GET /v1/health` | `status`, `upstream` (`ok` / `degraded` / `off`), `feedVersion`, `snapshotAgeSeconds`. | none |
| `GET /metrics` | Prometheus text. Refused through Cloudflare; optional bearer token. | none |

The payload types live in [`KGLiveAPI/`](KGLiveAPI), a Foundation-only package that the app keeps an identical copy
of (`Packages/KGLiveAPI` in the app repo). **Change it here first**, then copy it to the app, and check with
`scripts/check-api.sh`. Fields are only ever added to v1; anything else is `/v2/`.

## Layout

```
Sources/LiveCore     the engine: timetable, EFA-BW client, rate limit + circuit breaker, matching, snapshot
Sources/LiveServer   Vapor routes and wiring          Sources/Run   entry point + Docker health check
Tests/               recorded EFA-BW responses only; tests never call EFA-BW
KGLiveAPI/           the shared API package
data/timetable.db    the timetable baked into the image; must be the same feed as the app's
deploy/              docker-compose, .env.example, the server and Cloudflare runbook
loadtest/            N simulated apps against the container, with a fake EFA-BW
scripts/             fixture recording, timetable update, API check
docs/SERVER_PLAN.md  the build plan and the decisions behind it
```

## Run it

```bash
# Tests (macOS or Linux with libsqlite3-dev and zlib1g-dev)
swift test
swift test --package-path KGLiveAPI

# Locally in Docker; this one does call EFA-BW, at most once per second
docker compose -f deploy/docker-compose.yml up --build
curl -s http://127.0.0.1:8080/v1/health

# Load test: 1000 apps at once against a fake EFA-BW, then a report and the server logs
python3 loadtest/run.py --users 1000
```

In the app, Debug builds point at `http://localhost:8080`. The switch is in More → "Live-Server (Test, ab Neustart)".

## Configuration

Environment variables only (see [`deploy/.env.example`](deploy/.env.example)):

- **`LIVE_UPSTREAM_ENABLED`** — the kill switch. Set it to `false` and no request leaves within one cycle.
- **`UPSTREAM_MAX_RPS`** — clamped to at most 1.
- **`POLL_INTERVAL_SECONDS`** — between sweeps; at least 10.
- **`HUB_STOPS`** — a fixed list of stops to poll instead of the computed one.
- **`CONTACT_EMAIL`** — goes in the User-Agent, so NVBW can reach you instead of blocking you.
- **`METRICS_TOKEN`**, **`LOG_LEVEL`**, **`PORT`** and **`TIMETABLE_PATH`**.
- **`UPSTREAM_URL`** — exists only for the load test's fake. Never set it in production.

## Release

1. Push to `main`. CI runs the tests on Linux, builds the image and smoke-tests it with the upstream switched off.
2. Tag `vX.Y.Z` and push the tag. CI publishes `ghcr.io/42778312/konstanzgo-live:vX.Y.Z` and `:latest`.
3. On the server, set `LIVE_TAG=vX.Y.Z` in `.env`, then run `docker compose --profile tunnel up -d`. To roll back, set
   the previous tag. Full runbook: [`deploy/README.md`](deploy/README.md).

## Before going live (owner's checklist)

- [ ] Tell NVBW about the server and its User-Agent; set `CONTACT_EMAIL`.
- [ ] VM in Germany (2 vCPU / 4 GB), Docker, SSH keys only, no inbound ports.
- [ ] Domain on Cloudflare, Tunnel to `http://live:8080`, cache rule on `/v1/*`, rate limit of 120 requests/min per
      IP, block `/metrics`, request logs off.
- [ ] First tag `v1.0.0`. Make the ghcr package public, or `docker login ghcr.io` on the VM.
- [ ] Uptime check on `/v1/health` every minute, with an alert when `snapshotAgeSeconds` is above 120 during service
      hours.
- [ ] App release builds: set `KG_LIVE_URL=https://api.<domain>` and, after a TestFlight week, `KG_LIVE_ENABLED=YES`.
- [ ] **The timetable ends 12.12.2026.** Rebuild it in the app repo, run `scripts/update-timetable.sh`, and release the
      app and the server together.

## Ground rules

At most 1 request per second to EFA-BW, one instance only, and never two servers. Delays older than 3 minutes are
dropped. Never key anything on EFA's numeric ids. LiveCore never imports Vapor. Tests never call EFA-BW. No
microservices, no Kubernetes, no accounts.

Timetable data: NVBW GmbH, Datenlizenz Deutschland – Namensnennung – Version 2.0. Real-time data: EFA-BW.
KonstanzGo is not an official app of the Stadtwerke Konstanz or NVBW.
