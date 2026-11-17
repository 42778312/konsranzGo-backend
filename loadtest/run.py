#!/usr/bin/env python3
"""Load test of the KonstanzGo live server in Docker: N simulated apps at once, then a report and the server logs.

    python3 loadtest/run.py --users 1000
    python3 loadtest/run.py --users 5000 --seconds 300 --ramp 30

Starts what it needs and stops it again afterwards (keep it with --keep):
- a fake EFA-BW on :9090 (fake_efa.py), so no request ever reaches the real EFA-BW;
- the server container `kg-load` from the image `konstanzgo-live:loadtest`, pointed at the fake.
  The image is built first when it doesn't exist (a few minutes). --rebuild builds it again after code changes.

It refuses a running container that talks to the real EFA-BW.
Each user behaves like the app (users.py): a snapshot on opening and every 15 s, a stop sheet every 20-90 s.
Results are saved to loadtest/results/.
"""
import argparse
import asyncio
import json
import resource
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DB = REPO / "data/timetable.db"
FAKE_PORT = 9090
sys.path.insert(0, str(HERE))
import users  # noqa: E402


def say(text=""):
    print(text, flush=True)


def get(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode()


def alive(url):
    try:
        get(url, timeout=1)
        return True
    except OSError:
        return False


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check).stdout.strip()


def metrics(port):
    found = {}
    for line in get(f"http://127.0.0.1:{port}/metrics").splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            found[name] = float(value)
    return found


def total(values, prefix):
    return sum(v for k, v in values.items() if k.startswith(prefix))


def start_fake():
    if alive(f"http://127.0.0.1:{FAKE_PORT}/count"):
        say(f"• fake EFA-BW already running on :{FAKE_PORT}")
        return None
    say(f"• starting fake EFA-BW on :{FAKE_PORT}")
    process = subprocess.Popen([sys.executable, str(HERE / "fake_efa.py"), "--db", str(DB), "--port", str(FAKE_PORT)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if alive(f"http://127.0.0.1:{FAKE_PORT}/count"):
            return process
        time.sleep(0.2)
    sys.exit("fake EFA-BW did not start")


def start_container(args):
    running = docker("inspect", "-f", "{{.State.Running}}", args.container, check=False) == "true"
    if running and not args.rebuild:
        env = docker("inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", args.container)
        if f":{FAKE_PORT}/" not in env:
            sys.exit(f"'{args.container}' is running against the real EFA-BW. Stop it first: docker rm -f {args.container}")
        say(f"• container {args.container} already running")
        return False
    if args.rebuild or not docker("images", "-q", args.image):
        say(f"• building {args.image} (a few minutes) ...")
        subprocess.run(["docker", "build", "-q", "-f", "Dockerfile", "-t", args.image, "."], cwd=REPO, check=True,
                       stdout=subprocess.DEVNULL)
    docker("rm", "-f", args.container, check=False)
    say(f"• starting container {args.container} on :{args.port}")
    docker("run", "-d", "--name", args.container, "-p", f"127.0.0.1:{args.port}:8080", "--memory", "512m", "--read-only",
           "--tmpfs", "/tmp", "-e", f"UPSTREAM_URL=http://host.docker.internal:{FAKE_PORT}/nvbw/XML_DM_REQUEST",
           args.image)
    return True


def wait_until_live(port):
    say("• waiting for the server's first sweep of the hub stops ...")
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            if json.loads(get(f"http://127.0.0.1:{port}/v1/health")).get("upstream") == "ok":
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    sys.exit("the server did not get live data within 2 min; see: docker logs kg-load")


def row(label, value):
    say(f"  {label:<40}{value}")


def report(summary, before, after, fake_before, fake_after, stats):
    s, st = summary["by_kind"]["snapshot"], summary["by_kind"]["stop"]
    say()
    say("═" * 64)
    say(f"  {summary['users']:,} users for {summary['seconds']} s")
    say("═" * 64)
    say("\nWhat the phones sent to the server")
    row("requests in total", f"{summary['requests']:,}")
    row("average per second", f"{summary['per_second_avg']:,}")
    row("busiest second", f"{summary['per_second_peak']:,}")
    row("failed (no answer or 5xx)", f"{summary['errors']:,}")
    say("\nMap snapshot  (/v1/live/snapshot)")
    row("requests", f"{s['requests']:,}   {s['statuses']}")
    row("answer time p50 / p95 / p99", f"{s['p50_ms']} / {s['p95_ms']} / {s['p99_ms']} ms   (max {s['max_ms']})")
    row("data sent", f"{s['bytes'] / 1024:,.0f} KB")
    say("\nStop sheets  (/v1/stops/<stop>/departures)")
    row("requests", f"{st['requests']:,}   {st['statuses']}")
    row("answer time p50 / p95 / p99", f"{st['p50_ms']} / {st['p95_ms']} / {st['p99_ms']} ms   (max {st['max_ms']})")
    say("    (404 = a Swiss stop the server doesn't cover; the app shows the timetable there)")
    say("\nWhat the server sent to EFA-BW (here the fake)")
    asked = fake_after - fake_before
    row("requests", f"{asked:,}  = {asked / summary['seconds']:.2f} per second (hard limit: 1)")
    row("  of them by the 30 s hub sweep", f"{total(after, 'poll_cycles_total') - total(before, 'poll_cycles_total'):.0f} sweeps")
    row("stop sheets answered without EFA-BW",
        f"{total(after, 'upstream_skipped_total') - total(before, 'upstream_skipped_total'):,.0f} "
        "(from the 20 s cache or the hub sweep)")
    row("requests per phone request", f"{asked / max(summary['requests'], 1):.4f}")
    say("\nServer")
    row("memory / CPU right after", stats)
    row("live trips / match rate", f"{after.get('live_trips', 0):.0f} / {after.get('live_match_rate', 0) * 100:.0f}%")
    say("\nRequests per second over the run")
    timeline = summary["timeline"]
    peak = max(timeline) or 1
    step = max(1, len(timeline) // 30)
    for i in range(0, len(timeline), step):
        value = max(timeline[i:i + step])
        say(f"  {i:>4}s {'█' * max(1 if value else 0, round(value / peak * 40)):<40} {value:,}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--users", type=int, default=1000, help="how many apps at once (default 1000)")
    parser.add_argument("--seconds", type=float, default=120, help="how long they use the app (default 120)")
    parser.add_argument("--ramp", type=float, default=1, help="seconds over which they all open it (default 1)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--container", default="kg-load")
    parser.add_argument("--image", default="konstanzgo-live:loadtest")
    parser.add_argument("--rebuild", action="store_true", help="build the image again first")
    parser.add_argument("--keep", action="store_true", help="leave the container and fake EFA-BW running")
    parser.add_argument("--log-lines", type=int, default=40, help="server log lines to show at the end")
    args = parser.parse_args()

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = min(hard, max(soft, args.users * 2 + 256))
    resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    if want < args.users + 100:
        sys.exit(f"this shell allows only {want} open connections; run `ulimit -n {args.users * 2 + 256}` first")

    fake = start_fake()
    started = start_container(args)
    try:
        wait_until_live(args.port)
        began = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        before, fake_before = metrics(args.port), json.loads(get(f"http://127.0.0.1:{FAKE_PORT}/count"))["requests"]
        say(f"• {args.users:,} users open the app within {args.ramp:g} s and use it for {args.seconds:g} s\n")

        def progress(second, requests):
            print(f"\r  {second:>4}s  {requests:>9,} requests", end="", flush=True)

        summary = await users.simulate(port=args.port, users=args.users, ramp=args.ramp, seconds=args.seconds,
                                       db=str(DB), progress=progress)
        after, fake_after = metrics(args.port), json.loads(get(f"http://127.0.0.1:{FAKE_PORT}/count"))["requests"]
        stats = docker("stats", "--no-stream", "--format", "{{.MemUsage}}  {{.CPUPerc}}", args.container)
        report(summary, before, after, fake_before, fake_after, stats)

        logs = subprocess.run(["docker", "logs", "--since", began, args.container], capture_output=True, text=True)
        lines = (logs.stdout + logs.stderr).splitlines()
        requests = [line for line in lines if "] GET /" in line or "] POST /" in line]
        problems = [line for line in lines if any(level in line for level in ("[ WARNING ]", "[ ERROR ]", "[ CRITICAL ]"))]
        other = [line for line in lines if line not in requests and line not in problems]
        say(f"\nServer logs during the run: {len(lines):,} lines")
        say("─" * 64)
        row("request lines", f"{len(requests):,}")
        row("warnings and errors", f"{len(problems):,}")
        row("other (poller, fallbacks)", f"{len(other):,}")
        if problems:
            say("\n  Warnings and errors:")
            for line in problems[-args.log_lines:]:
                say("    " + line)
        if other:
            say(f"\n  Other lines (last {args.log_lines}):")
            for line in other[-args.log_lines:]:
                say("    " + line)
        say("\n  Last requests:")
        for line in requests[-5:]:
            say("    " + line)
        say(f"\n  All of it: docker logs {args.container}")

        out = HERE / "results" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.users}users.json"
        out.parent.mkdir(exist_ok=True)
        summary.update(upstream_requests=fake_after - fake_before, server=stats)
        out.write_text(json.dumps(summary, indent=1))
        say(f"\nSaved: {out.relative_to(REPO)}")
    finally:
        if not args.keep:
            if started:
                docker("rm", "-f", args.container, check=False)
            if fake:
                fake.terminate()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit("\nstopped")
