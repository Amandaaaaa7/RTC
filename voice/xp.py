"""
Simplified XP / level system.

XP is awarded per interaction. Levels unlock new response strategies
or eye animation intensities. All state is kept in memory and optionally
persisted to a JSON file.
"""

import json
import os
from dataclasses import dataclass, asdict


LEVEL_THRESHOLDS = [
    0,      # Level 1
    50,     # Level 2
    120,    # Level 3
    220,    # Level 4
    360,    # Level 5
    560,    # Level 6
    840,    # Level 7
    1200,   # Level 8
]


@dataclass
class XPState:
    """Player-like progress state."""

    total_xp: int = 0
    interaction_count: int = 0
    streak: int = 0
    last_interaction_time: float = 0.0

    def level(self) -> int:
        lvl = 1
        for threshold in LEVEL_THRESHOLDS:
            if self.total_xp >= threshold:
                lvl = LEVEL_THRESHOLDS.index(threshold) + 1
        return lvl

    def next_level_xp(self) -> int:
        lvl = self.level()
        if lvl >= len(LEVEL_THRESHOLDS):
            return 0
        return LEVEL_THRESHOLDS[lvl]


class XPSystem:
    """Award XP for voice interactions and track level progression."""

    def __init__(self, persist_path: str | None = None):
        self.persist_path = persist_path
        self.state = XPState()
        if persist_path and os.path.exists(persist_path):
            self._load()

    def award_interaction(self, emotion: str, text_length: int):
        """Award XP based on interaction quality."""
        base = 5
        bonus = min(text_length // 10, 10)
        streak_bonus = min(self.state.streak, 5)
        xp = base + bonus + streak_bonus
        self.state.total_xp += xp
        self.state.interaction_count += 1
        self.state.streak += 1
        print(f"[XP] +{xp} XP (总计 {self.state.total_xp}, 等级 {self.state.level()})")
        self.save()

    def break_streak(self):
        """Call when the session goes idle for a long time."""
        if self.state.streak > 0:
            self.state.streak = 0
            print("[XP] 连续互动中断")

    def save(self):
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self.state), f, ensure_ascii=False, indent=2)

    def _load(self):
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state = XPState(**data)
        except Exception as e:
            print(f"[XP] 加载失败: {e}")
