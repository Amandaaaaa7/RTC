#!/usr/bin/env python3
"""
eye-emotion-engine 输出评审 GIF 的本地浏览/嵌入工具。

把 `eye-emotion-engine/outputs/review/<时间戳>_<tag>/` 下的评审产物（GIF +
VERSION.md + 静态 PNG）通过**软链接**的方式暴露到
`pi_affe_sys/outputs/reviews/<tag>/`，并生成一个本地 `index.html` 用浏览器
一次性查看所有 GIF。同时为 `tools/eye_style_gallery.py` 提供 GIF 列表的 JSON
接口（`outputs/reviews/_gallery.json`），在 Pi 上能把这些评审 GIF 直接循环
播到屏幕端预览。

设计原则：
  1. **软链不拷贝**——eye-emotion-engine 是上游仓库，每次跑 build 后本工具
     只需刷新链接即可查看最新 GIF，git 不入库。
  2. **单一入口**——所有版本都能在同一 index.html 切换查看，按版本文件夹分组。
  3. **JSON 接口**——给 eye_style_gallery.py 提供稳定的 list 路径，避免硬编码
     eye-emotion-engine 路径。

用法（在 pi_affe_sys 仓根目录）:
    python3 tools/review_browser.py --setup         # 创建软链 + 生成 index.html + JSON
    python3 tools/review_browser.py --setup --open  # 完事自动打开浏览器
    python3 tools/review_browser.py --list          # 打印所有可用版本 + GIF 列表
    python3 tools/review_browser.py --review 20260712-142022_gaze-tuned-r2-painsadgaze-amusearc
                                       # 只同步某一个版本
"""

import argparse
import json
import os
import shutil
import sys
import textwrap
import webbrowser
from pathlib import Path

# ----------------------------------------------------------
# 路径解析：eye-emotion-engine 是同级仓库
# ----------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent  # pi_affe_sys/
PEER_REVIEW_DIR_NAME = "eye-emotion-engine"
SYMLINK_ROOT = ROOT / "outputs" / "reviews"


def _resolve_engine_dir() -> Path:
    """定位同级 eye-emotion-engine 仓库。"""
    for parent in (ROOT.parent, ROOT.parent.parent):
        candidate = parent / PEER_REVIEW_DIR_NAME
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"找不到同级仓库 '{PEER_REVIEW_DIR_NAME}'，请确认布局：\n"
        f"  codes/\n"
        f"  ├─ {ROOT.name}/\n"
        f"  └─ {PEER_REVIEW_DIR_NAME}/"
    )


def _list_versions(engine_dir: Path) -> list[Path]:
    """列出 eye-emotion-engine/outputs/review/ 下所有版本目录（按时间倒序）。

    只挑根目录直接子项里有 VERSION.md 的：gaze-tuning/ 是按情绪分组的容器，
    不算独立版本。
    """
    review_dir = engine_dir / "outputs" / "review"
    if not review_dir.is_dir():
        return []
    versions = sorted(
        [p for p in review_dir.iterdir()
         if p.is_dir() and (p / "VERSION.md").is_file()],
        key=lambda p: p.name,  # 时间戳前缀，确保时间倒序
        reverse=True,
    )
    return versions


def _safe_relpath(src: Path, base: Path) -> Path:
    """src 相对 base 的路径，统一用正斜杠用于 HTML。"""
    return Path(os.path.relpath(src, base))


# ----------------------------------------------------------
# 软链
# ----------------------------------------------------------
def _link_version(version_dir: Path, target_root: Path) -> int:
    """把某个版本目录的产物软链到 target_root/<version_name>/，返回文件数。"""
    name = version_dir.name
    link_dir = target_root / name
    link_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(version_dir.iterdir()):
        if src.name == "VERSION.md" or src.suffix.lower() in (".gif", ".png", ".md"):
            dst = link_dir / src.name
            if dst.is_symlink() or dst.exists():
                if dst.is_symlink() or dst.is_file():
                    dst.unlink()
            os.symlink(src, dst)
            count += 1
    return count


# ----------------------------------------------------------
# index.html 生成
# ----------------------------------------------------------
_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Eye Emotion Review Gallery</title>
<style>
  :root { --bg:#1d1f24; --card:#26292f; --line:#3a3d44; --txt:#dcdcdc; --muted:#888; }
  body { font-family: -apple-system, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--txt);
         margin: 0; padding: 24px 32px; }
  h1 { font-size: 1.4rem; margin: 0 0 6px; color: #f5e3a1; }
  .sub { color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }
  .ver { margin-bottom: 36px; padding: 16px 20px;
          border: 1px solid var(--line); border-radius: 10px;
          background: var(--card); }
  .ver h2 { margin: 0 0 4px; font-size: 1.1rem; color: #9ad6ff;
            font-family: ui-monospace, monospace; }
  .ver .meta { font-size: 0.78rem; color: var(--muted);
               margin-bottom: 12px; white-space: pre-wrap; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 16px; }
  .gif { background: #fff; border-radius: 8px; padding: 8px;
         text-align: center; }
  .gif img { max-width: 100%; height: auto; border-radius: 4px; }
  .gif .name { display: block; margin-top: 6px;
               font-size: 0.78rem; color: #555; font-family: ui-monospace, monospace;
               word-break: break-all; }
  .gif .meta { display: block; font-size: 0.7rem; color: #888; margin-top: 2px; }
  .png { background: #fff; border-radius: 8px; padding: 8px;
         text-align: center; }
  .png img { max-width: 100%; border-radius: 4px; }
  .png .name { display: block; margin-top: 6px;
               font-size: 0.78rem; color: #555; font-family: ui-monospace, monospace; }
</style>
</head>
<body>
<h1>🎬 Eye Emotion Review GIF 画廊</h1>
<p class="sub">来源：<code>{engine_dir}</code> · 共 {version_count} 个版本 · {gif_count} 个 GIF · 软链（不拷贝）</p>
{versions}
</body>
</html>
"""

_VER_TEMPLATE = """<section class="ver">
  <h2>{name}</h2>
  <pre class="meta">{meta}</pre>
  <div class="grid">{cells}</div>
</section>
"""


def _gif_meta(path: Path) -> tuple[int, int]:
    """读 GIF 的尺寸 + 帧数（用于卡片 meta）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size[0], getattr(im, "n_frames", 1)
    except Exception:
        return 0, 0


def _build_index_html(link_root: Path, engine_dir: Path, versions: list[Path]) -> Path:
    cells_per_version = []
    total_gifs = 0
    for v in versions:
        link_v = link_root / v.name
        meta_md = link_v / "VERSION.md"
        meta_text = meta_md.read_text(encoding="utf-8") if meta_md.exists() else "(无 VERSION.md)"
        cards = []
        for f in sorted(link_v.iterdir()):
            if f.suffix.lower() not in (".gif", ".png"):
                continue
            rel = _safe_relpath(f, link_root / "index.html").parent
            rel_posix = (Path(rel) / f.name).as_posix() if rel != Path(".") else f.name
            # Use forward slashes; HTML browsers accept relative posix
            rel_html = rel_posix
            if f.suffix.lower() == ".gif":
                w, n = _gif_meta(f)
                size_info = f"{w}px" if w else "?"
                meta = f"{size_info} · {n} 帧"
                cards.append(
                    f'<div class="gif"><img src="{rel_html}" alt="{f.name}">'
                    f'<span class="name">{f.name}</span>'
                    f'<span class="meta">{meta}</span></div>'
                )
                total_gifs += 1
            else:
                cards.append(
                    f'<div class="png"><img src="{rel_html}" alt="{f.name}">'
                    f'<span class="name">{f.name}</span></div>'
                )
        cells_per_version.append(_VER_TEMPLATE.format(
            name=v.name,
            meta=meta_text.replace("<", "&lt;").replace(">", "&gt;"),
            cells="".join(cards) or "<i>（空）</i>",
        ))
    html = (_INDEX_HTML
            .replace("{engine_dir}", str(engine_dir))
            .replace("{version_count}", str(len(versions)))
            .replace("{gif_count}", str(total_gifs))
            .replace("{versions}", "".join(cells_per_version)))
    out = link_root / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


# ----------------------------------------------------------
# gallery.json —— 给 eye_style_gallery.py 读
# ----------------------------------------------------------
def _build_gallery_json(link_root: Path, versions: list[Path]) -> Path:
    """生成 _gallery.json：所有版本的所有 GIF 路径（相对 pi_affe_sys/），
    让 gallery 直接 PIL.Image.open 即可。"""
    data = []
    for v in versions:
        link_v = link_root / v.name
        items = []
        for f in sorted(link_v.glob("*.gif")):
            items.append({
                "filename": f.name,
                "abs_path": str(f.resolve()),  # 用真实路径，PIL 不跟软链玩
                "rel_path": f.relative_to(ROOT).as_posix(),
            })
        data.append({
            "version": v.name,
            "version_dir": str(link_v.resolve()),
            "gifs": items,
        })
    out = link_root / "_gallery.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ----------------------------------------------------------
# 27 锚点 × 4 强度参数 dump —— 给 Pi 离线预览
# ----------------------------------------------------------
ANCHOR_INTENSITIES = [0.3, 0.6, 1.0, 1.4]


def _build_anchor_grid_json(engine_dir: Path, link_root: Path) -> Path | None:
    """用 eye-emotion-engine 仓的 EyeEmotionEngine 跑一帧 dump 参数。

    与 emotion-engine 的 review_intensity.gif 同源：pose + 动效综合后
    在某个时刻取的 params_at(t)，而不是裸的 pose_for_emotion → to_draw_kwargs
    静态目标（后者某些表情 eyelids 会塌到底画出闭眼）。
    Pi 端 gallery 启动时直接读这个 JSON，无需 import 上游。

    实现：在 subprocess 里调用 emotion-engine —— emotion_engine 内部
    `from eye_render import lid_apex_positions` 这个函数只存在于 doll-eye-styles
    的 eye_render，不能与 pi_affe_sys 的同名模块混淆。subprocess 隔离了
    sys.modules 状态，避免主进程污染。
    """
    import subprocess
    doll_root = engine_dir.parent
    doll_des = doll_root / "doll-eye-styles"
    if not (doll_root / "eye-emotion-engine" / "eye_emotions.py").is_file():
        print(f"[anchor] 找不到 {doll_root / 'eye-emotion-engine'}")
        return None
    if not doll_des.is_dir():
        print(f"[anchor] 找不到 {doll_des}")
        return None
    if not (doll_des / "eye_render.py").is_file():
        print(f"[anchor] 找不到 doll-eye-styles/eye_render.py")
        return None

    script = textwrap.dedent("""\
        import sys, json
        sys.path.insert(0, %r)        # doll-eye-styles
        sys.path.insert(0, %r)        # eye-emotion-engine
        from eye_emotions import pose_for_emotion, list_emotions, EMOTION_ANCHORS
        from emotion_engine import EmotionSpec, EyeEmotionEngine

        # pi_affe_sys 的 draw_eye 接受白名单；多余字段（tear/iris_tremor/
        # lower_lid_motion_scale）会触发 TypeError，必须丢弃。
        ACCEPT = {"gaze_x", "gaze_y", "eyelid", "mirror",
                  "pupil_relative_scale", "glint_jitter_x", "glint_jitter_y",
                  "vergence_x", "is_idle", "glint_scale", "iris_scale",
                  "eyelid_tilt", "eyelid_target_y", "eyelid_flatten",
                  "show_lower_eyelid", "eyelid_close_y",
                  "lower_eyelid_cup", "lower_eyelid_tilt", "cry_glint_data"}

        FPS = 12
        DURATION = 5.0
        NF = FPS  # 5s × 12fps = 60 帧，预 dump 给屏端节奏用；屏端按屏帧率取样
        rows = []
        for name in list_emotions():
            a = EMOTION_ANCHORS.get(name, {})
            row = {"emotion": name, "cells": []}
            for inten in (0.3, 0.6, 1.0, 1.4):
                pose = pose_for_emotion(name, intensity=inten)
                spec = EmotionSpec(name=name, pose=pose, va=tuple(a.get("va", (0.0, 0.0))),
                                   intensity=inten, duration=DURATION,
                                   motion=dict(a.get("motion", {})))
                eng = EyeEmotionEngine(spec, seed=inten * 1000)
                frames = []
                for fi in range(NF):
                    t = fi / FPS
                    kw = eng.params_at(t=t, mirror=False)
                    kw = {k: v for k, v in kw.items() if k in ACCEPT}
                    kw["mirror"] = False
                    kw["vergence_x"] = 0
                    kw["is_idle"] = False
                    frames.append({"t": round(t, 3), "params": kw})
                row["cells"].append({"intensity": inten, "frames": frames})
            rows.append(row)
        print(json.dumps({"fps": FPS, "duration": DURATION,
                          "frames_per_cell": NF, "rows": rows},
                         default=lambda x: round(x, 4) if isinstance(x, float) else x))
    """) % (str(doll_des), str(doll_root / "eye-emotion-engine"))

    try:
        proc = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"[anchor] subprocess 启动失败: {e}")
        return None
    if proc.returncode != 0:
        print(f"[anchor] subprocess 失败:\n{proc.stderr[:1500]}")
        return None
    try:
        data = json.loads(proc.stdout)
    except Exception as e:
        print(f"[anchor] 解析 subprocess 输出失败: {e}; stdout head: {proc.stdout[:300]}")
        return None
    out = data["rows"]
    path = link_root / "_anchor_grid.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[anchor] {len(out)} 锚点 × 4 强度 = "
          f"{len(out) * 4} 单元 → {path.relative_to(ROOT)}")
    return path
    path = link_root / "_anchor_grid.json"
    path.write_text(json.dumps({"intensities": ANCHOR_INTENSITIES,
                                "rows": out},
                               indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[anchor] {len(out)} 锚点 × {len(ANCHOR_INTENSITIES)} 强度 = "
          f"{len(out) * len(ANCHOR_INTENSITIES)} 单元 → {path.relative_to(ROOT)}")
    return path


# ----------------------------------------------------------
# 子命令
# ----------------------------------------------------------
def cmd_setup(only_version: str | None, open_after: bool):
    engine_dir = _resolve_engine_dir()
    versions = _list_versions(engine_dir)
    if only_version:
        versions = [v for v in versions if v.name == only_version]
        if not versions:
            raise SystemExit(f"版本 '{only_version}' 不存在；用 --list 查看")

    if not versions:
        raise SystemExit(
            f"{engine_dir}/outputs/review/ 下没有任何版本目录；"
            f"请先在 eye-emotion-engine 跑 build_emotion_review"
        )

    SYMLINK_ROOT.mkdir(parents=True, exist_ok=True)
    # 清掉上一次的旧链接目录（保留 index.html / _gallery.json）
    for entry in SYMLINK_ROOT.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
    linked = 0
    for v in versions:
        n = _link_version(v, SYMLINK_ROOT)
        linked += n
        print(f"  [link] {v.name}: {n} files → {SYMLINK_ROOT / v.name}/")
    html = _build_index_html(SYMLINK_ROOT, engine_dir, versions)
    gallery = _build_gallery_json(SYMLINK_ROOT, versions)
    anchor_grid = _build_anchor_grid_json(engine_dir, SYMLINK_ROOT)
    print(f"[OK] 画廊: file://{html}")
    print(f"[OK] JSON:  {gallery.relative_to(ROOT)}")
    if anchor_grid:
        print(f"[OK] 锚点: {anchor_grid.relative_to(ROOT)}")
    print(f"[OK] 总计: {len(versions)} 个版本, {linked} 个文件已链接")
    if open_after:
        webbrowser.open(f"file://{html}")


def cmd_list():
    engine_dir = _resolve_engine_dir()
    versions = _list_versions(engine_dir)
    if not versions:
        print(f"{engine_dir}/outputs/review/ 下没有任何版本目录。")
        return
    print(f"{engine_dir}/outputs/review/:")
    for v in versions:
        print(f"  {v.name}/")
        for f in sorted(v.iterdir()):
            if f.suffix.lower() in (".gif", ".png", ".md"):
                print(f"    - {f.name}")


# ----------------------------------------------------------
# CLI
# ----------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="eye-emotion-engine 评审 GIF 的本地浏览/嵌入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--setup", action="store_true",
                   help="创建软链 + 生成 index.html + gallery.json（默认动作）")
    g.add_argument("--list", action="store_true",
                   help="列出所有可用版本及其产物")
    p.add_argument("--review", type=str, default=None,
                   help="只同步指定版本（目录名），默认全部")
    p.add_argument("--open", action="store_true",
                   help="--setup 完后自动打开 index.html")
    return p.parse_args()


def main():
    args = parse_args()
    if args.list:
        cmd_list()
        return
    cmd_setup(args.review, args.open)


if __name__ == "__main__":
    main()
