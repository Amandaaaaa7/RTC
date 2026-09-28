"""Energy-based dummy keyword spotting backend for pipeline testing.

This backend does not perform real keyword recognition; it triggers on a loud
short utterance so the fast-reaction loop (audio + eye response) can be tested
before a real KWS model is deployed.
"""

import numpy as np

from voice.kws.base import KWSBackend


class EnergyDummyKWS(KWSBackend):
    """Dummy KWS that fires on a short, loud utterance.

    Useful for validating the full fast-reaction pipeline (MicMonitor →
    FastReactionModule → speaker + eye) without installing sherpa-onnx or
    openwakeword on the Pi.
    """

    @property
    def name(self) -> str:
        return "energy-dummy"

    def __init__(self, keyword: str = "name_call", threshold: float = 0.15,
                 min_blocks: int = 2, max_blocks: int = 8):
        """
        Args:
            keyword: The pseudo-keyword emitted on trigger.
            threshold: Normalized RMS threshold [0, 1].
            min_blocks: Utterance must be at least this many blocks loud.
            max_blocks: Ignore if utterance lasts longer than this many blocks.
        """
        self.keyword = keyword
        self.threshold = threshold
        self.min_blocks = min_blocks
        self.max_blocks = max_blocks
        self._active_blocks = 0
        self._fired = False

    @property
    def available(self) -> bool:
        return True

    def sample_rate(self) -> int:
        return 16000

    def reset(self):
        self._active_blocks = 0
        self._fired = False

    def detect(self, pcm_float32: np.ndarray) -> tuple[str | None, float]:
        if len(pcm_float32) == 0:
            return None, 0.0
        rms = float(np.sqrt(np.mean(pcm_float32.astype(np.float64) ** 2)))
        loud = rms > self.threshold
        if loud:
            self._active_blocks += 1
            if self._active_blocks >= self.min_blocks and not self._fired:
                self._fired = True
                # Confidence ramps slightly with duration but caps at 1.0
                conf = min(1.0, 0.6 + 0.05 * self._active_blocks)
                return self.keyword, conf
        else:
            if self._active_blocks > self.max_blocks:
                self._fired = False
            self._active_blocks = 0
        return None, 0.0
