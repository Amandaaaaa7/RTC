"""
眼睛图层生成与静态预览（单元测试/生成器两用）

运行方式：
    cd /path/to/pi_affe_sys
    python3 -m unittest tests.test_eye_layers

功能：
1. 遍历 eye_styles.py 中所有眼睛样式；
2. 对每个样式调用 draw_eye() 生成最终图像；
3. 将分层缓存中的白目、虹彩、纹理、瞳孔、高光分别保存为独立 PNG；
4. 生成一个本地 HTML 画廊页面，可浏览每个风格的最终图与各图层；
5. 生成所有风格的网格对比图 all_composites.png。

输出目录：
    outputs/eye_layers/
        index.html
        all_composites.png
        {style_name}/
            composite.png
            layer_00_sclera.png
            layer_01_iris.png
            layer_02_iris_fibers.png
            layer_03_pupil.png
            layer_04_glint.png
"""

import os
import sys
import unittest
from pathlib import Path

# 保证在 tests/ 目录下也能导入项目根目录的 eye_render / eye_styles
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image, ImageDraw

from eye_render import WIDTH, HEIGHT, draw_eye, _init_cache
import eye_render
from eye_styles import list_styles, get_style


OUTPUT_DIR = Path(__file__).parent.parent / "outputs" / "eye_layers"

LAYER_ORDER = [
    ("sclera", "00_白目层"),
    ("iris", "01_虹彩层"),
    ("iris_fibers", "02_虹膜纹理"),
    ("pupil", "03_瞳孔层"),
    ("glint", "04_高光层"),
]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _save_layer(img: Image.Image, path: Path) -> None:
    """保留 alpha 通道保存为 PNG。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img.save(path, "PNG")


def render_style(style):
    """渲染指定样式，返回最终图与各图层字典。"""
    _init_cache(style)
    composite = draw_eye(
        gaze_x=0.0,
        gaze_y=0.0,
        eyelid=0,
        mirror=False,
        pupil_relative_scale=1.0,
        style=style,
    )
    layers = {name: eye_render._CACHE[name] for name, _ in LAYER_ORDER}
    return composite, layers


def build_contact_sheet(composites, labels, cols=4, thumb=160, pad=10):
    """把所有风格最终图拼成一张网格图，并在下方标注名称。"""
    n = len(composites)
    rows = (n + cols - 1) // cols
    sheet_w = cols * (thumb + pad) + pad
    sheet_h = rows * (thumb + pad) + pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    for i, (img, label) in enumerate(zip(composites, labels)):
        r, c = divmod(i, cols)
        x = c * (thumb + pad) + pad
        y = r * (thumb + pad) + pad
        thumb_img = img.convert("RGB").resize((thumb, thumb), Image.Resampling.LANCZOS)
        sheet.paste(thumb_img, (x, y))
        # 简单文字标注（不依赖外部字体）
        draw.text((x + 2, y + thumb - 12), label, fill=(60, 60, 60))
    return sheet


def generate_html(out_dir: Path, style_rows):
    """生成本地 HTML 画廊。"""
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Eye Styles Layer Gallery</title>
<style>
body { font-family: sans-serif; background: #f5f5f5; margin: 20px; color: #333; }
h1 { font-size: 1.6rem; }
.style-card { background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.style-card h2 { margin: 0 0 8px; font-size: 1.1rem; }
.layers { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-top: 10px; }
.layers figure { margin: 0; text-align: center; }
.layers img { height: 120px; border: 1px solid #ddd; border-radius: 4px; background: #fafafa; }
.layers figcaption { font-size: 0.75rem; color: #666; margin-top: 4px; }
.composite { height: 160px; border: 1px solid #ccc; border-radius: 50%; background: #fafafa; }
.grid-img { max-width: 100%; border: 1px solid #ddd; border-radius: 4px; margin-top: 16px; }
</style>
</head>
<body>
<h1>眼睛样式图层画廊</h1>
<p>共 {count} 套样式；每行显示最终效果图与各分层原图。所有图片均为 160×160 或图层原始尺寸。</p>
<img class="grid-img" src="all_composites.png" alt="all styles grid">
"""
    html = html.replace("{count}", str(len(style_rows)))

    for name, desc, layer_files in style_rows:
        html += f'<div class="style-card"><h2>{name}</h2><p>{desc}</p>\n'
        html += '<div class="layers">\n'
        html += f'  <figure><img class="composite" src="{name}/composite.png" alt="{name} composite"><figcaption>最终图</figcaption></figure>\n'
        for layer_key, filename, pretty in layer_files:
            html += f'  <figure><img src="{name}/{filename}" alt="{pretty}"><figcaption>{pretty}</figcaption></figure>\n'
        html += '</div></div>\n'

    html += "</body>\n</html>\n"
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def generate_all(out_dir: Path = OUTPUT_DIR):
    """生成所有样式的图层、最终图、网格图与 HTML 画廊。"""
    _ensure_dir(out_dir)
    styles = list_styles()
    style_rows = []
    composites = []
    labels = []

    for style_name, desc in styles:
        style = get_style(style_name)
        composite, layers = render_style(style)
        composites.append(composite)
        labels.append(style_name)

        style_dir = out_dir / style_name
        _ensure_dir(style_dir)

        # 保存最终图（无 alpha 便于直接查看）
        composite.convert("RGB").save(style_dir / "composite.png", "PNG")

        layer_files = []
        for layer_key, pretty in LAYER_ORDER:
            filename = f"layer_{pretty}.png"
            _save_layer(layers[layer_key], style_dir / filename)
            layer_files.append((layer_key, filename, pretty.split("_", 1)[1]))

        style_rows.append((style_name, desc, layer_files))

    # 网格对比图
    contact = build_contact_sheet(composites, labels)
    contact.save(out_dir / "all_composites.png", "PNG")

    # HTML 画廊
    generate_html(out_dir, style_rows)
    return out_dir


class EyeLayerTests(unittest.TestCase):
    """验证每个眼睛样式都能成功生成图层与最终图。"""

    def test_all_styles_generate_layers_and_html(self):
        out_dir = generate_all()
        self.assertTrue((out_dir / "index.html").exists())
        self.assertTrue((out_dir / "all_composites.png").exists())

        for style_name, _ in list_styles():
            style_dir = out_dir / style_name
            self.assertTrue(style_dir.is_dir(), f"缺少样式目录: {style_dir}")
            self.assertTrue((style_dir / "composite.png").exists())

            for layer_key, pretty in LAYER_ORDER:
                path = style_dir / f"layer_{pretty}.png"
                self.assertTrue(path.exists(), f"缺少图层文件: {path}")
                self.assertGreater(path.stat().st_size, 0)

            # 最终图尺寸必须保持 160×160，确保硬件兼容
            with Image.open(style_dir / "composite.png") as img:
                self.assertEqual(img.size, (WIDTH, HEIGHT))


if __name__ == "__main__":
    out = generate_all()
    print(f"[OK] 已生成眼睛图层画廊: {out}/index.html")
