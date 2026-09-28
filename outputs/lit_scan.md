# 阶段1：文献扫描 — 语音代码迁移与 Pi Zero 2W 优化

## 研究课题

将原项目 `Interaction_Version_Management-master` 的完整语音交互功能迁移到 Doll Robot 项目 `pi_affe_sys`，并针对 Raspberry Pi Zero 2W（1GHz 四核 A53 / 512MB RAM）做性能优化，最终形成一套可在 Pi 上长期运行的理想化代码。

## 核心信息源

| 来源 | 类型 | 位置 / URL | 用途 |
|------|------|------------|------|
| 当前 `pi_affe_sys` 代码库 | 本地代码 | `D:\pythonProject\pi_affe_sys\` | 了解已实现模块、接口与冲突点 |
| 原始语音项目 | 本地代码 | `Interaction_Version_Management-master` | 明确待迁移的完整功能集 |
| pyalsaaudio 文档 | 官方文档 | https://larsimmisch.github.io/pyalsaaudio/ | ALSA PCM 捕获/播放最佳实践 |
| Raspberry Pi OS 文档 | 官方文档 | https://www.raspberrypi.com/documentation/ | 外设启用、内存与性能调优 |
| FunASR / SenseVoice 仓库 | 开源项目 | https://github.com/modelscope/FunASR | 本地 ASR 选型与模型大小 |
| Piper TTS 仓库 | 开源项目 | https://github.com/rhasspy/piper | 本地 TTS 选型 |
| Edge TTS 仓库 | 开源项目 | https://github.com/rany2/edge-tts | 云端免费 TTS 备选 |
| DashScope 文档 | 官方文档 | https://help.aliyun.com/zh/dashscope/ | 国内 LLM API 调用 |
| Pi Zero 2W 性能基准 | 社区资料 | Multiple forums / GitHub issues | 评估本地模型可行性 |

## 当前代码库状态

### 已迁移的最小闭环

`voice/` 目录下已存在：
- `voice/audio.py`：基于 `pyalsaaudio` 的阈值触发录音 + WAV/MP3 播放包装。
- `voice/emotion_dialogue.py`：录音 → Google ASR（zh-CN）→ DashScope `qwen-plus` 情绪分析 → 播放 `audio_assets/vo/<emotion>/` 下随机 MP3。
- `voice/config.py`：配置 API Key、音频目录、ALSA 设备等。
- `voice/test_minimal.py`：最小可运行测试入口。
- `audio_assets/vo/`：162 个 MP3（27 种情绪 × 6 条），共约 4.7MB。

### 关键缺陷与集成缺口

1. **ALSA 设备独占冲突**
   - `mic.py` 的 `MicMonitor` 在后台持续占用 `hw:1,0` 做 capture。
   - `voice/audio.py` 的 `VoiceRecorder` 每次录音又独立打开 `hw:1,0`。
   - 结果：两者无法同时运行，录音时会与麦克风 VU 监控冲突。

2. **`speaker.py` 缺少 `play_buffer`**
   - `voice/audio.py` 中 `from speaker import play_buffer as _play_buffer`，但 `speaker.py` 未实现该函数。
   - 当前 `play_audio()` 只能播放文件，无法直接播放内存中的音频数据。

3. **未与 `main.py` 集成**
   - `main.py` 只启动 Camera、FaceTracker、MotionTracker、MicMonitor、EyeDisplay。
   - 语音模块未注册、未启动、未与眼睛动画联动。

4. **未与 `eye_display.py` 联动**
   - 录音、识别、播放等状态没有视觉反馈（如说话时眼睛睁开/眨眼、思考时注视等）。

5. **依赖未完全安装**
   - Pi 上 WiFi 到 PyPI 不稳定，`dashscope` 下载失败（`Connection reset by peer`）。
   - 需要离线安装方案。

### 原始功能待迁移清单

原 `Interaction_Version_Management-master` 包含但不限于：
- 多模态情绪识别与对话管理（非仅一句情绪标签）。
- XP 经验/等级系统。
- 双轨记忆矩阵（短期/长期）。
- 句法碎片器（syntactic fragmenter）与模态解析器（modality parser）。
- 涌现调度器（emergence scheduler）。
- Parrot TTS（`ca.py`）。
- 唱歌模块（`sing.py`）。
- 火山引擎 RTC（实时通信）。
- 存档/读档系统。
- 音频特效处理。

当前最小闭环仅覆盖了“录音 → ASR → 情绪标签 → 播放预录音频”这一小段。

## 关键发现

### 1. ALSA 架构约束

- `pyalsaaudio` 的 `PCM` 对象对 `hw:` 设备是独占的；同一设备不能同时被两个 PCM 对象以 capture 模式打开。
- `MicMonitor` 已经在后台持续 capture，因此 `VoiceRecorder` 不应再开新 capture，而应复用 `MicMonitor` 的音频回调流。
- 播放使用 `hw:` 时，也需避免与 capture 冲突；I2S 全双工场景下，capture 与 playback 可共用时钟线，但需确认声卡驱动支持同时打开。

### 2. ASR 选型

| 方案 | 位置 | 模型大小 | Pi Zero 2W 可行性 | 备注 |
|------|------|----------|-------------------|------|
| Google Speech Recognition | 云端 | 无 | 可用但国内网络不稳定 | 当前已使用，依赖外网 |
| DashScope 语音识别 | 云端 | 无 | 国内稳定 | 需 API Key，按量计费 |
| FunASR SenseVoice q8 (GGUF) | 本地 | ~254MB | 可运行但内存紧张 | 最佳本地 ASR 候选 |
| Whisper tiny / base | 本地 | 39MB / 74MB | tiny 可跑，base 较慢 | 中文效果弱于 SenseVoice |
| Porcupine / Coqui STT | 本地 | 较小 | 唤醒词可行，连续 ASR 弱 | 更适合唤醒而非对话 |

结论：默认使用云端 DashScope ASR（国内稳定），可选本地 SenseVoice q8 作为离线 fallback。

### 3. LLM 选型

- **DashScope `qwen-plus`**：国内访问稳定，OpenAI 兼容，已用于情绪分析，推荐继续作为主力。
- **SiliconFlow**：价格更低，OpenAI 兼容，可作为成本敏感时的备选。
- **Volcano Engine（字节跳动）**：国内可用，但 SDK 较重，适合后续 RTC/高级场景。

### 4. TTS 选型

| 方案 | 位置 | 模型/体积 | Pi Zero 2W 可行性 | 备注 |
|------|------|-----------|-------------------|------|
| Edge TTS | 云端 | 无 | 可用，免费 | 依赖外网，延迟 200-800ms |
| Piper TTS | 本地 | 20-50MB | 实时 | 最佳本地 TTS 候选 |
| Kokoro | 本地 | 较大 | 较慢 | 质量更好但资源占用高 |
| 预录音频库 | 本地 | 4.7MB | 零延迟 | 当前已使用，适合固定情绪反馈 |

结论：保留预录音频库作为快速情绪反馈；新增 Piper TTS 作为动态文本朗读备选；Edge TTS 作为调试/开发备选。

### 5. 性能优化约束

- Pi Zero 2W 仅有 512MB LPDDR2 RAM，运行完整 Linux + Python + 本地模型会非常紧张。
- 推荐系统级优化：
  - 使用 headless Raspberry Pi OS Lite（无桌面）。
  - 启用 zram / swap。
  - `gpu_mem=16` 甚至更低。
  - 关闭蓝牙、WiFi 扫描等不必要服务（如需离线运行）。
  - 32-bit OS 在内存占用上略优于 64-bit。
- 进程级优化：
  - ASR/LLM/TTS 使用可插拔后端，云端优先，本地 fallback。
  - 眼睛动画与语音模块通过共享内存/队列通信，避免阻塞。
  - 音频数据在 `MicMonitor` 与 `VoiceModule` 间通过 `queue.Queue` 或 ring buffer 传递。

### 6. 现有生态参考

- 类似项目（如桌面宠物、具身智能小机器人）通常将 ASR/LLM/TTS 拆分为独立进程或微服务，主进程只做调度与动画。
- 在 512MB RAM 设备上，本地 ASR+TTS+LLM 同时驻留内存会导致 OOM，因此需要分层：
  - 反射级：本地唤醒词 + 快速反应（<100ms）。
  - 情绪级：预录音频 + 简单规则（<500ms）。
  - 认知级：云端 ASR/LLM/TTS（1-3s）。

## 研究空白（Research Gaps）

1. **ALSA 独占问题** 尚未在代码中解决：如何在不关闭 `MicMonitor` 的情况下触发录音？
2. **原始功能完整映射** 尚未完成：哪些功能必须在 Pi 上运行，哪些可以简化或移除？
3. **ASR fallback 策略** 未确定：云端失败时是否降级到本地 SenseVoice，还是直接重试？
4. **TTS 与预录音频的切换策略** 未确定：什么场景用预录，什么场景用 Piper/Edge TTS？
5. **语音与眼睛动画的联动协议** 未设计：事件类型、状态机、优先级。
6. **Pi Zero 2W 内存预算** 未实测：本地 ASR/TTS 同时加载后剩余内存是否足够运行主程序？
7. **离线依赖安装流程** 未验证：Linux 电脑下载 whl 后复制到 Pi 安装是否可行？
8. **长时间运行稳定性** 未测试：ALSA 句柄泄漏、队列积压、进程僵死等问题。
9. **RTC/实时通话** 未评估：火山引擎 RTC 在 Pi Zero 2W 上的可行性。
10. **配置与部署脚本** 缺失：一键启用语音模块、自动检测 ALSA 设备、自动下载模型。

## 待阶段2回答的关键问题

1. 采用“云端优先 + 本地 fallback”还是“本地优先 + 云端增强”？
2. 语音模块作为 `main.py` 中的线程，还是独立进程？
3. 如何复用 `MicMonitor` 的音频流实现 VAD 与录音？
4. 哪些原始功能保留、哪些简化、哪些移除？
5. 眼睛动画的状态机应如何与语音状态（监听/思考/说话/空闲）联动？
6. 新分支的代码结构应如何组织，才能既理想化又可维护？

## 初步判断

- **短期（MVP）**：先解决 ALSA 冲突与 `play_buffer` 缺失，将语音模块接入 `main.py`，实现“听见声音 → 录音 → 识别 → 情绪 → 播放预录音频 → 眼睛联动”的完整链路。
- **中期**：将 ASR/LLM/TTS 抽象为可插拔后端，支持云端与本地模型切换，增加 Piper TTS 与 SenseVoice 本地 fallback。
- **长期**：逐步迁移原始项目的 XP、记忆、碎片器、调度器等高级功能，但需在 Pi Zero 2W 内存约束下取舍。

下一阶段将基于以上发现生成 3 个候选迁移/优化方案，并给出 Top1/Top2 推荐。
