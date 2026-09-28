"""
ReactiveVoiceModule — 最小反应式语音播放模块。

接入 MicMonitor 回调，检测到说话并静默后，随机播放一段预录情绪音频。
不保存录音、不调用 ASR/LLM、不需要网络。

Used by main.py --reactive-voice.
"""

import os
import queue
import random
import threading
import time
from collections import deque

import numpy as np

from voice.config import AUDIO_DIR, SAMPLE_RATE, BLOCK_SIZE
from voice.backends.preset import PresetAudioTTS


def _rms_level(frame: np.ndarray) -> float:
    """Normalized RMS level (0.0 - 1.0) for int32 samples."""
    if len(frame) == 0:
        return 0.0
    peak = 2_147_483_647.0
    rms = np.sqrt(np.mean(frame.astype(np.float64) ** 2))
    return min(rms / peak, 1.0)


class ReactiveVoiceModule:
    """
    基于 MicMonitor 回调的语音反应模块。

    Flow:
        1. 注册为 MicMonitor 的音频回调消费者。
        2. 音量超过 threshold 认为开始说话。
        3. 连续 silence_duration 秒低于 threshold 认为说话结束。
        4. 随机播放 audio_assets/vo/<emotion>/ 下的一个音频文件。
        5. 循环等待下一次说话。
    """

    def __init__(
        self,
        mic_monitor,
        threshold: float = 0.5,
        silence_duration: float = 0.5,
        volume: float = 0.3,
        emotion: str | None = None,
    ):
        self.mic = mic_monitor
        self.threshold = threshold
        self.silence_duration = silence_duration
        self.volume = volume
        self.emotion = emotion

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1024)
        self._thread: threading.Thread | None = None
        self._running = False
        self._silence_limit = int(silence_duration * SAMPLE_RATE / BLOCK_SIZE)

        self._tts = PresetAudioTTS(audio_dir=AUDIO_DIR, volume=volume)
        self._playback_lock = threading.Lock()

        # 状态供外部调试叠加层读取
        self.state = "等待说话"
        self.last_info = ""

        # 实时输出节流（避免终端被刷爆）
        self._last_log_time = 0.0
        self._log_interval = 0.1  # 每 100ms 打印一次 level

        # 空闲计时：15 秒无播放则主动触发
        self._idle_interval = 15.0
        self._last_play_time = time.time()

        # 歌曲计时：每 5 分钟的下一次随机播放从歌曲池抽取
        self._song_interval = 300.0
        self._last_song_time = time.time()

        # 自适应噪声底线：环境噪声会漂移（实测 0.037~0.042），固定阈值会失效
        self._noise_floor = 0.02
        self._noise_alpha = 0.001  # EMA 时间常数约 10 秒

        # Day 3 声音响应 timing。仅保留最近有限个数值样本，不保存音频或事件队列。
        # 这些时间均使用 monotonic_ns，避免系统校时使一次交互的相对耗时失真。
        self._timing_lock = threading.Lock()
        self._timing_callback = None
        self._interaction_id = 0
        self._active_interaction_id = None
        self._vad_onset_ns = None
        self._speech_end_estimate_ns = None
        self._silence_confirmed_ns = None
        self._vad_to_listening_cue_ms = deque(maxlen=256)
        self._speech_end_to_silence_confirmed_ms = deque(maxlen=256)
        self._silence_confirmed_to_first_audio_sample_ms = deque(maxlen=256)
        self._last_timing_event = None

    def set_timing_callback(self, callback):
        """Attach EyeDisplay's lightweight timing callback after it is constructed."""
        self._timing_callback = callback

    @staticmethod
    def _percentiles(values):
        if not values:
            return {"p50": None, "p95": None}
        ordered = sorted(values)
        return {
            "p50": round(ordered[round((len(ordered) - 1) * 0.50)], 3),
            "p95": round(ordered[round((len(ordered) - 1) * 0.95)], 3),
        }

    def _notify_timing(self, event, interaction_id, timestamp_ns):
        """Send timing events without letting an optional UI callback break audio handling."""
        self._last_timing_event = {
            "event": event,
            "interaction_id": interaction_id,
            "timestamp_ns": timestamp_ns,
        }
        callback = self._timing_callback
        if callback is not None:
            try:
                callback(event, interaction_id, timestamp_ns)
            except Exception as exc:
                print(f"[REACTIVE] timing callback error: {exc}")

    def on_listening_cue_frame(self, interaction_id, displayed_at_ns):
        """Called once by EyeDisplay after the VAD cue has reached display()."""
        with self._timing_lock:
            if interaction_id != self._active_interaction_id or self._vad_onset_ns is None:
                return
            elapsed_ms = (displayed_at_ns - self._vad_onset_ns) / 1_000_000
            if elapsed_ms >= 0:
                self._vad_to_listening_cue_ms.append(elapsed_ms)

    def _on_first_audio_sample(self, interaction_id, written_at_ns):
        """Called once after the first audio block has been accepted by ALSA."""
        with self._timing_lock:
            if interaction_id != self._active_interaction_id or self._silence_confirmed_ns is None:
                return
            elapsed_ms = (written_at_ns - self._silence_confirmed_ns) / 1_000_000
            if elapsed_ms >= 0:
                self._silence_confirmed_to_first_audio_sample_ms.append(elapsed_ms)
                self._notify_timing("response_first_audio_sample", interaction_id, written_at_ns)

    def _update_noise_floor(self, level: float):
        """用全部帧缓慢更新噪声底线；限制单帧上限，突发语音不会大幅抬高基线。"""
        capped = min(level, self._noise_floor * 1.5 + 0.005)
        self._noise_floor += self._noise_alpha * (capped - self._noise_floor)

    def _voice_threshold(self) -> float:
        """语音判定阈值：取配置下限与噪声底线 +0.03 的较大值。"""
        return max(self.threshold, self._noise_floor + 0.03)

    def _silence_threshold(self) -> float:
        """静音判定阈值：噪声底线 +0.01，保证环境噪声能凑满静音。"""
        return max(self.threshold * 0.5, self._noise_floor + 0.01)

    def on_audio_frame(self, frame: np.ndarray):
        """Called by MicMonitor for each audio frame. Must not block."""
        try:
            self._queue.put_nowait(frame.copy())
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(frame.copy())
            except queue.Empty:
                pass

    def start(self):
        """Start the reactive detection thread and register MicMonitor callback."""
        if self._running:
            return
        self._running = True
        self.state = "等待说话"
        self.last_info = ""
        if self.mic:
            self.mic.register_audio_callback(self.on_audio_frame)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="reactive-voice")
        self._thread.start()
        print(f"[REACTIVE] 反应式语音模块已启动 (threshold={self.threshold}, silence={self.silence_duration}s, volume={self.volume})")

    def stop(self):
        """Stop the reactive detection thread and unregister MicMonitor callback."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.mic:
            try:
                self.mic.unregister_audio_callback(self.on_audio_frame)
            except Exception:
                pass
        self.state = "已停止"
        print("[REACTIVE] 反应式语音模块已停止")

    def _loop(self):
        """Continuously wait for speech, silence, then play."""
        while self._running:
            if not self._wait_for_voice():
                return
            if not self._wait_for_silence():
                return
            self._play_response()

    def _log_level(self, level: float):
        """Throttle-print current RMS level to terminal."""
        now = time.time()
        if now - self._last_log_time >= self._log_interval:
            self._last_log_time = now
            print(f"[REACTIVE] level={level:.4f} floor={self._noise_floor:.4f} vth={self._voice_threshold():.4f}")

    def _wait_for_voice(self) -> bool:
        """Wait until RMS exceeds threshold, or idle timer fires. Returns False if stopping."""
        self.state = "等待说话"
        print(f"[REACTIVE] 等待说话... (vth={self._voice_threshold():.4f})")
        while self._running:
            # 短 timeout：既能快速响应语音，又能在每次超时时检查 idle 计时器
            try:
                frame = self._queue.get(timeout=0.05)
            except queue.Empty:
                # 队列空 → 检查空闲计时器
                if time.time() - self._last_play_time >= self._idle_interval:
                    with self._timing_lock:
                        self._active_interaction_id = None
                        self._vad_onset_ns = None
                        self._speech_end_estimate_ns = None
                        self._silence_confirmed_ns = None
                    self.state = "空闲触发"
                    self.last_info = "idle 15s"
                    print(f"[REACTIVE] 空闲 {self._idle_interval}s 无播放，主动触发")
                    return True
                continue

            level = _rms_level(frame)
            self._update_noise_floor(level)
            self._log_level(level)
            if level > self._voice_threshold():
                onset_ns = time.monotonic_ns()
                with self._timing_lock:
                    self._interaction_id += 1
                    self._active_interaction_id = self._interaction_id
                    self._vad_onset_ns = onset_ns
                    self._speech_end_estimate_ns = None
                    self._silence_confirmed_ns = None
                    interaction_id = self._active_interaction_id
                self._notify_timing("vad_onset", interaction_id, onset_ns)
                self.state = "开始监听"
                self.last_info = f"level={level:.4f}"
                print(f"[REACTIVE] 检测到声音 (level={level:.4f})，开始监听...")
                return True
            # 麦克风持续产帧时队列通常不会为空，因此空闲计时不能只放在
            # queue.Empty 分支；否则“15 秒无人说话自主播放”永远不会触发。
            if time.time() - self._last_play_time >= self._idle_interval:
                with self._timing_lock:
                    self._active_interaction_id = None
                    self._vad_onset_ns = None
                    self._speech_end_estimate_ns = None
                    self._silence_confirmed_ns = None
                self.state = "空闲触发"
                self.last_info = f"idle {self._idle_interval:g}s"
                print(f"[REACTIVE] 空闲 {self._idle_interval}s 无播放，主动触发")
                return True
        return False

    def _wait_for_silence(self) -> bool:
        """Wait until silence duration is reached. Returns False if stopping."""
        silence_blocks = 0
        self.state = "开始监听"
        while self._running:
            # 长时间卡在监听状态（环境噪声持续压线，凑不满静音）→ 主动触发播放并退回等待，避免卡死
            if time.time() - self._last_play_time >= self._idle_interval:
                with self._timing_lock:
                    self._active_interaction_id = None
                    self._vad_onset_ns = None
                    self._speech_end_estimate_ns = None
                    self._silence_confirmed_ns = None
                self.state = "空闲触发"
                self.last_info = "idle 15s"
                print(f"[REACTIVE] 空闲 {self._idle_interval}s 无播放，主动触发")
                return True
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            level = _rms_level(frame)
            self._update_noise_floor(level)
            self._log_level(level)
            if level > self._silence_threshold():
                silence_blocks = 0
                # 用户又继续说话：此前第一个低音量帧只是错误候选，不能当作结束。
                with self._timing_lock:
                    self._speech_end_estimate_ns = None
            else:
                now_ns = time.monotonic_ns()
                with self._timing_lock:
                    if self._speech_end_estimate_ns is None:
                        self._speech_end_estimate_ns = now_ns
                        interaction_id = self._active_interaction_id
                        if interaction_id is not None:
                            self._notify_timing("speech_end_estimate", interaction_id, now_ns)
                silence_blocks += 1
                if silence_blocks >= self._silence_limit:
                    confirmed_ns = time.monotonic_ns()
                    with self._timing_lock:
                        interaction_id = self._active_interaction_id
                        self._silence_confirmed_ns = confirmed_ns
                        if self._speech_end_estimate_ns is not None:
                            elapsed_ms = (confirmed_ns - self._speech_end_estimate_ns) / 1_000_000
                            if elapsed_ms >= 0:
                                self._speech_end_to_silence_confirmed_ms.append(elapsed_ms)
                    if interaction_id is not None:
                        self._notify_timing("silence_confirmed", interaction_id, confirmed_ns)
                    self.state = "触发播放"
                    self.last_info = f"silent {self.silence_duration}s"
                    print(f"[REACTIVE] 静音 {self.silence_duration}s，说话结束，触发播放")
                    return True
        return False

    def _play_response(self):
        """Play a random preset emotion audio file."""
        if not self._playback_lock.acquire(blocking=False):
            return
        try:
            self.state = "正在播放TTS"
            print("[REACTIVE] 正在播放...")
            interaction_id = self._active_interaction_id
            first_sample_callback = (
                (lambda timestamp_ns: self._on_first_audio_sample(interaction_id, timestamp_ns))
                if interaction_id is not None else None
            )
            self._tts.set_first_sample_callback(first_sample_callback)
            playback = None
            if self.emotion:
                self.last_info = f"{self.emotion}"
                playback = self._tts.speak("", emotion=self.emotion)
            else:
                # 每 5 分钟的下一次随机播放属于歌曲池
                now = time.time()
                songs = self._tts.list_songs()
                if songs and now - self._last_song_time >= self._song_interval:
                    self.last_info = "song"
                    playback = self._tts.play_random_song()
                    if playback:
                        self._last_song_time = time.time()
                else:
                    emotions = self._tts.list_emotions()
                    if emotions:
                        emotion = random.choice(emotions)
                        self.last_info = f"{emotion}"
                        playback = self._tts.speak("", emotion=emotion)
                    else:
                        self.last_info = "no audio"
                        print("[REACTIVE] 未找到情绪音频目录")
            # Only the reactive voice worker waits here to suppress speaker
            # echo.  Playback itself is on speaker.py's worker, so eye/face
            # animation never wait for the whole clip.
            if hasattr(playback, "wait"):
                playback.wait()
            self.state = "播放结束"
            self.last_info = ""
            self._last_play_time = time.time()
            if interaction_id is not None:
                self._notify_timing("response_finished", interaction_id, time.monotonic_ns())
            print("[REACTIVE] 播放结束，继续监听")
        finally:
            self._tts.set_first_sample_callback(None)
            self._playback_lock.release()
        # 播放期间采集到的音频帧全部丢弃，避免扬声器回声自我触发
        dropped = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        if dropped > 0:
            print(f"[REACTIVE] 播放后丢弃 {dropped} 帧回声")

    def health(self) -> dict:
        """Return a quick health snapshot."""
        return {
            "running": self._running,
            "threshold": self.threshold,
            "silence_duration": self.silence_duration,
            "volume": self.volume,
            "emotion": self.emotion,
            "mic_attached": self.mic is not None,
        }

    def get_metrics(self) -> dict:
        """Return bounded Day 3 voice timing metrics for /status and the collector."""
        with self._timing_lock:
            return {
                "state": self.state,
                "active_interaction_id": self._active_interaction_id,
                "last_event": self._last_timing_event,
                "vad_to_listening_cue_ms": self._percentiles(self._vad_to_listening_cue_ms),
                "speech_end_to_silence_confirmed_ms": self._percentiles(
                    self._speech_end_to_silence_confirmed_ms),
                "silence_confirmed_to_first_audio_sample_ms": self._percentiles(
                    self._silence_confirmed_to_first_audio_sample_ms),
                "vad_to_listening_cue_count": len(self._vad_to_listening_cue_ms),
                "speech_end_to_silence_confirmed_count": len(
                    self._speech_end_to_silence_confirmed_ms),
                "silence_confirmed_to_first_audio_sample_count": len(
                    self._silence_confirmed_to_first_audio_sample_ms),
                "silence_duration_s": self.silence_duration,
            }
