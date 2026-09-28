"""Fast reflex voice reaction module.

Subscribes to MicMonitor callbacks, runs keyword spotting inference in a separate
worker thread, and immediately triggers pre-recorded interjection audio + an eye
reaction when the configured keyword (e.g. "小小熊") is spotted.

Key design points:

- MicMonitor callbacks must not block. ``on_audio_frame`` only enqueues the
  raw int32 frame into a bounded queue.
- A single worker thread consumes the queue, accumulates a sliding window of
  audio, resamples it to the backend's target rate, and calls ``KWSBackend.detect``.
- When a detection exceeds the configured confidence threshold, the module
  signals the eye display via ``EyeDisplay.set_expression`` immediately, then
  plays a pre-recorded audio file. The audio is pre-decoded at module start into
  a 48 kHz stereo S32_LE PCM buffer and submitted directly to ALSA through
  ``speaker.play_buffer`` (non-blocking), avoiding ffmpeg/aplay startup latency.
- A cooldown prevents the same utterance from triggering repeatedly.

This module is optional and only loaded when ``main.py`` is started with
``--kws``.
"""

import json
import os
import queue
import random
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from voice.config import SAMPLE_RATE, BLOCK_SIZE, AUDIO_DIR
from voice.kws import KWSBackend, EnergyDummyKWS, SherpaOnnxKWS, OpenWakeWordKWS
import speaker


# Default configuration path. Keeping it in config/ lets users edit keyword and
# reaction mapping without touching code.
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "fast_reaction.json"


def _load_config(config_path: str | None) -> dict:
    """Load configuration from JSON or return sensible defaults.

    Supports both legacy single-reaction config (keyword/audio_path/eye_expression)
    and new multi-reaction config (reactions list). Legacy configs are converted
    into a single-item reactions list for uniform handling.
    """
    if config_path is None:
        config_path = str(DEFAULT_CONFIG_PATH)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    project_root = Path(__file__).parent.parent
    # audio_assets 与项目根目录平级：pi_affe_sys/audio_assets/...
    default_audio = project_root / "audio_assets" / "vo" / "034_confusion_short.mp3"

    defaults = {
        "detection_threshold": 0.5,
        "cooldown_s": 3.0,
        "backend": "dummy",
        "backend_config": {},
        "eye_expression_duration_s": 2.0,
    }
    for key, value in defaults.items():
        cfg.setdefault(key, value)

    # Convert legacy single-reaction config into the new reactions list.
    if "reactions" not in cfg:
        cfg["reactions"] = [{
            "keywords": [cfg.get("keyword", "小小熊")],
            "audio_path": cfg.get("audio_path", str(default_audio)),
            "eye_expression": cfg.get("eye_expression", "surprise"),
        }]

    # Build a keyword -> reaction lookup table.
    cfg["_reaction_map"] = {}
    for reaction in cfg["reactions"]:
        for kw in reaction.get("keywords", []):
            cfg["_reaction_map"][kw] = reaction

    return cfg


def _build_backend(cfg: dict) -> KWSBackend:
    """Instantiate the configured KWS backend, falling back to dummy on errors.

    The backend is given the union of all keywords across configured reactions.
    """
    backend_name = cfg.get("backend", "dummy").lower()
    keywords = list(cfg.get("_reaction_map", {}).keys())
    # Backward-compatible fallback for legacy single-keyword configs.
    if not keywords:
        keywords = [cfg.get("keyword", "小小熊")]
    cfg["_primary_keyword"] = keywords[0]
    keyword = keywords[0]
    threshold = float(cfg.get("detection_threshold", 0.5))
    bcfg = cfg.get("backend_config", {})

    if backend_name == "sherpa-onnx":
        try:
            model_dir = bcfg.get("model_dir", "models/kws/sherpa-onnx")
            return SherpaOnnxKWS(
                keyword=keywords,
                model_dir=model_dir,
                threshold=threshold,
                backend_config=bcfg,
            )
        except Exception as exc:
            print(f"[KWS] 无法加载 sherpa-onnx backend: {exc}; 回退到 dummy")
    elif backend_name == "openwakeword":
        try:
            model_path = bcfg.get("model_path", "models/kws/openwakeword/xiaoxiaoxiong.onnx")
            return OpenWakeWordKWS(
                keyword=keyword,
                model_path=model_path,
                threshold=threshold,
                required_positive_frames=bcfg.get("required_positive_frames", 3),
                inference_framework=bcfg.get("inference_framework", "onnx"),
                backend_config=bcfg,
            )
        except Exception as exc:
            print(f"[KWS] 无法加载 openwakeword backend: {exc}; 回退到 dummy")

    return EnergyDummyKWS(
        keyword=keyword,
        threshold=bcfg.get("dummy_threshold", 0.15),
        min_blocks=bcfg.get("dummy_min_blocks", 2),
        max_blocks=bcfg.get("dummy_max_blocks", 8),
    )


def _resolve_reaction_audio_paths(reaction: dict) -> list[str]:
    """Return a list of audio file paths for a reaction entry.

    Supports three forms:
      - "audio_path": string -> [path]  (legacy single-file)
      - "audio_paths": list of strings -> [...]
      - "audio_path": directory -> scan for .wav/.mp3 files
    """
    paths = reaction.get("audio_paths")
    if isinstance(paths, list):
        return [str(p) for p in paths if p]

    audio_path = reaction.get("audio_path", "")
    if not audio_path:
        return []
    audio_path = str(audio_path)
    if os.path.isdir(audio_path):
        files = []
        for fname in sorted(os.listdir(audio_path)):
            if fname.lower().endswith((".wav", ".mp3")):
                files.append(os.path.join(audio_path, fname))
        return files
    return [audio_path] if os.path.exists(audio_path) else []


def _preload_audio(audio_path: str, volume: float) -> tuple[bytes, int, int, int, float] | None:
    """把反应音频预解码成 48kHz 单声道 S32_LE PCM 字节，触发时直接写 ALSA。

    刻意输出单声道（-ac 1），让 speaker.play_buffer 内部走 column_stack
    展开成立体声，避免直接传交织立体声时 chunk 边界不是 8 字节整数倍而
    触发 ALSA "buffer size must be a multiple of element size" 错误。

    返回 (pcm_bytes, sample_rate, channels, sample_width, duration_s) 或 None（失败）。
    duration_s 按 PCM 字节数 / (sample_rate * channels * sample_width) 计算，
    供 FastReactionModule._trigger 决定表情复位时间，确保表情不短于音频。
    失败时不抛异常，调用方回退到 speaker.play_wav。
    """
    if not audio_path or not os.path.exists(audio_path):
        return None
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", audio_path,
            "-af", f"volume={volume}",
            "-f", "s32le", "-acodec", "pcm_s32le",
            "-ar", str(speaker.SAMPLE_RATE),
            "-ac", "1", "-",
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
        )
        raw = proc.stdout
        if not raw:
            return None
        sample_rate = speaker.SAMPLE_RATE
        channels = 1
        sample_width = 4
        duration_s = len(raw) / (sample_rate * channels * sample_width)
        return raw, sample_rate, channels, sample_width, duration_s
    except Exception as exc:
        print(f"[KWS] 预加载音频失败，将走 speaker.play_wav 回退: {exc}")
        return None


def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear interpolation resample (same as speaker._resample)."""
    if src_rate == dst_rate:
        return data
    ratio = dst_rate / src_rate
    x_old = np.linspace(0, 1, len(data))
    x_new = np.linspace(0, 1, int(len(data) * ratio))
    return np.interp(x_new, x_old, data)


class FastReactionModule:
    """Keyword-triggered fast reaction: audio + eye response within ~100-500 ms."""

    # Expose the backend builder so main.py can rebuild after CLI overrides.
    _backend_factory = staticmethod(_build_backend)

    def __init__(
        self,
        mic_monitor,
        eye_display=None,
        config_path: str | None = None,
        volume: float = 0.3,
        on_keyword: Callable[[str, float], None] | None = None,
    ):
        """
        Args:
            mic_monitor: MicMonitor instance to subscribe to.
            eye_display: Optional EyeDisplay; if provided, expressions/states
                will be sent to it on detection.
            config_path: Path to ``fast_reaction.json``. If None, the default
                ``config/fast_reaction.json`` is used.
            volume: Playback volume for the interjection audio.
            on_keyword: Optional callback(keyword, confidence) invoked on
                every detection. Useful for tests or HTTP status hooks.
        """
        self.mic = mic_monitor
        self.eye = eye_display
        self.cfg = _load_config(config_path)
        self.volume = volume
        self.on_keyword = on_keyword

        self._backend = _build_backend(self.cfg)
        self._target_rate = self._backend.sample_rate()

        # Pre-load each reaction's audio so we don't pay ffmpeg/aplay startup
        # cost at trigger time. Keyed by keyword -> list of (pcm, rate, ch, width, duration).
        # If a file fails to load, that reaction falls back to speaker.play_wav.
        self._preloaded_pcm: dict[str, list[tuple]] = {}
        self._preload_reaction_audio()

        # Bounded queue for MicMonitor frames. Dropping old frames is acceptable
        # for a fast reflex because we only care about the latest utterance.
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self._thread: threading.Thread | None = None
        self._running = False

        self._lock = threading.Lock()
        self._last_detection_time = 0.0
        self._last_keyword: str | None = None
        self._last_confidence = 0.0
        self._metrics = {
            "frames_processed": 0,
            "detections": 0,
            "inference_ms_p50": None,
            "inference_ms_p95": None,
        }
        self._inference_times: list[float] = []

        # Audio playback gate: while a reaction audio clip is playing, ignore
        # new keyword detections so that long interjections (e.g. songs) are not
        # interrupted or followed by another reaction immediately after ending.
        self._audio_busy_until = 0.0

        # Accumulated audio window for backends that need a longer chunk than one
        # MicMonitor block. Keep up to 1 second of 48 kHz PCM.
        self._window = np.zeros(0, dtype=np.int32)
        self._max_window_samples = SAMPLE_RATE

    @property
    def keyword(self) -> str:
        """Return the primary keyword (first configured keyword) for display."""
        return self.cfg.get("keyword", self.cfg.get("_primary_keyword", "小小熊"))

    @property
    def keywords(self) -> list[str]:
        """Return all configured keywords."""
        return list(self.cfg.get("_reaction_map", {}).keys())

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def _reset_state(self):
        """Reset per-utterance backend state."""
        self._backend.reset()
        self._window = np.zeros(0, dtype=np.int32)

    def _should_cooldown(self) -> bool:
        return time.time() - self._last_detection_time < self.cfg["cooldown_s"]

    def on_audio_frame(self, frame: np.ndarray):
        """Called by MicMonitor for each audio frame. Must not block."""
        try:
            self._queue.put_nowait(frame.copy())
        except queue.Full:
            # Drop the oldest frame to make room for the newest one.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(frame.copy())
            except queue.Empty:
                pass

    def _consume_frame(self, frame: np.ndarray):
        """Process one MicMonitor frame in the worker thread."""
        now = time.time()
        if now < self._audio_busy_until:
            return
        if self._should_cooldown():
            return

        # Streaming backends (e.g. sherpa-onnx) maintain internal state, so only
        # the new audio should be fed each call. Resample one MicMonitor block
        # (48 kHz int32) to the backend target rate and normalize using the fixed
        # int32 range to avoid per-frame volume distortion.
        resampled = _resample(frame.astype(np.float32) / 2147483648.0, SAMPLE_RATE, self._target_rate)
        pcm_float32 = resampled.astype(np.float32)

        # Also keep a short sliding window for diagnostics / non-streaming use.
        self._window = np.concatenate([self._window, frame])
        if len(self._window) > self._max_window_samples:
            self._window = self._window[-self._max_window_samples:]

        t0 = time.monotonic()
        keyword, confidence = self._backend.detect(pcm_float32)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        self._inference_times.append(elapsed_ms)
        if len(self._inference_times) > 256:
            self._inference_times = self._inference_times[-256:]

        self._metrics["frames_processed"] += 1

        if keyword is not None and confidence >= self.cfg["detection_threshold"]:
            self._trigger(keyword, confidence)

    def _preload_reaction_audio(self):
        """在启动前把每个反应音频解码进内存；失败时 _trigger 会回退到 play_wav。

        同一个 audio_path 只解码一次，多个关键词共享同一份 PCM，避免重复 ffmpeg
        和重复内存占用。支持单个音频、目录或多音频列表（audio_paths）。
        """
        path_cache: dict[str, tuple] = {}
        for keyword, reaction in self.cfg.get("_reaction_map", {}).items():
            audio_files = _resolve_reaction_audio_paths(reaction)
            if not audio_files:
                print(f"[KWS] 关键词 '{keyword}' 未配置音频路径或路径不存在")
                continue
            loaded_entries: list[tuple] = []
            for audio_path in audio_files:
                if audio_path in path_cache:
                    loaded_entries.append(path_cache[audio_path])
                    print(f"[KWS] 预加载反应音频 '{keyword}': {os.path.basename(audio_path)} (共享)")
                    continue
                loaded = _preload_audio(audio_path, self.volume)
                if loaded is not None:
                    path_cache[audio_path] = loaded
                    loaded_entries.append(loaded)
                    print(f"[KWS] 预加载反应音频 '{keyword}': {os.path.basename(audio_path)}")
                else:
                    print(f"[KWS] 关键词 '{keyword}' 音频预加载失败: {audio_path}")
            if loaded_entries:
                self._preloaded_pcm[keyword] = loaded_entries
        if not self._preloaded_pcm:
            print("[KWS] 没有预加载任何反应音频，触发时将走 play_wav")

    def _play_reaction_audio(self, keyword: str) -> float:
        """Play one randomly selected pre-loaded audio for the given keyword.

        Returns the actual duration of the selected audio in seconds, or 0.0
        when playback falls back to play_wav whose duration is not precomputed.
        """
        reaction = self.cfg.get("_reaction_map", {}).get(keyword, {})
        audio_path = reaction.get("audio_path", "")

        preloaded_list = self._preloaded_pcm.get(keyword)
        if preloaded_list:
            chosen = random.choice(preloaded_list)
            pcm, rate, channels, width, duration_s = chosen
            try:
                speaker.play_buffer(
                    pcm,
                    sample_rate=rate,
                    channels=channels,
                    sample_width=width,
                    volume=1.0,  # volume already baked during preloading
                    blocking=False,
                    on_first_sample=self._log_first_sample_latency,
                )
                print(f"[KWS] 播放 '{keyword}': {duration_s:.2f}s")
                return duration_s
            except Exception as exc:
                print(f"[KWS] '{keyword}' 预加载播放失败，回退到 play_wav: {exc}")

        # Fallback: try the legacy single audio_path.
        fallback_paths = _resolve_reaction_audio_paths(reaction)
        if fallback_paths:
            audio_path = fallback_paths[0]
        if audio_path and os.path.exists(audio_path):
            try:
                speaker.play_wav(audio_path, volume=self.volume, blocking=False)
                print(f"[KWS] '{keyword}' 走 play_wav 回退")
            except Exception as exc:
                print(f"[KWS] '{keyword}' 播放失败: {exc}")
        else:
            print(f"[KWS] '{keyword}' 未找到音频文件: {audio_path}")
        return 0.0

    def _log_first_sample_latency(self, ts_ns: int):
        """在首采样写入 ALSA 时打印相对关键词触发的时间差。"""
        if not self._last_detection_time:
            return
        latency_ms = (ts_ns - int(self._last_detection_time * 1_000_000_000)) / 1_000_000
        print(f"[KWS] 音频首采样延迟: {latency_ms:.1f} ms")

    def _trigger(self, keyword: str, confidence: float):
        """Play interjection audio and update eyes for the detected keyword."""
        now = time.time()
        with self._lock:
            if now - self._last_detection_time < self.cfg["cooldown_s"]:
                return
            self._last_detection_time = now
            self._last_keyword = keyword
            self._last_confidence = confidence
            self._metrics["detections"] += 1

        print(f"[KWS] 检测到关键词 '{keyword}' (confidence={confidence:.2f})")

        # Look up the reaction mapping for this keyword.
        reaction = self.cfg.get("_reaction_map", {}).get(keyword, {})
        eye_expression = reaction.get("eye_expression") or self.cfg.get("eye_expression")
        # Backward-compatible fallback: if no reaction map, use top-level config.
        if not reaction:
            eye_expression = self.cfg.get("eye_expression")

        # 1. Trigger eye reaction.
        if self.eye is not None:
            try:
                self.eye.on_fast_reaction_event(keyword)
                if eye_expression:
                    self.eye.set_expression(eye_expression)
            except Exception as exc:
                print(f"[KWS] 眼睛反应失败: {exc}")

        # 2. Play interjection audio (non-blocking) and get its duration.
        audio_duration = self._play_reaction_audio(keyword)

        # 2.5 During long audio playback, gate keyword detection so other
        # reactions do not interrupt the current audio or change eyes.
        if audio_duration > 0:
            with self._lock:
                self._audio_busy_until = time.time() + audio_duration

        # 3. Reset to normal idle/tracking after the expression finishes.
        # 表情持续时间取配置值与音频实际长度的较大者，避免"声音没完眼睛先回idle"。
        if self.eye is not None:
            try:
                min_duration = self.cfg.get("eye_expression_duration_s", 2.0)
                reset_after = max(min_duration, audio_duration)
                threading.Timer(reset_after, self._clear_expression).start()
                print(f"[KWS] 表情持续 {reset_after:.2f}s (min={min_duration:.2f}, audio={audio_duration:.2f})")
            except Exception as exc:
                print(f"[KWS] 设置表情复位定时器失败: {exc}")

        # 4. Reset streaming backend after a trigger so the next utterance starts
        # from a clean acoustic context. This prevents sherpa-onnx beam paths from
        # getting stuck in a previously triggered keyword state.
        try:
            self._backend.reset()
        except Exception as exc:
            print(f"[KWS] reset backend 失败: {exc}")

        # 5. External hook.
        if self.on_keyword is not None:
            try:
                self.on_keyword(keyword, confidence)
            except Exception as exc:
                print(f"[KWS] on_keyword 回调错误: {exc}")

    def _clear_expression(self):
        if self.eye is not None:
            try:
                self.eye.set_expression(None)
                self.eye.clear_fast_reaction_event()
            except Exception:
                pass

    def _loop(self):
        """Worker thread: consume audio frames and run KWS inference."""
        print(f"[KWS] 推理线程启动 (backend={self._backend.name}, "
              f"target_rate={self._target_rate}Hz, keyword='{self.keyword}')")
        while self._running:
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._consume_frame(frame)
            except Exception as exc:
                print(f"[KWS] 推理异常: {exc}")
        print("[KWS] 推理线程结束")

    def start(self):
        """Start the KWS worker thread and register the MicMonitor callback."""
        if self._running:
            return
        self._running = True
        self._reset_state()
        if self.mic:
            self.mic.register_audio_callback(self.on_audio_frame)
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="fast-reaction-kws"
        )
        self._thread.start()
        print(f"[KWS] 快反应模块已启动 (keyword='{self.keyword}', "
              f"backend={self._backend.name})")

    def stop(self):
        """Stop the worker thread and unregister the callback."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.mic:
            try:
                self.mic.unregister_audio_callback(self.on_audio_frame)
            except Exception:
                pass
        print("[KWS] 快反应模块已停止")

    def get_metrics(self) -> dict:
        """Return bounded metrics for /status and tests."""
        times = self._inference_times
        with self._lock:
            metrics = dict(self._metrics)
        if times:
            ordered = sorted(times)
            metrics["inference_ms_p50"] = round(
                ordered[int((len(ordered) - 1) * 0.50)], 2
            )
            metrics["inference_ms_p95"] = round(
                ordered[int((len(ordered) - 1) * 0.95)], 2
            )
        metrics["backend"] = self._backend.name
        metrics["keyword"] = self.keyword
        metrics["last_keyword"] = self._last_keyword
        metrics["last_confidence"] = round(self._last_confidence, 3)
        metrics["last_detection_age_s"] = round(
            time.time() - self._last_detection_time, 2
        ) if self._last_detection_time else None
        return metrics
