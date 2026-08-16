import http.server
import importlib.util
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
        assert "SHOULD_NOT_LOG_SECRET" not in cls.output

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
        self.assertEqual(json.loads(body), {"ok": True, "version": "1.0.4"})
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
        st, _, _ = self.request("/api/client-log", "POST", {
            "event": "navigation.tab_select", "level": "info",
            "fields": {"from": "plan", "to": "flights", "api_key": "SHOULD_NOT_LOG_SECRET"}
        }, {"Cookie": cookie_pair, "Origin": "https://trek.example.com"})
        self.assertEqual(st, 200)
        st, _, _ = self.request(f"/api/shared/{TRIP}", headers={"Cookie": cookie_pair})
        self.assertEqual(st, 404)

class ProxyClientIpUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("trek_guest_server_proxy_v103", ROOT / "companion/server/server.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    class DummyHandler:
        def __init__(self, peer, headers):
            self.client_address = (peer, 12345)
            self.headers = headers

    def test_trusted_proxy_header_is_used(self):
        old_enabled = self.mod.TRUST_PROXY_HEADERS
        old_nets = self.mod._TRUSTED_PROXY_NETWORKS
        old_header = self.mod.CLIENT_IP_HEADER
        try:
            self.mod.TRUST_PROXY_HEADERS = True
            self.mod._TRUSTED_PROXY_NETWORKS = self.mod._parse_trusted_proxy_networks("192.0.2.10/32")
            self.mod.CLIENT_IP_HEADER = "X-Guest-Client-IP"
            h = self.DummyHandler("192.0.2.10", {"X-Guest-Client-IP": "198.51.100.27"})
            client, source, peer = self.mod._resolved_client(h)
            self.assertEqual(client, "198.51.100.27")
            self.assertEqual(source, "trusted-proxy-header")
            self.assertEqual(peer, "192.0.2.10")
        finally:
            self.mod.TRUST_PROXY_HEADERS = old_enabled
            self.mod._TRUSTED_PROXY_NETWORKS = old_nets
            self.mod.CLIENT_IP_HEADER = old_header

    def test_untrusted_peer_cannot_spoof_header(self):
        old_enabled = self.mod.TRUST_PROXY_HEADERS
        old_nets = self.mod._TRUSTED_PROXY_NETWORKS
        old_header = self.mod.CLIENT_IP_HEADER
        try:
            self.mod.TRUST_PROXY_HEADERS = True
            self.mod._TRUSTED_PROXY_NETWORKS = self.mod._parse_trusted_proxy_networks("192.0.2.10/32")
            self.mod.CLIENT_IP_HEADER = "X-Guest-Client-IP"
            h = self.DummyHandler("203.0.113.99", {"X-Guest-Client-IP": "198.51.100.27"})
            client, source, peer = self.mod._resolved_client(h)
            self.assertEqual(client, "203.0.113.99")
            self.assertEqual(source, "untrusted-peer")
            self.assertEqual(peer, "203.0.113.99")
        finally:
            self.mod.TRUST_PROXY_HEADERS = old_enabled
            self.mod._TRUSTED_PROXY_NETWORKS = old_nets
            self.mod.CLIENT_IP_HEADER = old_header

    def test_malformed_forwarded_header_is_ignored(self):
        old_enabled = self.mod.TRUST_PROXY_HEADERS
        old_nets = self.mod._TRUSTED_PROXY_NETWORKS
        old_header = self.mod.CLIENT_IP_HEADER
        try:
            self.mod.TRUST_PROXY_HEADERS = True
            self.mod._TRUSTED_PROXY_NETWORKS = self.mod._parse_trusted_proxy_networks("192.0.2.10/32")
            self.mod.CLIENT_IP_HEADER = "X-Guest-Client-IP"
            h = self.DummyHandler("192.0.2.10", {"X-Guest-Client-IP": "1.1.1.1, 2.2.2.2"})
            client, source, peer = self.mod._resolved_client(h)
            self.assertEqual(client, "192.0.2.10")
            self.assertEqual(source, "trusted-proxy-missing-header")
        finally:
            self.mod.TRUST_PROXY_HEADERS = old_enabled
            self.mod._TRUSTED_PROXY_NETWORKS = old_nets
            self.mod.CLIENT_IP_HEADER = old_header


class ClientTelemetryUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("trek_guest_server_telemetry_v104", ROOT / "companion/server/server.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_sensitive_client_fields_are_removed(self):
        clean = self.mod._sanitize_client_fields({
            "tab": "flights", "api_key": "secret", "password": "secret2",
            "confirmation": "ABC123", "viewport_w": 390, "message": "safe#fragment",
        })
        self.assertEqual(clean["tab"], "flights")
        self.assertEqual(clean["viewport_w"], 390)
        self.assertEqual(clean["message"], "safe")
        self.assertNotIn("api_key", clean)
        self.assertNotIn("password", clean)
        self.assertNotIn("confirmation", clean)


class FlightSchedulerUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("trek_guest_server_v103", ROOT / "companion/server/server.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_far_future_flight_suppresses_provider_and_checks_every_10m(self):
        from datetime import datetime, timezone, timedelta
        dep = datetime.now(timezone.utc) + timedelta(hours=72)
        arr = dep + timedelta(hours=8)
        reservation = {
            "id": 77, "type": "flight",
            "reservation_time": dep.strftime("%Y-%m-%dT%H:%M"),
            "reservation_end_time": arr.strftime("%Y-%m-%dT%H:%M"),
            "metadata": {"flight_number": "AC123", "airline_code": "AC"},
            "endpoints": [],
        }
        shared = {"days": [], "trip": {"id": 1}}
        old_cfg = self.mod.aerodatabox_configured
        old_build = self.mod._build_live_flight_payload
        try:
            self.mod.aerodatabox_configured = lambda: True
            self.mod._build_live_flight_payload = lambda *a, **k: (_ for _ in ()).throw(AssertionError("provider build should not run"))
            payload = self.mod.get_live_flight_payload(reservation, "77", "1", shared, None)
        finally:
            self.mod.aerodatabox_configured = old_cfg
            self.mod._build_live_flight_payload = old_build
        live = payload["_guestLive"]
        self.assertFalse(live["apiWindowOpen"])
        self.assertEqual(live["refreshAfterSeconds"], 600)
        self.assertGreater(live["apiRefreshAfterSeconds"], 20 * 3600)
        self.assertLess(live["apiRefreshAfterSeconds"], 25 * 3600)

    def test_inside_48h_checks_each_minute_but_provider_ttl_is_30m(self):
        now_ms = int(time.time() * 1000)
        payload = {
            "booking": {"phase": "active", "depMs": now_ms + 24 * 3600 * 1000, "estimatedTimes": False},
            "legs": [{"status": {"status": "Scheduled"}}],
        }
        plan = self.mod._flight_runtime_plan(payload, now_ms)
        self.assertTrue(plan["apiWindowOpen"])
        self.assertEqual(plan["pollAfterSeconds"], 60)
        self.assertEqual(plan["apiTtlSeconds"], 1800)
        self.assertGreaterEqual(plan["apiRefreshAfterSeconds"], 1799)

    def test_active_flight_provider_ttl_is_one_minute(self):
        now_ms = int(time.time() * 1000)
        payload = {
            "booking": {"phase": "active", "depMs": now_ms - 30 * 60 * 1000, "estimatedTimes": False},
            "legs": [{"status": {"status": "EnRoute"}}],
        }
        plan = self.mod._flight_runtime_plan(payload, now_ms)
        self.assertEqual(plan["pollAfterSeconds"], 60)
        self.assertEqual(plan["apiTtlSeconds"], 60)


if __name__ == "__main__":
    unittest.main()
