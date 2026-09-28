---
name: spi-display-debug
description: >
  SPI 屏幕黑屏/花屏排查流程。适用于任何 SPI 接口的小型 LCD/OLED 屏幕
  (GC9D01, ST7789, ILI9341 等)。使用此 skill 当: (1) 屏幕只有背光无画面；
  (2) 画面花屏/闪烁； (3) SPI 屏幕完全不响应； (4) 屏幕之前能亮现在不亮了。
  覆盖: 驱动层排查, 引脚控制验证, SPI 通信测试, 物理连接检查。
  不用于: HDMI 屏幕, DSI 接口屏幕, 非 SPI 协议屏幕。
---

# 屏幕黑屏排查 5 步法

按顺序执行，哪一步失败就停在哪一步排查，不要跳过。

---

## Step 1: 驱动层 — SPI 设备是否存在

```bash
ls -l /dev/spidev*
```

**正常:** 列出至少一个 `spidev0.0`。如果 `No such file or directory`：
- 运行 `sudo raspi-config nonint do_spi 0` 或检查 `/boot/firmware/config.txt` 中有 `dtparam=spi=on`
- 检查内核模块是否加载: `lsmod | grep spi`

---

## Step 2: 引脚层 — CS/DC/RST/BL 能否控制

选一个已知正常的引脚（比如背光 BL）做对照，测试目标引脚是否正常输出：

```python
import RPi.GPIO as GPIO, time
GPIO.setmode(GPIO.BCM)
# BL 做对照
GPIO.setup(23, GPIO.OUT)  # 左眼 BL
GPIO.setup(24, GPIO.OUT)  # 左眼 RST

# 交替: BL 闪烁, RST 同步变化, 看屏幕有没有跟随闪烁
for _ in range(3):
    GPIO.output(23, 0)  # BL off
    GPIO.output(24, 1)  # RST high
    time.sleep(0.5)
    GPIO.output(23, 1)  # BL on
    GPIO.output(24, 0)  # RST low
    time.sleep(0.5)
GPIO.output(24, 1)  # RST back to high
GPIO.cleanup()
```

**正常:** 屏幕背光随 BL 闪烁。如果 RST 接线正确，不应看到其他异常。
**失败:** 线接错了或 GPIO 编号不对。

---

## Step 3: 通信层 — SPI 基本指令能否到达屏幕

硬件复位 → SLPOUT → DISPON，看背光是否有变化：

```python
import spidev, time, RPi.GPIO as GPIO
PIN_DC, PIN_RST, PIN_CS = 25, 24, 5
GPIO.setmode(GPIO.BCM)
for p in [PIN_DC, PIN_RST, PIN_CS]: GPIO.setup(p, GPIO.OUT)
GPIO.output(PIN_CS, GPIO.HIGH)
spi = spidev.SpiDev(); spi.open(0,0); spi.max_speed_hz=10000000; spi.mode=0

def cmd(c, data=None):
    GPIO.output(PIN_CS,0); GPIO.output(PIN_DC,0); spi.xfer3([c])
    if data: GPIO.output(PIN_DC,1); spi.xfer3(list(data))
    GPIO.output(PIN_CS,1)

# 硬件复位
GPIO.output(PIN_RST,0); time.sleep(0.1); GPIO.output(PIN_RST,1); time.sleep(0.1)
# 唤醒 + 显示开
cmd(0x11); time.sleep(0.2); cmd(0x29)
print("SLPOUT + DISPON 已发送")
GPIO.cleanup()
```

**正常:** 屏幕背光明显变亮（屏幕从睡眠唤醒）。说明 SPI 通信正常，CS/DC/RST 接线都正确。
**失败（背光无变化）:** SPI 没通，检查 MOSI/SCLK/CS/DC 接线。

---

## Step 4: 隔离层 — 排除其他外设干扰

拔掉所有其他外设（摄像头、麦克风、另一个屏幕的线），只留目标屏幕。

运行原版/已知正常的单眼测试代码：

```bash
python3 eye_pi_zero.py  # 或对应屏幕的原版测试
```

**正常:** 屏幕显示红绿蓝纯色。→ 问题是被其他外设干扰了，逐一恢复外设排查哪根线冲突。
**失败:** 即使隔离后也不亮 → 硬件接线或屏幕本身有问题。

---

## Step 5: 物理层 — 面包板/线材/接触排查

如果 Step 1-4 都通过但屏幕仍不稳定（花屏、间歇性黑屏）：

1. **直接连接测试:** 把 MOSI/SCLK/CS/DC/RST 从面包板上拔下来，Pi 排针直连屏幕
2. **降速测试:** 把 `spi.max_speed_hz` 从 60MHz 降到 10MHz 或更低
3. **线材检查:** 检查杜邦线是否有生锈、氧化、插针松动
4. **GND 优先:** 确保 GND 回路连接可靠，地线不良是所有奇怪问题的根源

**常见的物理层问题:**
| 现象 | 原因 |
|------|------|
| 背光亮但无画面 | GND 接触不良或 MOSI/SCLK 信号畸变 |
| 间歇性花屏 | 面包板转接导致信号反射，降速或直连解决 |
| 有时候亮有时候不亮 | 杜邦线松动或氧化 |

---

## 总结流程图

```
背光不亮?   ──→ Step 1 (SPI设备) ──→ Step 2 (引脚) ──→ 接线问题
    │
背光亮,无画面 ──→ Step 3 (SPI通信) ──→ 失败? ──→ 查 MOSI/SCLK/CS/DC
    │                    │
    │                 通过 ↓
    └────────── Step 4 (隔离) ──→ 失败? ──→ 硬件/接线问题
                         │
                      通过 ↓
                    Step 5 (物理层) ──→ 直连/降速/换线
```

## 原理说明

每次排查都从最简单、最基础的步骤开始（驱动是否存在），逐步深入到物理层。这样做是因为：
- SPI 屏幕不显示的根因 **80% 是物理连接问题**（接线、面包板、信号干扰）
- 跳过 Step 1-2 直接调代码是浪费时间
- "曾经能亮现在不亮了" 几乎一定是接线松了而不是代码变了