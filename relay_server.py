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

STOP_NORTH = "N10N"  # uptown / toward 96 St
STOP_SOUTH = "N10S"  # downtown / toward Manhattan-Coney Island
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
