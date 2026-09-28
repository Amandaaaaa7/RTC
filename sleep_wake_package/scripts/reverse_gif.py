#!/usr/bin/env python3
"""Reverse GIF frame order from one folder to another, with optional jitter hold."""

import argparse
import importlib.util
import math
import os
import random
import sys
from pathlib import Path
from PIL import Image


_CRY_DIR = Path(__file__).resolve().parent


def _load_cry_module(name: str):
    p = _CRY_DIR / name
    spec = importlib.util.spec_from_file_location(f"_cry_{name[:-3]}", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cry_render = _cry_styles = None


def _ensure_cry_modules():
    global _cry_render, _cry_styles
    if _cry_render is None:
        _cry_render = _load_cry_module("eye_render.py")
        _cry_styles = _load_cry_module("eye_styles.py")
        sys.modules["eye_render"] = _cry_render
        sys.modules["eye_styles"] = _cry_styles
    return _cry_render, _cry_styles


def _composite(rgba: Image.Image, bg=(250, 250, 250)) -> Image.Image:
    """Composite RGBA frame onto light background (matches sleep_gifs_cry)."""
    canvas = Image.new("RGBA", rgba.size, (*bg, 255))
    out = Image.alpha_composite(canvas, rgba)
    return out.convert("RGB")


def reverse_one(
    src_gif: Path,
    dst_gif: Path,
    first_pause_ms: int = 0,
    last_pause_ms: int = 0,
    tail_source_frame_idx: int = -1,
    jitter_tail: bool = False,
    jitter_seed: int = 1234,
) -> None:
    """Reverse a GIF. Optionally:
      * override first-frame duration (start pause)
      * override last-frame duration (end pause)
      * truncate source before reversing so wake ends at source frame tail_source_frame_idx
      * jitter_tail: re-render tail hold using draw_eye with continuous pupil/iris/glint jitter
    """
    im = Image.open(src_gif)
    frames = []
    durations = []
    default_duration = im.info.get("duration", 40) or 40
    try:
        while True:
            frames.append(im.convert("RGB").copy())
            d = im.info.get("duration", 0)
            durations.append(d if d else default_duration)
            im.seek(im.tell() + 1)
    except EOFError:
        pass
    if not frames:
        return

    if 0 <= tail_source_frame_idx < len(frames):
        frames = frames[tail_source_frame_idx:]
        durations = durations[tail_source_frame_idx:]

    frames.reverse()
    durations.reverse()

    if first_pause_ms > 0 and durations:
        durations[0] = first_pause_ms

    if jitter_tail:
        stem = src_gif.stem
        for suf in ("_sleep_dual", "_sleep"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        style_name = stem

        _ensure_cry_modules()
        style = _cry_styles.get_style(style_name)

        if tail_source_frame_idx <= 0:
            base_params = dict(
                gaze_x=0.0, gaze_y=0.0, eyelid=0,
                eyelid_target_y=117.0, show_lower_eyelid=True,
                eyelid_close_y=147, mirror=False, pupil_relative_scale=1.0,
                vergence_x=0, is_idle=True, style=style,
            )
        else:
            base_params = dict(
                gaze_x=0.02, gaze_y=-0.05, eyelid=14,
                eyelid_target_y=112.5, show_lower_eyelid=True,
                eyelid_close_y=147, mirror=False, pupil_relative_scale=1.0,
                vergence_x=0, is_idle=True, style=style,
            )

        rng = random.Random(jitter_seed + (hash(style_name) % 10000))
        n_jitter = max(1, last_pause_ms // 40)
        jitter_frames = []
        for k in range(n_jitter):
            t = k / max(1, n_jitter - 1) if n_jitter > 1 else 0.0
            jx = math.sin(t * math.tau * 1.7) * 0.05 + (rng.random() - 0.5) * 0.04
            jy = math.cos(t * math.tau * 2.3) * 0.05 + (rng.random() - 0.5) * 0.04
            gx = math.sin(t * math.tau * 1.1) * 0.025 + (rng.random() - 0.5) * 0.02
            gy = math.cos(t * math.tau * 1.7) * 0.04 + (rng.random() - 0.5) * 0.03
            breath = math.sin(t * math.tau * 0.5) * 0.005
            params = dict(base_params)
            params["gaze_x"] = base_params["gaze_x"] + gx
            params["gaze_y"] = base_params["gaze_y"] + gy
            params["glint_jitter_x"] = jx
            params["glint_jitter_y"] = jy
            params["pupil_relative_scale"] = 1.0 + breath
            frame_rgba = _cry_render.draw_eye(**params)
            jitter_frames.append(_composite(frame_rgba))

        if jitter_frames:
            frames = frames[:-1] + jitter_frames
            durations = durations[:-1] + [40] * len(jitter_frames)
    else:
        if last_pause_ms > 0 and durations:
            durations[-1] = last_pause_ms

    frames[0].save(
        dst_gif,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="source folder (e.g. sleep_transition final)")
    ap.add_argument("--dst", required=True, help="destination folder (e.g. wake)")
    ap.add_argument("--suffix", default="", help="optional suffix in destination filenames")
    ap.add_argument("--duration", type=float, default=20.0, help="duration in seconds (for logging only)")
    ap.add_argument("--first-pause-ms", type=int, default=0,
                    help="override first-frame duration (ms); wake starts with eye fully closed")
    ap.add_argument("--last-pause-ms", type=int, default=2000,
                    help="total duration of jittered tail hold (ms) when --jitter-tail is set")
    ap.add_argument("--tail-source-frame-idx", type=int, default=-1,
                    help="truncate source frames BEFORE reversing, so wake ends at this source frame index")
    ap.add_argument("--jitter-tail", action="store_true",
                    help="re-render tail hold with continuous pupil/iris/glint jitter using draw_eye")
    ap.add_argument("--jitter-seed", type=int, default=1234)
    args = ap.parse_args()
    src = Path(args.src)
    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for src_gif in sorted(src.glob("*.gif")):
        if args.suffix:
            stem = src_gif.stem
            if stem.endswith("_sleep"):
                new_stem = stem[: -len("_sleep")] + args.suffix
            else:
                new_stem = stem + args.suffix
            dst_gif = dst / (new_stem + ".gif")
        else:
            dst_gif = dst / src_gif.name
        reverse_one(
            src_gif, dst_gif,
            first_pause_ms=args.first_pause_ms,
            last_pause_ms=args.last_pause_ms,
            tail_source_frame_idx=args.tail_source_frame_idx,
            jitter_tail=args.jitter_tail,
            jitter_seed=args.jitter_seed,
        )
        n += 1
    print(f"reversed {n} GIFs from {src} to {dst}")


if __name__ == "__main__":
    main()
