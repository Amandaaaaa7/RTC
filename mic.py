"""
麦克风模块 — INMP441 I2S 麦克风持续电平监控

基于 pi-mic 项目的成功经验，使用 pyalsaaudio 直接操作 ALSA。
googlevoicehat-soundcard: 48kHz, S32_LE, 2ch → 取左声道 (INMP441 L/R=GND)
"""

import threading
import time
import numpy as np
import alsaaudio as alsa


# ============================================================
# 常量 (与 pi-mic 保持一致)
# ============================================================
SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 2048      # ~43ms 每块
FORMAT = alsa.PCM_FORMAT_S32_LE
DTYPE = np.int32


def find_i2s_device() -> str:
    """自动查找 I2S 录音设备，回退到 hw:1,0。"""
    try:
        cards = alsa.cards()
        for i, card in enumerate(cards):
            if any(kw in card.lower() for kw in
                   ['googlevoice', 'voicehat', 'soundcar', 'sndrpig']):
                print(f"[MIC] 找到 I2S 设备: card={i} -> hw:{i},0")
                return f"hw:{i},0"
    except Exception as e:
        print(f"[MIC] 搜索设备失败: {e}")
    print("[MIC] 未自动找到 I2S 设备，使用 hw:1,0")
    return "hw:1,0"


class MicMonitor:
    """
    后台麦克风电平监视器。

    在独立线程中持续采集音频，计算 RMS/Peak 电平，
    通过线程安全接口对外暴露，供主程序或 HTTP 服务使用。
    支持注册多个音频回调消费者（如 VoiceModule），避免 ALSA 设备独占冲突。
    """

    def __init__(self, device: str = "hw:1,0", gain: float = 8.0):
        """
        Args:
            device: ALSA 设备名
            gain: 软件增益倍率 (默认 8x，安静时接近底部，正常说话到中间)
        """
        self.device = device
        self.gain = gain
        self.running = False
        self._thread: threading.Thread | None = None

        # 电平状态 (线程安全, 通过 property 访问)
        self._lock = threading.Lock()
        self._rms = 0.0
        self._peak = 0.0
        self._clipping = False
        self._samples_read = 0

        # 录音缓冲 (浏览器触发)
        self._recording = False
        self._record_buf: list[np.ndarray] = []
        self._record_lock = threading.Lock()

        # 音频回调消费者
        self._callbacks: list[callable] = []
        self._callback_lock = threading.Lock()

        # 播放期间暂停回调消费，防止麦克风被扬声器声音触发
        self._pause_event = threading.Event()
        self._pause_event.set()  # 默认不暂停

    # ---- 音频回调注册 ----

    def register_audio_callback(self, callback: callable):
        """注册一个音频帧回调函数。callback(frame: np.ndarray[int32]) -> None"""
        with self._callback_lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unregister_audio_callback(self, callback: callable):
        """注销音频帧回调函数。"""
        with self._callback_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def pause_callbacks(self):
        """暂停所有音频回调，播放期间调用"""
        self._pause_event.clear()

    def resume_callbacks(self):
        """恢复所有音频回调，播放结束后调用"""
        self._pause_event.set()

    def drain_all_queues(self):
        """播放结束后调用，清空所有已注册回调的队列（防止播放期间堆积的帧触发误识别）"""
        with self._callback_lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            if hasattr(cb, '_queue'):
                dropped = 0
                try:
                    while True:
                        cb._queue.get_nowait()
                        dropped += 1
                except Exception:
                    pass
                if dropped > 0:
                    print(f"[MIC] 播放后丢弃 {dropped} 帧")

    # ---- 线程安全读取 ----

    @property
    def rms(self) -> float:
        with self._lock:
            return self._rms

    @property
    def peak(self) -> float:
        with self._lock:
            return self._peak

    @property
    def clipping(self) -> bool:
        with self._lock:
            return self._clipping

    @property
    def samples_read(self) -> int:
        with self._lock:
            return self._samples_read

    def get_levels(self) -> dict:
        """一次性获取所有电平数据。"""
        with self._lock:
            return {
                "rms": self._rms,
                "peak": self._peak,
                "clipping": self._clipping,
                "samples_read": self._samples_read,
            }

    # ---- 内部采集循环 ----

    def _capture_loop(self):
        try:
            pcm = alsa.PCM(alsa.PCM_CAPTURE, device=self.device)
            pcm.setchannels(CHANNELS)
            pcm.setrate(SAMPLE_RATE)
            pcm.setformat(FORMAT)
            pcm.setperiodsize(BLOCK_SIZE)
        except Exception as e:
            print(f"[MIC] 打开 PCM 设备失败: {e}")
            self.running = False
            return

        print(f"[MIC] 开始采集 ({SAMPLE_RATE}Hz, {CHANNELS}ch, BLOCK_SIZE={BLOCK_SIZE})")

        while self.running:
            try:
                length, data = pcm.read()
                if length <= 0:
                    continue

                frame = np.frombuffer(data, dtype=DTYPE).reshape(-1, CHANNELS)
                left = frame[:, 0].astype(np.float64)

                # 软件增益 (与 pi-mic 64x 一致)
                if self.gain != 1.0:
                    left = np.clip(left * self.gain, -2_147_483_648, 2_147_483_647)

                with self._lock:
                    self._samples_read += len(left)
                    self._rms = float(np.sqrt(np.mean(left ** 2)))
                    self._peak = float(np.max(np.abs(left)))
                    self._clipping = bool(np.any(np.abs(left) >= 2_000_000_000))

                # 录音缓冲 (存原始数据, 保存时统一 64x 增益)
                with self._record_lock:
                    if self._recording:
                        raw = frame[:, 0].astype(np.float64)
                        self._record_buf.append(raw)

                # 分发音频回调（播放期间暂停，防止扬声器声音触发麦克风）
                if not self._pause_event.is_set():
                    continue
                left_copy = left.copy()
                with self._callback_lock:
                    callbacks = list(self._callbacks)
                for cb in callbacks:
                    try:
                        cb(left_copy)
                    except Exception as e:
                        print(f"[MIC] 音频回调异常: {e}")

            except Exception as e:
                print(f"[MIC] 读取错误: {e}")
                time.sleep(0.1)

        pcm.close()
        print("[MIC] 采集线程结束")

    # ---- 录音缓冲 (浏览器录制) ----

    def start_recording(self):
        """开始缓冲音频数据。"""
        with self._record_lock:
            self._record_buf = []
            self._recording = True
        print(f"[REC] 开始录音缓冲")

    def stop_recording(self) -> np.ndarray | None:
        """停止录音并返回采集到的音频数据 (int32, 1ch)。"""
        with self._record_lock:
            self._recording = False
            if not self._record_buf:
                return None
            data = np.concatenate(self._record_buf)
            self._record_buf = []
        print(f"[REC] 录音结束: {len(data)} 样本 ({len(data)/SAMPLE_RATE:.2f}s)")
        return data

    def save_recording(self, data: np.ndarray, filename: str | None = None,
                       gain: float = 64.0, normalize: bool = True) -> str:
        """
        将录音数据保存为 24-bit WAV，同时保留监控增益不变。
        录音时始终用 64x + 归一化以保证可听。
        """
        if filename is None:
            filename = f"rec_{int(time.time())}.wav"

        # 应用增益
        if gain != 1.0:
            data = np.clip(data * gain, -2_147_483_648, 2_147_483_647).astype(np.int32)
        # 归一化
        if normalize:
            peak = max(np.abs(np.max(data)), np.abs(np.min(data)))
            if peak > 0:
                scale = 0.95 * 2_147_483_647 / peak
                data = (data * scale).astype(np.int32)

        # 24-bit 打包
        d24 = ((data >> 8) & 0xFFFFFF).astype(np.uint32)
        buf = np.zeros((len(d24), 4), dtype=np.uint8)
        buf[:, 0] = (d24 & 0xFF).astype(np.uint8)
        buf[:, 1] = ((d24 >> 8) & 0xFF).astype(np.uint8)
        buf[:, 2] = ((d24 >> 16) & 0xFF).astype(np.uint8)

        import wave
        with wave.open(filename, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(buf[:, :3].tobytes())

        print(f"[REC] 已保存: {filename} ({SAMPLE_RATE}Hz, 24-bit, mono)")
        return filename

    # ---- 生命周期 ----

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop,
                                        daemon=True, name="mic-monitor")
        self._thread.start()
        print(f"[MIC] 启动 (device={self.device}, gain={self.gain}x)")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        print("[MIC] 已停止")


import wave
import os


def record_to_wav(duration: int, device: str = "hw:1,0",
                  gain: float = 64.0, normalize: bool = True) -> str:
    """
    录音并保存为 24-bit WAV 文件。

    直接从 ALSA 采集，应用增益 + 可选归一化，保存为 48kHz 24-bit mono WAV。
    用于验证麦克风是否真的能录到声音。

    Args:
        duration: 录音秒数
        device: ALSA 设备名
        gain: 软件增益倍率 (默认 64x)
        normalize: 是否自动归一化到满量程

    Returns:
        WAV 文件路径
    """
    pcm = alsa.PCM(alsa.PCM_CAPTURE, device=device)
    pcm.setchannels(CHANNELS)
    pcm.setrate(SAMPLE_RATE)
    pcm.setformat(FORMAT)
    pcm.setperiodsize(BLOCK_SIZE)

    recorded = []
    total_blocks = int(SAMPLE_RATE / BLOCK_SIZE * duration)
    print(f"[REC] 录音 {duration}s ({total_blocks} 块, {gain}x增益)...")

    try:
        for i in range(total_blocks):
            length, data = pcm.read()
            if length > 0:
                frame = np.frombuffer(data, dtype=DTYPE).reshape(-1, CHANNELS)
                recorded.append(frame[:, 0].copy())  # 左声道
            if i % max(1, total_blocks // 10) == 0:
                print(f"  {i * 100 // total_blocks}%", end="", flush=True)
        print("  100%")
    finally:
        pcm.close()

    if not recorded:
        raise RuntimeError("未采集到任何音频数据")

    all_data = np.concatenate(recorded)
    print(f"[REC] 采集 {len(all_data)} 样本 ({len(all_data)/SAMPLE_RATE:.2f}s)")

    # 应用增益 (带防削波)
    if gain != 1.0:
        all_data = np.clip(all_data * gain, -2_147_483_648, 2_147_483_647).astype(np.int32)

    # 归一化
    if normalize:
        peak = max(np.abs(np.max(all_data)), np.abs(np.min(all_data)))
        if peak > 0:
            scale = 0.95 * (2_147_483_647) / peak
            all_data = (all_data * scale).astype(np.int32)
            print(f"[REC] 归一化: 峰值={peak} 放大{scale:.1f}x")

    # 24-bit 打包
    d24 = ((all_data >> 8) & 0xFFFFFF).astype(np.uint32)
    buf = np.zeros((len(d24), 4), dtype=np.uint8)
    buf[:, 0] = (d24 & 0xFF).astype(np.uint8)
    buf[:, 1] = ((d24 >> 8) & 0xFF).astype(np.uint8)
    buf[:, 2] = ((d24 >> 16) & 0xFF).astype(np.uint8)

    filename = f"rec_{int(time.time())}.wav"
    with wave.open(filename, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(buf[:, :3].tobytes())

    print(f"[REC] OK: {filename} ({SAMPLE_RATE}Hz, 24-bit, mono)")
    return filename


def dbfs_to_bar(dbfs: float, width: int = 40) -> str:
    """
    将 dBFS 值映射到进度条。

    -80dBFS(底噪) = 0格, 0dBFS(满量程) = 满格。
    这样正常说话(-40~-20dBFS)能看到明显跳动。
    """
    floor, ceiling = -80, 0
    pct = 0.0 if dbfs <= floor else 100.0 if dbfs >= ceiling else (dbfs - floor) / (ceiling - floor) * 100
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_vu_bar(level: float, peak: float, width: int = 40):
    """
    在终端打印 VU 表 (dBFS 映射)。

    用于 --console-vu 模式下的可视化。
    """
    max_level = 2_000_000_000  # int32 最大值参考

    r = min(level / max_level, 1.0) if max_level > 0 else 0
    p = min(peak / max_level, 1.0) if max_level > 0 else 0

    rms_dbfs = 20 * np.log10(max(r, 1e-10))
    peak_dbfs = 20 * np.log10(max(p, 1e-10))

    bar = dbfs_to_bar(rms_dbfs, width)

    print(f"\r[MIC] |{bar}| RMS={rms_dbfs:+6.1f}dBFS Peak={peak_dbfs:+6.1f}dBFS"
          f"  Peak={peak:>10.0f}", end="", flush=True)
