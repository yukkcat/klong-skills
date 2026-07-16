import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "klong-image" / "scripts" / "generate.py"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test-image").decode("ascii")


class ImageHandler(BaseHTTPRequestHandler):
    requests = 0
    active = 0
    max_active = 0
    delay = 0
    lock = threading.Lock()
    failures_remaining = 0

    def do_POST(self):
        with type(self).lock:
            type(self).requests += 1
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        time.sleep(type(self).delay)
        with type(self).lock:
            should_fail = type(self).failures_remaining > 0
            if should_fail:
                type(self).failures_remaining -= 1
        if should_fail:
            body = b'{"error":"rate limited"}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            with type(self).lock:
                type(self).active -= 1
            return
        body = json.dumps({"data": [{"b64_json": PNG}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        with type(self).lock:
            type(self).active -= 1

    def log_message(self, _format, *_args):
        pass


class GenerateCliTests(unittest.TestCase):
    def setUp(self):
        ImageHandler.requests = 0
        ImageHandler.active = 0
        ImageHandler.max_active = 0
        ImageHandler.delay = 0
        ImageHandler.failures_remaining = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ImageHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_cli(self, *args):
        env = {**os.environ, "KLONG_API_KEY": "test-key"}
        base_url = f"http://127.0.0.1:{self.server.server_port}"
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--base-url", base_url, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def test_count_generates_numbered_output_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image.png"
            result = self.run_cli("--prompt", "test", "--output", str(output), "--count", "3")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ImageHandler.requests, 3)
            self.assertEqual(
                sorted(path.name for path in Path(directory).iterdir()),
                ["image-001.png", "image-002.png", "image-003.png"],
            )

    def test_concurrency_limits_parallel_requests(self):
        ImageHandler.delay = 0.15
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image.png"
            result = self.run_cli(
                "--prompt", "test", "--output", str(output),
                "--count", "4", "--concurrency", "2",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ImageHandler.requests, 4)
            self.assertEqual(ImageHandler.max_active, 2)

    def test_transient_rate_limit_is_retried(self):
        ImageHandler.failures_remaining = 2
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image.png"
            result = self.run_cli(
                "--prompt", "test", "--output", str(output),
                "--retries", "2", "--retry-delay", "0",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ImageHandler.requests, 3)
            self.assertTrue(output.exists())

    def test_serial_model_rejects_concurrency(self):
        result = self.run_cli(
            "--model", "gpt-image-2-vip", "--prompt", "test",
            "--count", "2", "--concurrency", "2",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("only supports --concurrency 1", result.stderr)
        self.assertEqual(ImageHandler.requests, 0)

    def test_concurrency_above_count_is_reduced_to_count(self):
        ImageHandler.delay = 0.1
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image.png"
            result = self.run_cli(
                "--prompt", "test", "--output", str(output),
                "--count", "3", "--concurrency", "50",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ImageHandler.requests, 3)
            self.assertEqual(ImageHandler.max_active, 3)

    def test_partial_batch_failure_returns_structured_summary(self):
        ImageHandler.failures_remaining = 1
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "image.png"
            result = self.run_cli(
                "--prompt", "test", "--output", str(output),
                "--count", "3", "--concurrency", "1", "--retries", "0",
            )

            self.assertEqual(result.returncode, 1)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["succeeded"], 2)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["failures"][0]["index"], 1)
            self.assertEqual(
                sorted(path.name for path in Path(directory).iterdir()),
                ["image-002.png", "image-003.png"],
            )


if __name__ == "__main__":
    unittest.main()
