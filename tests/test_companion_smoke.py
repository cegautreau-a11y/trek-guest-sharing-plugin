import http.server
import json
import os
import pathlib
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRIP = "TESTTRIP_TOKEN_abcdefghijklmnopqrstuvwxyz012345"
JOURNEY = "TESTJOURNEY_TOKEN_abcdefghijklmnopqrstuvwxyz"

TRIP_PAYLOAD = {
    "trip": {"id": 1, "title": "Test Trip"},
    "permissions": {"share_bookings": True, "share_map": True},
    "days": [],
    "reservations": [],
    "accommodations": [],
}
JOURNEY_PAYLOAD = {
    "journey": {"title": "Test Trip"},
    "permissions": {"share_gallery": True},
    "gallery": [],
}

class TrekHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.path == f"/api/shared/{TRIP}":
            data = json.dumps(TRIP_PAYLOAD).encode()
        elif self.path == f"/api/public/journey/{JOURNEY}":
            data = json.dumps(JOURNEY_PAYLOAD).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

class ReuseServer(socketserver.TCPServer):
    allow_reuse_address = True

class CompanionSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="trek-gp-test-"))
        cls.cache = cls.tmp / "cache"; cls.cache.mkdir()
        cls.trek = ReuseServer(("127.0.0.1", 0), TrekHandler)
        cls.trek_port = cls.trek.server_address[1]
        threading.Thread(target=cls.trek.serve_forever, daemon=True).start()

        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); cls.port = sock.getsockname()[1]; sock.close()
        env = os.environ.copy()
        env.update({
            "PUBLIC_ROOT": str(ROOT / "companion/public"),
            "PUBLIC_ORIGIN": "https://trek.example.com",
            "COOKIE_PATH": "/guest-portal/",
            "TREK_HOST": "127.0.0.1",
            "TREK_PORT": str(cls.trek_port),
            "LISTEN_HOST": "127.0.0.1",
            "LISTEN_PORT": str(cls.port),
            "GUEST_CACHE_DB": str(cls.cache / "guest-portal.db"),
            "LOG_LEVEL": "DEBUG",
            "IMMICH_URL": "",
            "AERODATABOX_API_KEY": "TEST_PROVIDER_KEY_DO_NOT_USE",
        })
        cls.proc = subprocess.Popen(
            ["python3", str(ROOT / "companion/server/server.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/health", timeout=0.2).read(); break
            except Exception: time.sleep(0.05)
        else:
            raise RuntimeError("companion did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try: cls.output = cls.proc.communicate(timeout=3)[0]
        except subprocess.TimeoutExpired:
            cls.proc.kill(); cls.output = cls.proc.communicate()[0]
        cls.trek.shutdown(); cls.trek.server_close()
        assert TRIP not in cls.output
        assert JOURNEY not in cls.output
        assert "TEST_PROVIDER_KEY_DO_NOT_USE" not in cls.output

    def request(self, path, method="GET", payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        hdrs = dict(headers or {})
        if payload is not None: hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=3) as res:
                return res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_health_minimal(self):
        st, headers, body = self.request("/health")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body), {"ok": True, "version": "1.0.2"})
        self.assertIn("Content-Security-Policy", headers)

    def test_session_origin_and_token_free_api(self):
        payload = {"trip": TRIP, "journey": JOURNEY, "title": "Test Trip"}
        st, _, _ = self.request("/api/session", "POST", payload, {"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, headers, _ = self.request("/api/session", "POST", payload, {"Origin": "https://trek.example.com"})
        self.assertEqual(st, 200)
        cookie = headers.get("Set-Cookie")
        self.assertIn("HttpOnly", cookie); self.assertIn("Secure", cookie); self.assertIn("SameSite=Strict", cookie)
        cookie_pair = cookie.split(";", 1)[0]
        st, _, body = self.request("/api/trip", headers={"Cookie": cookie_pair})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["trip"]["id"], 1)
        st, _, _ = self.request(f"/api/shared/{TRIP}", headers={"Cookie": cookie_pair})
        self.assertEqual(st, 404)

if __name__ == "__main__":
    unittest.main()
