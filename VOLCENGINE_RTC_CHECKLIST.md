# 旧 RTC 配置清单：已废弃

此文件名因兼容历史链接而保留，但其中曾描述的 RTC `StartVoiceChat` / AI Agent 方案**不是当前项目运行路径**。

当前主程序是：

```text
rtcVoiceClient/DirectVoiceClient.py
```

它直接使用豆包语音 API Key 和火山方舟 API Key，不使用：

- RTC App ID / App Key
- 火山引擎 Access Key / Secret Key
- RTC Token
- StartVoiceChat / StopVoiceChat
- RTC VoiceChat 回调
- `rtc_agent.*` 配置
- VikingDB 记忆库

请改用以下文档：

- [火山引擎项目配置指南](VOLCENGINE_PROJECT_SETUP.md)：开通豆包 ASR/TTS、方舟模型、创建 API Key、填写实际配置。
- [当前直连语音助手运行说明](CURRENT_DIRECT_VOICE_README.md)：本机/树莓派部署、音频和故障排查。

旧 RTC 程序 `rtcVoiceClient/RTCVoiceClient_3.3.py` 仍在仓库中，仅用于历史对照；请勿按旧 RTC 清单为当前直连程序申请或填写凭据。
