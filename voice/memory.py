"""
Simplified dual-track memory matrix.

- Short-term memory: recent turn-by-turn context (last N turns).
- Long-term memory: persistent key-value facts about the user/session.

For Pi Zero 2W, use an in-memory structure by default; optionally persist
to a lightweight JSON file on clean shutdown.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Turn:
    """One dialogue turn."""

    role: str          # "user" or "bot"
    text: str
    emotion: str
    timestamp: float


class Memory:
    """Simple dual-track memory for voice dialogue."""

    def __init__(self, max_short_term: int = 10, persist_path: str | None = None):
        self.max_short_term = max_short_term
        self.persist_path = persist_path
        self.short_term: list[Turn] = []
        self.long_term: dict[str, Any] = {}

        if persist_path and os.path.exists(persist_path):
            self._load()

    def add_turn(self, role: str, text: str, emotion: str):
        """Add a turn to short-term memory."""
        turn = Turn(role, text, emotion, time.time())
        self.short_term.append(turn)
        if len(self.short_term) > self.max_short_term:
            self.short_term.pop(0)

    def remember(self, key: str, value: Any):
        """Store a long-term fact."""
        self.long_term[key] = value

    def recall(self, key: str, default: Any = None) -> Any:
        """Recall a long-term fact."""
        return self.long_term.get(key, default)

    def recent_context(self, n: int | None = None) -> list[Turn]:
        """Return the last n turns (or all if n is None)."""
        if n is None:
            return list(self.short_term)
        return self.short_term[-n:]

    def summary_prompt(self) -> str:
        """Generate a short context string for the LLM."""
        if not self.short_term:
            return ""
        lines = []
        for turn in self.short_term[-5:]:
            lines.append(f"{turn.role}: {turn.text} (情绪: {turn.emotion})")
        return "\n".join(lines)

    def save(self):
        """Persist long-term memory to disk."""
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
        data = {
            "long_term": self.long_term,
            "short_term": [asdict(t) for t in self.short_term],
        }
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self):
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.long_term = data.get("long_term", {})
            self.short_term = [Turn(**t) for t in data.get("short_term", [])]
        except Exception as e:
            print(f"[MEMORY] 加载记忆失败: {e}")
