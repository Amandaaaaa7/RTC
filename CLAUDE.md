# CLAUDE.md

## Project Overview

Doll Robot — Pi Zero 2W 多外设集成项目。
将 INMP441 I2S 麦克风 + Pi Camera + GC9D01 屏幕 + MAX98357A 扬声器集成到一个树莓派 Zero 2W 上。

## 关键决策: 双目共享 SPI0

两个 GC9D01 眼睛共享 **同一条 SPI0 总线** (GPIO10 MOSI, GPIO11 SCLK)，
各自独立 CS/DC/RST/BL 引脚。这样无需 SPI1，彻底避开了 I2S 占用的 GPIO18-21。

**之前的方案 (已废弃):** `eye_pi_zero_dual.py` 使用 SPI0+SPI1 两路独立总线，
导致 SPI1 (GPIO20/21) 与 I2S 冲突。共享 SPI0 后冲突消除。

## Hardware Resources & Conflicts

| 外设 | 接口 | 占用 GPIO | 冲突 |
|------|------|----------|------|
| INMP441 麦克风 | I2S capture | GPIO18,19,20 | 与 speaker 共享时钟 |
| MAX98357A 扬声器 | I2S playback | GPIO18,19,21 | 独立数据线, Web UI 触发播放 |
| Pi Camera | CSI | 无 GPIO | 独立 |
| GC9D01 左眼 | SPI0 共享 | GPIO10,11,5,25,24,23 | ✅ 无冲突 |
| GC9D01 右眼 | SPI0 共享 | GPIO6,27,22,26 (CS/DC/RST/BL) | ✅ 无冲突 |

**结论:** 双目共享 SPI0 + I2S 全双工 = **全部可共存** ✅

## Architecture

```
mic_speaker_camera_test/
├── main.py          # 集成入口, threading 并行运行各组件
├── mic.py           # 麦克风后台电平监控 (pyalsaaudio)
├── camera.py        # 摄像头 HTTP MJPEG 流 + Web UI (含 VU 表)
├── eye_display.py   # GC9D01 动画眼睛 (单目/双目, 可对声音反应)
├── speaker.py       # MAX98357A 扬声器播放 (Web UI 触发)
├── DEBUG_LOG.md     # 调试日志
├── CLAUDE.md        # 项目规则
├── setup/           # Pi 端一键配置脚本
│   ├── enable_all.sh
│   ├── enable_i2s.sh
│   └── enable_camera.sh
└── docs/
    └── hardware.md  # 接线文档 (含 GPIO 总表)
├── tools/
│   ├── eye_style_gallery.py    # 键盘热切换样式 + 表情 + review GIF
│   ├── preview.py              # 单姿态离线预览（输出 PNG）
│   └── review_browser.py       # eye-emotion-engine 评审 GIF 软链 + index.html + gallery.json
```

## Architecture Rules

1. **每个外设独立模块**, 后台线程运行, 通过线程安全接口对外暴露状态
2. **HTTP 服务器** (camera.py) 是中央仪表盘, 提供摄像头 + 各传感器状态的 Web UI
3. **主线程**仅负责编排生命周期 (start/stop), 不阻塞
4. **ALSA 设备**必须使用 `pyalsaaudio` (而非 `sounddevice` — PortAudio 兼容性问题)
5. **I2S 参数固定**: 48kHz, S32_LE, 2ch (取左声道 INMP441), `hw:1,0`
6. **EyeDisplay** 接收 MicMonitor 引用, 声音大时眼睛睁大 + 随机扫视
7. **双目共享 SPI0**: 两个 GC9D01 共用 MOSI/SCLK, 独立 CS/DC/RST/BL
8. **严禁任何热插拔** — CSI、GPIO、SPI、I2S、I2C 等所有外设接线都必须先断 USB 电源再操作，否则可能烧毁接口或导致系统崩溃
9. **SPI 屏幕黑屏排查流程** — 按顺序: (1) 确认 `/dev/spidev*` 存在 (2) 测试 BL/RST 引脚可控制 (3) 发 `0x11+0x29` 看背光是否变亮 (4) 跑原版独立脚本排除代码问题 (5) 大数据需分片发送 (spi.xfer3 分批, 4096 限制)
10. **大 SPI 数据传输** — 单次 `spi.xfer()`/`spi.writebytes()` 上限 4096 字节，160x160x2=51200 字节需分片或使用 `xfer3`
11. **面包板使用限制** — SPI 信号线（MOSI、SCLK、CS、DC、RST）尽量不通过面包板转接，直接 Pi 排针到屏幕最稳定。调试阶段必须转接时，确保每根线插紧，优先保证 GND 和 SPI 总线的连接质量。双目共享 SPI 时总线降速至 30MHz
12. **Review GIF 嵌入预览** — `tools/review_browser.py --setup` 把同级
    `eye-emotion-engine/outputs/review/<版本>/` 通过**软链**暴露到
    `pi_affe_sys/outputs/reviews/<版本>/`，生成 `index.html`（dark theme 画廊）
    和 `_gallery.json`（给 gallery 程序读）。gallery 里按 `r` 进入 review
    预览模式（左右屏同时播放当前 GIF），`[`/`]` 翻 GIF，`0` 回到当前帧开头。
    `EyeDisplay.set_review_frame_provider(callback)` 是这条预览通路的挂载点；
    传 `None` 恢复表达式/追踪渲染。GIF 在屏端按其原帧率播放，不阻塞主循环。
    `outputs/reviews/` 在 .gitignore 里，不入库；上游 rebuild 后重跑 `--setup`
    即可刷新。

## Commands (run on Pi)

```bash
cd /path/to/mic_speaker_camera_test

# 一键配置所有外设
sudo bash setup/enable_all.sh
sudo reboot

# 运行
python3 main.py                              # 单眼 + 摄像头 + 麦克风
python3 main.py --dual-eye                   # 双目 + 摄像头 + 麦克风
python3 main.py --dual-eye --port 9090       # 双目 + 自定义端口
python3 main.py --no-camera                  # 仅麦克风 + 眼睛
python3 main.py --no-mic                     # 仅摄像头 + 眼睛
python3 main.py --no-eye                     # 仅麦克风 + 摄像头
```
