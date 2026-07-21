"""
NYC MTA Q Train + M15 Bus + Weather Relay Server
--------------------------------------------------
Run this on a Raspberry Pi, spare computer, or small cloud host — NOT on
the MatrixPortal M4 itself. The M4 doesn't have enough RAM (or a real
protobuf library) to decode MTA's GTFS-realtime feed directly, so this
server does the heavy lifting and exposes plain JSON that the M4 can
fetch over plain HTTP.

Everything is bundled into ONE endpoint (/departures) on purpose: the
MatrixPortal's WiFi co-processor is known to be flaky with repeated
HTTPS/TLS connections, so the fewer separate requests the board has to
make, the better. One fetch gets subway + bus + weather + clock.

Q trains run on the MTA's "NQRW" realtime feed. 86 St (Second Ave
Subway) has parent stop id Q04:
    Q04N = northbound platform (toward 96 St)
    Q04S = southbound platform (toward Manhattan / Coney Island)

M15 bus data comes from MTA Bus Time's SIRI API, which (unlike the
subway feeds) requires its own free API key:
    1. Register at https://bustime.mta.info/wiki/Developers/Index
       You'll get a key by email, usually within ~30 minutes.
    2. Set it as an environment variable called MTA_BUS_API_KEY on
       wherever you're hosting this (e.g. Render's dashboard -> your
       service -> Environment tab). Do NOT hardcode it in this file.

M15_STOP_ID below is a PLACEHOLDER and almost certainly wrong for your
exact corner -- unlike the subway stop ID, this one couldn't be
verified from official data, so it needs to be looked up manually:
    1. Go to https://bustime.mta.info
    2. Search "2 Av & E 89 St" (or your cross street)
    3. Click the southbound M15 stop marker on the map
    4. The stop code shown (a 6-digit number) is what goes below

Weather comes from the National Weather Service (api.weather.gov),
free, no key required, using the Central Park observation station
(KNYC) -- the nearest official NWS station to the Upper East Side.

Local install/run:
    pip install nyct-gtfs flask gunicorn requests
    python relay_server.py
    -> http://localhost:5000/departures

For free cloud hosting (e.g. Render), see README.md — this file is
already set up to work there via gunicorn and the PORT env var.
"""

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from flask import Flask, jsonify
from nyct_gtfs import NYCTFeed

app = Flask(__name__)

STOP_NORTH = "Q04N"  # uptown / toward 96 St
STOP_SOUTH = "Q04S"  # downtown / toward Manhattan-Coney Island
MAX_RESULTS = 4
NY_TZ = ZoneInfo("America/New_York")

MTA_BUS_API_KEY = os.environ.get("MTA_BUS_API_KEY")
M15_STOP_ID = "401755"  # PLACEHOLDER -- replace with your real stop code, see above
BUS_SIRI_URL = "https://bustime.mta.info/api/siri/stop-monitoring.json"

NWS_STATION = "KNYC"  # Central Park
NWS_USER_AGENT = "mta-departure-sign (replace-with-your-email@example.com)"


def get_subway_times():
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
    return north[:MAX_RESULTS], south[:MAX_RESULTS]


def get_bus_times():
    if not MTA_BUS_API_KEY:
        return []  # not configured yet -- fail quietly, sign just shows N/A

    params = {
        "key": MTA_BUS_API_KEY,
        "MonitoringRef": M15_STOP_ID,
        "LineRef": "MTA NYCT_M15",
    }
    resp = requests.get(BUS_SIRI_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    visits = (
        data.get("Siri", {})
        .get("ServiceDelivery", {})
        .get("StopMonitoringDelivery", [{}])[0]
        .get("MonitoredStopVisit", [])
    )

    now = time.time()
    mins = []
    for visit in visits:
        call = visit.get("MonitoredVehicleJourney", {}).get("MonitoredCall", {})
        arrival_str = call.get("ExpectedArrivalTime") or call.get("ExpectedDepartureTime")
        if not arrival_str:
            continue
        try:
            arrival_dt = datetime.fromisoformat(arrival_str)
        except ValueError:
            continue
        m = int((arrival_dt.timestamp() - now) / 60)
        if m >= 0:
            mins.append(m)

    mins.sort()
    return mins[:MAX_RESULTS]


def get_weather():
    headers = {"User-Agent": NWS_USER_AGENT}
    url = f"https://api.weather.gov/stations/{NWS_STATION}/observations/latest"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    props = resp.json().get("properties", {})

    temp_c = props.get("temperature", {}).get("value")
    temp_f = round(temp_c * 9 / 5 + 32) if temp_c is not None else None
    conditions = props.get("textDescription") or "Unknown"

    return {"temp_f": temp_f, "conditions": conditions}


def get_departures():
    now = time.time()
    local_now = datetime.now(NY_TZ)

    north, south = get_subway_times()

    try:
        m15_south = get_bus_times()
    except Exception as e:
        print("Bus fetch failed:", e)
        m15_south = []

    try:
        weather = get_weather()
    except Exception as e:
        print("Weather fetch failed:", e)
        weather = {"temp_f": None, "conditions": "Unknown"}

    return {
        "north": north,
        "south": south,
        "m15_south": m15_south,
        "weather": weather,
        "updated": int(now),
        "hour": local_now.hour,       # 24-hour, 0-23, America/New_York
        "minute": local_now.minute,
        "second": local_now.second,
    }


@app.route("/departures")
def departures():
    try:
        return jsonify(get_departures())
    except Exception as e:  # keep the sign alive even if MTA hiccups
        return jsonify({"error": str(e)}), 500


@app.route("/debug-bus")
def debug_bus():
    """Temporary diagnostic endpoint - shows the raw MTA Bus Time
    response to help figure out why m15_south might be empty. Safe to
    remove once things are working."""
    if not MTA_BUS_API_KEY:
        return jsonify({"error": "MTA_BUS_API_KEY is not set"}), 500

    params = {
        "key": MTA_BUS_API_KEY,
        "MonitoringRef": M15_STOP_ID,
        "LineRef": "MTA NYCT_M15",
    }
    try:
        resp = requests.get(BUS_SIRI_URL, params=params, timeout=10)
        return jsonify({
            "stop_id_used": M15_STOP_ID,
            "http_status": resp.status_code,
            "raw_response": resp.json(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
