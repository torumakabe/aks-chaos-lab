import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bicep_api_audit import Fetcher, executable, inventory, resolve_href  # noqa: E402


class Response(io.BytesIO):
    def __init__(self, status, url, headers=None, body=b"cached body"):
        super().__init__(body)
        self.code, self.url, self.headers = status, url, headers or {}

    def geturl(self):
        return self.url


class SupportTests(unittest.TestCase):
    def test_inventory(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            subprocess.run([executable("git"), "init", "-q", directory], check=True)
            source = """// resource ignored 'Example/no@2020-01-01' = {}
var example = 'resource ignored \\'Example/no@2020-01-01\\' = {}'
type Alias = resourceInput<'Example/no@2020-01-01'>
/* resource ignored 'Example/no@2020-01-01' = {} */
resource root 'Example.Provider/roots@2026-01-01' existing = {
  name: 'parent'
}
resource loop 'Example.Provider/loops@2026-01-01' = [
  for item in []: {
    name: item
  }
]
resource parent 'Example.Provider/parents@2026-01-01' = if (true) {
  name: 'p'
  resource child 'children' = {
    name: 'c'
  }
}
"""
            Path(directory, "fixture.bicep").write_text(source)
            result = inventory(directory)
            self.assertEqual(len(result["declarations"]), 4)
            self.assertEqual(sum(r["existing"] for r in result["declarations"]), 1)
            self.assertEqual(
                result["declarations"][-1]["type"], "Example.Provider/parents/children"
            )
            self.assertEqual(result["declarations"][-1]["version"], "2026-01-01")
            self.assertEqual(result["declarations"][0]["line"], 5)

    def test_cache_spacing_and_relative_href(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            now, calls = [0.0], []

            def sleep(seconds):
                now[0] += seconds

            def open_request(request, timeout):
                self.assertEqual(timeout, 30)
                calls.append((request.full_url, now[0]))
                return Response(200, request.full_url, body=b"selector")

            fetch = Fetcher(directory, open_request, lambda: now[0], sleep)
            url = "https://learn.microsoft.com/en-us/azure/templates/microsoft.app/agents/connectors"
            result = fetch.get(url)
            self.assertEqual(fetch.get(url), result)
            restored = Fetcher(directory, open_request, lambda: now[0], sleep)
            self.assertEqual(restored.get(url), result)
            resolved = resolve_href(result, "../2026-01-01/agents/connectors")
            self.assertEqual(
                resolved,
                "https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2026-01-01/agents/connectors",
            )
            restored.get(resolved)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][1] - calls[0][1], 3)
            persisted = json.loads(Path(directory, "http-attempts.json").read_text())
            self.assertEqual(persisted, restored.attempts)
            self.assertEqual(result["body"], "selector")

    def test_retry_after_seconds_and_http_date(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            now, statuses = (
                [0.0],
                iter(
                    [
                        (429, {"Retry-After": "9"}),
                        (200, {}),
                        (429, {"Retry-After": "Thu, 01 Jan 1970 00:00:20 GMT"}),
                        (200, {}),
                    ]
                ),
            )

            def sleep(seconds):
                now[0] += seconds

            def open_request(request, timeout):
                status, headers = next(statuses)
                return Response(status, request.full_url, headers)

            fetch = Fetcher(
                directory, open_request, lambda: now[0], sleep, lambda: now[0]
            )
            self.assertEqual(fetch.get("https://learn.microsoft.com/a")["status"], 200)
            self.assertEqual(fetch.get("https://learn.microsoft.com/b")["status"], 200)
            self.assertEqual([a["wait_seconds"] for a in fetch.attempts], [0, 9, 3, 8])
            self.assertEqual(sum(a["status"] == 429 for a in fetch.attempts), 2)
            self.assertFalse(fetch.learn_stopped)

    def test_three_consecutive_429_stop(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            now = [0.0]

            def sleep(seconds):
                now[0] += seconds

            fetch = Fetcher(
                directory,
                lambda request, timeout: Response(429, request.full_url),
                lambda: now[0],
                sleep,
            )
            self.assertEqual(fetch.get("https://learn.microsoft.com/a")["status"], 429)
            self.assertTrue(fetch.learn_stopped)
            self.assertEqual([a["wait_seconds"] for a in fetch.attempts], [0, 30, 60])
            with self.assertRaises(RuntimeError):
                fetch.get("https://learn.microsoft.com/b")
            self.assertEqual(len(fetch.attempts), 3)
            restored = Fetcher(directory, clock=lambda: now[0], sleep=sleep)
            self.assertTrue(restored.learn_stopped)
            with self.assertRaises(RuntimeError):
                restored.get("https://learn.microsoft.com/b")


if __name__ == "__main__":
    unittest.main()
