"""Voice backend implementations."""

from voice.backends.base import ASRBackend, LLMBackend, TTSBackend, VoiceState

__all__ = [
    "ASRBackend",
    "LLMBackend",
    "TTSBackend",
    "VoiceState",
    "DashScopeASR",
    "GoogleASR",
    "DashScopeLLM",
    "EdgeTTSBackend",
    "PresetAudioTTS",
]


def __getattr__(name):
    """Keep offline preset playback importable without optional cloud packages."""
    if name in {"DashScopeASR", "GoogleASR", "DashScopeLLM", "EdgeTTSBackend"}:
        from voice.backends import cloud
        return getattr(cloud, name)
    if name == "PresetAudioTTS":
        from voice.backends.preset import PresetAudioTTS
        return PresetAudioTTS
    raise AttributeError(name)
