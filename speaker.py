"""
扬声器播放模块 — MAX98357A I2S 音频输出

通过 pyalsaaudio 的 PCM_PLAYBACK 播放 WAV 文件。
自动将任意格式 WAV 转换为 S32_LE 48kHz 立体声。
"""

import threading
import time
import wave
import os
import subprocess
import queue
from dataclasses import dataclass
import numpy as np

try:
    import alsaaudio as alsa
except ImportError:  # 让纯播放队列单测可在非树莓派环境运行。
    alsa = None

SAMPLE_RATE = 48000
CHANNELS = 2
FORMAT = alsa.PCM_FORMAT_S32_LE if alsa is not None else None

AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio")


class AudioPlaybackHandle:
    """One submitted playback job; callers may wait without owning the worker."""

    def __init__(self, name: str):
        self.name = name
        self._done = threading.Event()
        self.error: Exception | None = None

    def wait(self, timeout: float | None = None) -> bool:
        """Return True after the job has completed (successfully or not)."""
        return self._done.wait(timeout)

    @property
    def completed(self) -> bool:
        return self._done.is_set()

    @property
    def succeeded(self) -> bool:
        return self.completed and self.error is None

    def _finish(self, error: Exception | None):
        self.error = error
        self._done.set()


@dataclass
class _PlaybackJob:
    name: str
    runner: object
    handle: AudioPlaybackHandle
    on_first_sample: object = None
    on_finished: object = None


class AudioPlaybackController:
    """Single-worker playback queue that never blocks a producer on audio I/O.

    ALSA only has one output device in this project.  Serialising jobs here avoids
    concurrent ``aplay``/PCM writers while keeping camera, face tracking and eye
    rendering independent from playback duration.
    """

    def __init__(self, max_queue: int = 2):
        if max_queue < 1:
            raise ValueError("max_queue must be at least 1")
        self._queue: queue.Queue[_PlaybackJob] = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._state = "idle"
        self._active_name = None
        self._started_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._rejected_count = 0
        self._last_started_ns = None
        self._last_finished_ns = None
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="audio-playback"
        )
        self._thread.start()

    def submit(self, name: str, runner, on_first_sample=None, on_finished=None):
        """Queue ``runner(first_sample_callback)`` and return immediately.

        A full queue deliberately rejects new work rather than making a UI, VAD,
        or voice thread wait behind a long song.
        """
        handle = AudioPlaybackHandle(name)
        job = _PlaybackJob(name, runner, handle, on_first_sample, on_finished)
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._lock:
                self._rejected_count += 1
            print(f"[SPK] 播放队列已满，忽略: {name}")
            return None
        with self._lock:
            if self._state == "idle":
                self._state = "queued"
        return handle

    def _worker(self):
        while True:
            job = self._queue.get()
            with self._lock:
                self._state = "playing"
                self._active_name = job.name
                self._started_count += 1
                self._last_started_ns = time.monotonic_ns()

            first_sample_sent = False

            def first_sample(timestamp_ns):
                nonlocal first_sample_sent
                if first_sample_sent:
                    return
                first_sample_sent = True
                if job.on_first_sample is not None:
                    try:
                        job.on_first_sample(timestamp_ns)
                    except Exception as callback_error:
                        # Timing/UI callbacks must never terminate real audio.
                        print(f"[SPK] 首采样回调错误: {callback_error}")

            error = None
            try:
                job.runner(first_sample)
            except Exception as exc:
                error = exc
                print(f"[SPK] 播放错误: {exc}")
            finally:
                finished_ns = time.monotonic_ns()
                job.handle._finish(error)
                if job.on_finished is not None:
                    try:
                        job.on_finished(finished_ns, error)
                    except Exception as callback_error:
                        print(f"[SPK] 播放完成回调错误: {callback_error}")
                with self._lock:
                    self._completed_count += 1
                    self._failed_count += int(error is not None)
                    self._last_finished_ns = finished_ns
                    self._active_name = None
                    self._state = "queued" if not self._queue.empty() else "idle"
                self._queue.task_done()

    def get_metrics(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "active_name": self._active_name,
                "queued_count": self._queue.qsize(),
                "started_count": self._started_count,
                "completed_count": self._completed_count,
                "failed_count": self._failed_count,
                "rejected_count": self._rejected_count,
                "last_started_ns": self._last_started_ns,
                "last_finished_ns": self._last_finished_ns,
            }


_playback_controller = AudioPlaybackController()


def get_playback_metrics() -> dict:
    """Expose bounded audio state to the HTTP status endpoint."""
    return _playback_controller.get_metrics()


def list_audio_files() -> list[dict]:
    """列出 audio/ 目录下的所有 WAV 文件。"""
    files = []
    if not os.path.isdir(AUDIO_DIR):
        return files
    for fname in sorted(os.listdir(AUDIO_DIR)):
        if not fname.lower().endswith(".wav"):
            continue
        path = os.path.join(AUDIO_DIR, fname)
        try:
            with wave.open(path) as wf:
                files.append({
                    "name": fname,
                    "channels": wf.getnchannels(),
                    "bits": wf.getsampwidth() * 8,
                    "rate": wf.getframerate(),
                    "frames": wf.getnframes(),
                    "size": os.path.getsize(path),
                })
        except Exception as e:
            files.append({"name": fname, "error": str(e)})
    return files


def _read_wav_as_float64(filepath: str) -> tuple[np.ndarray, int, int, int]:
    """
    读取 WAV 文件，返回 (float64_mono, rate, bits, channels)。
    值范围对应原始位深的满量程 (±2^(bits-1))。
    """
    with wave.open(filepath) as wf:
        ch = wf.getnchannels()
        bits = wf.getsampwidth() * 8
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    # 读取原始 PCM
    if bits == 16:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    elif bits == 24:
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        data = buf[:, 0].astype(np.uint32) | \
               (buf[:, 1].astype(np.uint32) << 8) | \
               (buf[:, 2].astype(np.uint32) << 16)
        data = np.where(data > 2 ** 23 - 1, data - 2 ** 24, data).astype(np.float64)
    elif bits == 32:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float64)
    elif bits == 8:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128
    else:
        raise ValueError(f"不支持的位深: {bits}")

    # 多声道 → 取左声道
    if ch > 1:
        data = data.reshape(-1, ch)[:, 0]

    return data, rate, bits, ch


def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """线性插值重采样。"""
    if src_rate == dst_rate:
        return data
    ratio = dst_rate / src_rate
    x_old = np.linspace(0, 1, len(data))
    x_new = np.linspace(0, 1, int(len(data) * ratio))
    return np.interp(x_new, x_old, data)


def _write_alsa(stereo: np.ndarray, device: str, on_first_sample=None):
    """将 int32 立体声数组分块写入 ALSA（避免整段 tobytes 的额外拷贝）。

    periodsize 从 1024 降到 512，并把 write chunk 从 4096 降到 1024，
    缩短 ALSA 缓冲延迟，让快反应音频在眼睛动作后尽快出声；
    512 周期在 Pi Zero 2W 上仍留有足够余量，避免 underrun。
    """
    if alsa is None:
        raise RuntimeError("pyalsaaudio 仅可在树莓派音频环境中使用")
    pcm = alsa.PCM(alsa.PCM_PLAYBACK, device=device)
    pcm.setchannels(CHANNELS)
    pcm.setrate(SAMPLE_RATE)
    pcm.setformat(FORMAT)
    pcm.setperiodsize(512)
    chunk_size = 1024
    first_sample_written = False
    for i in range(0, len(stereo), chunk_size):
        pcm.write(stereo[i:i + chunk_size].tobytes())
        if not first_sample_written:
            first_sample_written = True
            if on_first_sample is not None:
                on_first_sample(time.monotonic_ns())
    pcm.close()


def play_wav(filepath: str, device: str = "hw:1,0",
             volume: float = 0.5, blocking: bool = False,
             on_first_sample=None, on_finished=None):
    """
    播放 WAV 文件 (后台线程)。

    使用 ffmpeg 流式解码+重采样，管道送 aplay 输出 S32_LE 48kHz 立体声。
    内存占用恒定 (~10MB)，与文件大小无关，避免长歌曲导致 OOM。
    """
    def _play(first_sample):
        name = os.path.basename(filepath)
        # 只读头部信息用于日志（不加载帧数据）
        try:
            with wave.open(filepath) as wf:
                info = (f"{wf.getframerate()}Hz/"
                        f"{wf.getsampwidth() * 8}bit/{wf.getnchannels()}ch")
        except Exception:
            info = "unknown"
        print(f"[SPK] 播放: {name} ({info} → 48kHz/32bit/2ch)")

        ffmpeg_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", filepath,
            "-af", f"volume={volume}",
            "-f", "wav", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-c:a", "pcm_s32le", "-"
        ]
        aplay_cmd = ["aplay", "-D", device, "-"]
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE)
        aplay_proc = subprocess.Popen(aplay_cmd, stdin=ffmpeg_proc.stdout)
        ffmpeg_proc.stdout.close()  # 允许 ffmpeg 在 aplay 退出时收到 SIGPIPE
        # aplay 已启动并开始接受流，这是首个可记录的软件播放边界。
        first_sample(time.monotonic_ns())
        aplay_proc.wait()
        ffmpeg_proc.wait()
        print("[SPK] 播放完成")

    handle = _playback_controller.submit(
        os.path.basename(filepath), _play, on_first_sample, on_finished
    )
    if blocking:
        if handle is not None:
            handle.wait()
    return handle


def _play_buffer_sync(pcm_bytes: bytes, sample_rate: int, channels: int, sample_width: int,
                      device: str, volume: float, on_first_sample=None):
    """
    直接播放原始 PCM 字节数据（阻塞式）。

    Args:
        pcm_bytes: 原始 PCM 数据。
        sample_rate: 采样率 (Hz)。
        channels: 声道数 (1 或 2)。
        sample_width: 每个采样字节数 (1/2/3/4)。
        device: ALSA 播放设备。
        volume: 音量倍率 (0.0 - 1.0)。
    """
    if sample_width == 1:
        data = np.frombuffer(pcm_bytes, dtype=np.uint8).astype(np.float64) - 128
        scale = (2 ** 31 - 1) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
        scale = (2 ** 31 - 1) / (2 ** 15)
    elif sample_width == 3:
        buf = np.frombuffer(pcm_bytes, dtype=np.uint8).reshape(-1, 3)
        raw = buf[:, 0].astype(np.uint32) | \
              (buf[:, 1].astype(np.uint32) << 8) | \
              (buf[:, 2].astype(np.uint32) << 16)
        data = np.where(raw > 2 ** 23 - 1, raw - 2 ** 24, raw).astype(np.float64)
        scale = (2 ** 31 - 1) / (2 ** 23)
    elif sample_width == 4:
        data = np.frombuffer(pcm_bytes, dtype=np.int32).astype(np.float64)
        scale = 1.0
    else:
        raise ValueError(f"不支持的采样宽度: {sample_width}")

    # 重采样到 48kHz（如需要）
    if sample_rate != SAMPLE_RATE:
        data = _resample(data, sample_rate, SAMPLE_RATE)

    # 缩放到 32-bit 满量程并应用音量
    data = np.clip(data * scale * volume, -2_147_483_648, 2_147_483_647).astype(np.int32)

    # 单声道 → 立体声
    if channels == 1:
        data = np.column_stack([data, data])
    elif channels != 2:
        raise ValueError(f"不支持的声道数: {channels}")

    # ALSA 播放
    _write_alsa(data, device, on_first_sample=on_first_sample)


def play_buffer(pcm_bytes: bytes, sample_rate: int, channels: int, sample_width: int,
                device: str = "hw:1,0", volume: float = 0.5,
                on_first_sample=None, on_finished=None, blocking: bool = True):
    """Queue raw PCM playback; conversion and ALSA writes run in the audio worker."""

    def _play(first_sample):
        _play_buffer_sync(
            pcm_bytes, sample_rate, channels, sample_width, device, volume,
            on_first_sample=first_sample,
        )

    handle = _playback_controller.submit("pcm-buffer", _play, on_first_sample, on_finished)
    if blocking and handle is not None:
        handle.wait()
    return handle
