"""
EyeDebugOverlay — 左眼状态信息半透明叠加层

用较大字体把当前关键状态打在左眼屏幕上：
  - 眼睛追踪来源 [idle] / [face] / [motion]
  - 反应式语音状态：等待说话 / 开始监听 / 触发播放 / 正在播放TTS / 播放结束
  - 当前播放的情绪 / 文件名提示
  - 实时麦克风电平

叠加层保持半透明，不遮挡眼睛动画本身。

字体策略：
- 树莓派上常见中文字体（DroidSansFallbackFull）只有 CJK 字形、没有拉丁字母，
  因此使用“拉丁字体 + CJK fallback”组合渲染，避免混排时出现方框。
"""

import os
import time
from PIL import Image, ImageDraw, ImageFont


WIDTH = 160
HEIGHT = 160
UPDATE_INTERVAL = 0.2  # 状态变化响应更快
FONT_SIZE = 11          # 略小，避免遮挡瞳孔
PUPIL_CENTER_Y = HEIGHT // 2  # 瞳孔中心在屏幕正中央


def _is_cjk(char):
    """判断字符是否属于 CJK 统一表意文字区域。"""
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF          # CJK Unified Ideographs
        or 0x3400 <= code <= 0x4DBF      # CJK Extension A
        or 0xF900 <= code <= 0xFAFF      # CJK Compatibility Ideographs
        or 0x3000 <= code <= 0x303F      # CJK Symbols and Punctuation
        or 0xFF00 <= code <= 0xFFEF      # Fullwidth forms
    )


class FallbackFont:
    """
    双字体 fallback：拉丁字符用 latin_font，CJK 字符用 cjk_font。

    提供与 PIL ImageFont 类似的 textbbox / draw 接口，自动切分文本。
    """

    def __init__(self, latin_font, cjk_font, size=FONT_SIZE):
        self.latin_font = latin_font
        self.cjk_font = cjk_font
        self.size = size

    def _segments(self, text):
        """把文本切成同字体段。"""
        if not text:
            yield "", False
            return
        current = []
        current_cjk = _is_cjk(text[0])
        for ch in text:
            if ch == "\n":
                yield "".join(current), current_cjk
                current = []
                continue
            is_c = _is_cjk(ch)
            if is_c == current_cjk or not current:
                current.append(ch)
                current_cjk = is_c
            else:
                yield "".join(current), current_cjk
                current = [ch]
                current_cjk = is_c
        if current:
            yield "".join(current), current_cjk

    def getbbox(self, text):
        """估算文本包围盒，返回真实的 top / bottom。"""
        total_w = 0
        min_top = 0
        max_bottom = 0
        for seg, is_cjk_seg in self._segments(text):
            font = self.cjk_font if is_cjk_seg else self.latin_font
            bbox = font.getbbox(seg)
            if bbox:
                total_w += bbox[2] - bbox[0]
                min_top = min(min_top, bbox[1])
                max_bottom = max(max_bottom, bbox[3])
        return (0, min_top, total_w, max_bottom)

    def draw(self, draw, xy, text, fill):
        """在指定位置绘制混排文本。"""
        x, y = xy
        for seg, is_cjk_seg in self._segments(text):
            font = self.cjk_font if is_cjk_seg else self.latin_font
            draw.text((x, y), seg, font=font, fill=fill)
            bbox = font.getbbox(seg)
            if bbox:
                x += bbox[2] - bbox[0]


def _load_fonts():
    """加载拉丁字体 + CJK fallback 字体。"""
    latin_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf",
    ]
    cjk_candidates = [
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
    ]

    latin_font = None
    for path in latin_candidates:
        if os.path.exists(path):
            try:
                latin_font = ImageFont.truetype(path, FONT_SIZE)
                print(f"[OVERLAY] 加载拉丁字体: {path}")
                break
            except Exception as e:
                print(f"[OVERLAY] 拉丁字体加载失败 {path}: {e}")

    cjk_font = None
    for path in cjk_candidates:
        if os.path.exists(path):
            try:
                cjk_font = ImageFont.truetype(path, FONT_SIZE)
                print(f"[OVERLAY] 加载中文字体: {path}")
                break
            except Exception as e:
                print(f"[OVERLAY] 中文字体加载失败 {path}: {e}")

    if cjk_font is None:
        print("[OVERLAY] 未找到中文字体，中文将显示为方框")

    if latin_font and cjk_font:
        return FallbackFont(latin_font, cjk_font, FONT_SIZE), True
    if latin_font:
        return latin_font, False
    if cjk_font:
        return cjk_font, True
    print("[OVERLAY] 使用 PIL 默认字体")
    return ImageFont.load_default(), False


# 如果最终没有中文字体，把中文状态映射成英文关键词（避免显示成方块）
_STATE_MAP = {
    "等待说话": "WAIT",
    "开始监听": "LISTEN",
    "触发播放": "TRIGGER",
    "正在播放TTS": "PLAYING",
    "播放结束": "DONE",
    "已停止": "STOPPED",
}


def _safe_text(text, has_cjk):
    """在没有中文字体时，把已知中文状态替换为英文。"""
    if has_cjk or not text:
        return text
    return _STATE_MAP.get(text, text)


class EyeDebugOverlay:
    """
    为 EyeDisplay 提供的状态叠加层。

    用法:
        overlay = EyeDebugOverlay(
            eye_display=eye,
            mic_monitor=mic,
            voice_module=reactive,
        )
        eye.set_debug_overlay(overlay)
    """

    def __init__(
        self,
        eye_display=None,
        mic_monitor=None,
        camera_streamer=None,
        voice_module=None,
        http_port=8080,
    ):
        self.eye = eye_display
        self.mic = mic_monitor
        self.camera = camera_streamer
        self.voice = voice_module
        self.http_port = http_port

        self._font, self._has_cjk = _load_fonts()

        self._last_update = 0.0
        self._cached = None
        self._last_key = ""

    def _state_key(self):
        """生成当前状态指纹，用于判断是否需要重绘。"""
        if self.eye:
            src = getattr(self.eye, "target_source", "idle")
        else:
            src = "idle"
        if self.voice:
            vstate = getattr(self.voice, "state", "")
            vinfo = getattr(self.voice, "last_info", "")
        else:
            vstate = ""
            vinfo = ""
        rms = round(self.mic.rms, 4) if self.mic else 0.0
        return f"{src}|{vstate}|{vinfo}|{rms}"

    def _collect_lines(self):
        """收集要显示的大字号状态行。"""
        lines = []

        # 第 1 行：眼睛追踪来源
        if self.eye:
            src = getattr(self.eye, "target_source", "idle")
        else:
            src = "idle"
        lines.append(f"[{src}]")

        # 第 2 行：优先显示 random_audio 播放状态，否则显示 reactive voice 状态
        try:
            from voice.backends.preset import player_state
        except Exception:
            player_state = {"count": 0, "category": "", "filename": ""}

        if player_state.get("count", 0) > 0:
            lines.append(_safe_text("正在播放", self._has_cjk))
            cat = player_state.get("category", "")
            fname = player_state.get("filename", "")
            lines.append(f"{cat} {fname}")
        elif self.voice:
            vstate = getattr(self.voice, "state", "")
            lines.append(_safe_text(vstate, self._has_cjk))
            vinfo = getattr(self.voice, "last_info", "")
            if vinfo:
                lines.append(vinfo)
        else:
            lines.append(_safe_text("等待说话", self._has_cjk))

        # 第 4 行：实时麦克风电平
        if self.mic:
            rms = self.mic.rms
            lines.append(f"mic {rms:.4f}")
        else:
            lines.append("mic n/a")

        return lines

    def _line_metrics(self, draw, line):
        """返回单行文本的 (width, top, bottom)。"""
        if isinstance(self._font, FallbackFont):
            bbox = self._font.getbbox(line)
        else:
            bbox = draw.textbbox((0, 0), line, font=self._font)
        return bbox[2] - bbox[0], bbox[1], bbox[3]

    def _line_height(self, draw):
        """计算行高（视觉高度 + 紧凑行间距）。"""
        _, top, bottom = self._line_metrics(draw, "Ay等待")
        return (bottom - top) + 2

    def render(self, size=(WIDTH, HEIGHT)):
        """渲染半透明叠加层；状态未变时返回缓存。"""
        now = time.time()
        key = self._state_key()
        if self._cached is not None and key == self._last_key and \
                (now - self._last_update) < UPDATE_INTERVAL:
            return self._cached

        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        lines = self._collect_lines()
        metrics = [self._line_metrics(draw, line) for line in lines]
        line_h = self._line_height(draw)

        # 把整段文字的视觉中心对准瞳孔中心
        top0 = metrics[0][1] if metrics else 0
        bottom_last = metrics[-1][2] if metrics else 0
        block_center_offset = (top0 + bottom_last + (len(lines) - 1) * line_h) / 2
        start_y = int(round(PUPIL_CENTER_Y - block_center_offset))

        for i, line in enumerate(lines):
            w = metrics[i][0]
            x = (size[0] - w) // 2
            y = start_y + i * line_h
            # 阴影
            if isinstance(self._font, FallbackFont):
                self._font.draw(draw, (x + 1, y + 1), line, fill=(0, 0, 0, 120))
                self._font.draw(draw, (x, y), line, fill=(255, 255, 255, 200))
            else:
                draw.text((x + 1, y + 1), line, font=self._font, fill=(0, 0, 0, 120))
                draw.text((x, y), line, font=self._font, fill=(255, 255, 255, 200))

        self._cached = overlay
        self._last_key = key
        self._last_update = now
        return overlay
