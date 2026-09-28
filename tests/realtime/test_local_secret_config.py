import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice.realtime.config import RealtimeVoiceConfig


class LocalSecretConfigTest(unittest.TestCase):
    def test_ignored_local_config_supplies_keys_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "realtime_voice.local.json"
            path.write_text(json.dumps({"volc_direct": {
                "voice_api_key": "local-voice", "ark_api_key": "local-ark", "ark_model": "local-model"
            }}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                local = RealtimeVoiceConfig.from_file(path)
            self.assertEqual("local-voice", local.direct.voice_api_key)
            self.assertEqual("local-ark", local.direct.ark_api_key)
            self.assertEqual("local-model", local.direct.ark_model)

            with patch.dict(os.environ, {"ARK_MODEL": "environment-model"}, clear=True):
                overridden = RealtimeVoiceConfig.from_file(path)
            self.assertEqual("environment-model", overridden.direct.ark_model)
