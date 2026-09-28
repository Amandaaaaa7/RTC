"""
双目独立测试 — 共享 SPI0 总线

左右眼共用 SPI0 (GPIO10 MOSI, GPIO11 SCLK)，
各自独立 CS/DC/RST/BL 引脚。接线与大项目保持一致。

用法:
    python3 tools/dual_eye_test.py
"""

import time, math, random, spidev, RPi.GPIO as GPIO
from PIL import Image, ImageDraw

W, H = 160, 160

# 左眼
L = {"cs": 5, "dc": 25, "rst": 24, "bl": 23}
# 右眼
R = {"cs": 6, "dc": 27, "rst": 22, "bl": 26}

GPIO.setmode(GPIO.BCM)


class GC9D01:
    def __init__(self, spi, cs, dc, rst, bl):
        for p in [dc, rst, cs]:
            GPIO.setup(p, GPIO.OUT)
        GPIO.output(cs, GPIO.HIGH)
        GPIO.setup(bl, GPIO.OUT)
        GPIO.output(bl, GPIO.HIGH)
        self.cs, self.dc, self.rst = cs, dc, rst
        self.spi = spi
        self._init()

    def _cmd(self, c, data=None):
        GPIO.output(self.cs, 0)
        GPIO.output(self.dc, 0)
        self.spi.xfer3([c])
        if data:
            GPIO.output(self.dc, 1)
            self.spi.xfer3(list(data))
        GPIO.output(self.cs, 1)

    def _data(self, buf):
        GPIO.output(self.cs, 0)
        GPIO.output(self.dc, 1)
        for i in range(0, len(buf), 4096):
            self.spi.xfer3(list(buf[i:i + 4096]))
        GPIO.output(self.cs, 1)

    def _init(self):
        # 硬件复位
        GPIO.output(self.rst, 0)
        time.sleep(0.1)
        GPIO.output(self.rst, 1)
        time.sleep(0.1)

        self._cmd(0xFE)
        self._cmd(0xEF)
        self._cmd(0x80, b"\xff" * 16)
        self._cmd(0x3A, b"\x05")
        self._cmd(0xEC, b"\x01")
        self._cmd(0x74, b"\x02\x0E" + b"\x00" * 5)
        self._cmd(0x98, b"\x3E")
        self._cmd(0x99, b"\x3E")
        self._cmd(0xB5, b"\x0D\x0D")
        self._cmd(0x60, b"\x38\x0F\x79\x67")
        self._cmd(0x61, b"\x38\x11\x79\x67")
        self._cmd(0x64, b"\x38\x17\x71\x5F\x79\x67")
        self._cmd(0x65, b"\x38\x13\x71\x5B\x79\x67")
        self._cmd(0x6A, b"\x00\x00")
        self._cmd(0x6C, b"\x22\x02\x22\x02\x22\x22\x50")
        self._cmd(0x6E, bytes([
            0x03, 0x03, 0x01, 0x01, 0x00, 0x00, 0x0f, 0x0f,
            0x0d, 0x0d, 0x0b, 0x0b, 0x09, 0x09, 0x00, 0x00,
            0x00, 0x00, 0x0a, 0x0a, 0x0c, 0x0c, 0x0e, 0x0e,
            0x10, 0x10, 0x00, 0x00, 0x02, 0x02, 0x04, 0x04,
        ]))
        self._cmd(0xBF, b"\x01")
        self._cmd(0xF9, b"\x40")
        self._cmd(0x9B, b"\x3B")
        self._cmd(0x93, b"\x33\x7F\x00")
        self._cmd(0x7E, b"\x30")
        self._cmd(0x70, b"\x0D\x02\x08\x0D\x02\x08")
        self._cmd(0x71, b"\x0D\x02\x08")
        self._cmd(0x91, b"\x0E\x09")
        self._cmd(0xC3, b"\x1F")
        self._cmd(0xC4, b"\x1F")
        self._cmd(0xC9, b"\x1F")
        self._cmd(0xF0, b"\x53\x15\x0A\x04\x00\x3E")
        self._cmd(0xF2, b"\x53\x15\x0A\x04\x00\x3A")
        self._cmd(0xF1, b"\x56\xA8\x7F\x33\x34\x5F")
        self._cmd(0xF3, b"\x52\xA4\x7F\x33\x34\xDF")
        self._cmd(0x36, b"\xC8")
        self._cmd(0xB0, b"\x00")
        self._cmd(0xB1, b"\x00\x00")
        self._cmd(0xB4, b"\x00")
        self._cmd(0x11)
        time.sleep(0.2)
        self._cmd(0x29)
        self._cmd(0x2C)

    def display(self, img):
        img = img.convert("RGB")
        px = img.load()
        buf = bytearray(W * H * 2)
        for y in range(H):
            for x in range(W):
                r, g, b = px[x, y]
                c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                i = (y * W + x) * 2
                buf[i] = (c >> 8) & 0xFF
                buf[i + 1] = c & 0xFF
        self._cmd(0x2A, bytes([0, 0, 0, W - 1]))
        self._cmd(0x2B, bytes([0, 0, 0, H - 1]))
        self._cmd(0x2C)
        self._data(buf)


def draw_eye(gx, gy, eyelid=0, mirror=False):
    img = Image.new("RGB", (W, H), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2 + 10
    gx = -gx if mirror else gx
    px = cx + int(gx * 30)
    py = cy + int(gy * 20)
    draw.ellipse([px - 22, py - 32, px + 22, py + 32], fill=(20, 20, 20))
    draw.ellipse([px - 14, py - 14, px - 4, py - 4], fill=(255, 255, 255))
    if eyelid > 0:
        draw.rectangle([0, 0, W, eyelid], fill=(240, 240, 240))
        if eyelid < H - 1:
            draw.line([(0, eyelid), (W, eyelid)], fill=(0, 0, 0), width=1)
    return img


def main():
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 30_000_000
    spi.mode = 0

    left = GC9D01(spi, L["cs"], L["dc"], L["rst"], L["bl"])
    right = GC9D01(spi, R["cs"], R["dc"], R["rst"], R["bl"])

    left.display(Image.new("RGB", (W, H), (255, 0, 0)))
    right.display(Image.new("RGB", (W, H), (0, 0, 255)))
    time.sleep(1)

    print("双目动画运行中... Ctrl+C 退出")
    frame, blink = 0, 0
    try:
        while True:
            blink -= 1
            if blink <= 0 and random.random() < 0.005:
                blink = 10
            eyelid = int(80 - 80 * math.cos(blink * 0.3)) if blink > 0 else 0
            gx = math.sin(frame * 0.05)
            gy = math.sin(frame * 0.07 + 1)
            left.display(draw_eye(gx, gy, eyelid, mirror=False))
            right.display(draw_eye(gx, gy, eyelid, mirror=True))
            time.sleep(0.02)
            frame += 1
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()
        print("退出")


if __name__ == "__main__":
    main()
