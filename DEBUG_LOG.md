# 调试日志 — Doll Robot 集成

## 日志规则
- 每条记录：日期 [#编号] | 问题描述 | 解决方案 | 改动代码 | 结果
- `[WIP]` = 进行中，`[DONE]` = 已解决，`[FAIL]` = 方案无效

---

### 2026-06-22: [#1] 项目初始化 — Phase 1 麦克风+摄像头集成

**问题:** 需要将 pi-mic (INMP441 I2S) 和 camera-pi (CSI 摄像头) 集成到统一入口。

**方案:**
- 从 `pi-mic/utils/audio_utils.py` 提取 MicMonitor 类，后台线程采集 → 线程安全电平接口
- 从 `camera-pi/camera_stream.py` 提取 CameraStreamer + HTTP MJPEG 服务器
- 创建统一 `main.py` 入口，使用 `ThreadingHTTPServer` 在后台运行，mic 在独立线程
- Web UI 增加实时 VU 表，通过 `/mic-level` JSON endpoint 轮询

**新增文件:**
- `mic.py` — 麦克风电平监视器
- `camera.py` — 摄像头流 + HTTP 服务 (含 VU 表前端)
- `main.py` — 集成入口
- `setup/` — 一键配置脚本
- `docs/hardware.md` — 接线文档

**关键发现 — GPIO 冲突:**
- googlevoicehat-soundcard (I2S) 占用 GPIO18-20
- SPI1 (双目第二个眼) 占用 GPIO20-21
- **SPI1 与 I2S 在 GPIO20 冲突** → 麦克风 + 双目屏幕无法共存
- SPI0 (单眼) 与 I2S 无冲突 → 单眼 + 麦克风可正常工作

**结果:** [DONE] Phase 1 代码完成，待 Pi 上验证。

---

---

### 2026-06-22: [#3] Phase 2 — 加入 GC9D01 眼睛屏幕

**问题:** 需要将动画眼睛与麦克风、摄像头集成到同一进程。

**方案:**
- 从 `5.Animated_Eye12/eye_pi_zero.py` 提取 `EyeDisplay` 类，后台线程驱动
- 保留完整 GC9D01 初始化序列和抗锯齿绘图
- 传入 MicMonitor引用 → 声音大时眼睛睁大 + 随机扫视
- `main.py` 增加 `--no-eye` 开关

**新增文件:**
- `eye_display.py` — GC9D01 驱动 + 动画眼睛 (后台线程)

**GPIO 确认:** SPI0 (GPIO10,11,5,25,24,23) 与 I2S (GPIO18-20) 无冲突 ✅

**结果:** [DONE] Phase 2 代码完成，待 Pi 上验证。

---

### 2026-06-22: [#4] (待验证) Phase 2 首次运行测试

---

### 2026-06-22: [#5] Phase 3 硬件预留确认

**扬声器方案:** MAX98357A + PH1.25 座子喇叭
**接线预留:**

| MAX98357A | Pi GPIO |
|-----------|---------|
| VIN | 5V (Pin 2/4) |
| GND | GND |
| BCLK | GPIO18 (与 INMP441 共享) |
| LRCLK | GPIO19 (与 INMP441 共享) |
| DIN | **GPIO21 (PCM_DOUT)** |

**全双工拓扑:** I2S 总线共享 BCLK/LRCLK，capture 用 GPIO20, playback 用 GPIO21。
**冲突矩阵确认:** I2S 全双工 + SPI0 单眼 = ✅ 可行；双目 ❌ 不可行。

**决定:** 扬声器代码暂不编写，等 mic+camera+eye 验证通过后再加入。
*更新于 docs/hardware.md*

---

### 2026-06-22: [#6] 双目优化 — 共享 SPI0 消除冲突

**问题:** 原 `eye_pi_zero_dual.py` 用 SPI0 + SPI1 两路独立总线驱动双目，
但 SPI1 (GPIO20/21) 与 I2S (GPIO18-20) 在 GPIO20 冲突，导致双目 + 麦克风无法共存。

**方案:** 两个眼睛共享同一条 SPI0 总线 (GPIO10 MOSI, GPIO11 SCLK)，
各自独立 CS/DC/RST/BL 引脚。

**改动:**
- `eye_display.py`: GC9D01 构造器改为接受外部 SPI 对象，不再自己创建
- `eye_display.py`: 新增 `dual` 参数，双目时创建两个 GC9D01 实例共享同一 spi 对象
- `eye_display.py`: `draw_eye()` 支持 `mirror` 参数 (右眼镜像)
- `main.py`: 新增 `--dual-eye` 标志
- `docs/hardware.md`: 重写接线部分，右眼改用 GPIO6,27,22,26 (非 SPI1)

**优化效果:**
| 指标 | 旧方案 (SPI0+SPI1) | 新方案 (共享 SPI0) |
|------|------------------|------------------|
| 占用 GPIO | 12 个 | 11 个 |
| I2S 冲突 | ❌ GPIO20/21 | ✅ 无冲突 |
| 需要启用 SPI1 | 是 (`dtoverlay=spi1-3cs`) | 否 |
| 帧率 | 两路独立同时传输 | 分时传输，60MHz 下每眼 ~8.5ms |

**接线 (右眼新引脚):**
| 信号 | 新引脚 | 旧引脚 (SPI1) |
|------|--------|-------------|
| MOSI | GPIO10 (共享) | GPIO20 |
| SCLK | GPIO11 (共享) | GPIO21 |
| CS | GPIO6 | GPIO6 (不变) |
| DC | GPIO27 | GPIO27 (不变) |
| RST | GPIO22 | GPIO22 (不变) |
| BL | GPIO26 | GPIO26 (不变) |

**结果:** [DONE] 双目 + 麦克风 + 摄像头可同时工作。`--dual-eye` 启动。

---

### 2026-06-22: [#7] Python 类作用域闭包陷阱 — camera.py NameError

**问题:** `main.py` 启动时在 `create_server()` 处报 `NameError: name 'streamer' is not defined`。

**根因:** `create_server()` 内部定义 `StreamHandler` 类时用了 `streamer = streamer` 把外层变量赋给类属性。但 Python 的 class body 作用域特殊——赋值语句右侧的名字 **不捕获** 外层函数的局部变量，导致查找失败。

**方案:**
- 将函数参数重命名为 `cam_streamer` 避免混淆
- 去掉类变量赋值，methods 直接通过闭包引用外层变量 `cam_streamer` 和 `mic_monitor`
- 全局替换 `self.streamer` → `cam_streamer`，`self.mic` → `mic_monitor`

**改动:**
- `camera.py:create_server()` — 修改参数名和引用方式

**结果:** [DONE] HTTP 服务正常启动。

---

### 2026-06-22: [#8] VU 表灵敏度调整 + 麦克风增益

**问题:** 两个独立问题：
1. VU 表以 int32 满量程(21.5亿)做参考，正常说话电平占比例极小，条几乎不动
2. 默认 64x 增益过大，安静时底噪也占据 2/3 条

**方案:**
- VU 表改用 **dBFS 映射**：-80dBFS(安静)=0%, 0dBFS(满量程)=100%，正常说话(-40~-20dBFS)跳到 50%~75%
- 默认增益从 64x 降到 **8x**
- 新增 `--mic-gain` 参数让用户手动调节

**改动:**
- `mic.py`: `dbfs_to_bar()` 新函数，`print_vu_bar()` 改用 dBFS 映射
- `mic.py`: MicMonitor 默认 gain=8.0，采集循环中应用增益 + 防削波
- `camera.py`: web UI 中 VU 条改用 dBFS 百分比
- `main.py`: 新增 `--mic-gain` 参数

**结果:** [DONE] 安静时条接近底部，正常说话在中间，吹气接近满格。

---

### 2026-06-22: [#9] 带电插拔 CSI 导致 Pi 失联 — 教训记录

**问题:** 测试摄像头时报 `No cameras available`，插拔 CSI 排线后 Pi 从局域网消失。

**根因:** CSI 排线带电操作，高温电可能通过排线引脚短路，导致 Pi 系统崩溃（文件系统损坏、WiFi 连接丢失）。

**结论:**
- CSI 排线 **严禁带电插拔**，必须先断 USB 电源
- 不止 CSI，所有外设接线（GPIO、SPI、I2S、I2C）都应断电操作
- 已写入 CLAUDE.md 架构规则第 8 条

**结果:** [DONE] 新 TF 卡重刷系统后恢复正常。

---

### 2026-06-22: [#10] 新 SD 卡 + 集成测试验证通过

**测试内容:** 新烧录的树莓派系统上重新配置环境并验证麦克风 + 摄像头。

**步骤:**
1. 新 SD 卡烧录 Raspberry Pi OS (Bookworm)，预配 SSH + WiFi
2. 安装依赖并启用 I2S 驱动 + 摄像头
3. 项目代码通过 SFTP 传到 `/home/pi/mic_speaker_camera_test/`
4. `main.py --no-camera --no-eye --mic-gain 64 --console-vu` → VU 表正常跳动
5. `main.py --no-eye` → 浏览器摄像头画面 + VU 表正常工作

**验证结果:**
| 模块 | 结果 |
|------|------|
| I2S 麦克风 (INMP441) | ✅ 电平跳动正常 |
| 摄像头 (CSI) | ✅ MJPEG 流正常 |
| Web UI (VU 表) | ✅ 实时响应 |
| HTTP 服务 | ✅ 分辨率切换正常 |

**结论:** Phase 1+2 核心功能在新系统上验证通过。

**结果:** [DONE]

---

### 2026-06-22: [#11] 摄像头中途掉线 + MJPEG 流超时修复

**问题:** 浏览器画面在运行一段时间后卡住不动，刷新后也无法恢复。

**根因:** rpicam-vid 进程因 Pi Zero 2W (512MB RAM) 内存压力崩溃后，MJPEG 循环卡在 `while True` 中永远等待下一帧。

**方案:**
- `_serve_mjpeg()` 添加 5 秒超时，无新帧自动断开，浏览器自动重连
- 实测结果: 默认 2592x1944(5MP) 对 Zero 2W 负载过大

**Pi Zero 2W 压力测试结果:**

| 分辨率 | 稳定运行时间 | 表现 |
|--------|------------|------|
| 2592x1944 (5MP) | < 5 min | 画面卡死 |
| 1920x1080 (1080p) | ~5 min | 实时画面卡顿 |
| 1280x720 (720p) | ~10 min | 略有卡顿，总体可用 |
| 640x480 (VGA) | > 30 min | 流畅 (未测试极限) |

**发现: 麦克风 + 摄像头存在 CPU/缓存资源竞争。**
- 麦克风线程负载恒定（无论是否说话都在采集+算电平），但说话时更易察觉卡顿
- 挪动设备 / 画面中有快速移动 → MJPEG 编码量增大，卡顿更明显
- 推测: 音频线程的 numpy 运算会污染 L2 缓存，挤掉 rpicam-vid 的编码数据，导致编码性能下降
- 建议: 长时间运行用 640x480；720p 可接受；1080p 及以上仅适合短时测试

**结论:** 长时间运行建议用 720p 或更低分辨率。5MP 仅适合短时测试拍照场景。有实时对话需求时建议降至 640x480。

**改动:**
- `camera.py:_serve_mjpeg()` — 添加 `last_time` + 超时 `break`
- 回退 `_ensure_alive` 自动重启逻辑

**结果:** [DONE] 流卡死时断开，重连后恢复。

---

### 2026-06-22: [#12] 浏览器录音按钮 + 增益修复

**问题:** 需要方便的录音验证方式，最好在浏览器页面点一下就能录。

**方案:**
1. `MicMonitor` 添加录音缓冲机制：`start_recording()`/`stop_recording()`/`save_recording()`
2. 采集线程在录音标志置位时向缓冲追加原始音频数据
3. Web UI 添加 "录制 5s" 按钮 → 调 `/record?duration=5` → 阻塞录制 → 返回 WAV 下载

**改动:**
- `mic.py` — 新增 `start_recording()`, `stop_recording()`, `save_recording()` 方法
- `camera.py` — 新增 `/record` HTTP 端点 + 页面录制按钮 + 下载链接
- `main.py` — 新增 `--record N` 参数 (独立录制模式)

**增益陷阱 (重要):**
录音缓冲存的是 **原始数据**（不加监控增益），保存时才统一 **64x + 归一化**。
否则监控增益(8x) × 保存增益(64x) = 512x 会导致底噪炸裂、人声削波。

**用法:**
```bash
# 命令行录制
python3 main.py --record 5

# 浏览器录制: 打开 http://<pi-ip>:8080 → 点击 "录制 5s"
```

**结果:** [DONE] 浏览器录音质量与 `--record` 命令行一致。

---

### 2026-06-22: [#13] 双目屏幕调试 — RST 引脚未存储导致黑屏

**问题:** 两个 GC9D01 屏幕都是只有背光，无画面。

**根因:** 从 `eye_pi_zero.py` 重构到共享 SPI 版 `eye_display.py` 时，`GC9D01.__init__` 中 `rst` 参数只设置了 GPIO.OUT，但没有 `self.rst = rst` 存储。导致 `init_display()` 无法调用 `self._reset(self.rst)` 进行硬件复位，SPI 初始化命令被忽略。

**修复:**
- `GC9D01.__init__` 添加 `self.cs, self.dc, self.rst = cs, dc, rst`
- `init_display()` 首行添加 `self._reset(self.rst)` — 硬件复位后再发初始化命令

**改动:**
- `eye_display.py:GC9D01.__init__()` — 存储 `self.rst`
- `eye_display.py:GC9D01.init_display()` — 添加硬件复位

**屏幕黑屏排查流程 (完整):**

| 步骤 | 操作 | 结果 | 结论 |
|------|------|------|------|
| 1 | 检查 `/dev/spidev*` | spidev0.0 + 0.1 存在 | SPI 驱动正常 |
| 2 | 检查 `config.txt` 的 SPI 配置 | `dtparam=spi=on` 已启用 | 配置正确 |
| 3 | BL 引脚控制 | 背光可开关 | BL 接线正确 |
| 4 | RST 引脚控制 | RST 可像 BL 一样正常输出 | RST 接线正确 |
| 5 | SPI 发 `0x11`(SLPOUT) + `0x29`(DISPON) | 背光明显变亮 | SPI 通信正常，屏幕响应命令 |
| 6 | 跑原版 `eye_pi_zero.py` | 正常显示 ✅ | 屏幕本身没问题 |
| 7 | 完整初始化 + 全屏填色 | 仍然黑屏 | 非代码逻辑问题 |
| 8 | 改用 `writebytes` + 分批发送 | 仍黑屏 | 非 spi.xfer3 兼容性 |
| 9 | 恢复原版 + 升级版混合调试 | 建独立测试文件 | 创建 `tools/dual_eye_test.py` |

**关键发现:**
- `spi.xfer()` 单次传输上限 4096 字节，大数据需分片或改用 `xfer3`
- 屏幕调试必须从最小可验证步骤开始（先 SPI 通信 → 再寄存器配置 → 再像素数据）
- 隔离外设有助于排除干扰

**建立的排查流程 (后续屏幕问题参考):**
```bash
# 1. 确认 SPI 设备
ls -l /dev/spidev*

# 2. 确认引脚可控制
GPIO.output(BL, 0/1)  # 背光
GPIO.output(RST, 0/1) # 复位

# 3. 确认 SPI 通信 (SLPOUT + DISPON)
cmd(0x11); sleep(0.2); cmd(0x29)

# 4. 缩小范围: 原版代码能否跑通?
# 5. 分批发送大数据, 避开 4096 限制
```

**结果:** [DONE] 排查流程已建立，`tools/dual_eye_test.py` 用于独立验证。

---

### 2026-06-22: [#14] 右眼独立测试工具

**问题:** 双目测试时左眼亮、右眼不亮，需隔离排查右眼接线。

**方案:** 创建 `tools/right_eye_test.py`，逻辑与 `eye_pi_zero.py` 完全一致，
仅将引脚从 (CS=5,DC=25,RST=24,BL=23) 改为 (CS=6,DC=27,RST=22,BL=26)。

**文件:** `tools/right_eye_test.py`
**与左眼唯一区别:** PIN_CS=6, PIN_DC=27, PIN_RST=22, PIN_BL=26

**用法:**
```bash
python3 tools/right_eye_test.py
```

**结果:** [DONE] 右眼单眼测试文件可用。

---

### 2026-06-22: [#15] 双目屏花屏/不亮 — 面包板转接接触不良排查

**现象:** 双目可同时点亮，但不定时花屏；单眼直连 Pi 排针正常，走面包板转接就不行。

**排查过程和发现:**

| 检查项 | 结果 |
|--------|------|
| 线材 | 发现部分杜邦线有生锈，更换后好转 |
| 单眼直连排针 | 两个屏幕各自都能正常显示 ✅ |
| 面包板转接 GND | 有一个屏幕的 GND 线不能走面包板，必须直插排针 |
| 面包板转接 SPI 总线 | MOSI 和 SCLK 极易受影响，稍微松动就花屏或不亮 |
| VCC/GND 走面包板 | 同样敏感，接触不良时屏幕有背光但无画面 |

**根因:** 60MHz SPI 信号对线路阻抗极其敏感。面包板 + 杜邦线的弹簧片接触一旦有松动（氧化、生锈、插入深度不够），MOSI/SCLK 信号就畸变，屏幕无法正确接收初始化命令和像素数据。多根线通过面包板转接时，任何一根松了都会导致问题。

**解决方案:**
- 信号线（MOSI、SCLK、CS、DC、RST）尽量不面包板转接，必须确保插紧
- VCC/GND 尽量直连，至少保证 GND 回路低阻抗
- **双目共享 SPI 时，SPI 总线降速到 30MHz**（原 60MHz → 30MHz，容性负载加倍后信号畸变）
- 排查流程: 发现花屏或不亮 → 检查每根线的松紧 → 优先排除面包板转接

**记录到 CLAUDE.md:**
> 新增规则 #11: 面包板只能走 VCC/GND，信号线必须直连。SPI 总线降速规则。

**结果:** [DONE] SPI 降速至 30MHz 后显示稳定。根因为 60MHz 下面包板转接连线阻抗过大、信号畸变。

---

### 2026-06-22: [#16] 全功能集成测试通过

**测试内容:** 双目 + 摄像头 + 麦克风 + Web UI 全部同时运行。

**命令:**
```bash
python3 main.py --dual-eye
```

**测试结果:**

| 模块 | 结果 |
|------|:----:|
| 双目 GC9D01 (共享 SPI0, 30MHz) | ✅ 稳定显示，声音反应正常 |
| 摄像头 (rpicam-vid MJPEG) | ✅ 流正常，切换分辨率可用 |
| 麦克风 (INMP441, ALSA) | ✅ VU 表实时跳动 |
| Web UI (VU 表 + 浏览器录制) | ✅ 访问正常 |
| 全功能并发 | ✅ 全部同时运行无冲突 |

**结论:** Phase 2 全部完成，双目共享 SPI0 + I2S 全双工方案验证通过。

**结果:** [DONE]

---

### 独立测试文件清单

以下文件可用于隔离排查各模块，不依赖 `main.py` 集成入口：

| 文件 | 用途 | 用法 |
|------|------|------|
| `tools/dual_eye_test.py` | 双目共享 SPI0 独立测试 | `python3 tools/dual_eye_test.py` |
| `tools/right_eye_test.py` | 右眼单眼测试 (仅引脚区别于左眼) | `python3 tools/right_eye_test.py` |
| `eye_pi_zero.py` (外部) | 左眼原版单眼测试 | `python3 /home/pi/eye_pi_zero.py` |
| `main.py --record 5` | 麦克风录音验证 (64x增益+归一化) | `python3 main.py --record 5` |
| `main.py --no-camera --no-eye` | 仅麦克风监控 | `python3 main.py --no-camera --no-eye` |
| `main.py --no-mic --no-eye` | 仅摄像头 | `python3 main.py --no-mic --no-eye` |
| `speaker.py` | 扬声器播放 | Web UI 点击播放按钮 |

---

### 2026-06-23: [#17] MAX98357A 扬声器集成 — Web UI 播放控制

**问题:** 需要将 MAX98357A I2S 扬声器集成到系统中，支持在浏览器页面播放 audio/ 目录下的 WAV 文件。

**方案:**
- 创建 `speaker.py`：WAV 读取 → 自动重采样/位深转换 → ALSA PCM_PLAYBACK 输出
- 任意格式 WAV 自动转换为 S32_LE 48kHz 立体声
- Web UI 添加音频文件列表和播放按钮
- 播放后台线程运行，不阻塞 HTTP 服务

**接线:**
| MAX98357A | Pi GPIO | 排针 |
|-----------|---------|:----:|
| VIN | 3.3V | Pin 1 |
| GND | GND | Pin 6 |
| BCLK | GPIO18 (与 INMP441 共享) | Pin 12 |
| LRC | GPIO19 (与 INMP441 共享) | Pin 35 |
| DIN | **GPIO21** | **Pin 40** |

**新增文件:**
- `speaker.py` — 扬声器播放模块
- `audio/` — WAV 音频文件目录

**改动:**
- `camera.py` — 新增 `/play`, `/audio-files` 端点 + 页面播放按钮
- `main.py` — 导入 speaker 模块并注入 HTTP 服务

**结果:** [DONE] Web UI 可点击播放音频文件，扬声器集成验证通过。
