"""眼睑几何层：从 eye_render 提取的可复用数学工具。

只做「眼睑弧线怎么算」，不做「眼睑弧线怎么画」。
draw_eye() 仍负责绘制（与 eye Image 紧耦合），但所有几何参数与弧线点列
都从此模块取，外部代码（如 eye-emotion-engine）也能 import。

公开 API：
    LID_COLOR              上眼睑填充色（深棕色 RGBA）
    END_Y                  巩膜左右边缘 y 坐标
    CLOSE_Y                闭眼时上下眼睑汇合位置
    lid_apex_positions     取上下眼睑顶点 y（供外部 e.g. 泪光贴位用）
    compute_lid_arcs       取上下眼睑弧线点列 + 顶点（供 draw_eye 用）

历史：原本 _circle_arc_pts 是 draw_eye() 内部的嵌套函数，弧线预计算
（约 35 行）也散落在 draw_eye() 中段。提取到这里后：
  1. eye-emotion-engine._tear_glints 早已在等 lid_apex_positions，现在终于能 import
  2. draw_eye() 眼睑段落从 ~75 行缩到 ~15 行调用
  3. 弧线数学可独立单元测试
"""

from __future__ import annotations

import math
from typing import NamedTuple

from eye_render import WIDTH, HEIGHT  # 常量同步源（draw_eye 已先 import）


# ============================================================
# 眼睑常量（与 eye_render 中原本内联的值一致；抽出后变 single source of truth）
# ============================================================

# 深棕色（眼睑填充）
LID_COLOR: tuple[int, int, int, int] = (55, 28, 10, 255)

# 巩膜左右边缘 y 坐标（弧线两端固定点）
END_Y: float = 80.0

# 闭眼时上下眼睑汇合位置（略低于中心线 → 形成自然向下弧线）
CLOSE_Y: float = 107.0

# 眼睑行程上限（与 draw_eye 中 max(0, min(120, eyelid)) 一致）
EYELID_MAX: float = 120.0

# 闭眼阈值：eyelid >= 此值时跳过虹膜/瞳孔/高光绘制
EYELID_CLOSE_SKIP: float = 115.0


# ============================================================
# 公开 API：上下眼睑顶点 y（外部调用方 e.g. 泪光贴位）
# ============================================================

def lid_apex_positions(
    eyelid: float,
    close_y: float = CLOSE_Y,
    target_y: float | None = None,
    lower_eyelid_cup: bool = False,
    lower_lid_motion_scale: float = 0.28,
) -> tuple[float, float]:
    """计算上下眼睑顶点 y 坐标。

    参数与 eye-emotion-engine._tear_glints 调用契约完全一致：
        lid_apex_positions(eyelid, close_y, target_y, lower_eyelid_cup,
                           lower_lid_motion_scale)

    返回 (upper_apex_y, lower_apex_y)。

    注：lower_lid_motion_scale 是 emotion-engine 用来表示 Duchenne 下睑
    上推幅度的参数；doll-eye-styles 当前渲染未消费此参数（接受但忽略），
    仅为 API 兼容而保留。
    """
    eyelid_clamped = max(0.0, min(EYELID_MAX, eyelid))

    # 上眼睑顶点
    if target_y is not None:
        upper_apex_y = eyelid_clamped * (float(target_y) / EYELID_MAX)
    else:
        upper_apex_y = eyelid_clamped * (close_y / EYELID_MAX)

    # 下眼睑顶点
    if lower_eyelid_cup:
        # ∩→∪ 翻转：用 sqrt 加速曲线让中部先翻为 ∪
        progress = eyelid_clamped / EYELID_MAX
        accelerated = progress ** 0.5
        lower_apex_y = HEIGHT - accelerated * (HEIGHT - close_y)
    else:
        # ∩ 形，从底部（y=HEIGHT）线性上移到 close_y
        lower_apex_y = HEIGHT - eyelid_clamped * ((HEIGHT - close_y) / EYELID_MAX)

    return upper_apex_y, lower_apex_y


def eye_offset_from_apex(upper_apex_x: float) -> float:
    """根据上睑 peak x 推导眼睛的水平偏移（规则：眼睛跟着 peak 一起移动）。

    规则：eye_offset_x = upper_apex_x - WIDTH / 2
    - upper_apex_x = 80（默认中央）→ eye_offset_x = 0（不偏移）
    - upper_apex_x > 80（peak 右移）→ eye_offset_x > 0（眼睛整体右移）
    - upper_apex_x < 80（peak 左移）→ eye_offset_x < 0（眼睛整体左移）
    """
    return float(upper_apex_x) - WIDTH / 2.0


# ============================================================
# 内部弧线生成（与原 draw_eye 内 _circle_arc_pts 数学等价）
# ============================================================

def _circle_arc_pts(
    apex_y: float,
    endpoint_y: float = END_Y,
    apex_x: float = WIDTH / 2.0,
    endpoint_y_left: float | None = None,
    endpoint_y_right: float | None = None,
) -> list[tuple[int, int]]:
    """用与巩膜同圆的圆弧点，从 (0, eyl) 到 (WIDTH, eyr) 经过 (apex_x, apex_y)。

    apex_x 默认 WIDTH/2=80（中央对称）。
    偏移 apex_x 可让弧线峰值左右移动 → 产生"左侧缓、右侧陡"或反之的不对称弧。

    endpoint_y_left / endpoint_y_right 默认均为 endpoint_y（保持向后兼容）。
    当两者不同时，分别作为左右端 y，可形成"左/右上抬或下垂"的不对称端点。
    这常用于让下眼睑端点对齐上眼睑端点（tilt 后）。

    当 apex_y < endpoint_y 时弧线为 ∪ 形（向上凸 = cup 笑眼 / U 形哭），
    当 apex_y > endpoint_y 时为 ∩ 形（向下凸 = 标准下压眼睑）。
    """
    eyl = float(endpoint_y if endpoint_y_left is None else endpoint_y_left)
    eyr = float(endpoint_y if endpoint_y_right is None else endpoint_y_right)
    # 退化：左右端 y 接近时退化为直线，避免除零
    if abs(apex_y - eyl) < 0.5 and abs(apex_y - eyr) < 0.5:
        return [(x, int(eyl)) for x in range(0, WIDTH + 1)]

    # 不对称圆弧：左右两半各一个圆，圆心都在 x = apex_x（峰在切点处水平相切）
    # 左半圆通过 (0, eyl) 和 (apex_x, apex_y)，圆心 (apex_x, cy_left)
    # 右半圆通过 (apex_x, apex_y) 和 (WIDTH, eyr)，圆心 (apex_x, cy_right)
    # 峰真正落在 apex_x，且左右两段在峰处自动相切（半径向量都竖直 → 切线都水平）
    _half_w = float(WIDTH)
    _ax = float(apex_x)
    _ax2 = _ax * _ax
    _eyl2 = eyl * eyl
    _eyr2 = eyr * eyr
    _apex_y2 = apex_y * apex_y

    # 左圆 (cx=apex_x)：当 apex_y ≈ eyl 时退化为水平直线
    if abs(apex_y - eyl) < 0.5:
        cy_left = eyl
        r_left = _ax
    else:
        cy_left = (_ax2 + _eyl2 - _apex_y2) / (2.0 * (eyl - apex_y))
        r_left = abs(apex_y - cy_left)
    # 右圆 (cx=apex_x, 弦 (apex_x, apex_y)-(WIDTH, eyr))
    if abs(apex_y - eyr) < 0.5:
        cy_right = eyr
        r_right = abs(_half_w - _ax)
    else:
        cy_right = (_eyr2 + (_half_w - _ax) ** 2 - _apex_y2) / (2.0 * (eyr - apex_y))
        r_right = abs(apex_y - cy_right)

    is_cup = apex_y < min(eyl, eyr)
    pts = []
    for x in range(0, WIDTH + 1):
        if x <= _ax:
            dx = x - _ax
            r2 = r_left * r_left
            if dx * dx <= r2:
                sqrt_term = math.sqrt(r2 - dx * dx)
                y = cy_left - sqrt_term if is_cup else cy_left + sqrt_term
                pts.append((x, int(round(y))))
            else:
                pts.append((x, int(eyl)))
        else:
            dx = x - _ax
            r2 = r_right * r_right
            if dx * dx <= r2:
                sqrt_term = math.sqrt(r2 - dx * dx)
                y = cy_right - sqrt_term if is_cup else cy_right + sqrt_term
                pts.append((x, int(round(y))))
            else:
                pts.append((x, int(eyr)))
    return pts


def _apply_upper_flatten(
    pts: list[tuple[int, int]],
    flatten: float,
    eyelid_clamped: float,
) -> list[tuple[int, int]]:
    """上眼睑展平：从弧线向直线（y=END_Y）插值。flatten=0 或 eyelid=0 都不动。

    与原 draw_eye 等价条件：`flatten > 0 and eyelid_clamped > 0`。
    """
    if flatten <= 0 or eyelid_clamped <= 0:
        return pts
    out = []
    for x, y in pts:
        y_flat = y * (1.0 - flatten) + END_Y * flatten
        out.append((x, int(round(y_flat))))
    return out


def _apply_lid_tilt(
    pts: list[tuple[int, int]],
    tilt: float,
    eyelid_clamped: float,
    side: str = 'both',
) -> list[tuple[int, int]]:
    """上下眼睑倾斜：左右两端相对中心线垂直偏移 tilt。

    side 参数控制倾斜作用范围（默认 `both` 保持向后兼容）：
      - `both`（默认）：左/右两端对称倾斜
        tilt > 0 → 左眼角上挑 / 右眼角下垂（anger 系）
        tilt < 0 → 左眼角下垂 / 右眼角上挑（cry/sadness 系）
      - `right`：只影响右半边（x >= WIDTH/2），左侧保持原位
        tilt < 0 → 仅右角上抬
      - `left`：只影响左半边（x <= WIDTH/2），右侧保持原位
    与原 draw_eye 等价条件：`tilt != 0 and eyelid_clamped > 0`。
    """
    if tilt == 0 or eyelid_clamped <= 0:
        return pts
    out = []
    for x, y in pts:
        tf = (x - WIDTH / 2.0) / (WIDTH / 2.0)
        if side == 'right' and tf < 0:
            out.append((x, y))
            continue
        if side == 'left' and tf > 0:
            out.append((x, y))
            continue
        y_adj = tf * tilt
        out.append((x, int(round(y + y_adj))))
    return out


# ============================================================
# 复合入口：draw_eye() 一行调用完成所有几何
# ============================================================

class LidArcs(NamedTuple):
    """眼睑几何预计算结果，供 draw_eye() 直接消费。"""
    upper_pts: list[tuple[int, int]]
    lower_pts: list[tuple[int, int]]
    upper_apex_y: float
    lower_apex_y: float


def compute_lid_arcs(
    eyelid: float,
    *,
    eyelid_tilt: float = 0,
    eyelid_tilt_side: str = 'both',
    eyelid_flatten: float = 0,
    eyelid_close_y: float | None = None,
    eyelid_target_y: float | None = None,
    lower_eyelid_cup: bool = False,
    lower_eyelid_tilt: float = 0,
    endpoint_y: float = END_Y,
    upper_apex_x: float = WIDTH / 2.0,
    lower_apex_x: float = WIDTH / 2.0,
    lower_endpoint_y_left: float | None = None,
    lower_endpoint_y_right: float | None = None,
) -> LidArcs:
    """完整眼睑几何：从 eyelid 参数到上下弧线点列与顶点。

    参数语义与 draw_eye() 完全一致；返回值供 draw_eye() 直接绘制。
    """
    eyelid_clamped = max(0.0, min(EYELID_MAX, eyelid))
    close_y = float(eyelid_close_y) if eyelid_close_y is not None else CLOSE_Y

    upper_apex_y, lower_apex_y = lid_apex_positions(
        eyelid_clamped,
        close_y=close_y,
        target_y=eyelid_target_y,
        lower_eyelid_cup=lower_eyelid_cup,
    )

    upper_pts = _circle_arc_pts(upper_apex_y, endpoint_y, upper_apex_x)
    upper_pts = _apply_upper_flatten(upper_pts, eyelid_flatten, eyelid_clamped)
    upper_pts = _apply_lid_tilt(upper_pts, eyelid_tilt, eyelid_clamped, eyelid_tilt_side)

    leyl = endpoint_y if lower_endpoint_y_left is None else float(lower_endpoint_y_left)
    leyr = endpoint_y if lower_endpoint_y_right is None else float(lower_endpoint_y_right)
    lower_pts = _circle_arc_pts(lower_apex_y, endpoint_y, lower_apex_x, leyl, leyr)
    lower_pts = _apply_lid_tilt(lower_pts, lower_eyelid_tilt, eyelid_clamped)

    return LidArcs(
        upper_pts=upper_pts,
        lower_pts=lower_pts,
        upper_apex_y=upper_apex_y,
        lower_apex_y=lower_apex_y,
    )
