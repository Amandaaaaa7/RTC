"""
GC9D01 Animated Eye - Pi Zero 2W (右眼抗锯齿版)
与 eye_pi_zero.py 完全一致，仅修改引脚定义。

接线：
  VCC -> 3.3V, GND -> GND
  MOSI -> GPIO10 (共享左眼)
  SCLK -> GPIO11 (共享左眼)
  CS  -> GPIO6,  DC -> GPIO27
  RST -> GPIO22, BL -> GPIO26
"""

import time, math, random
import spidev
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw

# ★★★ 右眼引脚 (与左眼唯一区别) ★★★
PIN_DC, PIN_RST, PIN_BL, PIN_CS = 27, 22, 26, 6
WIDTH, HEIGHT = 160, 160

# ========== GC9D01 驱动 ==========
class GC9D01:
    def __init__(self, dc, rst, cs, bl=None):
        for p in [dc, rst, cs]:
            GPIO.setup(p, GPIO.OUT)
        GPIO.output(cs, GPIO.HIGH)
        if bl is not None:
            GPIO.setup(bl, GPIO.OUT)
            GPIO.output(bl, GPIO.HIGH)
        self.cs, self.dc = cs, dc

        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 60000000
        self.spi.mode = 0
        self.init_display()

    def _cmd(self, cmd, data=None):
        GPIO.output(self.cs, 0)
        GPIO.output(self.dc, 0)
        self.spi.xfer3([cmd])
        if data:
            GPIO.output(self.dc, 1)
            self.spi.xfer3(list(data))
        GPIO.output(self.cs, 1)

    def _data(self, data):
        GPIO.output(self.cs, 0)
        GPIO.output(self.dc, 1)
        self.spi.xfer3(list(data))
        GPIO.output(self.cs, 1)

    def reset(self):
        GPIO.output(PIN_RST, 1); time.sleep(0.01)
        GPIO.output(PIN_RST, 0); time.sleep(0.1)
        GPIO.output(PIN_RST, 1); time.sleep(0.1)

    def init_display(self):
        self.reset()
        self._cmd(0xFE); self._cmd(0xEF)
        self._cmd(0x80, b'\xFF'*16); self._cmd(0x3A, b'\x05'); self._cmd(0xEC, b'\x01')
        self._cmd(0x74, b'\x02\x0E' + b'\x00'*5); self._cmd(0x98, b'\x3E'); self._cmd(0x99, b'\x3E')
        self._cmd(0xB5, b'\x0D\x0D')
        self._cmd(0x60, b'\x38\x0F\x79\x67'); self._cmd(0x61, b'\x38\x11\x79\x67')
        self._cmd(0x64, b'\x38\x17\x71\x5F\x79\x67'); self._cmd(0x65, b'\x38\x13\x71\x5B\x79\x67')
        self._cmd(0x6A, b'\x00\x00')
        self._cmd(0x6C, b'\x22\x02\x22\x02\x22\x22\x50')
        self._cmd(0x6E, bytes([0x03,0x03,0x01,0x01,0x00,0x00,0x0f,0x0f,0x0d,0x0d,0x0b,0x0b,
            0x09,0x09,0x00,0x00,0x00,0x00,0x0a,0x0a,0x0c,0x0c,0x0e,0x0e,0x10,0x10,
            0x00,0x00,0x02,0x02,0x04,0x04]))
        self._cmd(0xBF, b'\x01'); self._cmd(0xF9, b'\x40'); self._cmd(0x9B, b'\x3B')
        self._cmd(0x93, b'\x33\x7F\x00'); self._cmd(0x7E, b'\x30')
        self._cmd(0x70, b'\x0D\x02\x08\x0D\x02\x08'); self._cmd(0x71, b'\x0D\x02\x08')
        self._cmd(0x91, b'\x0E\x09')
        self._cmd(0xC3, b'\x1F'); self._cmd(0xC4, b'\x1F'); self._cmd(0xC9, b'\x1F')
        self._cmd(0xF0, b'\x53\x15\x0A\x04\x00\x3E')
        self._cmd(0xF2, b'\x53\x15\x0A\x04\x00\x3A')
        self._cmd(0xF1, b'\x56\xA8\x7F\x33\x34\x5F')
        self._cmd(0xF3, b'\x52\xA4\x7F\x33\x34\xDF')
        self._cmd(0x36, b'\xC8'); self._cmd(0xB0, b'\x00')
        self._cmd(0xB1, b'\x00\x00'); self._cmd(0xB4, b'\x00')
        self._cmd(0x11); time.sleep(0.2)
        self._cmd(0x29); self._cmd(0x2C)

    def display(self, pil_img):
        """把 PIL Image 转为 RGB565 发送到屏幕"""
        img = pil_img.convert("RGB")
        px = img.load()
        buf = bytearray(WIDTH * HEIGHT * 2)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = px[x, y]
                c = ((r>>3)<<11) | ((g>>2)<<5) | (b>>3)
                i = (y * WIDTH + x) * 2
                buf[i] = (c>>8) & 0xFF
                buf[i+1] = c & 0xFF
        self._cmd(0x2A, bytes([0,0,0, WIDTH-1]))
        self._cmd(0x2B, bytes([0,0,0, HEIGHT-1]))
        self._cmd(0x2C)
        self._data(buf)


# ========== 绘图 ==========
def draw_eye(gaze_x, gaze_y, eyelid=0):
    """用 Pillow 画抗锯齿眼睛"""
    img = Image.new("RGB", (WIDTH, HEIGHT), (240, 240, 240))
    draw = ImageDraw.Draw(img)

    cx, cy = WIDTH//2, HEIGHT//2 + 10
    px = cx + int(gaze_x * 30)
    py = cy + int(gaze_y * 20)

    # 瞳孔（抗锯齿椭圆）
    draw.ellipse([px-22, py-32, px+22, py+32], fill=(20, 20, 20), width=0)

    # 高光
    draw.ellipse([px-14, py-14, px-4, py-4], fill=(255, 255, 255), width=0)

    # 眼皮（从上往下遮盖）
    if eyelid > 0:
        draw.rectangle([0, 0, WIDTH, eyelid], fill=(240, 240, 240))
        # 下眼线
        if eyelid < HEIGHT-1:
            draw.line([(0, eyelid), (WIDTH, eyelid)], fill=(0, 0, 0), width=1)

    return img


# ========== 主程序 ==========
def main():
    GPIO.setmode(GPIO.BCM)
    tft = GC9D01(PIN_DC, PIN_RST, PIN_CS, PIN_BL)

    # 纯色测试
    tft.display(Image.new("RGB", (WIDTH, HEIGHT), (255, 0, 0)))
    time.sleep(1)
    tft.display(Image.new("RGB", (WIDTH, HEIGHT), (0, 255, 0)))
    time.sleep(1)
    tft.display(Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 255)))
    time.sleep(1)

    print("Running...")
    frame, blink = 0, 0
    while True:
        blink -= 1
        if blink <= 0 and random.random() < 0.005:
            blink = 10

        eyelid = int(80 - 80 * math.cos(blink * 0.3)) if blink > 0 else 0

        img = draw_eye(
            gaze_x=math.sin(frame * 0.05),
            gaze_y=math.sin(frame * 0.07 + 1),
            eyelid=eyelid
        )
        tft.display(img)
        time.sleep(0.02)
        frame += 1

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        GPIO.cleanup()
