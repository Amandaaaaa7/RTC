"""Small, stateful PCM conversions for the shared MicMonitor/speaker path."""

from __future__ import annotations

import numpy as np


class Capture48kTo16k:
    """Convert MicMonitor's 48 kHz signed-32-bit mono frames to ASR PCM."""

    def __init__(self) -> None:
        self._residual = np.empty(0, dtype=np.float64)

    def convert(self, frame: np.ndarray) -> bytes:
        samples = np.asarray(frame, dtype=np.float64).reshape(-1)
        if self._residual.size:
            samples = np.concatenate((self._residual, samples))
        usable = (samples.size // 3) * 3
        self._residual = samples[usable:]
        if usable == 0:
            return b""
        # A three-sample box filter is deterministic and low-cost on Pi Zero 2W.
        reduced = samples[:usable].reshape(-1, 3).mean(axis=1)
        pcm16 = np.clip(np.rint(reduced / 65536.0), -32768, 32767).astype("<i2")
        return pcm16.tobytes()

    def reset(self) -> None:
        self._residual = np.empty(0, dtype=np.float64)
