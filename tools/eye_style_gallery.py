#!/usr/bin/env python3
"""
眼睛样式浏览器 — 手动快速浏览全部内置样式（只启动屏幕，秒启动）。

不启动摄像头/麦克风/语音，眼睛保持 idle 扫视 + 眨眼；终端按键实时热切换，
切换后样式名在左眼屏幕上短暂显示 2 秒。

用法:
    sudo systemctl stop doll-robot          # 先停生产服务（避免抢占 SPI/GPIO）
    python3 tools/eye_style_gallery.py
    python3 tools/eye_style_gallery.py --auto 5   # 或每 5 秒自动轮播
    sudo systemctl start doll-robot         # 浏览完恢复生产

按键（终端直接按，无需回车）:
    空格 / 回车 / n / ↓   下一个样式
    p / ↑                上一个样式
    → / e                下一个表情（正常→开心→生气→哭→嫌弃→缩瞳，循环）
    ←                    上一个表情
    数字 + 回车           跳转到第 N 个样式
    q                    退出

表情动画与 doll-eye-styles 的 GIF 生成器同源（eye_expressions.py），
在 Pi 上实时参数化渲染，不依赖 GIF 文件。
"""

import argparse
import json
import os
import queue
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont

from eye_display import EyeDisplay
from eye_styles import list_styles
from eye_expressions import list_expressions

W = H = 160
NAME_HOLD = 2.0  # 切换后样式名在屏幕上停留秒数
START_STYLE = "lovot_lianlian"  # 从生产样式开始，方便对比

# 表情循环：None = 正常 idle 行为
EXPR_CHOICES = [None] + [n for n, _ in list_expressions()]
EXPR_CN = {None: None, **dict(list_expressions())}

# Review 模式：读 outputs/reviews/_anchor_grid.json（review_browser.py --setup 生成），
# 与 eye-emotion-engine 的 review_intensity.gif 一致 — 27 锚点 × 4 强度 = 108 单元，
# 用眼睛当前样式实时画出来。无 anchor JSON 时退回本仓 5 表情。
from eye_expressions import EXPRESSIONS  # noqa: E402

REVIEW_INTENSITIES = [0.3, 0.6, 1.0, 1.4]


def _load_anchor_grid() -> dict | None:
    """读 review_browser 生成的 _anchor_grid.json（27×4 单元静态 draw_eye 参数）。

    缺失时返回 None — ReviewPlayer 退回到本仓 5 表情。
    """
    p = Path(__file__).resolve().parent.parent / "outputs" / "reviews" / "_anchor_grid.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[review] 解析 {p.name} 失败: {e}")
        return None


class ReviewPlayer:
    """把每一帧的 eye 渲染责任揽下来：用当前样式实时画 (emotion, intensity)。

    数据源（优先级）：
      (1) outputs/reviews/_anchor_grid.json — eye-emotion-engine 27 锚点 × 4 强度
          × 12 帧（5 秒动效序列），含 mirror=True 副本
      (2) 本仓 eye_expressions 5 表情 × 4 强度（fallback）

    关键属性：
      - 默认不自动循环 — 用户按键 [ ] 0 才翻格
      - 返回 (left, right) 元组，轴对称（与 happy/angry/cry 一致）
      - 屏端重画时按当前帧序列取值（基于 time.time()）
    """

    def __init__(self, style_getter):
        self._style_getter = style_getter
        self.cells: list[dict] = []           # 每项 {"emotion", "intensity", "label", "frames"?}
        self.fps = 12
        self.frame_dur_ms = 1.0 / self.fps
        self.i = 0
        self._last_left: Image.Image = self._blank()
        self._last_right: Image.Image = self._blank()
        self._last_tick = 0.0
        self._frame_idx = 0
        self._hold_remaining = 0   # 闭眼眶停留计数器（帧数）
        self._init_cells()

    def _blank(self) -> Image.Image:
        return Image.new("RGBA", (W, H), (0, 0, 0, 0))

    def _init_cells(self):
        grid = _load_anchor_grid()
        if grid:
            self.fps = grid.get("fps", 12)
            self.frame_dur_ms = 1000.0 / self.fps
            for row in grid.get("rows", []):
                emo = row.get("emotion", "?")
                for cell in row.get("cells", []):
                    entry = {"emotion": emo, "intensity": cell["intensity"],
                             "label": emo, "frames": cell.get("frames")}
                    self.cells.append(entry)
            if self.cells:
                nf = grid.get("frames_per_cell", 0)
                print(f"[review] 27 锚点 × 4 强度 × {nf} 帧 = "
                      f"{len(self.cells)} 单元 × 序列动画 "
                      f"(from _anchor_grid.json, fps={self.fps})")
                return
        # fallback：本仓 5 表情
        for name, cn in list_expressions():
            for inten in REVIEW_INTENSITIES:
                self.cells.append({"emotion": name, "intensity": inten,
                                   "label": cn, "frames": None})
        print(f"[review] 退回 {len(self.cells)} 单元 (本仓 5 表情 × 4 强度)")

    def is_empty(self) -> bool:
        return not self.cells

    def __len__(self) -> int:
        return len(self.cells)

    def current_label(self) -> str:
        if not self.cells:
            return "(无)"
        c = self.cells[self.i]
        return f"{c['label']} ×{c['intensity']:.1f}"

    def next(self):
        if self.cells:
            self.i = (self.i + 1) % len(self.cells)
            self._frame_idx = 0
            self._last_tick = time.time()

    def prev(self):
        if self.cells:
            self.i = (self.i - 1) % len(self.cells)
            self._frame_idx = 0
            self._last_tick = time.time()

    def seek_start(self):
        self.i = 0
        self._frame_idx = 0
        self._last_tick = time.time()

    def restart_anim(self):
        """进 review 时调用：当前格动画从头播，且把 _last_tick 校准到现在
        （player 在 gallery 启动时创建，_last_tick=0.0，不校准的话第一次
        __call__ 会把 _frame_idx 冲到天文数字）。"""
        self._frame_idx = 0
        self._last_tick = time.time()
        self._hold_remaining = 0

    def _render_one(self, kw_base: dict, mirror: bool) -> Image.Image:
        """按 kw_base 调 draw_eye 画一帧，specifying mirror。

        mirror=True（右眼）时：
        - gaze_x 在 draw_eye 内部已翻转
        - cry_glint_data 坐标必须手动水平翻转（x → 160-x-size）
        - eyelid_tilt / lower_eyelid_tilt 取反（眉毛/下睑倾斜方向镜像）
        """
        from eye_render import draw_eye
        ACCEPT = {"gaze_x", "gaze_y", "eyelid", "mirror",
                  "pupil_relative_scale", "glint_jitter_x", "glint_jitter_y",
                  "vergence_x", "is_idle", "glint_scale", "iris_scale",
                  "eyelid_tilt", "eyelid_target_y", "eyelid_flatten",
                  "show_lower_eyelid", "eyelid_close_y",
                  "lower_eyelid_cup", "lower_eyelid_tilt", "cry_glint_data"}
        kw = {k: v for k, v in kw_base.items() if k in ACCEPT}

        # 右眼：眼睑/泪光镜像
        if mirror:
            # 泪光坐标水平翻转（像素坐标，160×160 画布）
            cgd = kw.get("cry_glint_data")
            if cgd:
                kw["cry_glint_data"] = [
                    {**g, "x": 160 - g["x"] - int(g["size"]),
                     "rotation": -(g.get("rotation", 0))}
                    for g in cgd
                ]
            # 眉毛/下睑倾斜取反（正→负，负→正）
            for tilt_key in ("eyelid_tilt", "lower_eyelid_tilt"):
                if tilt_key in kw:
                    kw[tilt_key] = -kw[tilt_key]

        kw["mirror"] = mirror
        kw.setdefault("vergence_x", 0)
        kw.setdefault("is_idle", False)
        style = self._style_getter()
        try:
            return draw_eye(style=style, **kw).convert("RGBA")
        except Exception:
            return self._blank()

    def _draw_animated(self) -> tuple[Image.Image, Image.Image]:
        """返回 (left, right) - 轴对称对。

        锚点路径：用当前帧序列的 params。
        fallback：5 表情静态强度插值。
        """
        c = self.cells[self.i]
        if c.get("frames"):
            frames = c["frames"]
            fi = self._frame_idx % len(frames)
            kw = dict(frames[fi]["params"])
        else:
            # fallback
            try:
                expr = EXPRESSIONS[c["emotion"]]
                inten = c["intensity"]
                neutral = dict(
                    gaze_x=0.0, gaze_y=0.0, eyelid=0,
                    pupil_relative_scale=1.0,
                    glint_jitter_x=0.0, glint_jitter_y=0.0,
                )
                full = expr.step(0.6)
                blended = {
                    k: neutral[k] + inten * (full.get(k, neutral[k]) - neutral[k])
                    for k in neutral
                }
                kw = {
                    "gaze_x": blended["gaze_x"], "gaze_y": blended["gaze_y"],
                    "eyelid": blended["eyelid"], "pupil_relative_scale":
                        blended["pupil_relative_scale"],
                    "glint_jitter_x": blended["glint_jitter_x"],
                    "glint_jitter_y": blended["glint_jitter_y"],
                    "is_idle": full.get("is_idle", True),
                }
                if inten >= 0.99:
                    extra = expr.frame_kwargs(full, mirror=False)
                    for k, v in extra.items():
                        kw.setdefault(k, v)
            except Exception:
                kw = {"eyelid": 0}
        return self._render_one(kw, mirror=False), self._render_one(kw, mirror=True)

    def __call__(self) -> tuple[Image.Image, Image.Image]:
        """每帧调用一次，返回 (左 RGBA, 右 RGBA)。

        帧序列：基于 time.time() 推进 self._frame_idx；press [ ] 才换 cell。
        闭眼眶（eyelid >= 115）时停留 3 帧（约 250ms @12fps），让人眼看清闭眼。
        """
        if not self.cells:
            return self._blank(), self._blank()
        now = time.time()
        elapsed_ms = (now - self._last_tick) * 1000.0
        advance = int(elapsed_ms / self.frame_dur_ms)
        if advance > 0:
            if self._hold_remaining > 0:
                # 闭眼眶停留：消费掉本轮 advance ticks 但不推进帧号
                if self._hold_remaining >= advance:
                    self._hold_remaining -= advance
                    advance = 0
                else:
                    advance -= self._hold_remaining
                    self._hold_remaining = 0
            if advance > 0:
                self._frame_idx += advance
                self._last_tick += advance * self.frame_dur_ms / 1000.0
        left, right = self._draw_animated()
        self._last_left, self._last_right = left, right
        # 闭眼眶停留：eyelid >= 90 时触发 3 帧 hold
        # 原 115 太高——Anger/Triumph 等眨眼到 88-112 过程没有停留感，
        # 用户只看到快进的全闭帧。90 覆盖下降尾段（约 88→90→112→120）
        c = self.cells[self.i]
        frs = c.get("frames")
        if frs:
            fi = self._frame_idx % len(frs)
            if frs[fi]["params"].get("eyelid", 0) >= 90:
                self._hold_remaining = max(self._hold_remaining, 3)
        return left, right


class StyleNameOverlay:
    """左眼叠加层：切换样式后短暂显示 样式名 + 序号（半透明底，自动消失）。"""

    _FONT_CANDIDATES = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]

    def __init__(self):
        self._name = ""
        self._expr = None
        self._label = ""
        self._until = 0.0
        self._font = self._load(16)
        self._font_small = self._load(12)

    def _load(self, size):
        for path in self._FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def show(self, name, index, total, expr_cn=None):
        self._name = name
        self._expr = expr_cn
        self._label = f"{index}/{total}"
        self._until = time.time() + NAME_HOLD

    def render(self, size=(W, H)):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        if not self._name or time.time() > self._until:
            return img
        draw = ImageDraw.Draw(img)
        w, h = size
        # 样式名超过 15 字符折两行
        n = self._name
        lines = [(n, self._font)] if len(n) <= 15 else \
            [(n[:15], self._font), (n[15:], self._font)]
        if self._expr:
            lines.append((self._expr, self._font))
        lines.append((self._label, self._font_small))
        metrics = []
        for text, font in lines:
            bb = draw.textbbox((0, 0), text, font=font)
            metrics.append((bb[2] - bb[0], bb[3] - bb[1]))
        pad_x, pad_y, gap = 10, 6, 3
        box_w = max(mw for mw, _ in metrics) + pad_x * 2
        box_h = sum(mh for _, mh in metrics) + gap * (len(lines) - 1) + pad_y * 2
        x0, y0 = (w - box_w) // 2, (h - box_h) // 2
        draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h],
                               radius=8, fill=(0, 0, 0, 120))
        ty = y0 + pad_y
        for (text, font), (mw, mh) in zip(lines, metrics):
            draw.text(((w - mw) / 2, ty), text, font=font, fill=(255, 255, 255, 240))
            ty += mh + gap
        return img


def _service_running() -> bool:
    """doll-robot 服务是否正在运行（会与本工具抢占 SPI/GPIO）。"""
    try:
        r = subprocess.run(["systemctl", "is-active", "--quiet", "doll-robot"],
                           capture_output=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _stdin_reader(cmd_queue: queue.Queue, stop: threading.Event):
    """后台读键盘：TTY 用 cbreak 单字符模式（方向键可用），管道用行模式。"""
    if sys.stdin.isatty():
        while not stop.is_set():
            ch = sys.stdin.read(1)
            if ch == "\x1b":  # 方向键是 ESC [ A/B/C/D 三字节序列
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch += sys.stdin.read(1)
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        ch += sys.stdin.read(1)
            cmd_queue.put(ch)
    else:  # 管道/重定向：按行读（用于脚本测试）
        while not stop.is_set():
            line = sys.stdin.readline()
            if line == "":  # EOF
                cmd_queue.put("q")
                return
            cmd_queue.put(line.strip())


def main():
    parser = argparse.ArgumentParser(
        description="眼睛样式浏览器：按键热切换全部内置样式（只启动屏幕）")
    parser.add_argument("--auto", type=float, default=0.0,
                        help="自动轮播间隔秒数（0=手动，默认 0）")
    parser.add_argument("--mono", action="store_true", help="单目模式")
    parser.add_argument("--force", action="store_true",
                        help="跳过 doll-robot 服务占用检查")
    args = parser.parse_args()

    if not args.force and _service_running():
        print("[!] doll-robot 服务正在运行，会与本工具抢占 SPI/GPIO。")
        print("    请先执行:  sudo systemctl stop doll-robot")
        print("    浏览完后:  sudo systemctl start doll-robot")
        print("    （确认无冲突可加 --force 跳过检查）")
        return 1

    styles = list_styles()
    total = len(styles)
    print("=" * 58)
    print(f"眼睛样式浏览器 — 共 {total} 款样式 × {len(EXPR_CHOICES)} 种表情")
    for i, (name, desc) in enumerate(styles, 1):
        print(f"  {i:2d}. {name:<20} {desc}")
    print("-" * 58)
    print("样式: 空格/回车/n/↓=下一个  p/↑=上一个  数字+回车=跳转")
    print("表情: →/e=下一个表情  ←=上一个表情")
    print("      (idle→happy→angry→cry→dislike→miosis→surprise→surprise1.0→amusement1.0→sadness0.6→sadness1.0)")
    print("评审: r=进入/退出 review 预览 (5表情 × 4强度, 用当前样式实时绘制)")
    print("      [ / ]=上下翻单元  0=回到第 1 个单元")
    print("退出: q")
    if args.auto > 0:
        print(f"自动轮播: 每 {args.auto:g} 秒切表情，转完一圈自动换样式（手动按键重新计时）")
    print("=" * 58)

    eye = EyeDisplay(dual=not args.mono)
    overlay = StyleNameOverlay()
    eye.set_debug_overlay(overlay)
    eye.start()

    # review 预览：用当前样式实时渲染每个 (expressions, intensity) 单元，
    # 不是贴 GIF（硬贴会让眼睛看不出当前样式的真实效果）
    review_player = ReviewPlayer(style_getter=lambda: eye.style)
    print(f"[*] review 预览: {len(review_player)} 单元 (5表情 × 4强度)，按 r 进入")

    cmd_queue: queue.Queue = queue.Queue()
    stop = threading.Event()

    # TTY 切到 cbreak 模式，实现无需回车的单键响应；退出时务必恢复
    old_attrs = None
    cbreak_mode = sys.stdin.isatty()
    if cbreak_mode:
        import termios
        import tty
        old_attrs = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())

    reader = threading.Thread(target=_stdin_reader, args=(cmd_queue, stop),
                              daemon=True, name="gallery-stdin")
    reader.start()

    idx = next((i for i, (n, _) in enumerate(styles) if n == START_STYLE), 0)
    expr_i = 0  # EXPR_CHOICES 下标，0 = 正常 idle
    review_mode = False

    def show_line():
        name, desc = styles[idx]
        if review_mode:
            suffix = f" · review: {review_player.current_label()}"
        else:
            expr_cn = EXPR_CN[EXPR_CHOICES[expr_i]]
            suffix = f" · 表情: {expr_cn}" if expr_cn else ""
        overlay.show(name, idx + 1, total,
                     review_player.current_label() if review_mode else None)
        print(f">>> [{idx + 1}/{total}] {name} — {desc}{suffix}")

    def apply_style(i):
        nonlocal idx
        idx = i % total
        eye.set_style(styles[idx][0])
        # 切样式时让 review 立刻重画（style 已换，缓存的帧已不准）
        if review_mode:
            review_player._last_render_at = 0.0
        show_line()

    def apply_expr(i):
        nonlocal expr_i
        expr_i = i % len(EXPR_CHOICES)
        eye.set_expression(EXPR_CHOICES[expr_i])
        show_line()

    def enter_review():
        nonlocal review_mode
        if review_player.is_empty():
            print("[!] review 列表为空，不应发生")
            return
        review_mode = True
        eye.set_expression(None)  # 表情时间线停掉，让 review 独占帧
        review_player.restart_anim()
        eye.set_review_frame_provider(review_player)
        show_line()

    def exit_review():
        nonlocal review_mode
        review_mode = False
        eye.set_review_frame_provider(None)
        show_line()

    apply_style(idx)
    next_auto = time.time() + args.auto if args.auto > 0 else float("inf")
    pending_digits = ""

    try:
        while not stop.is_set():
            try:
                cmd = cmd_queue.get(timeout=0.1)
            except queue.Empty:
                cmd = None
            if cmd is None:
                if time.time() >= next_auto:
                    # 自动轮播：先切表情，转回 idle 时自动换下一个样式
                    apply_expr(expr_i + 1)
                    if expr_i == 0:
                        apply_style(idx + 1)
                    next_auto = time.time() + args.auto
                continue

            c = cmd
            if c.isdigit():
                if not cbreak_mode:
                    # 管道行模式：读到的是完整数字 → 直接跳转
                    n = int(c)
                    if 1 <= n <= total:
                        apply_style(n - 1)
                    else:
                        print(f"序号超出范围 (1-{total})")
                else:  # cbreak 模式逐字符收集，回车确认
                    pending_digits += c
                    print(f"跳转 {pending_digits} ? (回车确认)", end="\r")
                continue
            if c in ("\r", "\n", "") and pending_digits:
                n = int(pending_digits)
                pending_digits = ""
                if 1 <= n <= total:
                    apply_style(n - 1)
                else:
                    print(f"序号超出范围 (1-{total})")
                continue
            pending_digits = ""

            if c in (" ", "n", "\r", "\n", "", "\x1b[B"):   # 空格/回车/n/↓
                apply_style(idx + 1)
            elif c in ("p", "\x1b[A"):                       # p/↑
                apply_style(idx - 1)
            elif c in ("e", "\x1b[C"):                       # e/→
                apply_expr(expr_i + 1)
            elif c == "\x1b[D":                              # ←
                apply_expr(expr_i - 1)
            elif c.lower() == "r":                           # r/R review 模式
                if review_mode:
                    exit_review()
                else:
                    enter_review()
            elif review_mode and c in ("]", "】"):             # review 翻到下一个单元
                review_player.next()
                show_line()
            elif review_mode and c in ("[", "【"):             # review 翻到上一个单元
                review_player.prev()
                show_line()
            elif review_mode and c == "0":                    # review 回到第 1 单元
                review_player.seek_start()
            elif c in ("q", "Q", "\x03"):  # q / Ctrl-C
                break
            else:
                # 别静默吞掉异常字符——打印被丢弃的字节码方便用户诊断
                if c and c.isprintable():
                    hint = repr(c)
                else:
                    hint = f"0x{ord(c[0]):02x}" if c else "(空)"
                print(f"未识别按键 {hint}（空格=下个样式, →=下个表情, r=review, q=退出）")

            if args.auto > 0:
                next_auto = time.time() + args.auto  # 手动操作后重新计时
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if old_attrs is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attrs)
        eye.set_review_frame_provider(None)  # 退出前先清掉，否则屏上一直贴尾帧
        eye.stop()
        print("已退出样式浏览器。恢复生产: sudo systemctl start doll-robot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
