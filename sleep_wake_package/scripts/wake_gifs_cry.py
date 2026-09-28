#SLEEP_BLINK_CYCLE2_END_Y = 130.0  # cycle 2 end y (was 115 = peak, made cycle 2 second half a no-op)   # second blink END target_y (returns only 5 units from peak 115, less than cycle1)!/usr/bin/env python3
"""WAKE GIF generator (single + dual).

Animates each eye style with a sleepy/drowsy expression:
  - Upper eyelid: starts U-shaped from top, transitions to cap-shaped
  - Lower eyelid: stays cap-shaped, moves from bottom up to lower 1/3
  - Gaze: moves rightward with slight upward roll (side-eye dismissive look)

Usage:
    cd /path/to/doll-eye-styles
    python tools/run_cry_branch.py sleep
    python tools/run_cry_branch.py sleep --duration 5.0 --fps 25
    python tools/run_cry_branch.py sleep --styles p1_jingdian p2_qiqi

Output:
    outputs/eye_gifs_new/sleep/{style_name}_wake.gif
    outputs/eye_gifs_new/sleep/{style_name}_wake_dual.gif
"""

import argparse
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image

from eye_render import WIDTH, HEIGHT, draw_eye
from eye_styles import list_styles, get_style

TARGET_FRAME_TIME = 0.04  # 25 fps

GAZE_JITTER_AMP = 0.005
GLINT_JITTER_AMP = 0.8
BREATH_FREQ = 0.0007
BREATH_AMP = 0.002

# Wake-specific SOFTEN sub-phase constants (v13 state)
PRE_END = 0.5
HOLD_END = 0.5      # = PRE_END, so HOLD branch is skipped (no pause)
POST_EYELID = 15
POST_TARGET_Y = 80.0
OUTPUT_DIR = Path(__file__).parent.parent / "outputs" / "eye_gifs_new" / "wake_transition final"
SLEEP_CLOSE_Y = 147.0   # lower-lid closed position (apex_y=148.08). BLINK close_y gets dynamically lowered by blink_factor
SLEEP_UPPER_TARGET = 117.0
SLEEP_SOFTEN_DUR       = 2.5  # SOFTEN with 1.0s POST       # 软化阶段时长 (整体放慢, 2.0s)
SLEEP_BLINK_DUR        = 11.0      # BLINK 总时长 (cycle1=5s + cycle2=6s, 第二次更慢)
SLEEP_BLINK_CYCLE1_DUR = 5.0       # 第一次眨眼时长 (5s)
SLEEP_BLINK_CYCLE1_TARGET_Y = 100.0   # 第一次眨眼峰值 (更浅，apex_y=91.67；比 cycle2 的 115 浅约 14 px)

SLEEP_BLINK_CYCLE1_END_Y = 92.0    # first blink END target_y (not fully return; apex_y=84.33)
SLEEP_BLINK_CYCLE2_END_Y = 130.0  # cycle 2 end y (was 115 = peak, made cycle 2 second half a no-op) target_y (returns 5 units from peak 115; less than cycle1)
SLEEP_BLINK_END         = 13.0      # 眨眼结束 (2.0 + 11.0)
SLEEP_HOLD_END          = 14.0      # hold 软闭结束
SLEEP_FULLY_CLOSED_END  = 13.5      # FULLY_CLOSED pause removed; TRANSITION ends at FULLY state  # TRANSITION+FULLY_CLOSED sped up      # 完全闭合渐变结束
SLEEP_UPPER_TARGET_FULLY = 131.0   # 完全闭合 target_y (apex_y=120，上下隍几凸碰面)
SLEEP_BLINK_TARGET_Y  = 115.0    # 眨眼峰值 (上睑 target_y, eyelid=110 时 apex y=105)
SLEEP_STAY_END         = 10.0      # 软化+眨眼后停留到 10.0s (4 + 1.5 + 停留 4.5s)
SLEEP_CLOSE_DUR        = 3.0       # 闭眼阶段时长 (4.3s→7.3s)
SLEEP_LINE_END         = 21.0      # line 起点 = hold 终点, 最后一帧 = 完全闭合
SLEEP_UPPER_TARGET_SOFT = 115.0  # SOFTEN trajectory 117 -> 115    # apex -> 110*84/120 = 77.0 (CONVEX UP since 77 < 80 corners, ∩ shape, ~4 px above corners)
SLEEP_UPPER_TARGET_LINE = 131.0   # apex -> 110*131/120 = 120 (meets lower apex y=120, form a line)
SLEEP_STAY_END         = 2.3       # soft 后停留到 2.3s
SLEEP_CLOSE_DUR        = 3.0       # 闭眼阶段时长
SLEEP_LINE_END         = 11.0      # hold+重合后停留到 11.0s
SLEEP_OPEN_END         = 8.5       # 睁眼到 8.5s
SLEEP_END              = 9.0       # 静止到 9.0s
SLEEP_MAX_EYELID       = 110     # stays below skip threshold (115) so iris is still drawn
SLEEP_GAZE_Y           = -0.40   # iris drifts UPWARD (back to "up" step before "down 4 px")
SLEEP_CONVERGE_X       = 0.15    # mild horizontal drift toward center (was 0.45 / ~14 px, now ~4.6 px)
SLEEP_OFFSET_Y_MAX     = 5.0     # whole eye shifts up this many pixels when fully closed


class WakeAnimator:
    """wake expression animation parameter generator.

    Timeline (5s cycle):
      0.0-0.5s   : Enter sleep
      0.5-2.0s   : Hold sleep with jitter
      3.6-3.9s   : Snap open
      3.9-5.0s   : Resting / open eye state with idle jitter
    """

    def __init__(self, duration: float = 5.0, seed: int = 42):
        self.rng = random.Random(seed)
        self.duration = duration
        self.num_frames = int(round(duration / TARGET_FRAME_TIME))

    def _ease_in_out(self, t: float) -> float:
        if t < 0.5:
            return 2.0 * t * t
        return 1.0 - (-2.0 * t + 2.0) ** 2 / 2.0

    def step(self, now: float):
        now_ms = now * 1000.0

        # gaze_y / gaze_x 直绑 eyelid 跟着眼睑（开到什么程度）/内偏走到什么程度
        # 上睑靠下多少等变化完全跟动，眼睑也不断跳
        eyelid = 0
        gaze_y = 0.0
        gaze_x = 0.0
        eyelid_target_y = None

        if now < SLEEP_SOFTEN_DUR:
            # SOFTEN: 全睁 → soft-close（下睑保持 155，eyelid 升到 110）
            # 含 pause at apex_y=10 (PRE/HOLD/POST 三段)
            PRE_END = 0.5
            START_EYELID = 0
            POST_EYELID = 15
            POST_TARGET_Y = 80
            START_TARGET_Y = 117
            SOFTEN_TOTAL = SLEEP_SOFTEN_DUR
            if now < PRE_END:
                t = now / PRE_END
                eyelid = int(START_EYELID + (POST_EYELID - START_EYELID) * t)
                eyelid_target_y = START_TARGET_Y + (POST_TARGET_Y - START_TARGET_Y) * t
            elif now < HOLD_END:
                eyelid = POST_EYELID
                eyelid_target_y = POST_TARGET_Y
            else:
                t = (now - HOLD_END) / (SOFTEN_TOTAL - HOLD_END)
                eyelid = int(POST_EYELID + (SLEEP_MAX_EYELID - POST_EYELID) * t)
                eyelid_target_y = POST_TARGET_Y  # stays at 80
            gaze_x = 0
            gaze_y = 0
            eye_offset_y = SLEEP_OFFSET_Y_MAX * (eyelid / SLEEP_MAX_EYELID)
        elif now < SLEEP_BLINK_END:
            # BLINK: cycle1 only, cycle2 reformulated to linear transition
            blink_t = (now - SLEEP_SOFTEN_DUR) / SLEEP_BLINK_DUR
            cycle1_ratio = SLEEP_BLINK_CYCLE1_DUR / SLEEP_BLINK_DUR
            if blink_t < cycle1_ratio:
                # Cycle 1 (5s): 115 -> peak 100 -> 92
                phase = blink_t / cycle1_ratio
                peak_y = SLEEP_BLINK_CYCLE1_TARGET_Y
                cycle_start_y = SLEEP_UPPER_TARGET_SOFT  # 115
                cycle_end_y = SLEEP_BLINK_CYCLE1_END_Y  # 92
            else:
                # Cycle 2 reformulated: linear 92 -> 115
                phase = (blink_t - cycle1_ratio) / (1.0 - cycle1_ratio)
                peak_y = SLEEP_UPPER_TARGET_SOFT  # 115
                cycle_start_y = SLEEP_BLINK_CYCLE1_END_Y  # 92
                cycle_end_y = SLEEP_BLINK_CYCLE2_END_Y  # 115
            if phase < 0.5:
                bt = phase * 2
                eased = self._ease_in_out(min(1.0, max(0.0, bt)))
                eyelid_target_y = cycle_start_y + (peak_y - cycle_start_y) * eased
            else:
                bt = (phase - 0.5) * 2
                eased = self._ease_in_out(min(1.0, max(0.0, bt)))
                eyelid_target_y = peak_y + (cycle_end_y - peak_y) * eased
            eyelid = SLEEP_MAX_EYELID
        elif now < SLEEP_FULLY_CLOSED_END:
            # TRANSITION: 115 -> 131 (fast)
            t = (now - SLEEP_BLINK_END) / (SLEEP_FULLY_CLOSED_END - SLEEP_BLINK_END)
            eased = self._ease_in_out(min(1.0, max(0.0, t)))
            eyelid = SLEEP_MAX_EYELID
            eyelid_target_y = SLEEP_BLINK_CYCLE2_END_Y + (SLEEP_UPPER_TARGET_FULLY - SLEEP_BLINK_CYCLE2_END_Y) * eased
        else:
            # Fully closed (1.5s-16s)
            eyelid = SLEEP_MAX_EYELID
            eyelid_target_y = SLEEP_UPPER_TARGET_FULLY
            gaze_x = SLEEP_CONVERGE_X
            gaze_y = SLEEP_GAZE_Y
            eye_offset_y = SLEEP_OFFSET_Y_MAX

        # gaze_factor / slow_factor (same as before)
        if SLEEP_MAX_EYELID > 0:
            e_ratio = eyelid / SLEEP_MAX_EYELID
        else:
            e_ratio = 0.0
        extra = 0.0
        if eyelid_target_y is not None and eyelid_target_y > SLEEP_BLINK_TARGET_Y:
            extra = (eyelid_target_y - SLEEP_BLINK_TARGET_Y) / (SLEEP_UPPER_TARGET_FULLY - SLEEP_BLINK_TARGET_Y) * 1.5
        gaze_factor = e_ratio + extra
        gaze_y = SLEEP_GAZE_Y * gaze_factor
        gaze_x = SLEEP_CONVERGE_X * gaze_factor

        if eyelid_target_y is not None and eyelid_target_y > SLEEP_UPPER_TARGET_SOFT:
            close_progress = (eyelid_target_y - SLEEP_UPPER_TARGET_SOFT) / (SLEEP_UPPER_TARGET_FULLY - SLEEP_UPPER_TARGET_SOFT)
            slow_factor = max(0.0, 1.0 - close_progress)
        else:
            slow_factor = 1.0

        jitter_x = (math.sin(now_ms * 0.012 * slow_factor) + math.cos(now_ms * 0.027 * slow_factor)) * GAZE_JITTER_AMP
        jitter_y = (math.cos(now_ms * 0.015 * slow_factor) + math.sin(now_ms * 0.023 * slow_factor)) * GAZE_JITTER_AMP
        render_x = max(-1.0, min(1.0, gaze_x + jitter_x))
        render_y = max(-1.0, min(1.0, gaze_y + jitter_y))

        glint_jit_x = (math.sin(now_ms * 0.3 * slow_factor) + self.rng.random() * 0.5 - 0.25) * GLINT_JITTER_AMP
        glint_jit_y = (math.cos(now_ms * 0.35 * slow_factor) + self.rng.random() * 0.5 - 0.25) * GLINT_JITTER_AMP

        breath = math.sin(now_ms * BREATH_FREQ) * BREATH_AMP

        # Lower lid rises with how closed the eye is
        # Refs: target_y=80 (SOFTEN end, motion=0) -> target_y=131 (FULLY, motion=25)
        # Lower lid motion: eyelid-based (0->5px during SOFTEN) + target_y-based (0->20px during TRANSITION/FULLY)
        eyelid_motion = (eyelid / SLEEP_MAX_EYELID) * 5.0
        target_y_motion = 0.0
        if eyelid_target_y is not None:
            _p = (eyelid_target_y - 80.0) / 51.0
            if _p < 0.0: _p = 0.0
            elif _p > 1.0: _p = 1.0
            target_y_motion = _p * 20.0
        lower_lid_motion = eyelid_motion + target_y_motion

        return {
            "gaze_x": render_x,
            "gaze_y": render_y,
            "eyelid": eyelid,
            "eyelid_target_y": eyelid_target_y,
            "pupil_relative_scale": 1.0 + breath,
            "glint_jitter_x": glint_jit_x,
            "glint_jitter_y": glint_jit_y,
            "is_idle": True,
            "lower_lid_motion": lower_lid_motion,
        }


def _draw_eye_frame(style, params: dict, mirror: bool = False) -> Image.Image:
    return draw_eye(
        gaze_x=params["gaze_x"],
        gaze_y=params["gaze_y"],
        eyelid=params["eyelid"],
        eyelid_target_y=params.get("eyelid_target_y"),
        show_lower_eyelid=True,
        # BLINK 时下睑跟着上睑上眨一点 (<=3 px)
        eyelid_close_y=SLEEP_CLOSE_Y - params.get("lower_lid_motion", 0.0),
        mirror=mirror,
        pupil_relative_scale=params["pupil_relative_scale"],
        glint_jitter_x=params.get("glint_jitter_x", 0.0),
        glint_jitter_y=params.get("glint_jitter_y", 0.0),
        vergence_x=0,
        is_idle=params.get("is_idle", True),
        style=style,
    )


def render_wake_gif(style, duration: float = 5.0, seed: int = 42):
    animator = WakeAnimator(seed=seed, duration=duration)
    frames = []
    for i in range(animator.num_frames):
        now = i * TARGET_FRAME_TIME
        params = animator.step(now)
        frames.append(_draw_eye_frame(style, params, mirror=False))
    return frames


def render_wake_gif_dual(style, duration: float = 5.0, seed: int = 42, gap: int = 20):
    animator = WakeAnimator(seed=seed, duration=duration)
    dual_w = WIDTH * 2 + gap
    dual_h = HEIGHT
    frames = []
    for i in range(animator.num_frames):
        now = i * TARGET_FRAME_TIME
        params = animator.step(now)
        left = _draw_eye_frame(style, params, mirror=False)
        right = _draw_eye_frame(style, params, mirror=True)
        canvas = Image.new("RGBA", (dual_w, dual_h), (0, 0, 0, 0))
        canvas.paste(left, (0, 0), left.split()[3])
        canvas.paste(right, (WIDTH + gap, 0), right.split()[3])
        frames.append(canvas)
    return frames


def composite_for_gif(rgba_frame: Image.Image, bg_color=(250, 250, 250)) -> Image.Image:
    bg = Image.new("RGBA", rgba_frame.size, (*bg_color, 255))
    composite = Image.alpha_composite(bg, rgba_frame)
    return composite.convert("RGB")


def save_gif(frames, path: Path, fps: int = 25, optimize: bool = True):
    rgb_frames = [composite_for_gif(f) for f in frames]
    duration_ms = int(round(1000.0 / fps))

    first = rgb_frames[0]
    palette_img = first.quantize(colors=128, method=Image.Quantize.MEDIANCUT)

    quantized = []
    for img in rgb_frames:
        q = img.quantize(
            colors=128,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
            palette=palette_img,
        )
        quantized.append(q)

    quantized[0].save(
        path,
        save_all=True,
        append_images=quantized[1:],
        duration=duration_ms,
        loop=0,
        optimize=optimize,
    )


def build_index_html(out_dir: Path, style_names, dual: bool = True):
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Eye Styles WAKE GIF Gallery</title>
<style>
body { font-family: sans-serif; background: #2a2a2a; margin: 20px; color: #ddd; }
h1 { font-size: 1.6rem; color: #9b9b9b; }
h2 { font-size: 1.2rem; margin-top: 32px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }
.grid-wide { grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); }
.card { background: #3a3a3a; border-radius: 8px; padding: 12px; text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.card img { border: 1px solid #555; border-radius: 50%; background: #fafafa; }
.card img.single { width: 160px; height: 160px; }
.card img.dual { width: 320px; height: 160px; border-radius: 80px; }
.card h3 { margin: 10px 0 4px; font-size: 0.95rem; }
.card p { margin: 0; font-size: 0.8rem; color: #999; }
</style>
</head>
<body>
<h1>Wake Animation Gallery</h1>
<p>Total {count} styles; each shows a 5-second wake expression.</p>
<h2>Single Eye</h2>
<div class="grid">
"""
    html = html.replace("{count}", str(len(style_names)))
    for name in style_names:
        gif = f"{name}_wake.gif"
        html += f"""  <div class="card">
    <img class="single" src="{gif}" alt="{name} wake">
    <h3>{name}</h3>
    <p>{gif}</p>
  </div>
"""
    html += """</div>
"""

    if dual:
        html += """<h2>Dual Eye</h2>
<div class="grid grid-wide">
"""
        for name in style_names:
            gif = f"{name}_wake_dual.gif"
            html += f"""  <div class="card">
    <img class="dual" src="{gif}" alt="{name} wake dual">
    <h3>{name}</h3>
    <p>{gif}</p>
  </div>
"""
        html += """</div>
"""

    html += """</body>
</html>
"""
    (out_dir / "wake_index.html").write_text(html, encoding="utf-8")
    print(f"[OK] Gallery: {out_dir / 'wake_index.html'}")


def parse_args():
    available = ", ".join(name for name, _ in list_styles())
    parser = argparse.ArgumentParser(
        description="Generate wake GIF animations for each eye style (single + dual).",
    )
    parser.add_argument(
        "--duration", type=float, default=5.0,
        help="GIF duration in seconds (default 5.0)",
    )
    parser.add_argument(
        "--fps", type=int, default=25,
        help="Frames per second (default 25)",
    )
    parser.add_argument(
        "--seed", type=int, default=2024,
        help="Animation random seed (default 2024)",
    )
    parser.add_argument(
        "--styles", nargs="+", default=None,
        help=f"Style names to generate (default: all). Available: {available}",
    )
    parser.add_argument(
        "--mode", choices=["single", "dual", "both"], default="both",
        help="Generation mode (default both)",
    )
    parser.add_argument(
        "--dual-gap", type=int, default=20,
        help="Gap between eyes in dual version (pixels, default 20)",
    )
    parser.add_argument(
        "--output-dir", type=str, default=str(OUTPUT_DIR),
        help=f"Output directory (default {OUTPUT_DIR})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.styles:
        styles = [(name, get_style(name)["description"]) for name in args.styles]
    else:
        styles = list_styles()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gen_single = args.mode in ("single", "both")
    gen_dual = args.mode in ("dual", "both")

    style_names = []
    for idx, (name, desc) in enumerate(styles):
        style = get_style(name)
        seed = args.seed + idx

        if gen_single:
            frames = render_wake_gif(style, duration=args.duration, seed=seed)
            gif_path = out_dir / f"{name}_wake.gif"
            save_gif(frames, gif_path, fps=args.fps)
            print(f"[OK] {name} single sleep: {gif_path} ({len(frames)} frames, seed={seed})")

        if gen_dual:
            frames_dual = render_wake_gif_dual(
                style, duration=args.duration, seed=seed, gap=args.dual_gap
            )
            gif_path_dual = out_dir / f"{name}_wake_dual.gif"
            save_gif(frames_dual, gif_path_dual, fps=args.fps)
            print(f"[OK] {name} dual sleep: {gif_path_dual} ({len(frames_dual)} frames, seed={seed})")

        style_names.append(name)

    build_index_html(out_dir, style_names, dual=gen_dual)


if __name__ == "__main__":
    main()

 






