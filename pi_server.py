#!/usr/bin/env python3
"""
StreetFlood – Raspberry Pi Local API Server
Runs alongside the sensor script.
Exposes:  GET http://0.0.0.0:5000/status
          GET http://0.0.0.0:5000/history   (last 60 readings)

Run with:  python3 pi_server.py
"""

import time
import threading
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

from gpiozero import DistanceSensor
from supabase import create_client, Client

# ═══════════════════════ CONFIGURATION ═══════════════════════
TRIG_PIN          = 18
ECHO_PIN          = 24
SENSOR_HEIGHT_CM  = 19
MOCK_TEMP_VAL     = 0
SAMPLE_INTERVAL   = 2        # seconds between readings
HISTORY_MAX       = 60       # readings to keep in memory
API_PORT          = 5000

SUPABASE_URL      = ""
SUPABASE_KEY      = ""
FUNCTION_NAME     = "insert_water_reading"
DEVICE_SECRET     = "RASPBERRY_SECERT-PASS-555"
PING_HOST         = "8.8.8.8"   # host used for latency check
# ═════════════════════════════════════════════════════════════

# ── Shared state (written by sensor thread, read by HTTP thread) ──
state = {
    "level_cm":        0,
    "gap_cm":          0,
    "temperature":     MOCK_TEMP_VAL,
    "sensor_id":       "raspberry_pi_1",
    "timestamp":       None,
    "db_ok":           False,      # last Supabase push succeeded
    "db_last_ok":      None,       # ISO timestamp of last successful push
    "ping_ms":         None,       # None = no network
    "readings":        [],         # history list
}
state_lock = threading.Lock()

# ── Supabase & sensor setup ────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None
sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=4)


def ping_ms(host=PING_HOST):
    """Return round-trip latency in ms, or None if unreachable."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True, text=True, timeout=3
        )
        for line in result.stdout.splitlines():
            if "time=" in line:
                ms = float(line.split("time=")[1].split()[0])
                return round(ms, 1)
    except Exception:
        pass
    return None


def measure():
    distances = []
    for _ in range(5):
        d = sensor.distance * 100
        if d > 0:
            distances.append(d)
        time.sleep(0.1)
    if not distances:
        return None, None
    avg = sum(distances) / len(distances)
    level = max(0, SENSOR_HEIGHT_CM - avg)
    return round(level), round(avg, 2)


def push_supabase(level_cm):
    if not supabase:
        return False
    try:
        supabase.rpc(FUNCTION_NAME, {
            "p_device_key":  DEVICE_SECRET,
            "p_level":       int(level_cm),
            "p_temperature": int(MOCK_TEMP_VAL),
            "p_sensor_id":   "raspberry_pi_1",
        }).execute()
        return True
    except Exception as e:
        print(f"[DB] Push failed: {e}")
        return False


def sensor_loop():
    """Background thread: measure → push → update state."""
    while True:
        now = datetime.now()
        level, gap = measure()

        if level is None:
            print("[SENSOR] No valid reading")
            time.sleep(SAMPLE_INTERVAL)
            continue

        db_ok = push_supabase(level)
        latency = ping_ms()

        reading = {
            "ts":    now.strftime("%H:%M:%S"),
            "level": level,
            "gap":   gap,
        }

        with state_lock:
            state["level_cm"]    = level
            state["gap_cm"]      = gap
            state["timestamp"]   = now.isoformat()
            state["ping_ms"]     = latency
            state["db_ok"]       = db_ok
            if db_ok:
                state["db_last_ok"] = now.isoformat()
            state["readings"].append(reading)
            if len(state["readings"]) > HISTORY_MAX:
                state["readings"].pop(0)

        print(f"[{now.strftime('%H:%M:%S')}] Level: {level} cm | Gap: {gap} cm | "
              f"Ping: {latency} ms | DB: {'OK' if db_ok else 'FAIL'}")

        time.sleep(SAMPLE_INTERVAL)


# ── HTTP server ────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass   # suppress default access log

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")   # allow dashboard on any origin
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            with state_lock:
                self.send_json({
                    "level_cm":    state["level_cm"],
                    "gap_cm":      state["gap_cm"],
                    "temperature": state["temperature"],
                    "sensor_id":   state["sensor_id"],
                    "timestamp":   state["timestamp"],
                    "db_ok":       state["db_ok"],
                    "db_last_ok":  state["db_last_ok"],
                    "ping_ms":     state["ping_ms"],
                })

        elif self.path == "/history":
            with state_lock:
                self.send_json({"readings": list(state["readings"])})

        else:
            self.send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


def main():
    # Start sensor loop in background
    t = threading.Thread(target=sensor_loop, daemon=True)
    t.start()

    # Start HTTP server (blocking)
    server = HTTPServer(("0.0.0.0", API_PORT), Handler)
    print(f"[API] Listening on http://0.0.0.0:{API_PORT}")
    print(f"[API] Endpoints: /status  /history")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
