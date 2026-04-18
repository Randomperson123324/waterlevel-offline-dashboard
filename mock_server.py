#!/usr/bin/env python3
"""
StreetFlood – Mock API Server (for testing dashboard without real Pi)

Simulates water level cycling:  Safe → Warning → Danger → back to Safe
Also simulates ping latency and occasional DB failures.

SETUP (run once):
    python3 -m venv venv
    source venv/bin/activate

RUN:
    source venv/bin/activate
    python3 mock_server.py

Then open streetflood-dashboard.html in your browser.
"""

import time
import random
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

API_PORT = 5000

# ── Simulated state ────────────────────────────────────────────
state = {
    "level_cm":    0,
    "gap_cm":      0,
    "temperature": 28,
    "sensor_id":   "mock_sensor",
    "timestamp":   None,
    "db_ok":       True,
    "db_last_ok":  None,
    "ping_ms":     12.4,
    "readings":    [],
}
state_lock = threading.Lock()

def input_loop():
    print("\n  Enter values when prompted. Press Enter to keep current value.")
    print("  Type 'q' at any prompt to quit.\n")

    while True:
        # ── Water level ───────────────────────────────────────
        with state_lock:
            current_lvl = state["level_cm"]
        try:
            raw = input(f"  Water level cm [{current_lvl}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if raw.lower() == 'q':
            break
        if raw == '':
            lvl = current_lvl
        else:
            try:
                lvl = max(0, int(raw))
            except ValueError:
                print("  ✗ Enter a number. Try again.\n")
                continue

        # ── DB status ─────────────────────────────────────────
        with state_lock:
            current_db = state["db_ok"]
        try:
            raw2 = input(f"  DB ok? (y/n) [{'y' if current_db else 'n'}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if raw2 == 'q':
            break
        if raw2 == '':
            db_ok = current_db
        elif raw2 in ('y', 'yes', '1', 'true'):
            db_ok = True
        elif raw2 in ('n', 'no', '0', 'false'):
            db_ok = False
        else:
            print("  ✗ Enter y or n. Try again.\n")
            continue

        # ── Update state ──────────────────────────────────────
        now  = datetime.now()
        gap  = max(0, 19 - lvl)
        ping = round(random.gauss(18, 4), 1)
        ping = max(1, ping)
        level_label = "SAFE" if lvl <= 30 else "WARNING" if lvl <= 60 else "DANGER"

        reading = {"ts": now.strftime("%H:%M:%S"), "level": lvl, "gap": gap}

        with state_lock:
            state["level_cm"]  = lvl
            state["gap_cm"]    = gap
            state["timestamp"] = now.isoformat()
            state["ping_ms"]   = ping
            state["db_ok"]     = db_ok
            if db_ok:
                state["db_last_ok"] = now.isoformat()
            state["readings"].append(reading)
            if len(state["readings"]) > 60:
                state["readings"].pop(0)

        print(f"  ✓ [{now.strftime('%H:%M:%S')}] Level: {lvl:3d} cm  {level_label:<8}  "
              f"Ping: {ping:.1f} ms  DB: {'OK' if db_ok else 'FAIL'}\n")


# ── HTTP handler ───────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            with state_lock:
                self.send_json(dict(state, readings=None))   # exclude history from /status
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
    server = HTTPServer(("0.0.0.0", API_PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    print("=" * 50)
    print(f"  StreetFlood Mock API running")
    print(f"  http://localhost:{API_PORT}/status")
    print(f"  http://localhost:{API_PORT}/history")
    print("=" * 50)

    try:
        input_loop()
    except KeyboardInterrupt:
        pass

    print("Stopped.")
    server.shutdown()


if __name__ == "__main__":
    main()