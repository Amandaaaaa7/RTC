"""Voice emotion dialogue module configuration."""

import os

# Project root (parent of voice/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DashScope / Qwen API key — set via environment or local config file.
# DO NOT hardcode real keys here; this file is tracked by git.
QIANWEN_API_KEY = os.environ.get(
    "DASHSCOPE_API_KEY",
    os.environ.get("QIANWEN_API_KEY", "YOUR_QIANWEN_API_KEY_HERE")
)

# Optional local override: create voice/.env with DASHSCOPE_API_KEY=...
_ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DASHSCOPE_API_KEY" and value.strip():
                QIANWEN_API_KEY = value.strip().strip('"').strip("'")

# Directory with emotion audio files (MP3 or WAV)
AUDIO_DIR = os.path.join(PROJECT_ROOT, "audio_assets", "vo")

# Output / cache directories
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")
RECORD_PATH = os.path.join(OUTPUTS_DIR, "user_recording.wav")
MEMORY_PATH = os.path.join(OUTPUTS_DIR, "memory.json")
XP_PATH = os.path.join(OUTPUTS_DIR, "xp.json")

# ALSA device for I2S capture / playback
DEVICE = "hw:1,0"

# Recording parameters
SAMPLE_RATE = 48000
CHANNELS = 2
FORMAT = "S32_LE"
BLOCK_SIZE = 2048

# VAD thresholds
VAD_THRESHOLD = 0.05      # RMS level to trigger start (0-1 normalized)
SILENCE_DURATION = 1.5    # seconds of silence to stop recording
MAX_RECORDING_DURATION = 30.0  # hard cap to avoid huge files

# Backend selection
DEFAULT_ASR_BACKEND = os.environ.get("VOICE_ASR_BACKEND", "dashscope")
DEFAULT_LLM_BACKEND = os.environ.get("VOICE_LLM_BACKEND", "dashscope")
DEFAULT_TTS_BACKEND = os.environ.get("VOICE_TTS_BACKEND", "preset")

# Speaker volume
SPEAKER_VOLUME = float(os.environ.get("VOICE_SPEAKER_VOLUME", "0.5"))
