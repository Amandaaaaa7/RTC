import tempfile
import unittest
from pathlib import Path

from voice.realtime.config import LocalMemoryConfig
from voice.realtime.local_memory import LocalConversationMemory


class LocalMemoryTest(unittest.TestCase):
    def test_persists_prompt_context_and_writes_a_text_only_daily_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = LocalMemoryConfig(True, 2, root / "memory.json", root / "journal")
            memory = LocalConversationMemory(config)
            memory.record_completed_turn("trace-1", "我叫小明", "你好，小明")

            restored = LocalConversationMemory(config)
            self.assertEqual(
                [{"role": "user", "content": "我叫小明"}, {"role": "assistant", "content": "你好，小明"}],
                restored.history_for_prompt(),
            )
            journals = list(config.journal_dir.glob("*.jsonl"))
            self.assertEqual(1, len(journals))
            self.assertIn("我叫小明", journals[0].read_text(encoding="utf-8"))

            restored.forget_all()
            self.assertFalse(config.store_path.exists())
            self.assertEqual([], list(config.journal_dir.glob("*.jsonl")))


if __name__ == "__main__":
    unittest.main()
