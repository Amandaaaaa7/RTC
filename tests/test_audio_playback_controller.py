import threading
import time
import unittest

from speaker import AudioPlaybackController


class AudioPlaybackControllerTest(unittest.TestCase):
    def test_submit_returns_before_a_running_job_finishes(self):
        controller = AudioPlaybackController(max_queue=1)
        started = threading.Event()
        release = threading.Event()

        def slow_runner(first_sample):
            started.set()
            first_sample(time.monotonic_ns())
            release.wait(timeout=1.0)

        began = time.monotonic()
        handle = controller.submit("slow.wav", slow_runner)
        elapsed = time.monotonic() - began

        self.assertIsNotNone(handle)
        self.assertLess(elapsed, 0.1)
        self.assertTrue(started.wait(timeout=1.0))
        self.assertEqual("playing", controller.get_metrics()["state"])
        release.set()
        self.assertTrue(handle.wait(timeout=1.0))
        self.assertEqual("idle", controller.get_metrics()["state"])

    def test_jobs_are_serialized_and_callbacks_are_ordered(self):
        controller = AudioPlaybackController(max_queue=1)
        first_started = threading.Event()
        release_first = threading.Event()
        events = []

        def first_runner(_first_sample):
            events.append("first-run")
            first_started.set()
            release_first.wait(timeout=1.0)

        def second_runner(_first_sample):
            events.append("second-run")

        first = controller.submit("first.wav", first_runner)
        self.assertTrue(first_started.wait(timeout=1.0))
        second = controller.submit("second.wav", second_runner)
        self.assertIsNotNone(second)
        self.assertEqual(["first-run"], events)
        release_first.set()
        self.assertTrue(first.wait(timeout=1.0))
        self.assertTrue(second.wait(timeout=1.0))
        self.assertEqual(["first-run", "second-run"], events)

        metrics = controller.get_metrics()
        self.assertEqual(2, metrics["started_count"])
        self.assertEqual(2, metrics["completed_count"])
        self.assertEqual(0, metrics["failed_count"])

    def test_full_queue_is_rejected_without_blocking(self):
        controller = AudioPlaybackController(max_queue=1)
        started = threading.Event()
        release = threading.Event()

        def slow_runner(_first_sample):
            started.set()
            release.wait(timeout=1.0)

        first = controller.submit("first.wav", slow_runner)
        self.assertTrue(started.wait(timeout=1.0))
        second = controller.submit("second.wav", slow_runner)
        rejected = controller.submit("third.wav", slow_runner)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(rejected)
        self.assertEqual(1, controller.get_metrics()["rejected_count"])
        release.set()
        self.assertTrue(first.wait(timeout=1.0))
        self.assertTrue(second.wait(timeout=1.0))


if __name__ == "__main__":
    unittest.main()
