import time
import unittest

from expression_debug import ExpressionDebugTrace
from eye_display import EyeDisplay


class ExpressionDebugTraceTests(unittest.TestCase):
    def test_latest_command_overrides_instead_of_queueing(self):
        trace = ExpressionDebugTrace()
        first = trace.receive_command("happy", 5.0)
        second = trace.receive_command("angry", 5.0)
        events = trace.snapshot()["events"]
        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 2)
        self.assertEqual([event["event"] for event in events], [
            "command_received", "expression_overridden", "command_received",
        ])
        self.assertEqual(events[1]["command_id"], first["id"])

    def test_render_and_spi_are_recorded_once(self):
        trace = ExpressionDebugTrace()
        command = trace.receive_command("happy", 5.0)
        trace.affect_changed(command["id"])
        trace.record_render(command["id"], command["received_ns"] + 1_000_000)
        trace.record_render(command["id"], command["received_ns"] + 2_000_000)
        trace.record_spi_done(command["id"], command["received_ns"] + 3_000_000)
        trace.record_spi_done(command["id"], command["received_ns"] + 4_000_000)
        self.assertEqual([event["event"] for event in trace.snapshot()["events"]], [
            "command_received", "affect_state_changed", "render_first_new_frame",
            "spi_first_new_frame_done",
        ])

    def test_debug_command_returns_to_idle_after_duration(self):
        eye = EyeDisplay(expression_debug=True)
        snapshot = eye.command_expression_debug("happy", duration_ms=100)
        self.assertEqual(snapshot["active"]["expression"], "happy")
        time.sleep(0.11)
        expression, _start, command_id = eye._expression_for_frame(time.time())
        self.assertIsNone(expression)
        self.assertIsNone(command_id)
        self.assertEqual(eye.get_expression_debug_snapshot()["events"][-1]["event"],
                         "expression_finished")

    def test_debug_is_disabled_by_default(self):
        eye = EyeDisplay()
        with self.assertRaises(RuntimeError):
            eye.command_expression_debug("happy")


if __name__ == "__main__":
    unittest.main()
