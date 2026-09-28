"""豆包流式 ASR + 方舟流式 LLM + 豆包 TTS backend.

This is intentionally a backend implementation, not a Flask application or a
hardware driver. It consumes MicMonitor frames through the orchestrator and
returns normalized DialogueEvent objects.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import inspect
import json
import queue
import struct
import threading
import time
import uuid
from typing import Any, Callable

import numpy as np

from ..audio_format import Capture48kTo16k
from ..config import VolcDirectConfig
from ..contracts import DialogueEvent
from ..dialogue_backend import DialogueBackend, DialogueEventCallback

ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def _v3_header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes([(1 << 4) | 1, (message_type << 4) | flags, (serialization << 4) | compression, 0])


def _v3_full_request(payload: dict[str, Any], sequence: int) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return _v3_header(1, 1, 1, 1) + struct.pack(">i", sequence) + struct.pack(">I", len(body)) + body


def _v3_audio_request(pcm: bytes, sequence: int, is_last: bool = False) -> bytes:
    body = gzip.compress(pcm)
    flags = 3 if is_last else 1
    packet_sequence = -sequence if is_last else sequence
    return _v3_header(2, flags, 0, 1) + struct.pack(">i", packet_sequence) + struct.pack(">I", len(body)) + body


def _decode_v3_response(raw: bytes) -> dict[str, Any]:
    if len(raw) < 4:
        return {}
    header_size = (raw[0] & 0x0F) * 4
    if header_size < 4 or len(raw) < header_size:
        return {}
    message_type, flags = raw[1] >> 4, raw[1] & 0x0F
    serialization, compression = raw[2] >> 4, raw[2] & 0x0F
    position = header_size
    if flags & 0x01:
        position += 4
    if flags & 0x04:
        position += 4
    code = 0
    if message_type == 0x0F:
        if len(raw) < position + 4:
            return {}
        code = struct.unpack(">i", raw[position:position + 4])[0]
        position += 4
    if len(raw) < position + 4:
        return {}
    payload_size = struct.unpack(">I", raw[position:position + 4])[0]
    payload = raw[position + 4:position + 4 + payload_size]
    if compression == 1:
        payload = gzip.decompress(payload)
    if serialization == 1 and payload:
        decoded = json.loads(payload.decode("utf-8"))
        if isinstance(decoded, dict) and code and "code" not in decoded:
            decoded["code"] = code
        return decoded
    return {}


class _AsrSession:
    """One short utterance ASR WebSocket session; it never owns microphone ALSA."""

    def __init__(self, api_key: str, resource_id: str, on_text: Callable[[str], None],
                 on_error: Callable[[Exception], None]) -> None:
        self.api_key = api_key
        self.resource_id = resource_id
        self.on_text = on_text
        self.on_error = on_error
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=80)
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_thread, daemon=True, name="volc-direct-asr")
        self._thread.start()

    def feed(self, pcm: bytes) -> None:
        if self._closed.is_set() or not pcm:
            return
        try:
            self._queue.put_nowait(pcm)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(pcm)
            except queue.Empty:
                pass

    def finish(self) -> None:
        if not self._closed.is_set():
            self._closed.set()
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(None)
                except queue.Empty:
                    pass

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.on_error(exc)

    async def _run(self) -> None:
        import websockets

        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        header_option = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
        options = {header_option: headers, "ping_interval": 20, "max_size": 2 ** 22, "open_timeout": 30}
        request_payload = {
            "user": {"uid": f"doll_{uuid.uuid4().hex[:12]}"},
            "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel", "enable_itn": True, "enable_punc": True,
                "enable_ddc": True, "show_utterances": True, "enable_nonstream": True,
                "end_window_size": 800,
            },
        }
        async with websockets.connect(ASR_URL, **options) as ws:
            sequence = 1
            await ws.send(_v3_full_request(request_payload, sequence))
            sequence += 1
            receiver = asyncio.create_task(self._receive(ws))
            while True:
                pcm = await asyncio.to_thread(self._queue.get)
                if pcm is None:
                    await ws.send(_v3_audio_request(b"", sequence, is_last=True))
                    break
                await ws.send(_v3_audio_request(pcm, sequence))
                sequence += 1
            try:
                await asyncio.wait_for(receiver, timeout=5)
            except asyncio.TimeoutError:
                receiver.cancel()

    async def _receive(self, ws: Any) -> None:
        delivered: set[str] = set()
        async for message in ws:
            response = json.loads(message) if isinstance(message, str) else _decode_v3_response(message)
            if response.get("code", 0) != 0:
                raise RuntimeError(str(response.get("message", response)))
            body = response.get("payload_msg")
            if not isinstance(body, dict):
                body = response
            result = body.get("result", {})
            if isinstance(result, list):
                result = result[0] if result else {}
            for item in result.get("utterances", []) or []:
                if not isinstance(item, dict) or not item.get("definite"):
                    continue
                text = str(item.get("text", "")).strip()
                key = f"{item.get('start_time', '')}|{item.get('end_time', '')}|{text}"
                if text and key not in delivered:
                    delivered.add(key)
                    self.on_text(text)


class VolcDirectDialogueBackend(DialogueBackend):
    """In-process direct-cloud backend; it can later be swapped for RTC."""

    def __init__(self, config: VolcDirectConfig) -> None:
        self.config = config
        self._callback: DialogueEventCallback | None = None
        self._running = False
        self._lock = threading.RLock()
        self._turn_id: int | None = None
        self._trace_id = ""
        self._session: _AsrSession | None = None
        self._converter = Capture48kTo16k()
        self._cancelled: set[int] = set()
        self._answered_turns: set[int] = set()
        self._history: list[dict[str, str]] = []
        self._state = "idle"

    def set_event_callback(self, callback: DialogueEventCallback) -> None:
        self._callback = callback

    def start(self) -> None:
        missing = self.config.missing_settings()
        if missing:
            raise ValueError("实时语音缺少环境变量：" + "、".join(missing))
        self._running = True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            session, self._session = self._session, None
            if self._turn_id is not None:
                self._cancelled.add(self._turn_id)
            self._turn_id = None
            self._state = "idle"
        if session:
            session.finish()

    def start_turn(self, turn_id: int, trace_id: str) -> None:
        with self._lock:
            if not self._running:
                raise RuntimeError("backend is not running")
            old_turn, old_session = self._turn_id, self._session
            if old_turn is not None:
                self._cancelled.add(old_turn)
            self._turn_id, self._trace_id = turn_id, trace_id
            self._session = _AsrSession(
                self.config.voice_api_key,
                self.config.asr_resource_id,
                lambda text: self._on_transcript(turn_id, trace_id, text),
                lambda exc: self._on_asr_error(turn_id, trace_id, exc),
            )
            self._converter.reset()
            self._state = "listening"
            session = self._session
        if old_session:
            old_session.finish()
        self._emit(turn_id, trace_id, "cloud_state", state="listening")
        session.start()

    def push_audio(self, turn_id: int, frame: np.ndarray) -> None:
        with self._lock:
            if turn_id != self._turn_id or turn_id in self._cancelled or not self._session:
                return
            pcm = self._converter.convert(frame)
            session = self._session
        session.feed(pcm)

    def end_turn(self, turn_id: int) -> None:
        with self._lock:
            if turn_id != self._turn_id or turn_id in self._cancelled:
                return
            session = self._session
            self._state = "transcribing"
        self._emit(turn_id, self._trace_id, "cloud_state", state="transcribing")
        if session:
            session.finish()

    def cancel_turn(self, turn_id: int) -> None:
        with self._lock:
            self._cancelled.add(turn_id)
            session = self._session if turn_id == self._turn_id else None
            if turn_id == self._turn_id:
                self._state = "idle"
        if session:
            session.finish()

    def health(self) -> dict:
        with self._lock:
            return {"running": self._running, "state": self._state, "active_turn": self._turn_id}

    def clear_history(self) -> None:
        """Forget only conversational context; it does not touch cloud credentials."""
        with self._lock:
            self._history.clear()

    def restore_history(self, messages: list[dict[str, str]]) -> None:
        """Restore validated local context; provider requests still see only text."""
        valid = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
        ]
        with self._lock:
            self._history = valid[-20:]

    def synthesize_text(self, text: str) -> bytes:
        """Synthesize a local system utterance such as the startup greeting.

        It deliberately does not create a dialogue turn or mutate history.
        The orchestrator owns the decision to play the returned PCM.
        """
        import requests

        if not text.strip():
            return b""
        payload = {
            "req_params": {
                "text": text,
                "speaker": self.config.tts_voice_type,
                "audio_params": {"format": "pcm", "sample_rate": self.config.tts_sample_rate},
            }
        }
        response = requests.post(
            TTS_URL,
            headers={
                "X-Api-Key": self.config.voice_api_key,
                "X-Api-Resource-Id": self.config.tts_resource_id,
                "X-Api-Request-Id": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=(10, 60),
        )
        response.raise_for_status()
        chunks: list[bytes] = []
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            item = json.loads(line)
            if item.get("code", 0) not in (0, 20_000_000):
                raise RuntimeError(item.get("message", "TTS 服务错误"))
            if item.get("data"):
                chunks.append(base64.b64decode(item["data"]))
        return b"".join(chunks)

    def _on_asr_error(self, turn_id: int, trace_id: str, exc: Exception) -> None:
        if self._is_current(turn_id):
            self._emit(turn_id, trace_id, "error", message=f"ASR 连接失败：{exc}")

    def _on_transcript(self, turn_id: int, trace_id: str, text: str) -> None:
        with self._lock:
            if not self._is_current(turn_id) or turn_id in self._answered_turns:
                return
            # A long utterance can contain several provider-level definite
            # clauses. VAD has already defined the user turn, so only its first
            # final transcript is allowed to start a cloud answer.
            self._answered_turns.add(turn_id)
        self._emit(turn_id, trace_id, "transcript", text=text, final=True)
        threading.Thread(target=self._answer, args=(turn_id, trace_id, text), daemon=True,
                         name="volc-direct-answer").start()

    def _answer(self, turn_id: int, trace_id: str, user_text: str) -> None:
        if not self._is_current(turn_id):
            return
        with self._lock:
            self._state = "thinking"
            messages = [{"role": "system", "content": self.config.system_prompt}] + self._history[-12:] + [
                {"role": "user", "content": user_text}
            ]
        self._emit(turn_id, trace_id, "cloud_state", state="thinking")
        answer, sentence = "", ""
        try:
            for token in self._stream_ark(messages):
                if not self._is_current(turn_id):
                    return
                answer += token
                sentence += token
                self._emit(turn_id, trace_id, "response_text", text=answer, final=False)
                if len(sentence) >= 8 and any(mark in sentence for mark in "。！？!?；;\n"):
                    self._synthesize_sentence(turn_id, trace_id, sentence)
                    sentence = ""
            if sentence and self._is_current(turn_id):
                self._synthesize_sentence(turn_id, trace_id, sentence)
            if self._is_current(turn_id) and answer:
                with self._lock:
                    self._history.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}])
                    self._history = self._history[-20:]
                    self._state = "listening"
                self._emit(turn_id, trace_id, "response_text", text=answer, final=True)
                self._emit(turn_id, trace_id, "cloud_state", state="listening")
        except Exception as exc:
            if self._is_current(turn_id):
                self._emit(turn_id, trace_id, "error", message=f"生成回复失败：{exc}")

    def _stream_ark(self, messages: list[dict[str, str]]):
        import requests

        url = self.config.ark_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.ark_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.5,
            "thinking": {"type": self.config.ark_thinking},
        }
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.config.ark_api_key}", "Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=(10, 120),
        )
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=False):
            line = raw_line.decode("utf-8") if raw_line else ""
            if not line.startswith("data:"):
                continue
            item = line[5:].strip()
            if item == "[DONE]":
                break
            choices = json.loads(item).get("choices", [])
            if choices:
                token = choices[0].get("delta", {}).get("content", "")
                if token:
                    yield token

    def _synthesize_sentence(self, turn_id: int, trace_id: str, text: str) -> None:
        import requests

        if not self._is_current(turn_id) or not text.strip():
            return
        self._emit(turn_id, trace_id, "cloud_state", state="speaking")
        payload = {
            "req_params": {
                "text": text,
                "speaker": self.config.tts_voice_type,
                "audio_params": {"format": "pcm", "sample_rate": self.config.tts_sample_rate},
            }
        }
        response = requests.post(
            TTS_URL,
            headers={
                "X-Api-Key": self.config.voice_api_key,
                "X-Api-Resource-Id": self.config.tts_resource_id,
                "X-Api-Request-Id": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=(10, 60),
        )
        response.raise_for_status()
        chunks: list[bytes] = []
        for line in response.iter_lines(decode_unicode=True):
            if not self._is_current(turn_id):
                return
            if not line:
                continue
            item = json.loads(line)
            if item.get("code", 0) not in (0, 20_000_000):
                raise RuntimeError(item.get("message", "TTS 服务错误"))
            if item.get("data"):
                chunks.append(base64.b64decode(item["data"]))
        if chunks and self._is_current(turn_id):
            handle = self._emit(
                turn_id, trace_id, "response_audio", pcm=b"".join(chunks),
                sample_rate=self.config.tts_sample_rate, channels=1, sample_width=2,
            )
            # Existing speaker.py serialises output. Waiting here prevents a long
            # response from overflowing that bounded queue; this never blocks ALSA,
            # the camera, eye renderer, or MicMonitor callback.
            if hasattr(handle, "wait"):
                handle.wait()

    def _is_current(self, turn_id: int) -> bool:
        with self._lock:
            return self._running and self._turn_id == turn_id and turn_id not in self._cancelled

    def _emit(self, turn_id: int, trace_id: str, event_type: str, **payload):
        callback = self._callback
        if callback is None:
            return None
        return callback(DialogueEvent(
            trace_id=trace_id,
            turn_id=turn_id,
            type=event_type,  # type: ignore[arg-type]
            created_at_ns=time.monotonic_ns(),
            payload=payload,
        ))
