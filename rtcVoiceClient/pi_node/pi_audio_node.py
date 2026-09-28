#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""树莓派轻量音频节点：只采集/播放 PCM，不保存任何云端 API Key。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import ssl
import sys
import threading
from array import array
from pathlib import Path
from typing import Any, Dict, Optional

import pyaudio
import websockets


class PiAudioNode:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.node_id = str(config.get("node_id", "yuanbao-pi"))
        self.server_url = str(config.get("server_url", "")).strip()
        self.token = os.environ.get(str(config.get("token_env", "RTCVOICE_NODE_TOKEN")), "")
        self.channels = int(config.get("channels", 1))
        self.capture_rate = int(config.get("capture_sample_rate", 16000))
        self.playback_rate = int(config.get("playback_sample_rate", 24000))
        # *_rate 是电脑协议采样率；device_*_rate 是树莓派声卡真正打开的
        # 采样率。许多 USB/I2S 声卡不能直接打开 16 kHz 或 24 kHz。
        self.device_capture_rate = int(config.get("device_capture_sample_rate", self.capture_rate))
        self.device_playback_rate = int(config.get("device_playback_sample_rate", self.playback_rate))
        self.frame_ms = int(config.get("frame_ms", 40))
        if self.channels != 1:
            raise ValueError("当前树莓派音频节点仅支持单声道 PCM")
        if self.device_capture_rate % self.capture_rate or self.device_playback_rate % self.playback_rate:
            raise ValueError("device_capture_sample_rate 和 device_playback_sample_rate 必须分别是协议采样率的整数倍")
        self.capture_ratio = self.device_capture_rate // self.capture_rate
        self.playback_ratio = self.device_playback_rate // self.playback_rate
        self.frames_per_buffer = max(160, self.device_capture_rate * self.frame_ms // 1000)
        self.echo_guard_seconds = max(0, int(config.get("echo_guard_ms", 220))) / 1000
        self.audio = pyaudio.PyAudio()
        self.capture_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=100)
        self.capture_enabled = threading.Event()
        self.capture_paused = threading.Event()
        self.playback_active = False
        self.input_stream: Any = None
        self.output_stream: Any = None

    def _device_index(self, configured_name: str, input_device: bool) -> Optional[int]:
        name = configured_name.strip().lower()
        if not name or name == "default":
            return None
        channel_key = "maxInputChannels" if input_device else "maxOutputChannels"
        matches = []
        for index in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(index)
            if int(info.get(channel_key, 0)) > 0 and name in str(info.get("name", "")).lower():
                matches.append(index)
        if not matches:
            direction = "输入" if input_device else "输出"
            raise RuntimeError(f"未找到名称包含“{configured_name}”的{direction}设备")
        return matches[0]

    def _capture_callback(self, in_data: bytes, frame_count: int, time_info: Any, status: int):
        if self.capture_enabled.is_set():
            # 豆包流式 ASR 必须持续收到音频包；圆宝播放时若完全停止上行，约 8 秒
            # 后服务端会以 “Timeout waiting next packet” 结束会话。半双工期间
            # 上传等长度的静音 PCM，既保持 ASR 会话，又不会把扬声器声音当成人声。
            pcm = (
                b"\x00" * (frame_count * self.channels * 2)
                if self.capture_paused.is_set()
                else self._to_protocol_capture_pcm(in_data)
            )
            try:
                self.capture_queue.put_nowait(pcm)
            except queue.Full:
                try:
                    self.capture_queue.get_nowait()
                    self.capture_queue.put_nowait(in_data)
                except queue.Empty:
                    pass
        return None, pyaudio.paContinue

    def _to_protocol_capture_pcm(self, pcm: bytes) -> bytes:
        """将硬件 48 kHz PCM 降采样为电脑 ASR 所需的 16 kHz PCM。"""
        if self.capture_ratio == 1:
            return pcm
        samples = array("h")
        samples.frombytes(pcm)
        result = array("h")
        for start in range(0, len(samples), self.capture_ratio):
            group = samples[start:start + self.capture_ratio]
            if len(group) == self.capture_ratio:
                result.append(sum(group) // self.capture_ratio)
        return result.tobytes()

    def _to_device_playback_pcm(self, pcm: bytes) -> bytes:
        """将电脑 24 kHz TTS PCM 上采样到树莓派声卡实际采样率。"""
        if self.playback_ratio == 1:
            return pcm
        samples = array("h")
        samples.frombytes(pcm)
        result = array("h")
        for sample in samples:
            result.extend([sample] * self.playback_ratio)
        return result.tobytes()

    def _open_input(self) -> None:
        if self.input_stream:
            return
        device = self._device_index(str(self.config.get("input_device", "default")), True)
        self.input_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.device_capture_rate,
            input=True,
            input_device_index=device,
            frames_per_buffer=self.frames_per_buffer,
            stream_callback=self._capture_callback,
            start=True,
        )

    def _close_input(self) -> None:
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None
        while True:
            try:
                self.capture_queue.get_nowait()
            except queue.Empty:
                return

    def _open_output(self) -> None:
        if self.output_stream:
            return
        device = self._device_index(str(self.config.get("output_device", "default")), False)
        self.output_stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.device_playback_rate,
            output=True,
            output_device_index=device,
            frames_per_buffer=max(160, self.device_playback_rate * self.frame_ms // 1000),
            start=True,
        )

    def _stop_output(self) -> None:
        if self.output_stream:
            self.output_stream.stop_stream()

    def _close_output(self) -> None:
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None

    async def _send_status(self, websocket: Any, message: str) -> None:
        await websocket.send(json.dumps({"type": "node_status", "message": message}, ensure_ascii=False))

    async def _capture_sender(self, websocket: Any) -> None:
        while True:
            try:
                frame = await asyncio.to_thread(self.capture_queue.get, True, 0.25)
            except queue.Empty:
                continue
            # pause_capture 仅代表发送静音而非中断流，确保 ASR 不会因无包超时。
            if self.capture_enabled.is_set():
                await websocket.send(frame)

    async def _start_capture(self, websocket: Any) -> None:
        self._open_input()
        self.capture_paused.clear()
        self.capture_enabled.set()
        await self._send_status(websocket, f"麦克风已开始采集（硬件 {self.device_capture_rate} Hz → ASR {self.capture_rate} Hz）")

    async def _stop_capture(self, websocket: Any) -> None:
        self.capture_enabled.clear()
        self.capture_paused.clear()
        self._close_input()
        await websocket.send(json.dumps({"type": "capture_stopped"}))
        await self._send_status(websocket, "麦克风已停止")

    async def _handle_message(self, websocket: Any, message: bytes | str) -> None:
        if isinstance(message, bytes):
            if self.playback_active:
                self._open_output()
                await asyncio.to_thread(self.output_stream.write, self._to_device_playback_pcm(message))
            return
        payload = json.loads(message)
        kind = payload.get("type")
        if kind == "start_capture":
            await self._start_capture(websocket)
        elif kind == "stop_capture":
            await self._stop_capture(websocket)
        elif kind == "pause_capture":
            self.capture_paused.set()
        elif kind == "resume_capture":
            if self.capture_enabled.is_set():
                self.capture_paused.clear()
        elif kind == "playback_start":
            rate = int(payload.get("sample_rate", self.playback_rate))
            if rate != self.playback_rate:
                raise RuntimeError(f"电脑要求 {rate} Hz 播放，但节点配置为 {self.playback_rate} Hz")
            self.playback_active = True
            self._open_output()
            if self.output_stream.is_stopped():
                self.output_stream.start_stream()
        elif kind == "playback_end":
            self.playback_active = False
            # stream.write 已经是阻塞写；再留一段尾音保护时间再恢复麦克风。
            if self.echo_guard_seconds:
                await asyncio.sleep(self.echo_guard_seconds)
        elif kind == "cancel_playback":
            self.playback_active = False
            self._stop_output()

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self.server_url.startswith("wss://"):
            return None
        tls = self.config.get("tls", {}) or {}
        if tls.get("insecure_skip_verify", False):
            context = ssl._create_unverified_context()
        else:
            context = ssl.create_default_context(cafile=tls.get("ca_file") or None)
        return context

    async def run_forever(self) -> None:
        if not self.server_url or not self.token:
            raise ValueError("请填写 server_url，并设置 RTCVOICE_NODE_TOKEN 环境变量")
        reconnect_seconds = max(1, int(self.config.get("reconnect_seconds", 3)))
        while True:
            try:
                async with websockets.connect(
                    self.server_url,
                    ssl=self._ssl_context(),
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                ) as websocket:
                    await websocket.send(json.dumps({
                        "type": "hello",
                        "node_id": self.node_id,
                        "token": self.token,
                        "capture_sample_rate": self.capture_rate,
                        "playback_sample_rate": self.playback_rate,
                        "channels": self.channels,
                    }, ensure_ascii=False))
                    hello = json.loads(await asyncio.wait_for(websocket.recv(), timeout=8))
                    if hello.get("type") != "hello_ok":
                        raise RuntimeError("电脑端未确认音频节点")
                    if hello.get("capture"):
                        await self._start_capture(websocket)
                    print(f"已连接电脑端：{self.server_url}", flush=True)
                    sender = asyncio.create_task(self._capture_sender(websocket), name="capture-sender")
                    try:
                        async for message in websocket:
                            await self._handle_message(websocket, message)
                    finally:
                        sender.cancel()
                        self.capture_enabled.clear()
                        self.capture_paused.clear()
                        self._close_input()
                        self._stop_output()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"连接失败：{exc}；{reconnect_seconds} 秒后重试", file=sys.stderr, flush=True)
                await asyncio.sleep(reconnect_seconds)

    def close(self) -> None:
        self.capture_enabled.clear()
        self._close_input()
        self._close_output()
        self.audio.terminate()


def load_config(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description="圆宝树莓派音频节点")
    parser.add_argument("--config", default=Path(__file__).with_name("config.json"), type=Path)
    args = parser.parse_args()
    node = PiAudioNode(load_config(args.config))
    try:
        asyncio.run(node.run_forever())
    except KeyboardInterrupt:
        pass
    finally:
        node.close()


if __name__ == "__main__":
    main()
