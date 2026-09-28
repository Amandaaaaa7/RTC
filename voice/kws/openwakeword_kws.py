"""openwakeword backend for the fast reflex layer.

openwakeword trains small ONNX models from synthetic data (TTS-generated
utterances + noise). Each model recognizes one wake word. This backend loads
such an ONNX model and runs streaming inference on 16 kHz mono PCM.

Deployment on the Pi
---------------------

1. Install openwakeword and ONNX Runtime::

       pip3 install --break-system-packages openwakeword onnxruntime

   On Pi Zero 2W you may need the ARM wheel for onnxruntime from:
   https://github.com/nanowell/openwakeword-raspberrypi or build it yourself.

2. Train or download a model for "小小熊". Training example::

       from openwakeword.utils import download_models
       from openwakeword.openwakeword import Model
       # See openwakeword docs for the exact training API; it typically
       # requires ~100 positive TTS samples + background noise clips.

3. Place the generated ``*.onnx`` model under ``models/kws/openwakeword/``::

       models/kws/openwakeword/
       └── xiaoxiaoxiong.onnx

4. Test the backend::

       python3 tools/test_kws_model.py --backend openwakeword \
           --model models/kws/openwakeword/xiaoxiaoxiong.onnx \
           --keyword 小小熊 \
           --audio tests/audio/name_call_01.wav

Configuration keys
------------------

- ``keyword``: the keyword text (for logging / mapping only; the model itself
  only knows one wake word).
- ``model_path``: path to the ``*.onnx`` model file.
- ``threshold``: inference threshold (default 0.5). The raw model outputs a
  per-frame score; triggering requires several consecutive frames above the
  threshold to reduce false positives.
- ``required_positive_frames``: how many consecutive frames must exceed the
  threshold before a detection is reported (default 3).
- ``inference_framework``: ``"onnx"`` or ``"tflite`` (default ``"onnx"``).

For details see ``docs/fast-reaction-model-research-plan.md`` and
``tools/test_kws_model.py``.
"""

import numpy as np

from voice.kws.base import KWSBackend

try:
    import openwakeword
    from openwakeword.openwakeword import Model
except ImportError:  # pragma: no cover
    openwakeword = None
    Model = None


class OpenWakeWordKWS(KWSBackend):
    """Wake-word spotter using an openwakeword ONNX model."""

    name = "openwakeword"

    def __init__(self, keyword: str, model_path: str, threshold: float = 0.5,
                 required_positive_frames: int = 3,
                 inference_framework: str = "onnx",
                 sample_rate: int = 16000):
        if openwakeword is None or Model is None:
            raise RuntimeError(
                "openwakeword is not installed. Run: "
                "pip3 install --break-system-packages openwakeword onnxruntime"
            )
        self.keyword = keyword
        self.model_path = model_path
        self.threshold = threshold
        self.required_positive_frames = max(1, required_positive_frames)
        self._sample_rate = sample_rate
        self._model = Model(
            wakeword_models=[model_path],
            inference_framework=inference_framework,
        )
        self._positive_frames = 0
        self._last_triggered = False

    @property
    def available(self) -> bool:
        return openwakeword is not None and Model is not None

    def sample_rate(self) -> int:
        return self._sample_rate

    def reset(self):
        self._model.reset()
        self._positive_frames = 0
        self._last_triggered = False

    def detect(self, pcm_float32: np.ndarray) -> tuple[str | None, float]:
        """Feed one 16 kHz chunk and return the keyword if triggered.

        The input is already normalized to [-1, 1]. openwakeword expects
        16-bit int16 PCM in most versions, so we convert here.
        """
        peak = max(np.max(np.abs(pcm_float32)), 1e-9)
        scaled = (pcm_float32 / peak * 32767).astype(np.int16)

        # openwakeword's streaming API usually takes a dict with the model name
        # as key. The dict returned maps model names to scores.
        predictions = self._model.predict(scaled)

        # Find the score for our model file.
        score = 0.0
        if isinstance(predictions, dict):
            score = max(float(v) for v in predictions.values())

        if score >= self.threshold:
            self._positive_frames += 1
        else:
            self._positive_frames = 0

        if self._positive_frames >= self.required_positive_frames:
            # Only emit once per continuous activation.
            if not self._last_triggered:
                self._last_triggered = True
                # Confidence ramps a little with duration.
                conf = min(1.0, 0.5 + 0.1 * self._positive_frames)
                return self.keyword, conf
        else:
            self._last_triggered = False

        return None, 0.0
