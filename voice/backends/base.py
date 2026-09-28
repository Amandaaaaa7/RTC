"""
Voice backend abstract interfaces.

All ASR/LLM/TTS backends implement these base classes so that VoiceModule
can switch between cloud and local implementations without code changes.
"""

from abc import ABC, abstractmethod
from typing import Tuple


class ASRBackend(ABC):
    """Automatic Speech Recognition backend."""

    @abstractmethod
    def transcribe(self, wav_path: str, language: str = "zh-CN") -> str:
        """
        Transcribe a WAV file to text.

        Args:
            wav_path: Path to a mono WAV file.
            language: Language code.

        Returns:
            Recognized text, or empty string on failure.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend display name."""
        ...


class LLMBackend(ABC):
    """Large Language Model backend for emotion / intent analysis."""

    @abstractmethod
    def analyze_emotion(self, text: str, allowed_emotions: list[str]) -> str:
        """
        Analyze the emotion of the given text.

        Args:
            text: User utterance.
            allowed_emotions: List of valid emotion labels.

        Returns:
            One emotion label from allowed_emotions, or a safe fallback.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend display name."""
        ...


class TTSBackend(ABC):
    """Text-to-Speech backend."""

    @abstractmethod
    def speak(self, text: str, emotion: str | None = None) -> bool:
        """
        Synthesize and play the given text.

        Args:
            text: Text to speak.
            emotion: Optional emotion hint.

        Returns:
            True if playback started, False on failure.
        """
        ...

    @abstractmethod
    def speak_file(self, path: str) -> bool:
        """Play an existing audio file."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend display name."""
        ...


class VoiceState:
    """Immutable voice interaction state snapshot."""

    IDLE = "idle"
    VAD_TRIGGERED = "vad_triggered"
    RECORDING = "recording"
    ASR_PENDING = "asr_pending"
    LLM_PENDING = "llm_pending"
    TTS_PENDING = "tts_pending"
    SPEAKING = "speaking"
    ERROR = "error"

    def __init__(self, state: str = IDLE, emotion: str | None = None, text: str | None = None):
        self.state = state
        self.emotion = emotion
        self.text = text

    def __repr__(self) -> str:
        return f"VoiceState(state={self.state}, emotion={self.emotion}, text={self.text})"
