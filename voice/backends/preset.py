"""
Preset emotion audio backend.

Plays pre-recorded MP3/WAV files from audio_assets/vo/.
Supports both flat layout (minions style: 009_calmness_i1.mp3) and
subdirectory layout (audio_assets/vo/<emotion>/file.mp3).
"""

import os
import random
import re

from voice.config import AUDIO_DIR
from voice.backends.base import TTSBackend
from speaker import play_wav


class PresetAudioTTS(TTSBackend):
    """TTS backend that plays pre-recorded emotion audio files."""

    # Match flat filename pattern: <index>_<emotion>_<variant>.mp3
    # e.g. 009_calmness_i1.mp3, 144_calmness_i1.mp3
    _FLAT_PATTERN = re.compile(r"^\d+_([a-z]+)_\w+\.mp3$", re.IGNORECASE)

    def __init__(self, audio_dir: str = AUDIO_DIR, volume: float = 0.5):
        self.audio_dir = audio_dir
        self.volume = volume
        self._first_sample_callback = None
        self._finished_callback = None
        self._flat_cache: dict[str, list[str]] = {}
        self._song_cache: list[str] = []
        self._scan_flat_files()
        self._scan_songs()

    def set_first_sample_callback(self, callback):
        """Set a one-playback callback used by the Day 3 response timing path."""
        self._first_sample_callback = callback

    def set_finished_callback(self, callback):
        """Set a callback invoked by the shared audio worker after playback ends."""
        self._finished_callback = callback

    def _scan_flat_files(self):
        """Scan audio_dir recursively for flat-format files and group by emotion."""
        for root, dirs, files in os.walk(self.audio_dir):
            for fname in files:
                m = self._FLAT_PATTERN.match(fname)
                if m:
                    emotion = m.group(1).lower()
                    path = os.path.join(root, fname)
                    self._flat_cache.setdefault(emotion, []).append(path)

    def _scan_songs(self):
        """Scan audio_dir/sing/ recursively for song files (wav/mp3)."""
        sing_dir = os.path.join(self.audio_dir, "sing")
        if not os.path.isdir(sing_dir):
            return
        for root, dirs, files in os.walk(sing_dir):
            for fname in files:
                if fname.lower().endswith((".mp3", ".wav")):
                    self._song_cache.append(os.path.join(root, fname))

    def list_songs(self) -> list[str]:
        """Return all available song files."""
        return list(self._song_cache)

    def play_random_song(self) -> bool:
        """Play a random song from the sing/ pool."""
        if not self._song_cache:
            print("[TTS] 未找到歌曲文件")
            return False
        path = random.choice(self._song_cache)
        print(f"[TTS] 播放歌曲: {os.path.basename(path)}")
        return self.speak_file(path)

    @property
    def name(self) -> str:
        return "preset-audio"

    def _list_files(self, emotion: str) -> list[str]:
        """List audio files for a given emotion (case-insensitive)."""
        emotion_lower = emotion.lower()

        # 1. Subdirectory mode (case-insensitive match)
        for entry in os.listdir(self.audio_dir):
            if entry.lower() == emotion_lower:
                folder = os.path.join(self.audio_dir, entry)
                if os.path.isdir(folder):
                    files = [
                        os.path.join(folder, f)
                        for f in os.listdir(folder)
                        if f.lower().endswith((".mp3", ".wav"))
                    ]
                    if files:
                        return files
                    break

        # 2. Flat-file mode: emotion in filename
        if emotion_lower in self._flat_cache:
            return self._flat_cache[emotion_lower]

        return []

    def list_emotions(self) -> list[str]:
        """Return all available emotion names (from both subdirs and flat files)."""
        emotions = set(self._flat_cache.keys())

        # From subdirectories: only add dir names that have non-flat files
        for entry in os.listdir(self.audio_dir):
            path = os.path.join(self.audio_dir, entry)
            if os.path.isdir(path):
                has_non_flat = any(
                    not self._FLAT_PATTERN.match(f)
                    for f in os.listdir(path)
                    if f.lower().endswith((".mp3", ".wav"))
                )
                if has_non_flat:
                    emotions.add(entry.lower())

        return sorted(emotions)

    def speak(self, text: str, emotion: str | None = None) -> bool:
        """Ignore text, play a random file for the given emotion."""
        if not emotion:
            emotion = "calmness"
        files = self._list_files(emotion)
        if not files:
            print(f"[TTS] 未找到情绪音频: {emotion}")
            return False
        path = random.choice(files)
        print(f"[TTS] 播放预录音频: {os.path.basename(path)}")
        return self.speak_file(path)

    def speak_file(self, path: str) -> bool:
        try:
            ext = os.path.splitext(path)[1].lower()
            # speaker.play_wav uses ffmpeg as a streaming decoder despite its
            # historical name, so WAV and MP3 both avoid whole-file decoding
            # in the voice/VAD caller thread.
            if ext in (".wav", ".mp3"):
                return play_wav(
                    path, volume=self.volume, blocking=False,
                    on_first_sample=self._first_sample_callback,
                    on_finished=self._finished_callback,
                )

            print(f"[TTS] 不支持的音频格式: {path}")
            return False
        except Exception as e:
            print(f"[TTS] 播放失败: {e}")
            return False
