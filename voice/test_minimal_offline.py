"""Minimal offline voice test: VAD -> record -> random preset emotion audio."""
import os
import random
import time

import numpy as np

# Make sure module paths resolve on the Pi when running from project root
import sys
sys.path.insert(0, "/home/pi/mic_speaker_camera_test")

from voice.config import (
    AUDIO_DIR, SAMPLE_RATE,
    SILENCE_DURATION, MAX_RECORDING_DURATION,
    SPEAKER_VOLUME,
)
from voice.audio import CallbackVoiceRecorder, play_audio
from mic import MicMonitor, find_i2s_device

# Software gain must be high enough for quiet speech but not so high that
# ambient noise continuously triggers VAD.
MIC_GAIN = 32.0


def _rms_level(data: np.ndarray) -> float:
    """Return normalized RMS level (0.0 - 1.0) for int32 samples."""
    if len(data) == 0:
        return 0.0
    peak = 2_147_483_647.0
    rms = np.sqrt(np.mean(data.astype(np.float64) ** 2))
    return min(rms / peak, 1.0)


def calibrate_noise_floor(mic: MicMonitor, seconds: float = 1.0) -> float:
    """Measure ambient noise level after mic starts."""
    print(f"[OFFLINE] 校准环境噪声 {seconds}s，请保持安静...")
    start = time.time()
    levels = []
    while time.time() - start < seconds:
        # mic.rms is the raw int32 RMS (not normalized)
        levels.append(mic.rms)
        time.sleep(0.05)
    floor = float(np.median(levels)) if levels else 0.0
    normalized = floor / 2_147_483_647.0
    print(f"[OFFLINE] 环境噪声基线: {normalized:.4f} (raw={floor:.0f})")
    return normalized


def record_one():
    """Use MicMonitor (with software gain) to feed VAD and record a clip."""
    device = find_i2s_device()
    mic = MicMonitor(device=device, gain=MIC_GAIN)
    mic.start()

    # Calibrate threshold against current ambient noise.
    floor = calibrate_noise_floor(mic, seconds=1.0)
    threshold = max(floor * 4.0, 0.08)
    print(f"[OFFLINE] VAD 阈值设置为 {threshold:.4f}")

    recorder = CallbackVoiceRecorder(
        threshold=threshold,
        silence_duration=SILENCE_DURATION,
        sample_rate=SAMPLE_RATE,
        max_duration=MAX_RECORDING_DURATION,
    )
    mic.register_audio_callback(recorder.on_audio_frame)

    recorder.start()
    print("[OFFLINE] 等待语音...")

    try:
        while recorder._running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[OFFLINE] 被用户中断")
    finally:
        mic.unregister_audio_callback(recorder.on_audio_frame)
        mic.stop()

    wav_path = "/tmp/offline_recording.wav"
    result = recorder.stop_and_save(wav_path)
    time.sleep(0.5)  # let ALSA release capture device before playback
    return result


def play_random_emotion():
    emotions = [d for d in os.listdir(AUDIO_DIR) if os.path.isdir(os.path.join(AUDIO_DIR, d))]
    emotion = random.choice(emotions)
    folder = os.path.join(AUDIO_DIR, emotion)
    files = [f for f in os.listdir(folder) if f.lower().endswith((".mp3", ".wav"))]
    if not files:
        print(f"[OFFLINE] 情绪 {emotion} 没有音频文件")
        return
    path = os.path.join(folder, random.choice(files))
    print(f"[OFFLINE] 播放情绪音频: {emotion}/{os.path.basename(path)}")
    play_audio(path, blocking=True, volume=SPEAKER_VOLUME)


if __name__ == "__main__":
    print("=" * 50)
    print("无联网最小语音测试")
    print("流程: 检测声音 -> 录音 -> 随机播放一个情绪音频")
    print("按 Ctrl+C 退出")
    print("=" * 50)

    try:
        while True:
            wav_path = record_one()
            if wav_path:
                print(f"[OFFLINE] 已录音: {wav_path}")
            else:
                print("[OFFLINE] 未录音")
            play_random_emotion()
            print("-" * 50)
    except KeyboardInterrupt:
        print("\n[OFFLINE] 退出")
