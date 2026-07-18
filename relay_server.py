"""
NYC MTA Q Train Departure Relay Server
----------------------------------------
Run this on a Raspberry Pi, spare computer, or small cloud host — NOT on
the MatrixPortal M4 itself. The M4 doesn't have enough RAM (or a real
protobuf library) to decode MTA's GTFS-realtime feed directly, so this
server does the heavy lifting and exposes plain JSON that the M4 can
fetch over plain HTTP.

Q trains run on the MTA's "NQRW" realtime feed. 86 St (Second Ave
Subway) has parent stop id N10:
    N10N = northbound platform (toward 96 St)
    N10S = southbound platform (toward Manhattan / Coney Island)

Local install/run:
    pip install nyct-gtfs flask gunicorn
    python relay_server.py
    -> http://localhost:5000/departures

For free cloud hosting (e.g. Render), see README.md — this file is
already set up to work there via gunicorn and the PORT env var.
"""

import os
import time
from flask import Flask, jsonify
from nyct_gtfs import NYCTFeed

app = Flask(__name__)

STOP_NORTH = "Q04N"  # uptown / toward 96 St
STOP_SOUTH = "Q04S"  # downtown / toward Manhattan-Coney Island
MAX_RESULTS = 4


def get_departures():
    # "N" pulls the whole NQRW feed; the Q rides along on it.
    feed = NYCTFeed("N")
    q_trains = feed.filter_trips(line_id="Q")

    now = time.time()
    north, south = [], []

    for train in q_trains:
        for stu in train.stop_time_updates:
            if stu.arrival is None:
                continue
            mins = int((stu.arrival.timestamp() - now) / 60)
            if mins < 0:
                continue
            if stu.stop_id == STOP_NORTH:
                north.append(mins)
            elif stu.stop_id == STOP_SOUTH:
                south.append(mins)

    north.sort()
    south.sort()

    return {
        "north": north[:MAX_RESULTS],
        "south": south[:MAX_RESULTS],
        "updated": int(now),
    }


@app.route("/departures")
def departures():
    try:
        return jsonify(get_departures())
    except Exception as e:  # keep the sign alive even if MTA hiccups
        return jsonify({"error": str(e)}), 500


@app.route("/debug")
def debug():
    """Temporary diagnostic endpoint - shows raw feed data to help
    figure out why departures might be coming back empty. Safe to
    remove once things are working."""
    try:
        feed = NYCTFeed("N")
        q_trains = feed.filter_trips(line_id="Q")

        now_dt = time.time()
        sample = []
        stop_ids_seen = set()

        for train in q_trains[:5]:
            for stu in train.stop_time_updates:
                stop_ids_seen.add(stu.stop_id)
                if stu.stop_id in (STOP_NORTH, STOP_SOUTH):
                    sample.append({
                        "stop_id": stu.stop_id,
                        "stop_name": stu.stop_name,
                        "raw_arrival": str(stu.arrival),
                    })

        return jsonify({
            "server_time_now": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_dt)),
            "server_timezone": time.tzname,
            "feed_last_generated": str(feed.last_generated),
            "num_q_trains_total": len(q_trains),
            "sample_stop_ids_seen": list(stop_ids_seen)[:20],
            "sample_86st_matches": sample,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
