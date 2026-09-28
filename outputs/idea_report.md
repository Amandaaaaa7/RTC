# 阶段2：候选方案生成 — 语音代码迁移与 Pi Zero 2W 优化

## 方案总览

| 方案 | 定位 | ASR | LLM | TTS | 眼睛联动 | 原始功能 | 风险等级 |
|------|------|-----|-----|-----|----------|----------|----------|
| A 轻量集成 | 最保守、最快可用 | Google / DashScope 云端 | DashScope 云端 | 预录音频库 + Edge TTS | 简单状态机 | 仅情绪对话 | 低 |
| B 离线自治 | 最激进、完全离线 | SenseVoice q8 本地 | 本地 tiny 或可选云端 | Piper 本地 | 完整状态机 | 情绪对话 + 核心记忆/XP | 高 |
| C 混合分层 | 折中、可扩展 | 云端默认 + SenseVoice fallback | DashScope 云端 | 预录 + Piper + Edge 可插拔 | 完整状态机 | 情绪对话 + XP/记忆子集 | 中 |

---

## 方案 A：轻量集成（云端优先）

### 一句话定位

先解决 ALSA 冲突与主程序集成，用最少的改动让语音情绪对话跑在 Pi 上，所有智能依赖云端 API。

### 架构图

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  MicMonitor │────>│ VoiceModule │────>│  EyeDisplay │
│  (持续capture)│     │ (VAD/录音/调用API)│     │ (状态联动)  │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
      Google ASR      DashScope LLM    预录音频库
      / DashScope                        / Edge TTS
```

### 核心时序

1. `main.py` 启动 `MicMonitor`（独占 `hw:1,0` capture）。
2. `VoiceModule` 注册为 `MicMonitor` 的音频回调消费者。
3. 当 RMS 超过阈值并持续一定时间，`VoiceModule` 开始累积音频帧到 WAV。
4. 静默超时后，调用云端 ASR 识别文字。
5. 调用 DashScope LLM 分析情绪。
6. 从 `audio_assets/vo/<emotion>/` 随机选择 MP3，调用 `speaker.play_audio()` 播放。
7. 播放期间 `EyeDisplay` 进入 `SPEAKING` 状态，说完回到 `IDLE`。

### 关键改动点

- `mic.py`：新增 `register_audio_callback(callback)` 与 `unregister_audio_callback(callback)`，支持多消费者。
- `voice/audio.py`：移除独立 ALSA capture 逻辑，`VoiceRecorder` 改为消费 `MicMonitor` 回调数据。
- `speaker.py`：新增 `play_buffer(pcm_bytes, sample_rate, channels, fmt)`。
- `voice/emotion_dialogue.py`：改为基于事件回调的 `VoiceModule` 类。
- `main.py`：实例化 `VoiceModule` 并传入 `mic_monitor` 与 `eye_display`。
- `eye_display.py`：新增 `on_voice_state(state)` 接口与简单状态机（IDLE / LISTENING / THINKING / SPEAKING）。

### 优点

- 改动最小，2-3 天可完成并跑通。
- 不增加 Pi 的本地内存压力。
- 预录音频库零延迟，体验流畅。

### 风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 网络不稳定导致 ASR/LLM 失败 | 中 | 国内 Google ASR 可能超时；DashScope 相对稳定 |
| 麦克风回调延迟导致 VAD 不灵敏 | 低 | 当前 MicMonitor 已是后台线程 |
| 眼睛动画状态机过于简单 | 低 | 可后续扩展 |

### MVP

- 修改 `mic.py` 支持回调 → 修改 `voice/audio.py` 复用 MicMonitor → 修改 `speaker.py` 增加 `play_buffer` → `main.py` 集成 `VoiceModule` → `eye_display.py` 增加语音状态 → 在 Pi 上跑通一句对话。

### 失败信号

- ALSA 回调导致 `MicMonitor` 性能下降，VU 表帧率明显掉帧。
- 云端 API 延迟 >3s，体验不可接受。
- 预录音频库无法覆盖用户期望的回应多样性。

---

## 方案 B：离线自治（本地优先）

### 一句话定位

将所有智能推理搬到 Pi 本地，目标是无需联网即可持续对话，但需要承担内存与启动时间压力。

### 架构图

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────┐
│  MicMonitor │────>│   VoiceModule       │────>│  EyeDisplay │
│             │     │ (SenseVoice + tinyLLM │     │             │
│             │     │  + Piper + 本地记忆)   │     │             │
└─────────────┘     └─────────────────────┘     └─────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
      SenseVoice q8     tiny/Qwen-0.5B      Piper TTS
       (254MB)           (300-500MB)        (50MB)
```

### 核心时序

1. 启动时一次性加载 SenseVoice、tinyLLM、Piper 到内存。
2. `MicMonitor` 持续 capture，`VoiceModule` 监听回调。
3. 触发 VAD 后累积音频，直接调用本地 SenseVoice 识别。
4. 将识别文本送入 tinyLLM 生成回应文本与情绪标签。
5. 使用 Piper 合成语音并通过 `speaker.play_buffer()` 播放。
6. 同时将对话写入本地 SQLite 记忆矩阵。

### 关键改动点

- 新增 `voice/asr_sensevoice.py`：加载 SenseVoice GGUF，提供 `transcribe(wav_bytes) -> text`。
- 新增 `voice/llm_local.py`：加载 Qwen-0.5B / tinyLLM，提供 `chat(context) -> (text, emotion)`。
- 新增 `voice/tts_piper.py`：加载 Piper 模型，提供 `synthesize(text) -> pcm_bytes`。
- 新增 `voice/memory.py`：SQLite 双轨记忆矩阵（短期/长期）。
- 新增 `voice/xp.py`：XP 经验与等级系统。
- 修改启动脚本：按需下载 SenseVoice / Piper 模型。
- 系统级优化：zram、headless、关闭不必要服务。

### 优点

- 完全离线运行，不受网络波动影响。
- 可移植性强，适合展示/竞赛场景。
- 能完整迁移原始项目的记忆、XP 等核心机制。

### 风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 内存不足导致 OOM | 高 | SenseVoice + tinyLLM + Piper 同时驻留可能超过 512MB |
| 推理速度慢，对话卡顿 | 高 | tinyLLM 在 Pi Zero 2W 上可能每秒几个 token |
| 启动时间长 | 高 | 模型加载可能需要数十秒 |
| 中文 ASR/TTS 质量下降 | 中 | 本地模型效果通常弱于云端 |

### MVP

- 单独验证 SenseVoice 在 Pi 上能否实时识别（<2s）。
- 单独验证 Piper 中文语音是否可接受。
- 单独验证 tinyLLM 加载后剩余内存。
- 三者同时运行 5 分钟，监控内存与响应延迟。

### 失败信号

- 任一模型加载后系统可用内存 <50MB。
- 端到端延迟 >5s。
- ASR 识别准确率 <70%。

---

## 方案 C：混合分层（可插拔后端）

### 一句话定位

默认走云端保证体验，云端失败时降级到本地模型；所有后端抽象为统一接口，眼睛动画与记忆系统完整联动。

### 架构图

```
┌─────────────┐     ┌─────────────────────────────┐     ┌─────────────┐
│  MicMonitor │────>│       VoiceModule           │────>│  EyeDisplay │
│             │     │  ┌─────┐ ┌─────┐ ┌───────┐  │     │             │
│             │     │  │ ASR │ │ LLM │ │  TTS  │  │     │             │
│             │     │  │接口 │ │接口 │ │ 接口  │  │     │             │
│             │     │  └──┬──┘ └──┬──┘ └───┬───┘  │     │             │
│             │     │     │       │        │      │     │             │
│             │     │  ┌──┴───────┴────────┴──┐   │     │             │
│             │     │  │ 云端实现 / 本地实现    │   │     │             │
│             │     │  └──────────────────────┘   │     │             │
└─────────────┘     └─────────────────────────────┘     └─────────────┘
                            │
                            ▼
                  ┌─────────────────────┐
                  │  Memory + XP System │
                  └─────────────────────┘
```

### 核心时序

1. `VoiceModule` 持有三个后端接口：`ASRBackend`、`LLMBackend`、`TTSBackend`。
2. 默认配置：`DashScopeASR` → `DashScopeLLM` → `PresetAudioTTS`（预录音频）。
3. 当云端失败或配置为离线模式时，自动/手动切换到 `SenseVoiceASR` / `DashScopeLLM`（可选本地 tinyLLM）/ `PiperTTS`。
4. 对话上下文进入 `Memory` 模块，影响情绪选择与长期记忆。
5. `XP` 模块根据互动频率/质量升级，解锁更多回应策略。
6. `EyeDisplay` 根据 `VoiceState` 与情绪标签执行不同动画。

### 关键改动点

- 新增 `voice/backends/base.py`：定义 `ASRBackend`、`LLMBackend`、`TTSBackend` 抽象类。
- 新增 `voice/backends/cloud.py`：DashScope ASR / LLM，Edge TTS。
- 新增 `voice/backends/local.py`：SenseVoice ASR，Piper TTS，可选 tinyLLM。
- 新增 `voice/backends/preset.py`：预录音频库 TTS。
- 新增 `voice/memory.py`：简化版双轨记忆矩阵。
- 新增 `voice/xp.py`：简化版 XP 系统。
- 新增 `voice/emotion_engine.py`：将 LLM 返回的情绪标签映射到眼睛动画参数与音频选择策略。
- 修改 `voice/emotion_dialogue.py` 为 `voice/module.py`：`VoiceModule` 类统一调度。
- 修改 `main.py`：启动 `VoiceModule` 并传入 `mic_monitor`、`eye_display`、配置。
- 修改 `eye_display.py`：扩展 `EyeState` 枚举，支持 `LISTENING`、`THINKING`、`SPEAKING`、`SURPRISED` 等。

### 优点

- 云端体验好，离线有兜底。
- 代码结构清晰，后续替换后端不影响主流程。
- 保留原始项目的记忆、XP 等核心机制，但可按需裁剪。
- 眼睛动画与语音状态完整联动，体验更生动。

### 风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 代码量增加，开发周期较长 | 中 | 需要 1-2 周完成并测试 |
| 本地后端首次运行配置复杂 | 中 | 需要下载模型、处理依赖 |
| 抽象层可能引入不必要的复杂度 | 中 | 需要保持接口稳定 |

### MVP

- 先实现云端默认链路（方案 A 的功能）。
- 再抽象出后端接口。
- 再接入一个本地后端（如 Piper TTS）。
- 最后加入记忆与 XP 简化版。

### 失败信号

- 抽象层导致主流程延迟增加 >200ms。
- 本地后端加载失败时降级逻辑有漏洞。
- 记忆/XP 系统导致数据库锁或性能问题。

---

## 方案对比总表

| 维度 | 方案 A | 方案 B | 方案 C |
|------|--------|--------|--------|
| 开发周期 | 2-3 天 | 2-4 周 | 1-2 周 |
| Pi 内存压力 | 低 | 高 | 中（云端模式低） |
| 网络依赖 | 高 | 无 | 中（可降级） |
| 离线能力 | 无 | 完整 | 部分 |
| 原始功能迁移 | 最少 | 最多 | 核心子集 |
| 可维护性 | 高 | 中 | 高 |
| 眼睛联动 | 简单 | 完整 | 完整 |
| 扩展性 | 低 | 中 | 高 |
| 风险 | 低 | 高 | 中 |
| 推荐排序 | Top2 | 不推荐 | Top1 |

---

## Top1 推荐：方案 C（混合分层）

### 理由

1. **平衡体验与可行性**：默认云端保证响应质量，离线 fallback 保证可用性，避免方案 A 一旦断网就完全失效，也避免方案 B 在 512MB RAM 上频繁 OOM。
2. **结构可扩展**：后端抽象层让 ASR/LLM/TTS 可以独立替换，未来升级模型或切换供应商时影响面小。
3. **保留原始项目灵魂**：记忆、XP、情绪引擎等核心机制得以保留，但只实现最小可用版本，避免过度工程。
4. **与现有代码集成最自然**：`MicMonitor` 回调、`EyeDisplay` 状态机、`speaker.py` 播放接口都能被统一接入。

## Top2 推荐：方案 A（轻量集成）

### 理由

- 如果用户希望最快速度在 Pi 上验证语音链路，方案 A 是最佳起点。
- 它是方案 C 的 MVP 阶段，完成 A 后再逐步抽象为 C，风险可控。
- 对于演示“眼睛 + 语音情绪反馈”的最小闭环，A 已足够。

## 不推荐方案 B

- Pi Zero 2W 的 512MB RAM 无法稳定同时承载 SenseVoice + tinyLLM + Piper。
- 启动时间与推理延迟会显著影响交互体验。
- 可作为未来升级到 Pi 5 或 CM4 时的候选，但不在当前硬件目标内。

---

## 进入阶段3的前提

确定采用 **方案 C（混合分层）**，并以其为 Top1 设计实验计划、运行手册与评审循环。阶段3将按“先完成方案 A 的 MVP，再抽象为方案 C”的渐进路线制定具体步骤。
