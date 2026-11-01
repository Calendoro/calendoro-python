import json
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))
import calendoro  # noqa: E402

VECTORS = json.loads((pathlib.Path(__file__).parents[1] / "fixtures" / "webhook-vectors.json").read_text("utf-8"))


class Stub(BaseHTTPRequestHandler):
    seen: list = []
    fail_next: list = []

    def log_message(self, *args):
        pass

    def reply(self, status, payload, headers=None):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", "req_stub")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def handle_any(self):
        length = int(self.headers.get("Content-Length") or 0)
        Stub.seen.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(length)})
        if Stub.fail_next:
            status, headers = Stub.fail_next.pop(0)
            return self.reply(status, {"error": {"code": "stub_failure", "message": "try again"}}, headers)
        if self.path.startswith("/slow"):
            time.sleep(1)
        if self.path.startswith("/v1/webhooks"):
            if "cursor=p2" in self.path:
                return self.reply(200, {"data": [{"id": "whk_2"}], "nextCursor": None})
            return self.reply(200, {"data": [{"id": "whk_1"}], "nextCursor": "p2"})
        if self.path == "/v1/me":
            return self.reply(200, {"organizationId": "org_1"})
        if self.path == "/v1/bookings" and self.command == "POST":
            return self.reply(201, {"id": "bkg_1", "status": "confirmed"})
        if self.path in ("/v1/event-types", "/v1/auth/login") and self.command == "POST":
            return self.reply(500, {"error": {"code": "internal", "message": "boom"}})
        return self.reply(404, {"error": {"code": "not_found", "message": "nope", "details": ["x"]}})

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_any


class SdkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Stub.seen.clear()
        Stub.fail_next.clear()
        self.client = calendoro.Calendoro("cdo_key", base_url=self.url)
        self.client_fast = calendoro.Calendoro("cdo_key", base_url=self.url, timeout=0.1, max_retries=0)

    def test_api_key_and_oauth_token_are_bearer(self):
        self.assertEqual(self.client.account.get_api_identity()["organizationId"], "org_1")
        self.assertEqual(Stub.seen[0]["headers"]["authorization"], "Bearer cdo_key")
        calendoro.Calendoro(oauth_token="cdoa_t", base_url=self.url).account.get_api_identity()
        self.assertEqual(Stub.seen[1]["headers"]["authorization"], "Bearer cdoa_t")

    def test_retries_503_with_retry_after(self):
        Stub.fail_next.append((503, {"Retry-After": "0"}))
        self.assertEqual(self.client.account.get_api_identity()["organizationId"], "org_1")
        self.assertEqual(len(Stub.seen), 2)

    def test_gives_up_after_max_retries(self):
        Stub.fail_next.extend([(503, {"Retry-After": "0"})] * 3)
        with self.assertRaises(calendoro.ServerError):
            self.client.account.get_api_identity()
        self.assertEqual(len(Stub.seen), 3)

    def test_idempotency_key_stable_across_retries(self):
        Stub.fail_next.append((503, {"Retry-After": "0"}))
        booking = self.client.bookings.create_booking({"eventTypeId": "e"})
        self.assertEqual(booking["id"], "bkg_1")
        keys = [s["headers"]["idempotency-key"] for s in Stub.seen]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.client.bookings.create_booking({"eventTypeId": "e"}, idempotency_key="mine")
        self.assertEqual(Stub.seen[-1]["headers"]["idempotency-key"], "mine")

    def test_write_without_key_is_not_retried_on_500(self):
        with self.assertRaises(calendoro.ServerError):
            self.client.authentication.login({"email": "a@example.com", "password": "password"})
        self.assertEqual(len(Stub.seen), 1)
        self.assertNotIn("idempotency-key", Stub.seen[0]["headers"])

    def test_iterator_follows_cursor(self):
        self.assertEqual([w["id"] for w in self.client.webhooks.list_webhooks_iter()], ["whk_1", "whk_2"])
        self.assertEqual(len(Stub.seen), 2)

    def test_typed_error_carries_request_id(self):
        with self.assertRaises(calendoro.NotFoundError) as caught:
            self.client.event_types.get_event_type("a/b")
        e = caught.exception
        self.assertEqual((e.status, e.code, e.request_id, e.details), (404, "not_found", "req_stub", ["x"]))
        self.assertEqual(Stub.seen[0]["path"], "/v1/event-types/a%2Fb")

    def test_timeout(self):
        with self.assertRaises(calendoro.TimeoutError):
            self.client_fast.request("GET", "/slow")

    def test_webhook_vectors_match_packages_webhooks(self):
        for v in VECTORS:
            with self.subTest(v["name"]):
                got = calendoro.verify_signature(v["header"], v["secret"], v["body"], now=v["now"], tolerance=v.get("tolerance", 300))
                self.assertEqual(got, v["valid"])

    def test_construct_event(self):
        v = VECTORS[0]
        self.assertEqual(calendoro.construct_event(v["body"], v["header"], v["secret"], now=v["now"])["id"], "wev_1")
        with self.assertRaises(calendoro.WebhookSignatureError):
            calendoro.construct_event(v["body"], None, v["secret"])


if __name__ == "__main__":
    unittest.main()
