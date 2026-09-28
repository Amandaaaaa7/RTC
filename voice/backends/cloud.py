"""
Cloud-based ASR/LLM/TTS backends.

- DashScope ASR (Paraformer) for Chinese speech recognition.
- DashScope Qwen for emotion analysis.
- Edge TTS as an optional dynamic TTS fallback.
"""

import os
import random
import tempfile
import subprocess
from pathlib import Path

import dashscope
from dashscope import Generation
from dashscope.audio.asr import Recognition

from voice.config import QIANWEN_API_KEY, SAMPLE_RATE
from voice.backends.base import ASRBackend, LLMBackend, TTSBackend
from speaker import play_wav


EMOTION_PROMPT = """\
请分析以下用户话语的情绪，只返回一个最匹配的情绪标签。
可选标签（严格从以下列表中选择）：
{emotions}

用户话语："{text}"
情绪标签："""


def _dashscope_api_key() -> str:
    key = QIANWEN_API_KEY
    if not key or key == "YOUR_QIANWEN_API_KEY_HERE":
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    return key


class DashScopeASR(ASRBackend):
    """DashScope Paraformer ASR (zh-CN)."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or _dashscope_api_key()
        dashscope.api_key = self._api_key

    @property
    def name(self) -> str:
        return "dashscope-asr"

    def transcribe(self, wav_path: str, language: str = "zh-CN") -> str:
        try:
            # Use synchronous recognition for short utterances
            recognition = Recognition(
                model="paraformer-realtime-v1",
                format="wav",
                sample_rate=SAMPLE_RATE,
                callback=None,
            )
            # The Recognition class has a .call() method for file input
            result = recognition.call(wav_path)
            if result and result.status_code == 200:
                text = ""
                for sentence in result.get_sentence() or []:
                    text += sentence.text
                print(f"[ASR] DashScope 识别结果: {text}")
                return text.strip()
            else:
                code = result.status_code if result else "None"
                print(f"[ASR] DashScope ASR 错误: {code}")
                return ""
        except Exception as e:
            print(f"[ASR] DashScope ASR 异常: {e}")
            return ""


class GoogleASR(ASRBackend):
    """Google Speech Recognition fallback (requires internet)."""

    def __init__(self):
        import speech_recognition as sr
        self._recognizer = sr.Recognizer()

    @property
    def name(self) -> str:
        return "google-asr"

    def transcribe(self, wav_path: str, language: str = "zh-CN") -> str:
        import speech_recognition as sr
        try:
            with sr.AudioFile(wav_path) as source:
                audio = self._recognizer.record(source)
            text = self._recognizer.recognize_google(audio, language=language)
            print(f"[ASR] Google 识别结果: {text}")
            return text
        except sr.UnknownValueError:
            print("[ASR] Google 无法识别语音")
            return ""
        except sr.RequestError as e:
            print(f"[ASR] Google 请求错误: {e}")
            return ""


class DashScopeLLM(LLMBackend):
    """DashScope Qwen-plus emotion analysis."""

    def __init__(self, api_key: str | None = None, model: str = "qwen-plus"):
        self._api_key = api_key or _dashscope_api_key()
        self._model = model
        dashscope.api_key = self._api_key

    @property
    def name(self) -> str:
        return "dashscope-llm"

    def analyze_emotion(self, text: str, allowed_emotions: list[str]) -> str:
        if not text:
            return allowed_emotions[0] if allowed_emotions else "Calmness"

        prompt = EMOTION_PROMPT.format(
            emotions=", ".join(allowed_emotions),
            text=text,
        )
        try:
            response = Generation.call(
                model=self._model,
                messages=[
                    {"role": "system", "content": "你只返回一个情绪标签，不要解释。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=10,
                temperature=0.0,
            )
            if response.status_code == 200:
                label = response.output.text.strip().strip('"').strip("'").strip()
                if label in allowed_emotions:
                    print(f"[LLM] 情绪分析: {label}")
                    return label
                else:
                    print(f"[LLM] 返回未知情绪 '{label}', 回退到 {allowed_emotions[0]}")
                    return allowed_emotions[0]
            else:
                print(f"[LLM] API 错误: {response.status_code}")
                return allowed_emotions[0] if allowed_emotions else "Calmness"
        except Exception as e:
            print(f"[LLM] 调用异常: {e}")
            return allowed_emotions[0] if allowed_emotions else "Calmness"


class EdgeTTSBackend(TTSBackend):
    """Edge TTS fallback for dynamic text (requires internet)."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", volume: float = 0.5):
        self.voice = voice
        self.volume = volume
        self._first_sample_callback = None
        self._finished_callback = None

    def set_first_sample_callback(self, callback):
        self._first_sample_callback = callback

    def set_finished_callback(self, callback):
        self._finished_callback = callback

    @property
    def name(self) -> str:
        return "edge-tts"

    def speak(self, text: str, emotion: str | None = None) -> bool:
        try:
            import edge_tts
        except ImportError:
            print("[TTS] Edge TTS 未安装，跳过")
            return False

        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            communicate = edge_tts.Communicate(text, self.voice)
            communicate.save_sync(tmp.name)
            return self.speak_file(tmp.name)
        except Exception as e:
            print(f"[TTS] Edge TTS 失败: {e}")
            return False

    def speak_file(self, path: str) -> bool:
        try:
            return play_wav(
                path, volume=self.volume, blocking=False,
                on_first_sample=self._first_sample_callback,
                on_finished=self._finished_callback,
            )
        except Exception as e:
            print(f"[TTS] 播放失败: {e}")
            return False
