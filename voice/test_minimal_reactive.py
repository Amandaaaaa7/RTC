#!/usr/bin/env python3
"""
Minimal reactive voice test: detect speech, wait for silence, then play a
random preset emotion audio clip.

Usage:
    python3 voice/test_minimal_reactive.py
    python3 voice/test_minimal_reactive.py --threshold 0.03 --silence 1.0
    python3 voice/test_minimal_reactive.py --volume 0.5 --emotion Joy

Flow:
    1. MicMonitor captures audio.
    2. When RMS exceeds threshold, we enter "listening" state.
    3. When RMS stays below threshold for `silence` seconds, we consider the
       utterance finished and trigger playback.
    4. A random preset audio file is played (or a specific emotion if given).
    5. Loop back to step 1.

No audio is saved, no ASR/LLM is used, no network required.
"""

import argparse
import os
import queue
import random
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mic import MicMonitor, find_i2s_device
from voice.backends.preset import PresetAudioTTS
from voice.config import AUDIO_DIR, SAMPLE_RATE, BLOCK_SIZE


def _rms_level(frame: np.ndarray) -> float:
    """Normalized RMS level (0.0 - 1.0) for int32 samples."""
    if len(frame) == 0:
        return 0.0
    peak = 2_147_483_647.0
    rms = np.sqrt(np.mean(frame.astype(np.float64) ** 2))
    return min(rms / peak, 1.0)


class ReactiveVoiceTrigger:
    """
    Consumes MicMonitor audio frames and triggers when speech starts and stops.

    Does not save recordings; it only detects the start/end of an utterance.
    """

    def __init__(self, threshold: float = 0.05, silence_duration: float = 1.5):
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.block_size = BLOCK_SIZE
        self.sample_rate = SAMPLE_RATE

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1024)
        self._thread: threading.Thread | None = None
        self._running = False
        self._silence_limit = int(silence_duration * SAMPLE_RATE / BLOCK_SIZE)

    def on_audio_frame(self, frame: np.ndarray):
        """Called by MicMonitor for each audio frame."""
        try:
            self._queue.put_nowait(frame.copy())
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(frame.copy())
            except queue.Empty:
                pass

    def start(self):
        """Start the detection thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process, daemon=True, name="reactive-trigger")
        self._thread.start()

    def stop(self):
        """Stop the detection thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _process(self):
        """Wait for speech -> wait for silence -> notify."""
        while self._running:
            # Phase 1: wait for voice activity
            print("[VAD] 等待说话...")
            while self._running:
                try:
                    frame = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                level = _rms_level(frame)
                if level > self.threshold:
                    print(f"[VAD] 检测到声音 (level={level:.4f})，开始监听...")
                    silence_blocks = 0
                    # Phase 2: wait for silence
                    while self._running:
                        try:
                            frame = self._queue.get(timeout=0.1)
                        except queue.Empty:
                            continue

                        level = _rms_level(frame)
                        if level > self.threshold:
                            silence_blocks = 0
                        else:
                            silence_blocks += 1
                            if silence_blocks >= self._silence_limit:
                                print(f"[VAD] 静音 {self.silence_duration}s，说话结束，触发播放")
                                self._on_trigger()
                                break
                    break

    def _on_trigger(self):
        """Override or replace via callback to act on speech end."""
        pass


def parse_args():
    parser = argparse.ArgumentParser(description="Minimal reactive voice-to-audio loop")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="RMS threshold to trigger speech detection (0-1, default 0.5)")
    parser.add_argument("--silence", type=float, default=0.5,
                        help="Seconds of silence before triggering playback (default 0.5)")
    parser.add_argument("--volume", type=float, default=0.3,
                        help="Playback volume (0-1, default 0.3)")
    parser.add_argument("--emotion", type=str, default=None,
                        help="Specific emotion to play; random if omitted")
    parser.add_argument("--mic-gain", type=float, default=8.0,
                        help="Microphone software gain (default 8.0)")
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 50)
    print("最小反应式语音测试")
    print(f"阈值: {args.threshold}, 静音结束: {args.silence}s, 音量: {args.volume}")
    print("按 Ctrl+C 退出")
    print("=" * 50)

    device = find_i2s_device()
    mic = MicMonitor(device=device, gain=args.mic_gain)
    tts = PresetAudioTTS(audio_dir=AUDIO_DIR, volume=args.volume)

    trigger = ReactiveVoiceTrigger(threshold=args.threshold, silence_duration=args.silence)

    playback_lock = threading.Lock()
    playback_event = threading.Event()

    def on_trigger():
        # Avoid overlapping playback
        if not playback_lock.acquire(blocking=False):
            return
        try:
            print("[PLAY] 正在播放...")
            if args.emotion:
                tts.speak("", emotion=args.emotion)
            else:
                # Pick a random existing emotion folder
                emotions = [
                    d for d in os.listdir(AUDIO_DIR)
                    if os.path.isdir(os.path.join(AUDIO_DIR, d))
                ]
                if emotions:
                    emotion = random.choice(emotions)
                    tts.speak("", emotion=emotion)
                else:
                    print("[PLAY] 未找到情绪音频目录")
            print("[PLAY] 播放结束，继续监听")
        finally:
            playback_lock.release()

    trigger._on_trigger = on_trigger

    mic.register_audio_callback(trigger.on_audio_frame)
    mic.start()
    trigger.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[EXIT] 正在停止...")
    finally:
        trigger.stop()
        mic.stop()


if __name__ == "__main__":
    main()
