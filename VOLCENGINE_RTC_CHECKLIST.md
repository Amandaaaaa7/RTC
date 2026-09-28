# 火山 RTC AI 对话配置核对表

本项目使用的是 RTC 的 `StartVoiceChat`（`Version=2025-06-01`）接口：客户端先以 RTC Token 进入房间，服务端再创建 AI Bot 并将用户的音频送入 ASR、方舟 LLM 和 TTS。RTC 的 App ID / App Key 只是其中一部分；ASR、TTS、LLM 没有分别开通或填入凭证时，接口可能返回成功下发，但 AI Bot 仍无法正常入房或对话。

官方依据： [RTC 基本概念](https://docs.volcengine.com/docs/real_time_communication/Basicconcepts-5?lang=zh)、[AI 对话 StartVoiceChat](https://docs.volcengine.com/docs/real_time_communication/StartAIconversationStartVoiceChat?lang=zh&redirect=1)。

## 控制台必须完成的事项

1. 在 **实时音视频 RTC → 应用管理** 创建或选择一个应用，记录同一个应用的 `App ID` 和 `App Key`。两者必须同时用于本程序：`App ID` 创建 Web SDK 引擎和调用 StartVoiceChat，`App Key` 只留在本机/服务端生成 RTC Token，绝不能放入浏览器或仓库。
2. 在账号的 **访问控制 → Access Key 管理** 创建可调用 RTC OpenAPI 的 AK/SK，并确保该身份具有 RTC AI 对话相关权限。把它填入 `volcano.access_key` 和 `volcano.secret_key`。AK/SK 与 RTC App Key 是两套不同的凭证，不能互相替代。
3. 在 **豆包语音控制台 → 流式语音识别大模型** 开通实际要使用的资源，获取该语音应用的 `App ID`、`Access Token` 和资源类型。填到 `rtc_agent.asr`。若开通的是 ASR 2.0，资源类型通常是 `volc.seedasr.sauc.duration` 或并发版；`stream_mode` 使用 2。
4. 在 **豆包语音控制台 → 语音合成大模型** 开通 TTS，获取该应用的 `App ID` 和 `Access Token`；在同一服务里选择与资源版本匹配的音色，填到 `rtc_agent.tts`。示例模板的 `seed-tts-1.0` 只能搭配该版本可用的音色；如使用 TTS 2.0，要同时改为已开通的 2.0 资源和音色。
5. 在 **火山方舟 → 推理接入点** 创建并启用一个**自定义推理接入点**，选择已获得权限的文本模型，复制形如 `ep-...` 的接入点 ID 到 `rtc_agent.llm.endpoint_id`。不要填模型展示名或预置推理接入点；RTC AI 对话当前字段是 `EndPointId`。
6. （可选）在 **VikingDB → 记忆库** 创建记忆库实例和可访问该实例的 API Key。确认实例名后才将 `memory.Enable` 改为 `true`，再填 `memory.api_key` 与 `collection_name`。不使用长期记忆时保持 `false`。
7. 在 **RTC 控制台 → 功能配置** 开启 VoiceChat 任务事件回调（测试阶段至少开启控制台可见的状态/错误事件）。HTTP 返回 `200` 只表示任务已下发，不代表 AI Bot 入房、ASR、LLM、TTS 均正常；事件回调才是定位服务侧错误的依据。

## 本地填写与启动

首次执行 `RTCVoiceClient_3.3.py` 时，程序会把 `rtcVoiceClient/config.example.json` 复制为同目录的 `config.json`。填写真实值只写入 `config.json`，不要提交、截图或发送其中任何密钥。

关键字段的对应关系如下：

| 配置字段 | 来源 | 用途 |
| --- | --- | --- |
| `volcano.app_id` / `app_key` | RTC 应用详情 | Web SDK / RTC Token |
| `volcano.access_key` / `secret_key` | 访问控制 | 签名调用 StartVoiceChat、StopVoiceChat |
| `rtc_agent.asr.*` | 豆包语音 ASR 应用 | 用户语音识别 |
| `rtc_agent.tts.*` | 豆包语音 TTS 应用 | AI 语音播报 |
| `rtc_agent.llm.endpoint_id` | 火山方舟自定义推理接入点 | AI 文本推理 |
| `memory.*` | VikingDB 记忆库 | 可选跨会话记忆 |

填完后运行：

```powershell
cd RTCVoice_v1.0\rtcVoiceClient
python RTCVoiceClient_3.3.py
```

若界面提示缺少字段，先按提示补齐 `config.json`；这是本地校验，不会向火山引擎发送请求。若 StartVoiceChat 已返回成功但仍无声音或 AI 未入房，优先查看第 7 步的 VoiceChat 事件回调错误码。
