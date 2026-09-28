"""Voice interaction module for pi_affe_sys."""

__all__ = ["VoiceModule", "VoiceState"]


def __getattr__(name):
    """Do not require cloud ASR/LLM packages for offline reactive playback."""
    if name == "VoiceModule":
        from voice.module import VoiceModule
        return VoiceModule
    if name == "VoiceState":
        from voice.backends.base import VoiceState
        return VoiceState
    raise AttributeError(name)
