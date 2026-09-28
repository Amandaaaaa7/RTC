# 火山引擎项目配置指南（当前直连语音助手）

本文对应当前 `rtcVoiceClient/DirectVoiceClient.py`，不是旧 RTC AI Agent。配置完成后，电脑端直接使用：

```text
豆包语音 API Key：流式 ASR 2.0 + TTS 2.0
火山方舟 API Key：流式对话模型
```

不需要创建 RTC 应用，不需要 RTC App ID/App Key，不需要 Access Key/Secret Key，不需要 StartVoiceChat 或 VoiceChat 回调。

## 1. 先确认项目空间

豆包语音 API Key 和方舟 API Key 均与其所在项目空间相关。先登录 [火山引擎控制台](https://console.volcengine.com/)，确认页面左下角/顶部正在使用的项目空间；建议把本项目的语音服务、方舟模型和 API Key 放在同一项目中。

创建 Key 后不要把真实内容发到聊天、截图、Git 仓库或树莓派。泄露时立即到对应 API Key 管理页面禁用或删除并生成新 Key。

## 2. 豆包语音：开通 ASR 与 TTS

进入 [豆包语音控制台](https://console.volcengine.com/speech/)，在当前项目中完成以下两项开通：

1. 在“开通管理”中开通 **豆包流式语音识别模型 2.0**。
2. 在“开通管理”中开通 **豆包语音合成大模型 2.0**。

控制台名称会随版本调整；若页面显示“模型服务”“服务开通”或“快速购买”，选择名称包含上述能力的 2.0 服务即可。费用、可用音色和资源包以控制台当前页面为准。

### 2.1 创建豆包语音 API Key

在豆包语音控制台的“API Key 管理”创建 API Key，复制一次后保存在电脑 `rtcVoiceClient/config.json` 的：

```json
"direct_voice": {
  "api_key": "这里填豆包语音API Key"
}
```

当前程序使用新版 API Key 鉴权，发送 `X-Api-Key` 请求头；不读取旧版 App ID、Access Token 或 Secret Key。豆包语音官方说明指出 API Key 可在控制台的 API Key 管理中查看/创建，且新版调用无需 App ID：[API Key 使用](https://docs.volcengine.com/docs/DoubaoVoice/APIKeyUsage)。

### 2.2 填入本项目使用的语音资源

在同一 `direct_voice` 段中保留或调整：

```json
{
  "asr_resource_id": "volc.seedasr.sauc.duration",
  "tts_resource_id": "seed-tts-2.0",
  "tts_voice_type": "zh_female_vv_uranus_bigtts",
  "tts_sample_rate": 24000
}
```

| 字段 | 当前用途 | 选择原则 |
| --- | --- | --- |
| `asr_resource_id` | 流式语音识别 | `volc.seedasr.sauc.duration` 是 ASR 2.0 小时版；如已购买并发版才改为 `volc.seedasr.sauc.concurrent`。 |
| `tts_resource_id` | 流式语音合成 | 当前实现使用 `seed-tts-2.0`。 |
| `tts_voice_type` | 合成音色 | 到豆包语音控制台的音色库复制该 TTS 资源可用的音色 ID。 |
| `tts_sample_rate` | 电脑到扬声器的 PCM 采样率 | 保持 `24000`；树莓派节点会自动转换为设备常用的 48 kHz。 |

流式 ASR 2.0 官方文档列出的资源 ID 包含 `volc.seedasr.sauc.duration`（小时版）和 `volc.seedasr.sauc.concurrent`（并发版），并说明使用 `X-Api-Key` 鉴权：[流式 ASR WebSocket](https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-automatic-speech-recognition-websocket?lang=zh)。TTS 2.0 的 `seed-tts-2.0` 资源 ID 见官方鉴权/资源说明：[语音合成大模型](https://docs.volcengine.com/docs/6561/2534847?lang=zh)。

## 3. 火山方舟：开通模型、获取 API Key 和模型标识

进入 [火山方舟控制台](https://console.volcengine.com/ark/)。本程序使用 OpenAI 兼容的 Chat Completions 接口，默认地址为：

```text
https://ark.cn-beijing.volces.com/api/v3
```

### 3.1 选择一个可调用模型

在“模型列表”或“在线推理”中选择并开通一个文本对话模型。随后有两种填写 `ark.model` 的方式：

| 方式 | 填写内容 | 适用情况 |
| --- | --- | --- |
| 预置推理接入点 | `Model ID` | 个人/低流量快速开始；不需要创建自定义接入点。 |
| 自定义推理接入点 | `Endpoint ID`，通常形如 `ep-...` | 需要指定模型、配置或独立接入点时。 |

自定义接入点创建完成后需等待状态变为“健康”再调用。方舟官方说明确认：预置接入点直接使用 Model ID；自定义接入点使用 Endpoint ID：[在线推理（常规）](https://docs.volcengine.com/docs/ark/online-inference-standard?lang=zh)。

### 3.2 创建方舟 API Key

在当前方舟项目的“API Key 管理”中点击“创建 API Key”。将生成的 Key 填到电脑配置：

```json
"ark": {
  "api_key": "这里填方舟API Key",
  "model": "这里填Model ID或ep-Endpoint ID",
  "base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "thinking": "disabled"
}
```

推荐在 API Key 页面为 Key 限制可调用模型/接入点；如果控制台提供 IP 白名单，生产使用时也应限制为电脑出口 IP。方舟 API Key 与项目/接入点存在关联，跨项目使用会导致鉴权失败；详见官方 [获取 API Key 并配置](https://docs.volcengine.com/docs/ark/api-key?lang=zh)。

`thinking: "disabled"` 是本项目面向实时语音的推荐值，可降低模型在回答前的等待时间；它不是控制台开关。

## 4. 最小可运行配置

以下为电脑 `rtcVoiceClient/config.json` 的最小必要配置。真实 Key 请自行填写：

```json
{
  "direct_voice": {
    "api_key": "DOUBAO_VOICE_API_KEY",
    "asr_resource_id": "volc.seedasr.sauc.duration",
    "tts_resource_id": "seed-tts-2.0",
    "tts_voice_type": "已开通的音色ID",
    "tts_sample_rate": 24000
  },
  "ark": {
    "api_key": "ARK_API_KEY",
    "model": "MODEL_ID_OR_EP_ID",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "thinking": "disabled"
  }
}
```

运行前，`DirectVoiceClient.py` 会检查 `direct_voice.api_key`、`ark.api_key` 和 `ark.model`。缺少任一字段会在点击“启动AI助手”时提示，不会发起云端调用。

## 5. 与旧 RTC 配置的区别

| 旧字段/资源 | 当前直连程序是否使用 | 处理 |
| --- | --- | --- |
| `volcano.access_key` / `secret_key` | 否 | 不要为当前方案创建或填写。 |
| `volcano.app_id` / `app_key` | 否 | 不需要 RTC 应用。 |
| `rtc_agent.asr` / `tts` / `llm` | 否 | 不需要 App ID、Access Token 或 RTC Agent 参数。 |
| `memory` / VikingDB | 否 | 当前长期记忆写入本地 `MEMORY.md`。 |
| `llm` | 可选 | 只用于会话停止后生成日记；实时对话固定使用 `ark`。 |

旧文件名 `VOLCENGINE_RTC_CHECKLIST.md` 仅保留迁移提示，不能作为当前配置步骤。

## 6. 配置后的验证与排错

1. 启动电脑端 `DirectVoiceClient.py`。
2. 点击“启动AI助手”。
3. 看到“ASR 已连接，正在聆听”后说一句完整的话。
4. 网页应显示用户字幕、方舟回复，随后听到 TTS。

常见鉴权症状：

| 症状 | 优先检查 |
| --- | --- |
| ASR 连接/返回鉴权错误 | 豆包语音 API Key 所在项目、ASR 2.0 是否已开通、`asr_resource_id` 是否匹配。 |
| TTS 返回资源或音色错误 | TTS 2.0 是否已开通、`seed-tts-2.0` 与 `tts_voice_type` 是否匹配。 |
| 方舟 401/403 | 方舟 API Key 与 `model` 是否属于同一项目，Key 是否被模型/接入点权限限制拦截。 |
| 方舟模型不存在 | `ark.model` 填错了 Model ID/Endpoint ID，或自定义接入点尚未健康。 |

树莓派连接、音频设备和网络端口问题不属于火山引擎配置，见 [CURRENT_DIRECT_VOICE_README.md](CURRENT_DIRECT_VOICE_README.md)。
