# 圆宝当前直连语音助手：运行与部署说明

本文只说明当前运行入口 `rtcVoiceClient/DirectVoiceClient.py`。它直接调用豆包语音与火山方舟，**不创建 RTC 房间、不调用 StartVoiceChat、不需要 RTC 或 AK/SK 凭据**。

```text
本机模式：电脑浏览器麦克风 → 电脑 Python → 豆包 ASR → 方舟 → 豆包 TTS → 电脑浏览器扬声器

树莓派模式：树莓派麦克风 → 树莓派音频节点 → 电脑 Python → 豆包 ASR → 方舟
          → 豆包 TTS → 树莓派音频节点 → 树莓派扬声器
```

电脑端保存全部云端 Key、对话记录、长期记忆和日记；树莓派只是受认证的音频外设。

## 1. 当前目录与入口

| 文件 | 用途 |
| --- | --- |
| `rtcVoiceClient/DirectVoiceClient.py` | 当前电脑端主程序，也是唯一推荐启动入口。 |
| `rtcVoiceClient/config.json` | 电脑端私密配置，保存豆包/方舟 Key。 |
| `rtcVoiceClient/config.example.json` | 与当前代码一致的无密钥配置模板。 |
| `rtcVoiceClient/pi_audio_gateway.py` | 电脑端树莓派音频 WebSocket 网关。 |
| `rtcVoiceClient/pi_node/pi_audio_node.py` | 树莓派音频节点。 |
| `rtcVoiceClient/MEMORY.md` | 自动维护的长期记忆。 |
| `rtcVoiceClient/conversation_history.md` | 问答记录。 |
| `rtcVoiceClient/conversation_summary.txt` | 停止会话后生成的日记。 |

`RTCVoiceClient_3.3.py`、`AccessToken.py`、`Web_4.68.1.min.js` 与旧 RTC 配置段不是当前主路径的一部分。

## 2. 实际需要的火山引擎配置

| 服务 | 必填资源 | 写入字段 |
| --- | --- | --- |
| 豆包语音 | 同一项目中已开通的流式 ASR 2.0、TTS 2.0、API Key | `direct_voice.api_key` |
| 火山方舟 | API Key、可调用的 Model ID 或 Endpoint ID | `ark.api_key`、`ark.model` |

详细的控制台操作、功能开通、Key 来源、模型/接入点选择见 [VOLCENGINE_PROJECT_SETUP.md](VOLCENGINE_PROJECT_SETUP.md)。

以下旧资源不应再为了本程序创建或填写：RTC App ID/App Key、火山引擎 Access Key/Secret Key、RTC Token、VoiceChat 回调、VikingDB 记忆库。

## 3. 电脑端 `config.json`

首次配置时复制模板：

```powershell
cd C:\Users\Meanieee\Desktop\RTC\RTCVoice_v1.0\rtcVoiceClient
Copy-Item .\config.example.json .\config.json
```

仅需要填写以下字段；示例中的 Key 与模型 ID 均为占位符：

```json
{
  "scene": {
    "welcome_message": "你好！我是圆宝，我来啦我来啦。"
  },
  "direct_voice": {
    "api_key": "你的豆包语音APIKey",
    "asr_resource_id": "volc.seedasr.sauc.duration",
    "tts_resource_id": "seed-tts-2.0",
    "tts_voice_type": "zh_female_vv_uranus_bigtts",
    "tts_sample_rate": 24000
  },
  "ark": {
    "api_key": "你的方舟APIKey",
    "model": "你的ModelID或ep-接入点ID",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "thinking": "disabled"
  },
  "local_memory": {
    "enable": true,
    "max_context_memories": 6,
    "write_diary_on_stop": true
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `direct_voice.api_key` | 电脑端使用的豆包语音 API Key；同一 Key 同时用于本程序 ASR/TTS。 |
| `asr_resource_id` | 当前推荐 `volc.seedasr.sauc.duration`（流式识别 2.0 小时版）。 |
| `tts_resource_id` | 当前代码默认 `seed-tts-2.0`。音色必须与已开通的 TTS 资源匹配。 |
| `tts_voice_type` | 豆包语音控制台音色库中的音色 ID。 |
| `ark.api_key` | 火山方舟项目中的 API Key。 |
| `ark.model` | 预置接入点的 Model ID，或自定义接入点的 `ep-...` Endpoint ID。 |
| `ark.thinking` | 推荐 `disabled`，降低实时对话的首字等待。 |
| `local_memory` | 本地 Markdown 记忆和停止后的日记开关；无云端记忆库依赖。 |

`llm` 段是可选项，只用于停止会话后以第三方 OpenAI 兼容接口写日记；没有它时日记自动回退到方舟。它不参与实时对话。

## 4. 电脑本机模式

使用电脑麦克风和扬声器时，设置：

```json
"audio_io": { "mode": "local_browser" }
```

启动：

```powershell
cd C:\Users\Meanieee\Desktop\RTC\RTCVoice_v1.0\rtcVoiceClient
python .\DirectVoiceClient.py
```

网页打开后点击“启动AI助手”，授权电脑浏览器使用麦克风。网页显示字幕，TTS 在电脑端播放。

## 5. 树莓派音频模式

树莓派模式仍由电脑网页控制开始、停止、字幕和记忆。树莓派不保存火山 Key。

### 5.1 电脑配置

电脑 `config.json` 增加或修改：

```json
"audio_io": {
  "mode": "raspberry_pi",
  "node_id": "yuanbao-pi",
  "listen_host": "0.0.0.0",
  "listen_port": 8765,
  "shared_token": "仅电脑与树莓派知道的强随机字符串",
  "tls": {"enabled": false, "cert_file": "certs/server.crt", "key_file": "certs/server.key"},
  "capture": {"sample_rate": 16000, "channels": 1, "sample_width": 2, "frame_ms": 40},
  "playback": {"sample_rate": 24000, "channels": 1, "sample_width": 2},
  "pause_capture_during_playback": true
}
```

首次在可信局域网调试时可使用 `ws://` 与 `tls.enabled: false`。长期使用或不可信网络必须使用受信任证书的 `wss://`，并在 Windows 防火墙只允许树莓派 IP 访问 TCP `8765`。

### 5.2 树莓派配置与启动

将 `pi_node` 中以下文件复制到树莓派任意工作目录：

```text
pi_audio_node.py
config.json
requirements.txt
```

树莓派 `config.json` 中填写电脑的局域网地址：

```json
{
  "node_id": "yuanbao-pi",
  "server_url": "ws://电脑局域网IP:8765",
  "token_env": "RTCVOICE_NODE_TOKEN",
  "input_device": "default",
  "output_device": "default",
  "capture_sample_rate": 16000,
  "playback_sample_rate": 24000,
  "device_capture_sample_rate": 48000,
  "device_playback_sample_rate": 48000,
  "channels": 1,
  "frame_ms": 40,
  "echo_guard_ms": 220,
  "reconnect_seconds": 3
}
```

`capture/playback_sample_rate` 是电脑协议采样率；`device_*_sample_rate` 是实际打开树莓派声卡的采样率。默认 48 kHz 可兼容多数 USB/I2S 声卡，节点会自动在 48 kHz ↔ 16/24 kHz 间转换。

树莓派首次安装：

```bash
sudo apt update
sudo apt install -y python3-pyaudio python3-venv portaudio19-dev
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

创建仅树莓派本机保存的 token 文件：

```bash
sudo nano /etc/rtcvoice-audio-node.env
```

内容为：

```text
RTCVOICE_NODE_TOKEN=与电脑audio_io.shared_token完全相同的值
```

手动启动时让当前用户可读取该文件：

```bash
sudo chown "$USER":"$USER" /etc/rtcvoice-audio-node.env
sudo chmod 600 /etc/rtcvoice-audio-node.env
set -a
source /etc/rtcvoice-audio-node.env
set +a
python pi_audio_node.py --config config.json
```

终端显示“已连接电脑端”后，回到电脑网页点击“启动AI助手”。

### 5.3 回声保护与 ASR 保活

圆宝播放时，节点暂停上传真实麦克风内容，改为持续发送同长度静音 PCM。这样既减少扬声器回声被识别为人声，也避免豆包流式 ASR 因约 8 秒没有收到音频包而关闭会话。

## 6. 验证清单

1. 电脑启动后，网页显示欢迎界面。
2. 树莓派模式下，网页顶部显示“树莓派音频节点已连接”。
3. 点击启动，树莓派终端显示“麦克风已开始采集（硬件 48000 Hz → ASR 16000 Hz）”。
4. 说一句完整的话并停顿约一秒，网页出现用户字幕与圆宝回复。
5. 圆宝播放超过 8 秒后，ASR 不应出现 `Timeout waiting next packet`。
6. 说“我最近在看哈姆雷特”，页面显示已记住；之后询问最近在看什么，圆宝应能回答。

## 7. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `Invalid sample rate` | 树莓派声卡不支持直接 16 kHz；使用 `device_capture_sample_rate: 48000` 和最新 `pi_audio_node.py`。 |
| `Timeout waiting next packet` | 上传被暂停导致 ASR 超时；上传最新节点，它会在播放时发送静音保活。 |
| `ASR 连接失败: timed out during opening handshake` | 电脑到豆包 ASR 的网络/代理问题；确认电脑可访问 `openspeech.bytedance.com`，重启电脑主程序后重试。 |
| 有字幕无声音 | 检查树莓派 `aplay -l`，再在节点配置中用稳定设备名称替代 `output_device: default`。 |
| 没有用户字幕 | 检查 `arecord -l`，并调整 `input_device`；确认节点仍显示已连接。 |
| 树莓派连接不上 | 检查电脑 IP、TCP 8765 防火墙规则、`shared_token` 与 `RTCVOICE_NODE_TOKEN` 是否完全一致。 |
