#!/usr/bin/env python3
"""A stand-in for EFA-BW's departure monitor, for load tests only. Never calls the real EFA-BW.

Answers `XML_DM_REQUEST?name_dm=<stop gid>` in rapidJSON with the next hour of departures from the bundled
timetable, each with a random delay of 0-5 min, so the server matches and serves them like real ones.
Counts every request it gets: GET /count returns {"requests": N}.

    python3 loadtest/fake_efa.py --db data/timetable.db --port 9090
"""
import argparse
import json
import random
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
lock = threading.Lock()
count = 0
db_path = ""


def departures(gid, limit):
    now = datetime.now(BERLIN)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute = now.hour * 60 + now.minute
    db = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    rows = db.execute(
        """SELECT p.gtfs_id, r.short_name, t.headsign, st.departure, r.is_city
           FROM stop_time st JOIN platform p ON p.id = st.platform_id JOIN trip t ON t.id = st.trip_id
           JOIN route r ON r.id = t.route_id JOIN service_date sd ON sd.service_id = t.service_id
           WHERE (p.gtfs_id = ? OR p.gtfs_id LIKE ? || ':%') AND sd.date = ? AND st.departure BETWEEN ? AND ?
           ORDER BY st.departure LIMIT ?""",
        (gid, gid, int(now.strftime("%Y%m%d")), minute - 5, minute + 60, limit),
    ).fetchall()
    db.close()
    events = []
    for platform, line, headsign, dep, is_city in rows:
        planned = midnight + timedelta(minutes=dep)
        estimated = planned + timedelta(seconds=random.choice([0, 0, 60, 120, 180, 300]))
        iso = lambda d: d.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        events.append({
            "realtimeStatus": ["MONITORED"],
            "location": {"id": platform, "parent": {"id": gid}},
            "departureTimePlanned": iso(planned),
            "departureTimeEstimated": iso(estimated),
            "transportation": {"number": line, "product": {"class": 5 if is_city else 6},
                               "destination": {"name": headsign}},
        })
    return {"version": "fake", "stopEvents": events}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global count
        url = urlparse(self.path)
        if url.path == "/count":
            body = json.dumps({"requests": count}).encode()
        else:
            with lock:
                count += 1
            query = parse_qs(url.query)
            gid = query.get("name_dm", [""])[0]
            body = json.dumps(departures(gid, int(query.get("limit", ["40"])[0]))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    db_path = args.db
    print(f"fake EFA-BW on :{args.port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
