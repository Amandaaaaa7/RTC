"""
GC9D01 动画眼睛 — 后台线程驱动 (单目/双目)
RL2 companion-robot 眼睛风格迁移版。

双目优化: 两个眼睛共享同一条 SPI0 总线 (GPIO10/11)，
各自独立 CS 脚，完全避开 I2S (GPIO18-20) 冲突。

左眼 (默认): CS=GPIO5, DC=GPIO25, RST=GPIO24, BL=GPIO23
右眼 (--dual-eye): CS=GPIO6, DC=GPIO27, RST=GPIO22, BL=GPIO26

接线 (双目共享 SPI0):
  SPI0_MOSI (GPIO10) ─┬── 左眼 DIN
                       └── 右眼 DIN
  SPI0_SCLK (GPIO11) ─┬── 左眼 CLK
                       └── 右眼 CLK
  左眼 CS  = GPIO5     右眼 CS  = GPIO6
  左眼 DC  = GPIO25    右眼 DC  = GPIO27
  左眼 RST = GPIO24    右眼 RST = GPIO22
  左眼 BL  = GPIO23    右眼 BL  = GPIO26

本模块职责:
  - GC9D01 SPI 驱动
  - EyeDisplay 后台动画线程
  - 人脸/运动/语音状态对眼睛参数的影响
  - 眨眼状态机、呼吸、视线平滑
  - 左眼调试叠加层集成

**纯渲染层 (gradient / mask / glint / draw_eye / _init_cache) 已剥离到 eye_render.py**，
本文件只通过 `from eye_render import WIDTH, HEIGHT, draw_eye, _init_cache` 调用。
这样可以让眼睛样式调整工作脱离硬件依赖，在新仓库 doll-eye-styles 中独立迭代。
"""

import threading
import time
import math
import random

try:
    import spidev
    import RPi.GPIO as GPIO
except ImportError:  # 允许在非树莓派环境导入/测试图层生成
    spidev = None
    GPIO = None

import numpy as np
from PIL import Image, ImageDraw

from eye_debug_overlay import EyeDebugOverlay
from eye_styles import get_style, DEFAULT_STYLE
from eye_render import WIDTH, HEIGHT, draw_eye, _init_cache
from eye_expressions import (
    ArrivalExcitement, JsonAnchorExpression, get_expression, list_expressions,
)
from expression_debug import ExpressionDebugTrace
from proximity_behavior import ProximityBehavior

from collections import deque

# ============================================================
# 引脚定义 (双目，均使用 SPI0)
# ============================================================
LEFT = {
    "cs": 5, "dc": 25, "rst": 24, "bl": 23,
}
RIGHT = {
    "cs": 6, "dc": 27, "rst": 22, "bl": 26,
}

# 追踪采样与平滑参数
SAMPLE_INTERVAL = 0.04      # 每个逻辑帧读取 tracker 最新值；推理频率由 tracker 自己控制
SNAPSHOT_STALE_MS = 1500.0
METRICS_WINDOW = 256
GAZE_POLICIES = ("continuous", "fixed", "blink_latched")
BLINK_EXPEDITE_MIN_S = 0.3
BLINK_EXPEDITE_MAX_S = 0.7
ARRIVAL_REARM_AFTER_NO_FACE_S = 1.5
ARRIVAL_GAZE_SMOOTHING = 0.70
PROXIMITY_DISENGAGE_DURATION_S = 1.25
RETREAT_GAZE_SMOOTHING = 0.22
INTEREST_FINAL_HOLD_S = 3.0
# 无人在场 → 入睡/唤醒状态机的默认参数。
SLEEP_ON_ABSENCE_TIMEOUT_S = 120.0   # FaceTracker 判定无脸多久后开始入睡
SLEEP_WAKE_CONFIRM_S = 0.15          # 唤醒前需连续有脸的最短时间（去抖）
# 参考 ice/YOLO.html: currentX += (targetX - currentX) * smoothFactor
# 25fps 下 0.35 约等于 60fps 下 0.1 的轨迹，且能在 ~100ms 内接近目标
GAZE_SMOOTH_FACTOR = 0.35
TARGET_FRAME_TIME = 0.04    # 25 fps 目标帧间隔（s）

# 眼睛视觉参数 (基于 RL2 companion-robot eyes 项目适配 160x160)
IRIS_SCALE = 0.80
PUPIL_SCALE = 0.68
MAX_GAZE_OFFSET = 31           # 原项目 70px 按 160/360 缩放

# 默认样式（可由 EyeDisplay 构造时覆盖）
_EYE_STYLE = get_style(DEFAULT_STYLE)
IRIS_COLOR_CENTER = _EYE_STYLE["iris_center"]
IRIS_COLOR_EDGE = _EYE_STYLE["iris_edge"]
SCLERA_COLOR_CENTER = _EYE_STYLE["sclera_center"]
SCLERA_COLOR_EDGE = _EYE_STYLE["sclera_edge"]

_PUPIL_SHAPE = _EYE_STYLE["pupil_shape"]
_PUPIL_SCALE = _EYE_STYLE["pupil_scale"]
_PUPIL_ASPECT = _EYE_STYLE["pupil_aspect"]
_PUPIL_FILL = _EYE_STYLE["pupil_fill"]
_ENABLE_SCLERA_SHADE = _EYE_STYLE["enable_sclera_shade"]
_FIBER_COLOR_A = _EYE_STYLE["fiber_color_a"]
_FIBER_COLOR_B = _EYE_STYLE["fiber_color_b"]

# RL2 眼睛质感参数（从 360px 缩放到 160px）
# 辐辏先关闭，等瞳孔居中和边缘柔和后再开
VERGENCE_MAX = 0                # 18 * 160/360，当前设为 0 以验证同心
GLINT_JITTER_AMP = 0.8          # 高光高频闪烁振幅（像素），160 屏需比理论值大才可见
GAZE_JITTER_AMP = 0.005         # 眼神微颤幅度，在此调小
GAZE_NOISE_AMP = 0.0            # 随机噪声暂停，避免不平滑

PUPIL_BASE_SCALE = 1.0
PUPIL_DETECTED_SCALE = 1.04
PUPIL_SMOOTH_FACTOR = 0.1
BREATH_FREQ = 0.0007            # ms^-1，周期约 9.0s，更慢更柔和
BREATH_AMP = 0.002              # 更小幅度，呼吸更柔和
BLINK_PUPIL_DILATE_MAX = 0.11

MIC_REACT_THRESHOLD = 500_000_000  # 麦克风峰值阈值（S32_LE 约 23% 满量程）

# 语音状态对眼睛的影响参数
VOICE_STATE_PUPIL_SCALE = {
    "idle": 1.0,
    "vad_triggered": 1.02,
    "recording": 1.06,
    "listening": 1.06,
    "asr_pending": 0.98,
    "llm_pending": 0.96,
    "tts_pending": 1.04,
    "speaking": 1.10,
    "error": 0.94,
}

VOICE_STATE_GAZE = {
    "idle": (0.0, 0.0),
    "vad_triggered": (0.0, 0.0),
    "recording": (0.0, 0.05),       # 微微向上，表现“在听”
    "listening": (0.0, 0.05),       # reactive VAD 的即时“我听到了”回执
    "asr_pending": (0.0, 0.0),
    "llm_pending": (0.0, -0.05),    # 微微向下，表现“思考”
    "tts_pending": (0.0, 0.0),
    "speaking": (0.0, 0.08),        # 说话时略向上看
    "error": (0.0, 0.0),
}

BLINK_INTERVAL_MIN = 2.0
BLINK_INTERVAL_MAX = 3.0
DOUBLE_BLINK_CHANCE = 0.08
DOUBLE_BLINK_GAP_MIN = 0.08
DOUBLE_BLINK_GAP_MAX = 0.20
BLINK_CLOSED_MS = 120
BLINK_HOLD_MS = 90                # 全闭保持：必须 > 帧间隔（40ms@25fps），
                                  # 否则帧延迟时全闭画面被整个跳过（32→90）
BLINK_OPEN_MS = 115
BLINK_PUPIL_SHRINK_MS = 380
BLINK_EYELID_MAX = 120          # 上眼睑最大遮挡高度（75% of 160）


# ============================================================
# GC9D01 驱动 — 接受外部 SPI 对象 (共享总线)
# ============================================================
class GC9D01:
    def __init__(self, spi, cs, dc, rst, bl=None):
        if spidev is None or GPIO is None:
            raise RuntimeError(
                "spidev / RPi.GPIO 不可用，无法初始化 GC9D01 硬件显示。"
            )
        for p in [dc, rst, cs]:
            GPIO.setup(p, GPIO.OUT)
        GPIO.output(cs, GPIO.HIGH)
        if bl is not None:
            GPIO.setup(bl, GPIO.OUT)
            GPIO.output(bl, GPIO.HIGH)
        self.cs, self.dc, self.rst = cs, dc, rst
        self.spi = spi  # 共享 SPI 总线
        self.init_display()

    def _cmd(self, cmd, data=None):
        GPIO.output(self.cs, 0)
        GPIO.output(self.dc, 0)
        self.spi.xfer3([cmd])
        if data:
            GPIO.output(self.dc, 1)
            self.spi.xfer3(list(data))
        GPIO.output(self.cs, 1)

    def _data(self, data):
        GPIO.output(self.cs, 0)
        GPIO.output(self.dc, 1)
        # 分片发送，避免超过 spidev 单次缓冲区限制
        for i in range(0, len(data), 4096):
            self.spi.xfer3(list(data[i:i + 4096]))
        GPIO.output(self.cs, 1)

    def _reset(self, rst):
        GPIO.output(rst, 1)
        time.sleep(0.01)
        GPIO.output(rst, 0)
        time.sleep(0.1)
        GPIO.output(rst, 1)
        time.sleep(0.1)

    def init_display(self):
        self._reset(self.rst)  # 硬件复位后再发初始化命令
        self._cmd(0xFE)
        self._cmd(0xEF)
        self._cmd(0x80, b"\xff" * 16)
        self._cmd(0x3A, b"\x05")
        self._cmd(0xEC, b"\x01")
        self._cmd(0x74, b"\x02\x0E" + b"\x00" * 5)
        self._cmd(0x98, b"\x3E")
        self._cmd(0x99, b"\x3E")
        self._cmd(0xB5, b"\x0d\x0d")
        self._cmd(0x60, b"\x38\x0F\x79\x67")
        self._cmd(0x61, b"\x38\x11\x79\x67")
        self._cmd(0x64, b"\x38\x17\x71\x5F\x79\x67")
        self._cmd(0x65, b"\x38\x13\x71\x5B\x79\x67")
        self._cmd(0x6A, b"\x00\x00")
        self._cmd(0x6C, b"\x22\x02\x22\x02\x22\x22\x50")
        self._cmd(0x6E, bytes([
            0x03, 0x03, 0x01, 0x01, 0x00, 0x00, 0x0f, 0x0f,
            0x0d, 0x0d, 0x0b, 0x0b, 0x09, 0x09, 0x00, 0x00,
            0x00, 0x00, 0x0a, 0x0a, 0x0c, 0x0c, 0x0e, 0x0e,
            0x10, 0x10, 0x00, 0x00, 0x02, 0x02, 0x04, 0x04,
        ]))
        self._cmd(0xBF, b"\x01")
        self._cmd(0xF9, b"\x40")
        self._cmd(0x9B, b"\x3B")
        self._cmd(0x93, b"\x33\x7F\x00")
        self._cmd(0x7E, b"\x30")
        self._cmd(0x70, b"\x0D\x02\x08\x0D\x02\x08")
        self._cmd(0x71, b"\x0D\x02\x08")
        self._cmd(0x91, b"\x0E\x09")
        self._cmd(0xC3, b"\x1F")
        self._cmd(0xC4, b"\x1F")
        self._cmd(0xC9, b"\x1F")
        self._cmd(0xF0, b"\x53\x15\x0A\x04\x00\x3E")
        self._cmd(0xF2, b"\x53\x15\x0A\x04\x00\x3A")
        self._cmd(0xF1, b"\x56\xA8\x7F\x33\x34\x5F")
        self._cmd(0xF3, b"\x52\xA4\x7F\x33\x34\xDF")
        self._cmd(0x36, b"\xC8")
        self._cmd(0xB0, b"\x00")
        self._cmd(0xB1, b"\x00\x00")
        self._cmd(0xB4, b"\x00")
        self._cmd(0x11)
        time.sleep(0.2)
        self._cmd(0x29)
        self._cmd(0x2C)

    def display(self, pil_img):
        """PIL Image → BGR565 → SPI 发送 (numpy 向量化转换)"""
        img = pil_img.convert("RGB")
        arr = np.array(img, dtype=np.uint8)          # (h, w, 3)
        r = arr[:, :, 0].astype(np.uint16)
        g = arr[:, :, 1].astype(np.uint16)
        b = arr[:, :, 2].astype(np.uint16)
        # 屏幕按 BGR565 解析：高 5 位 = 蓝，低 5 位 = 红
        c = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
        hi = (c >> 8).astype(np.uint8)
        lo = (c & 0xFF).astype(np.uint8)
        buf = np.stack((hi, lo), axis=-1).tobytes()

        self._cmd(0x2A, bytes([0, 0, 0, WIDTH - 1]))
        self._cmd(0x2B, bytes([0, 0, 0, HEIGHT - 1]))
        self._cmd(0x2C)
        self._data(buf)


# ============================================================
# SPI 总线初始化 (共享)
# ============================================================
def _init_spi():
    """创建并配置 SPI0。"""
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 30_000_000
    spi.mode = 0
    return spi


# ============================================================
# 后台眼睛显示类
# ============================================================
class EyeDisplay:
    """
    后台动画眼睛 (单目/双目)。

    在独立线程中运行眼睛动画循环。双目使用共享 SPI0 总线，
    两个屏幕各自独立 CS/DC/RST/BL 引脚，无 GPIO 冲突。

    可接入 FaceTracker 或 MotionTracker：
      - 优先跟随人脸（FaceTracker）
      - 无人脸时跟随运动目标（MotionTracker）
      - 无任何目标时恢复缓慢自主扫视
    """
    # 阅读顺序（不影响运行）：
    # 1. __init__ 建立“显示状态、眨眼状态、策略状态、性能指标”四组状态；
    # 2. _animation_loop 是唯一的屏幕线程，按帧推进眨眼和瞳孔动画；
    # 3. 每隔 sample_interval，它只读取 FaceTracker 的一份 latest snapshot；
    # 4. _select_policy_target 决定 snapshot 是否立刻成为“已批准注视目标”；
    # 5. blink_latched 模式把新坐标暂存为 pending，闭眼时才提交；
    # 6. target_x/y 是已批准的目的地，gaze_x/y 是平滑移动中的实际瞳孔位置。

    def __init__(self, mic_monitor=None, dual=False, motion_tracker=None, face_tracker=None,
                 voice_module=None, debug_overlay=None, style=None,
                 gaze_smoothing=GAZE_SMOOTH_FACTOR, sample_interval=SAMPLE_INTERVAL,
                 gaze_policy="continuous", retarget_deadband_x=0.06,
                 retarget_deadband_y=0.06, large_retarget_distance=0.35,
                 expression_debug=False, sleep_on_absence=False,
                 sleep_absence_timeout=SLEEP_ON_ABSENCE_TIMEOUT_S,
                 sleep_wake_confirm=SLEEP_WAKE_CONFIRM_S):
        if sample_interval <= 0:
            raise ValueError("sample_interval must be greater than zero")
        if gaze_policy not in GAZE_POLICIES:
            raise ValueError(f"unknown gaze_policy {gaze_policy!r}; choose from {GAZE_POLICIES}")
        if sleep_absence_timeout <= 0:
            raise ValueError("sleep_absence_timeout must be greater than zero")
        if sleep_wake_confirm < 0:
            raise ValueError("sleep_wake_confirm must be >= 0")
        self.mic = mic_monitor
        self.dual = dual
        self.motion = motion_tracker
        self.face = face_tracker
        self.voice = voice_module
        self.debug_overlay = debug_overlay
        self.style = get_style(style)
        self.gaze_smoothing = gaze_smoothing
        self.sample_interval = sample_interval
        self.gaze_policy = gaze_policy
        self.retarget_deadband_x = max(0.0, retarget_deadband_x)
        self.retarget_deadband_y = max(0.0, retarget_deadband_y)
        self.large_retarget_distance = max(0.0, large_retarget_distance)
        self.sleep_on_absence = sleep_on_absence
        self.sleep_absence_timeout = sleep_absence_timeout
        self.sleep_wake_confirm = sleep_wake_confirm
        self._bg_img = self._make_bg_image()
        self._thread: threading.Thread | None = None
        self._displays: list[tuple[GC9D01, bool]] = []
        self.running = False

        # 睡/醒状态机（sleep_on_absence 启用时生效）
        self._sleep_state = "awake"          # awake | falling | asleep
        self._sleep_expression = None        # SleepExpression 实例（falling 期间）
        self._sleep_start = None             # falling 起始时刻
        self._sleep_face_since = None        # 连续有脸起点（唤醒去抖）

        # 当前注视位置与目标位置（用于平滑插值）。
        # target_x/y：策略层已经批准、动画应抵达的“目的地”。
        # gaze_x/y：本帧实际渲染的位置；每帧向 target_x/y 靠近，避免瞳孔跳变。
        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self.target_x = 0.0
        self.target_y = 0.0
        self.target_source = "idle"

        # 瞳孔 / 呼吸 / 眨眼状态。blink_freeze 为真时，瞳孔视线暂停插值，
        # 因此可以在眼睑闭合的遮挡期安全地切换 blink_latched 的注视目标。
        self.pupil_scale = PUPIL_BASE_SCALE
        self.pupil_target = PUPIL_BASE_SCALE
        self.breath = 0.0
        self.blink_pupil_dilate = 0.0
        self.blink_active = False
        self.blink_start = 0.0
        self.blink_freeze = False
        self.blink_closed_ramp = False
        self.blink_post_open = False
        self.blink_post_open_max = BLINK_PUPIL_DILATE_MAX
        self._next_blink_time = 0.0
        self._pending_double = False
        self._blink_is_double = False
        self._blink_policy_committed = False

        # 追踪检测结果（需要在两次采样之间保持最新状态）
        self.face_detected = False
        self.motion_detected = False
        self._mic_react_cooldown = 0.0

        # IDLE 自主扫视状态：每隔几秒随机看向左/右，像生物一样
        self._idle_saccade_time = 0.0
        self._idle_saccade_target_x = 0.0
        self._idle_saccade_target_y = 0.0

        # 人脸丢失后保持原坐标一段时间，避免检测器一次短暂漏检就进入 idle。
        # _last_face_target_* 保存的是“最后检测到”的坐标。continuous 可直接保持它；
        # blink_latched 短暂漏检时则保持已提交 policy target，防止 pending 被提前显示。
        self._last_face_target_x = 0.0
        self._last_face_target_y = 0.0
        self._last_face_time = 0.0
        self._face_hold_duration = 0.3

        # Policy state（策略层）最多存两个目标，绝不存轨迹队列：
        # - _policy_target_*：已经被策略“正式批准”的当前注视方向；
        # - _pending_target：最新但尚未被批准的候选方向。后来的候选会直接覆盖它。
        # 首次有效人脸会立即初始化 _policy_target_*；continuous 每次都批准新值；
        # fixed 之后永不更改；blink_latched 只在眼睑完全闭合时提交 pending。
        self._policy_has_initial_target = False
        self._policy_target_x = 0.0
        self._policy_target_y = 0.0
        self._pending_target = None
        self._pending_snapshot_at_ns = 0
        self._pending_policy_commit_ns = None

        # Day 2 latest-value 与 timing 指标。deque 有固定最大长度，保存的是统计样本
        # 而不是坐标历史，因此不会造成旧坐标积压或无限制的内存增长。
        self.snapshot_age_ms = None
        self.snapshot_stale_drops = 0
        self._snapshot_age_samples = deque(maxlen=METRICS_WINDOW)
        self._eye_frame_timestamps_ns = deque(maxlen=METRICS_WINDOW)
        self._eye_frame_intervals_ms = deque(maxlen=METRICS_WINDOW)
        self._face_to_eye_first_frame_ms = deque(maxlen=METRICS_WINDOW)
        self._snapshot_to_policy_commit_ms = deque(maxlen=METRICS_WINDOW)
        self._policy_commit_to_first_eye_frame_ms = deque(maxlen=METRICS_WINDOW)
        self._face_was_detected = False
        self._pending_face_entry_ns = None

        # A one-shot viewer-arrival cue. It must be re-armed by a sustained
        # no-face interval so a single detector miss cannot repeat the cue.
        self._arrival_excitement = ArrivalExcitement()
        self._arrival_excitement_start = None
        self._arrival_excitement_armed = True
        self._face_absent_since = None

        # 人脸框面积用作距离代理；“检测到人脸”与“仍积极关注远处人脸”分开处理。
        self._proximity = ProximityBehavior()
        self.proximity_state = "idle"
        self._interest_expression = JsonAnchorExpression("Interest", 1.0)
        self._interest_start = None
        self._disengage_start = None
        self._disengage_from = (0.0, 0.0)

        # 语音状态
        self._voice_lock = threading.Lock()
        self._voice_state = "idle"
        self._voice_emotion = None
        self._pending_listening_cue = None
        self._voice_timing_callback = None

        # 表情动画（None = 正常 idle 行为；由样式浏览器/情绪系统挂载）
        self._expression_lock = threading.Lock()
        self._expression = None
        self._expr_start = 0.0
        # 默认关闭：既有 set_expression() 的循环行为不会被 debug 自动结束逻辑影响。
        self.expression_debug_enabled = expression_debug
        self._expression_debug = ExpressionDebugTrace() if expression_debug else None
        self._expression_debug_command_id = None

        # 外部 GIF/图像源预览（None = 由表达式/追踪路径绘制）
        # 回调签名 () -> PIL.Image RGB 160x160；gallery 用它把 review GIF 帧
        # 直接喂进左右眼，绕过参数化渲染，方便预览 emotion-engine 评审产物。
        self._review_frame_provider = None

    def _update_sleep_state(self, now_s):
        """睡/醒状态机（最外层优先级）：

        - 无脸时长（FaceTracker 判定的 _lost_since_ns 起点）≥ timeout → 进入 falling，
          播放入睡动画（复用 sleep_gifs_cry 的 20s 轨迹）。
        - falling 期间若人出现，立刻 wake → 睁眼（awake() 复位 + 清除表达式）。
        - asleep 后需连续有脸 sleep_wake_confirm 秒才唤醒（去抖），
          若仍是假回脸，回到 asleep（眼睑保持全闭，无跳变）。

        返回 True 表示当前正处于睡/醒接管状态（falling/asleep），调用方应跳过
        正常眨眼/追踪/表情路径。
        """
        if not self.sleep_on_absence or self.face is None:
            return False

        face_running = self.face.running and not self.face.error_message
        absent = self.face.face_absent_duration() if face_running else None
        present = self.face.face_present_duration() if face_running else None

        if self._sleep_state == "awake":
            if absent is not None and absent >= self.sleep_absence_timeout:
                self._sleep_state = "falling"
                self._sleep_expression = get_expression("sleep")
                self._sleep_expression._start = now_s
                self._sleep_start = now_s
                self._sleep_face_since = None
                print(f"[EYE] no face for {absent:.1f}s -> falling asleep")
            else:
                return False

        if self._sleep_state == "falling":
            if present is not None:
                # 人出现 → 立刻睁眼（不再等入睡动画播完）
                self._sleep_expression.awake()
                self._sleep_expression = None
                self._sleep_state = "awake"
                self._sleep_start = None
                print("[EYE] face detected -> wake immediately")
                return False
            return True

        # asleep
        if present is None:
            return True  # 仍无脸，保持睡着
        if self._sleep_face_since is None:
            self._sleep_face_since = now_s
        if now_s - self._sleep_face_since >= self.sleep_wake_confirm:
            self._sleep_state = "awake"
            self._sleep_face_since = None
            print("[EYE] face sustained -> wake")
            return False
        return True  # 去抖中，仍保持睡着（眼睑全闭，无跳变）

    def set_debug_overlay(self, overlay):
        """设置/更新调试信息叠加层（显示在左眼）。"""
        self.debug_overlay = overlay

    def set_review_frame_provider(self, provider):
        """挂载一个返回 (left_rgb, right_rgb) 或单张图的回调。

        设置后渲染循环不再走 draw_eye，而是每帧调用 provider() 取图像发给屏幕。
        传 None 恢复原表达式/追踪路径。
        """
        self._review_frame_provider = provider

    def set_style(self, style_name: str):
        """运行时热切换眼睛样式（下一帧生效，图层缓存自动重建）。

        未知样式名抛 KeyError（含可用列表）。单属性赋值，动画线程安全。
        """
        self.style = get_style(style_name)
        self._bg_img = self._make_bg_image()
        print(f"[EYE] 切换样式 -> {self.style['name']}")

    def _make_bg_image(self):
        """按样式巩膜边缘色生成合成底图（眼睑裁剪/圆外透明区的填充色）。

        深色样式→近黑（闭眼融入屏幕底色）；浅色样式→浅巩膜色（类肤色的眼皮效果）。
        """
        bg = self.style.get("sclera_edge", (0, 0, 0))
        return Image.new("RGBA", (WIDTH, HEIGHT), (bg[0], bg[1], bg[2], 255))

    def set_expression(self, name):
        """挂载表情动画（happy/angry/cry/dislike/miosis；None 恢复正常 idle）。

        表情参数每帧覆盖眨眼状态机与追踪行为，未知表情名抛 KeyError。
        """
        expression = get_expression(name) if name else None
        with self._expression_lock:
            self._expression = expression
            self._expr_start = time.time()
            # 外部旧接口不创建新的 debug 命令；若它覆盖正在测试的命令，只留下覆盖证据。
            self._expression_debug_command_id = None
        print(f"[EYE] 表情 -> {expression.name if expression else 'idle(正常)'}")

    def command_expression_debug(self, name, duration_ms=None):
        """Opt-in command endpoint: latest command wins and finite commands return to idle."""
        if not self.expression_debug_enabled or self._expression_debug is None:
            raise RuntimeError("expression debug is disabled")
        normalized = (name or "idle").lower()
        valid = {expression for expression, _cn_name in list_expressions()} | {"idle"}
        if normalized not in valid:
            raise ValueError(f"unknown expression {normalized!r}; choose from {sorted(valid)}")
        expression = None if normalized == "idle" else get_expression(normalized)
        if duration_ms is None:
            duration_s = expression.cycle if expression else None
        else:
            duration_s = float(duration_ms) / 1000.0
            if not 0.1 <= duration_s <= 60.0:
                raise ValueError("duration_ms must be between 100 and 60000")
        command = self._expression_debug.receive_command(normalized, duration_s)
        with self._expression_lock:
            self._expression = expression
            self._expr_start = time.time()
            self._expression_debug_command_id = command["id"]
        self._expression_debug.affect_changed(command["id"])
        print(f"[EYE] debug expression #{command['id']} -> {normalized}")
        return self.get_expression_debug_snapshot()

    def get_expression_debug_snapshot(self):
        """Return the bounded event timeline; unavailable unless --expression-debug was enabled."""
        if not self.expression_debug_enabled or self._expression_debug is None:
            return None
        return self._expression_debug.snapshot()

    def _expression_for_frame(self, now_s):
        """Read one expression snapshot; debug finite commands finish at their requested boundary."""
        if self._expression_debug is not None:
            finished = self._expression_debug.finish_if_due()
            if finished is not None:
                with self._expression_lock:
                    if self._expression_debug_command_id == finished["id"]:
                        self._expression = None
                        self._expression_debug_command_id = None
                        self._expr_start = now_s
        with self._expression_lock:
            return self._expression, self._expr_start, self._expression_debug_command_id

    def on_voice_state(self, state):
        """Called by VoiceModule when voice interaction state changes."""
        with self._voice_lock:
            self._voice_state = getattr(state, "state", "idle")
            self._voice_emotion = getattr(state, "emotion", None)

    def clear_fast_reaction_event(self):
        """Fast reaction expression ended: reset voice state so idle path doesn't
        keep the listening pupil multiplier alive after the surprise animation.
        """
        with self._voice_lock:
            self._voice_state = "idle"
            self._pending_listening_cue = None
        print("[EYE] fast reaction ended -> voice_state idle")

    def set_voice_timing_callback(self, callback):
        """Attach ReactiveVoiceModule's metric sink without coupling EyeDisplay to it."""
        self._voice_timing_callback = callback

    def on_voice_timing_event(self, event, interaction_id, timestamp_ns):
        """Receive a reactive voice timing event; VAD immediately requests a listening cue."""
        with self._voice_lock:
            if event == "vad_onset":
                self._voice_state = "listening"
                self._pending_listening_cue = (interaction_id, timestamp_ns)
            elif event == "response_first_audio_sample":
                self._voice_state = "speaking"
            elif event == "response_finished":
                self._voice_state = "idle"

    def on_fast_reaction_event(self, event_type, keyword=None):
        """Receive a fast-reflex trigger event from FastReactionModule.

        Temporarily sets a alert/listening gaze cue and, if configured by the
        caller, mounts an expression.  The caller is responsible for clearing any
        expression via ``set_expression(None)``.
        """
        with self._voice_lock:
            self._voice_state = "listening"
        print(f"[EYE] fast reaction -> {event_type} ({keyword or 'unknown'})")

    def _record_listening_cue_frame(self, completed_ns):
        """Complete one VAD→display measurement at the first successfully submitted eye frame."""
        with self._voice_lock:
            pending = self._pending_listening_cue
            self._pending_listening_cue = None
        if pending is None:
            return
        interaction_id, _vad_onset_ns = pending
        callback = self._voice_timing_callback
        if callback is not None:
            try:
                callback(interaction_id, completed_ns)
            except Exception as exc:
                print(f"[EYE] voice timing callback error: {exc}")

    def _schedule_blink(self, now):
        """安排下一次自然眨眼（普通间隔）。

        这里仅计算未来的触发时间；不会立即改变眼睑或注视目标。blink_latched
        模式会利用这次自然闭眼作为“安全提交 pending 坐标”的边界。
        """
        delay = random.uniform(BLINK_INTERVAL_MIN, BLINK_INTERVAL_MAX)
        self._next_blink_time = now + delay
        self._pending_double = False

    def _start_blink(self, now):
        """开始一次眨眼周期，并重置本次闭眼的策略提交标记。

        _blink_policy_committed 确保同一次闭眼停留阶段只提交一次 pending target，
        即使动画循环在该阶段运行多帧也不会重复记录或重复切换目标。
        """
        self.blink_active = True
        self.blink_start = now
        self.blink_freeze = True
        self.blink_closed_ramp = True
        self.blink_post_open = False
        self.blink_pupil_dilate = 0.0
        self.blink_post_open_max = BLINK_PUPIL_DILATE_MAX
        self._blink_policy_committed = False
        if self._pending_double:
            self._blink_is_double = True
            self._pending_double = False

    def _reset_display(self, rst_pin):
        """硬件复位单个屏幕 (先设置引脚)。"""
        GPIO.setup(rst_pin, GPIO.OUT)
        GPIO.output(rst_pin, 1)
        time.sleep(0.01)
        GPIO.output(rst_pin, 0)
        time.sleep(0.1)
        GPIO.output(rst_pin, 1)
        time.sleep(0.1)

    @staticmethod
    def _percentiles(values):
        if not values:
            return {"p50": None, "p95": None}
        ordered = sorted(values)
        return {
            "p50": round(ordered[round((len(ordered) - 1) * 0.50)], 3),
            "p95": round(ordered[round((len(ordered) - 1) * 0.95)], 3),
        }

    def _consume_face_snapshot(self, snapshot, now_s, now_ns):
        """消费一份最新人脸快照；只接受新鲜结果，绝不建立坐标队列。

        返回值只是“本次采样看到的最新人脸坐标”，尚未代表眼睛一定会立刻转向；
        下一步仍要交给 _select_policy_target 依据 gaze_policy 决策。
        """
        if not snapshot.detected:
            # 本轮检测没有人脸：不清除最后的有效目标，由后续 hold/fallback 决定
            # 是否暂时保持原注视。此处返回 None，避免把“无检测”伪装成新坐标。
            self._face_was_detected = False
            self._pending_face_entry_ns = None
            if self._face_absent_since is None:
                self._face_absent_since = now_s
            elif now_s - self._face_absent_since >= ARRIVAL_REARM_AFTER_NO_FACE_S:
                self._arrival_excitement_armed = True
                self._proximity.reset()
                self.proximity_state = "idle"
                self._interest_start = None
                self._disengage_start = None
            return None
        # snapshot age 是“FaceTracker 发布 latest snapshot”到“眼睛线程读取”之间的
        # 等待时间；没有发布时刻的旧 snapshot 才回退到检测完成时刻。
        snapshot_at_ns = snapshot.snapshot_at_ns or snapshot.inferred_at_ns
        age_ms = (now_ns - snapshot_at_ns) / 1_000_000
        self.snapshot_age_ms = round(age_ms, 3)
        self._snapshot_age_samples.append(age_ms)
        if age_ms > SNAPSHOT_STALE_MS:
            # 过期结果宁可丢弃，也不能让眼睛追随数秒前的人；最后有效目标会保留。
            self.snapshot_stale_drops += 1
            return None
        if not self._face_was_detected:
            # 记录一次“无脸 -> 有脸”起点，_record_eye_frame 会据此统计 Day 2 的
            # face_to_eye_first_frame_ms；同一段连续人脸只记录一次首帧。
            self._pending_face_entry_ns = snapshot.face_entered_at_ns or snapshot.inferred_at_ns
            if self._arrival_excitement_armed:
                self._arrival_excitement_start = now_s
                self._arrival_excitement_armed = False
                print("[EYE] viewer arrival -> excitement cue")
        self._face_was_detected = True
        self._face_absent_since = None
        if self.gaze_policy != "fixed":
            update = self._proximity.observe(snapshot.face_area_ratio, now_s)
            self.proximity_state = update.state
            if update.approach_started and self._interest_start is None:
                self._interest_start = now_s
                print("[EYE] viewer approach -> interest cue")
            if update.started_disengaging:
                self._disengage_start = now_s
                self._disengage_from = (snapshot.x, snapshot.y)
                print("[EYE] viewer distant -> disengaging")
            if update.state in ("retreating", "disengaging") and self._interest_start is not None:
                self._interest_start = None
                print("[EYE] viewer retreat -> interest hold cancelled")
        # 这些字段用于短暂漏检保持；它们记录检测层的最新值，不等同于 policy target。
        self._last_face_target_x = snapshot.x
        self._last_face_target_y = snapshot.y
        self._last_face_time = now_s
        return snapshot.x, snapshot.y

    def _arrival_excitement_for_frame(self, now_s):
        """Return the active one-shot arrival cue, clearing it at completion."""
        start = self._arrival_excitement_start
        if start is None:
            return None
        elapsed = now_s - start
        if elapsed >= self._arrival_excitement.duration:
            self._arrival_excitement_start = None
            return None
        return self._arrival_excitement.step(elapsed)

    def _interest_for_frame(self, now_s):
        """靠近后播放 Interest，保持最终帧 3 秒，再恢复普通注视。"""
        if self._interest_start is None:
            return None
        elapsed = now_s - self._interest_start
        if elapsed >= self._interest_expression.duration + INTEREST_FINAL_HOLD_S:
            self._interest_start = None
            return None
        return self._interest_expression.step(elapsed)

    def _disengagement_target(self, now_s):
        """将最后的关注点平滑混合到当前待机扫视目标。"""
        if self.proximity_state != "disengaging" or self._disengage_start is None:
            return None
        progress = min(1.0, (now_s - self._disengage_start) / PROXIMITY_DISENGAGE_DURATION_S)
        eased = 1.0 - (1.0 - progress) ** 3
        idle_target = (self._idle_saccade_target_x, self._idle_saccade_target_y)
        target = (
            self._disengage_from[0] * (1.0 - eased) + idle_target[0] * eased,
            self._disengage_from[1] * (1.0 - eased) + idle_target[1] * eased,
        )
        if progress >= 1.0:
            self._proximity.finish_disengaging()
            self.proximity_state = self._proximity.state
            self._disengage_start = None
        return target

    @staticmethod
    def _target_distance(left, right):
        return math.hypot(left[0] - right[0], left[1] - right[1])

    def _is_meaningful_retarget(self, candidate):
        """小幅检测抖动既不替换 pending，也不触发提前眨眼。"""
        reference = self._pending_target or (
            self._policy_target_x, self._policy_target_y
        )
        return (
            abs(candidate[0] - reference[0]) >= self.retarget_deadband_x
            or abs(candidate[1] - reference[1]) >= self.retarget_deadband_y
        )

    def _commit_policy_target(self, target, snapshot_at_ns, now_ns):
        """把目标正式交给显示层，并记录计算/产品策略分界的时间。"""
        if self._policy_has_initial_target and target == (
            self._policy_target_x, self._policy_target_y
        ):
            return
        self._policy_has_initial_target = True
        self._policy_target_x, self._policy_target_y = target
        self.target_x, self.target_y = target
        self.target_source = "face"
        self._pending_policy_commit_ns = now_ns
        if snapshot_at_ns:
            self._snapshot_to_policy_commit_ms.append(
                (now_ns - snapshot_at_ns) / 1_000_000
            )

    def _expedite_blink_for_large_retarget(self, candidate, now_s):
        """大位移只提前自然眨眼，不立即改已批准的注视目标。"""
        committed = (self._policy_target_x, self._policy_target_y)
        if self.blink_active or self._target_distance(candidate, committed) < self.large_retarget_distance:
            return
        expedited_at = now_s + random.uniform(
            BLINK_EXPEDITE_MIN_S, BLINK_EXPEDITE_MAX_S
        )
        # 未初始化或已到期的排程也必须从 300 ms 之后开始，不能被 min(...)
        # 保留为“现在”，否则大位移会错误地立即触发眨眼。
        self._next_blink_time = (
            expedited_at if self._next_blink_time <= now_s
            else min(self._next_blink_time, expedited_at)
        )

    def _select_policy_target(self, latest_target, snapshot_at_ns=0, now_ns=None, now_s=None):
        """接收一个 latest target，并返回策略当前已经批准的目标。

        pending 与 policy target 的关系：
        - 每个有效 latest target 都覆盖 _pending_target，故永远不会积压轨迹；
        - continuous：当前值立即批准并返回；
        - fixed：首次值批准，之后始终返回首次值；
        - blink_latched：首次值立即批准，之后继续返回旧的已批准值，直到闭眼提交。
        """
        if latest_target is None:
            # 没有新鲜人脸时不修改 pending 或已批准目标；保持/fallback 由主循环处理。
            return None
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        now_s = time.time() if now_s is None else now_s
        if self.gaze_policy == "continuous":
            # 性能基准模式：检测到什么就立刻批准什么，不额外引入产品策略等待。
            self._pending_target = latest_target
            self._pending_snapshot_at_ns = snapshot_at_ns
            self._commit_policy_target(latest_target, snapshot_at_ns, now_ns)
            return latest_target
        if not self._policy_has_initial_target:
            # fixed 和 blink_latched 的共同规则：首次有效人脸必须立刻看过去，
            # 不需要等下一次眨眼，避免“用户刚出现但玩偶没有反应”。
            self._pending_target = latest_target
            self._pending_snapshot_at_ns = snapshot_at_ns
            self._commit_policy_target(latest_target, snapshot_at_ns, now_ns)
            return latest_target
        if self.gaze_policy == "blink_latched" and self._is_meaningful_retarget(latest_target):
            # 覆盖而非 append：只保留下一次闭眼要采用的唯一最新位置。
            self._pending_target = latest_target
            self._pending_snapshot_at_ns = snapshot_at_ns
            self._expedite_blink_for_large_retarget(latest_target, now_s)
        return self._policy_target_x, self._policy_target_y

    def _commit_blink_latched_target(self, now_ns=None):
        """仅在完全闭眼边界采用唯一最新的 pending target。

        这个函数不从 FaceTracker 读取历史结果；它只提交当前那一格 pending，
        所以人脸在一次眨眼间移动多次时，最终只会采用最新位置。
        """
        if self.gaze_policy != "blink_latched" or self._pending_target is None:
            # continuous/fixed 不需要闭眼提交；没有 pending 也没有可提交内容。
            return
        # 同时更新策略层和显示层目的地。实际 gaze_x/y 仍由后面的逐帧平滑完成。
        self._commit_policy_target(
            self._pending_target,
            self._pending_snapshot_at_ns,
            time.monotonic_ns() if now_ns is None else now_ns,
        )

    def _fixed_target(self):
        """返回 fixed 模式的首次目标；它在首次锁定后不可被任何 fallback 替换。"""
        if self.gaze_policy == "fixed" and self._policy_has_initial_target:
            return self._policy_target_x, self._policy_target_y
        return None

    def _record_eye_frame(self, completed_ns):
        """记录一次完成 display() 调用后的软件可见眼睛帧。

        completed_ns 位于 SPI 提交之后，因此它用于衡量“逻辑已提交到第一帧”的边界，
        并不等同于 LCD 面板最终发光的物理光学延迟。
        """
        if self._eye_frame_timestamps_ns:
            self._eye_frame_intervals_ms.append(
                (completed_ns - self._eye_frame_timestamps_ns[-1]) / 1_000_000
            )
        self._eye_frame_timestamps_ns.append(completed_ns)
        if self._pending_policy_commit_ns is not None:
            self._policy_commit_to_first_eye_frame_ms.append(
                (completed_ns - self._pending_policy_commit_ns) / 1_000_000
            )
            self._pending_policy_commit_ns = None
        if self._pending_face_entry_ns is not None:
            # 只把本次 no-face -> face 的第一帧计入一次，避免每一帧都重复统计。
            self._face_to_eye_first_frame_ms.append(
                (completed_ns - self._pending_face_entry_ns) / 1_000_000
            )
            self._pending_face_entry_ns = None

    def get_metrics(self):
        """返回 /status 暴露的有界统计指标，供采集器轮询。"""
        intervals = tuple(self._eye_frame_intervals_ms)
        eye_fps = None
        if intervals and sum(intervals) > 0:
            eye_fps = round(1000.0 / (sum(intervals) / len(intervals)), 3)
        first_frame = self._percentiles(tuple(self._face_to_eye_first_frame_ms))
        first_frame["count"] = len(self._face_to_eye_first_frame_ms)
        return {
            "sample_interval_ms": round(self.sample_interval * 1000, 3),
            "snapshot_age_ms": self.snapshot_age_ms,
            "snapshot_age_ms_percentiles": self._percentiles(tuple(self._snapshot_age_samples)),
            "snapshot_stale_drops": self.snapshot_stale_drops,
            "face_to_eye_first_frame_ms": first_frame,
            "snapshot_to_policy_commit_ms": self._percentiles(
                tuple(self._snapshot_to_policy_commit_ms)
            ),
            "policy_commit_to_first_eye_frame_ms": self._percentiles(
                tuple(self._policy_commit_to_first_eye_frame_ms)
            ),
            "eye_effective_fps": eye_fps,
            "eye_frame_interval_ms": self._percentiles(intervals),
        }

    def _animation_loop(self):
        GPIO.setmode(GPIO.BCM)
        spi = _init_spi()

        # 初始化屏幕
        # 注：因摄像头旋转 180°，左右眼都使用 mirror=True，使双眼同向追踪
        left = GC9D01(spi, cs=LEFT["cs"], dc=LEFT["dc"],
                      rst=LEFT["rst"], bl=LEFT["bl"])
        self._displays = [(left, True)]  # (display, mirror)

        if self.dual:
            self._reset_display(RIGHT["rst"])
            right = GC9D01(spi, cs=RIGHT["cs"], dc=RIGHT["dc"],
                           rst=RIGHT["rst"], bl=RIGHT["bl"])
            self._displays.append((right, True))

        # 纯色测试
        left.display(Image.new("RGB", (WIDTH, HEIGHT), (255, 0, 0)))
        if self.dual:
            self._displays[1][0].display(Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 255)))
        time.sleep(0.5)
        left.display(Image.new("RGB", (WIDTH, HEIGHT), (0, 255, 0)))
        if self.dual:
            self._displays[1][0].display(Image.new("RGB", (WIDTH, HEIGHT), (0, 255, 0)))
        time.sleep(0.5)

        print(f"[EYE] 动画开始 ({'双目' if self.dual else '单目'})")

        # 采样状态
        last_sample_time = time.time()

        face_active = False
        motion_active = False
        target_source = "idle"
        self.face_detected = False
        self.motion_detected = False

        # 安排第一次眨眼
        self._schedule_blink(time.time())

        while self.running:
            now_s = time.time()
            now_ms = now_s * 1000.0

            # ---- 睡/醒状态机（最外层优先级）----
            # 仅当 sleep_on_absence 启用且当前处于 falling/asleep 时，下面的
            # sleep_takeover 才为 True：跳过眨眼状态机、追踪采样与表情路径，
            # 改由睡眠表达式驱动眼睑；唤醒后本帧立即恢复下方正常路径。
            sleep_takeover = self._update_sleep_state(now_s)
            sleep_params = None
            if sleep_takeover and self._sleep_expression is not None:
                sleep_params = self._sleep_expression.step(now_s - self._sleep_start)
            elif sleep_takeover:
                # asleep 期间（已过 20s 入睡动画末尾）：睡眠表达式已结束，
                # 但保持全闭姿态与视线收敛，仅保留极慢呼吸，冻结 jitter。
                sleep_params = {
                    "gaze_x": 0.0,
                    "gaze_y": 0.0,
                    "eyelid": 120,
                    "eyelid_target_y": 131.0,
                    "pupil_relative_scale": 1.0,
                    "glint_jitter_x": 0.0,
                    "glint_jitter_y": 0.0,
                    "is_idle": True,
                }

            # ---- 眨眼状态机 ----
            # 一帧内先推进眼睑状态，再在稍后的采样块读取 latest snapshot。闭眼停留段
            # 是 blink_latched 提交 pending 的唯一入口；其他时刻 pending 只会被覆盖。
            eyelid = 0
            if not sleep_takeover and not self.blink_active and now_s >= self._next_blink_time:
                # 到达预定时间才开始一次自然眨眼，避免每帧重复进入眨眼流程。
                self._start_blink(now_s)

            if self.blink_active:
                elapsed_ms = (now_s - self.blink_start) * 1000.0
                post_start = BLINK_CLOSED_MS + BLINK_HOLD_MS

                if elapsed_ms < BLINK_CLOSED_MS:
                    # 闭合斜坡：眼睑越来越低，同时冻结 gaze 插值以遮住内部状态变化。
                    t = elapsed_ms / BLINK_CLOSED_MS
                    eyelid = int(BLINK_EYELID_MAX * (t * t))
                    self.blink_closed_ramp = True
                    self.blink_freeze = True
                elif elapsed_ms < post_start:
                    # 完全闭眼并短暂停留：这是最自然、最不显突兀的目标切换时机。
                    eyelid = BLINK_EYELID_MAX
                    self.blink_closed_ramp = False
                    self.blink_freeze = True
                    if not self._blink_policy_committed:
                        # 同一次闭眼只允许提交一次，避免循环每帧都反复采纳 pending。
                        self._commit_blink_latched_target(time.monotonic_ns())
                        self._blink_policy_committed = True
                elif elapsed_ms < post_start + BLINK_OPEN_MS:
                    # 睁眼斜坡：解除 gaze 冻结，新提交的 target 可在用户可见时平滑生效。
                    t = (elapsed_ms - post_start) / BLINK_OPEN_MS
                    ease = 1.0 - (1.0 - t) ** 3
                    eyelid = int(BLINK_EYELID_MAX * (1.0 - ease))
                    self.blink_freeze = False
                else:
                    eyelid = 0
                    self.blink_freeze = False

                self.blink_post_open = (
                    post_start <= elapsed_ms < post_start + BLINK_PUPIL_SHRINK_MS
                )

                if self.blink_closed_ramp:
                    ramp = min(1.0, elapsed_ms / BLINK_CLOSED_MS)
                    self.blink_pupil_dilate = BLINK_PUPIL_DILATE_MAX * (ramp * ramp)
                elif self.blink_post_open:
                    t = min(1.0, (elapsed_ms - post_start) / BLINK_PUPIL_SHRINK_MS)
                    ease = 1.0 - (1.0 - t) ** 3
                    self.blink_pupil_dilate = self.blink_post_open_max * (1.0 - ease)
                else:
                    self.blink_pupil_dilate = 0.0

                # 眨眼周期结束：按概率安排下一次单眨或双眨，但不影响已提交的 gaze policy。
                if elapsed_ms > post_start + BLINK_PUPIL_SHRINK_MS:
                    self.blink_active = False
                    self.blink_freeze = False
                    self.blink_closed_ramp = False
                    self.blink_post_open = False
                    self.blink_pupil_dilate = 0.0
                    if self._blink_is_double:
                        self._blink_is_double = False
                        self._schedule_blink(now_s)
                    else:
                        if random.random() < DOUBLE_BLINK_CHANCE:
                            self._pending_double = True
                            self._next_blink_time = now_s + random.uniform(
                                DOUBLE_BLINK_GAP_MIN, DOUBLE_BLINK_GAP_MAX
                            )
                        else:
                            self._schedule_blink(now_s)
            else:
                self.blink_freeze = False
                self.blink_closed_ramp = False
                self.blink_post_open = False
                self.blink_pupil_dilate = 0.0

            # ---- 按采样周期读取唯一 latest tracker snapshot。----
            # 这里不等待 FaceTracker 推理，也不读取历史帧；读到的是“此刻最新的一份”。
            if not sleep_takeover and now_s - last_sample_time >= self.sample_interval:
                last_sample_time = now_s

                face_active = (
                    self.face and self.face.running and not self.face.error_message
                )
                motion_active = (
                    self.motion and self.motion.running and not self.motion.error_message
                )
                target_source = "idle"
                new_tx, new_ty = 0.0, 0.0
                self.face_detected = False
                self.motion_detected = False

                if face_active:
                    snapshot_now_ns = time.monotonic_ns()
                    face_snapshot = self.face.get_snapshot()
                    # 先过滤无检测/过期 snapshot，再让策略层决定是否立刻采用该位置。
                    face_target = self._consume_face_snapshot(
                        face_snapshot, now_s, snapshot_now_ns
                    )
                    # 对 blink_latched 来说，这里通常返回旧的 policy target；新值仅进入
                    # pending，直到下一次完全闭眼才会变成新的 target_x/y。
                    policy_target = self._select_policy_target(
                        face_target,
                        snapshot_at_ns=(
                            face_snapshot.snapshot_at_ns or face_snapshot.inferred_at_ns
                        ),
                        now_ns=snapshot_now_ns,
                        now_s=now_s,
                    )
                    if policy_target is not None:
                        new_tx, new_ty = policy_target
                        target_source = "face"
                        self.face_detected = True

                # fixed 是渲染/SPI 隔离模式：一旦首次目标锁定，人脸丢失、运动检测和
                # valid face target is locked, no face-loss hold, motion, or
                # idle fallback may replace that target.
                fixed_target = self._fixed_target()
                if fixed_target is not None:
                    new_tx, new_ty = fixed_target
                    target_source = "face"
                    self.face_detected = True

                if self.gaze_policy != "fixed":
                    disengagement_target = self._disengagement_target(now_s)
                    if disengagement_target is not None:
                        new_tx, new_ty = disengagement_target
                        target_source = "disengaging"
                        self.face_detected = False
                    elif self.proximity_state == "distant":
                        new_tx = self._idle_saccade_target_x
                        new_ty = self._idle_saccade_target_y
                        target_source = "distant"
                        self.face_detected = False

                # 人脸刚丢失时保持原坐标 1 秒，不要立刻进入 idle。blink_latched
                # 必须保持已批准 policy target，不能把尚未闭眼提交的 pending 提前显示。
                if self.gaze_policy != "fixed" and target_source == "idle" and \
                        (now_s - self._last_face_time) < self._face_hold_duration:
                    if self.gaze_policy == "blink_latched" and self._policy_has_initial_target:
                        new_tx = self._policy_target_x
                        new_ty = self._policy_target_y
                    else:
                        new_tx = self._last_face_target_x
                        new_ty = self._last_face_target_y
                    target_source = "face"
                    self.face_detected = True

                if self.gaze_policy != "fixed" and target_source == "idle" and motion_active:
                    # 仅在没有有效人脸及其短暂保持期时，运动检测才可以成为回退目标。
                    mx, my, detected = self.motion.get_target()
                    if detected:
                        new_tx, new_ty = mx, my
                        target_source = "motion"
                        self.motion_detected = True

                if self.gaze_policy != "fixed" and target_source == "idle":
                    # 最后才进入 idle；这样 face 优先级始终高于 motion，再高于自主扫视。
                    # 无追踪目标时保持当前 idle 扫视点，不在采样块里生成连续旋转
                    new_tx = self._idle_saccade_target_x
                    new_ty = self._idle_saccade_target_y

                new_tx = max(-1.0, min(1.0, new_tx))
                new_ty = max(-1.0, min(1.0, new_ty))

                # 将本次策略/回退结果写为动画目的地；实际 gaze_x/y 在下方每帧平滑靠近。
                self.target_x, self.target_y = new_tx, new_ty
                self.target_source = target_source

                if face_active or motion_active:
                    print(
                        f"[EYE] sample target=({self.target_x:+.2f}, {self.target_y:+.2f}) "
                        f"[{target_source}]"
                    )

            # ---- 麦克风实时反应（声音大时睁眼 + 随机扫视）----
            mic_react = (
                self.mic and self.mic.running and self.mic.peak > MIC_REACT_THRESHOLD
            )
            if mic_react and now_s > self._mic_react_cooldown:
                self._mic_react_cooldown = now_s + 0.5
                eyelid = 0
                self.target_x = max(
                    -1.0, min(1.0, self.target_x + (random.random() - 0.5) * 0.3)
                )
                self.target_y = max(
                    -1.0, min(1.0, self.target_y + (random.random() - 0.5) * 0.3)
                )

            # IDLE 模式下做生物式扫视：每隔几秒随机看向左/右，停留并微颤
            if target_source in ("idle", "distant"):
                if now_s >= self._idle_saccade_time:
                    self._idle_saccade_time = now_s + random.uniform(1.5, 3.5)
                    # 在屏幕中心附近随机选一个目标点，不要偏离中心太远
                    angle = random.uniform(0, 2 * math.pi)
                    radius = random.uniform(0.15, 0.50)
                    self._idle_saccade_target_x = math.cos(angle) * radius
                    self._idle_saccade_target_y = math.sin(angle) * radius * 0.7
                # 持有期间加入轻微漂移，避免完全静止，同时保留 is_idle 的失焦模糊
                self._idle_saccade_target_x += random.uniform(-0.004, 0.004)
                self._idle_saccade_target_y += random.uniform(-0.002, 0.002)
                self._idle_saccade_target_x = max(-0.7, min(0.7, self._idle_saccade_target_x))
                self._idle_saccade_target_y = max(-0.3, min(0.3, self._idle_saccade_target_y))
                self.target_x = self._idle_saccade_target_x
                self.target_y = self._idle_saccade_target_y

            # ---- 语音状态影响 ----
            with self._voice_lock:
                vstate = self._voice_state
            voice_pupil_mult = VOICE_STATE_PUPIL_SCALE.get(vstate, 1.0)
            voice_gaze_offset = VOICE_STATE_GAZE.get(vstate, (0.0, 0.0))
            voice_tx = self.target_x + voice_gaze_offset[0]
            voice_ty = self.target_y + voice_gaze_offset[1]
            # 说话时减少眨眼频率
            if vstate in ("speaking", "tts_pending"):
                # Postpone scheduled blinks slightly
                self._next_blink_time = max(self._next_blink_time, now_s + 0.3)

            # ---- 每帧平滑插值（参考 ice/YOLO.html）----
            # 目的地 target 不等于当前 gaze。只有不在闭眼冻结期才移动，形成自然缓动。
            arrival_params = self._arrival_excitement_for_frame(now_s)
            interest_params = self._interest_for_frame(now_s)
            gaze_smoothing = self.gaze_smoothing
            if arrival_params is not None:
                # The first turn should read as a quick acknowledgement while
                # retaining interpolation instead of a mechanical hard jump.
                gaze_smoothing = max(gaze_smoothing, ARRIVAL_GAZE_SMOOTHING)
            elif self.proximity_state == "retreating":
                gaze_smoothing = min(gaze_smoothing, RETREAT_GAZE_SMOOTHING)
            if not self.blink_freeze:
                self.gaze_x += (voice_tx - self.gaze_x) * gaze_smoothing
                self.gaze_y += (voice_ty - self.gaze_y) * gaze_smoothing

            self.gaze_x = max(-1.0, min(1.0, self.gaze_x))
            self.gaze_y = max(-1.0, min(1.0, self.gaze_y))

            # ---- 瞳孔尺度 / 呼吸 / 检测到人 ----
            base_pupil_target = PUPIL_BASE_SCALE
            if self.face_detected and self.proximity_state not in (
                    "retreating", "disengaging", "distant"):
                base_pupil_target = PUPIL_DETECTED_SCALE
            self.pupil_target = base_pupil_target * voice_pupil_mult
            self.pupil_scale += (self.pupil_target - self.pupil_scale) * PUPIL_SMOOTH_FACTOR

            if self.blink_freeze or self.blink_closed_ramp or self.blink_post_open:
                self.breath = 0.0
            else:
                self.breath = math.sin(now_ms * BREATH_FREQ) * BREATH_AMP

            pupil_relative_scale = self.pupil_scale + self.breath + self.blink_pupil_dilate

            # ---- 视线 jitter（fixed 是渲染/SPI 隔离模式，必须静止）----
            # 轻微 jitter 仅模拟活体感，不改变 policy/pending，也不反馈到 FaceTracker。
            if self.blink_freeze or self.gaze_policy == "fixed":
                jitter_x = jitter_y = 0.0
            else:
                jitter_x = (
                    math.sin(now_ms * 0.012) + math.cos(now_ms * 0.027)
                ) * GAZE_JITTER_AMP
                jitter_y = (
                    math.cos(now_ms * 0.015) + math.sin(now_ms * 0.023)
                ) * GAZE_JITTER_AMP

            render_x = max(-1.0, min(1.0, self.gaze_x + jitter_x))
            render_y = max(-1.0, min(1.0, self.gaze_y + jitter_y))

            # ---- 高光高频微颤 ----
            if self.blink_freeze:
                glint_jitter_x = glint_jitter_y = 0.0
            else:
                glint_jitter_x = (
                    math.sin(now_ms * 0.3) + random.random() * 0.5 - 0.25
                ) * GLINT_JITTER_AMP
                glint_jitter_y = (
                    math.cos(now_ms * 0.35) + random.random() * 0.5 - 0.25
                ) * GLINT_JITTER_AMP

            # ---- 辐辏（当前关闭，VERGENCE_MAX = 0）----
            vergence_px = VERGENCE_MAX

            # IDLE 模式：无人脸/运动目标
            is_idle = not self.face_detected and not self.motion_detected

            # ---- 表情覆盖：激活时用表情时间线参数替代眨眼状态机/追踪行为 ----
            # 优先级：睡眠（sleep_takeover）> 表情 > arrival > interest。
            expr, expr_start, expression_command_id = self._expression_for_frame(now_s)
            expr_params = None
            if sleep_takeover:
                expr = self._sleep_expression
                expr_params = sleep_params
            elif expr is not None:
                # 循环表情用 % cycle；一次性 JSON 锚点表情在 duration 后停在 hold_t（默认最后一帧）。
                if getattr(expr, "loop", True):
                    expr_now = (now_s - expr_start) % expr.cycle
                else:
                    hold_t = getattr(expr, "hold_t", expr.duration)
                    expr_now = max(0.0, min(now_s - expr_start, hold_t))
                expr_params = expr.step(expr_now)
            elif arrival_params is not None:
                # ArrivalExcitement only owns presentation parameters. It
                # must never replace the viewer's current gaze target.
                expr = self._arrival_excitement
                expr_params = arrival_params
            elif interest_params is not None:
                # Interest 的 step() 在结束时钳制最后一帧；_interest_for_frame
                # 只会将此最终帧保留 3 秒，随后即使人仍很近也恢复普通注视。
                expr = self._interest_expression
                expr_params = interest_params

            review_provider = self._review_frame_provider
            # review 模式：用 provider() 返回的 RGBA 图，覆盖到样式底色后贴屏，
            # 与正常 draw_eye 路径共用合成代码（保留眼皮裁剪 / 圆外透明 / debug
            # overlay）。provider 可以返回单张图（双眼同）或 (左,右) 元组；可以是
            # RGB（视为完整成品）或 RGBA（参与 alpha 合成）。
            if review_provider is not None:
                review_frame = review_provider()
                if isinstance(review_frame, tuple):
                    left_img, right_img = review_frame
                else:
                    left_img = right_img = review_frame
                for idx, (tft, _mirror) in enumerate(self._displays):
                    src = left_img if idx == 0 else right_img
                    if src is None:
                        eye_img = self._bg_img.copy()
                    else:
                        src_rgba = src.convert("RGBA").resize((WIDTH, HEIGHT))
                        eye_img = Image.alpha_composite(self._bg_img, src_rgba)
                    # 左眼加 debug overlay（保持与正常路径一致）
                    if idx == 0 and self.debug_overlay is not None:
                        overlay = self.debug_overlay.render(size=(WIDTH, HEIGHT))
                        eye_img = Image.alpha_composite(eye_img, overlay)
                    if expression_command_id is not None and self._expression_debug is not None:
                        self._expression_debug.record_render(
                            expression_command_id, time.monotonic_ns()
                        )
                    tft.display(eye_img)
                    if expression_command_id is not None and self._expression_debug is not None:
                        self._expression_debug.record_spi_done(
                            expression_command_id, time.monotonic_ns()
                        )
            else:
                for idx, (tft, mirror) in enumerate(self._displays):
                    if expr_params is not None:
                        # 表情路径不用追踪镜像（两屏都 True 会导致画面一模一样），
                        # 改由表情声明左右眼各自的 mirror（与 GIF 生成器 dual 一致）
                        preserve_gaze = getattr(expr, "preserves_gaze", False)
                        expr_mirror = mirror if preserve_gaze else expr.mirror_for_eye(idx)
                        expr_gaze_x = render_x if preserve_gaze else expr_params["gaze_x"]
                        expr_gaze_y = render_y if preserve_gaze else expr_params["gaze_y"]
                        expr_eyelid = expr_params["eyelid"]
                        expr_pupil_scale = expr_params.get("pupil_relative_scale", 1.0)
                        # frame_kwargs 可能透传 JSON 里的 vergence_x，而 draw_eye 会显式接收它，
                        # 为避免重复关键字参数，先取出再从 kwargs 里移除。
                        expr_frame_kwargs = expr.frame_kwargs(expr_params, expr_mirror)
                        expr_vergence_x = expr_frame_kwargs.pop("vergence_x", 0)
                        # 无风格时样式参数为空 dict，避免 draw_eye 收到重复的 style 关键字
                        if not expr_frame_kwargs:
                            expr_frame_kwargs = None
                        eye_img = draw_eye(
                            expr_gaze_x,
                            expr_gaze_y,
                            eyelid=expr_eyelid,
                            mirror=expr_mirror,
                            pupil_relative_scale=expr_pupil_scale,
                            glint_jitter_x=expr_params.get("glint_jitter_x", 0.0),
                            glint_jitter_y=expr_params.get("glint_jitter_y", 0.0),
                            vergence_x=expr_vergence_x,
                            is_idle=expr_params.get("is_idle", False),
                            style=self.style,
                            **(expr_frame_kwargs or {}),
                        )
                    else:
                        vergence_x = vergence_px if idx == 0 else -vergence_px
                        eye_img = draw_eye(
                            render_x,
                            render_y,
                            eyelid=eyelid,
                            mirror=mirror,
                            pupil_relative_scale=pupil_relative_scale,
                            glint_jitter_x=glint_jitter_x,
                            glint_jitter_y=glint_jitter_y,
                            vergence_x=vergence_x,
                            is_idle=is_idle,
                            style=self.style,
                        )
                    # 合成到样式背景色：draw_eye 返回 RGBA，眼睑裁剪/圆外区域为透明，
                    # 而 display() 的 convert("RGB") 直接丢 alpha，会透出底层瞳孔 RGB
                    # （旧版矩形遮罩同病）。按 doll-eye-styles GIF 生成器的方式合成底色。
                    eye_img = Image.alpha_composite(self._bg_img, eye_img)
                    # 仅在左眼叠加调试信息，且保持半透明不遮挡眼睛
                    if idx == 0 and self.debug_overlay is not None:
                        overlay = self.debug_overlay.render(size=(WIDTH, HEIGHT))
                        eye_img = Image.alpha_composite(eye_img, overlay)
                    if expression_command_id is not None and self._expression_debug is not None:
                        self._expression_debug.record_render(
                            expression_command_id, time.monotonic_ns()
                        )
                    tft.display(eye_img)
                    if expression_command_id is not None and self._expression_debug is not None:
                        self._expression_debug.record_spi_done(
                            expression_command_id, time.monotonic_ns()
                        )

            # 两块屏幕均完成本帧 display() 后再记时间，作为软件可见的 SPI 提交边界。
            frame_completed_ns = time.monotonic_ns()
            self._record_eye_frame(frame_completed_ns)
            self._record_listening_cue_frame(frame_completed_ns)

            # 帧时间预算：稳定 25 fps
            frame_time = time.time() - now_s
            sleep_time = TARGET_FRAME_TIME - frame_time
            if sleep_time > 0:
                time.sleep(sleep_time)

        GPIO.cleanup()
        print("[EYE] 动画线程结束")

    def start(self):
        if self.running:
            return
        self.running = True
        name = "eye-display"
        self._thread = threading.Thread(target=self._animation_loop,
                                        daemon=True, name=name)
        self._thread.start()
        print(f"[EYE] 启动 ({'双目' if self.dual else '单目'})")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        print("[EYE] 已停止")
