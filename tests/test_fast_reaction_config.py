import json
import json
import unittest
from pathlib import Path

from eye_expressions import get_expression
from voice.fast_reaction import _load_config, _resolve_reaction_audio_paths


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "fast_reaction.json"


class FastReactionConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = _load_config(str(CONFIG_PATH))

    def test_keyword_reactions_match_the_configured_behavior(self):
        reaction_map = self.config["_reaction_map"]

        self.assertEqual(
            "audio_assets/vo/en.mp3", reaction_map["小小熊"]["audio_path"]
        )
        for keyword in ("再见", "拜拜"):
            self.assertEqual(
                "audio_assets/vo/bye.mp3", reaction_map[keyword]["audio_path"]
            )
            self.assertEqual("calmness1.0", reaction_map[keyword]["eye_expression"])
        for keyword in ("你好", "哈喽"):
            self.assertEqual(
                ["audio_assets/vo/hi.mp3", "audio_assets/vo/ya.mp3"],
                reaction_map[keyword]["audio_paths"],
            )
            self.assertEqual("excitement0.6", reaction_map[keyword]["eye_expression"])

    def test_greeting_audio_paths_are_resolved_as_two_candidates(self):
        reaction = self.config["_reaction_map"]["你好"]
        self.assertEqual(
            ["audio_assets/vo/hi.mp3", "audio_assets/vo/ya.mp3"],
            _resolve_reaction_audio_paths(reaction),
        )

    def test_every_configured_keyword_has_a_kws_entry(self):
        keyword_entries = {
            line.rsplit("@", 1)[1].strip()
            for line in (PROJECT_ROOT / "config" / "my_keywords.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if "@" in line
        }
        self.assertTrue(set(self.config["_reaction_map"]).issubset(keyword_entries))

    def test_config_file_is_valid_json(self):
        with CONFIG_PATH.open(encoding="utf-8") as config_file:
            self.assertIsInstance(json.load(config_file), dict)

    def test_configured_eye_expressions_are_registered(self):
        for expression_name in ("calmness1.0", "excitement0.6"):
            expression = get_expression(expression_name)
            self.assertGreater(expression.duration, 0.0)
            self.assertTrue(expression.step(0.0))

    def test_greeting_expression_holds_the_open_eye_frame(self):
        expression = get_expression("excitement0.6")

        self.assertEqual(-5, expression.HOLD_FRAME_OFFSET)
        self.assertEqual(expression._frames[-5]["t"], expression.hold_t)
        self.assertEqual(
            expression._frames[-5]["params"]["eyelid"],
            expression.step(expression.hold_t)["eyelid"],
        )


if __name__ == "__main__":
    unittest.main()
