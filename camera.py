"""
摄像头模块 — Pi Camera MJPEG HTTP Stream + 麦克风电平显示

基于 camera-pi 项目的成功经验。
rpicam-vid 输出 MJPEG 流，通过 HTTP multipart/x-mixed-replace
在浏览器中显示，同时展示麦克风电平表。
"""

import json
from pathlib import Path
import threading
import subprocess
import signal
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ============================================================
# 配置
# ============================================================
RESOLUTIONS = [
    (2592, 1944, "2592x1944 (5MP)"),
    (1920, 1080, "1920x1080 (1080p)"),
    (1640, 1232, "1640x1232"),
    (1280, 720,  "1280x720  (720p)"),
    (640,  480,  "640x480   (VGA)"),
    (320,  240,  "320x240   (QVGA)"),
]
ROTATION = 180
BENCHMARK_DIR = Path(__file__).resolve().parent / "outputs" / "benchmarks" / "day2"


class CameraStreamer:
    """Camera streaming via rpicam-vid / libcamera-vid MJPEG output."""

    def __init__(self):
        self.res_idx = 0
        self.process = None
        self.reader_thread = None
        self._frame = None
        # 单调时钟：表示这一张 latest JPEG 在读取线程中完整组装完成的时刻。
        # 它不试图声称是传感器曝光时刻，但足以稳定比较端侧管线的等待时间。
        self._frame_captured_at_ns = 0
        self._lock = threading.Lock()
        self.running = True
        self.buf = b""
        self.frame_count = 0
        self.fps_val = 0
        self.last_fps_time = time.time()
        self.camera_cmd = None
        self._record_lock = threading.Lock()
        self._record_handle = None
        self._record_path = None
        self._record_deadline = 0.0
        self._recorded_frames = 0
        self._detect_camera_tool()

    def _detect_camera_tool(self):
        for cmd in ["rpicam-vid", "libcamera-vid"]:
            try:
                subprocess.run([cmd, "--help"], capture_output=True, timeout=5)
                self.camera_cmd = cmd
                print(f"[CAM] 使用摄像头工具: {cmd}")
                return
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
        print("[CAM] 错误: 未找到 rpicam-vid 或 libcamera-vid")
        print("   安装: sudo apt install rpicam-apps")

    @property
    def frame(self):
        with self._lock:
            return self._frame

    def get_frame_snapshot(self):
        """原子返回唯一 latest JPEG 及其捕获时间，不建立帧队列。"""
        with self._lock:
            return self._frame, self._frame_captured_at_ns

    @property
    def fps(self):
        return self.fps_val

    def start(self, res_idx=None):
        if res_idx is not None:
            self.res_idx = res_idx
        self._launch()

    def _launch(self):
        """启动 rpicam-vid 进程 + 读取线程。"""
        self.stop()
        w, h, name = RESOLUTIONS[self.res_idx]
        print(f"[CAM] 启动摄像头: {w}x{h} ({name})")

        cmd = [
            self.camera_cmd,
            "--codec", "mjpeg",
            "--width", str(w),
            "--height", str(h),
            "--framerate", "15",
            "--timeout", "0",
            "--output", "-",
            "--nopreview",
            "--flush",
            "--rotation", str(ROTATION),
        ]

        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            )
            self.reader_thread = threading.Thread(
                target=self._read_frames, daemon=True, name="cam-reader"
            )
            self.reader_thread.start()
            time.sleep(1.5)
            if self.process.poll() is not None:
                err = (self.process.stderr.read().decode("utf-8", errors="replace")
                       if self.process.stderr else "")
                print(f"[CAM] 摄像头进程退出 (code={self.process.returncode})")
                if err:
                    print(f"[CAM] stderr: {err.strip()}")
                self.process = None
            else:
                print(f"[CAM] 摄像头 OK ({w}x{h})")
        except FileNotFoundError:
            print(f"[CAM] {self.camera_cmd} 未找到")

    def _read_frames(self):
        """从 rpicam-vid stdout 读取 MJPEG 帧。"""
        while self.running and self.process and self.process.poll() is None:
            try:
                chunk = self.process.stdout.read(65536)
                if not chunk:
                    break
                self.buf += chunk

                while True:
                    soi = self.buf.find(b"\xff\xd8")
                    if soi < 0:
                        if len(self.buf) > 2 * 1024 * 1024:
                            self.buf = self.buf[-1024 * 1024:]
                        break
                    eoi = self.buf.find(b"\xff\xd9", soi + 2)
                    if eoi < 0:
                        if len(self.buf) > 2 * 1024 * 1024:
                            self.buf = self.buf[-1024 * 1024:]
                        break

                    frame = self.buf[soi:eoi + 2]
                    self.buf = self.buf[eoi + 2:]

                    with self._lock:
                        self._frame = frame
                        self._frame_captured_at_ns = time.monotonic_ns()
                    self._append_benchmark_frame(frame)
                    self.frame_count += 1

                    now = time.time()
                    if now - self.last_fps_time >= 5.0:
                        self.fps_val = round(self.frame_count / (now - self.last_fps_time), 1)
                        self.frame_count = 0
                        self.last_fps_time = now

            except (OSError, ValueError):
                break

        # 读取残余 stderr
        if self.process and self.process.stderr:
            try:
                rem = self.process.stderr.read()
                if rem:
                    print(f"[CAM stderr] {rem.decode('utf-8', errors='replace').strip()}")
            except OSError:
                pass

    def _append_benchmark_frame(self, frame):
        with self._record_lock:
            if self._record_handle is None:
                return
            self._record_handle.write(frame)
            self._recorded_frames += 1
            if time.monotonic() >= self._record_deadline:
                self._finish_benchmark_recording_locked()

    def _finish_benchmark_recording_locked(self):
        if self._record_handle is None:
            return
        self._record_handle.close()
        print(f"[CAM] benchmark MJPEG saved: {self._record_path} ({self._recorded_frames} frames)")
        self._record_handle = None
        self._record_deadline = 0.0

    def start_benchmark_recording(self, duration=60):
        duration = max(1, min(int(duration), 120))
        if self.res_idx != 5:
            raise ValueError("Select 320x240 (QVGA) in the web page before recording")
        with self._record_lock:
            if self._record_handle is not None:
                raise RuntimeError("A camera MJPEG recording is already in progress")
            BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self._record_path = BENCHMARK_DIR / f"qvga-ab-{stamp}.mjpeg"
            self._record_handle = self._record_path.open("wb")
            self._record_deadline = time.monotonic() + duration
            self._recorded_frames = 0
            print(f"[CAM] recording {duration}s MJPEG: {self._record_path}")
            return str(self._record_path)

    @property
    def benchmark_recording(self):
        with self._record_lock:
            return {"active": self._record_handle is not None, "path": str(self._record_path) if self._record_path else None, "frames": self._recorded_frames}

    def stop(self):
        """停止摄像头进程。"""
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process = None
            self.buf = b""

    def set_resolution(self, idx):
        """切换分辨率。"""
        if 0 <= idx < len(RESOLUTIONS):
            self.res_idx = idx
            self._launch()
            return True
        return False

    def cleanup(self):
        self.running = False
        with self._record_lock:
            self._finish_benchmark_recording_locked()
        self.stop()


# ============================================================
# HTTP 页面 HTML (嵌入 VU 表)
# ============================================================
HTML_PAGE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Doll Robot - 摄像头 + 麦克风</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1a1a2e; color: #eee; text-align: center;
    min-height: 100vh; display: flex; flex-direction: column; align-items: center;
}
h1 { margin: 15px 0 5px; font-size: 22px; color: #e94560; }
#res-name { font-size: 16px; color: #aaa; margin-bottom: 8px; }
#status-bar { display: flex; gap: 20px; justify-content: center; margin-bottom: 10px; }
#fps-display { font-size: 14px; color: #0f0; }

/* VU 表 */
#vu-container { display: flex; align-items: center; gap: 8px; }
#vu-label { font-size: 13px; color: #aaa; white-space: nowrap; }
#vu-track {
    width: 200px; height: 16px; background: #2a2a3e; border-radius: 8px;
    overflow: hidden; position: relative; border: 1px solid #444;
}
#vu-fill {
    height: 100%; width: 0%; border-radius: 8px;
    background: linear-gradient(90deg, #4caf50, #ffeb3b, #f44336);
    transition: width 0.05s linear;
}
#vu-dbfs { font-size: 12px; color: #888; min-width: 100px; text-align: left; }
#vu-peak { font-size: 12px; color: #ff9800; min-width: 70px; text-align: left; }
#vu-clip { font-size: 12px; color: #f44336; font-weight: bold; display: none; }

/* 摄像头 */
#video-wrapper {
    position: relative; display: inline-block;
    max-width: 95vw; max-height: 65vh;
    border: 3px solid #333; border-radius: 8px;
    background: #000; overflow: hidden;
}
#stream {
    display: block; width: 100%; height: auto;
    background: #000; image-rendering: auto;
}
#motion-overlay {
    position: absolute; width: 24px; height: 24px;
    transform: translate(-50%, -50%); pointer-events: none; display: none;
}
#motion-overlay::before, #motion-overlay::after {
    content: ''; position: absolute; background: rgba(255, 0, 0, 0.85);
}
#motion-overlay::before { top: 11px; left: 0; width: 24px; height: 2px; }
#motion-overlay::after { top: 0; left: 11px; width: 2px; height: 24px; }

/* 按钮 */
.btn-group { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center;
    margin: 10px 10px; max-width: 800px; }
.btn {
    padding: 8px 16px; border: 2px solid #555; border-radius: 6px;
    background: #16213e; color: #ccc; cursor: pointer; font-size: 13px;
    transition: all 0.2s;
}
.btn:hover { background: #0f3460; border-color: #e94560; }
.btn.active { background: #e94560; color: #fff; border-color: #e94560; font-weight: bold; }

#status-tip { font-size: 13px; color: #888; margin: 5px 0 10px; }
#rec-btn {
    padding: 6px 14px; border: 2px solid #f44336; border-radius: 6px;
    background: #2a1a1a; color: #f44336; cursor: pointer; font-size: 13px;
    transition: all 0.2s;
}
#rec-btn:hover { background: #4a1a1a; }
#rec-btn:disabled { opacity: 0.5; cursor: not-allowed; }
#rec-btn.recording { background: #f44336; color: #fff; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
#rec-download { display: none; margin-left: 8px; color: #4caf50; font-size: 13px; }

/* 可选实时语音面板：沿用原本机语音助手的完整会话布局。 */
#realtime-panel {
    display:none; width:min(760px,94vw); margin:16px auto 8px; overflow:hidden;
    text-align:left; background:#385a7c; border:1px solid #526e9a; border-radius:16px;
    box-shadow:0 12px 28px rgba(0,0,0,.2);
}
#realtime-header { padding:16px 18px 12px; color:#fff; background:linear-gradient(135deg,#667eea,#764ba2); }
#realtime-heading { display:flex; align-items:center; gap:11px; }
#realtime-avatar { width:38px; height:38px; display:grid; place-items:center; border-radius:50%; background:rgba(255,255,255,.2); font-size:21px; }
#realtime-title { font-size:18px; font-weight:700; }
#realtime-state-row { display:flex; align-items:center; gap:7px; margin-top:3px; font-size:13px; opacity:.96; }
#realtime-dot { width:9px; height:9px; border-radius:50%; background:#cbd5e1; }
#realtime-dot.active { background:#6ee7b7; box-shadow:0 0 0 4px rgba(110,231,183,.18); animation:realtime-pulse 1.8s infinite; }
@keyframes realtime-pulse { 50% { opacity:.55; } }
#realtime-meta { margin-top:10px; font-size:12px; color:#eeeaff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
#realtime-vad { display:flex; align-items:center; gap:8px; margin-top:9px; font-size:11px; color:#eeeaff; }
#realtime-vad-track { flex:1; height:6px; overflow:hidden; border-radius:99px; background:rgba(255,255,255,.3); }
#realtime-vad-fill { width:0; height:100%; border-radius:inherit; background:#6ee7b7; transition:width .12s ease; }
#realtime-vad-value { min-width:112px; text-align:right; font-variant-numeric:tabular-nums; }
#realtime-chat { min-height:230px; max-height:360px; overflow-y:auto; padding:16px 18px; }
.realtime-empty { padding:60px 16px; text-align:center; color:#d9e5f1; font-size:14px; }
.realtime-message { display:flex; flex-direction:column; max-width:80%; margin:0 0 14px; animation:realtime-enter .2s ease; }
@keyframes realtime-enter { from { opacity:0; transform:translateY(7px); } to { opacity:1; transform:none; } }
.realtime-message.user { align-items:flex-end; margin-left:auto; }
.realtime-message.assistant,.realtime-message.system { align-items:flex-start; }
.realtime-message-label { margin:0 5px 4px; color:#d8e4f4; font-size:11px; }
.realtime-message-body { padding:10px 13px; border-radius:15px; white-space:pre-wrap; word-break:break-word; line-height:1.5; font-size:14px; }
.realtime-message.user .realtime-message-body { border-bottom-right-radius:4px; background:linear-gradient(135deg,#667eea,#764ba2); color:#fff; }
.realtime-message.assistant .realtime-message-body { border-bottom-left-radius:4px; background:#fff; color:#1e293b; }
.realtime-message.system .realtime-message-body { border-bottom-left-radius:4px; background:#fce7f3; color:#86198f; }
#realtime-actions { display:flex; gap:8px; flex-wrap:wrap; padding:13px 16px; background:#fff; border-top:1px solid #dbe4ee; }
#realtime-actions .btn { flex:1; min-width:100px; padding:10px 14px; border:0; border-radius:999px; background:#eef2ff; color:#39457c; font-weight:600; }
#realtime-actions .btn:hover:not(:disabled) { background:#dfe5ff; border-color:transparent; }
#realtime-actions #realtime-start { background:linear-gradient(135deg,#667eea,#764ba2); color:#fff; }
#realtime-actions #realtime-stop { background:#fee2e2; color:#b91c1c; }
#realtime-actions #realtime-forget { flex:0 1 auto; background:#f8fafc; color:#64748b; }
#realtime-actions .btn:disabled { opacity:.42; cursor:not-allowed; }
</style>
</head>
<body>
<h1>Doll Robot - 摄像头 + 麦克风</h1>
<div id="res-name">Loading...</div>

<div id="status-bar">
    <div id="fps-display">FPS: --</div>
    <div id="vu-container">
        <span id="vu-label">MIC</span>
        <div id="vu-track"><div id="vu-fill"></div></div>
        <span id="vu-dbfs">-- dBFS</span>
        <span id="vu-peak">P: --</span>
        <span id="vu-clip">CLIP</span>
    </div>
    <button id="cam-rec-btn" onclick="startCameraRecord(60)">Record camera MJPEG 60s</button>
    <span id="cam-rec-status" style="font-size:12px;color:#aaa;"></span>
    <button id="rec-btn" onclick="startRecord(5)">录制 5s</button>
    <a id="rec-download" download style="display:none;margin-left:8px;color:#4caf50;font-size:13px;">下载</a>
</div>

<div class="btn-group" id="buttons"></div>

<div id="video-wrapper">
  <img id="stream" src="/stream" alt="Camera Stream">
  <div id="motion-overlay"></div>
</div>
<div id="status-tip">点击分辨率按钮切换</div>

<div id="audio-panel" style="margin:8px 0;display:none;">
    <div style="font-size:13px;color:#aaa;margin-bottom:4px;">扬声器</div>
    <div id="audio-buttons" style="display:flex;flex-wrap:wrap;gap:6px;justify-content:center;"></div>
    <div id="play-status" style="font-size:12px;color:#888;margin-top:4px;"></div>
</div>

<section id="realtime-panel" aria-live="polite">
    <div id="realtime-header">
        <div id="realtime-heading">
            <div id="realtime-avatar">🤖</div>
            <div>
                <div id="realtime-title">圆宝 · 实时语音助手</div>
                <div id="realtime-state-row"><span id="realtime-dot"></span><span id="realtime-state">加载中…</span></div>
            </div>
        </div>
        <div id="realtime-meta">正在连接本机语音服务…</div>
        <div id="realtime-vad"><span>输入</span><div id="realtime-vad-track"><div id="realtime-vad-fill"></div></div><span id="realtime-vad-value">--</span></div>
    </div>
    <div id="realtime-chat"></div>
    <div id="realtime-actions">
        <button class="btn" id="realtime-start">启动助手</button>
        <button class="btn" id="realtime-stop">停止助手</button>
        <button class="btn" id="realtime-clear">清空会话</button>
        <button class="btn" id="realtime-forget">删除记忆</button>
    </div>
</section>

<script>
const RES = [];
let curIdx = 0;

async function loadRes() {
    const r = await fetch('/resolutions').then(r => r.json());
    curIdx = r.pop().current;
    const c = document.getElementById('buttons');
    r.forEach(d => {
        RES.push(d);
        const b = document.createElement('button');
        b.className = 'btn' + (d.idx === curIdx ? ' active' : '');
        b.textContent = d.idx === curIdx ? '\\u25b6 ' + d.name : d.name;
        b.onclick = () => switchRes(d.idx);
        c.appendChild(b);
    });
    updateInfo();
}

function updateInfo() {
    const r = RES[curIdx];
    if (!r) return;
    document.getElementById('res-name').textContent = r.w + 'x' + r.h;
    // 只更新分辨率按钮；页面中的播放和实时语音按钮不属于该列表。
    document.querySelectorAll('#buttons .btn').forEach((b, i) => {
        const d = RES[i];
        b.className = 'btn' + (i === curIdx ? ' active' : '');
        b.textContent = i === curIdx ? '\\u25b6 ' + d.name : d.name;
    });
}

async function switchRes(idx) {
    document.getElementById('stream').src = '/stream?' + Date.now();
    document.getElementById('status-tip').textContent = '切换中...';
    await fetch('/resolution?idx=' + idx);
    curIdx = idx;
    updateInfo();
    setTimeout(() => {
        document.getElementById('stream').src = '/stream?' + Date.now();
        document.getElementById('status-tip').textContent =
            'Streaming ' + RES[idx].name;
    }, 500);
}

// FPS 更新
setInterval(async () => {
    try {
        const s = await fetch('/status').then(r => r.json());
        document.getElementById('fps-display').textContent = 'FPS: ' + s.fps;
    } catch(e) {}
}, 2000);

// VU 表更新 (~30fps)
async function updateVU() {
    try {
        const m = await fetch('/mic-level').then(r => r.json());
        const maxVal = 2_000_000_000;
        const r = Math.max(m.rms / maxVal, 1e-10);
        const dbfs = 20 * Math.log10(r);
        const pct = Math.max(0, Math.min((dbfs + 80) / 80 * 100, 100));

        document.getElementById('vu-fill').style.width = pct + '%';
        document.getElementById('vu-dbfs').textContent = dbfs.toFixed(1) + ' dBFS';
        document.getElementById('vu-peak').textContent = 'P: ' + m.peak.toLocaleString();
        document.getElementById('vu-clip').style.display = m.clipping ? 'inline' : 'none';
    } catch(e) {}
    requestAnimationFrame(updateVU);
}
requestAnimationFrame(updateVU);

// 运动追踪 / 人脸追踪 overlay
async function updateMotionOverlay() {
    try {
        // 优先使用人脸目标，无人脸时回退到运动目标
        let t = await fetch('/face-target').then(r => r.json());
        if (!t.detected) {
            t = await fetch('/motion-target').then(r => r.json());
        }
        const overlay = document.getElementById('motion-overlay');
        if (t.detected) {
            overlay.style.display = 'block';
            overlay.style.left = ((t.x + 1) / 2 * 100) + '%';
            overlay.style.top = ((t.y + 1) / 2 * 100) + '%';
        } else {
            overlay.style.display = 'none';
        }
    } catch(e) {}
    setTimeout(updateMotionOverlay, 200);
}
updateMotionOverlay();

loadRes();

// 加载音频文件列表
async function loadAudio() {
    try {
        const r = await fetch('/audio-files').then(r => r.json());
        const c = document.getElementById('audio-buttons');
        if (!r || r.length === 0) return;
        document.getElementById('audio-panel').style.display = 'block';
        r.forEach(f => {
            const b = document.createElement('button');
            b.className = 'btn';
            b.textContent = f.name.slice(0, -4);  // 去掉 .WAV 后缀
            b.style.borderColor = '#4caf50'; b.style.color = '#4caf50';
            b.onclick = async () => {
                document.getElementById('play-status').textContent = '播放中: ' + f.name;
                await fetch('/play?file=' + encodeURIComponent(f.name));
                setTimeout(() => {
                    document.getElementById('play-status').textContent = '';
                }, 3000);
            };
            c.appendChild(b);
        });
    } catch(e) {}
}
loadAudio();

let realtimeRevision = -1;
function renderRealtimeMessages(messages) {
    const chat = document.getElementById('realtime-chat');
    chat.replaceChildren();
    if (!messages || messages.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'realtime-empty';
        empty.textContent = '启动助手后，圆宝会在这里显示听到的话和回复。';
        chat.appendChild(empty);
        return;
    }
    (messages || []).forEach(message => {
        const item = document.createElement('div');
        item.className = 'realtime-message ' + (message.role || 'system');
        const label = document.createElement('div');
        label.className = 'realtime-message-label';
        label.textContent = message.role === 'user' ? '你' : (message.role === 'assistant' ? '圆宝' : '系统');
        const body = document.createElement('div');
        body.className = 'realtime-message-body';
        body.textContent = message.text || '';
        item.append(label, body);
        chat.appendChild(item);
    });
    chat.scrollTop = chat.scrollHeight;
}

async function realtimeAction(action) {
    const state = document.getElementById('realtime-state');
    try {
        const response = await fetch('/realtime-voice/' + action, {method: 'POST'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
        updateRealtimePanel(data, true);
    } catch (error) {
        state.textContent = '错误：' + error.message;
    }
}

function updateRealtimePanel(data, force) {
    const panel = document.getElementById('realtime-panel');
    if (!data.available) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = 'block';
    const running = !!data.running;
    document.getElementById('realtime-state').textContent = running
        ? ('助手运行中 · ' + (data.state || '等待说话')) : '助手已停止';
    document.getElementById('realtime-dot').classList.toggle('active', running);
    const diagnostic = data.diagnostics || {};
    const threshold = Number(diagnostic.vad_threshold || 0);
    const input = Number(diagnostic.recent_rms || 0);
    const fill = threshold > 0 ? Math.min(100, input / threshold * 100) : 0;
    document.getElementById('realtime-vad-fill').style.width = fill + '%';
    document.getElementById('realtime-vad-value').textContent = diagnostic.vad_calibrating
        ? '正在校准环境底噪…'
        : (threshold > 0
            ? ('输入 ' + input.toFixed(3) + ' / 门限 ' + threshold.toFixed(3))
            : '麦克风数据等待中');
    const turn = diagnostic.active_turn ? (' · 正在处理第 ' + diagnostic.active_turn + ' 轮') : '';
    document.getElementById('realtime-meta').textContent =
        (data.last_info || 'volc_direct') + turn +
        (diagnostic.vad_floor ? (' · 底噪 ' + Number(diagnostic.vad_floor).toFixed(3)) : '') +
        (data.memory && data.memory.enabled ? (' · 本地记忆 ' + data.memory.stored_turns + ' 轮') : '');
    document.getElementById('realtime-start').disabled = running;
    document.getElementById('realtime-stop').disabled = !running;
    if (force || data.revision !== realtimeRevision) {
        realtimeRevision = data.revision;
        renderRealtimeMessages(data.messages);
    }
}

async function pollRealtimePanel() {
    try {
        const response = await fetch('/realtime-voice/status');
        updateRealtimePanel(await response.json(), false);
    } catch (error) {}
    setTimeout(pollRealtimePanel, 350);
}
document.getElementById('realtime-start').onclick = () => realtimeAction('start');
document.getElementById('realtime-stop').onclick = () => realtimeAction('stop');
document.getElementById('realtime-clear').onclick = () => realtimeAction('clear');
document.getElementById('realtime-forget').onclick = () => {
    if (window.confirm('删除实时语音保存的本地记忆和日记？此操作无法撤销。')) realtimeAction('forget');
};
pollRealtimePanel();

document.getElementById('stream').onerror = function() {
    setTimeout(() => { this.src = '/stream?' + Date.now(); }, 2000);
};

// 录音按钮
async function startCameraRecord(duration) {
    const btn = document.getElementById('cam-rec-btn');
    const status = document.getElementById('cam-rec-status');
    btn.disabled = true;
    try {
        const resp = await fetch('/camera-record?duration=' + duration);
        const payload = await resp.json();
        if (!resp.ok) throw new Error(payload.error || 'HTTP ' + resp.status);
        status.textContent = 'Recording ' + duration + 's...';
        btn.textContent = 'Camera recording...';
        setTimeout(() => {
            status.textContent = 'Saved on Pi: ' + payload.path;
            btn.textContent = 'Record camera MJPEG 60s';
            btn.disabled = false;
        }, duration * 1000 + 800);
    } catch(e) {
        status.textContent = 'Camera recording failed: ' + e.message;
        btn.textContent = 'Record camera MJPEG 60s';
        btn.disabled = false;
    }
}

async function startRecord(duration) {
    const btn = document.getElementById('rec-btn');
    const link = document.getElementById('rec-download');
    btn.textContent = '录音中 ' + duration + 's...';
    btn.disabled = true;
    link.style.display = 'none';
    try {
        const resp = await fetch('/record?duration=' + duration);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        link.href = url;
        link.style.display = 'inline';
    } catch(e) {
        console.error(e);
    }
    btn.textContent = '录制 5s';
    btn.disabled = false;
}
</script>
</body>
</html>
"""


def create_server(host: str, port: int,
                  cam_streamer: CameraStreamer,
                  mic_monitor=None,
                  speaker_module=None,
                  motion_monitor=None,
                  face_monitor=None,
                  eye_monitor=None,
                  voice_monitor=None) -> ThreadingHTTPServer:
    """
    创建 HTTP 服务器工厂函数。
    cam_streamer, mic_monitor, speaker_module, motion_monitor, face_monitor 通过闭包注入 Handler 方法。
    """

    class StreamHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            params = parse_qs(parsed.query)

            if path == "/":
                self._serve_html()
            elif path == "/stream":
                self._serve_mjpeg()
            elif path == "/resolution":
                self._handle_resolution(params)
            elif path == "/resolutions":
                self._serve_resolutions()
            elif path == "/status":
                self._serve_status()
            elif path == "/mic-level":
                self._serve_mic_level()
            elif path == "/motion-target":
                self._serve_motion_target()
            elif path == "/face-target":
                self._serve_face_target()
            elif path == "/camera-record":
                self._serve_camera_record(params)
            elif path == "/record":
                self._serve_record(params)
            elif path == "/audio-files":
                self._serve_audio_files()
            elif path == "/play":
                self._serve_play(params)
            elif path == "/expression-debug":
                self._serve_expression_debug(params)
            elif path == "/realtime-voice/status":
                self._serve_realtime_voice_status()
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            path = urlparse(self.path).path
            if path in ("/realtime-voice/start", "/realtime-voice/stop", "/realtime-voice/clear", "/realtime-voice/forget"):
                self._handle_realtime_voice_action(path.rsplit("/", 1)[-1])
            else:
                self.send_response(404)
                self.end_headers()

        def _serve_html(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        def _serve_mjpeg(self):
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Connection", "close")
            self.end_headers()

            timeout = 10
            start = time.time()
            while cam_streamer.frame is None:
                time.sleep(0.1)
                if time.time() - start > timeout:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: text/plain\r\n\r\n")
                    self.wfile.write(b"Camera not responding.\r\n")
                    self.wfile.write(b"--frame--\r\n")
                    return

            try:
                last = None
                last_time = time.time()
                while True:
                    f = cam_streamer.frame
                    if f is not None and f is not last:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(f))
                        self.wfile.write(f)
                        self.wfile.write(b"\r\n")
                        last = f
                        last_time = time.time()
                    # 5秒无新帧 → 断开, 浏览器自动重连
                    if time.time() - last_time > 5:
                        break
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _handle_resolution(self, params):
            success = False
            if params and "idx" in params:
                idx = int(params["idx"][0])
                w, h, name = RESOLUTIONS[idx]
                print(f"[CAM] 切换分辨率: {w}x{h} ({name})")
                success = cam_streamer.set_resolution(idx)
            self.send_response(200 if success else 400)
            self.end_headers()
            self.wfile.write(b"OK" if success else b"FAIL")

        def _serve_resolutions(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            info = [
                {"idx": i, "w": w, "h": h, "name": n}
                for i, (w, h, n) in enumerate(RESOLUTIONS)
            ]
            info.append({"current": cam_streamer.res_idx})
            self.wfile.write(json.dumps(info).encode())

        def _serve_status(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "fps": cam_streamer.fps,
                "resolution": cam_streamer.res_idx,
                "benchmark_recording": cam_streamer.benchmark_recording,
                "face_metrics": face_monitor.get_metrics() if face_monitor else None,
                "eye_metrics": eye_monitor.get_metrics() if eye_monitor else None,
                "voice_metrics": voice_monitor.get_metrics() if voice_monitor else None,
                "audio_metrics": (
                    speaker_module.get_playback_metrics()
                    if speaker_module and hasattr(speaker_module, "get_playback_metrics")
                    else None
                ),
            }
            self.wfile.write(json.dumps(payload).encode())

        def _realtime_voice_available(self) -> bool:
            return voice_monitor is not None and hasattr(voice_monitor, "get_ui_snapshot")

        def _write_json(self, status: int, payload: dict) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def _serve_realtime_voice_status(self) -> None:
            if not self._realtime_voice_available():
                self._write_json(200, {"available": False})
                return
            self._write_json(200, voice_monitor.get_ui_snapshot())

        def _handle_realtime_voice_action(self, action: str) -> None:
            if not self._realtime_voice_available():
                self._write_json(404, {"error": "实时语音未启用"})
                return
            methods = {
                "start": "start_from_ui",
                "stop": "stop_from_ui",
                "clear": "clear_from_ui",
                "forget": "forget_from_ui",
            }
            method = getattr(voice_monitor, methods[action])
            try:
                self._write_json(200, method())
            except Exception as exc:
                self._write_json(409, {"error": str(exc)})

        def _serve_expression_debug(self, params):
            """Opt-in Day 3 expression command/status endpoint; disabled in normal runs."""
            if eye_monitor is None or not getattr(eye_monitor, "expression_debug_enabled", False):
                self.send_response(404)
                self.end_headers()
                return
            try:
                if "name" in params:
                    duration_ms = params.get("duration_ms", [None])[0]
                    snapshot = eye_monitor.command_expression_debug(
                        params["name"][0], duration_ms=duration_ms
                    )
                else:
                    snapshot = eye_monitor.get_expression_debug_snapshot()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(snapshot).encode())
            except (TypeError, ValueError) as exc:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode())

        def _serve_mic_level(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if mic_monitor:
                levels = mic_monitor.get_levels()
                self.wfile.write(json.dumps(levels).encode())
            else:
                self.wfile.write(json.dumps({
                    "rms": 0, "peak": 0, "clipping": False, "samples_read": 0
                }).encode())

        def _serve_motion_target(self):
            """返回当前运动检测目标（用于网页 overlay）。"""
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if motion_monitor:
                x, y, detected = motion_monitor.get_target()
                self.wfile.write(json.dumps({
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "detected": detected,
                }).encode())
            else:
                self.wfile.write(json.dumps({
                    "x": 0.0, "y": 0.0, "detected": False,
                }).encode())

        def _serve_face_target(self):
            """Return one unambiguous calibration sample from the latest face."""
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if face_monitor:
                target = face_monitor.get_snapshot()
                payload = {
                    "detected": target.detected,
                    # x/y keep the original browser overlay compatible.
                    "x": round(target.x, 3),
                    "y": round(target.y, 3),
                    "eye_x": round(target.x, 3),
                    "eye_y": round(target.y, 3),
                    "camera_x": (
                        round(target.camera_x, 3) if target.detected else None
                    ),
                    "camera_y": (
                        round(target.camera_y, 3) if target.detected else None
                    ),
                    "score": round(target.confidence, 3) if target.detected else None,
                }
            else:
                payload = {
                    "detected": False,
                    "x": 0.0, "y": 0.0,
                    "eye_x": 0.0, "eye_y": 0.0,
                    "camera_x": None, "camera_y": None, "score": None,
                }
            self.wfile.write(json.dumps(payload).encode())

        def _serve_camera_record(self, params):
            duration = max(1, min(int(params.get("duration", [60])[0]), 120))
            try:
                path = cam_streamer.start_benchmark_recording(duration)
            except (RuntimeError, ValueError) as exc:
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"path": path, "duration": duration}).encode())

        def _serve_record(self, params):
            """录制音频并返回 WAV 文件下载。"""
            duration = max(1, min(int(params.get("duration", [5])[0]), 30))
            if not mic_monitor:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"No mic")
                return

            print(f"[REC] 浏览器请求录音 {duration}s")
            mic_monitor.start_recording()
            # 阻塞当前线程等待录制完成 (ThreadingHTTPServer 每请求独立线程)
            for remaining in range(duration, 0, -1):
                time.sleep(1)
                print(f"[REC] 还有 {remaining}s...")
            data = mic_monitor.stop_recording()

            if data is None or len(data) == 0:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Record failed")
                return

            filename = f"rec_{int(time.time())}.wav"
            mic_monitor.save_recording(data, filename=filename, gain=64.0, normalize=True)

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with open(filename, "rb") as f:
                self.wfile.write(f.read())

        def _serve_audio_files(self):
            """返回可播放的音频文件列表。"""
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if speaker_module:
                files = speaker_module.list_audio_files()
            else:
                files = []
            self.wfile.write(json.dumps(files).encode())

        def _serve_play(self, params):
            """播放指定音频文件。"""
            if not speaker_module:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"No speaker")
                return
            name = params.get("file", [None])[0]
            if not name:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing file param")
                return
            import os
            filepath = os.path.join(speaker_module.AUDIO_DIR, name)
            if not os.path.isfile(filepath):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"File not found")
                return
            speaker_module.play_wav(filepath)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Playing {name}".encode())

        def log_message(self, fmt, *args):
            pass  # 静默日志

    server = ThreadingHTTPServer((host, port), StreamHandler)
    return server
