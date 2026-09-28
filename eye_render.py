"""
离线眼睛渲染 — 纯 Pillow 实现，零硬件依赖。

提供 160×160 画布的眼睛单帧合成：巩膜、虹膜、虹膜纹理、瞳孔、高光、眼睑、
可选睫毛 / 眼线 / 暗角 / 软阴影。模块内自带图层缓存（_CACHE），样式切换时
自动重建。

依赖：
  - Pillow（PIL）
  - eye_styles（样式数据）

公开 API：
  WIDTH, HEIGHT         — 画布尺寸 (160×160)
  draw_eye(...)         — 单帧合成
  _init_cache(style)    — 预渲染各图层；样式切换时自动重建
"""

import math

from PIL import Image, ImageDraw, ImageChops, ImageFilter

from eye_styles import get_style, DEFAULT_STYLE

# ============================================================
# 画布尺寸
# ============================================================
WIDTH = 160
HEIGHT = 160

# 预渲染图层缓存
_CACHE = {}
_CURRENT_STYLE = None


# ============================================================
# 通用图像工具
# ============================================================
def _radial_gradient(size, center_color, edge_color):
    """生成从中心到边缘的径向渐变 RGB 图像。"""
    img = Image.new("RGB", (size, size))
    cx = cy = size // 2
    max_dist = math.hypot(cx, cy)
    pixels = img.load()
    cr, cg, cb = center_color
    er, eg, eb = edge_color
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy) / max_dist
            d = min(d, 1.0)
            r = int(cr + (er - cr) * d)
            g = int(cg + (eg - cg) * d)
            b = int(cb + (eb - cb) * d)
            pixels[x, y] = (r, g, b)
    return img


def _circle_mask(size):
    """生成圆形 alpha 遮罩。"""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def _radial_ring_gradient(size, center_color, ring_color, edge_color, ring_radius=0.38, top_color=None, bottom_color=None, vertical_blend=0.35):
    """Ring radial gradient with bottom brightness boost for jelly effect."""
    img = Image.new("RGB", (size, size))
    cx = cy = size // 2
    max_dist = math.hypot(cx, cy)
    pixels = img.load()
    cr, cg, cb = center_color
    rr, rg, rb = ring_color
    er, eg, eb = edge_color
    has_vert = top_color is not None and bottom_color is not None
    if has_vert:
        tr, tg, tb = top_color
        br, bg, bb = bottom_color
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy) / max_dist
            d = min(d, 1.0)
            if d <= ring_radius:
                t = d / ring_radius
                r = int(cr + (rr - cr) * t)
                g = int(cg + (rg - cg) * t)
                b = int(cb + (rb - cb) * t)
            else:
                t = (d - ring_radius) / (1.0 - ring_radius)
                r = int(rr + (er - rr) * t)
                g = int(rg + (eg - rg) * t)
                b = int(rb + (eb - rb) * t)
            if has_vert:
                vy = y / max(1, size - 1)
                vr = int(tr + (br - tr) * vy)
                vg = int(tg + (bg - tg) * vy)
                vb = int(tb + (bb - tb) * vy)
                vb2 = vertical_blend * (1.0 if vy < 0.6 else 1.0 - (vy - 0.6) / 0.4 * 0.9)
                r = int(r * (1 - vb2) + vr * vb2)
                g = int(g * (1 - vb2) + vg * vb2)
                b = int(b * (1 - vb2) + vb * vb2)
            pixels[x, y] = (min(255, r), min(255, g), min(255, b))
    return img
def _ellipse_mask(size, width, height):
    """生成居中的椭圆 alpha 遮罩。"""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    cx = cy = size // 2
    draw.ellipse(
        [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2],
        fill=255,
    )
    return mask


def _egg_mask(size, width, height, asym=0.18):
    """
    生成鸡蛋形 alpha 遮罩（顶部更尖、底部更圆润）。
    通过沿垂直方向缩放半径实现，asym 越大底部越饱满。
    """
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    cx = cy = size // 2
    a = width / 2
    b = height / 2
    points = []
    for deg in range(0, 360, 3):
        t = math.radians(deg)
        # 鸡蛋曲线：顶部半径略小，底部半径略大
        radius_scale = 1.0 + asym * math.sin(t)
        x = cx + a * math.cos(t) * radius_scale
        y = cy + b * math.sin(t)
        x = max(0, min(size - 1, x))
        y = max(0, min(size - 1, y))
        points.append((x, y))
    draw.polygon(points, fill=255)
    return mask


def _enhance_rgb(img, vividness):
    """对 RGB 图像应用对比度/饱和度/亮度增强。"""
    if not vividness:
        return img
    from PIL import ImageEnhance
    img = ImageEnhance.Brightness(img).enhance(vividness.get("brightness", 1.0))
    img = ImageEnhance.Contrast(img).enhance(vividness.get("contrast", 1.0))
    img = ImageEnhance.Color(img).enhance(vividness.get("saturation", 1.0))
    return img


def _apply_style_defaults(style):
    """为旧样式/简化样式填充新增参数的默认值，保持向后兼容。"""
    defaults = {
        "iris_gradient_mode": "vertical",
        "iris_top_color": style.get("iris_edge"),
        "iris_bottom_color": style.get("iris_center"),
        "iris_ring_color": style.get("iris_center"),
        "iris_ring_radius": 0.40,
        "iris_bottom_rim": (
            min(255, int(style.get("iris_center", (128, 128, 128))[0] * 1.2 + 100)),
            min(255, int(style.get("iris_center", (128, 128, 128))[1] * 0.8 + 120)),
            min(255, int(style.get("iris_center", (128, 128, 128))[2] * 0.5 + 150)),
        ) if style.get("iris_center") else None,
        "iris_pattern": "fibers",
        "iris_scale": 0.80,
        "iris_aspect": 1.0,
        "limbal_ring": None,
        "pupil_blur": 3.0,
        "pupil_outline": None,
        "sclera_tint": None,
        "enable_sclera_shade": True,
        "sclera_vignette": None,
        "iris_shadow": None,
        "iris_offset_y": 0,
        "eye_shape": "circle",
        "eye_width": WIDTH,
        "eye_height": HEIGHT,
        "eye_asym": 0.0,
        "vividness": None,
        "eyelashes": None,
        "glints": [
            {"shape": "round", "x": 62, "y": 54, "w": 26, "h": 24,
             "color": (255, 255, 255, 235), "glow": (255, 255, 255, 45), "glow_radius": 34},
            {"shape": "ellipse", "x": 90, "y": 92, "w": 16, "h": 5,
             "color": (255, 255, 255, 120), "rotation": -40, "blur": 1.5},
            {"shape": "ellipse", "x": 84, "y": 106, "w": 12, "h": 4,
             "color": (255, 255, 255, 85), "rotation": -35, "blur": 1.0},
        ],
    }
    merged = {**defaults, **{k: v for k, v in style.items() if v is not None}}
    return merged


def _vertical_gradient(size, top_color, bottom_color):
    """生成从上到下的垂直渐变 RGB 图像。"""
    img = Image.new("RGB", (size, size))
    pixels = img.load()
    tr, tg, tb = top_color
    br, bg, bb = bottom_color
    max_y = size - 1
    for y in range(size):
        d = y / max_y if max_y else 0
        r = int(tr + (br - tr) * d)
        g = int(tg + (bg - tg) * d)
        b = int(tb + (bb - tb) * d)
        for x in range(size):
            pixels[x, y] = (r, g, b)
    return img


def _diamond_box(cx, cy, w, h):
    """返回菱形多边形的四个顶点。"""
    return [
        (cx, cy - h / 2),
        (cx + w / 2, cy),
        (cx, cy + h / 2),
        (cx - w / 2, cy),
    ]


def _draw_star(draw, cx, cy, outer, inner, points, color):
    """在指定 Draw 对象上画一个实心星形。"""
    coords = []
    for i in range(points * 2):
        angle = math.pi / 2 - i * math.pi / points
        r = outer if i % 2 == 0 else inner
        coords.append((cx + math.cos(angle) * r, cy - math.sin(angle) * r))
    draw.polygon(coords, fill=color)


def _draw_teardrop(draw, cx, cy, w, h, color):
    """水滴形：圆形主体 + 向下尖尾。"""
    if h <= w:
        draw.ellipse([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], fill=color)
        return
    r = w / 2
    cy_c = cy - h / 2 + r
    draw.ellipse([cx - r, cy_c - r, cx + r, cy_c + r], fill=color)
    tip = (cx, cy + h / 2)
    coords = [(cx - r * 0.7, cy_c + r * 0.5),
              (cx + r * 0.7, cy_c + r * 0.5),
              tip]
    draw.polygon(coords, fill=color)


def _draw_trapezoid(draw, cx, cy, w, h, color):
    """等腰梯形，上底 w，下底 1.4w。"""
    bw = w * 1.4
    coords = [
        (cx - w / 2, cy - h / 2),
        (cx + w / 2, cy - h / 2),
        (cx + bw / 2, cy + h / 2),
        (cx - bw / 2, cy + h / 2),
    ]
    draw.polygon(coords, fill=color)


def _make_glint(spec):
    """根据样式中的高光描述生成一个 RGBA 高光图及其左上角粘贴坐标。"""
    shape = spec.get("shape", "round")
    x, y = spec["x"], spec["y"]
    w, h = spec.get("w", 8), spec.get("h", 8)
    color = spec.get("color", (255, 255, 255, 200))
    glow = spec.get("glow")
    glow_radius = spec.get("glow_radius", max(w, h))
    rotation = spec.get("rotation", 0)
    blur = spec.get("blur", 0.0)

    pad = int(max(w, h) * 1.2)
    tmp_w = w + pad * 2
    tmp_h = h + pad * 2
    tmp = Image.new("RGBA", (tmp_w, tmp_h), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tmp)
    tcx, tcy = tmp_w // 2, tmp_h // 2

    # 光晕（可选）
    if glow:
        gr = max(glow_radius, max(w, h) / 2)
        gh = int(gr * h / w) if w else gr
        if shape in ("round", "ellipse"):
            tdraw.ellipse([tcx - gr, tcy - gh, tcx + gr, tcy + gh], fill=glow)
        elif shape == "diamond":
            tdraw.polygon(_diamond_box(tcx, tcy, gr * 2, gh * 2), fill=glow)
        elif shape == "star":
            _draw_star(tdraw, tcx, tcy, gr, max(gr * 0.4, 2), 5, glow)
        elif shape == "trapezoid":
            _draw_trapezoid(tdraw, tcx, tcy, gr * 2, gh * 2, glow)
        elif shape == "teardrop":
            _draw_teardrop(tdraw, tcx, tcy, gr * 2, gh * 2, glow)

    # 核心形状
    if shape in ("round", "ellipse"):
        tdraw.ellipse([tcx - w / 2, tcy - h / 2, tcx + w / 2, tcy + h / 2], fill=color)
    elif shape == "diamond":
        tdraw.polygon(_diamond_box(tcx, tcy, w, h), fill=color)
    elif shape == "star":
        outer = max(w, h) / 2
        _draw_star(tdraw, tcx, tcy, outer, max(outer * 0.45, 1), 5, color)
    elif shape == "trapezoid":
        _draw_trapezoid(tdraw, tcx, tcy, w, h, color)
    elif shape == "teardrop":
        _draw_teardrop(tdraw, tcx, tcy, w, h, color)

    if blur:
        tmp = tmp.filter(ImageFilter.GaussianBlur(radius=blur))
    if rotation:
        tmp = tmp.rotate(rotation, resample=Image.Resampling.BILINEAR, expand=False)

    return tmp, (x - tmp_w // 2, y - tmp_h // 2)


def _init_cache(style=None):
    """预渲染眼睛各图层，避免每帧重复计算。"""
    global _CACHE, _CURRENT_STYLE

    if style is None:
        style = _CURRENT_STYLE if _CURRENT_STYLE is not None else get_style(DEFAULT_STYLE)

    # 样式变化时强制重建缓存
    if _CACHE and _CURRENT_STYLE is not None and _CURRENT_STYLE["name"] != style["name"]:
        _CACHE = {}

    if _CACHE:
        return

    style = _apply_style_defaults(style)
    _CURRENT_STYLE = style

    # 巩膜
    sclera = _radial_gradient(WIDTH, style["sclera_center"], style["sclera_edge"])
    if style.get("sclera_tint"):
        tint = Image.new("RGBA", (WIDTH, HEIGHT), (*style["sclera_tint"], 30))
        sclera = Image.alpha_composite(sclera.convert("RGBA"), tint).convert("RGB")

    # 巩膜球体感：左上暖高光 + 右下冷阴影（仅在浅色底时启用）
    sclera_shade = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    if style["enable_sclera_shade"]:
        sdraw = ImageDraw.Draw(sclera_shade)
        sdraw.ellipse([-30, -30, 100, 100], fill=(255, 255, 255, 45))
        sdraw.ellipse([60, 60, 200, 200], fill=(100, 100, 130, 35))
        sclera_shade = sclera_shade.filter(ImageFilter.GaussianBlur(radius=14))
    sclera = Image.alpha_composite(sclera.convert("RGBA"), sclera_shade).convert("RGB")

    # 巩膜暗角（增强球体边缘深度）
    vig = style.get("sclera_vignette")
    if vig:
        vig_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        vdraw = ImageDraw.Draw(vig_layer)
        vdraw.ellipse([10, 10, WIDTH - 10, HEIGHT - 10], outline=vig, width=14)
        vig_layer = vig_layer.filter(ImageFilter.GaussianBlur(radius=10))
        sclera = Image.alpha_composite(sclera.convert("RGBA"), vig_layer).convert("RGB")

    # 增强对比度/饱和度/亮度
    sclera = _enhance_rgb(sclera, style.get("vividness"))

    # 虹膜
    iris_base_size = int(WIDTH * style.get("iris_scale", 0.80))
    iris_aspect = style.get("iris_aspect", 1.0)
    if abs(iris_aspect - 1.0) < 0.02:
        iris_size = iris_base_size
    else:
        s = math.sqrt(iris_aspect)
        iris_w_tmp = int(iris_base_size * s)
        iris_h_tmp = int(iris_base_size / s)
        iris_size = max(iris_w_tmp, iris_h_tmp)
        iris_w = iris_w_tmp
        iris_h = iris_h_tmp
    if style["iris_gradient_mode"] == "vertical":
        iris = _vertical_gradient(iris_size, style["iris_top_color"], style["iris_bottom_color"])
    elif style["iris_gradient_mode"] == "radial_ring":
        iris = _radial_ring_gradient(
            iris_size,
            style["iris_edge"],
            style["iris_ring_color"],
            style["iris_edge"],
            style.get("iris_ring_radius", 0.40),
        style.get("iris_top_color"),
        style.get("iris_bottom_color"),
        0.75,  # vertical_blend (y-dependent: 0.75 at top, ~0.15 at bottom)
        )
    else:
        iris = _radial_gradient(iris_size, style["iris_center"], style["iris_edge"])

    # 虹膜边缘环（可选）
    limbal = style.get("limbal_ring")
    if limbal:
        idraw = ImageDraw.Draw(iris)
        # Gradient limbal: 2-tuple of (top_color, bottom_color)
        if isinstance(limbal, (list, tuple)) and len(limbal) == 2 and isinstance(limbal[0], (list, tuple)):
            tr, tg, tb = limbal[0][:3]
            br, bg, bb = limbal[1][:3]
            for i in range(36):
                a1 = i * 10.0
                a2 = (i + 1) * 10.0
                mid = math.radians((a1 + a2) / 2)
                t = (math.sin(mid) + 1) / 2
                clr = (int(tr + (br - tr) * t), int(tg + (bg - tg) * t), int(tb + (bb - tb) * t))
                idraw.arc([0, 0, iris_size - 1, iris_size - 1], a1, a2, fill=clr, width=2)
        else:
            # Draw limbal ring matching iris aspect ratio
            if abs(iris_aspect - 1.0) < 0.02:
                idraw.ellipse([0, 0, iris_size - 1, iris_size - 1], outline=limbal[:3] if len(limbal) >= 3 else limbal, width=2)
            else:
                cx = cy = iris_size // 2
                ew = max(2, iris_w)
                eh = max(2, iris_h)
                idraw.ellipse([cx - ew//2, cy - eh//2, cx + ew//2, cy + eh//2], outline=limbal[:3] if len(limbal) >= 3 else limbal, width=2)

    # Bottom iris rim highlight (blue-white arc at bottom edge)
    rim = style.get("iris_bottom_rim")
    if rim:
        rc = rim[:3] if len(rim) >= 3 else rim
        glow = Image.new("RGBA", (iris_size, iris_size), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.arc([1, 1, iris_size - 2, iris_size - 2], 10, 170, fill=(*rc, 30), width=24)
        gdraw.arc([3, 3, iris_size - 4, iris_size - 4], 20, 160, fill=(*rc, 60), width=14)
        gdraw.arc([5, 5, iris_size - 6, iris_size - 6], 35, 145, fill=(*rc, 130), width=5)
        glow = glow.filter(ImageFilter.GaussianBlur(radius=3.5))
        iris = Image.alpha_composite(iris.convert("RGBA"), glow).convert("RGB")

    # 增强虹膜鲜艳度与对比度
    iris = _enhance_rgb(iris, style.get("vividness"))

    # 虹膜投影（增强立体感）：比虹膜略大并做高斯模糊，避免边缘锐利
    iris_shadow = None
    iris_shadow_pad = 0
    if style.get("iris_shadow"):
        iris_shadow_pad = 14
        iris_aspect = style.get("iris_aspect", 1.0)
        s = math.sqrt(iris_aspect)
        iris_w = int(iris_base_size * s)
        iris_h = int(iris_base_size / s)
        shadow_size = max(iris_size + iris_shadow_pad * 2,
                          iris_w + iris_shadow_pad * 2,
                          iris_h + iris_shadow_pad * 2)
        iris_shadow = Image.new("RGBA", (shadow_size, shadow_size), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(iris_shadow)
        cx = cy = shadow_size // 2
        sdraw.ellipse(
            [cx - iris_w / 2, cy - iris_h / 2,
             cx + iris_w / 2, cy + iris_h / 2],
            fill=style["iris_shadow"],
        )
        iris_shadow = iris_shadow.filter(ImageFilter.GaussianBlur(radius=7))
        iris_shadow_offset = (shadow_size - iris_size) // 2
    else:
        iris_shadow_offset = 0

    # 瞳孔：支持正圆 / 竖椭圆猫眼 / 菱形
    pupil_diameter = int(iris_size * style["pupil_scale"])
    pupil_width = max(2, int(pupil_diameter * style["pupil_aspect"]))
    pupil_pad = 12
    pupil = Image.new(
        "RGBA", (pupil_diameter + pupil_pad * 2, pupil_diameter + pupil_pad * 2), (0, 0, 0, 0)
    )
    pdraw = ImageDraw.Draw(pupil)
    pupil_shape = style["pupil_shape"]
    pcx = pupil_diameter // 2 + pupil_pad
    pcy = pupil_diameter // 2 + pupil_pad

    if pupil_shape in ("cat", "ellipse"):
        px0 = pupil_pad + (pupil_diameter - pupil_width) // 2
        py0 = pupil_pad
        px1 = px0 + pupil_width
        py1 = pupil_pad + pupil_diameter
        pdraw.ellipse([(px0, py0), (px1, py1)], fill=style["pupil_fill"])
    elif pupil_shape == "diamond":
        pdraw.polygon(_diamond_box(pcx, pcy, pupil_width, pupil_diameter), fill=style["pupil_fill"])
    else:
        pdraw.ellipse(
            [(pupil_pad, pupil_pad),
             (pupil_pad + pupil_diameter, pupil_pad + pupil_diameter)],
            fill=style["pupil_fill"],
        )

    # 瞳孔描边（可选，用于插画感强的眼睛）
    outline = style.get("pupil_outline")
    if outline:
        ow = outline.get("width", 2)
        oc = outline.get("color", (0, 0, 0, 180))
        if pupil_shape in ("cat", "ellipse"):
            px0 = pupil_pad + (pupil_diameter - pupil_width) // 2
            py0 = pupil_pad
            px1 = px0 + pupil_width
            py1 = pupil_pad + pupil_diameter
            pdraw.ellipse([(px0, py0), (px1, py1)], outline=oc, width=ow)
        elif pupil_shape == "diamond":
            pdraw.polygon(_diamond_box(pcx, pcy, pupil_width, pupil_diameter), outline=oc, width=ow)
        else:
            pdraw.ellipse(
                [(pupil_pad, pupil_pad),
                 (pupil_pad + pupil_diameter, pupil_pad + pupil_diameter)],
                outline=oc, width=ow,
            )

    pupil = pupil.filter(ImageFilter.GaussianBlur(radius=style["pupil_blur"]))

    # IDLE 时更模糊的瞳孔（参考 HTML 失焦效果）
    pupil_idle = pupil.filter(ImageFilter.GaussianBlur(radius=2.5))

    # 虹膜纤维纹理：交替颜色放射线 + 轻微模糊
    iris_fibers = Image.new("RGBA", (iris_size, iris_size), (0, 0, 0, 0))
    if style["iris_pattern"] == "fibers":
        fdraw = ImageDraw.Draw(iris_fibers)
        icx = icy = iris_size // 2
        for i, angle in enumerate(range(0, 360, 5)):
            rad = math.radians(angle)
            fx = icx + math.cos(rad) * (iris_size * 0.42)
            fy = icy + math.sin(rad) * (iris_size * 0.42)
            color = style["fiber_color_a"] if i % 2 == 0 else style["fiber_color_b"]
            fdraw.line([(icx, icy), (fx, fy)], fill=color, width=1)
        iris_fibers = iris_fibers.filter(ImageFilter.GaussianBlur(radius=0.8))
        # Boost fiber visibility across all styles
        r, g, b, a = iris_fibers.split()
        a = a.point(lambda i: min(255, int(i * 1.8)))
        iris_fibers = Image.merge("RGBA", (r, g, b, a))

    # 高光：按样式描述逐个生成
    glint = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    for gspec in style.get("glints", []):
        gimg, pos = _make_glint(gspec)
        glint.paste(gimg, pos, gimg.split()[3])

    # 眼型遮罩：支持正圆 / 椭圆 / 鸡蛋形
    eye_shape = style.get("eye_shape", "circle")
    if eye_shape == "ellipse":
        eye_mask = _ellipse_mask(WIDTH, style["eye_width"], style["eye_height"])
    elif eye_shape == "egg":
        eye_mask = _egg_mask(WIDTH, style["eye_width"], style["eye_height"], asym=style.get("eye_asym", 0.18))
    else:
        eye_mask = _circle_mask(WIDTH)

    # 虹膜遮罩：支持正圆 / 椭圆，使虹膜形状与瞳孔/眼型一致
    iris_aspect = style.get("iris_aspect", 1.0)
    if abs(iris_aspect - 1.0) < 0.02:
        iris_mask = _circle_mask(iris_size)
    else:
        s = math.sqrt(iris_aspect)
        iris_w = int(iris_base_size * s)
        iris_h = int(iris_base_size / s)
        iris_mask = _ellipse_mask(iris_size, iris_w, iris_h)

    _CACHE = {
        "sclera": sclera,
        "iris": iris,
        "iris_fibers": iris_fibers,
        "iris_shadow": iris_shadow,
        "iris_shadow_offset": iris_shadow_offset,
        "pupil": pupil,
        "pupil_idle": pupil_idle,
        "glint": glint,
        "eye_mask": eye_mask,
        "circle_mask": _circle_mask(WIDTH),
        "iris_mask": iris_mask,
        "iris_size": iris_size,
        "iris_offset_y": style.get("iris_offset_y", 0),
        "pupil_diameter": pupil_diameter,
        "pupil_pad": pupil_pad,
    }


# ============================================================
# 绘图函数
# ============================================================
def draw_eye(
    gaze_x,
    gaze_y,
    eyelid=0,
    mirror=False,
    pupil_relative_scale=1.0,
    glint_jitter_x=0.0,
    glint_jitter_y=0.0,
    vergence_x=0,
    is_idle=False,
    glint_scale=1.0,
    iris_scale=1.0,
    eyelid_tilt=0,
    eyelid_target_y=None,
    eyelid_flatten=0,
    show_lower_eyelid=True,
    eyelid_close_y=None,
    lower_eyelid_cup=False,
    lower_eyelid_tilt=0,
    cry_glint_data=None,
    style=None,
):
    """
    绘制一帧眼睛图像。

    gaze_x/y: -1 ~ 1
    eyelid: 上眼睑像素高度，0 为完全睁开
    mirror: 是否水平镜像（右眼）
    pupil_relative_scale: 瞳孔相对基准的缩放
    glint_jitter_x/y: 高光高频微颤偏移
    vergence_x: 辐辏偏移（当前关闭为 0）
    is_idle: 是否处于 IDLE 模式（瞳孔更糊）
    glint_scale: 高光整体缩放（缩瞳时随瞳孔同步缩小），1.0 = 原始大小
    iris_scale: 虹膜整体缩放（缩瞳时虹膜同步缩小），1.0 = 原始大小
    style: 眼睛样式配置（None 则使用当前全局样式）
    eyelid_close_y: 覆盖眼睑汇合位置（默认 CLOSE_Y=107），传入较小值（如 50）使眼睑在更上方合并
    lower_eyelid_cup: 下眼睑使用向上凸（U）形弧线（默认 False，即原始向下凸的 ∩ 形）
    """
    _init_cache(style)
    cx, cy = WIDTH // 2, HEIGHT // 2

    # 虹膜/瞳孔位置镜像（右眼），但高光不镜像
    gx = -gaze_x if mirror else gaze_x
    move_x = int(gx * 31 + vergence_x)
    move_y = int(gaze_y * 31 * 0.65)

    # 基础画布
    eye = _CACHE["sclera"].copy()

    # ---- 眼睑参数预计算（用于控制虹膜/瞳孔/高光的绘制） ----
    eyelid_param = max(0, min(120, eyelid))
    skip_eye_content = eyelid_param >= 115

    # 虹膜位置
    iris_size = _CACHE["iris_size"]
    iris_x = cx - iris_size // 2 + move_x
    iris_y = cy - iris_size // 2 + move_y + _CACHE.get("iris_offset_y", 0)

    # 虹膜投影（画在虹膜下方，增强立体感；用自身 alpha 保证边缘柔和）
    iris_shadow = _CACHE.get("iris_shadow")
    if iris_shadow is not None and not skip_eye_content:
        so = _CACHE.get("iris_shadow_offset", 0)
        eye.paste(
            iris_shadow,
            (iris_x - so + 3, iris_y - so + 4),
            iris_shadow.split()[3],
        )

    # 虹彩缩放（缩瞳时虹膜同步缩小）
    if not skip_eye_content:
        _iris_layer = _CACHE["iris"]
        _iris_fibers = _CACHE["iris_fibers"]
        _iris_mask = _CACHE["iris_mask"]
        _iris_use_size = iris_size
        _iris_x = iris_x
        _iris_y = iris_y
        if abs(iris_scale - 1.0) > 0.001:
            new_sz = max(4, int(round(iris_size * iris_scale)))
            # 合并虹膜+遮罩为 RGBA 再缩放，消除边缘走样
            _iris_rgba = Image.new("RGBA", (iris_size, iris_size), (0, 0, 0, 0))
            _iris_rgba.paste(_CACHE["iris"], (0, 0), _CACHE["iris_mask"])
            _iris_rgba = _iris_rgba.resize((new_sz, new_sz), Image.Resampling.LANCZOS)
            _iris_layer = _iris_rgba.convert("RGB")
            _iris_mask = _iris_rgba.split()[3]
            _iris_fibers = _CACHE["iris_fibers"].resize((new_sz, new_sz), Image.Resampling.LANCZOS)
            off = (iris_size - new_sz) // 2
            _iris_x = iris_x + off
            _iris_y = iris_y + off
            _iris_use_size = new_sz
        eye.paste(_iris_layer, (_iris_x, _iris_y), _iris_mask)
    # 纤维纹理叠加
    if not skip_eye_content:
        eye.paste(
            _iris_fibers,
            (_iris_x, _iris_y),
            _iris_fibers.split()[3],
        )

    # 瞳孔：按 pupil_relative_scale 缩放，用 BILINEAR 降低 CPU
    if not skip_eye_content:
        base_d = _CACHE["pupil_diameter"]
        pad = _CACHE["pupil_pad"]
        new_d = max(2, int(round(base_d * pupil_relative_scale)))
        new_size = new_d + pad * 2

        pupil_src = _CACHE["pupil_idle"] if is_idle else _CACHE["pupil"]
        pupil = pupil_src.resize(
            (new_size, new_size), Image.Resampling.BILINEAR
        )
        # 用包含 pad 的图层尺寸来居中，确保瞳孔与虹膜同心
        pupil_x = _iris_x + (_iris_use_size - new_size) // 2
        pupil_y = _iris_y + (_iris_use_size - new_size) // 2
        eye.paste(pupil, (pupil_x, pupil_y), pupil.split()[3])

    # 高光跟随瞳孔/虹膜移动
    if not skip_eye_content:
        glint_x = int(round(move_x * 0.75 + glint_jitter_x))
        glint_y = int(round(move_y * 0.75 + glint_jitter_y))
        glint = _CACHE["glint"]
        if abs(glint_scale - 1.0) > 0.001:
            new_w = max(8, int(round(WIDTH * glint_scale)))
            new_h = max(8, int(round(HEIGHT * glint_scale)))
            glint = glint.resize((new_w, new_h), Image.Resampling.BILINEAR)
            dx = (WIDTH - new_w) // 2
            dy = (HEIGHT - new_h) // 2
            eye.paste(glint, (glint_x + dx, glint_y + dy), glint.split()[3])
        else:
            eye.paste(glint, (glint_x, glint_y), glint.split()[3])

    # 动态泪珠高光（哭表情）：在眼睑裁剪前绘制，使之下眼睑自然覆盖
    if cry_glint_data is not None and len(cry_glint_data) > 0:
        gdraw = ImageDraw.Draw(eye)
        for g in cry_glint_data:
            gx, gy, gsize = g["x"], g["y"], g["size"]
            rot = g.get("rotation", 0.0)
            # 单个旋转椭圆泪珠
            pad = int(gsize * 0.8) + 2
            alpha = g.get("alpha", 255)
            tmp = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
            tdraw = ImageDraw.Draw(tmp)
            ew = gsize * 0.95
            eh = gsize * 0.92
            tdraw.ellipse([
                pad - ew / 2, pad - eh / 2,
                pad + ew / 2, pad + eh / 2,
            ], fill=(255, 255, 255, alpha))
            cw = ew * 0.5
            ch = eh * 0.5
            tdraw.ellipse([
                pad - cw / 2, pad - ch / 2,
                pad + cw / 2, pad + ch / 2,
            ], fill=(255, 255, 255, alpha))
            rot_deg = math.degrees(rot)
            rotated = tmp.rotate(-rot_deg, resample=Image.Resampling.BILINEAR, expand=False)
            eye.paste(rotated, (int(gx - pad), int(gy - pad)), rotated.split()[3])

    # 眼型裁剪（圆 / 椭圆 / 鸡蛋形）（圆 / 椭圆 / 鸡蛋形）
    eye.putalpha(_CACHE.get("eye_mask", _CACHE["circle_mask"]))
            # ---- 眼睑弧线系统 ----
    # 上眼睑：? 形朝上 → 经过巩膜中线后翻转成 ? 朝下
    # 下眼睑：始终 ? 形，从底部向上移动
    # 两端固定在巩膜左右边缘 (0,80) 和 (160,80)，闭眼时汇合
    eyelid_param_clamped = max(0, min(120, eyelid_param))
    lid_color = (55, 28, 10, 255)  # 深棕色
    END_Y = 80.0  # 巩膜左右边缘 y 坐标
    CLOSE_Y = 107.0  # 闭眼时上下眼睑汇合位置（略低于中心线，形成自然向下弧线）
    close_y = float(eyelid_close_y) if eyelid_close_y is not None else CLOSE_Y

    # 上眼睑：顶点从 0（巩膜顶部）线性下移到 CLOSE_Y（闭眼位置）
    # 当 apex_y < 80 时为 ?（∪ 形朝上），> 80 时为 ?（∩ 形朝下）
    if eyelid_target_y is not None:
        upper_apex_y = eyelid_param_clamped * (float(eyelid_target_y) / 120.0)
    else:
        upper_apex_y = eyelid_param_clamped * (close_y / 120.0)

    # 下眼睑：顶点从 160（巩膜底部）线性上移到 CLOSE_Y（闭眼位置）
    # 始终保持 ? 形（∩ 朝上）
    if lower_eyelid_cup:
        # 从∩形（向下凸→apex≥80）过渡到∪形（向上凸→apex≤80）：
        # 用加速曲线（sqrt）使得在行程中部眼睑已经翻转为∪，
        # 与上眼睑弧度方向一致，最后两条弧线汇合闭合。
        progress = eyelid_param_clamped / 120.0
        accelerated = progress ** 0.5
        lower_apex_y = 160.0 - accelerated * (160.0 - close_y)
    else:
        # ∩ 形（向下凸），从巩膜底部（y=160）线性向上移动到 close_y
        lower_apex_y = 160.0 - eyelid_param_clamped * ((160.0 - close_y) / 120.0)

    def _circle_arc_pts(apex_y):
        # 用与巩膜同圆的圆弧点，从 (0,80) 到 (160,80) 经过 (80, apex_y)
        if abs(apex_y - 80.0) < 0.5:
            return [(x, 80) for x in range(0, WIDTH + 1)]
        c = (12800.0 - apex_y * apex_y) / (2.0 * (80.0 - apex_y))
        r = abs(apex_y - c)
        is_cup = apex_y < 80.0
        pts = []
        for x in range(0, WIDTH + 1):
            dx = x - 80.0
            if abs(dx) <= r:
                sqrt_term = math.sqrt(r * r - dx * dx)
                y = c - sqrt_term if is_cup else c + sqrt_term
                pts.append((x, int(round(y))))
            else:
                pts.append((x, 80))
        return pts
    # 生成上下眼睭弧线
    upper_pts = _circle_arc_pts(upper_apex_y)

    # 上眼睑展平：从弧线向直线插值
    if eyelid_flatten > 0 and eyelid_param_clamped > 0:
        flat_pts = []
        for x, y in upper_pts:
            y_flat = y * (1.0 - eyelid_flatten) + 80.0 * eyelid_flatten
            flat_pts.append((x, int(round(y_flat))))
        upper_pts = flat_pts

    lower_pts = _circle_arc_pts(lower_apex_y)

    # 上眼睑倾斜（生气表情）：左高右低
    if eyelid_tilt != 0 and eyelid_param_clamped > 0:
        tilted = []
        for x, y in upper_pts:
            tf = (x - 80.0) / 80.0
            y_adj = tf * eyelid_tilt
            tilted.append((x, int(round(y + y_adj))))
        upper_pts = tilted

    # 下眼睑倾斜：让左右两端跟随上眼睑（逆时针旋转）
    if lower_eyelid_tilt != 0 and eyelid_param_clamped > 0:
        lower_tilted = []
        for x, y in lower_pts:
            tf = (x - 80.0) / 80.0
            y_adj = tf * lower_eyelid_tilt
            lower_tilted.append((x, int(round(y + y_adj))))
        lower_pts = lower_tilted

    # ---- 根据上下眼睭弧线裁剪 ----
    # 上眼睭以上和下眼睭以下的区域隐藏虹膜/瞥孔/高光，巩膜保持白色可见
    if eyelid_param_clamped > 0:
        clip = Image.new("L", (WIDTH, HEIGHT), 0)  # 全透明
        cdraw = ImageDraw.Draw(clip)
        # 上下眼睭之间的多边形区域设为白色（显示内容）
        if show_lower_eyelid:
            polygon = upper_pts + list(reversed(lower_pts))
        else:
            polygon = upper_pts + [(WIDTH, HEIGHT), (0, HEIGHT)]
        cdraw.polygon(polygon, fill=255)
        # 将眼睭之间的可见蒙版应用到眼睛内容
        # 应用弧形裁剪：眼睭之外透明（显示白色背景）
        eye.putalpha(ImageChops.multiply(eye.split()[3], clip))
    # ---- 绘制眼睭弧线 ----
    lashes = _CURRENT_STYLE.get("eyelashes") if _CURRENT_STYLE else None
    if eyelid_param_clamped > 0:
        edraw = ImageDraw.Draw(eye)

        # 先画下眼睭（细），再画上眼睭（粗），闭眼时上眼睭盖住下眼睭
        if show_lower_eyelid:
            edraw.line(lower_pts, fill=(120, 75, 40, 255), width=2)  # 下眼睭浅棕色
        # 上眼睭：多边形填充实现平滑变宽
        if abs(upper_apex_y - 80.0) < 0.5 or eyelid_tilt != 0 or eyelid_flatten > 0:
            # 用硬边多边形替代 line()，避免抗锯齿产生过渡色
            poly = []
            for x, y in upper_pts:
                half_w = 2 + 3 * (1 - ((x - 80.0) / 80.0) ** 2)
                poly.append((x, int(round(y - half_w))))
            for x, y in reversed(upper_pts):
                half_w = 2 + 3 * (1 - ((x - 80.0) / 80.0) ** 2)
                poly.append((x, int(round(y + half_w))))
            edraw.polygon(poly, fill=lid_color)
        else:
            outer, inner = [], []
            for i, (x, y) in enumerate(upper_pts):
                dd = (x - 80.0) / 80.0
                hw = (2.0 + 8.0 * (1.0 - dd * dd)) / 2.0
                # 局部差分计算曲线法线（支持任意形状，不依赖圆弧中心）
                if i == 0:
                    dx_t, dy_t = upper_pts[i+1][0] - x, upper_pts[i+1][1] - y
                elif i == len(upper_pts) - 1:
                    dx_t, dy_t = x - upper_pts[i-1][0], y - upper_pts[i-1][1]
                else:
                    dx_t, dy_t = upper_pts[i+1][0] - upper_pts[i-1][0], upper_pts[i+1][1] - upper_pts[i-1][1]
                ln = math.hypot(dx_t, dy_t) or 1
                nx, ny = dy_t / ln, -dx_t / ln
                outer.append((int(round(x + nx * hw)), int(round(y + ny * hw))))
                inner.append((int(round(x - nx * hw)), int(round(y - ny * hw))))
            edraw.polygon(outer + list(reversed(inner)), fill=lid_color)
    if lashes:
        lash_draw = ImageDraw.Draw(eye)
        # 睫毛跟随上眼睑线：以睁眼弧线（apex=0、无倾斜/展平）为基准，
        # 每个睫毛点按当前上眼睑在该 x 处的垂直位移平移，表情/眨眼时贴着眼睑走。
        upper_pts_open = _circle_arc_pts(0.0)

        def _lid_delta(x):
            xi = max(0, min(WIDTH, int(round(x))))
            return upper_pts[xi][1] - upper_pts_open[xi][1]

        for lash in lashes:
            sx, sy = lash["start"]
            ex, ey = lash["end"]
            lash_draw.line(
                [(sx, sy + _lid_delta(sx)), (ex, ey + _lid_delta(ex))],
                fill=lash.get("color", (0, 0, 0, 160)),
                width=lash.get("width", 1),
            )

    return eye
