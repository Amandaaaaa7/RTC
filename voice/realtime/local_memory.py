"""Local-only, inspectable text memory for the optional real-time dialogue path."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import LocalMemoryConfig


class LocalConversationMemory:
    """Persist completed text turns and append an auditable daily JSONL journal.

    Raw microphone audio, credentials, and cloud protocol payloads are never
    written here. This store is deliberately separate from ``voice/memory.py``
    so the legacy VoiceModule keeps its existing data and behavior.
    """

    def __init__(self, config: LocalMemoryConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._turns: list[dict[str, str | int]] = []
        if config.enabled:
            self._load()

    def history_for_prompt(self) -> list[dict[str, str]]:
        """Return the bounded user/assistant messages used to continue a session."""
        with self._lock:
            recent = self._turns[-self.config.max_context_turns:]
            messages: list[dict[str, str]] = []
            for turn in recent:
                messages.extend([
                    {"role": "user", "content": str(turn["user_text"])},
                    {"role": "assistant", "content": str(turn["assistant_text"])},
                ])
            return messages

    def record_completed_turn(self, trace_id: str, user_text: str, assistant_text: str) -> None:
        if not self.config.enabled or not user_text.strip() or not assistant_text.strip():
            return
        now = datetime.now().astimezone()
        record = {
            "timestamp": now.isoformat(timespec="seconds"),
            "created_at_ns": time.time_ns(),
            "trace_id": trace_id,
            "user_text": user_text.strip(),
            "assistant_text": assistant_text.strip(),
        }
        with self._lock:
            self._turns.append(record)
            self._turns = self._turns[-self.config.max_context_turns:]
            self._save_locked()
            self._append_journal_locked(record, now)

    def clear_context(self) -> None:
        """Forget prompt context while retaining the explicit daily journal."""
        if not self.config.enabled:
            return
        with self._lock:
            self._turns.clear()
            self._save_locked()

    def forget_all(self) -> None:
        """Delete this feature's local context and daily journals on explicit request."""
        if not self.config.enabled:
            return
        with self._lock:
            self._turns.clear()
            try:
                self.config.store_path.unlink(missing_ok=True)
            except OSError as exc:
                raise RuntimeError(f"删除本地记忆失败：{exc}") from exc
            try:
                if self.config.journal_dir.exists():
                    for journal in self.config.journal_dir.glob("*.jsonl"):
                        journal.unlink()
            except OSError as exc:
                raise RuntimeError(f"删除本地日记失败：{exc}") from exc

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "stored_turns": len(self._turns),
                "store_path": str(self.config.store_path),
                "journal_dir": str(self.config.journal_dir),
            }

    def _load(self) -> None:
        try:
            data = json.loads(self.config.store_path.read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            if isinstance(turns, list):
                self._turns = [turn for turn in turns if self._valid_turn(turn)][-self.config.max_context_turns:]
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[REALTIME] 本地记忆加载失败：{exc}")

    @staticmethod
    def _valid_turn(turn: object) -> bool:
        return isinstance(turn, dict) and isinstance(turn.get("user_text"), str) and isinstance(turn.get("assistant_text"), str)

    def _save_locked(self) -> None:
        self.config.store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.store_path.with_suffix(self.config.store_path.suffix + ".tmp")
        payload = {"version": 1, "turns": self._turns}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config.store_path)

    def _append_journal_locked(self, record: dict, now: datetime) -> None:
        self.config.journal_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.journal_dir / f"{now.date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(record, ensure_ascii=False) + "\n")
