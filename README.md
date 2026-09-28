# 圆宝实时语音助手

这是当前可运行版本的说明入口。程序不使用火山 RTC 房间或 RTC AI Agent，而是由电脑端 Python 直接调用豆包语音 ASR/TTS 和火山方舟模型；可选择使用电脑浏览器，或树莓派的麦克风与扬声器。

```text
麦克风 → 电脑 DirectVoiceClient → 豆包 ASR → 火山方舟 → 豆包 TTS → 扬声器
```

## 当前架构

| 组件 | 运行位置 | 职责 |
| --- | --- | --- |
| `DirectVoiceClient.py` | 电脑 | 网页控制、ASR、模型调用、TTS、记忆、对话记录、日记 |
| 豆包语音 | 火山引擎 | 流式识别与流式合成 |
| 火山方舟 | 火山引擎 | 流式文本对话 |
| `pi_audio_node.py` | 树莓派（可选） | 仅采集麦克风、播放 PCM，不保存云端 Key |

当前主路径需要的云端凭据只有两类：

- 豆包语音 API Key：同时用于 ASR 与 TTS。
- 火山方舟 API Key：用于模型对话；另需可调用的 `Model ID` 或 `Endpoint ID`。

**不需要** RTC App ID、RTC App Key、Access Key/Secret Key、StartVoiceChat、RTC Token、VikingDB。仓库中仍保留的旧 RTC 程序仅供历史参考，不能按旧文档作为当前启动入口。

## 快速开始

1. 在 `rtcVoiceClient/config.json` 填写当前配置。模板见 [config.example.json](rtcVoiceClient/config.example.json)。
2. 电脑端启动：

   ```powershell
   cd C:\Users\Meanieee\Desktop\RTC\RTCVoice_v1.0\rtcVoiceClient
   python .\DirectVoiceClient.py
   ```

3. 默认 `audio_io.mode` 为 `local_browser`，使用电脑浏览器麦克风和扬声器。
4. 使用树莓派硬件时，将 `audio_io.mode` 设为 `raspberry_pi`，并启动树莓派音频节点；电脑网页仍负责全部控制。

## 文档

- [当前运行、配置与树莓派部署说明](CURRENT_DIRECT_VOICE_README.md)
- [火山引擎项目配置指南：开通服务、创建 API Key、获取 Model/Endpoint ID](VOLCENGINE_PROJECT_SETUP.md)
- [旧 RTC 文档迁移说明](VOLCENGINE_RTC_CHECKLIST.md)

## 安全规则

- 真实 Key 只保存在电脑 `rtcVoiceClient/config.json`；不要提交、截图或发送。
- 树莓派只保存音频节点 token，绝不保存豆包/方舟 API Key。
- 局域网调试可使用 `ws://`；长期运行或不可信网络应启用 `wss://`、限制 Windows 防火墙来源 IP，并使用强随机 `audio_io.shared_token`。
