"""
ALSA-based audio recorder and WAV/MP3 playback wrapper.

This module no longer opens its own ALSA capture device. Instead, it consumes
audio frames from MicMonitor callbacks to avoid device exclusivity conflicts.
"""

import os
import wave
import queue
import threading
import numpy as np

from voice.config import (
    SAMPLE_RATE, CHANNELS, BLOCK_SIZE, DEVICE,
    VAD_THRESHOLD, SILENCE_DURATION, MAX_RECORDING_DURATION,
    SPEAKER_VOLUME,
)

# Reuse existing speaker module for playback
from speaker import play_wav


def _rms_level(data: np.ndarray) -> float:
    """Return normalized RMS level (0.0 - 1.0) for int32 samples."""
    if len(data) == 0:
        return 0.0
    peak = 2_147_483_647.0
    rms = np.sqrt(np.mean(data.astype(np.float64) ** 2))
    return min(rms / peak, 1.0)


class CallbackVoiceRecorder:
    """
    Threshold-based voice activity recorder that consumes MicMonitor callbacks.

    Usage:
        recorder = CallbackVoiceRecorder()
        mic_monitor.register_audio_callback(recorder.on_audio_frame)
        recorder.start()
        ...
        wav_path = recorder.stop_and_save()
        mic_monitor.unregister_audio_callback(recorder.on_audio_frame)
    """

    def __init__(
        self,
        threshold: float = VAD_THRESHOLD,
        silence_duration: float = SILENCE_DURATION,
        sample_rate: int = SAMPLE_RATE,
        max_duration: float = MAX_RECORDING_DURATION,
    ):
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate
        self.max_duration = max_duration
        self.block_size = BLOCK_SIZE

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1024)
        self._thread: threading.Thread | None = None
        self._running = False

        self._frames: list[np.ndarray] = []
        self._recording = False
        self._silence_blocks = 0
        self._silence_limit = int(silence_duration * sample_rate / BLOCK_SIZE)
        self._max_blocks = int(max_duration * sample_rate / BLOCK_SIZE)

    def on_audio_frame(self, left_channel: np.ndarray):
        """Called by MicMonitor for each audio frame. Must not block."""
        try:
            self._queue.put_nowait(left_channel.copy())
        except queue.Full:
            # Drop oldest frame if consumer cannot keep up
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(left_channel.copy())
            except queue.Empty:
                pass

    def start(self):
        """Start the consumer thread."""
        if self._running:
            return
        self._running = True
        self._frames = []
        self._recording = False
        self._silence_blocks = 0
        self._thread = threading.Thread(target=self._process, daemon=True, name="voice-recorder")
        self._thread.start()

    def stop(self):
        """Stop the consumer thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _process(self):
        """Consume queued audio frames and apply VAD."""
        print("[REC] 等待语音...")
        while self._running:
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            level = _rms_level(frame)

            if not self._recording:
                if level > self.threshold:
                    self._recording = True
                    self._frames.append(frame)
                    self._silence_blocks = 0
                    print(f"[REC] 检测到语音 (level={level:.4f}), 开始录制")
                continue

            self._frames.append(frame)
            if level > self.threshold:
                self._silence_blocks = 0
            else:
                self._silence_blocks += 1
                if self._silence_blocks >= self._silence_limit:
                    print(f"[REC] 静默 {self.silence_duration}s, 停止录制")
                    self._running = False
                    break

            if len(self._frames) >= self._max_blocks:
                print(f"[REC] 达到最大录制时长 {self.max_duration}s")
                self._running = False
                break

    def stop_and_save(self, output_path: str) -> str | None:
        """Stop recording and save to a 16-bit mono WAV file."""
        self.stop()

        # Drain remaining frames
        while not self._queue.empty():
            try:
                frame = self._queue.get_nowait()
                if self._recording:
                    self._frames.append(frame)
            except queue.Empty:
                break

        if not self._frames:
            print("[REC] 未录制到任何音频")
            return None

        all_data = np.concatenate(self._frames)
        peak = np.max(np.abs(all_data))
        if peak > 0:
            scaled = (all_data.astype(np.float64) / peak * 32767).astype(np.int16)
        else:
            scaled = all_data.astype(np.int16)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with wave.open(output_path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(scaled.tobytes())

        duration = len(scaled) / self.sample_rate
        print(f"[REC] 已保存 {output_path} ({duration:.2f}s)")
        return output_path


def play_audio(filepath: str, blocking: bool = True, volume: float = SPEAKER_VOLUME):
    """
    Play a WAV or MP3 file using the existing speaker module.
    MP3 files are decoded with pydub (requires ffmpeg on the Pi).
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".wav", ".mp3"):
        return play_wav(filepath, blocking=blocking, volume=volume)

    raise ValueError(f"Unsupported audio format: {ext}")


# Backwards-compatible alias for old code that imported VoiceRecorder.
# The new implementation requires a MicMonitor callback.
VoiceRecorder = CallbackVoiceRecorder
