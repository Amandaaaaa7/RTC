"""
眼睛表情动画 — 从 doll-eye-styles 的 GIF 生成器移植的参数时间线。

每种表情是一个 5 秒循环的动画状态机：step(now) 返回该时刻的渲染参数，
frame_kwargs(params, mirror) 返回该表情特有的 draw_eye 额外参数
（眼睑倾斜/汇合位置/泪珠高光等）。

表情清单：happy 开心 / angry 生气 / cry 哭 / dislike 嫌弃 / miosis 缩瞳 / surprise 惊讶。
EyeDisplay.set_expression(name) 挂载后，动画循环用表情参数覆盖正常 idle 行为；
set_expression(None) 恢复。来源：doll-eye-styles/tools/generate_*_gifs.py。
"""

import json
import math
import os
import random


# 与 GIF 生成器一致的微动参数
GAZE_JITTER_AMP = 0.005
GLINT_JITTER_AMP = 0.8
BREATH_FREQ = 0.0007
BREATH_AMP = 0.002

# JSON 锚点表情网格路径（与 review_browser / eye_display 共用）
ANCHOR_GRID_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "outputs", "reviews", "_anchor_grid.json"
)


def _ease_in_out(t: float) -> float:
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 2 / 2.0


def _jitter(now_ms):
    jx = (math.sin(now_ms * 0.012) + math.cos(now_ms * 0.027)) * GAZE_JITTER_AMP
    jy = (math.cos(now_ms * 0.015) + math.sin(now_ms * 0.023)) * GAZE_JITTER_AMP
    gx = (math.sin(now_ms * 0.3) + random.random() * 0.5 - 0.25) * GLINT_JITTER_AMP
    gy = (math.cos(now_ms * 0.35) + random.random() * 0.5 - 0.25) * GLINT_JITTER_AMP
    return jx, jy, gx, gy


class _ExpressionBase:
    """表情基类：统一双眼镜像策略（与 GIF 生成器 dual 渲染一致）。

    EyeDisplay 因摄像头旋转 180°，追踪路径对两个屏幕都用 mirror=True；
    表情路径不能用追踪镜像（否则两屏画面一模一样），改由各表情声明
    左右眼各自的 mirror 标志——轴对称表情（开心/生气/哭）右眼镜像，
    同向斜视表情（嫌弃）和对称表情（缩瞳）双眼一致。
    """

    name = ""
    cn_name = ""
    cycle = 5.0
    loop = True
    # 轴对称表情的右眼镜像。注意物理接线与 GIF 生成器约定相反：
    # LEFT 引脚的屏是玩偶的右眼，所以 idx0=左屏 要镜像、idx1=右屏 用原图
    DUAL_MIRROR = (True, False)  # (左屏, 右屏)

    def mirror_for_eye(self, idx: int) -> bool:
        return self.DUAL_MIRROR[min(idx, len(self.DUAL_MIRROR) - 1)]


class HappyExpression(_ExpressionBase):
    """开心：双眼睑向中间眯起，在巩膜上三分之一处合并。"""

    name = "happy"
    cn_name = "开心"
    cycle = 5.0
    CLOSE_Y = 55.0

    def step(self, now: float) -> dict:
        now_ms = now * 1000.0
        eyelid, gaze_y = 0, 0.0

        if now < 0.5:
            eased = _ease_in_out(min(1.0, now / 0.5))
            eyelid = int(120 * eased)
            gaze_y = -0.15 * eased
        elif now < 1.5:
            tremor = math.sin(now_ms * 0.008) * 2 + math.cos(now_ms * 0.015) * 1
            eyelid = 120 + int(round(tremor))
            gaze_y = -0.15
        elif now < 1.54:
            pass  # 瞬间睁开

        jx, jy, gx, gy = _jitter(now_ms)
        return {
            "gaze_x": max(-1.0, min(1.0, jx)),
            "gaze_y": max(-1.0, min(1.0, gaze_y + jy)),
            "eyelid": eyelid,
            "pupil_relative_scale": 1.0 + math.sin(now_ms * BREATH_FREQ) * BREATH_AMP,
            "glint_jitter_x": gx,
            "glint_jitter_y": gy,
            "is_idle": True,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        return {
            "eyelid_close_y": self.CLOSE_Y,
            "lower_eyelid_cup": True,
            "show_lower_eyelid": True,
        }


class AngryExpression(_ExpressionBase):
    """生气：上眼睑倾斜下压变平，视线斜向。"""

    name = "angry"
    cn_name = "生气"
    cycle = 5.0

    def step(self, now: float) -> dict:
        now_ms = now * 1000.0
        eyelid, tilt = 0, 0
        gaze_x, gaze_y = 0.0, 0.0
        target_y, flatten = None, 0.0

        if now < 0.5:
            eased = _ease_in_out(min(1.0, now / 0.5))
            eyelid = int(55 * eased)
            tilt = int(28 * eased)
            gaze_x, gaze_y = 0.4 * eased, 0.25 * eased
            target_y, flatten = 80, 1.0 * eased
        elif now < 2.0:
            eyelid = 55 + int(round(math.sin(now_ms * 0.002) * 2))
            tilt = 28
            gaze_x, gaze_y = 0.4, 0.25
            target_y, flatten = 80, 1.0
        elif now < 2.5:
            eased = 1.0 - _ease_in_out(min(1.0, (now - 2.0) / 0.5))
            eyelid = int(55 * eased)
            tilt = int(28 * eased)
            gaze_x, gaze_y = 0.4 * eased, 0.25 * eased
            if eased > 0.01:
                target_y, flatten = 80, 1.0 * eased

        jx, jy, gx, gy = _jitter(now_ms)
        return {
            "gaze_x": max(-1.0, min(1.0, gaze_x + jx)),
            "gaze_y": max(-1.0, min(1.0, gaze_y + jy)),
            "eyelid": eyelid,
            "eyelid_tilt": tilt,
            "eyelid_target_y": target_y,
            "eyelid_flatten": flatten,
            "pupil_relative_scale": 1.0 + math.sin(now_ms * BREATH_FREQ) * BREATH_AMP,
            "glint_jitter_x": gx,
            "glint_jitter_y": gy,
            "is_idle": True,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        # 右眼镜像时倾斜取反：双眼内侧眼角同时下压，形成轴对称怒眉
        tilt = params.get("eyelid_tilt", 0)
        if mirror:
            tilt = -tilt
        return {
            "eyelid_tilt": tilt,
            "eyelid_target_y": params.get("eyelid_target_y"),
            "eyelid_flatten": params.get("eyelid_flatten", 0),
            "show_lower_eyelid": False,
        }


class DislikeExpression(_ExpressionBase):
    """嫌弃：上眼睑下压成半眯 + 右偏斜视。"""

    name = "dislike"
    cn_name = "嫌弃"
    cycle = 5.0
    DUAL_MIRROR = (False, False)  # 双眼同向斜视，不镜像
    CLOSE_Y = 140.0
    UPPER_TARGET = 120.0
    MAX_EYELID = 92

    def step(self, now: float) -> dict:
        now_ms = now * 1000.0
        eyelid, gaze_x, gaze_y, target_y = 0, 0.0, 0.0, None

        if now < 0.5:
            eased = _ease_in_out(min(1.0, now / 0.5))
            eyelid = int(self.MAX_EYELID * eased)
            target_y = self.UPPER_TARGET
            gaze_x, gaze_y = 0.75 * eased, -0.12 * eased
        elif now < 2.0:
            tremor = math.sin(now_ms * 0.008) * 2 + math.cos(now_ms * 0.015) * 1
            eyelid = self.MAX_EYELID + int(round(tremor))
            target_y = self.UPPER_TARGET
            gaze_x, gaze_y = 0.75, -0.12
        elif now < 2.04:
            pass  # 瞬间恢复

        jx, jy, gx, gy = _jitter(now_ms)
        return {
            "gaze_x": max(-1.0, min(1.0, gaze_x + jx)),
            "gaze_y": max(-1.0, min(1.0, gaze_y + jy)),
            "eyelid": eyelid,
            "eyelid_target_y": target_y,
            "pupil_relative_scale": 1.0 + math.sin(now_ms * BREATH_FREQ) * BREATH_AMP,
            "glint_jitter_x": gx,
            "glint_jitter_y": gy,
            "is_idle": True,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        return {
            "eyelid_target_y": params.get("eyelid_target_y"),
            "show_lower_eyelid": True,
            "eyelid_close_y": self.CLOSE_Y,
        }


class CryExpression(_ExpressionBase):
    """哭：上眼睑倾斜下压 + 下眼睑跟随 + 虹膜下方旋转泪珠。"""

    name = "cry"
    cn_name = "哭"
    cycle = 5.0
    UPPER_TARGET = 100.0
    CLOSE_Y = 150.0
    MAX_EYELID = 95
    TILT = -10
    GAZE_X = 0.30
    GAZE_Y = -0.08

    def step(self, now: float) -> dict:
        now_ms = now * 1000.0
        eyelid, gaze_x, gaze_y, target_y = 0, 0.0, 0.0, None
        show_tears = False

        if now < 0.5:
            eased = _ease_in_out(min(1.0, now / 0.5))
            eyelid = int(self.MAX_EYELID * eased)
            target_y = self.UPPER_TARGET
            gaze_x, gaze_y = self.GAZE_X * eased, self.GAZE_Y * eased
            show_tears = True
        elif now < 2.0:
            tremor = math.sin(now_ms * 0.008) * 2 + math.cos(now_ms * 0.015) * 1
            eyelid = self.MAX_EYELID + int(round(tremor))
            target_y = self.UPPER_TARGET
            gaze_x, gaze_y = self.GAZE_X, self.GAZE_Y
            show_tears = True
        elif now < 2.04:
            pass  # 瞬间恢复

        jx, jy, gx, gy = _jitter(now_ms)
        return {
            "gaze_x": max(-1.0, min(1.0, gaze_x + jx)),
            "gaze_y": max(-1.0, min(1.0, gaze_y + jy)),
            "eyelid": eyelid,
            "eyelid_target_y": target_y,
            "pupil_relative_scale": 1.0 + math.sin(now_ms * BREATH_FREQ) * BREATH_AMP,
            "glint_jitter_x": gx,
            "glint_jitter_y": gy,
            "is_idle": True,
            "show_tears": show_tears,
            "glint_rotation": now_ms * 0.0025,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        eyelid_val = params.get("eyelid", 0)
        progress = min(1.0, eyelid_val / float(self.MAX_EYELID))
        tilt = self.TILT * progress
        if mirror:
            tilt = -tilt

        cry_glint_data = None
        if params.get("show_tears", False):
            gx = -params["gaze_x"] if mirror else params["gaze_x"]
            iris_cx = 80 + int(gx * 31)
            iris_cy = 80 + int(params["gaze_y"] * 31 * 0.65)
            rotation = params.get("glint_rotation", 0.0)
            base_size = 21 * progress
            size_mults = [1.0, 1.0, 1.0, 1.5]
            if mirror:
                size_mults = list(reversed(size_mults))
            positions = [
                (iris_cx - 25, iris_cy + 48, size_mults[0]),
                (iris_cx + 18, iris_cy + 52, size_mults[1]),
                (iris_cx - 12, iris_cy + 58, size_mults[2]),
                (iris_cx + 28, iris_cy + 45, size_mults[3]),
            ]
            cry_glint_data = [
                {"x": int(px), "y": int(py), "size": base_size * sm,
                 "rotation": rotation + i * 0.8, "alpha": 255}
                for i, (px, py, sm) in enumerate(positions)
            ]

        return {
            "eyelid_target_y": params.get("eyelid_target_y"),
            "show_lower_eyelid": True,
            "eyelid_close_y": self.CLOSE_Y,
            "eyelid_tilt": tilt,
            "lower_eyelid_tilt": tilt,
            "cry_glint_data": cry_glint_data,
        }


class MiosisExpression(_ExpressionBase):
    """缩瞳：眼睑闭合时瞳孔/高光/虹膜同步缩小，睁开恢复。"""

    name = "miosis"
    cn_name = "缩瞳"
    cycle = 5.0
    DUAL_MIRROR = (False, False)  # 居中对称，无需镜像
    MAX_LID = 80
    CLOSE_FRAC = 0.45
    HOLD_FRAC = 0.10
    OPEN_FRAC = 0.45

    def step(self, now: float) -> dict:
        phase = (now % self.cycle) / self.cycle

        if phase < self.CLOSE_FRAC:
            p = phase / self.CLOSE_FRAC
            eyelid = int(round(self.MAX_LID * p ** 3))
        elif phase < self.CLOSE_FRAC + self.HOLD_FRAC:
            eyelid = self.MAX_LID
        else:
            p = (phase - self.CLOSE_FRAC - self.HOLD_FRAC) / self.OPEN_FRAC
            eyelid = int(round(self.MAX_LID * (1.0 - (1.0 - (1.0 - p) ** 3))))

        t = min(1.0, eyelid / self.MAX_LID) if self.MAX_LID > 0 else 0.0
        return {
            "gaze_x": 0.0,
            "gaze_y": 0.0,
            "eyelid": eyelid,
            "pupil_relative_scale": 1.0 - (t ** 0.7) * 0.65,
            "glint_scale": max(0.3, 1.0 - (t ** 1.2) * 0.55),
            "iris_scale": max(0.5, 1.0 - (t ** 0.8) * 0.35),
            "glint_jitter_x": 0.0,
            "glint_jitter_y": 0.0,
            "is_idle": False,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        return {
            "glint_scale": params.get("glint_scale", 1.0),
            "iris_scale": params.get("iris_scale", 1.0),
        }


class SurpriseExpression(_ExpressionBase):
    """惊讶：睁眼 + 瞳孔放大 + 视线快速上抬，模拟被呼唤时的 alert 状态。

    时间线（共 2.5 秒，循环播放时会自然回到 idle）：
    - 0.0–0.2 s：快速睁大眼睑、瞳孔放大、视线上移。
    - 0.2–1.2 s：保持睁大 + 轻微颤抖，表现警觉。
    - 1.2–2.0 s：慢慢回到中性状态。
    """

    name = "surprise"
    cn_name = "惊讶"
    cycle = 2.5
    DUAL_MIRROR = (False, False)  # 居中对称，双眼一致
    MAX_LID = 115

    def step(self, now: float) -> dict:
        now_ms = now * 1000.0
        cycle_now = now % self.cycle
        eyelid, gaze_y, pupil_scale = 0, 0.0, 1.0

        if cycle_now < 0.2:
            t = _ease_in_out(min(1.0, cycle_now / 0.2))
            eyelid = int(self.MAX_LID * t)
            gaze_y = -0.22 * t
            pupil_scale = 1.0 + 0.25 * t
        elif cycle_now < 1.2:
            tremor = math.sin(now_ms * 0.02) * 1.5
            eyelid = self.MAX_LID + int(round(tremor))
            gaze_y = -0.22
            pupil_scale = 1.25
        elif cycle_now < 2.0:
            eased = 1.0 - _ease_in_out(min(1.0, (cycle_now - 1.2) / 0.8))
            eyelid = int(self.MAX_LID * eased)
            gaze_y = -0.22 * eased
            pupil_scale = 1.0 + 0.25 * eased

        jx, jy, gx, gy = _jitter(now_ms)
        return {
            "gaze_x": max(-1.0, min(1.0, jx)),
            "gaze_y": max(-1.0, min(1.0, gaze_y + jy)),
            "eyelid": eyelid,
            "pupil_relative_scale": pupil_scale,
            "glint_jitter_x": gx * 0.3,
            "glint_jitter_y": gy * 0.3,
            "is_idle": True,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        return {
            "show_lower_eyelid": False,
        }


class JsonAnchorExpression(_ExpressionBase):
    """从 JSON 锚点网格读取表情参数，用于首次见人 / 近距离兴趣等提示。

    指定 emotion、intensity，按时间插值取出对应帧；保留 gaze 由外部实时提供。

    默认播完后停在最后一帧（HOLD_FRAME_OFFSET=-1）。子类可覆盖为倒数第 N 帧，
    例如某些表情最后一帧是闭眼，停在倒数第 5 帧可改善观感。
    """

    # 非循环表情结束后停在第几帧：
    #   -1 表示最后一帧
    #   -5 表示倒数第 5 帧
    #   0.5 表示时间轴中点（duration * 0.5）
    #   正整数表示具体帧索引
    HOLD_FRAME_OFFSET = -1

    def __init__(self, emotion: str, intensity: float):
        self.emotion = emotion
        self.intensity = float(intensity)
        self.preserves_gaze = True
        self.loop = False  # JSON 锚点是一次性提示动画，播完后停在最后一帧
        self._frames = self._load_frames()
        self.duration = 0.0
        self.hold_t = 0.0
        if self._frames:
            self.duration = self._frames[-1]["t"]
            hold_offset = getattr(self, "HOLD_FRAME_OFFSET", -1)
            if isinstance(hold_offset, float) and 0.0 <= hold_offset <= 1.0:
                self.hold_t = self.duration * hold_offset
            elif isinstance(hold_offset, int):
                if hold_offset < 0:
                    hold_idx = max(0, len(self._frames) + hold_offset)
                else:
                    hold_idx = min(hold_offset, len(self._frames) - 1)
                self.hold_t = self._frames[hold_idx]["t"]
            else:
                self.hold_t = self.duration
        self.cycle = max(self.duration, 0.001)

    @property
    def name(self) -> str:
        return f"json_anchor_{self.emotion}_{self.intensity}"

    @property
    def cn_name(self) -> str:
        return f"{self.emotion}@{self.intensity}"

    def _load_grid(self) -> dict:
        if not os.path.exists(ANCHOR_GRID_PATH):
            return {}
        try:
            with open(ANCHOR_GRID_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_frames(self) -> list:
        grid = self._load_grid()
        rows = grid.get("rows", [])
        for row in rows:
            if row.get("emotion") == self.emotion:
                for cell in row.get("cells", []):
                    if abs(float(cell.get("intensity", 0)) - self.intensity) < 1e-6:
                        return cell.get("frames", [])
        return []

    def _interp(self, t: float) -> dict:
        if not self._frames:
            return {}
        if t <= self._frames[0]["t"]:
            return dict(self._frames[0]["params"])
        if t >= self._frames[-1]["t"]:
            return dict(self._frames[-1]["params"])
        for i in range(1, len(self._frames)):
            f0, f1 = self._frames[i - 1], self._frames[i]
            if t <= f1["t"]:
                p0, p1 = f0["t"], f1["t"]
                ratio = (t - p0) / (p1 - p0) if p1 != p0 else 0.0
                params = {}
                for key, v0 in f0["params"].items():
                    v1 = f1["params"].get(key, v0)
                    if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
                        params[key] = v0 + (v1 - v0) * ratio
                    else:
                        params[key] = v0
                return params
        return dict(self._frames[-1]["params"])

    def step(self, now: float) -> dict:
        t = max(0.0, min(now, self.duration))
        params = self._interp(t)
        params.setdefault("is_idle", False)
        # gaze 由 EyeDisplay 用实时人脸坐标覆盖，此处只提供占位
        params.setdefault("gaze_x", 0.0)
        params.setdefault("gaze_y", 0.0)
        return params

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        # 将 JSON 中的参数直接透传给 draw_eye
        return {
            key: params.get(key)
            for key in (
                "eyelid_close_y", "lower_eyelid_cup", "show_lower_eyelid",
                "eyelid_tilt", "eyelid_target_y", "eyelid_flatten",
                "lower_eyelid_tilt", "cry_glint_data", "vergence_x",
            )
        }


class Surprise1Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Surprise@1.0 的惊讶表情。

    比手写 SurpriseExpression 更贴近原设计：眼睑快速睁开、瞳孔放大、
    gaze 由 EyeDisplay 用实时人脸坐标覆盖。
    """

    def __init__(self):
        super().__init__("Surprise", 1.0)

    @property
    def name(self) -> str:
        return "surprise1.0"

    @property
    def cn_name(self) -> str:
        return "惊讶1.0"


class Amusement1Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Amusement@1.0 的开心/被逗乐表情。"""

    def __init__(self):
        super().__init__("Amusement", 1.0)

    @property
    def name(self) -> str:
        return "amusement1.0"

    @property
    def cn_name(self) -> str:
        return "开心1.0"


class Calmness1Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Calmness@1.0 的平静表情。"""

    def __init__(self):
        super().__init__("Calmness", 1.0)

    @property
    def name(self) -> str:
        return "calmness1.0"

    @property
    def cn_name(self) -> str:
        return "平静1.0"


class Excitement0_6Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Excitement@0.6 的轻度兴奋表情。

    源动画的末帧会收眼；停在倒数第 5 帧，使问候反应维持在最睁的兴奋状态。
    """

    HOLD_FRAME_OFFSET = -5

    def __init__(self):
        super().__init__("Excitement", 0.6)

    @property
    def name(self) -> str:
        return "excitement0.6"

    @property
    def cn_name(self) -> str:
        return "兴奋0.6"


class Sadness0_6Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Sadness@0.6 的轻微委屈/难过表情。

    动画末段会完全闭眼，因此播完后停在时间轴中点，保持半睁开/委屈的神态。
    """

    HOLD_FRAME_OFFSET = 0.5

    def __init__(self):
        super().__init__("Sadness", 0.6)

    @property
    def name(self) -> str:
        return "sadness0.6"

    @property
    def cn_name(self) -> str:
        return "难过0.6"

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        # 本类固定使用 SLEEP_CLOSE_Y 作 close_y，与 base 默认一致；覆盖仅为
        # 显式声明、消除歧义（与后续 Sadness/Sleep 子类风格统一）。
        return {"eyelid_close_y": 147.0}


class Sadness1_0Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Sadness@1.0 的明显委屈/难过表情。

    动画末段会完全闭眼，因此播完后停在时间轴中点，保持半睁开/委屈的神态。
    """

    HOLD_FRAME_OFFSET = 0.5

    def __init__(self):
        super().__init__("Sadness", 1.0)

    @property
    def name(self) -> str:
        return "sadness1.0"

    @property
    def cn_name(self) -> str:
        return "难过1.0"

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        return {"eyelid_close_y": 147.0}


class Interest0_6Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Interest@0.6 的好奇/倾听表情（较柔和）。"""

    def __init__(self):
        super().__init__("Interest", 0.6)

    @property
    def name(self) -> str:
        return "interest0.6"

    @property
    def cn_name(self) -> str:
        return "好奇0.6"


class Interest1_0Expression(JsonAnchorExpression):
    """使用 doll-eye-styles 锚点网格 Interest@1.0 的明显好奇/倾听表情。

    用于语音关键词触发哼歌反应，表达"想听"的兴趣状态。
    比 Interest@0.6 更强烈：动画全程眼睛逐渐睁大，瞳孔放大，
    末段保持最大睁开状态，避免 0.6 版本后半段眼睑闭合的问题。
    """

    def __init__(self):
        super().__init__("Interest", 1.0)

    @property
    def name(self) -> str:
        return "interest1.0"

    @property
    def cn_name(self) -> str:
        return "好奇1.0"


class ArrivalExcitement(JsonAnchorExpression):
    """首次见人时的兴奋提示：使用 JSON 锚点 Excitement / 1.0。"""

    def __init__(self):
        super().__init__("Excitement", 1.0)

    @property
    def name(self) -> str:
        return "arrival_excitement"

    @property
    def cn_name(self) -> str:
        return "首次兴奋"


class SleepExpression(_ExpressionBase):
    """睡着：完整复刻 sleep_gifs_cry.SleepAnimator 的 20s 时间线。

    触发时机由 EyeDisplay 的 sleep/wake 状态机决定，本类只负责“播放入睡轨迹”。
    时间线（与离线 GIF 生成器 sleep_gifs_cry.py 完全一致）：
        0-2s    SOFTEN        上睑 target_y 117→84，巩膜升 4px
        2-13s   BLINK(双周期) 周期1 84→100→92，周期2 92→115→110
        13-14s  HOLD          停在 110（不回 soft-close）
        14-19s  TRANSITION    110→131
        19-20s+ FULLY_CLOSED  131，眼睑 110 但 target_y 拉满 → 全黑
    `awake()` 复位到 0，供“人出现 → 立刻睁眼”立即结束入睡动画。

    注：eye_offset_y / lower_lid_motion 是离线 GIF 生成器的渲染细节，主仓
    eye_render.draw_eye 不消费这两个参数，故运行时轨迹不输出。
    """

    name = "sleep"
    cn_name = "睡着"
    # 入睡轴对称：DUAL_MIRROR = (True, False)（沿用 _ExpressionBase 默认值）
    # 与 GIF 生成器 dual 渲染（左原图、右镜像）一致。
    SLEEP_CLOSE_Y = 147.0
    SLEEP_UPPER_TARGET = 117.0
    SLEEP_SOFTEN_DUR = 2.0
    SLEEP_BLINK_DUR = 11.0
    SLEEP_BLINK_CYCLE1_DUR = 5.0
    SLEEP_BLINK_CYCLE1_TARGET_Y = 100.0
    SLEEP_BLINK_CYCLE1_END_Y = 92.0
    SLEEP_BLINK_CYCLE2_END_Y = 110.0
    SLEEP_BLINK_TARGET_Y = 115.0
    SLEEP_HOLD_END = 14.0
    SLEEP_FULLY_CLOSED_END = 19.0
    SLEEP_UPPER_TARGET_FULLY = 131.0
    SLEEP_UPPER_TARGET_SOFT = 84.0
    SLEEP_MAX_EYELID = 110
    SLEEP_GAZE_Y = -0.40
    SLEEP_CONVERGE_X = 0.15

    def __init__(self):
        self._start = None  # None 表示尚未触发

    def awake(self):
        """立即复位到 0，结束入睡动画（人出现 → 立刻睁眼）。"""
        self._start = None

    @property
    def loop(self) -> bool:
        return self._start is not None

    def step(self, now: float) -> dict:
        now_ms = now * 1000.0
        eyelid = 0
        gaze_x = 0.0
        gaze_y = 0.0
        eyelid_target_y = None
        peak_y = self.SLEEP_BLINK_TARGET_Y

        if now < self.SLEEP_SOFTEN_DUR:
            # SOFTEN：全睁 → soft-close
            soften_t = now / self.SLEEP_SOFTEN_DUR
            eased = _ease_in_out(min(1.0, max(0.0, soften_t)))
            eyelid = int(self.SLEEP_MAX_EYELID * eased)
            eyelid_target_y = self.SLEEP_UPPER_TARGET + (
                self.SLEEP_UPPER_TARGET_SOFT - self.SLEEP_UPPER_TARGET
            ) * eased
        elif now < self.SLEEP_SOFTEN_DUR + self.SLEEP_BLINK_DUR:
            # BLINK：双周期
            blink_t = (now - self.SLEEP_SOFTEN_DUR) / self.SLEEP_BLINK_DUR
            cycle1_ratio = self.SLEEP_BLINK_CYCLE1_DUR / self.SLEEP_BLINK_DUR
            if blink_t < cycle1_ratio:
                phase = blink_t / cycle1_ratio
                peak_y = self.SLEEP_BLINK_CYCLE1_TARGET_Y
                cycle_start_y = self.SLEEP_UPPER_TARGET_SOFT
                cycle_end_y = self.SLEEP_BLINK_CYCLE1_END_Y
            else:
                phase = (blink_t - cycle1_ratio) / (1.0 - cycle1_ratio)
                peak_y = self.SLEEP_BLINK_TARGET_Y
                cycle_start_y = self.SLEEP_BLINK_CYCLE1_END_Y
                cycle_end_y = self.SLEEP_BLINK_CYCLE2_END_Y
            if phase < 0.5:
                bt = phase * 2
                eased = _ease_in_out(min(1.0, max(0.0, bt)))
                eyelid_target_y = cycle_start_y + (peak_y - cycle_start_y) * eased
            else:
                bt = (phase - 0.5) * 2
                eased = _ease_in_out(min(1.0, max(0.0, bt)))
                eyelid_target_y = peak_y + (cycle_end_y - peak_y) * eased
            eyelid = self.SLEEP_MAX_EYELID
        elif now < self.SLEEP_HOLD_END:
            # HOLD：停在 110，不回 soft-close
            eyelid = self.SLEEP_MAX_EYELID
            eyelid_target_y = self.SLEEP_BLINK_CYCLE2_END_Y
        elif now < self.SLEEP_FULLY_CLOSED_END:
            # TRANSITION：110→131
            t = (now - self.SLEEP_HOLD_END) / (
                self.SLEEP_FULLY_CLOSED_END - self.SLEEP_HOLD_END
            )
            eased = _ease_in_out(min(1.0, max(0.0, t)))
            eyelid = self.SLEEP_MAX_EYELID
            eyelid_target_y = self.SLEEP_BLINK_CYCLE2_END_Y + (
                self.SLEEP_UPPER_TARGET_FULLY - self.SLEEP_BLINK_CYCLE2_END_Y
            ) * eased
        else:
            # FULLY_CLOSED：停在 131
            eyelid = self.SLEEP_MAX_EYELID
            eyelid_target_y = self.SLEEP_UPPER_TARGET_FULLY

        # gaze 与眼睑进度绑死，无跳变
        e_ratio = eyelid / self.SLEEP_MAX_EYELID if self.SLEEP_MAX_EYELID > 0 else 0.0
        extra = 0.0
        if eyelid_target_y is not None and eyelid_target_y > self.SLEEP_BLINK_TARGET_Y:
            extra = (
                (eyelid_target_y - self.SLEEP_BLINK_TARGET_Y)
                / (self.SLEEP_UPPER_TARGET_FULLY - self.SLEEP_BLINK_TARGET_Y)
                * 1.5
            )
        gaze_factor = e_ratio + extra
        gaze_y = self.SLEEP_GAZE_Y * gaze_factor
        gaze_x = self.SLEEP_CONVERGE_X * gaze_factor

        # 入睡后期冻结 jitter（频率随 close_progress 衰减，全闭时完全静止）
        if eyelid_target_y is not None and eyelid_target_y > self.SLEEP_UPPER_TARGET_SOFT:
            close_progress = (eyelid_target_y - self.SLEEP_UPPER_TARGET_SOFT) / (
                self.SLEEP_UPPER_TARGET_FULLY - self.SLEEP_UPPER_TARGET_SOFT
            )
            slow_factor = max(0.0, 1.0 - close_progress)
        else:
            slow_factor = 1.0
        jx = (math.sin(now_ms * 0.012 * slow_factor) + math.cos(now_ms * 0.027 * slow_factor)) * GAZE_JITTER_AMP
        jy = (math.cos(now_ms * 0.015 * slow_factor) + math.sin(now_ms * 0.023 * slow_factor)) * GAZE_JITTER_AMP
        gx = (math.sin(now_ms * 0.3 * slow_factor) + random.random() * 0.5 - 0.25) * GLINT_JITTER_AMP
        gy = (math.cos(now_ms * 0.35 * slow_factor) + random.random() * 0.5 - 0.25) * GLINT_JITTER_AMP

        return {
            "gaze_x": max(-1.0, min(1.0, gaze_x + jx)),
            "gaze_y": max(-1.0, min(1.0, gaze_y + jy)),
            "eyelid": eyelid,
            "eyelid_target_y": eyelid_target_y,
            "pupil_relative_scale": 1.0 + math.sin(now_ms * BREATH_FREQ) * BREATH_AMP,
            "glint_jitter_x": gx,
            "glint_jitter_y": gy,
            "is_idle": True,
        }

    def frame_kwargs(self, params: dict, mirror: bool) -> dict:
        return {
            "show_lower_eyelid": True,
            "eyelid_close_y": self.SLEEP_CLOSE_Y,
        }


EXPRESSIONS = {
    e.name: e for e in (
        HappyExpression(),
        AngryExpression(),
        CryExpression(),
        DislikeExpression(),
        MiosisExpression(),
        SurpriseExpression(),
        Surprise1Expression(),
        Amusement1Expression(),
        Calmness1Expression(),
        Excitement0_6Expression(),
        Sadness0_6Expression(),
        Sadness1_0Expression(),
        Interest0_6Expression(),
        Interest1_0Expression(),
        SleepExpression(),
    )
}


def get_expression(name: str):
    """按名取表情对象，未知名抛 KeyError（含可用列表）。"""
    try:
        return EXPRESSIONS[name]
    except KeyError:
        raise KeyError(
            f"未知表情 '{name}'，可用: {', '.join(EXPRESSIONS)}"
        ) from None


def list_expressions():
    """返回 [(name, cn_name), ...]，按预设展示顺序。"""
    order = ["happy", "angry", "cry", "dislike", "miosis", "surprise",
             "surprise1.0", "amusement1.0", "calmness1.0", "excitement0.6",
             "sadness0.6", "sadness1.0",
             "interest0.6", "interest1.0", "sleep"]
    return [(n, EXPRESSIONS[n].cn_name) for n in order]
