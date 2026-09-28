# 阶段3：实验计划 — 语音代码迁移与 Pi Zero 2W 优化

## 实验目标

基于阶段2选定的 **方案 C（混合分层）**，在 `pi_affe_sys` 中实现一套可运行的语音交互系统：

1. 解决 `MicMonitor` 与 `VoiceRecorder` 的 ALSA 独占冲突。
2. 将语音模块接入 `main.py`，实现与眼睛动画的状态联动。
3. 保留预录音频库快速反馈，并为未来 Piper TTS / SenseVoice ASR 预留可插拔后端接口。
4. 在 Raspberry Pi Zero 2W 上验证端到端延迟与稳定性。
5. 产出理想化代码并推送到新分支 `ideal/voice-migration`。

---

## 关键假设与验证矩阵

| 假设 | 验证实验 | 成功标准 | 失败标准 | 回退动作 |
|------|----------|----------|----------|----------|
| `MicMonitor` 回调不会显著降低 VU 表/眼睛动画帧率 | Exp-1 回调性能测试 | 眼睛动画维持 ≥20 fps，VU 更新无明显卡顿 | 帧率下降 >20% 或音频延迟 >200ms | 改用 ring buffer + 独立消费线程 |
| `speaker.py` 增加 `play_buffer` 后可播放 pydub 转换的 PCM | Exp-2 播放接口测试 | 播放 MP3/WAV 无爆音、无段错误 | 播放失败或 ALSA 报错 | 改为先写临时 WAV 再调用 `play_wav` |
| 云端 ASR/LLM 在国内网络下可用 | Exp-3 云端链路测试 | 端到端（录音→识别→情绪→播放）<3s，成功率≥80% | 成功率<50% 或延迟>5s | 切换到 DashScope ASR 或本地 SenseVoice fallback |
| `EyeDisplay` 可接收语音状态并切换动画 | Exp-4 眼睛联动测试 | 状态切换可见，延迟<100ms | 状态丢失或动画冲突 | 简化状态，只保留 LISTEN/SPEAK |
| 主程序集成后长时间运行稳定 | Exp-5 稳定性测试 | 连续运行 30 分钟无崩溃、无内存泄漏 | 出现 ALSA 句柄泄漏或 OOM | 检查回调注销、队列大小限制 |
| 新分支代码可离线安装依赖 | Exp-6 离线依赖测试 | 在 Pi 上使用 `--no-index` 安装成功 | 安装失败 | 回到联网安装或精简依赖 |

---

## 实验矩阵详解

### Exp-1：MicMonitor 回调性能测试

- **目标**：验证为 `MicMonitor` 增加回调机制后，现有 VU 表与眼睛动画性能不受影响。
- **步骤**：
  1. 在 `mic.py` 中实现 `register_audio_callback` / `unregister_audio_callback`。
  2. 启动 `python3 main.py --console-vu --no-eye`。
  3. 注册一个空回调，持续 2 分钟。
  4. 观察 VU 输出频率与 CPU 占用。
- **成功标准**：VU 输出频率与未注册回调时一致；`top` 中 Python 进程 CPU <30%。
- **失败标准**：VU 卡顿或 CPU 占用显著上升。
- **回退**：将回调改为基于 `queue` 的独立消费线程。

### Exp-2：播放接口测试

- **目标**：验证 `speaker.play_buffer()` 可正确播放 pydub 解码后的 PCM。
- **步骤**：
  1. 在 `speaker.py` 中实现 `play_buffer(pcm_bytes, sample_rate, channels, width)`。
  2. 加载一个 `audio_assets/vo/Calmness/*.mp3`。
  3. 用 pydub 转为 48kHz / 16-bit / 单声道 PCM。
  4. 调用 `play_buffer` 播放。
- **成功标准**：声音清晰，无爆音，播放完成后 ALSA 设备正常关闭。
- **失败标准**：播放无声、爆音或程序崩溃。
- **回退**：继续使用临时 WAV 文件 + `play_wav()`。

### Exp-3：云端链路测试

- **目标**：验证从录音到播放的端到端链路。
- **步骤**：
  1. 配置 `DASHSCOPE_API_KEY`。
  2. 运行 `python3 voice/test_minimal.py`。
  3. 对麦克风说 3-5 句中文，记录每次的识别结果、情绪标签、播放文件名与总耗时。
- **成功标准**：≥80% 成功完成识别与播放，平均耗时 <3s。
- **失败标准**：成功率 <50% 或平均耗时 >5s。
- **回退**：将 ASR 从 Google 切换到 DashScope；或启用本地 SenseVoice fallback。

### Exp-4：眼睛联动测试

- **目标**：验证语音状态可驱动眼睛动画变化。
- **步骤**：
  1. 启动 `python3 main.py --dual-eye`。
  2. 对麦克风说话，观察眼睛是否从 IDLE 变为 LISTENING。
  3. 静默后观察是否变为 THINKING，播放时变为 SPEAKING。
- **成功标准**：状态切换肉眼可见，延迟 <100ms。
- **失败标准**：状态 stuck 或动画冲突。
- **回退**：只保留 SPEAKING 状态（播放时眼睛睁大）。

### Exp-5：长时间稳定性测试

- **目标**：验证集成后系统可稳定运行 30 分钟。
- **步骤**：
  1. 启动 `python3 main.py --dual-eye`。
  2. 每 2 分钟触发一次语音交互。
  3. 同时监控内存（`free -m`）、ALSA 句柄（`lsof /dev/snd/*`）、日志。
- **成功标准**：30 分钟内无崩溃，内存增长 <20MB，ALSA 句柄无泄漏。
- **失败标准**：崩溃、OOM、ALSA 设备无法再次打开。
- **回退**：检查回调是否正确注销、队列是否设 maxsize、播放后是否关闭 PCM。

### Exp-6：离线依赖安装测试

- **目标**：验证在另一台 Linux 电脑下载 whl 后可在 Pi 上离线安装。
- **步骤**：
  1. 在联网 Linux 电脑上执行 `pip download -r requirements.txt -d ./voice_pkgs`。
  2. 复制到 Pi：`scp -r ./voice_pkgs pi@pi.local:/home/pi/`。
  3. 在 Pi 上执行 `pip3 install --no-index --find-links /home/pi/voice_pkgs -r requirements.txt --break-system-packages`。
- **成功标准**：所有依赖安装成功，`python3 -c "import dashscope, speechrecognition, pydub"` 通过。
- **失败标准**：缺少平台相关 whl 或版本冲突。
- **回退**：使用 `pip wheel` 在 x86 上为 armhf 交叉编译，或精简依赖。

---

## 周计划

### 第 1 周：MVP 跑通（方案 A 功能）

- Day 1-2：修改 `mic.py` 支持音频回调；修改 `voice/audio.py` 复用 MicMonitor。
- Day 3：为 `speaker.py` 增加 `play_buffer`。
- Day 4：将 `voice/emotion_dialogue.py` 重构为基于回调的 `VoiceModule`。
- Day 5：在 `main.py` 中集成 `VoiceModule`，完成 Exp-1 / Exp-2 / Exp-3。
- 周末：文档更新与代码自审。

### 第 2 周：抽象为方案 C

- Day 1-2：设计并实现 `voice/backends/` 抽象层（ASR/LLM/TTS）。
- Day 3：将云端实现移入 `voice/backends/cloud.py`。
- Day 4：将预录音频库实现移入 `voice/backends/preset.py`。
- Day 5：实现 `EyeDisplay` 语音状态机与 `VoiceModule` 联动，完成 Exp-4。
- 周末：Runbook 初稿。

### 第 3 周：稳定性与原始功能迁移

- Day 1-2：实现简化版 `voice/memory.py` 与 `voice/xp.py`。
- Day 3：实现 `voice/emotion_engine.py`，将情绪映射到眼睛动画与音频策略。
- Day 4：长时间稳定性测试（Exp-5）。
- Day 5：离线依赖安装测试（Exp-6）。
- 周末：修复发现的问题。

### 第 4 周：评审、优化与分支推送

- Day 1-2：阶段4对抗性评审，修复短板。
- Day 3-4：性能优化（减少内存拷贝、限制队列大小、优化眼睛动画 CPU）。
- Day 5：创建并推送 `ideal/voice-migration` 分支。
- 周末：写 ARIS 代码优化 Skill。

---

## 资源需求

| 资源 | 说明 |
|------|------|
| Raspberry Pi Zero 2W | 目标运行设备，已配置好 SPI/I2S/摄像头 |
| 联网 Linux 电脑 | 用于下载 pip 依赖与模型文件 |
| DashScope API Key | 用于 LLM 与可选 ASR |
| GitHub 访问 | 推送 `ideal/voice-migration` 分支 |
| SD 卡 ≥32GB | 安装 headless Raspberry Pi OS Lite |
| USB 电源 5V/2.5A | 保证 I2S 与外设稳定供电 |

---

## 风险与回退

| 风险 | 影响 | 可能性 | 缓解措施 |
|------|------|--------|----------|
| Pi 内存不足 | 高 | 中 | 云端优先；本地模型按需加载；启用 zram |
| ALSA 独占冲突无法完美解决 | 高 | 低 | 复用 MicMonitor 回调；必要时改用 `dmix`/`dsnoop` 插件 |
| 云端 API 不稳定 | 中 | 中 | 增加重试与 fallback；预留本地 ASR 接口 |
| 眼睛动画与语音状态耦合过紧 | 中 | 中 | 通过事件总线解耦 |
| 原始功能过度迁移导致代码臃肿 | 中 | 中 | 按周计划裁剪，只保留核心机制 |
| 离线依赖安装失败 | 中 | 中 | 使用 `pip wheel` 为 armhf 构建；或精简依赖 |

---

## 进入阶段4的条件

满足以下全部条件即可进入对抗性评审：

1. `MicMonitor` 回调机制实现并通过 Exp-1。
2. `VoiceModule` 能完整运行一次“录音 → ASR → LLM → 播放”并触发眼睛动画（Exp-3 / Exp-4 通过）。
3. `voice/backends/` 抽象层至少包含云端 ASR/LLM 与预录音频 TTS 实现。
4. 代码已提交到本地 git，可创建新分支。
5. 已记录所有实验中发现的异常与修复动作。

如果 Exp-1 / Exp-3 失败，则先按回退动作修复，再重新验证。
