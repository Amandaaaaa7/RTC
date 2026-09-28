"""电脑端的树莓派音频网关。

这个模块只传递 PCM 与少量控制消息：ASR、TTS、模型、记忆和所有云端密钥
仍留在电脑端。树莓派通过 WebSocket 主动连入，因此树莓派无需开放端口。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import ssl
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import websockets


class PiAudioGateway:
    """接收树莓派麦克风 PCM，并将 TTS PCM 回传给树莓派。"""

    def __init__(
        self,
        options: Dict[str, Any],
        on_audio: Callable[[bytes], None],
        on_status: Callable[[str, str], None],
    ) -> None:
        self.options = options
        self.on_audio = on_audio
        self.on_status = on_status
        self.host = str(options.get("listen_host", "0.0.0.0"))
        self.port = int(options.get("listen_port", 8765))
        self.node_id = str(options.get("node_id", "yuanbao-pi"))
        self.token = str(options.get("shared_token", ""))
        self.capture_active = False
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._capture_stopped = threading.Event()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._connection: Any = None
        self._outbound: Optional[asyncio.Queue[bytes | str]] = None
        self._shutdown: Optional[asyncio.Event] = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def status(self) -> Dict[str, Any]:
        return {
            "mode": "raspberry_pi",
            "node_id": self.node_id,
            "connected": self.is_connected,
            "capture_active": self.capture_active,
            "host": self.host,
            "port": self.port,
        }

    def start(self, timeout: float = 5.0) -> None:
        if not self.token or "填写" in self.token:
            raise ValueError("请填写 audio_io.shared_token 后再启用树莓派模式")
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._stopped.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run_thread, daemon=True, name="pi-audio-gateway")
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("树莓派音频网关启动超时")
        if self._startup_error:
            raise RuntimeError(f"树莓派音频网关启动失败：{self._startup_error}") from self._startup_error

    def stop(self) -> None:
        loop = self._loop
        shutdown = self._shutdown
        if loop and shutdown:
            loop.call_soon_threadsafe(shutdown.set)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def start_capture(self) -> None:
        if not self.is_connected:
            raise RuntimeError(f"树莓派音频节点“{self.node_id}”未连接")
        self.capture_active = True
        self._capture_stopped.clear()
        self._enqueue_json({"type": "start_capture"})
        self.on_status("node_status", "树莓派麦克风已启动")

    def stop_capture(self, timeout: float = 0.7) -> None:
        if not self.capture_active:
            return
        self.capture_active = False
        self._capture_stopped.clear()
        if self.is_connected:
            self._enqueue_json({"type": "stop_capture"})
            self._capture_stopped.wait(timeout)
        self.on_status("node_status", "树莓派麦克风已停止")

    def pause_capture(self) -> None:
        """播放 TTS 时临时暂停上传，减少扬声器回声被 ASR 识别的概率。"""
        if self.capture_active and self.is_connected:
            self._enqueue_json({"type": "pause_capture"})

    def resume_capture(self) -> None:
        if self.capture_active and self.is_connected:
            self._enqueue_json({"type": "resume_capture"})

    def begin_playback(self, playback_id: str, sample_rate: int) -> None:
        if self.is_connected:
            self._enqueue_json({"type": "playback_start", "playback_id": playback_id, "sample_rate": sample_rate})

    def send_audio(self, pcm: bytes) -> bool:
        if not pcm or not self.is_connected:
            return False
        return self._enqueue(pcm)

    def end_playback(self, playback_id: str) -> None:
        if self.is_connected:
            self._enqueue_json({"type": "playback_end", "playback_id": playback_id})

    def cancel_playback(self) -> None:
        if self.is_connected:
            # 尚未发往树莓派的旧语音不应排在“取消”命令之前。
            self._discard_outbound_threadsafe()
            self._enqueue_json({"type": "cancel_playback"})

    def _enqueue_json(self, value: Dict[str, Any]) -> bool:
        return self._enqueue(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def _enqueue(self, value: bytes | str) -> bool:
        loop = self._loop
        outbound = self._outbound
        if not loop or not outbound or not self.is_connected:
            return False

        accepted = threading.Event()

        def put() -> None:
            try:
                outbound.put_nowait(value)
                accepted.set()
            except asyncio.QueueFull:
                # 音频积压说明网络或树莓派播放跟不上。丢弃最新包比阻塞 TTS
                # 线程更安全，且不会导致下一轮回答越来越慢。
                self.on_status("node_status", "树莓派网络拥塞：已丢弃一段待发送音频")

        loop.call_soon_threadsafe(put)
        accepted.wait(0.05)
        return accepted.is_set()

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            self._connected.clear()
            self.capture_active = False
            self._stopped.set()

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        tls = self.options.get("tls", {}) or {}
        if not tls.get("enabled", False):
            return None
        cert_file = Path(str(tls.get("cert_file", "")))
        key_file = Path(str(tls.get("key_file", "")))
        if not cert_file.is_file() or not key_file.is_file():
            raise ValueError("已启用 audio_io.tls，但 cert_file 或 key_file 不存在")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_file), str(key_file))
        return context

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()
        self._outbound = asyncio.Queue(maxsize=300)
        ssl_context = self._build_ssl_context()
        async with websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ssl=ssl_context,
            max_size=2**20,
            ping_interval=20,
            ping_timeout=20,
        ):
            asyncio.create_task(self._sender(), name="pi-audio-sender")
            scheme = "wss" if ssl_context else "ws"
            self.on_status("node_status", f"树莓派音频网关已监听 {scheme}://{self.host}:{self.port}")
            self._ready.set()
            await self._shutdown.wait()

    async def _sender(self) -> None:
        assert self._outbound is not None
        while True:
            message = await self._outbound.get()
            connection = self._connection
            if not connection or not self.is_connected:
                continue
            try:
                await connection.send(message)
            except Exception:
                # 断线由连接处理协程统一更新状态；这里不让发送任务退出。
                continue

    async def _handle_connection(self, websocket: Any) -> None:
        try:
            raw_hello = await asyncio.wait_for(websocket.recv(), timeout=8)
            if not isinstance(raw_hello, str):
                await websocket.close(code=4000, reason="需要 hello JSON")
                return
            hello = json.loads(raw_hello)
            if hello.get("type") != "hello":
                await websocket.close(code=4000, reason="缺少 hello")
                return
            if hello.get("node_id") != self.node_id or not hmac.compare_digest(str(hello.get("token", "")), self.token):
                await websocket.close(code=4001, reason="认证失败")
                return
            capture = self.options.get("capture", {}) or {}
            playback = self.options.get("playback", {}) or {}
            expected_format = {
                "capture_sample_rate": int(capture.get("sample_rate", 16000)),
                "playback_sample_rate": int(playback.get("sample_rate", 24000)),
                "channels": int(capture.get("channels", 1)),
            }
            if any(int(hello.get(key, -1)) != value for key, value in expected_format.items()):
                await websocket.close(code=4003, reason="PCM 格式与电脑端配置不一致")
                return
            if self.is_connected:
                await websocket.close(code=4002, reason="已有树莓派音频节点在线")
                return
            self._connection = websocket
            self._connected.set()
            await websocket.send(json.dumps({"type": "hello_ok", "capture": self.capture_active}, ensure_ascii=False))
            self.on_status("node_status", f"树莓派音频节点“{self.node_id}”已连接")
            async for message in websocket:
                if isinstance(message, bytes):
                    if self.capture_active:
                        self.on_audio(message)
                    continue
                self._handle_control_message(message)
        except Exception as exc:
            self.on_status("node_status", f"树莓派音频节点连接异常：{exc}")
        finally:
            if self._connection is websocket:
                self._connection = None
                self._connected.clear()
                self.capture_active = False
                self._capture_stopped.set()
                self._discard_outbound()
                self.on_status("node_status", "树莓派音频节点已断开")

    def _handle_control_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        if payload.get("type") == "capture_stopped":
            self._capture_stopped.set()
        elif payload.get("type") == "node_status":
            detail = str(payload.get("message", ""))
            if detail:
                self.on_status("node_status", f"树莓派：{detail}")

    def _discard_outbound(self) -> None:
        outbound = self._outbound
        if not outbound:
            return
        while True:
            try:
                outbound.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _discard_outbound_threadsafe(self) -> None:
        loop = self._loop
        if loop:
            loop.call_soon_threadsafe(self._discard_outbound)
