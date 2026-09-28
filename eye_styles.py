"""
眼睛样式库 (Eye Style Library)

定义多组可切换的眼睛配色与瞳孔形状，便于在不同硬件部署时快速切换。

命名规范：
  - 样式名采用 snake_case
  - 颜色统一为 RGB tuple；需要透明通道的位置用 RGBA
  - 瞳孔形状支持 "round"（正圆）、"cat"/"ellipse"（竖椭圆猫眼）、"diamond"（菱形）
  - 虹膜渐变支持 "radial"（径向）、"vertical"（上下垂直）
  - 高光列表 `glints` 支持形状：round / ellipse / diamond / star / trapezoid / teardrop
"""

# ============================================================
# 通用默认参数与样式工厂
# ============================================================

def make_style(name, description, **overrides):
    """使用一组默认值快速创建眼睛样式，减少重复参数。"""
    defaults = {
        "sclera_center": (255, 255, 255),
        "sclera_edge": (154, 154, 168),
        "iris_center": (67, 130, 223),
        "iris_edge": (5, 17, 24),
        "iris_gradient_mode": "vertical",
        "iris_top_color": None,
        "iris_bottom_color": None,
        "iris_ring_color": None,
        "iris_ring_radius": 0.40,
        "iris_bottom_rim": None,
        "iris_pattern": "fibers",
        "iris_scale": 0.80,
        "iris_aspect": 1.0,
        "iris_offset_y": 0,
        "iris_shadow": (0, 0, 0, 55),
        "fiber_color_a": (25, 55, 90, 28),
        "fiber_color_b": (40, 80, 120, 20),
        "pupil_fill": (5, 5, 5, 245),
        "pupil_shape": "round",
        "pupil_scale": 0.68,
        "pupil_aspect": 1.0,
        "pupil_blur": 3.0,
        "pupil_outline": None,
        "enable_sclera_shade": True,
        "sclera_tint": None,
        "sclera_vignette": (30, 30, 40, 85),
        "limbal_ring": None,
        "eye_shape": "circle",
        "eye_width": 160,
        "eye_height": 160,
        "eye_asym": 0.18,
        "vividness": {"contrast": 1.15, "saturation": 1.30, "brightness": 1.02},
        "eyelashes": None,
        "glints": [
            {"shape": "round", "x": 96, "y": 98, "w": 28, "h": 26,
             "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
            {"shape": "ellipse", "x": 96, "y": 98, "w": 8, "h": 8,
             "color": (255, 255, 255, 130), "rotation": -40, "blur": 1.5},
            {"shape": "ellipse", "x": 96, "y": 120, "w": 14, "h": 4,
             "color": (255, 255, 255, 90), "rotation": -35, "blur": 1.0},
        ],
    }
    style = {**defaults, "name": name, "description": description, **overrides}
    # 垂直渐变若未指定上下色，默认上深下浅
    if style.get("iris_gradient_mode") == "vertical":
        if style.get("iris_top_color") is None:
            style["iris_top_color"] = style["iris_edge"]
        if style.get("iris_bottom_color") is None:
            style["iris_bottom_color"] = style["iris_center"]
    if style.get("iris_gradient_mode") == "radial_ring":
        if style.get("iris_ring_color") is None:
            style["iris_ring_color"] = style["iris_center"]
    return style


# ============================================================
# 已验证的优秀配色方案（硬件默认）
# ============================================================

# pi2 默认：浅色底 + 蓝色虹膜 + 正圆瞳孔
# 特点：清新、科技感、与人脸追踪搭配友好
PI2_LIGHT_BLUE = {
    "name": "pi2_light_blue",
    "description": "浅色底蓝色虹膜圆眼 (pi2 默认)",
    "iris_gradient_mode": "vertical",
    "iris_top_color": (10, 25, 50),
    "iris_bottom_color": (67, 130, 223),
    "sclera_center": (255, 255, 255),
    "sclera_edge": (154, 154, 168),
    "iris_center": (67, 130, 223),
    "iris_edge": (5, 17, 24),
    "fiber_color_a": (25, 55, 90, 28),
    "fiber_color_b": (40, 80, 120, 20),
    "pupil_fill": (5, 5, 5, 245),
    "pupil_shape": "round",
    "pupil_scale": 0.68,
    "pupil_aspect": 1.0,
    "enable_sclera_shade": True,
}

# pi 深色荧光绿猫眼
# 特点：高对比、神秘、夜间/暗光效果好
PI_DARK_CAT_GREEN = {
    "name": "pi_dark_cat_green",
    "description": "深色荧光绿猫眼 (pi 默认)",
    "iris_gradient_mode": "vertical",
    "iris_top_color": (2, 45, 2),
    "iris_bottom_color": (6, 255, 0),
    "sclera_center": (0, 0, 0),
    "sclera_edge": (0, 0, 0),
    "iris_center": (6, 255, 0),
    "iris_edge": (255, 228, 0),
    "fiber_color_a": (182, 245, 0, 28),
    "fiber_color_b": (182, 245, 0, 20),
    "pupil_fill": (0, 0, 0, 245),
    "pupil_shape": "cat",
    "pupil_scale": 0.75,
    "pupil_aspect": 0.66,
    "enable_sclera_shade": False,
}

# 备选：深色底 + 橙色虹膜 + 金黄纹理 + 圆瞳
# 之前调试中效果也很好的暖色版本
PI_DARK_ORANGE = {
    "name": "pi_dark_orange",
    "description": "深色底橙虹膜圆眼",
    "iris_gradient_mode": "vertical",
    "iris_top_color": (50, 20, 5),
    "iris_bottom_color": (254, 127, 45),
    "sclera_center": (0, 0, 0),
    "sclera_edge": (0, 0, 0),
    "iris_center": (254, 127, 45),
    "iris_edge": (213, 62, 15),
    "fiber_color_a": (255, 191, 0, 28),
    "fiber_color_b": (255, 191, 0, 20),
    "pupil_fill": (0, 0, 0, 245),
    "pupil_shape": "round",
    "pupil_scale": 0.55,
    "pupil_aspect": 1.0,
    "enable_sclera_shade": False,
}

# 备选：浅色底 + 绿色虹膜 + 圆瞳
# 清新自然风格，可作为 pi2 的替代方案
PI2_LIGHT_GREEN = {
    "name": "pi2_light_green",
    "description": "浅色底绿色虹膜圆眼",
    "iris_gradient_mode": "vertical",
    "iris_top_color": (8, 40, 20),
    "iris_bottom_color": (34, 197, 94),
    "sclera_center": (255, 255, 255),
    "sclera_edge": (154, 154, 168),
    "iris_center": (34, 197, 94),
    "iris_edge": (20, 83, 45),
    "fiber_color_a": (132, 204, 22, 28),
    "fiber_color_b": (163, 230, 53, 20),
    "pupil_fill": (5, 5, 5, 245),
    "pupil_shape": "round",
    "pupil_scale": 0.68,
    "pupil_aspect": 1.0,
    "enable_sclera_shade": True,
}


# ============================================================
# 调研图复刻风格（lit_scan_images）
# ============================================================

P1_JINGDIAN = make_style(
    "p1_jingdian",
    "Page1 经典：真诚无攻击性的蓝绿圆眼",
    iris_gradient_mode="radial_ring",
    iris_ring_color=(80, 200, 255),
    iris_ring_radius=0.35,
    limbal_ring=((1, 3, 8), (55, 120, 175)),
    sclera_center=(255, 255, 255),
    iris_top_color=(1, 3, 8),
    iris_bottom_color=(60, 150, 210),
    iris_bottom_rim=(180, 230, 255),
    sclera_edge=(154, 154, 168),
    iris_center=(60, 150, 210),
    iris_edge=(5, 20, 35),
    fiber_color_a=(120, 235, 255, 48),
    fiber_color_b=(70, 190, 230, 36),
    pupil_shape="round",
    pupil_scale=0.65,
    glints=[
        {"shape": "round", "x": 62, "y": 54, "w": 24, "h": 22,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "round", "x": 108, "y": 110, "w": 8, "h": 8,
         "color": (255, 255, 255, 200), "glow": (255, 255, 255, 35), "glow_radius": 10},
    ],
)

P1_YUANYUAN = make_style(
    "p1_yuanyuan",
    "Page1 圆圆：活泼俏皮的橘色圆眼",
    iris_gradient_mode="vertical",
    iris_top_color=(60, 25, 8),
    iris_bottom_color=(255, 150, 50),
    sclera_center=(255, 255, 255),
    sclera_edge=(170, 165, 165),
    iris_center=(255, 150, 50),
    iris_edge=(180, 70, 10),
    fiber_color_a=(255, 200, 60, 30),
    fiber_color_b=(255, 170, 40, 22),
    pupil_shape="round",
    glints=[
        {"shape": "teardrop", "x": 62, "y": 52, "w": 14, "h": 22,
         "color": (255, 255, 255, 250), "rotation": 45, "blur": 0.8},
        {"shape": "round", "x": 108, "y": 110, "w": 8, "h": 8,
         "color": (255, 255, 255, 200), "glow": (255, 255, 255, 35), "glow_radius": 10},
    ],
)

P1_YONGYONG = make_style(
    "p1_yongyong",
    "Page1 勇勇：成熟智慧的棕黄椭圆眼",
    iris_gradient_mode="vertical",
    iris_top_color=(35, 20, 8),
    iris_bottom_color=(140, 100, 50),
    sclera_center=(255, 255, 240),
    sclera_edge=(195, 185, 160),
    iris_center=(140, 100, 50),
    iris_edge=(60, 40, 15),
    fiber_color_a=(200, 160, 80, 30),
    fiber_color_b=(180, 140, 60, 22),
    pupil_shape="ellipse",
    pupil_scale=0.63,
    pupil_aspect=0.78,
    iris_aspect=0.92,
    limbal_ring=(25, 12, 5),
    glints=[
       {"shape": "ellipse", "x": 80, "y": 38, "w": 26, "h": 10,
        "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "round", "x": 108, "y": 110, "w": 8, "h": 8, "color": (255, 255, 255, 170)},
       {"shape": "round", "x": 44, "y": 78, "w": 3, "h": 3, "color": (255, 255, 255, 150)},
        {"shape": "round", "x": 32, "y": 90, "w": 3, "h": 3, "color": (255, 255, 255, 130)},
    ],
)

P1_ROUROU = make_style(
    "p1_rourou",
    "Page1 柔柔：温柔脆弱的红粉圆眼",
    sclera_center=(255, 245, 245),
    sclera_edge=(205, 170, 170),
    iris_center=(180, 80, 90),
    iris_edge=(90, 40, 50),
    fiber_color_a=(220, 120, 130, 26),
    fiber_color_b=(240, 150, 160, 18),
    pupil_shape="round",
    pupil_scale=0.62,
    glints=[
        {"shape": "round", "x": 56, "y": 48, "w": 18, "h": 16,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "round", "x": 72, "y": 42, "w": 12, "h": 12,
         "color": (255, 255, 255, 230)},
        {"shape": "round", "x": 68, "y": 58, "w": 8, "h": 8,
         "color": (255, 255, 255, 200)},
        {"shape": "round", "x": 44, "y": 68, "w": 8, "h": 8,
         "color": (255, 255, 255, 200)},
        {"shape": "round", "x": 106, "y": 108, "w": 4, "h": 4,
         "color": (255, 255, 255, 150)},
        {"shape": "round", "x": 74, "y": 116, "w": 4, "h": 4,
         "color": (255, 255, 255, 160)},
        {"shape": "round", "x": 88, "y": 116, "w": 4, "h": 4,
         "color": (255, 255, 255, 160)},
    ],
)

P1_YOUYOU = make_style(
    "p1_youyou",
    "Page1 悠悠：宁静思考的墨绿椭圆眼",
    iris_gradient_mode="vertical",
    iris_top_color=(10, 60, 40),
    iris_bottom_color=(60, 160, 100),
    sclera_center=(245, 255, 250),
    sclera_edge=(150, 170, 160),
    fiber_color_a=(30, 110, 70, 28),
    fiber_color_b=(50, 150, 100, 20),
    pupil_shape="ellipse",
    pupil_scale=0.66,
    pupil_aspect=0.85,
    iris_aspect=0.95,
    glints=[
       {"shape": "ellipse", "x": 64, "y": 52, "w": 12, "h": 18,
         "color": (255, 255, 255, 250), "rotation": 45, "blur": 0.8},
    ],
)

P2_SHASHA = make_style(
    "p2_shasha",
    "Page2 沙沙：震惊呆愣的深蓝小圆眼",
    sclera_center=(230, 240, 255),
    sclera_edge=(130, 150, 180),
    iris_scale=0.55,
    iris_center=(50, 60, 80),
    iris_edge=(10, 15, 25),
    fiber_color_a=(70, 90, 120, 26),
    fiber_color_b=(90, 110, 140, 18),
    pupil_shape="round",
    pupil_scale=0.45,
    glints=[
        {"shape": "round", "x": 60, "y": 60, "w": 8, "h": 8, "color": (255, 255, 255, 250)},
        {"shape": "round", "x": 96, "y": 98, "w": 8, "h": 8, "color": (200, 210, 230, 140)},
        {"shape": "round", "x": 86, "y": 70, "w": 2, "h": 2, "color": (200, 210, 230, 120)},
        {"shape": "round", "x": 78, "y": 80, "w": 2, "h": 2, "color": (200, 210, 230, 120)},
        {"shape": "round", "x": 88, "y": 82, "w": 2, "h": 2, "color": (200, 210, 230, 100)},
        {"shape": "round", "x": 74, "y": 84, "w": 2, "h": 2, "color": (200, 210, 230, 100)},
    ],
)

P2_SHUOSHUO = make_style(
    "p2_shuoshuo",
    "Page2 烁烁：神秘疏离的紫蓝星芒眼",
    iris_gradient_mode="vertical",
    iris_top_color=(120, 70, 170),
    iris_bottom_color=(50, 110, 180),
    sclera_center=(225, 235, 255),
    sclera_edge=(130, 145, 180),
    fiber_color_a=(140, 100, 200, 26),
    fiber_color_b=(100, 130, 210, 18),
    pupil_shape="ellipse",
    pupil_scale=0.58,
    pupil_aspect=0.80,
    iris_aspect=0.92,
    iris_bottom_rim=(80, 120, 160),
    glints=[
        {"shape": "star", "x": 64, "y": 52, "w": 28, "h": 28,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "star", "x": 96, "y": 98, "w": 8, "h": 8, "color": (255, 255, 255, 150)},
        {"shape": "star", "x": 90, "y": 84, "w": 5, "h": 5, "color": (255, 255, 255, 130)},
    ],
)

P2_GUGU = make_style(
    "p2_gugu",
    "Page2 咕咕：失焦无害的无高光绿黄眼",
    iris_gradient_mode="vertical",
    iris_top_color=(30, 60, 30),
    iris_bottom_color=(140, 130, 40),
    sclera_center=(255, 255, 255),
    sclera_edge=(170, 170, 170),
    iris_scale=0.58,
    fiber_color_a=(60, 100, 50, 24),
    fiber_color_b=(120, 110, 60, 18),
    pupil_shape="diamond",
    pupil_scale=0.52,
    pupil_aspect=0.70,
    pupil_blur=2.5,
    glints=[],
)

P2_XIXI = make_style(
    "p2_xixi",
    "Page2 淅淅：温柔潮湿的翠绿圆眼",
    iris_gradient_mode="vertical",
    iris_top_color=(15, 70, 60),
    iris_bottom_color=(50, 170, 100),
    sclera_center=(235, 250, 245),
    sclera_edge=(150, 170, 160),
    fiber_color_a=(30, 130, 80, 28),
    fiber_color_b=(70, 160, 110, 20),
    pupil_shape="round",
    pupil_scale=0.58,
    glints=[
        {"shape": "teardrop", "x": 62, "y": 52, "w": 12, "h": 20,
         "color": (255, 255, 255, 250), "rotation": 45, "blur": 0.8},
        {"shape": "round", "x": 108, "y": 110, "w": 8, "h": 8,
         "color": (255, 255, 255, 200), "glow": (255, 255, 255, 35), "glow_radius": 10},
    ],
)

P2_SISI = make_style(
    "p2_sisi",
    "Page2 思思：安静专注的墨绿圆眼",
    iris_gradient_mode="vertical",
    iris_top_color=(10, 40, 20),
    iris_bottom_color=(22, 85, 45),
    iris_center=(16, 60, 30),
    iris_edge=(6, 22, 11),
    iris_bottom_rim=(16, 60, 32),
   sclera_center=(255, 255, 255),
    sclera_edge=(160, 165, 165),
    fiber_color_a=(20, 65, 35, 26),
    fiber_color_b=(10, 35, 18, 18),
   pupil_shape="round",
    pupil_scale=0.60,
    glints=[
        {"shape": "round", "x": 62, "y": 52, "w": 8, "h": 8,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "ellipse", "x": 96, "y": 98, "w": 8, "h": 8,
         "color": (150, 200, 255, 120), "rotation": 10, "blur": 1.0},
    ],
)

P2_LINLIN = make_style(
    "p2_linlin",
    "Page2 凛凛：机械冷感的湖蓝圆眼",
    sclera_center=(220, 235, 255),
    sclera_edge=(120, 140, 170),
    iris_center=(30, 100, 160),
    iris_edge=(5, 15, 40),
    fiber_color_a=(60, 130, 190, 28),
    fiber_color_b=(40, 100, 160, 20),
    pupil_shape="round",
    pupil_scale=0.62,
    limbal_ring=(5, 20, 50),
    glints=[
        {"shape": "ellipse", "x": 62, "y": 52, "w": 16, "h": 5,
         "color": (255, 255, 255, 250), "rotation": -45, "blur": 0.8},
    ],
)

P2_MENGMENG = make_style(
    "p2_mengmeng",
    "Page2 梦梦：梦幻童话的紫色星芒眼",
    iris_gradient_mode="vertical",
    iris_top_color=(140, 80, 190),
    iris_bottom_color=(80, 130, 210),
    sclera_center=(220, 240, 255),
    sclera_edge=(150, 170, 200),
    fiber_color_a=(180, 120, 220, 26),
    fiber_color_b=(120, 160, 230, 18),
    pupil_shape="round",
    pupil_scale=0.68,
    glints=[
        {"shape": "star", "x": 52, "y": 52, "w": 8, "h": 8,
        "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "star", "x": 52, "y": 52, "w": 8, "h": 8, "color": (255, 200, 230, 150)},
       {"shape": "star", "x": 86, "y": 74, "w": 4, "h": 4, "color": (200, 230, 255, 140)},
        {"shape": "star", "x": 74, "y": 76, "w": 4, "h": 4, "color": (255, 220, 200, 130)},
        {"shape": "star", "x": 82, "y": 84, "w": 3, "h": 3, "color": (220, 255, 220, 130)},
        {"shape": "star", "x": 90, "y": 86, "w": 4, "h": 4, "color": (255, 200, 220, 130)},
    ],
)

P2_LIULIU = make_style(
    "p2_liuliu",
    "Page2 溜溜：机警野性的猫眼梯形高光",
    iris_gradient_mode="vertical",
    iris_top_color=(15, 15, 15),
    iris_bottom_color=(60, 60, 55),
    sclera_center=(200, 220, 180),
    sclera_edge=(130, 150, 120),
    fiber_color_a=(40, 40, 35, 24),
    fiber_color_b=(70, 70, 65, 16),
    pupil_shape="diamond",
    pupil_scale=0.58,
    pupil_aspect=0.70,
    iris_bottom_rim=(100, 100, 105),
    glints=[
        {"shape": "trapezoid", "x": 64, "y": 48, "w": 12, "h": 10,
         "color": (255, 255, 255, 250), "rotation": 10, "blur": 0.6},
        {"shape": "round", "x": 96, "y": 98, "w": 8, "h": 8, "color": (255, 255, 255, 110)},
        {"shape": "round", "x": 76, "y": 58, "w": 3, "h": 3, "color": (255, 255, 255, 160)},
    ],
)

P2_QIQI = make_style(
    "p2_qiqi",
    "Page2 奇奇：华丽游戏风的蓝绿大眼+睫毛",
    iris_gradient_mode="vertical",
    iris_top_color=(15, 40, 50),
    iris_bottom_color=(80, 190, 210),
    sclera_center=(225, 240, 255),
    sclera_edge=(140, 155, 180),
    iris_center=(80, 190, 210),
    iris_edge=(30, 70, 100),
    fiber_color_a=(100, 210, 230, 28),
    fiber_color_b=(70, 150, 190, 20),
    pupil_shape="round",
    pupil_scale=0.55,
    pupil_outline={"color": (0, 0, 0, 200), "width": 2},
    glints=[
        {"shape": "ellipse", "x": 80, "y": 42, "w": 52, "h": 18,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "round", "x": 96, "y": 98, "w": 8, "h": 8, "color": (255, 255, 255, 170)},
        {"shape": "round", "x": 38, "y": 78, "w": 3, "h": 3, "color": (255, 255, 255, 150)},
        {"shape": "round", "x": 32, "y": 92, "w": 3, "h": 3, "color": (255, 255, 255, 130)},
    ],
   eyelashes=[
        {"start": (50, 16), "end": (42, 3), "color": (0, 0, 0, 200), "width": 3},
        {"start": (60, 16), "end": (56, 2), "color": (0, 0, 0, 200), "width": 3},
        {"start": (70, 16), "end": (72, 2), "color": (0, 0, 0, 200), "width": 3},
        {"start": (80, 16), "end": (86, 5), "color": (0, 0, 0, 200), "width": 3},
        {"start": (90, 16), "end": (100, 9), "color": (0, 0, 0, 200), "width": 3},
   ],
)

P2_XIUXIU = make_style(
    "p2_xiuxiu",
    "Page2 秀秀：稳重镜面感的橘棕大眼+睫毛",
    iris_gradient_mode="vertical",
    iris_top_color=(50, 25, 15),
    iris_bottom_color=(210, 120, 80),
    sclera_center=(255, 245, 230),
    sclera_edge=(190, 175, 155),
    iris_center=(210, 120, 80),
    iris_edge=(120, 60, 40),
    fiber_color_a=(255, 160, 90, 28),
    fiber_color_b=(230, 120, 60, 20),
    pupil_shape="round",
    pupil_scale=0.55,
    pupil_outline={"color": (0, 0, 0, 200), "width": 2},
    glints=[
        {"shape": "ellipse", "x": 80, "y": 42, "w": 48, "h": 18,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
        {"shape": "round", "x": 96, "y": 98, "w": 8, "h": 8, "color": (255, 255, 255, 170)},
        {"shape": "round", "x": 38, "y": 78, "w": 3, "h": 3, "color": (255, 255, 255, 150)},
        {"shape": "round", "x": 32, "y": 92, "w": 3, "h": 3, "color": (255, 255, 255, 130)},
    ],
   eyelashes=[
        {"start": (52, 16), "end": (44, 4), "color": (0, 0, 0, 200), "width": 3},
        {"start": (62, 16), "end": (58, 2), "color": (0, 0, 0, 200), "width": 3},
        {"start": (72, 16), "end": (74, 2), "color": (0, 0, 0, 200), "width": 3},
        {"start": (82, 16), "end": (88, 5), "color": (0, 0, 0, 200), "width": 3},
        {"start": (92, 16), "end": (102, 9), "color": (0, 0, 0, 200), "width": 3},
   ],
)

LOVOT_LIANLIAN = make_style(
    "lovot_lianlian",
    "LOVOT 恋恋：温暖含情的棕红圆眼",
    iris_gradient_mode="vertical",
    iris_top_color=(40, 18, 15),
    iris_bottom_color=(170, 90, 75),
    sclera_center=(255, 255, 255),
    sclera_edge=(200, 190, 185),
    iris_center=(170, 90, 75),
    iris_edge=(90, 40, 35),
    fiber_color_a=(200, 110, 90, 26),
    fiber_color_b=(160, 80, 60, 18),
    pupil_shape="round",
    pupil_scale=0.66,
    glints=[
        {"shape": "round", "x": 64, "y": 50, "w": 9, "h": 9,
         "color": (255, 255, 255, 250), "glow": (255, 255, 255, 55), "glow_radius": 34},
    ],
)


# ============================================================
# 样式索引
# ============================================================
EYE_STYLES = {
    # 硬件默认
    "pi2_light_blue": PI2_LIGHT_BLUE,
    "pi_dark_cat_green": PI_DARK_CAT_GREEN,
    "pi_dark_orange": PI_DARK_ORANGE,
    "pi2_light_green": PI2_LIGHT_GREEN,
    # 调研图复刻
    "p1_jingdian": P1_JINGDIAN,
    "p1_yuanyuan": P1_YUANYUAN,
    "p1_yongyong": P1_YONGYONG,
    "p1_rourou": P1_ROUROU,
    "p1_youyou": P1_YOUYOU,
    "p2_shasha": P2_SHASHA,
    "p2_shuoshuo": P2_SHUOSHUO,
    "p2_gugu": P2_GUGU,
    "p2_xixi": P2_XIXI,
    "p2_sisi": P2_SISI,
    "p2_linlin": P2_LINLIN,
    "p2_mengmeng": P2_MENGMENG,
    "p2_liuliu": P2_LIULIU,
    "p2_qiqi": P2_QIQI,
    "p2_xiuxiu": P2_XIUXIU,
    "lovot_lianlian": LOVOT_LIANLIAN,
}

DEFAULT_STYLE = "pi2_light_blue"


def get_style(name=None):
    """根据名称返回样式配置，未知名称返回默认样式。"""
    if name is None:
        return EYE_STYLES[DEFAULT_STYLE]
    if name not in EYE_STYLES:
        raise KeyError(
            f"未知眼睛样式: {name!r}。可用样式: {list(EYE_STYLES.keys())}"
        )
    return EYE_STYLES[name]


def list_styles():
    """返回所有可用样式的简要说明。"""
    return [(k, v["description"]) for k, v in EYE_STYLES.items()]
