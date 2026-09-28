#!/usr/bin/env python3
"""
单样式指定姿态预览 — 给样式调参用的轻量 CLI。

用法示例：
    python3 tools/preview.py --style pi2_light_blue
    python3 tools/preview.py --style p2_liuliu --gaze -0.4 0.3 --eyelid 60 --pupil-scale 1.08
    python3 tools/preview.py --style lovot_lianlian --idle --output outputs/preview.png
    python3 tools/preview.py --style p2_qiqi --mirror --output outputs/preview_right.png

参数：
    --style        样式名（必须存在于 EYE_STYLES），默认 pi2_light_blue
    --gaze X Y     视线方向 -1 ~ 1，默认 0 0
    --eyelid       上眼睑遮挡像素高度，0~120，默认 0
    --pupil-scale  瞳孔相对缩放，默认 1.0
    --idle         IDLE 模式（瞳孔更糊），默认关
    --mirror       水平镜像（右眼），默认关
    --output       输出 PNG 路径，默认 outputs/preview.png

依赖：与主测试一致，仅 Pillow。
"""

import argparse
import os
import sys
from pathlib import Path

# 让 tools/ 之外的导入能找到同级的 eye_styles / eye_render
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image

from eye_styles import get_style, list_styles
from eye_render import draw_eye


def parse_args():
    available = ", ".join(name for name, _ in list_styles())
    parser = argparse.ArgumentParser(
        description="渲染单个眼睛样式的指定姿态到 PNG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--style", default="pi2_light_blue",
        help=f"样式名（可用: {available}）",
    )
    parser.add_argument(
        "--gaze", nargs=2, type=float, default=[0.0, 0.0],
        metavar=("X", "Y"), help="视线方向 -1 ~ 1（默认 0 0）",
    )
    parser.add_argument(
        "--eyelid", type=int, default=0,
        help="上眼睑遮挡像素高度 0~120（默认 0）",
    )
    parser.add_argument(
        "--pupil-scale", type=float, default=1.0,
        help="瞳孔相对缩放（默认 1.0）",
    )
    parser.add_argument(
        "--idle", action="store_true",
        help="IDLE 模式：瞳孔更糊",
    )
    parser.add_argument(
        "--mirror", action="store_true",
        help="水平镜像（右眼）",
    )
    parser.add_argument(
        "--output", default="outputs/preview.png",
        help="输出 PNG 路径（默认 outputs/preview.png）",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    style = get_style(args.style)
    gx, gy = args.gaze
    # 视线方向夹紧
    gx = max(-1.0, min(1.0, gx))
    gy = max(-1.0, min(1.0, gy))
    eyelid = max(0, min(120, args.eyelid))

    img = draw_eye(
        gaze_x=gx,
        gaze_y=gy,
        eyelid=eyelid,
        mirror=args.mirror,
        pupil_relative_scale=args.pupil_scale,
        is_idle=args.idle,
        style=style,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")

    print(f"[OK] {args.style} → {out_path}")
    print(f"     gaze=({gx:+.2f}, {gy:+.2f}) eyelid={eyelid} "
          f"pupil_scale={args.pupil_scale} "
          f"idle={args.idle} mirror={args.mirror}")


if __name__ == "__main__":
    main()