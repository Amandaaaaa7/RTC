# Doll Robot — 树莓派 Zero 2W 多外设集成

将 **INMP441 I2S 麦克风** + **Pi Camera** + **GC9D01 双目屏幕** 集成到一台树莓派 Zero 2W 上。

## 硬件总览

### 接线表

#### 电源

| 信号 | 排针 |
|------|:----:|
| 3.3V | Pin 1, 17 |
| GND | Pin 6, 14 |

#### 左眼 (GC9D01, SPI0)

| 屏幕 | Pi GPIO | 排针 |
|------|---------|:----:|
| VCC | 3.3V | Pin 1/17 |
| GND | GND | Pin 6/14 |
| DIN | **GPIO10 (SPI0_MOSI)** | **Pin 19** |
| CLK | **GPIO11 (SPI0_SCLK)** | **Pin 23** |
| CS | **GPIO5** | **Pin 29** |
| DC | **GPIO25** | **Pin 22** |
| RST | **GPIO24** | **Pin 18** |
| BL | **GPIO23** | **Pin 16** |

#### 右眼 (GC9D01, 共享 SPI0 总线)

| 屏幕 | Pi GPIO | 排针 |
|------|---------|:----:|
| VCC | 3.3V | Pin 1/17 |
| GND | GND | Pin 6/14 |
| DIN | **GPIO10** (与左眼共享) | **Pin 19** |
| CLK | **GPIO11** (与左眼共享) | **Pin 23** |
| CS | **GPIO6** | **Pin 31** |
| DC | **GPIO27** | **Pin 13** |
| RST | **GPIO22** | **Pin 15** |
| BL | **GPIO26** | **Pin 37** |

#### 麦克风 (INMP441, I2S)

| INMP441 | Pi GPIO | 排针 |
|---------|---------|:----:|
| VDD | 3.3V | Pin 1 |
| GND | GND | Pin 6 |
| SCK | **GPIO18 (PCM_CLK)** | **Pin 12** |
| WS | **GPIO19 (PCM_FS)** | **Pin 35** |
| DOUT | **GPIO20 (PCM_DIN)** | **Pin 38** |
| L/R SEL | GND | Pin 39 |

#### 摄像头 (CSI)

CSI 排线直连，不占用 GPIO。

### 接线注意事项

- 所有接线必须先 **断电** 操作（`sudo poweroff`，绿灯熄灭后拔 USB）
- SPI 信号线（MOSI、SCLK、CS、DC、RST）尽量不经过面包板，直接 Pi 排针到屏幕最稳定
- 双目共享 SPI0 时两个屏幕的 DIN 和 CLK 并联到同一 GPIO
- **严禁任何热插拔**，否则可能烧毁接口或导致系统崩溃

---

## 操作流程

### 第一步：烧录系统

1. 下载 [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. 选择设备: `Raspberry Pi Zero 2W`
3. 选择系统: `Raspberry Pi OS (32-bit or 64-bit)`
4. 选择 SD 卡
5. 点齿轮图标配置:
   - 勾选 **Enable SSH** → 设置用户名 `pi` + 密码
   - 勾选 **Configure wireless LAN** → 填入 WiFi 名称和密码
6. 烧录完成后 SD 卡插入 Pi，通电

### 第二步：连接树莓派

#### 通过 MobaXterm SSH 连接

1. 打开 MobaXterm
2. 点左上角 **Session → SSH**
3. **Remote host** 填入 Pi 的 IP 地址（进路由器后台查看，或手机 App 扫描）
4. **Username** 填入 `pi`
5. 点 OK，输入密码

#### 配置公钥认证（推荐）

本项目建议配置为**仅允许公钥登录，禁用密码登录**。这样可免去每次输入密码，也便于 Claude Code 等工具远程执行命令。

**1. 在本地生成 SSH key**

在本地电脑（MobaXterm / Git Bash / PowerShell）执行：

```bash
ssh-keygen -t ed25519 -C "your_name@pc" -f ~/.ssh/id_ed25519 -N ""
```

**2. 复制公钥到树莓派**

```bash
ssh-copy-id pi@<pi-ip>
# 或在本局域网使用 mDNS 主机名
ssh-copy-id pi@pi.local
```

按提示输入树莓派密码（仅一次）。

**3. 验证免密登录**

```bash
ssh pi@<pi-ip> "echo ok"
```

如果直接显示 `ok` 而无需输入密码，说明配置成功。

**4. 禁用密码登录（安全加固）**

公钥登录验证成功后，在树莓派上关闭密码认证：

```bash
sudo nano /etc/ssh/sshd_config
```

确保以下两项设置：

```text
PasswordAuthentication no
PubkeyAuthentication yes
```

重启 SSH 服务：

```bash
sudo systemctl restart ssh
```

此后该树莓派**只能通过已授权的公钥登录**，密码登录会被拒绝。

**5. 给其他用户/电脑授权**

如果有其他电脑需要连接这块树莓派，将该电脑的 `~/.ssh/id_ed25519.pub` 公钥内容追加到树莓派的：

```bash
~/.ssh/authorized_keys
```

每行一个公钥即可。

#### 验证连接

```bash
# 查看系统信息
cat /etc/os-release | head -3
uname -m

# 查看 IP
ip a | grep inet
```

### 第三步：安装依赖

```bash
sudo apt update
sudo apt install -y python3-numpy python3-alsaaudio python3-pil python3-venv rpicam-apps
```

### 第四步：启用外设驱动

```bash
# I2S 麦克风驱动 (GPIO18-20)
echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a /boot/firmware/config.txt

# 摄像头驱动
echo "camera_auto_detect=1" | sudo tee -a /boot/firmware/config.txt

# SPI 接口 (用于屏幕)
echo "dtparam=spi=on" | sudo tee -a /boot/firmware/config.txt
```

重启：

```bash
sudo reboot
```

重启后等待 1 分钟，重新用 MobaXterm SSH 连接。

#### 验证驱动

```bash
# 检查 SPI
ls -l /dev/spidev*

# 检查 I2S 麦克风
arecord -l

# 检查摄像头
rpicam-vid --list-cameras
```

### 第五步：上传代码

#### 通过 MobaXterm SFTP 上传

1. 在 MobaXterm 左侧的 SFTP 浏览器中，导航到 `/home/pi/`
2. 点 **新建文件夹**，命名为 `mic_speaker_camera_test`
3. 从 Windows 资源管理器将项目文件拖入该文件夹
4. 确保以下文件都已上传：

```
mic_speaker_camera_test/
├── main.py              # 集成入口
├── mic.py               # 麦克风模块
├── camera.py            # 摄像头 + HTTP 服务
├── eye_display.py       # GC9D01 动画眼睛
├── DEBUG_LOG.md         # 调试日志
├── CLAUDE.md            # 项目规则
├── setup/               # 配置脚本
│   ├── enable_all.sh
│   ├── enable_i2s.sh
│   └── enable_camera.sh
├── docs/
│   └── hardware.md      # 详细接线文档
└── tools/
    ├── dual_eye_test.py    # 双目独立测试
    └── right_eye_test.py   # 右眼独立测试
```

#### 或者通过 SCP 命令行上传

在 Windows CMD 或 PowerShell 中：

```powershell
scp -r d:\path\to\mic_speaker_camera_test\* pi@<pi-ip>:/home/pi2/pi_affe_sys/
```

### 第六步：启动完整程序（推荐：人脸追踪 + 反应式语音）

日常运行使用**人脸追踪 + 反应式语音**模式：眼睛跟随人脸移动，检测到说话并静默后，随机播放一段情绪语音。

```bash
cd /home/pi2/pi_affe_sys

# 清理旧进程
pkill -9 -f 'random_audio_player\.py'
pkill -9 -f 'main\.py'
pkill -9 -f 'rpicam-vid'
sleep 1

# 测试扬声器（统一 30% 音量）
ffmpeg -y -loglevel error -i /home/pi2/pi_affe_sys/audio_assets/vo/Surprise/1.mp3 -af "volume=0.3" -f wav -ar 48000 -ac 2 -c:a pcm_s32le - | aplay -D hw:1,0 -

# 启动主程序：双目 + 人脸追踪 + 反应式语音（单命令）
nohup python3 -u main.py --dual-eye --face --reactive-voice > main_eye_face.log 2>&1 & echo "EYE_PID: $!"

# 查看日志
tail -f main_eye_face.log
```

参数说明：

- `--dual-eye`：启用双目屏幕
- `--eye-debug-overlay`：在左眼屏幕中央叠加状态文字（追踪来源 / 语音状态 / 麦克风电平），字号与位置已按瞳孔中心居中
- `--face`：启用摄像头人脸追踪（眼睛跟随人脸）
- `--face-detector yunet|caffe`：选择 CPU 检测器，默认 YuNet；Caffe 用于 A/B
- `--face-fps 2`：设置检测目标频率；眼睛每帧读取最新结果，不再二次等待 3 秒
- `--gaze-x-sign/--gaze-y-sign`、`--gaze-x-gain/--gaze-y-gain`、
  `--gaze-x-bias/--gaze-y-bias`、`--gaze-deadband`：真机坐标标定参数

首次使用 YuNet 前下载模型：

```bash
bash models/download_models.sh
```

最小人脸—双眼标定启动示例（关闭声音，避免干扰）：

```bash
python3 main.py --dual-eye --face --face-detector yunet --face-fps 2 --no-mic --no-voice
```

如果左右或上下方向相反，先调整对应 `sign`；再按顺序校准 `gain`、`bias`、
`deadband`。不要同时改多个参数，否则无法判断是哪一项产生效果。

### Day 1 coordinate calibration

The final reproducible calibration is stored in one file:

    config/gaze_calibration.json

Edit this file after a physical nine-point calibration; do not scatter the
final values across command lines or source files.

| Field | Meaning |
|---|---|
| x_sign, y_sign | Reverse an axis when the observed direction is opposite. |
| x_gain, y_gain | Set horizontal and vertical movement amplitude. |
| x_bias, y_bias | Correct a stable centre offset. |
| deadband_x, deadband_y | Suppress small detector jitter near the centre. |
| smoothing | Display-only continuous-motion smoothing. |

main.py with --face loads this file automatically. A --gaze-* option is only
a temporary A/B override and does not write back to the JSON file.

For every valid calibration sample, open:

    http://<pi-ip>:8080/face-target

When detected is true, record camera_x/y (pre-mapping), eye_x/y (mapped
target), and score (YuNet confidence). Do not record a response where
detected is false.

- `--reactive-voice`：启用反应式语音，说话结束后随机播放 `audio_assets/vo/` 下的 MP3
  - 默认阈值 `0.04`，静音结束 `0.8s`，音量 `0.3`
  - **15 秒空闲触发**：如果连续 15 秒没有任何播放（无论是否有人说话），自动随机播放一条情绪音频
  - 无需 ASR/LLM/API key/网络
- 默认会自动降低摄像头分辨率到 `320x240` 以节省 CPU

如需调整反应式语音参数：

```bash
# 降低触发阈值、缩短静音等待
python3 main.py --dual-eye --face --reactive-voice --reactive-threshold 0.04 --reactive-silence 0.8 --reactive-volume 0.1

# 固定只播放某一种情绪
python3 main.py --dual-eye --face --reactive-voice --reactive-emotion Joy
```

如果不用摄像头追踪，只跑眼睛动画 + 反应式语音：

```bash
python3 main.py --dual-eye --no-camera --reactive-voice
```

> 旧版 `random_audio_player.py` 仍保留在仓库中，作为独立循环播放的备用方案。

### 配置开机自启（systemd）

想让树莓派通电后自动运行完整程序，可以创建 systemd 服务：

```bash
# 1. 创建服务文件（以 pi 用户运行，工作目录为项目根目录）
sudo tee /etc/systemd/system/doll-robot.service > /dev/null <<'EOF'
[Unit]
Description=Doll Robot - voice/pi-minions-running
After=network.target sound.target
Wants=network.target sound.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/pi_affe_sys
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 -u main.py --dual-eye --face --reactive-voice --eye-style lovot_lianlian --eye-debug-overlay
ExecStop=/bin/kill -INT $MAINPID
TimeoutStopSec=10
Restart=on-failure
RestartSec=5
StandardOutput=append:/home/pi/pi_affe_sys/logs/doll-robot.log
StandardError=append:/home/pi/pi_affe_sys/logs/doll-robot.log

[Install]
WantedBy=multi-user.target
EOF

# 2. 重载并启用开机自启
sudo systemctl daemon-reload
sudo systemctl enable doll-robot.service

# 3. 立即启动
sudo systemctl start doll-robot.service

# 4. 查看状态和日志
sudo systemctl status doll-robot.service
sudo journalctl -u doll-robot.service -f
```

常用命令：

```bash
sudo systemctl stop doll-robot.service      # 停止
sudo systemctl restart doll-robot.service   # 重启
sudo systemctl disable doll-robot.service   # 取消开机自启
```

### 第七步：浏览器查看

打开 `http://<pi-ip>:8080`：

| 功能 | 位置 |
|------|------|
| 摄像头实时画面 | 页面主体 |
| VU 电平表 | 顶部状态栏 |
| FPS 显示 | 顶部状态栏 |
| 分辨率切换 | 按钮组，点击切换 |
| 录音 5s | 录制按钮，点击后等待下载 WAV |

---

## 模块分步测试

### 测试双目屏幕

接线确认后，只接屏幕独立运行：

```bash
cd /home/pi2/pi_affe_sys
python3 tools/dual_eye_test.py
```

两个屏幕应依次显示红绿蓝纯色，然后进入动画。

### 测试麦克风

```bash
# 录音 5 秒
python3 main.py --record 5

# 可配合麦克风实时监控
python3 main.py --no-camera --no-eye --console-vu
```

对着麦克风吹气或说话，VU 表应有明显跳动。
录音生成的 `rec_*.wav` 文件可通过 SFTP 拖到电脑播放。

### 测试摄像头

```bash
# 仅摄像头 + 麦克风（无屏幕）
python3 main.py --no-eye
```

浏览器打开 `http://<pi-ip>:8080`，应有摄像头画面 + VU 表。

### 人脸追踪详细说明

这是当前已验证的完整人脸追踪链路：

1. **下载人脸检测模型**（Pi 上执行一次即可）：

   ```bash
   cd /home/pi2/pi_affe_sys
   bash models/download_models.sh
   ```

   下载完成后 `models/` 目录下应有：

   - `face_detection_yunet_2023mar.onnx`（默认）
   - `deploy.prototxt`
   - `res10_300x300_ssd_iter_140000.caffemodel`（Caffe A/B）

2. **确认双目屏幕左右**（如果还没确认过）：

   ```bash
   python3 tools/identify_eyes.py
   ```

   站在机器人正面，观察两个屏幕的 L/R 标签，确认代码里的 `LEFT/RIGHT` 与实际物理左右一致。

3. **启动双目 + 人脸追踪（默认）**：

   ```bash
   python3 main.py --dual-eye --face
   ```

   - 摄像头分辨率会自动降到 `320x240`（降低 CPU）
   - 默认使用 YuNet，目标检测频率为 **2 fps**，可用 `--face-fps` 调整
   - 眼睛每个逻辑帧读取 tracker 的最新结果，不再额外等待 3 秒
   - 检测到人脸后，眼睛用 ease-out 曲线移动到目标位置
   - 无人脸时回退到运动检测；运动也没有时进入自主扫视

   使用旧 Caffe SSD 做同机 A/B：

   ```bash
   python3 main.py --dual-eye --face --face-detector caffe --face-fps 2
   ```

   如果画面中经常有多个人脸，会启用多目标锁定：

   - **只有一个人脸时**：行为与优化前一致，眼睛实时跟随该人脸移动；
   - **检测到多个人脸时**：通过 IOU 匹配锁定当前注视目标。切换发生在三种情况：
     1. 锁定目标丢失超过保持时间 → 立即锁定当前面积最大的人脸；
     2. 另一个人脸连续多帧面积显著更大（默认 2 帧、1.3 倍） → 切换到这个人脸；
     3. 场景从多人变回单人 → 立即锁定仅剩的那个人脸。

   推荐启动命令：

   ```bash
   python3 main.py --dual-eye --face --face-fps 2 \
     --face-hold-timeout 0.3 \
     --face-switch-ratio 1.3 \
     --face-iou-threshold 0.3 \
     --face-dominant-frames 2
   ```

#### 入睡 / 唤醒（无人闭眼、有人立刻睁眼）

在 `--face` 人脸追踪基础上，可开启“两分钟看不见人就闭眼，直到下一次人出现时立刻睁眼”：

```bash
python3 main.py --dual-eye --face --sleep-on-absence
```

行为：

- **计时起点**：`FaceTracker` 判定“锁定的人脸已丢失”那一刻起累计（锚点与锁定状态解耦，锁定清除后仍继续累计，不受 `target_hold_timeout` 抖动影响）。
- **闭眼（falling）**：无脸时长达到 `--sleep-absence-timeout`（默认 **120 秒**）后，眼睛播放入睡动画 —— 复用 `sleep_gifs_cry.py` 的 **20 秒完整轨迹**（软化 → 两次慢眨眼 → 保持 → 完全闭上）。此阶段一旦检测到人脸，**立刻睁眼**。
- **睡着（asleep）**：动画播完后进入“全闭”保持；此时需要人脸**持续出现** `--sleep-wake-confirm`（默认 **0.15 秒**）才睁眼，避免路过的人影造成抖动。
- **默认关闭**：不加 `--sleep-on-absence` 时行为与之前完全一致。

新增参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--sleep-on-absence` | 关 | 启用无人入睡 / 有人唤醒 |
| `--sleep-absence-timeout` | 120.0 | 无脸多少秒后开始闭眼 |
| `--sleep-wake-confirm` | 0.15 | 睡着后唤醒所需人脸持续秒数 |

实现涉及 `face_tracker.py`（无脸/有脸时长读取）、`eye_expressions.py`（新增 `SleepExpression`）、`eye_display.py`（睡/醒状态机）、`clock.py`（可测试的时钟入口）、`tests/test_sleep_wake.py` 等，详见下方“文件结构”。

   参数说明：

   | 参数 | 默认值 | 说明 |

   |---|---|---|
   | `--face-hold-timeout` | 0.3 s | 锁定目标匹配失败后仍保持原方向的时间。画面还有人时，超时后直接锁定最大脸。 |
   | `--face-switch-ratio` | 1.3 | 新人脸面积需达到锁定目标面积的多少倍才允许切换。用于“显著更大才切换”。 |
   | `--face-iou-threshold` | 0.3 | 判断两帧之间是否为同一人脸的框重叠度阈值。 |
   | `--face-dominant-frames` | 2 | 显著更大的人脸需连续满足多少帧才切换。避免单帧抖动切换。 |

   调参建议：

   ```bash
   # 更保守：保持更久、切换更难
   python3 main.py --dual-eye --face --face-hold-timeout 1.0 --face-switch-ratio 2.0

   # 更灵敏：保持更短、切换更容易
   python3 main.py --dual-eye --face --face-hold-timeout 0.2 --face-switch-ratio 1.0
   ```

   如果只需要运动追踪（不需要识别脸，只检测画面变化），可用：

   ```bash
   python3 main.py --dual-eye --motion
   ```

4. **浏览器查看**：

   打开 `http://<pi-ip>:8080`：

   - 页面主体：摄像头实时画面
   - 红色十字：当前眼睛注视目标（人脸/运动/自主扫视）
   - 顶部状态栏：VU 电平、FPS、分辨率切换、录音/扬声器按钮

### 左眼调试叠加层

启动时加上 `--eye-debug-overlay`，左眼屏幕会在瞳孔中心位置叠加半透明状态文字：

```bash
python3 main.py --dual-eye --face --eye-debug-overlay
```

显示内容（从上到下）：

| 行 | 示例 | 说明 |
|----|------|------|
| 追踪来源 | `[face]` / `[motion]` / `[idle]` | 当前眼睛注视目标类型 |
| 语音状态 | `等待说话` / `正在播放TTS` | 反应式语音模块状态 |
| 附加信息 | `Satisfaction` | 当前播放的情绪或文件名提示 |
| 麦克风电平 | `mic 0.0523` | 实时 RMS 电平 |

**字体 fallback**：树莓派上常见的 `DroidSansFallbackFull.ttf` 只有中文字形、没有拉丁字母。`eye_debug_overlay.py` 会自动把一行文本切成“拉丁段”和“中文段”，分别用不同字体绘制，避免中英混排时出现方框。

**居中逻辑**：文字块的视觉中心按每行真实包围盒计算，并精确对准屏幕中心（瞳孔位置），而不是简单顶对齐。

5. **停止**：

   ```bash
   pkill -f "python3 main.py"
   pkill -f rpicam-vid
   ```

### 当前追踪逻辑参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 人脸检测目标频率 | 2 fps | `--face-fps` 可调，YuNet/Caffe 独立控制 |
| 眼睛读取间隔 | 40 ms | 每个逻辑帧读取 tracker 最新结果，不排队 |
| 眼睛移动 | 25 fps 平滑 | 读取新目标后逐帧 ease-out |
| 眼睛渲染帧率 | 25 fps | `eye_display.py` 动画循环 |
| 人脸追踪分辨率 | 320x240 | `--face` 时自动降低 |
| 优先级 | 人脸 > 运动 > 自主扫视 | `eye_display.py` 目标选择策略 |
| 人脸目标保持时间 | 0.3 s | `--face-hold-timeout`，锁定目标丢失后仍保持注视 |
| 人脸切换面积比 | 1.3 | `--face-switch-ratio`，新人脸需明显更大才切换 |
| 人脸 IOU 阈值 | 0.3 | `--face-iou-threshold`，判断同一人脸的框重叠阈值 |

---

### 眼睛样式

项目内置可切换的眼睛样式库 (`eye_styles.py`)，共 **20 款**，全部样式自动继承相同的交互能力
（眨眼状态机、idle 扫视、人脸/运动追踪、对声音睁眼、语音状态→瞳孔/视线映射）。

**架构**：渲染层 (`eye_render.py`，纯 Pillow、无硬件依赖) 与运行时 (`eye_display.py`,
GC9D01 驱动 + 动画状态机) 已拆分——新增/调整样式只需改 `eye_styles.py`，可在 x86 开发机上
离线预览，不必上树莓派。

**样式迭代源头**：`eye_styles.py` / `eye_render.py` / `tests/test_eye_layers.py` 与独立仓库
**doll-eye-styles**（纯软件样式库，公开 API 契约：`WIDTH/HEIGHT/draw_eye/_init_cache` +
`get_style/list_styles/DEFAULT_STYLE`）保持同步。样式调参、新样式、GIF 预览
（idle/cry/happy/angry/dislike/miosis 表情动画 contact sheet）都在那个仓库做，
验证通过后把上述 3 个文件同步回本仓库（`eye_styles.py` 需去 BOM）。

#### 内置样式

| 样式名 | 描述 |
|--------|------|
| `pi2_light_blue` | 浅色底蓝色虹膜圆眼 (默认) |
| `pi_dark_cat_green` | 深色荧光绿猫眼 (pi 默认) |
| `pi_dark_orange` | 深色底橙虹膜圆眼 |
| `pi2_light_green` | 浅色底绿色虹膜圆眼 |
| `p1_jingdian` | Page1 经典：真诚无攻击性的蓝绿圆眼 |
| `p1_yuanyuan` | Page1 圆圆：活泼俏皮的橘色圆眼 |
| `p1_yongyong` | Page1 勇勇：成熟智慧的棕黄椭圆眼 |
| `p1_rourou` | Page1 柔柔：温柔脆弱的红粉圆眼 |
| `p1_youyou` | Page1 悠悠：宁静思考的墨绿椭圆眼 |
| `p2_shasha` | Page2 沙沙：震惊呆愣的深蓝小圆眼 |
| `p2_shuoshuo` | Page2 烁烁：神秘疏离的紫蓝星芒眼 |
| `p2_gugu` | Page2 咕咕：失焦无害的无高光绿黄眼 |
| `p2_xixi` | Page2 淅淅：温柔潮湿的翠绿圆眼 |
| `p2_sisi` | Page2 思思：安静专注的墨绿圆眼 |
| `p2_linlin` | Page2 凛凛：机械冷感的湖蓝圆眼 |
| `p2_mengmeng` | Page2 梦梦：梦幻童话的紫色星芒眼 |
| `p2_liuliu` | Page2 溜溜：机警野性的猫眼梯形高光 |
| `p2_qiqi` | Page2 奇奇：华丽游戏风的蓝绿大眼+睫毛 |
| `p2_xiuxiu` | Page2 秀秀：稳重镜面感的橘棕大眼+睫毛 |
| `lovot_lianlian` | LOVOT 恋恋：温暖含情的棕红圆眼 (当前生产样式) |

#### 内置表情

| 表情名 | 中文 | 说明 |
|--------|------|------|
| `happy` | 开心 | 双眼睑向中间眯起 |
| `angry` | 生气 | 上眼睑倾斜下压 |
| `cry` | 哭 | 上眼睑倾斜下压 + 泪珠 |
| `dislike` | 嫌弃 | 半眯 + 右偏斜视 |
| `miosis` | 缩瞳 | 眼睑闭合时瞳孔同步缩小 |
| `surprise` | 惊讶 | 手写睁眼 + 瞳孔放大 |
| `surprise1.0` | 惊讶 1.0 | JSON 锚点 `Surprise@1.0` |
| `amusement1.0` | 开心 1.0 | JSON 锚点 `Amusement@1.0` |
| `calmness1.0` | 平静 1.0 | JSON 锚点 `Calmness@1.0`（用于“再见/拜拜”） |
| `excitement0.6` | 兴奋 0.6 | JSON 锚点 `Excitement@0.6`（用于“你好/哈喽”；停在睁眼峰值帧，避免末帧收眼） |
| `sadness0.6` | 难过 0.6 | JSON 锚点 `Sadness@0.6` |
| `sadness1.0` | 难过 1.0 | JSON 锚点 `Sadness@1.0` |
| `interest0.6` | 好奇 0.6 | JSON 锚点 `Interest@0.6`（较柔和） |
| `interest1.0` | 好奇 1.0 | JSON 锚点 `Interest@1.0`（哼歌反应，更强烈的好奇/倾听） |

#### 切换样式

```bash
# 命令行参数切换，`--help` 会动态列出全部可用样式
python3 main.py --dual-eye --eye-style pi_dark_cat_green
python3 main.py --dual-eye --eye-style p1_jingdian
python3 main.py --dual-eye --eye-style lovot_lianlian
```

#### 快速浏览全部样式

不想为每款样式重启程序？用样式浏览器热切换（只启动屏幕，秒启动，按键秒切，
样式名短暂显示在左眼屏幕上）:

```bash
sudo systemctl stop doll-robot              # 先停生产服务（避免抢占 SPI/GPIO）
python3 tools/eye_style_gallery.py
python3 tools/eye_style_gallery.py --auto 5 # 自动轮播：先切完一圈表情再换样式
sudo systemctl start doll-robot             # 浏览完恢复生产
```

按键：

| 按键 | 作用 |
|------|------|
| 空格 / 回车 / `n` / `↓` | 下一个样式 |
| `p` / `↑` | 上一个样式 |
| `→` / `e` | 下一个表情（正常→开心→生气→哭→嫌弃→缩瞳，循环） |
| `←` | 上一个表情 |
| 数字 + 回车 | 跳转到第 N 个样式 |
| `q` | 退出 |

表情动画与 doll-eye-styles 的 GIF 生成器同源（`eye_expressions.py`，5 秒循环
时间线），在 Pi 上**实时参数化渲染**（不播放 GIF 文件），切换样式时表情保持，
方便对比同一表情在不同样式上的效果。`EyeDisplay.set_expression(name)` 也是
后续"语音情绪→眼睛表情"的现成接口。新增的表情 `interest0.6` 来自 JSON 锚点
网格 `Interest@0.6`，用于"想听歌/唱歌"触发时的眼睛反应。

#### 添加新样式（离线迭代，无需树莓派）

1. 在 `eye_styles.py` 用 `make_style()` 追加一个样式 dict，并注册进 `EYE_STYLES`
   （可调字段：`eye_shape` 圆/椭圆/鸡蛋、`iris_gradient_mode` radial/vertical/radial_ring、
   `iris_top_color`/`iris_bottom_color` 垂直渐变色、`iris_ring_color`/`iris_ring_radius` 环纹、
   `iris_bottom_rim` 底部亮边、`pupil` 形状 round/cat/ellipse/diamond + 模糊/描边、
   `limbal_ring` 角膜缘环、`glints` 多高光列表 (6 种形状，带 glow/rotation/blur)、
   `eyelashes` 睫毛线段、`iris_shadow` 软阴影、`sclera_vignette` 暗角、`vividness` 增强）
2. 跑 `python3 -m unittest tests.test_eye_layers` —— 导出全部样式的分层 PNG 画廊并断言 160×160
3. 浏览器打开 `outputs/eye_layers/index.html` 目视验收（该目录已 gitignore，纯本地预览产物）
4. 单样式任意姿态预览：`python3 tools/preview.py --style <名> --gaze X Y --eyelid 60 --pupil-scale 1.08`

渲染器还支持表情参数（`eyelid_tilt` 生气斜眉、`eyelid_flatten`、`lower_eyelid_cup/tilt`、
`cry_glint_data` 旋转泪珠、`glint_scale`/`iris_scale` 缩瞳联动），当前运行时未使用，
是后续"情绪→眼睛表情"接线的现成接口（参考 doll-eye-styles 的表情 GIF 生成器）。

#### 从浅色版改深色版的设计要点

1. **巩膜/背景必须纯黑** — 任何灰度都会让屏幕发灰、显脏
2. **关掉巩膜高光/阴影叠加** — 黑底上再加高光会出现不自然的灰斑
3. **虹膜用高饱和色** — 小屏幕 + BGR565 量化会吃掉低饱和细节，荧光绿/橙比暗红/暗紫更醒目
4. **边缘与中心形成对比** — 中心最亮、边缘略深，能强化球体感
5. **纹理颜色要比虹膜中心更亮或更饱和** — 否则会被径向渐变吞掉
6. **瞳孔与背景同色** — 黑底 + 黑瞳孔让虹膜成为唯一焦点
7. **猫眼用竖椭圆** — 高度约虹膜直径的 75%，宽度约高度的 66%，带轻微高斯模糊更自然

---

## 浏览器 Web UI

打开 `http://<pi-ip>:8080` 后：

| 功能 | 位置 |
|------|------|
| 摄像头实时画面 | 页面主体 |
| VU 电平表 | 顶部状态栏 |
| FPS 显示 | 顶部状态栏 |
| 分辨率切换 | 按钮组，点击切换 |
| 录音 5s | 录制按钮，点击后等待下载 WAV |

---

## 项目结构

```
mic_speaker_camera_test/
├── main.py              # 集成入口, threading 并行各组件
├── mic.py               # 麦克风后台电平监控 (pyalsaaudio)
├── camera.py            # 摄像头 MJPEG HTTP 流 + Web UI
├── eye_display.py       # GC9D01 动画眼睛 (单目/双目 + 追踪/自主扫视/表情)
├── eye_render.py        # 纯 Pillow 眼睛渲染层 (无硬件依赖, 可离线预览)
├── eye_styles.py        # 可切换的眼睛样式库 (20 款, make_style 工厂)
├── eye_expressions.py   # 表情动画 (开心/生气/哭/嫌弃/缩瞳/入睡, 参数时间线)
├── eye_debug_overlay.py # 左眼调试叠加层 (状态文字居中显示, 中/英混排 fallback)
├── face_tracker.py      # 摄像头人脸追踪 (OpenCV DNN, 本地推理 + 无脸/有脸时长)
├── clock.py             # 时钟统一入口 (生产经此取时间, 测试可替换为假时钟)
├── motion_tracker.py    # 摄像头运动检测 (帧差分, 给眼睛做 fallback 目标)
├── speaker.py           # MAX98357A 扬声器播放 (Web UI 触发)
├── random_audio_player.py  # 独立随机情绪音频播放器(备用)
├── DEBUG_LOG.md         # 调试日志
├── CLAUDE.md            # 项目规则
├── setup/               # Pi 端一键配置脚本
├── docs/
│   └── hardware.md      # 接线文档
├── voice/               # 语音交互模块
│   ├── module.py        # 云端 ASR/LLM/TTS 语音调度器
│   ├── reactive_module.py  # 反应式语音模块(无网络)
│   ├── audio.py         # 基于回调的录音器
│   ├── emotion_engine.py   # 情绪映射
│   ├── memory.py        # 对话记忆
│   ├── xp.py            # 经验系统
│   ├── config.py        # 语音配置
│   ├── emotion_dialogue.py # 兼容入口
│   ├── test_minimal.py  # ASR/LLM 最小闭环测试
│   └── test_minimal_reactive.py  # 反应式语音最小测试
├── models/              # OpenCV DNN 人脸检测模型 (运行 download_models.sh 下载)
├── tests/
│   └── test_eye_layers.py  # 眼睛样式分层画廊导出 + 160x160 断言 (离线预览)
│   └── test_sleep_wake.py  # 入睡动画轨迹 + 睡/醒状态机 (假时钟冻结测试)
└── tools/
    ├── dual_eye_test.py      # 双目独立测试
    ├── right_eye_test.py     # 右眼独立测试
    ├── identify_eyes.py      # 双目屏幕左右识别测试
    ├── eye_style_gallery.py  # 眼睛样式浏览器 (按键热切换全部样式)
    └── preview.py            # 单样式指定姿态离线预览 CLI
```

## 架构规则

1. **每个外设独立模块**, 后台线程运行, 线程安全接口对外暴露状态
2. **HTTP 服务**是中央仪表盘, 提供摄像头 + 传感器状态的 Web UI
3. **主线程**仅负责编排生命周期, 不阻塞
4. **ALSA 设备**必须使用 `pyalsaaudio` (而非 `sounddevice`)
5. **I2S 固定参数**: 48kHz, S32_LE, 2ch (取左声道), `hw:1,0`
6. **双目共享 SPI0**: 两个 GC9D01 共用 MOSI/SCLK, 独立 CS/DC/RST/BL
7. **严禁任何热插拔** — 所有接线必须断电操作
8. **双目 SPI 降速至 30MHz** — 容性负载加倍，60MHz 信号畸变

## GPIO 占用

| GPIO | 用途 | GPIO | 用途 |
|------|------|------|------|
| **5** | **左眼 CS** | **18** | **I2S BCLK** |
| **6** | **右眼 CS** | **19** | **I2S LRCLK** |
| **10** | **SPI0 MOSI (共享)** | **20** | **I2S mic DOUT** |
| **11** | **SPI0 SCLK (共享)** | **22** | **右眼 RST** |
| **23** | **左眼 BL** | **24** | **左眼 RST** |
| **25** | **左眼 DC** | **26** | **右眼 BL** |
| **27** | **右眼 DC** | 0-4,7-9,12-17,21 | 空闲/预留 |

预留 GPIO21 给 MAX98357A 扬声器 (I2S playback)。

## 语音情绪对话迁移状态（进行中）

### 已完成的改动

- 项目已从 `eye_camera` 重命名为 `pi_affe_sys`。
- 新增 `voice/` 模块：
  - `voice/audio.py`：基于 `pyalsaaudio` 的阈值触发录音 + WAV/MP3 播放包装。
  - `voice/emotion_dialogue.py`：录音 → Google ASR（zh-CN）→ 阿里云 DashScope `qwen-plus` 情绪分析 → 播放 `audio_assets/vo/` 下对应情绪的 MP3。
  - `voice/module.py`：统一语音交互调度器（ASR/LLM/TTS 可插拔后端）。
  - `voice/reactive_module.py`：反应式语音模块，无需 ASR/LLM，说话结束后直接随机播放情绪音频。
  - `voice/test_minimal.py`：带 ASR/LLM 的最小闭环测试脚本。
  - `voice/test_minimal_reactive.py`：反应式语音独立测试脚本。
- 将原语音项目的 `audio_assets/vo/`（162 个 MP3，27 种情绪）纳入仓库，避免在 Pi 上转换 WAV。
- `main.py` 已集成 `--reactive-voice`，可与人脸追踪、双目屏幕一起单命令运行。

### Pi 上待完成的步骤

1. **安装离线 Python 依赖**

   由于 Pi 当前 WiFi 到 PyPI 不稳定，建议在另一台 Linux 电脑上下载以下包及其依赖，再复制到 Pi 安装：

   ```bash
   # 在联网 Linux 电脑上下载
   pip download dashscope speechrecognition pydub -d ./voice_pkgs
   ```

   然后复制到 Pi：

   ```bash
   scp -r ./voice_pkgs pi@pi.local:/home/pi/
   ssh pi@pi.local "pip3 install --no-index --find-links /home/pi/voice_pkgs dashscope speechrecognition pydub --break-system-packages"
   ```

2. **配置 API Key**

   编辑 `/home/pi2/pi_affe_sys/voice/config.py`，把 `QIANWEN_API_KEY` 替换为真实的 DashScope API Key：

   ```python
   QIANWEN_API_KEY = "your-real-key"
   ```

   或者通过环境变量设置：

   ```bash
   export DASHSCOPE_API_KEY="your-real-key"
   ```

3. **运行最小闭环测试**

   ```bash
   cd /home/pi2/pi_affe_sys
   python3 voice/test_minimal.py
   ```

   预期流程：
   - 等待语音（LED/日志提示）
   - 检测到说话后开始录音，静默 1.5s 后停止
   - Google ASR 识别文字
   - DashScope 分析情绪
   - 播放 `audio_assets/vo/<情绪>/` 下的随机 MP3

### 已知问题 / 后续优化

- `--reactive-voice` 已集成到 `main.py`，无需网络即可运行；云端 ASR/LLM 语音对话（`VoiceModule`）仍为可选功能，需配置 API key。
- Google ASR 在国内网络环境可能不稳定，建议后续增加火山引擎 ASR 作为备选。
- `requirements.txt` 中的 `alsaaudio` 已修正为 `pyalsaaudio`。

### 相关分支

- 当前活跃分支：`voice/pi-minions-running`（Pi 192.168.31.18 生产运行分支）
  - 扁平音频文件支持（`minions/` 目录，文件名格式 `009_calmness_i1.mp3`）
  - 15 秒空闲主动播放
  - `audio_assets/vo/` 已在 git 中忽略（本地文件，不随 git 更新）
- 主分支：`main`
- 开发分支：`voice/minimal-loop`

### 眼神策略

`--gaze-policy` 控制眼睛何时采纳 FaceTracker 的唯一 latest 坐标：

| 策略 | 行为 | 用途 |
|---|---|---|
| `continuous`（默认） | 每次采样立即采用最新人脸目标。 | 暴露端侧性能上限和 continuous 基准。 |
| `fixed` | 首次有效人脸目标锁定；后续人脸移动、短暂丢失、motion 与 idle 回退均不改变眼睛目标，且关闭注视 jitter。 | 隔离渲染/SPI 的稳定性。 |
| `blink_latched` | 首次有效目标立即采用；之后只保留一个最新 pending 目标，并在下一次眼睑完全闭合时采用。 | 更自然的产品行为。 |

`blink_latched`行为：

- 人脸第一次出现：立即看过去，不等眨眼；
- 人脸移动：底层继续追踪，但眼睛暂时保持当前注视；
- 下一次自然眨眼闭合：采用最新坐标；
- 新结果只覆盖 pending，不形成轨迹队列；
- 人脸短暂漏检 0.5–1 秒：保持原目标；
- 大位移可把下一次眨眼提前到 300–700 ms，但不要立即机械跳转；
- deadband 内的小抖动不产生 pending retarget。

三种策略都不建立坐标队列，且 tracker 推理频率仍由 `--face-fps` 单独控制：

```bash
python3 main.py --dual-eye --face --no-mic --no-voice --gaze-policy fixed
python3 main.py --dual-eye --face --no-mic --no-voice --gaze-policy blink_latched
```

### 首次见人反应

行为定义：
无人时维持现有自然待机；确认首次人脸进入后，先立即将 gaze 转向人，同时在约 1 秒内呈现“眼睛稍睁大＋瞳孔/高光活跃”的兴奋感；期间始终盯着人；人持续在场时不重复触发。

具体实现：
无人时，眼睛保持 idle 扫视、轻微漂移、呼吸和自然眨眼。检测到从无人到有人时，眼睛会立即转向最新人脸目标，并直接播放 `_anchor_grid.json` 中 `Excitement` 强度 1.0 的约 0.92 秒提示。除 gaze 坐标与物理屏幕镜像外，眼睑、瞳孔、虹膜和高光等参数均原样来自 JSON；提示不接管 gaze，因此不会出现表情播放时反而没有看向观众的情况。连续无人脸检测 1.5 秒后才重新允许下一次触发，以避免偶发漏检重复反应。

## 当前互动行为

- 人突然靠近：当平滑后的人脸框面积相对上一有效样本增长至少 15% 时，播放 `Interest / 1.0`；这不是绝对距离阈值。慢速靠近的保底逻辑是：面积由低于 `0.055` 跨到达到/高于 `0.055` 时，也触发一次。动画约 0.92 秒，最终 JSON 状态仅保持 3 秒，之后即使人仍很近也恢复普通人脸注视跟随。
- 人远离：面积持续变小后，注视移动变柔和；当平滑后的人脸框面积比例持续低于 `0.010` 约 1 秒，会在 1.25 秒内平滑过渡回 idle。

#### 远离到待机的距离与过程

“逐渐放松”不是一次跳变，而是以下连续过程：

1. 检测到人脸面积持续减小时，进入 `retreating`；gaze smoothing 从正常标定值（默认 `0.35`）降到 `0.22`，眼睛仍看着人，但移动速度更柔和。
2. 瞳孔目标从“检测到人”的 `1.04` 平滑回基础 `1.00`，每帧以 `0.1` 的系数靠近目标，因此不是突然缩回。
3. 面积比例持续低于 `0.010` 达 1 秒后，进入 `disengaging`；最后的人脸注视目标以三次缓动在 1.25 秒内混合到当前 idle 扫视目标。
4. 过渡完成后，恢复无人时的自然扫视、轻微漂移、呼吸和眨眼。

如果 `Interest` 动画或其 3 秒最终帧保持期间检测到 `retreating`/`disengaging`，保持会立即取消，画面马上恢复普通实时注视；之后继续执行上述瞳孔放松和 gaze 回 idle 过程。

### 可选表情 timing debug

默认不启用表情远程控制。只有传入 `--expression-debug` 时，才可通过 `GET /expression-debug?name=happy`（可选 `duration_ms`）从 idle 切换到 `happy`、`angry`、`cry`、`dislike`、`miosis`，并通过 `GET /expression-debug` 读取有界事件序列。新命令立即覆盖旧命令，绝不排队；事件依次记录 `command_received`、`affect_state_changed`、首个新表情渲染、首个 SPI 提交、自然完成或被覆盖。不开此开关时，既有 `set_expression()` 继续循环表情，HTTP 端点返回 404。

## 语音播放、眼睛动画与人脸追踪并行

本次修改使本地预录音频播放与眼睛动画、人脸追踪并行运行。当前使用 `audio_assets/vo/` 中的 MP3/WAV：反应式语音在说话结束后播放；连续 15 秒没有播放时，也会自主随机播放本地音频。网页按钮只支持项目根目录 `audio/` 的 WAV，不属于本地素材的主要播放入口。

### 修改内容与已完成功能

- `speaker.py` 新增 `AudioPlaybackController`，作为唯一访问 ALSA/`aplay` 的后台播放 worker。播放请求进入有限队列后立即返回；队列满时拒绝新请求，不阻塞 HTTP、VAD、眼睛或人脸追踪线程。
- 本地 WAV、MP3 拟声与 TTS 后端统一接入该播放队列；MP3 通过 `ffmpeg` 流式解码，不在语音调用线程中完整加载音频。
- `EyeDisplay` 与 `FaceTracker` 保持独立线程运行。眼睛持续读取最新人脸 snapshot，不等待整段音频播放结束。
- 普通 `VoiceModule` 改为在首个音频样本真正输出时进入 `speaking`，在播放完成后回到 `idle`，不再按文本长度猜测说话时长。
- `ReactiveVoiceModule` 修复自主播放计时：即使麦克风持续产生低音量帧，15 秒未播放仍会触发随机本地音频；播放等待仅发生在反应式语音 worker 中，用于抑制扬声器回声，不影响眼睛和追踪。
- `/status` 新增 `audio_metrics`，提供播放状态、队列长度、启动 / 完成 / 失败 / 拒绝计数；60 秒采集器会将这些计数写入 CSV，便于验收音频任务是否失败或被拒绝。`voice_metrics`、`eye_metrics`、`face_metrics` 继续分别暴露语音、眼睛和人脸追踪状态。

### 快反应关键词触发优化

为了缩短“喊名字 → 眼睛动作 + 音频出声”的端到端延迟，`voice/fast_reaction.py` 在模块启动时即把配置的反应音频预解码成 48kHz 单声道 S32_LE PCM 缓冲在内存中；KWS 触发后不再临时启动 `ffmpeg`/`aplay` 子进程，而是直接通过 `speaker.play_buffer(channels=1)` 提交预加载 PCM，由 `speaker.py` 内部自动展开成立体声后写入 ALSA。采用单声道预加载是为了避免外部直接传递交织立体声 PCM 时 chunk 边界不是 8 字节整数倍而触发 ALSA 对齐错误。触发时仍保持眼睛动作先执行、音频随后立即跟上，避免整体等待。

同时 `speaker.py` 的 ALSA 写入参数从 `periodsize=1024 / chunk=4096` 降到 `periodsize=512 / chunk=1024`，进一步压缩 ALSA 缓冲排队延迟，降低“眼睛已反应但声音尚未出来”的感知差异。

表情持续时间不再是固定的 2.0 秒。`_preload_audio` 在启动预加载音频时根据 PCM 长度算出实际时长，`FastReactionModule._trigger` 触发时取 `max(eye_expression_duration_s, 音频时长)` 作为本次表情持续秒数。因此 2 秒音频仍保持 2 秒，3 秒音频眼睛会持续 3 秒，避免"声音没完眼睛先回到 idle"的错位。

检测到关键词后会立即调用 `self._backend.reset()` 重置流式 KWS 后端上下文，避免多次触发后模型 beam 路径残留导致的关键词间歇性失效。

当某个反应配置了较长的音频（例如哼歌反应的 5 秒以上 WAV），在该音频持续期间会暂停接收新的关键词检测，避免歌曲被其他关键词打断或在歌曲刚结束时立即触发另一个反应；音频结束后才恢复正常的 `cooldown_s` 冷却。短音频（拟声、短情绪音频）的行为不变。

### 快反应眼睛表情：JSON 锚点表情只播放一次

`eye_expressions.py` 中的 `JsonAnchorExpression`（包括 `Surprise1Expression`）原先会按 `cycle` 循环播放 JSON 锚点动画。由于 `Surprise@1.0` 动画本身只有约 0.92 秒，而快反应表情持续 2.0 秒，这会导致瞳孔在 2.0 秒内出现“放大 → 回弹 → 放大”的脉冲感。

现已将 `JsonAnchorExpression.loop` 设为 `False`：渲染循环对非循环表情用 `min(now - start, duration)` 取时间，`step()` 在 `duration` 之后始终返回最后一帧。因此 `Surprise@1.0` 触发后会快速睁大、保持，并在表情结束时一次性回到 idle，不再循环抽动。

其他手写循环表情（`happy`/`angry`/`cry`/`dislike`/`miosis`/`surprise`）保持 `loop=True`，行为不变。

### 自定义关键词

`config/fast_reaction.json` 控制快反应层的关键词、反应音频、表情与 KWS 后端。默认配置已支持多关键词多反应：不同关键词可触发不同音频 + 眼睛表情。所有关键词统一使用项目级自定义关键词文件 `config/my_keywords.txt`，避免修改模型目录下的官方 `keywords.txt`。

```bash
python3 main.py --kws --dual-eye --no-camera
```

#### 默认反应配置

当前默认配置包含以下反应：

| 触发 | 关键词 | 声音文件 | 眼睛表情 |
|------|--------|---------|---------|
| 叫名字 | 小小熊 | `audio_assets/vo/en.mp3` | `surprise1.0` |
| 告别 | 再见、拜拜 | `audio_assets/vo/bye.mp3` | `calmness1.0` |
| 问候 | 你好、哈喽 | `audio_assets/vo/hi.mp3`、`audio_assets/vo/ya.mp3` 中随机一个 | `excitement0.6` |
| 夸奖 | 真棒、厉害、聪明、好棒 | `audio_assets/vo/076_amusement_short.mp3` | `amusement1.0` |
| 责骂 / 负面语言 | 讨厌、你走开、你好烦 | `audio_assets/vo/004_sadness_short.mp3` | `sadness1.0` |
| 强烈生气 | 我生气了 | `audio_assets/vo/004_sadness_short.mp3` | `sadness1.0` |
| 哼歌反应 | 唱歌、想听歌 | `audio_assets/vo/song/` 下 5 首 WAV 随机播放 | `interest1.0` |

#### 如何改成你自己的名字 / 添加新反应

1. **准备自定义关键词文件**
   在 `config/` 下新建或编辑 `my_keywords.txt`，每行一个关键词，格式与官方 `keywords.txt` 一致：左侧为音素/音节序列，右侧 `@` 后为中文名。

   例如 `config/my_keywords.txt`：

   ```text
   x iǎo x iǎo x ióng @小小熊
   z ài j iàn @再见
   b ài b ài @拜拜
   n ǐ h ǎo @你好
   h ā l óu @哈喽
   zh ēn b àng @真棒
   l ì h ài @厉害
   c ōng m íng @聪明
   h ǎo b àng @好棒
   t ǎo y àn @讨厌
   n ǐ z ǒu k āi @你走开
   n ǐ h ǎo f án @你好烦
   w ǒ sh ēng q ì l e @我生气了
   ```

   注：拼音音节需与模型 `tokens.txt` 中已有 token 对应，例如 `x=48`、`iǎo=115`、`ióng=178`。新词尽量使用 `tokens.txt` 中已出现的声母/韵母，避免使用模型没见过的 token。

2. **修改 `config/fast_reaction.json`**

   使用 `reactions` 数组，每个反应包含：
   - `keywords`: 触发该反应的关键词列表
   - `audio_path`: 反应音频文件路径，或一个目录路径（会扫描目录下 `.wav/.mp3`）
   - `audio_paths`: 反应音频文件路径列表，触发时随机播放其中一首
   - `eye_expression`: 眼睛表情名

   `audio_paths` 与 `audio_path` 二选一；同时存在时优先使用 `audio_paths`。

   当前默认配置：

   ```json
   {
     "reactions": [
       {
         "keywords": ["小小熊"],
         "audio_path": "audio_assets/vo/en.mp3",
         "eye_expression": "surprise1.0"
       },
       {
         "keywords": ["再见", "拜拜"],
         "audio_path": "audio_assets/vo/bye.mp3",
         "eye_expression": "calmness1.0"
       },
       {
         "keywords": ["你好", "哈喽"],
         "audio_paths": [
           "audio_assets/vo/hi.mp3",
           "audio_assets/vo/ya.mp3"
         ],
         "eye_expression": "excitement0.6"
       },
       {
         "keywords": ["真棒", "厉害", "聪明", "好棒"],
         "audio_path": "audio_assets/vo/076_amusement_short.mp3",
         "eye_expression": "amusement1.0"
       },
       {
         "keywords": ["讨厌", "你走开", "你好烦"],
         "audio_path": "audio_assets/vo/004_sadness_short.mp3",
         "eye_expression": "sadness1.0"
       },
       {
         "keywords": ["唱歌", "想听歌"],
         "audio_paths": [
           "audio_assets/vo/song/mary_lamb.wav",
           "audio_assets/vo/song/jingle_bells.wav",
           "audio_assets/vo/song/jasmine_flower.wav",
           "audio_assets/vo/song/04_twinkle_twinkle_doubao.wav",
           "audio_assets/vo/song/01_daisy_bell_doubao_smooth.wav"
         ],
         "eye_expression": "interest1.0"
       }
     ],
     "eye_expression_duration_s": 2.0,
     "eye_state": "listening",
     "detection_threshold": 0.5,
     "cooldown_s": 2.0,
     "backend": "sherpa-onnx",
     "backend_config": {
       "model_dir": "models/kws/sherpa-onnx/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
       "encoder_model": "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
       "decoder_model": "decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
       "joiner_model": "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
       "keywords_file": "config/my_keywords.txt",
       "tokens_file": "tokens.txt"
     }
   }
   ```

   `backend_config.keywords_file` 支持三种路径写法：
   - 相对 `model_dir`：例如 `my_keywords.txt`
   - 相对项目根目录：例如 `config/my_keywords.txt`
   - 绝对路径

3. **验证**

   运行后日志应出现：

   ```text
   [sherpa-onnx] keywords=/home/pi/.../config/my_keywords.txt
   [KWS] 预加载反应音频 '小小熊': 034_confusion_short.mp3
   [KWS] 预加载反应音频 '真棒': 076_amusement_short.mp3
   [KWS] 预加载反应音频 '讨厌': 004_sadness_short.mp3
   [KWS] 预加载反应音频 '我生气了': 004_sadness_short.mp3
   [KWS] 快反应模块已启动 (backend=sherpa-onnx)
   ```

   喊“小小熊”后应出现：

   ```text
   [KWS] 检测到关键词 '小小熊' (confidence=1.00)
   [EYE] 表情 -> surprise1.0
   [KWS] 音频首采样延迟: XX.X ms
   ```

   喊“真棒”后应出现：

   ```text
   [KWS] 检测到关键词 '真棒' (confidence=1.00)
   [EYE] 表情 -> amusement1.0
   [KWS] 音频首采样延迟: XX.X ms
   ```

   喊“讨厌 / 你走开 / 你好烦”后会看到轻微委屈的 `sadness0.6`，喊“我生气了”后会看到更明显的 `sadness1.0`：

   ```text
   [KWS] 检测到关键词 '讨厌' (confidence=1.00)
   [EYE] 表情 -> sadness0.6
   [KWS] 音频首采样延迟: XX.X ms
   ```

#### 多关键词与不同反应（已支持）

- 将 `fast_reaction.json` 配置为 `reactions` 数组，每个反应包含 `keywords` / `audio_path` / `eye_expression`。
- `FastReactionModule` 启动时循环预加载每个反应音频。
- `_trigger(keyword, confidence)` 根据识别到的 `keyword` 查表选择对应 PCM 和表情。
- 所有关键词一起传给 SherpaOnnxKWS，模型同时监听多个关键词。

建议预加载数量控制在 **10 个以内、每个音频 2 秒以内**，总内存占用约 2–5 MB。更多或更长的音频应改用 LRU 缓存或触发时异步加载。

#### 延时速查

触发后日志会打印：

```text
[KWS] 音频首采样延迟: 35.2 ms
```

该数值表示从 KWS 检测到关键词到音频首采样真正写入 ALSA 的时间差，不含 KWS 本身的推理延迟。眼睛动作会在 KWS 检测结果出来后立即触发，因此用户端到端感知为“眼睛先动、声音紧跟”。
