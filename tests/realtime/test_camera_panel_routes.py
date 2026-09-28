import http.client
import json
import threading
import unittest

from camera import create_server


class FakeCamera:
    res_idx = 0
    fps = 0
    benchmark_recording = False


class FakeRealtimeVoice:
    def __init__(self):
        self.calls = []

    def get_metrics(self):
        return {"running": True}

    def get_ui_snapshot(self):
        return {
            "available": True, "running": True, "state": "等待说话",
            "last_info": "", "revision": 1,
            "messages": [{"role": "user", "text": "你好"}],
        }

    def start_from_ui(self):
        self.calls.append("start")
        return self.get_ui_snapshot()

    def stop_from_ui(self):
        self.calls.append("stop")
        return self.get_ui_snapshot()

    def clear_from_ui(self):
        self.calls.append("clear")
        return self.get_ui_snapshot()

    def forget_from_ui(self):
        self.calls.append("forget")
        return self.get_ui_snapshot()


class CameraPanelRoutesTest(unittest.TestCase):
    def setUp(self):
        self.voice = FakeRealtimeVoice()
        self.server = create_server("127.0.0.1", 0, FakeCamera(), voice_monitor=self.voice)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def _request(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request(method, path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_panel_html_status_and_controls(self):
        status, html = self._request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn(b'id="realtime-panel"', html)

        status, body = self._request("GET", "/realtime-voice/status")
        self.assertEqual(200, status)
        self.assertEqual("你好", json.loads(body)["messages"][0]["text"])

        status, body = self._request("POST", "/realtime-voice/clear")
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["available"])
        self.assertEqual(["clear"], self.voice.calls)

        status, body = self._request("POST", "/realtime-voice/forget")
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["available"])
        self.assertEqual(["clear", "forget"], self.voice.calls)


if __name__ == "__main__":
    unittest.main()
