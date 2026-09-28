# 阶段3：运行手册 — 语音代码迁移与 Pi Zero 2W 优化

## 环境准备

### 硬件

- Raspberry Pi Zero 2W
- INMP441 I2S 麦克风
- MAX98357A I2S 扬声器
- GC9D01 双目屏幕（可选，调试用 `--no-eye`）
- Pi Camera（可选，调试用 `--no-camera`）
- USB 电源 5V/2.5A

### 软件

- Raspberry Pi OS Lite（headless，32-bit 推荐）
- 已启用 I2S、SPI、摄像头（通过 `setup/enable_all.sh`）
- Python 3.11+
- 已配置 SSH 公钥登录

### 项目目录

```text
/home/pi/pi_affe_sys/
├── main.py
├── mic.py
├── speaker.py
├── eye_display.py
├── camera.py
├── voice/
│   ├── __init__.py
│   ├── config.py
│   ├── module.py          # VoiceModule 主类
│   ├── audio.py           # 录音辅助（复用 MicMonitor）
│   ├── emotion_engine.py  # 情绪映射
│   ├── memory.py          # 简化记忆
│   ├── xp.py              # 简化 XP
│   └── backends/
│       ├── __init__.py
│       ├── base.py        # 抽象接口
│       ├── cloud.py       # 云端 ASR/LLM/TTS
│       └── preset.py      # 预录音频库
├── audio_assets/vo/       # 162 个情绪 MP3
├── outputs/               # ARIS 调研产出
├── requirements.txt
└── setup/
    └── enable_all.sh
```

### 依赖安装

#### 在线安装（Pi 网络稳定时）

```bash
cd /home/pi/pi_affe_sys
pip3 install -r requirements.txt --break-system-packages
```

#### 离线安装（推荐）

在联网 Linux 电脑：

```bash
mkdir -p /tmp/voice_pkgs
cd /tmp/voice_pkgs
pip download -r /path/to/pi_affe_sys/requirements.txt -d .
```

复制到 Pi：

```bash
scp -r /tmp/voice_pkgs pi@pi.local:/home/pi/
ssh pi@pi.local "pip3 install --no-index --find-links /home/pi/voice_pkgs -r /home/pi/pi_affe_sys/requirements.txt --break-system-packages"
```

### API Key 配置

编辑 `voice/config.py`：

```python
QIANWEN_API_KEY = "your-real-dashscope-key"
```

或设置环境变量：

```bash
export DASHSCOPE_API_KEY="your-real-dashscope-key"
```

---

## 各组件启动命令

### 仅验证语音模块（无屏幕、无摄像头）

```bash
cd /home/pi/pi_affe_sys
python3 -u voice/test_minimal.py
```

预期输出：

```text
等待语音输入...
[ASR] 识别结果: 你好
[LLM] 情绪分析: Calmness
[PLAY] 播放情绪音频: Calmness/xxx.mp3
```

### 语音 + 麦克风 VU（无屏幕）

```bash
cd /home/pi/pi_affe_sys
python3 -u main.py --no-eye --console-vu
```

### 双目 + 语音 + 摄像头

```bash
cd /home/pi/pi_affe_sys
python3 -u main.py --dual-eye
```

日志写入 `main_dual.log`：

```bash
nohup python3 -u main.py --dual-eye > main_dual.log 2>&1 &
echo "PID: $!"
```

### 自定义端口

```bash
python3 -u main.py --dual-eye --port 9090
```

---

## 测试命令与预期输出

### 测试 ALSA 设备

```bash
# 查看 capture 设备
arecord -l

# 查看 playback 设备
aplay -l

# 查看 SPI 设备
ls -l /dev/spidev*
```

预期：
- `arecord -l` 显示 `card 1: ...`。
- `aplay -l` 显示 `card 0: ...` 或 `card 1: ...`。
- `/dev/spidev0.0` 与 `/dev/spidev0.1` 存在。

### 测试回调机制

```bash
python3 -c "
from mic import MicMonitor
m = MicMonitor(device='hw:1,0')
def cb(frame):
    print('callback got', len(frame), 'bytes')
m.register_audio_callback(cb)
m.start()
import time; time.sleep(3)
m.stop()
m.unregister_audio_callback(cb)
"
```

预期：持续输出 `callback got 8192 bytes`（具体大小取决于配置）。

### 测试播放接口

```bash
python3 -c "
import os
from pydub import AudioSegment
from speaker import play_buffer
path = 'audio_assets/vo/Calmness/001.mp3'
audio = AudioSegment.from_file(path).set_frame_rate(48000).set_channels(1).set_sample_width(2)
play_buffer(audio.raw_data, 48000, 1, 2)
"
```

预期：扬声器播放清晰的 MP3 音频。

### 测试端到端语音链路

```bash
python3 -u voice/test_minimal.py
```

预期：对麦克风说话后，约 1-3 秒内识别文字并播放对应情绪音频。

---

## 日志路径

| 日志 | 路径 | 说明 |
|------|------|------|
| 主程序日志 | `/home/pi/pi_affe_sys/main_dual.log` | `--dual-eye` 模式 |
| 单眼模式日志 | `/home/pi/pi_affe_sys/main_single.log` | 单眼模式 |
| 调试日志 | `/home/pi/pi_affe_sys/DEBUG_LOG.md` | 手动记录硬件问题 |
| 调研产出 | `/home/pi/pi_affe_sys/outputs/` | ARIS 各阶段 Markdown |

实时查看日志：

```bash
tail -n 100 /home/pi/pi_affe_sys/main_dual.log
tail -f /home/pi/pi_affe_sys/main_dual.log
```

---

## 常见问题排查

### 问题1：`VoiceModule` 无法录音，提示 ALSA 设备忙

- **原因**：`MicMonitor` 已独占 `hw:1,0`，`VoiceRecorder` 试图再次打开同一设备。
- **检查**：`lsof /dev/snd/*` 查看谁打开了设备。
- **修复**：确认 `VoiceModule` 已改为消费 `MicMonitor` 回调，而不是自己打开 ALSA。

### 问题2：播放时无声音或爆音

- **检查**：
  - 扬声器接线是否正确（BCLK/LRCLK/DIN/GND）。
  - `aplay -l` 中播放设备是否为 `hw:0,0` 或 `hw:1,0`。
  - 采样率是否为 48kHz，格式是否匹配。
- **修复**：使用 `speaker.play_buffer` 前确保数据已转换为 48kHz / 16-bit / 单声道。

### 问题3：眼睛动画卡顿

- **检查**：`top` 查看 CPU 占用，确认回调处理是否耗时。
- **修复**：回调中只做入队，不在回调线程中做 ASR/LLM/TTS 推理。

### 问题4：云端 ASR/LLM 超时

- **检查**：网络是否稳定，`DASHSCOPE_API_KEY` 是否配置正确。
- **修复**：
  - 增加重试次数与超时时间。
  - 切换到 DashScope ASR。
  - 配置本地 SenseVoice fallback（需先下载模型）。

### 问题5：依赖安装失败

- **检查**：是否缺少 armhf 架构的 whl。
- **修复**：
  - 在 Pi 上直接使用 `pip3 install`（网络允许时）。
  - 或在一台 arm 架构设备（如另一块 Pi）上构建 whl。
  - 或精简 requirements.txt，只安装当前运行所需依赖。

### 问题6：内存不足导致 OOM

- **检查**：`free -m` 查看可用内存。
- **修复**：
  - 启用 zram：`sudo apt install zram-tools`。
  - 设置 `gpu_mem=16`。
  - 禁用不必要服务：`sudo systemctl disable bluetooth` 等。
  - 云端优先，不加载本地模型。

---

## 安全与合规提醒

1. **断电操作**：所有 GPIO/SPI/CSI/I2S/I2C 接线必须先执行 `sudo poweroff` 并等待绿灯熄灭，严禁热插拔。
2. **API Key 管理**：不要将真实 API Key 提交到 git。使用环境变量或本地未跟踪的配置文件。
3. **扬声器音量**：首次播放前将系统音量调低，避免突然大音量损坏扬声器或惊吓用户：
   ```bash
   amixer set Master 50%
   ```
4. **网络暴露**：Web UI 默认监听 `0.0.0.0:8080`，在公网环境中应配置防火墙或只监听 `127.0.0.1`。
5. **日志隐私**：录音文件与识别文本可能包含敏感信息，定期清理 `outputs/` 与临时录音文件。
6. **长期运行**：建议配合 `systemd` 服务与 `watchdog` 使用，避免进程僵死后无人处理。

---

## 快速检查清单

部署前逐项确认：

- [ ] Pi 已启用 I2S、SPI、摄像头
- [ ] ALSA capture/playback 设备正常
- [ ] 依赖已安装（在线或离线）
- [ ] `DASHSCOPE_API_KEY` 已配置
- [ ] `voice/config.py` 中 `DEVICE` 与实际一致
- [ ] 双目屏幕接线正确并已单独测试
- [ ] 已阅读 `CLAUDE.md` 中的架构规则
- [ ] 已创建 `ideal/voice-migration` 分支并推送
