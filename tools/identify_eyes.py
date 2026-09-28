"""
双目屏幕识别测试。

在代码定义的"左眼"(LEFT pins) 显示红色 + "L" 字样，
在代码定义的"右眼"(RIGHT pins) 显示蓝色 + "R" 字样，
持续 5 秒后交换颜色再显示 5 秒。

用户观察两个物理屏幕，告诉程序：
  - 哪个屏幕先显示红/L、后显示蓝/R
  - 哪个屏幕先显示蓝/R、后显示红/L
从而确认代码 left/right 与实际物理左右是否一致。
"""

import time
from PIL import Image, ImageDraw, ImageFont

import spidev
import RPi.GPIO as GPIO
from eye_display import GC9D01, LEFT, RIGHT, WIDTH, HEIGHT


def make_label(color, label):
    """生成带大字母标签的纯色图。"""
    img = Image.new("RGB", (WIDTH, HEIGHT), color)
    draw = ImageDraw.Draw(img)
    # 尽量用大字体，如果没有 ttf 就用默认字体
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (WIDTH - tw) // 2
    y = (HEIGHT - th) // 2
    draw.text((x, y), label, fill=(255, 255, 255), font=font)
    return img


def main():
    GPIO.setmode(GPIO.BCM)
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 30_000_000
    spi.mode = 0

    print("初始化屏幕...")
    left = GC9D01(spi, cs=LEFT["cs"], dc=LEFT["dc"],
                  rst=LEFT["rst"], bl=LEFT["bl"])
    right = GC9D01(spi, cs=RIGHT["cs"], dc=RIGHT["dc"],
                   rst=RIGHT["rst"], bl=RIGHT["bl"])

    red_l = make_label((255, 0, 0), "L")
    blue_r = make_label((0, 0, 255), "R")
    blue_l = make_label((0, 0, 255), "L")
    red_r = make_label((255, 0, 0), "R")

    try:
        print("\n第一阶段: 代码左眼=红/L, 代码右眼=蓝/R (持续 5 秒)")
        left.display(red_l)
        right.display(blue_r)
        time.sleep(5)

        print("第二阶段: 代码左眼=蓝/L, 代码右眼=红/R (持续 5 秒)")
        left.display(blue_l)
        right.display(red_r)
        time.sleep(5)

        print("\n测试结束。请告诉我：")
        print("  站在机器人正面，哪个物理屏幕先红了？它现在对应代码 LEFT 还是 RIGHT？")
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
