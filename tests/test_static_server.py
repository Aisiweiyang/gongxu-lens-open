"""通过原始 HTTP 请求验证演示静态服务的文件边界。"""

from functools import partial
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from serve import BASE_DIR, SiteHandler


class QuietHandler(SiteHandler):
    def log_message(self, *args):
        pass


class TestStaticServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "site").mkdir()
        (self.root / "output").mkdir()
        (self.root / "site/index.html").write_bytes(b"PUBLIC_INDEX")
        (self.root / "output/report.html").write_bytes(b"PUBLIC_REPORT")
        (self.root / "private.txt").write_bytes(b"PRIVATE_SENTINEL")
        self.server = HTTPServer(("127.0.0.1", 0),
                                 partial(QuietHandler, directory=self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method="GET"):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            conn.close()

    def test_public_files_and_query_strings(self):
        for path, expected in (("/", b"PUBLIC_INDEX"),
                               ("/?preview=1", b"PUBLIC_INDEX"),
                               ("/site/index.html?preview=1", b"PUBLIC_INDEX"),
                               ("/output/report.html", b"PUBLIC_REPORT")):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(body, expected)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_traversal_is_rejected_for_get_and_head(self):
        for method in ("GET", "HEAD"):
            for path in ("/site/../private.txt", "/site/%2e%2e/private.txt",
                         "/output/..%2fprivate.txt", "/site/../../private.txt"):
                with self.subTest(method=method, path=path):
                    status, _, body = self.request(path, method)
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"PRIVATE_SENTINEL", body)

    def test_outside_files_are_rejected_for_get_and_head(self):
        for method in ("GET", "HEAD"):
            status, _, body = self.request("/private.txt", method)
            self.assertEqual(status, 403)
            self.assertNotIn(b"PRIVATE_SENTINEL", body)

    def test_directories_never_redirect_or_list(self):
        for method in ("GET", "HEAD"):
            for path in ("/site", "/site/", "/output", "/output/?list=1"):
                with self.subTest(method=method, path=path):
                    status, _, _ = self.request(path, method)
                    self.assertEqual(status, 403)

    def test_missing_allowed_file_returns_404(self):
        for method in ("GET", "HEAD"):
            status, _, _ = self.request("/site/missing.html", method)
            self.assertEqual(status, 404)

    def test_head_returns_headers_without_body(self):
        status, headers, body = self.request("/output/report.html", "HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(b"PUBLIC_REPORT"))
        self.assertEqual(body, b"")

    def test_invalid_paths_are_rejected_without_handler_exception(self):
        for path in ("/site/%00index.html", "/site/..%5cprivate.txt", "/site/%ff"):
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertEqual(status, 400)

    def test_symlinks_cannot_escape_allowed_root(self):
        try:
            (self.root / "site/link.txt").symlink_to(self.root / "private.txt")
            (self.root / "site/link-dir").symlink_to(self.root, target_is_directory=True)
            (self.root / "site/loop.txt").symlink_to("loop.txt")
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        for method in ("GET", "HEAD"):
            for path in ("/site/link.txt", "/site/link-dir/private.txt"):
                with self.subTest(method=method, path=path):
                    status, _, body = self.request(path, method)
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"PRIVATE_SENTINEL", body)
            status, _, _ = self.request("/site/loop.txt", method)
            self.assertIn(status, (400, 404))

    def test_default_directory_is_independent_of_working_directory(self):
        with patch("serve.SiteHandler.handle", lambda handler: None):
            import io
            class Socket:
                def makefile(self, *args):
                    return io.BytesIO()
            handler = SiteHandler(Socket(), ("127.0.0.1", 0), None)
        self.assertEqual(Path(handler.directory), BASE_DIR)


if __name__ == "__main__":
    unittest.main()
