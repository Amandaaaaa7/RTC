#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机直连版语音助手：豆包语音 API Key + 方舟 API Key。

本程序不创建 RTC 房间、不调用 StartVoiceChat，也不会将任何 API Key 发送给
WebView。浏览器只连接本机 127.0.0.1；Python 后端负责调用火山服务。
"""

import asyncio
import base64
import gzip
import inspect
import json
import os
import queue
import re
import socket
import struct
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
import webview
import websockets
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

from pi_audio_gateway import PiAudioGateway


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
EXAMPLE_CONFIG_PATH = BASE_DIR / "config.example.json"
MEMORY_PATH = BASE_DIR / "MEMORY.md"
CONVERSATION_LOG_PATH = BASE_DIR / "conversation_history.md"
DIARY_PATH = BASE_DIR / "conversation_summary.txt"
ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"未找到 {CONFIG_PATH.name}。请先从 config.example.json 创建配置文件。")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"config.json 不是合法 JSON：{exc}") from exc


def configured(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and "填写" not in text and "你的" not in text and "可选" not in text)


def load_prompt() -> str:
    sections: List[str] = []
    for filename, title in (("SOUL.md", "人格"), ("AGENTS.md", "能力")):
        path = BASE_DIR / filename
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                sections.append(f"【{title}】\n{text}")
    sections.append("请用自然、简洁的中文口语回答。避免过长回答，适合实时语音播报。")
    return "\n\n".join(sections)


class LocalMemory:
    """初版记忆能力的本地直连实现：无需 RTC 或 VikingDB。"""

    KEYWORDS = ("喜欢", "爱好", "讨厌", "不喜欢", "名字", "叫", "工作", "职业", "害怕", "希望", "需要", "想", "记住", "最近", "看", "读", "书")
    FACT_PATTERNS = (
        (re.compile(r"我(?:很|也)?喜欢([^，。！？!?；;]{1,30})"), "主人喜欢{}"),
        (re.compile(r"我不喜欢([^，。！？!?；;]{1,30})"), "主人不喜欢{}"),
        (re.compile(r"(?:我叫|我的名字是)([^，。！？!?；;]{1,30})"), "主人叫{}"),
        (re.compile(r"(?:我的爱好是|我的兴趣是)([^，。！？!?；;]{1,30})"), "主人的爱好是{}"),
        (re.compile(r"我(?:最近|正在|现在)?(?:在看|在读)([^，。！？!?；;]{1,50})"), "主人最近在看{}"),
        (re.compile(r"我(?:希望|害怕|需要)([^，。！？!?；;]{1,40})"), "主人{}"),
        (re.compile(r"请记住(?:，|：|:)?(.{3,60})"), "主人明确要求记住：{}"),
    )

    def __init__(self, config: Dict[str, Any]):
        options = config.get("local_memory", {})
        self.enabled = bool(options.get("enable", True))
        self.max_context = max(1, int(options.get("max_context_memories", 6)))
        self.write_diary_on_stop = bool(options.get("write_diary_on_stop", True))
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._session_turns: List[Dict[str, str]] = []

    @staticmethod
    def _clean(text: str) -> str:
        return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())

    def _append(self, path: Path, text: str) -> None:
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)

    @classmethod
    def extract_facts(cls, text: str) -> List[str]:
        """只保留可复用的陈述事实，排除“你知道我喜欢什么吗”之类问题。"""
        text = cls._clean(text)
        if not text:
            return []
        if text.startswith("主人"):
            return [text]
        facts: List[str] = []
        explicit_requests: List[str] = []
        for clause in re.split(r"[。！？!?；;]", text):
            clause = clause.strip()
            if not clause:
                continue
            for index, (pattern, template) in enumerate(cls.FACT_PATTERNS):
                for match in pattern.finditer(clause):
                    value = cls._clean(match.group(1)).strip("，,。！？!?；;")
                    if not value or "什么" in value or "吗" in value or "？" in clause or "?" in clause:
                        continue
                    normalized = template.format(value)
                    # “请记住，我喜欢看书”优先存“主人喜欢看书”；只有没有
                    # 可结构化事实时，才保留用户明确要求记住的自由文本。
                    (explicit_requests if index == len(cls.FACT_PATTERNS) - 1 else facts).append(normalized)
        if not facts:
            facts = explicit_requests
        return list(dict.fromkeys(facts))

    def remember_if_relevant(self, user_text: str) -> List[str]:
        """落盘本轮提取到的新事实，并返回本次真正新增的条目。"""
        if not self.enabled:
            return []
        saved: List[str] = []
        for fact in self.extract_facts(user_text):
            if fact in self._seen:
                continue
            self._seen.add(fact)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._append(MEMORY_PATH, f"- [{timestamp}] [记忆] {fact}\n")
            saved.append(fact)
        return saved

    def relevant(self, query: str) -> List[str]:
        if not self.enabled or not MEMORY_PATH.exists():
            return []
        query = self._clean(query)
        try:
            entries = [line.strip() for line in MEMORY_PATH.read_text(encoding="utf-8").splitlines() if line.strip().startswith("-")]
        except OSError:
            return []
        query_chars = {char for char in query if "\u4e00" <= char <= "\u9fff"}
        scored: List[tuple[int, str]] = []
        for entry in entries[-300:]:
            content = entry.rsplit("] ", 1)[-1]
            # 旧 MEMORY.md 中的原始句也会在读取时规范化，因此保留旧文件的
            # 有用事实，但不再把提问句送给模型。
            for fact in self.extract_facts(content):
                score = len(query_chars & {char for char in fact if "\u4e00" <= char <= "\u9fff"})
                score += 4 * sum(keyword in query and keyword in fact for keyword in self.KEYWORDS)
                if score:
                    scored.append((score, fact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return list(dict.fromkeys(content for _, content in scored))[: self.max_context]

    def record_turn(self, user_text: str, assistant_text: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_text, assistant_text = self._clean(user_text), self._clean(assistant_text)
        if not user_text and not assistant_text:
            return
        with self._lock:
            self._session_turns.append({"time": timestamp, "user": user_text, "assistant": assistant_text})
        self._append(CONVERSATION_LOG_PATH, f"## {timestamp}\n- 主人：{user_text}\n- 圆宝：{assistant_text}\n\n")

    def take_session_text(self) -> str:
        with self._lock:
            turns = list(self._session_turns)
            self._session_turns.clear()
        return "\n".join(f"[{item['time']}] 主人：{item['user']}\n[{item['time']}] 圆宝：{item['assistant']}" for item in turns)

    def clear_session(self) -> None:
        """与初版的“清空对话”一致：不删除已落盘的长期记忆。"""
        with self._lock:
            self._session_turns.clear()

    def save_diary(self, diary: str) -> None:
        diary = self._clean(diary)
        if diary:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._append(DIARY_PATH, f"[{timestamp}]\n日记：{diary}\n{'-' * 50}\n")


def v3_header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    """豆包语音 V3 WebSocket 公共头，四字节、网络字节序。"""
    return bytes([(1 << 4) | 1, (message_type << 4) | flags, (serialization << 4) | compression, 0])


def v3_full_request(payload: Dict[str, Any], sequence: int) -> bytes:
    """构造官方示例所用的：正序号 + JSON gzip 的首包。"""
    body = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return v3_header(1, 1, 1, 1) + struct.pack(">i", sequence) + struct.pack(">I", len(body)) + body


def v3_audio_request(pcm: bytes, sequence: int, is_last: bool = False) -> bytes:
    """构造官方示例所用的：正序号音频包，尾包使用负序号。"""
    body = gzip.compress(pcm)
    flags = 3 if is_last else 1  # bit0=序号；bit1=最后一包
    packet_sequence = -sequence if is_last else sequence
    return v3_header(2, flags, 0, 1) + struct.pack(">i", packet_sequence) + struct.pack(">I", len(body)) + body


def decode_v3_response(raw: bytes) -> Dict[str, Any]:
    """解码服务端 V3 响应；未知包安全忽略，避免单个状态包中断会话。"""
    if len(raw) < 4:
        return {}
    header_size = (raw[0] & 0x0F) * 4
    if header_size < 4 or len(raw) < header_size:
        return {}
    message_type = raw[1] >> 4
    flags = raw[1] & 0x0F
    serialization = raw[2] >> 4
    compression = raw[2] & 0x0F
    position = header_size
    # flags 是位标志：bit0=携带序号，bit1=最后一个包，bit2=携带事件号。
    # 响应包通常带事件号；若不跳过这 4 字节，会把事件号误当成 payload 长度，
    # 造成“ASR 已连接但页面始终没有文字”。
    if flags & 0x01:
        if len(raw) < position + 4:
            return {}
        position += 4  # sequence，仅用于定位，不参与业务逻辑
    if flags & 0x04:
        if len(raw) < position + 4:
            return {}
        position += 4  # event，仅用于状态标识，不参与业务逻辑
    if message_type == 0x0F:  # server error: status code 在 payload size 前
        if len(raw) < position + 4:
            return {}
        code = struct.unpack(">i", raw[position:position + 4])[0]
        position += 4
    else:
        code = 0
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


class EventBus:
    def __init__(self) -> None:
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def emit(self, event_type: str, **data: Any) -> None:
        self.events.put({"type": event_type, **data})

    def stream(self):
        while True:
            event = self.events.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class AsrSession:
    """将本机 PCM 队列桥接到豆包实时 ASR WebSocket。"""
    def __init__(self, api_key: str, resource_id: str, on_result: Callable[[str, bool], None], bus: EventBus):
        self.api_key = api_key
        self.resource_id = resource_id
        self.on_result = on_result
        self.bus = bus
        self.audio_queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=80)
        self.thread: Optional[threading.Thread] = None
        self.closed = threading.Event()
        self.sequence = 1
        # show_utterances 会随每次中间结果重复携带早先已完成句；只交付一次。
        self._emitted_utterance_keys: set[str] = set()
        self._emitted_utterance_order: List[str] = []

    def _remember_utterance_key(self, item: Dict[str, Any], text: str) -> bool:
        """返回该最终句是否是本 ASR 会话中第一次出现。"""
        start = item.get("start_time", item.get("start_time_ms", item.get("start", "")))
        end = item.get("end_time", item.get("end_time_ms", item.get("end", "")))
        key = f"{start}|{end}|{text}"
        if key in self._emitted_utterance_keys:
            return False
        self._emitted_utterance_keys.add(key)
        self._emitted_utterance_order.append(key)
        if len(self._emitted_utterance_order) > 300:
            old = self._emitted_utterance_order.pop(0)
            self._emitted_utterance_keys.discard(old)
        return True

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run_thread, daemon=True, name="doubao-asr")
        self.thread.start()

    def feed(self, pcm: bytes) -> None:
        if self.closed.is_set() or not pcm:
            return
        try:
            self.audio_queue.put_nowait(pcm)
        except queue.Full:
            # 实时语音优先保留最新声音，避免积压导致对话延迟越来越大。
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(pcm)
            except queue.Empty:
                pass

    def stop(self) -> None:
        if not self.closed.is_set():
            self.closed.set()
            self.audio_queue.put(None)

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.bus.emit("error", message=f"ASR 连接失败：{exc}")

    async def _run(self) -> None:
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        request_payload = {
            "user": {"uid": f"local_{uuid.uuid4().hex[:12]}"},
            "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": True,
                "end_window_size": 800,
            },
        }
        # websockets 14+ renamed ``extra_headers`` to ``additional_headers``.
        # Support both so a routine dependency upgrade cannot prevent ASR from starting.
        header_option = (
            "additional_headers"
            if "additional_headers" in inspect.signature(websockets.connect).parameters
            else "extra_headers"
        )
        connect_options = {
            header_option: headers,
            "ping_interval": 20,
            "max_size": 2**22,
            "open_timeout": 30,
        }
        async with websockets.connect(ASR_URL, **connect_options) as ws:
            self.bus.emit("status", message="ASR 已连接，正在聆听")
            await ws.send(v3_full_request(request_payload, self.sequence))
            self.sequence += 1
            receiver = asyncio.create_task(self._receive(ws))
            while True:
                pcm = await asyncio.to_thread(self.audio_queue.get)
                if pcm is None:
                    await ws.send(v3_audio_request(b"", self.sequence, is_last=True))
                    break
                await ws.send(v3_audio_request(pcm, self.sequence))
                self.sequence += 1
            try:
                await asyncio.wait_for(receiver, timeout=5)
            except asyncio.TimeoutError:
                receiver.cancel()
            finally:
                self.closed.set()

    async def _receive(self, ws: Any) -> None:
        async for message in ws:
            try:
                response = json.loads(message) if isinstance(message, str) else decode_v3_response(message)
                if response.get("code", 0) != 0:
                    self.bus.emit("error", message=f"ASR 返回错误：{response.get('message', response)}")
                    continue
                # 当前 SAUC 接口直接返回 {"result": ...}；部分旧协议封装则是
                # {"payload_msg": {"result": ...}}。两种结构都兼容，避免云端
                # 已识别文字却在本地被静默丢弃。
                response_body = response.get("payload_msg")
                if not isinstance(response_body, dict):
                    response_body = response
                result = response_body.get("result", {})
                if isinstance(result, list):
                    result = result[0] if result else {}
                utterances = result.get("utterances", []) or []
                # result.text 是“本连接至今的累计转写”，不能直接当作一轮提问。
                # 每个 definite utterance 才是 VAD 已结束的一句话；只把新句交给
                # 对话层，避免第二句携带第一句、进而反复回答同一问题。
                for item in utterances:
                    if not isinstance(item, dict) or not item.get("definite"):
                        continue
                    utterance_text = str(item.get("text", "")).strip()
                    if utterance_text and self._remember_utterance_key(item, utterance_text):
                        self.on_result(utterance_text, True)
            except Exception as exc:
                self.bus.emit("error", message=f"解析 ASR 响应失败：{exc}")


class DirectVoiceController:
    def __init__(self, config: Dict[str, Any], bus: EventBus, audio_gateway: Optional[PiAudioGateway] = None):
        self.config = config
        self.bus = bus
        self.voice = config.get("direct_voice", {})
        self.ark = config.get("ark", {})
        # 旧版 llm 配置只承担“停止会话后写日记”的工作；实时对话仍固定走 ark。
        self.diary_llm = config.get("llm", {})
        self.memory = LocalMemory(config)
        self.asr: Optional[AsrSession] = None
        self.history: List[Dict[str, str]] = []
        self.last_final = ""
        self.cancel_reply = threading.Event()
        self.reply_lock = threading.Lock()
        self._asr_lock = threading.Lock()
        self._pending_transcript = ""
        self._finalize_timer: Optional[threading.Timer] = None
        self.audio_gateway = audio_gateway
        self.pause_capture_during_playback = bool((config.get("audio_io", {}) or {}).get("pause_capture_during_playback", True))

    def set_audio_gateway(self, audio_gateway: PiAudioGateway) -> None:
        """在控制器创建后注入树莓派网关，避免网关反向依赖控制器的初始化顺序。"""
        self.audio_gateway = audio_gateway

    def missing_settings(self) -> List[str]:
        required = {
            "direct_voice.api_key": self.voice.get("api_key"),
            "ark.api_key": self.ark.get("api_key"),
            "ark.model": self.ark.get("model"),
        }
        return [name for name, value in required.items() if not configured(value)]

    def start(self) -> None:
        missing = self.missing_settings()
        if missing:
            raise ValueError("请先填写 config.json：" + "、".join(missing))
        self.stop()
        self.cancel_reply.clear()
        self.last_final = ""
        with self._asr_lock:
            self._pending_transcript = ""
        self.asr = AsrSession(
            self.voice["api_key"],
            self.voice.get("asr_resource_id", "volc.seedasr.sauc.duration"),
            self._on_asr_result,
            self.bus,
        )
        self.asr.start()
        if self.audio_gateway:
            self.audio_gateway.start_capture()
        welcome = str(self.config.get("scene", {}).get("welcome_message", "")).strip()
        if welcome:
            # 直连版没有 RTC Agent；在本地复刻原先由 Agent 触发的欢迎语。
            self.bus.emit("assistant_text", text=welcome, final=True)
            threading.Thread(
                target=self._speak,
                args=(welcome, self.cancel_reply),
                daemon=True,
                name="welcome-tts",
            ).start()

    def stop(self) -> None:
        self.cancel_reply.set()
        if self.audio_gateway:
            self.audio_gateway.cancel_playback()
            self.audio_gateway.stop_capture()
        with self._asr_lock:
            pending_transcript = self._pending_transcript
            if self._finalize_timer:
                self._finalize_timer.cancel()
                self._finalize_timer = None
            self._pending_transcript = ""
        # 点击停止不应丢掉刚说完、还在 ASR 防抖窗口内的“我喜欢……”之类事实。
        if pending_transcript and pending_transcript != self.last_final:
            self._save_new_memories(pending_transcript)
        if self.asr:
            self.asr.stop()
            self.asr = None
        session_text = self.memory.take_session_text()
        if session_text and self.memory.write_diary_on_stop:
            threading.Thread(target=self._write_session_diary, args=(session_text,), daemon=True, name="session-diary").start()
        self.bus.emit("status", message="已停止")

    def feed_audio(self, pcm: bytes) -> None:
        if not self.asr:
            raise RuntimeError("会话尚未开始")
        self.asr.feed(pcm)

    def clear_history(self) -> None:
        self.cancel_reply.set()
        if self.audio_gateway:
            self.audio_gateway.cancel_playback()
        with self._asr_lock:
            if self._finalize_timer:
                self._finalize_timer.cancel()
                self._finalize_timer = None
            self._pending_transcript = ""
        self.history.clear()
        self.last_final = ""
        self.memory.clear_session()
        self.bus.emit("status", message="本轮对话已清空")

    def _on_asr_result(self, text: str, definite: bool) -> None:
        text = " ".join(text.split())
        if not text:
            return
        # 用户重新发声时立刻停止上一轮尚未播完的回复。
        self.cancel_reply.set()
        if self.audio_gateway:
            self.audio_gateway.cancel_playback()
        # ASR 会连续返回不断扩展的转写。延迟极短时间确认文字稳定后再提交，
        # 避免每个中间结果都成为一条聊天消息、一次记忆和一次模型调用。
        with self._asr_lock:
            self._pending_transcript = text
            if self._finalize_timer:
                self._finalize_timer.cancel()
                self._finalize_timer = None
            if definite:
                self._finalize_timer = threading.Timer(0.55, self._commit_stable_transcript, args=(text,))
                self._finalize_timer.daemon = True
                self._finalize_timer.start()
        self.bus.emit("user_text", text=text, final=False)

    def _commit_stable_transcript(self, candidate: str) -> None:
        with self._asr_lock:
            if candidate != self._pending_transcript:
                return
            self._pending_transcript = ""
            self._finalize_timer = None
        if candidate == self.last_final:
            return
        self.last_final = candidate
        self.bus.emit("user_text", text=candidate, final=True)
        self._save_new_memories(candidate)
        self.cancel_reply = threading.Event()
        threading.Thread(target=self._answer, args=(candidate, self.cancel_reply), daemon=True, name="ark-reply").start()

    def _save_new_memories(self, user_text: str) -> List[str]:
        saved = self.memory.remember_if_relevant(user_text)
        if saved:
            self.bus.emit("memory_saved", text="；".join(saved))
        return saved

    def _answer(self, user_text: str, cancelled: threading.Event) -> None:
        with self.reply_lock:
            self.bus.emit("status", message="圆宝正在思考")
            system_prompt = load_prompt()
            memories = self.memory.relevant(user_text)
            if memories:
                system_prompt += "\n\n【与主人有关的已保存记忆】\n" + "\n".join(f"- {item}" for item in memories) + "\n仅在确实相关时自然使用，不能虚构。"
            messages = [{"role": "system", "content": system_prompt}] + self.history[-12:] + [{"role": "user", "content": user_text}]
            answer = ""
            pending_sentence = ""
            try:
                for token in self._stream_ark(messages):
                    if cancelled.is_set():
                        return
                    answer += token
                    pending_sentence += token
                    self.bus.emit("assistant_text", text=answer, final=False)
                    if len(pending_sentence) >= 8 and any(mark in pending_sentence for mark in "。！？!?；;\n"):
                        self._speak(pending_sentence, cancelled)
                        pending_sentence = ""
                if pending_sentence and not cancelled.is_set():
                    self._speak(pending_sentence, cancelled)
                if answer and not cancelled.is_set():
                    self.history.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}])
                    self.history = self.history[-20:]
                    self.memory.record_turn(user_text, answer)
                    self.bus.emit("assistant_text", text=answer, final=True)
                    self.bus.emit("status", message="正在聆听")
            except Exception as exc:
                self.bus.emit("error", message=f"生成回复失败：{exc}")

    def _write_session_diary(self, session_text: str) -> None:
        try:
            diary = self._generate_legacy_diary(session_text)
            if not diary:
                prompt = "请以圆宝第一人称，把以下本次互动写成约100字、具体克制的中文日记。只写日记正文，不编造事实。"
                diary = "".join(self._stream_ark([{"role": "system", "content": prompt}, {"role": "user", "content": session_text}]))
            self.memory.save_diary(diary)
            self.bus.emit("status", message="本次日记已保存")
        except Exception as exc:
            self.bus.emit("error", message=f"日记保存失败：{exc}")

    def _generate_legacy_diary(self, session_text: str) -> str:
        """兼容初版 llm（DashScope/OpenAI 兼容接口）；未配置时让方舟兜底。"""
        required = ("api_key", "model", "base_url")
        if not all(configured(self.diary_llm.get(key)) for key in required):
            return ""
        endpoint = str(self.diary_llm["base_url"]).rstrip("/") + "/chat/completions"
        prompt = str(self.diary_llm.get("messages") or "以圆宝第一人称写约100字中文日记，具体、克制，不编造事实。")
        payload = {
            "model": self.diary_llm["model"],
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请根据以下本次对话写日记：\n" + session_text},
            ],
            "stream": False,
            "max_tokens": int(self.diary_llm.get("max_tokens", 800)),
            "temperature": float(self.diary_llm.get("temperature", 0.3)),
        }
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {self.diary_llm['api_key']}", "Content-Type": "application/json"},
            json=payload,
            timeout=(10, 90),
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        if not choices:
            return ""
        return self.memory._clean(choices[0].get("message", {}).get("content", ""))

    def _stream_ark(self, messages: List[Dict[str, str]]):
        url = self.ark.get("base_url", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/") + "/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.ark["model"],
            "messages": messages,
            "stream": True,
            "temperature": 0.5,
            # 语音对话优先首字延迟。Seed 2.1 Pro 默认开启思考，可能让首字
            # 等待十余秒；普通陪伴对话显式关闭即可直接流式回复。
            "thinking": {"type": self.ark.get("thinking", "disabled")},
        }
        service_tier = str(self.ark.get("service_tier", "")).strip()
        if service_tier:
            payload["service_tier"] = service_tier
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.ark['api_key']}", "Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=(10, 120),
        )
        response.raise_for_status()
        # SSE 响应常未声明 charset，requests 会按 ISO-8859-1 推断并把中文
        # 解码成“ä½ å¥½”一类乱码。保留原始字节后强制按 UTF-8 解码。
        for raw_line in response.iter_lines(decode_unicode=False):
            line = raw_line.decode("utf-8") if raw_line else ""
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            data = json.loads(payload)
            choices = data.get("choices", [])
            if choices:
                token = choices[0].get("delta", {}).get("content", "")
                if token:
                    yield token

    def _speak(self, text: str, cancelled: threading.Event) -> None:
        if cancelled.is_set() or not text.strip():
            return
        playback_id = uuid.uuid4().hex
        remote_audio = self.audio_gateway
        if remote_audio:
            if self.pause_capture_during_playback:
                remote_audio.pause_capture()
            remote_audio.begin_playback(playback_id, int(self.voice.get("tts_sample_rate", 24000)))
        headers = {
            "X-Api-Key": self.voice["api_key"],
            "X-Api-Resource-Id": self.voice.get("tts_resource_id", "seed-tts-2.0"),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        payload = {
            "req_params": {
                "text": text,
                "speaker": self.voice.get("tts_voice_type", "zh_female_vv_uranus_bigtts"),
                "audio_params": {"format": "pcm", "sample_rate": self.voice.get("tts_sample_rate", 24000)},
            }
        }
        try:
            response = requests.post(TTS_URL, headers=headers, json=payload, stream=True, timeout=(10, 60))
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if cancelled.is_set():
                    if remote_audio:
                        remote_audio.cancel_playback()
                    return
                if not line:
                    continue
                data = json.loads(line)
                # 流式 TTS 用 20000000/OK 表示正常结束；它不是业务失败。
                if data.get("code", 0) not in (0, 20000000):
                    raise RuntimeError(data.get("message", "TTS 服务错误"))
                audio = data.get("data")
                if audio:
                    if remote_audio:
                        remote_audio.send_audio(base64.b64decode(audio))
                    else:
                        self.bus.emit("audio", pcm=audio, sample_rate=self.voice.get("tts_sample_rate", 24000))
        finally:
            if remote_audio:
                remote_audio.end_playback(playback_id)
                if self.pause_capture_during_playback:
                    remote_audio.resume_capture()


HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>圆宝 · 本机语音助手</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:linear-gradient(135deg,#667eea,#764ba2);height:100vh}.container{height:100vh;background:white;display:flex;flex-direction:column;overflow:hidden}.header{padding:18px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;text-align:center;flex-shrink:0}.header h3{font-size:1.5rem;margin-bottom:8px}.status{font-size:.85rem;display:flex;align-items:center;justify-content:center;gap:8px}.status-dot{width:10px;height:10px;border-radius:50%;background:#94a3b8;transition:.3s}.status-dot.active{background:#4ade80;animation:pulse 2s infinite}@keyframes pulse{50%{opacity:.5}}.server-status{font-size:.7rem;margin-top:8px;padding:4px 8px;background:rgba(0,0,0,.2);border-radius:20px;display:inline-block}.meter{width:104px;height:5px;margin:9px auto 0;border-radius:9px;background:rgba(255,255,255,.28);overflow:hidden}.meter i{display:block;width:0;height:100%;border-radius:inherit;background:#6ee7b7;transition:width .1s}.chat-area{flex:1;overflow-y:auto;padding:18px;background:#385a7c}.message{margin-bottom:16px;display:flex;animation:fade .25s ease}@keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}.message.user{justify-content:flex-end}.message.ai{justify-content:flex-start}.message-content{max-width:75%;padding:12px 16px;border-radius:18px;word-break:break-word;line-height:1.5}.user .message-content{background:linear-gradient(135deg,#667eea,#764ba2);color:white;border-bottom-right-radius:4px}.ai .message-content{background:#fff;color:#1e293b;border:1px solid #e2e8f0;border-bottom-left-radius:4px}.message-time{font-size:.7rem;color:#cbd5e1;margin-top:4px;text-align:center}.control-area{padding:20px;background:#fff;border-top:1px solid #e2e8f0;display:flex;gap:12px;flex-shrink:0}.btn{flex:1;padding:12px 20px;border:0;border-radius:40px;font-size:.9rem;font-weight:500;cursor:pointer}.btn:disabled{opacity:.5;cursor:not-allowed}.btn-primary{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff}.btn-danger{background:#ef4444;color:#fff}.btn-secondary{background:#f1f5f9;color:#475569}.thinking{display:inline-flex;gap:4px}.thinking i{width:8px;height:8px;background:#94a3b8;border-radius:50%;animation:bounce 1.2s infinite}.thinking i:nth-child(2){animation-delay:.2s}.thinking i:nth-child(3){animation-delay:.4s}@keyframes bounce{50%{transform:translateY(-7px)}}
</style></head><body><div class="container"><div class="header"><h3>圆宝 · 本机语音助手</h3><div class="status"><span class="status-dot" id="statusDot"></span><span id="statusText">未启动</span></div><div class="server-status" id="serverStatus">API Key 本机直连 · 等待启动</div><div class="meter"><i id="meter"></i></div></div><div class="chat-area" id="chatArea"><div class="message ai"><div class="message-content">点击「启动AI助手」开始对话</div></div></div><div class="control-area"><button class="btn btn-primary" id="startBtn">启动AI助手</button><button class="btn btn-danger" id="stopBtn" disabled>停止AI助手</button><button class="btn btn-secondary" id="clearBtn">清空对话</button></div></div>
<script>
const chat=document.querySelector('#chatArea'),status=document.querySelector('#statusText'),dot=document.querySelector('#statusDot'),server=document.querySelector('#serverStatus'),meter=document.querySelector('#meter'),start=document.querySelector('#startBtn'),stop=document.querySelector('#stopBtn'),clear=document.querySelector('#clearBtn');
let stream,context,source,processor,chunks=[],userBubble,aiBubble,nextPlay=0,packetCount=0,sending=false,remoteMode=false;
function addMessage(text,isUser,thinking=false){const wrap=document.createElement('div');wrap.className='message '+(isUser?'user':'ai');const body=document.createElement('div');body.className='message-content';if(thinking)body.innerHTML='<span class="thinking"><i></i><i></i><i></i></span>';else body.textContent=text;const stamp=document.createElement('div');stamp.className='message-time';stamp.textContent=new Date().toLocaleTimeString();wrap.append(body,stamp);chat.append(wrap);wrap.scrollIntoView({behavior:'smooth',block:'end'});return wrap}
function updateStatus(text,active=false){status.textContent=text;dot.classList.toggle('active',active)}
function bytesToBase64(bytes){let s='';for(let i=0;i<bytes.length;i++)s+=String.fromCharCode(bytes[i]);return btoa(s)}
function sendPcm(samples){const bytes=new Uint8Array(samples.length*2);for(let i=0;i<samples.length;i++){let v=Math.max(-1,Math.min(1,samples[i]));v=v<0?v*32768:v*32767;bytes[i*2]=v&255;bytes[i*2+1]=(v>>8)&255}packetCount++;server.textContent='麦克风已采集并发送 '+packetCount+' 个音频包';return fetch('/api/audio',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pcm:bytesToBase64(bytes)})}).then(r=>{if(!r.ok)throw Error('本机音频传输失败')}).catch(e=>{server.textContent=e.message})}
function resample(input,from,to){if(from===to)return input;const ratio=from/to,out=new Float32Array(Math.round(input.length/ratio));for(let i=0;i<out.length;i++){const p=i*ratio,a=Math.floor(p),b=Math.min(a+1,input.length-1),f=p-a;out[i]=input[a]*(1-f)+input[b]*f}return out}
async function capture(){stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});context=new AudioContext();nextPlay=context.currentTime;source=context.createMediaStreamSource(stream);processor=context.createScriptProcessor(4096,1,1);processor.onaudioprocess=e=>{const input=e.inputBuffer.getChannelData(0);let sum=0;for(const x of input)sum+=x*x;meter.style.width=Math.min(100,Math.sqrt(sum/input.length)*500)+'%';if(!sending)return;const data=resample(input,context.sampleRate,16000);chunks.push(...data);while(chunks.length>=3200)void sendPcm(new Float32Array(chunks.splice(0,3200)))};source.connect(processor);processor.connect(context.destination)}
async function flushPcm(){if(chunks.length){const last=new Float32Array(chunks.splice(0,chunks.length));await sendPcm(last)}}
function play(base64,rate){const raw=atob(base64),arr=new Int16Array(raw.length/2);for(let i=0;i<arr.length;i++)arr[i]=raw.charCodeAt(i*2)|(raw.charCodeAt(i*2+1)<<8);const b=context.createBuffer(1,arr.length,rate),d=b.getChannelData(0);for(let i=0;i<arr.length;i++)d[i]=arr[i]/32768;const n=context.createBufferSource();n.buffer=b;n.connect(context.destination);nextPlay=Math.max(nextPlay,context.currentTime+.04);n.start(nextPlay);nextPlay+=b.duration}
async function loadRuntime(){const r=await fetch('/api/runtime'),d=await r.json();remoteMode=d.audio_mode==='raspberry_pi';if(remoteMode){const n=d.audio_node||{};server.textContent=n.connected?'树莓派音频节点已连接':'等待树莓派音频节点连接'}return d}
void loadRuntime();
new EventSource('/events').onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='status'){updateStatus(m.message,!m.message.includes('停止'));if(m.message.includes('思考')&&!aiBubble)aiBubble=addMessage('',false,true)}if(m.type==='node_status'){server.textContent=m.message}if(m.type==='error'){updateStatus(m.message,false);addMessage('错误：'+m.message,false)}if(m.type==='memory_saved'){addMessage('已记住：'+m.text,false)}if(m.type==='user_text'){if(!userBubble)userBubble=addMessage(m.text,true);else userBubble.querySelector('.message-content').textContent=m.text;if(m.final)userBubble=null}if(m.type==='assistant_text'){if(!aiBubble)aiBubble=addMessage(m.text,false);else aiBubble.querySelector('.message-content').textContent=m.text;if(m.final)aiBubble=null}if(m.type==='audio'&&context&&!remoteMode)play(m.pcm,m.sample_rate)};
start.onclick=async()=>{try{const runtime=await loadRuntime();if(remoteMode&&!runtime.audio_node?.connected)throw Error('树莓派音频节点未连接');if(!remoteMode)await capture();const r=await fetch('/api/start',{method:'POST'}),d=await r.json();if(!r.ok)throw Error(d.message);packetCount=0;sending=!remoteMode;start.disabled=true;stop.disabled=false;updateStatus('AI助手运行中',true);server.textContent=remoteMode?'树莓派麦克风已启用，正在等待语音':'麦克风已启用，正在等待语音';addMessage(remoteMode?'树莓派麦克风已启用，请开始说话。':'麦克风已启用，请开始说话。',false)}catch(e){addMessage('无法启动：'+e.message,false);updateStatus('启动失败',false)}};
stop.onclick=async()=>{if(!remoteMode){sending=false;await flushPcm();await new Promise(r=>setTimeout(r,120));if(processor)processor.disconnect();if(source)source.disconnect();if(stream)stream.getTracks().forEach(t=>t.stop());if(context)await context.close()}await fetch('/api/stop',{method:'POST'});chunks=[];meter.style.width='0';start.disabled=false;stop.disabled=true;updateStatus('已停止',false);server.textContent=remoteMode?'树莓派音频节点 · 已停止':'API Key 本机直连 · 已停止';addMessage('AI助手已停止。',false)};
clear.onclick=async()=>{await fetch('/api/clear',{method:'POST'});chat.innerHTML='';userBubble=aiBubble=null;addMessage('对话已清空。',false)};
</script></body></html>"""


def run() -> None:
    config = load_config()
    app = Flask(__name__)
    bus = EventBus()
    controller = DirectVoiceController(config, bus)
    audio_options = config.get("audio_io", {}) or {}
    audio_mode = str(audio_options.get("mode", "local_browser")).strip().lower()
    gateway: Optional[PiAudioGateway] = None
    if audio_mode == "raspberry_pi":
        # 树莓派只连接此网关；网页控制接口仍保持在 127.0.0.1。
        playback = audio_options.get("playback", {}) or {}
        expected_playback_rate = int(playback.get("sample_rate", 24000))
        actual_tts_rate = int((config.get("direct_voice", {}) or {}).get("tts_sample_rate", 24000))
        if expected_playback_rate != actual_tts_rate:
            raise ValueError("audio_io.playback.sample_rate 必须与 direct_voice.tts_sample_rate 一致")

        def accept_pi_audio(pcm: bytes) -> None:
            try:
                controller.feed_audio(pcm)
            except RuntimeError:
                # 停止会话时可能恰好收到最后一个音频包，安全忽略即可。
                pass

        gateway = PiAudioGateway(
            audio_options,
            accept_pi_audio,
            lambda event_type, message: bus.emit(event_type, message=message),
        )
        controller.set_audio_gateway(gateway)
        gateway.start()
    elif audio_mode != "local_browser":
        raise ValueError("audio_io.mode 仅支持 local_browser 或 raspberry_pi")

    @app.get("/")
    def index():
        return HTML

    @app.get("/events")
    def events():
        return Response(bus.stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runtime")
    def runtime():
        return jsonify({
            "audio_mode": audio_mode,
            "audio_node": gateway.status() if gateway else None,
        })

    @app.post("/api/start")
    def start():
        try:
            controller.start()
            return jsonify({"success": True})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    @app.post("/api/audio")
    def audio():
        try:
            controller.feed_audio(base64.b64decode(request.get_json(force=True)["pcm"]))
            return jsonify({"success": True})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    @app.post("/api/stop")
    def stop():
        controller.stop()
        return jsonify({"success": True})

    @app.post("/api/clear")
    def clear():
        controller.clear_history()
        return jsonify({"success": True})

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    host, port = probe.getsockname()
    probe.close()
    server = make_server(host, port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="local-voice-server").start()
    window = webview.create_window("圆宝 · 本机语音助手", f"http://{host}:{port}", width=860, height=680)
    try:
        webview.start()
    finally:
        controller.stop()
        server.shutdown()
        if gateway:
            gateway.stop()


if __name__ == "__main__":
    run()
