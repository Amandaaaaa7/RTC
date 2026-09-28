import time
import unittest

import numpy as np

from voice.reactive_module import ReactiveVoiceModule


class ReactiveIdlePlaybackTest(unittest.TestCase):
    def test_low_level_mic_frames_still_allow_idle_autoplay(self):
        module = ReactiveVoiceModule(mic_monitor=None, threshold=0.9)
        module._running = True
        module._last_play_time = time.time() - module._idle_interval - 0.1
        module._queue.put(np.zeros(64, dtype=np.int32))

        self.assertTrue(module._wait_for_voice())
        self.assertEqual("空闲触发", module.state)
        self.assertEqual(f"idle {module._idle_interval:g}s", module.last_info)


if __name__ == "__main__":
    unittest.main()
