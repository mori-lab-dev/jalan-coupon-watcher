"""notify-jalan 呼び出しの取り直し（ネットワークに出ない試験）。"""
import io
import json
import socket
import unittest
import urllib.error
from unittest import mock

from jalan_watcher import notifier


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok():
    return _Resp(json.dumps({"mailed": True}).encode())


def _http(code):
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(b"err"))


class RetryTest(unittest.TestCase):
    def call(self, side_effect):
        sleeps = []
        with mock.patch.object(notifier.urllib.request, "urlopen", side_effect=side_effect) as m:
            try:
                r = notifier._call_edge_function("https://example.invalid", "k", subject="s", text="t",
                                                 _sleep=sleeps.append)
            except Exception as e:  # noqa: BLE001
                r = e
        return r, m, sleeps

    def test_success_first(self):
        r, m, s = self.call([_ok()])
        self.assertEqual(r, {"mailed": True}); self.assertEqual(m.call_count, 1); self.assertEqual(s, [])

    def test_timeout_then_success(self):
        r, m, s = self.call([TimeoutError("The read operation timed out"), socket.timeout(), _ok()])
        self.assertEqual(r, {"mailed": True}); self.assertEqual(m.call_count, 3); self.assertEqual(s, [10, 30])

    def test_timeout_three_times_raises(self):
        r, m, s = self.call([TimeoutError(), TimeoutError(), TimeoutError()])
        self.assertIsInstance(r, TimeoutError); self.assertEqual(m.call_count, 3)

    def test_5xx_retried_4xx_not(self):
        r, m, _ = self.call([_http(503), _ok()])
        self.assertEqual(r, {"mailed": True}); self.assertEqual(m.call_count, 2)
        r, m, s = self.call([_http(401)])
        self.assertIsInstance(r, RuntimeError); self.assertEqual(m.call_count, 1); self.assertEqual(s, [])

    def test_timeout_is_60(self):
        with mock.patch.object(notifier.urllib.request, "urlopen", return_value=_ok()) as m:
            notifier._call_edge_function("https://example.invalid", "k", subject="s", text="t")
        self.assertEqual(m.call_args.kwargs["timeout"], 60)


if __name__ == "__main__":
    unittest.main()
