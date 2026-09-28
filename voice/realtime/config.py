"""Configuration for the opt-in real-time voice path.

Secrets can be supplied by environment variables or by an ignored local JSON
file on the robot. Environment variables take precedence for managed deploys.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VolcDirectConfig:
    voice_api_key: str
    asr_resource_id: str
    tts_resource_id: str
    tts_voice_type: str
    tts_sample_rate: int
    ark_api_key: str
    ark_model: str
    ark_base_url: str
    ark_thinking: str
    system_prompt: str

    def missing_settings(self) -> list[str]:
        values = {
            "VOLC_VOICE_API_KEY": self.voice_api_key,
            "ARK_API_KEY": self.ark_api_key,
            "ARK_MODEL": self.ark_model,
        }
        return [key for key, value in values.items() if not value or value.startswith("YOUR_")]


@dataclass(frozen=True)
class LocalMemoryConfig:
    """Local-only text context and daily journal settings for real-time voice."""

    enabled: bool
    max_context_turns: int
    store_path: Path
    journal_dir: Path


@dataclass(frozen=True)
class RealtimeVoiceConfig:
    enabled: bool
    vad_threshold: float
    silence_duration_s: float
    backend: str
    pause_callbacks_during_playback: bool
    welcome_message: str
    direct: VolcDirectConfig
    local_memory: LocalMemoryConfig

    @classmethod
    def from_file(cls, path: str | Path) -> "RealtimeVoiceConfig":
        config_path = Path(path)
        with config_path.open(encoding="utf-8") as config_file:
            raw = json.load(config_file)
        direct_raw = raw.get("volc_direct", {})
        memory_raw = raw.get("local_memory", {})
        scene_raw = raw.get("scene", {})
        project_root = Path(__file__).parents[2]

        def secret(environment_name: str, config_name: str) -> str:
            """Prefer deployment environment variables over an ignored local file."""
            return os.environ.get(environment_name) or str(direct_raw.get(config_name, ""))

        def local_path(name: str, default: str) -> Path:
            candidate = Path(str(memory_raw.get(name, default)))
            return candidate if candidate.is_absolute() else project_root / candidate

        direct = VolcDirectConfig(
            voice_api_key=secret("VOLC_VOICE_API_KEY", "voice_api_key"),
            asr_resource_id=str(direct_raw.get("asr_resource_id", "volc.seedasr.sauc.duration")),
            tts_resource_id=str(direct_raw.get("tts_resource_id", "seed-tts-2.0")),
            tts_voice_type=str(direct_raw.get("tts_voice_type", "zh_female_vv_uranus_bigtts")),
            tts_sample_rate=int(direct_raw.get("tts_sample_rate", 24000)),
            ark_api_key=secret("ARK_API_KEY", "ark_api_key"),
            ark_model=secret("ARK_MODEL", "ark_model"),
            ark_base_url=str(direct_raw.get("ark_base_url", "https://ark.cn-beijing.volces.com/api/v3")),
            ark_thinking=str(direct_raw.get("ark_thinking", "disabled")),
            system_prompt=str(direct_raw.get("system_prompt", "你是一个陪伴型玩偶机器人。回答简短、自然、诚实，不编造事实。")),
        )
        return cls(
            enabled=bool(raw.get("enabled", False)),
            # This is an absolute lower bound. VoiceEventSource additionally
            # calibrates room noise and derives a higher effective threshold.
            vad_threshold=float(raw.get("vad_threshold", 0.02)),
            silence_duration_s=float(raw.get("silence_duration_s", 0.8)),
            backend=str(raw.get("backend", "volc_direct")),
            pause_callbacks_during_playback=bool(raw.get("pause_callbacks_during_playback", True)),
            welcome_message=str(raw.get(
                "welcome_message",
                scene_raw.get("welcome_message", "你好！我是圆宝，我来啦我来啦。"),
            )).strip(),
            direct=direct,
            local_memory=LocalMemoryConfig(
                enabled=bool(memory_raw.get("enabled", True)),
                max_context_turns=max(1, int(memory_raw.get("max_context_turns", 10))),
                store_path=local_path("store_path", "outputs/realtime_voice/memory.json"),
                journal_dir=local_path("journal_dir", "outputs/realtime_voice/journal"),
            ),
        )
