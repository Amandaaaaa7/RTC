"""Sherpa-onnx keyword spotting backend.

This backend loads a sherpa-onnx keyword spotter and runs streaming inference
on 16 kHz mono PCM. It is imported lazily: if ``sherpa_onnx`` is not installed,
``SherpaOnnxKWS`` is set to ``None`` in ``voice.kws``.

Model layout
------------

For the wenetspeech 3.3M INT8 transducer model the expected layout is::

    models/kws/sherpa-onnx/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/
    ├── encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx
    ├── decoder-epoch-12-avg-2-chunk-16-left-64.onnx      # note: non-int8
    ├── joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx
    ├── tokens.txt
    ├── keywords.txt          # official example keywords
    └── test_wavs/
        ├── 0.wav
        ├── 1.wav
        ├── 2.wav
        ├── 3.wav
        └── test_keywords.txt

The auto-discovery logic in this backend prefers ``epoch-12-avg-2`` files and
the non-int8 decoder, matching the official Python example, and falls back to
``keywords.txt`` / ``test_keywords.txt`` / ``my_keywords.txt`` in that order.

Quick sanity test::

    python3 tools/test_kws_model.py \
        --backend sherpa-onnx \
        --model models/kws/sherpa-onnx/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01 \
        --keyword 文森特卡索 \
        --threshold 0.25 \
        --audio models/kws/sherpa-onnx/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/test_wavs/3.wav

Configuration keys (used by ``FastReactionModule``)
--------------------------------------------------

- ``keyword``: the keyword text (must match an entry in the keywords file).
- ``model_dir``: directory containing the ONNX files and keywords file.
- ``threshold``: confidence threshold in [0.0, 1.0] (default 0.6).
- ``encoder_model`` / ``decoder_model`` / ``joiner_model`` / ``keywords_file`` /
  ``tokens_file``: explicit filenames when the directory contains multiple
  variants.

For details see ``docs/sherpa-onnx-kws-deployment.md`` and the
``tools/test_kws_model.py`` comparison script.
"""

import numpy as np

from voice.kws.base import KWSBackend

try:
    import sherpa_onnx
except ImportError:  # pragma: no cover
    sherpa_onnx = None


class SherpaOnnxKWS(KWSBackend):
    """Keyword spotter using the k2-fsa/sherpa-onnx streaming transducer.

    This implementation expects a transducer-style KWS model directory with
    ``encoder.onnx``, ``decoder.onnx``, ``joiner.onnx`` and ``keywords.txt``.
    The sherpa-onnx Python API is still evolving; the constructor attributes
    below correspond to the high-level ``KeywordSpotter`` class documented in
    the project examples.
    """

    name = "sherpa-onnx"

    def __init__(self, keyword: str | list[str], model_dir: str, threshold: float = 0.6,
                 sample_rate: int = 16000, backend_config: dict | None = None):
        if sherpa_onnx is None:
            raise RuntimeError(
                "sherpa-onnx is not installed. Run: "
                "pip3 install --break-system-packages sherpa-onnx"
            )
        self.keywords = keyword if isinstance(keyword, (list, tuple)) else [keyword]
        self.keyword = self.keywords[0] if self.keywords else ""
        self.model_dir = model_dir
        self.threshold = threshold
        self._sample_rate = sample_rate
        self._backend_config = backend_config or {}
        self._spotter = self._build_spotter()
        self._stream = self._spotter.create_stream()

    @property
    def available(self) -> bool:
        return sherpa_onnx is not None

    def sample_rate(self) -> int:
        return self._sample_rate

    def _build_spotter(self):
        import os
        from pathlib import Path
        bcfg = self._backend_config

        # Project root: voice/kws/ -> project root, used for config/ relative paths.
        project_root = Path(__file__).parent.parent.parent.resolve()

        def _resolve(default: str, prefix: str, keyword_file: bool = False) -> str:
            """Return explicit path or auto-discover a matching ONNX/keywords file."""
            explicit = bcfg.get(default)
            if explicit:
                # 1. Relative to model_dir (e.g. "test_wavs/test_keywords.txt").
                explicit_path = os.path.join(self.model_dir, explicit)
                if os.path.exists(explicit_path):
                    return explicit_path
                # 2. Absolute path.
                if os.path.exists(explicit):
                    return explicit
                # 3. Relative to project root (e.g. "config/my_keywords.txt").
                project_path = os.path.join(project_root, explicit)
                if os.path.exists(project_path):
                    return project_path
                raise FileNotFoundError(f"{default} not found: {explicit}")

            default_path = os.path.join(self.model_dir, default)
            if os.path.exists(default_path):
                return default_path

            try:
                names = [n for n in os.listdir(self.model_dir) if n.endswith(".onnx") or n.endswith(".txt")]
            except OSError:
                return default_path

            candidates = [n for n in names if n.lower().startswith(prefix)]
            if not candidates:
                return default_path

            # Keywords: support both top-level files and test_wavs/ subdir.
            if keyword_file:
                for preferred in (
                    "test_wavs/test_keywords.txt",
                    "test_keywords.txt",
                    "my_keywords.txt",
                    "keywords.txt",
                ):
                    candidate = os.path.join(self.model_dir, preferred)
                    if os.path.exists(candidate):
                        return candidate

            # Prefer the exact official working combo epoch-12-avg-2.
            for name in candidates:
                if "epoch-12-avg-2" in name:
                    if prefix == "decoder":
                        if not name.endswith(".int8.onnx"):
                            return os.path.join(self.model_dir, name)
                    else:
                        if name.endswith(".int8.onnx"):
                            return os.path.join(self.model_dir, name)

            # Fall back to any epoch-12-avg-2 file.
            for name in candidates:
                if "epoch-12-avg-2" in name:
                    if prefix == "decoder":
                        if not name.endswith(".int8.onnx"):
                            return os.path.join(self.model_dir, name)
                    else:
                        return os.path.join(self.model_dir, name)

            # Decoder fallback: prefer non-int8 over int8.
            if prefix == "decoder":
                non_int8 = [n for n in candidates if n.endswith(".onnx") and not n.endswith(".int8.onnx")]
                if non_int8:
                    return os.path.join(self.model_dir, sorted(non_int8)[0])
            else:
                # Encoder/joiner fallback: prefer int8 over non-int8.
                int8 = [n for n in candidates if n.endswith(".int8.onnx")]
                if int8:
                    return os.path.join(self.model_dir, sorted(int8)[0])

            return os.path.join(self.model_dir, sorted(candidates)[0])

        encoder = _resolve("encoder_model", "encoder")
        decoder = _resolve("decoder_model", "decoder")
        joiner = _resolve("joiner_model", "joiner")
        keywords = _resolve("keywords_file", "keywords", keyword_file=True)
        tokens = _resolve("tokens_file", "tokens")

        for path in (encoder, decoder, joiner, keywords, tokens):
            if not os.path.exists(path):
                raise FileNotFoundError(f"Missing sherpa-onnx KWS file: {path}")

        print(f"[sherpa-onnx] model_dir={self.model_dir}")
        print(f"[sherpa-onnx] encoder={encoder}")
        print(f"[sherpa-onnx] decoder={decoder}")
        print(f"[sherpa-onnx] joiner={joiner}")
        print(f"[sherpa-onnx] keywords={keywords}")
        print(f"[sherpa-onnx] tokens={tokens}")
        # Match the official Python example parameters as closely as possible.
        return sherpa_onnx.KeywordSpotter(
            tokens=tokens,
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            keywords_file=keywords,
            sample_rate=self._sample_rate,
            feature_dim=80,
            max_active_paths=4,
            keywords_score=1.0,
            keywords_threshold=self.threshold,
            num_trailing_blanks=1,
            num_threads=1,
            provider="cpu",
        )

    def reset(self):
        self._stream = self._spotter.create_stream()

    def detect(self, pcm_float32: np.ndarray) -> tuple[str | None, float]:
        """Feed audio and return any keyword detection.

        The input is already resampled to 16 kHz and normalized to [-1, 1].
        Sherpa-onnx accepts float32 PCM in this range directly; do NOT rescale
        to int16 range, or the dynamic range/keyword sensitivity changes.
        """
        chunk_size = 320  # 20 ms @ 16 kHz, matches the official streaming chunk.
        detected = None
        confidence = 0.0
        for start in range(0, len(pcm_float32), chunk_size):
            chunk = pcm_float32[start:start + chunk_size]
            self._stream.accept_waveform(self._sample_rate, chunk)
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
                result = self._spotter.get_result(self._stream)
                if result:
                    keyword = getattr(result, "keyword", str(result))
                    # sherpa-onnx get_result returns a string; there is no
                    # per-result confidence. The detection threshold lives in
                    # KeywordSpotter's keywords_threshold constructor parameter.
                    if detected is None and keyword in self.keywords:
                        detected = keyword
                        confidence = 1.0

        return detected, confidence

    def finalize_stream(self) -> tuple[str | None, float]:
        """Offline-only flush. Call once after the whole WAV has been streamed.

        In live mode this must never be called, because the stream is continuous.
        """
        detected = None
        confidence = 0.0
        self._stream.input_finished()
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                keyword = getattr(result, "keyword", str(result))
                if detected is None and keyword in self.keywords:
                    detected = keyword
                    confidence = 1.0
        return detected, confidence
