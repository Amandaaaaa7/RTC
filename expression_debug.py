"""Bounded, opt-in timing trace for Day 3 expression switching."""

from __future__ import annotations

from collections import deque
import threading
import time


class ExpressionDebugTrace:
    """Keep only the latest expression command; never create an action queue."""

    def __init__(self, max_events: int = 128):
        self._lock = threading.Lock()
        self._events = deque(maxlen=max_events)
        self._next_command_id = 0
        self._active = None

    def _record_locked(self, event: str, command: dict, timestamp_ns: int):
        self._events.append({
            "event": event,
            "command_id": command["id"],
            "expression": command["expression"],
            "timestamp_ns": timestamp_ns,
            "elapsed_ms": round((timestamp_ns - command["received_ns"]) / 1_000_000, 3),
        })

    def receive_command(self, expression: str | None, duration_s: float | None):
        """Create the sole active command and mark any unfinished one overridden."""
        now_ns = time.monotonic_ns()
        with self._lock:
            if self._active is not None:
                self._record_locked("expression_overridden", self._active, now_ns)
            self._next_command_id += 1
            command = {
                "id": self._next_command_id,
                "expression": expression or "idle",
                "received_ns": now_ns,
                "duration_ns": (int(duration_s * 1_000_000_000)
                                if duration_s is not None else None),
                "affect_changed": False,
                "rendered": False,
                "spi_done": False,
            }
            self._active = command
            self._record_locked("command_received", command, now_ns)
            return command.copy()

    def affect_changed(self, command_id: int):
        now_ns = time.monotonic_ns()
        with self._lock:
            if self._active is None or self._active["id"] != command_id:
                return
            self._active["affect_changed"] = True
            self._record_locked("affect_state_changed", self._active, now_ns)

    def record_render(self, command_id: int, timestamp_ns: int):
        with self._lock:
            if (self._active is None or self._active["id"] != command_id
                    or self._active["rendered"]):
                return
            self._active["rendered"] = True
            self._record_locked("render_first_new_frame", self._active, timestamp_ns)

    def record_spi_done(self, command_id: int, timestamp_ns: int):
        with self._lock:
            if (self._active is None or self._active["id"] != command_id
                    or self._active["spi_done"]):
                return
            self._active["spi_done"] = True
            self._record_locked("spi_first_new_frame_done", self._active, timestamp_ns)

    def finish_if_due(self):
        """Finish only a debug command with an explicit finite duration."""
        now_ns = time.monotonic_ns()
        with self._lock:
            command = self._active
            if command is None or command["duration_ns"] is None:
                return None
            if now_ns - command["received_ns"] < command["duration_ns"]:
                return None
            self._record_locked("expression_finished", command, now_ns)
            self._active = None
            return command.copy()

    def snapshot(self):
        with self._lock:
            active = self._active.copy() if self._active else None
            return {"active": active, "events": list(self._events)}
