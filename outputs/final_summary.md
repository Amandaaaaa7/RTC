# ARIS 最终总结 — 语音代码迁移与 Pi Zero 2W 优化

## 研究课题

将原项目 `Interaction_Version_Management-master` 的语音交互功能完整迁移到 Doll Robot 项目 `pi_affe_sys`，并针对 Raspberry Pi Zero 2W 的硬件约束（1GHz 四核 A53 / 512MB RAM）进行性能与架构优化，最终形成一套可维护、可扩展、可在 Pi 上长期运行的理想化代码。

## 核心结论

1. **ALSA 独占冲突是最大集成障碍**：`MicMonitor` 与 `VoiceRecorder` 同时打开 `hw:1,0` 会导致失败。解决方案是让 `VoiceModule` 注册为 `MicMonitor` 的音频回调消费者，回调中仅做入队，由独立线程处理 VAD 与录音。
2. **云端优先 + 本地 fallback 是最佳策略**：默认使用 DashScope（国内稳定）完成 ASR/LLM，TTS 使用预录音频库（零延迟），同时通过 `voice/backends/` 抽象层为 SenseVoice / Piper / Edge TTS 预留接口。
3. **Pi Zero 2W 必须避免同时加载多个本地模型**：SenseVoice q8 + tinyLLM + Piper 同时驻留会超出 512MB 内存，因此本地推理只作为可选 fallback，默认不启用。
4. **眼睛动画需要与语音状态联动**：增加 `VAD_TRIGGERED / RECORDING / ASR_PENDING / LLM_PENDING / TTS_PENDING / SPEAKING` 等状态，让等待期间也有视觉反馈。
5. **统一配置与结构化日志是可维护性的基础**：使用 `voice/.env` 管理 API Key，使用 `logging` 替代 print，使用 `VoiceModule.health()` 做运行时自检。

## 最终推荐架构

```
┌─────────────┐     ┌─────────────────────────────┐     ┌─────────────┐
│  MicMonitor │────▶│       VoiceModule           │────▶│  EyeDisplay │
│  (capture)  │     │  ┌─────┐ ┌─────┐ ┌───────┐  │     │ (animation) │
│  + callbacks│     │  │ ASR │ │ LLM │ │  TTS  │  │     │             │
└─────────────┘     │  │接口 │ │接口 │ │ 接口  │  │     └─────────────┘
                    │  └──┬──┘ └──┬──┘ └───┬───┘  │
                    │     │       │        │      │
                    │  ┌──┴───────┴────────┴──┐   │
                    │  │ 云端实现 / 本地实现    │   │
                    │  └──────────────────────┘   │
                    └─────────────────────────────┘
                            │
                            ▼
                  ┌─────────────────────┐
                  │  Memory + XP System │
                  └─────────────────────┘
```

## 针对关键问题的具体解答

| 问题 | 解答 |
|------|------|
| ALSA 独占冲突 | `MicMonitor` 增加 `register_audio_callback`，`VoiceModule` 消费回调而非自己打开设备 |
| `speaker.py` 缺少 `play_buffer` | 已新增 `play_buffer()`，支持 8/16/24/32-bit PCM 任意采样率 |
| 默认 ASR 不稳定 | 默认改为 DashScope Paraformer，Google 作为 fallback |
| 预录音频库播放 MP3 | `PresetAudioTTS` 使用 pydub 解码后调用 `play_buffer` |
| 眼睛如何联动语音 | `EyeDisplay.on_voice_state()` 根据状态调整瞳孔尺度与注视偏移 |
| 配置如何管理 | `voice/config.py` 读取环境变量或 `voice/.env`，不硬编码 Key |
| 长期运行稳定性 | 回调队列设 maxsize、播放后关闭 PCM、周期健康检查 |

## 关键实验计划

| 实验 | 目标 | 成功标准 |
|------|------|----------|
| Exp-1 | MicMonitor 回调性能 | 眼睛动画 ≥20 fps，VU 不卡顿 |
| Exp-2 | play_buffer 播放 | MP3/WAV 播放无爆音 |
| Exp-3 | 云端端到端链路 | 延迟 <3s，成功率 ≥80% |
| Exp-4 | 眼睛状态联动 | 状态切换可见，延迟 <100ms |
| Exp-5 | 长时间稳定性 | 30 分钟无崩溃，内存增长 <20MB |
| Exp-6 | 离线依赖安装 | Pi 上 `--no-index` 安装成功 |

## 风险与回退条件

| 风险 | 回退动作 |
|------|----------|
| DashScope API 不可用 | 切换到 Google ASR / 本地 SenseVoice fallback |
| 回调阻塞导致 MicMonitor 掉帧 | 改为 ring buffer + 独立消费线程 |
| `play_buffer` 全双工失败 | 使用 `dmix`/`dsnoop` ALSA 插件 |
| pydub/ffmpeg 安装困难 | 预先将 MP3 批量转换为 WAV |
| 内存不足 | 禁用 memory/XP 持久化、不加载本地模型 |

## 下一步行动

1. 在 Pi 上安装依赖（离线或联网）。
2. 配置 `voice/.env` 中的 `DASHSCOPE_API_KEY`。
3. 运行 `python3 voice/test_minimal.py` 验证最小闭环。
4. 运行 `python3 main.py --dual-eye` 验证主程序集成。
5. 执行 Exp-1 至 Exp-6，记录结果并修复问题。
6. 根据实验反馈继续完善 `voice/backends/local.py`（SenseVoice / Piper）。

## 产出文件清单

```
outputs/
├── lit_scan.md
├── idea_report.md
├── experiment_plan.md
├── runbook.md
├── review_loop.md
└── final_summary.md

voice/
├── __init__.py
├── config.py
├── module.py              # VoiceModule 主类
├── audio.py               # 基于回调的录音器
├── emotion_engine.py      # 情绪映射
├── memory.py              # 简化记忆
├── xp.py                  # 简化 XP
├── emotion_dialogue.py    # 兼容入口
├── test_minimal.py        # 最小测试
└── backends/
    ├── __init__.py
    ├── base.py            # 抽象接口
    ├── cloud.py           # DashScope / Google / Edge
    └── preset.py          # 预录音频库
```

## 分支信息

- 新分支：`ideal/voice-migration`
- 用途：保存本次 ARIS 优化后的理想化代码，供下一次在新电脑上继续开发时参考。
- 推送命令：
  ```bash
  git checkout -b ideal/voice-migration
  git add .
  git commit -m "ARIS voice migration: callback-based recorder, pluggable backends, eye state sync"
  git push -u origin ideal/voice-migration
  ```
