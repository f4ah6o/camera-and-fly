from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from host.atomcam import AtomCamError, AtomCamSource


class _Handler(BaseHTTPRequestHandler):
    payload = b"\xff\xd8fixture\xff\xd9"
    mode = "ok"

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/jpeg")
            self.end_headers()
            return
        if self.path == "/foreign":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/jpeg")
            self.end_headers()
            return
        if self.path != "/jpeg":
            self.send_error(404)
            return
        if self.mode == "invalid":
            body = b"not jpeg"
        elif self.mode == "large":
            body = b"\xff\xd8" + b"x" * 100 + b"\xff\xd9"
        else:
            body = self.payload
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class AtomCamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def url(self, path="/jpeg"):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def test_frame_metadata_and_bounded_jpeg(self):
        source = AtomCamSource(self.url("/"), jpeg_path="/jpeg", max_jpeg_bytes=1024)
        first = source.snapshot()
        second = source.snapshot()
        self.assertEqual(first.frame_id, 1)
        self.assertEqual(second.frame_id, 2)
        self.assertGreaterEqual(first.received_monotonic, first.request_started_monotonic)
        self.assertIsNone(first.capture_monotonic)
        self.assertEqual(first.captured_at, first.captured_at)

    def test_invalid_and_oversized_responses_fail(self):
        _Handler.mode = "invalid"
        try:
            with self.assertRaises(AtomCamError):
                AtomCamSource(self.url("/"), jpeg_path="/jpeg").snapshot()
        finally:
            _Handler.mode = "ok"
        _Handler.mode = "large"
        try:
            with self.assertRaises(AtomCamError):
                AtomCamSource(self.url("/"), jpeg_path="/jpeg", max_jpeg_bytes=32).snapshot()
        finally:
            _Handler.mode = "ok"

    def test_same_host_redirect_and_ipv6_rendering(self):
        source = AtomCamSource(self.url("/"), jpeg_path="/redirect")
        self.assertTrue(source.snapshot().jpeg.startswith(b"\xff\xd8"))
        source = AtomCamSource("http://[::1]")
        self.assertEqual(source.rtsp_url("video1"), "rtsp://[::1]:8554/video1_unicast")

    def test_foreign_redirect_is_rejected(self):
        with self.assertRaises(AtomCamError):
            AtomCamSource(self.url("/"), jpeg_path="/foreign").snapshot()

    def test_base_url_rejects_userinfo_and_query(self):
        with self.assertRaises(ValueError):
            AtomCamSource("http://user:pass@127.0.0.1")
        with self.assertRaises(ValueError):
            AtomCamSource("http://127.0.0.1/?secret=1")


if __name__ == "__main__":
    unittest.main()
