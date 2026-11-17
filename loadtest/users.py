#!/usr/bin/env python3
"""Simulates many KonstanzGo apps using the live server at once, the way the app does (LiveModel, S10):

- Opening the app: one snapshot, and half the users open a stop sheet right away.
- On the map: the snapshot every 15 s, with If-None-Match, so an unchanged one costs a 304.
- Now and then (every 20-90 s): a stop sheet, `/v1/stops/<gid>/departures?limit=40`.

Every user has its own connection, like a phone. Prints a summary and writes every request to --out.

    python3 loadtest/users.py --users 1000 --ramp 1 --seconds 120 --db data/timetable.db
"""
import argparse
import asyncio
import json
import random
import sqlite3
import time

SNAPSHOT_EVERY = 15


class Connection:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.reader = self.writer = None

    async def get(self, path, headers):
        for attempt in (0, 1):
            try:
                if self.writer is None:
                    self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                lines = [f"GET {path} HTTP/1.1", f"Host: {self.host}", "Accept: application/json",
                         "Accept-Encoding: gzip", "User-Agent: KonstanzGo-loadtest"]
                lines += [f"{k}: {v}" for k, v in headers.items()]
                self.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
                await self.writer.drain()
                head = await self.reader.readuntil(b"\r\n\r\n")
                status = int(head.split(b" ", 2)[1])
                fields = {}
                for line in head.decode("latin-1").split("\r\n")[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        fields[k.strip().lower()] = v.strip()
                size = int(fields.get("content-length", "0"))
                if size:
                    await self.reader.readexactly(size)
                return status, fields, size
            except (ConnectionError, asyncio.IncompleteReadError, OSError):
                self.close()
                if attempt:
                    raise

    def close(self):
        if self.writer:
            self.writer.close()
        self.reader = self.writer = None


async def user(index, args, stops, log, start):
    await asyncio.sleep(random.uniform(0, args.ramp))
    conn = Connection(args.host, args.port)
    etag = None
    end = start + args.seconds

    async def ask(kind, path, headers=None):
        t0 = time.monotonic()
        try:
            status, fields, size = await conn.get(path, headers or {})
        except Exception as error:  # noqa: BLE001
            log.append((t0 - start, kind, 0, (time.monotonic() - t0) * 1000, 0, type(error).__name__))
            return None
        log.append((t0 - start, kind, status, (time.monotonic() - t0) * 1000, size, ""))
        return fields

    def stop_path():
        return f"/v1/stops/{random.choice(stops)}/departures?limit=40"

    # Opening the app.
    fields = await ask("snapshot", "/v1/live/snapshot")
    etag = fields.get("etag") if fields else None
    if random.random() < 0.5:
        await ask("stop", stop_path())
    next_snapshot = time.monotonic() + SNAPSHOT_EVERY
    next_stop = time.monotonic() + random.uniform(20, 90)
    while True:
        now = time.monotonic()
        wake = min(next_snapshot, next_stop)
        if wake >= end:
            break
        await asyncio.sleep(max(0, wake - now))
        if wake == next_snapshot:
            fields = await ask("snapshot", "/v1/live/snapshot", {"If-None-Match": etag} if etag else {})
            if fields and fields.get("etag"):
                etag = fields["etag"]
            next_snapshot += SNAPSHOT_EVERY
        else:
            await ask("stop", stop_path())
            next_stop += random.uniform(20, 90)
    conn.close()


def pct(values, p):
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))]


async def simulate(host="127.0.0.1", port=8080, users=1000, ramp=1.0, seconds=120.0, db="", progress=None):
    """Runs the users and returns the summary. `progress(second, requests_so_far)` is called once a second."""
    args = argparse.Namespace(host=host, port=port, users=users, ramp=ramp, seconds=seconds)
    conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
    stops = sorted({":".join(row[0].split(":")[:3]) for row in conn.execute("SELECT gtfs_id FROM platform")})
    conn.close()

    log = []
    start = time.monotonic()

    async def ticker():
        while True:
            await asyncio.sleep(1)
            if progress:
                progress(int(time.monotonic() - start), len(log))

    tick = asyncio.create_task(ticker())
    await asyncio.gather(*(user(i, args, stops, log, start) for i in range(users)))
    tick.cancel()
    took = time.monotonic() - start

    per_second = {}
    for t, *_ in log:
        per_second[int(t)] = per_second.get(int(t), 0) + 1
    summary = {"users": users, "seconds": round(took, 1), "requests": len(log),
               "per_second_avg": round(len(log) / took, 1), "per_second_peak": max(per_second.values() or [0]),
               "by_kind": {}}
    for kind in ("snapshot", "stop"):
        rows = [r for r in log if r[1] == kind]
        statuses = {}
        for r in rows:
            statuses[str(r[2] or r[5])] = statuses.get(str(r[2] or r[5]), 0) + 1
        ok = [r[3] for r in rows if r[2]]
        summary["by_kind"][kind] = {"requests": len(rows), "statuses": statuses,
                                    "p50_ms": round(pct(ok, 50), 1), "p95_ms": round(pct(ok, 95), 1),
                                    "p99_ms": round(pct(ok, 99), 1), "max_ms": round(max(ok or [0]), 1),
                                    "bytes": sum(r[4] for r in rows)}
    summary["errors"] = sum(1 for r in log if not r[2] or r[2] >= 500)
    summary["timeline"] = [per_second.get(s, 0) for s in range(int(took) + 1)]
    return summary


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--ramp", type=float, default=1, help="seconds over which the users open the app")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--db", required=True, help="the timetable, for the list of stops")
    parser.add_argument("--out", default="loadtest-result.json")
    args = parser.parse_args()
    summary = await simulate(args.host, args.port, args.users, args.ramp, args.seconds, args.db)
    with open(args.out, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "timeline"}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
