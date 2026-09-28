# 圆宝本机直连语音助手：当前实现说明

本文记录目前可运行的“本机和圆宝实时说话”方案。它是对旧版 RTC AI Agent 的本机直连替代：Python 后端持有密钥并调用火山服务。

默认仍为电脑浏览器采集/播放音频；也支持“电脑端控制 + 树莓派麦克风/扬声器”的模式。树莓派只运行轻量音频节点，不保存火山、方舟或任何模型密钥。

## 1. 配置说明

配置文件位于 `rtcVoiceClient/config.json`；可参考同目录 `config.example.json`。所有密钥只应保存在本机配置文件中，不能提交到 Git、截图或发送到聊天中。

### 当前直连程序实际使用的配置

| 配置段 | 必填 | 用途 |
| --- | --- | --- |
| `direct_voice.api_key` | 是 | 豆包语音 API Key。用于 ASR（语音识别）和 TTS（语音合成）。 |
| `direct_voice.asr_resource_id` | 是/有默认值 | 默认 `volc.seedasr.sauc.duration`。 |
| `direct_voice.tts_resource_id` | 是/有默认值 | 默认 `seed-tts-2.0`。 |
| `direct_voice.tts_voice_type` | 否 | 圆宝的语音音色。 |
| `ark.api_key` | 是 | 火山方舟 API Key，用于对话回复。 |
| `ark.model` | 是 | 已开通模型或推理接入点 ID，例如 `ep-...`。 |
| `ark.base_url` | 否 | 默认 `https://ark.cn-beijing.volces.com/api/v3`。 |
| `ark.thinking` | 建议 | 当前建议为 `disabled`，避免深度思考显著增加首字延迟。 |
| `scene.welcome_message` | 否 | 启动后展示并语音播报的欢迎语。 |
| `local_memory.enable` | 建议 | `true` 时启用本地长期记忆。 |
| `local_memory.max_context_memories` | 否 | 每轮最多注入给模型的相关长期记忆，默认 6 条。 |
| `local_memory.write_diary_on_stop` | 否 | 点击停止后，是否根据本轮完整对话写日记。 |
| `audio_io.mode` | 否 | `local_browser`（默认）使用电脑网页音频；`raspberry_pi` 使用树莓派音频节点。 |
| `audio_io.*` | 树莓派模式必填 | 电脑端音频网关的端口、节点名称、认证 token、TLS 与 PCM 格式。 |

最小可用配置示意（请自行填入新生成的密钥，示例不是可用凭据）：

```json
{
  "direct_voice": {
    "api_key": "YOUR_DOUBAO_VOICE_API_KEY"
  },
  "ark": {
    "api_key": "YOUR_ARK_API_KEY",
    "model": "YOUR_ENDPOINT_OR_MODEL_ID",
    "thinking": "disabled"
  },
  "local_memory": {
    "enable": true,
    "max_context_memories": 6,
    "write_diary_on_stop": true
  }
}
```

### 树莓派模式的电脑端配置

在电脑 `rtcVoiceClient/config.json` 添加并启用如下配置。`shared_token` 必须是电脑和树莓派独有的高强度随机字符串；不应提交、截图或发送给其他人。

```json
"audio_io": {
  "mode": "raspberry_pi",
  "node_id": "yuanbao-pi",
  "listen_host": "0.0.0.0",
  "listen_port": 8765,
  "shared_token": "请替换为随机字符串",
  "tls": {
    "enabled": false,
    "cert_file": "certs/server.crt",
    "key_file": "certs/server.key"
  },
  "capture": {"sample_rate": 16000, "channels": 1, "sample_width": 2, "frame_ms": 40},
  "playback": {"sample_rate": 24000, "channels": 1, "sample_width": 2},
  "pause_capture_during_playback": true
}
```

- 首次仅在可信家庭/办公局域网调试时可暂用 `tls.enabled: false`，树莓派地址使用 `ws://电脑IP:8765`。
- 长期使用或不完全可信网络必须开启 TLS，树莓派地址改为 `wss://电脑主机名或IP:8765`，并让树莓派信任签发该证书的 CA。
- Windows 防火墙只开放 TCP `8765` 的专用网络入站规则，最好限制来源为树莓派 IP。现有网页控制服务仍只监听电脑 `127.0.0.1`，不会暴露到局域网。
- 保持 `pause_capture_during_playback: true` 作为首版默认值：圆宝播放时暂停树莓派上传麦克风，降低扬声器回声被识别成用户发言的概率。

### 保留但不参与当前直连实时通话的配置

| 配置段 | 当前作用 |
| --- | --- |
| `volcano`、`rtc_agent`、`agentconfig` | 旧版 RTC AI Agent 路径使用；`DirectVoiceClient.py` 不创建 RTC 房间，也不读取它们来进行实时通话。 |
| `memory` | 旧版 VikingDB/火山记忆配置。当前 `memory.Enable=false`，且本机直连程序使用 `local_memory`，不会依赖它。 |
| `llm` | 不影响实时回复。若完整填写 OpenAI 兼容接口的 `api_key`、`model`、`base_url`，程序会在停止会话后优先用它生成日记；未填写时自动用方舟生成日记。 |

## 2. 当前功能

### 实时语音对话

数据路径为：

```text
本机模式：

```text
电脑浏览器麦克风 → 本机 Flask/Python → 豆包 ASR → 方舟对话模型 → 豆包 TTS → 电脑浏览器播放
```

树莓派模式：

```text
树莓派麦克风 → 树莓派音频节点 → WebSocket（局域网）→ 电脑 Python
→ 豆包 ASR → 方舟对话模型 → 豆包 TTS → WebSocket → 树莓派扬声器
```
```

- API Key 不会发送到网页或树莓派；它们只保存在电脑 `config.json`。
- 语音识别使用双向流式 ASR，语音合成使用流式 TTS。
- 方舟回答使用流式文本和分句播报，尽量缩短“开始回复”前的等待。
- 关闭 `thinking` 是当前低延迟配置：保留正常回答能力，但不要求模型输出深度推理过程。
- 启动后会显示并朗读 `welcome_message`。

### 识别分句与防重复回复

- ASR 服务会在同一 WebSocket 连接中不断返回累计识别文本。
- 程序只提交 `utterances` 中 `definite=true` 的最终分句，并按时间戳和文本去重。
- 因此第二句话不会再携带第一句话，模型不会反复回答上一轮问题。
- ASR 以静音判定一句话结束；自然停顿约一秒后再开始下一句，可获得更稳定的识别和轮次切分。

### 本地长期记忆

- 记忆文件：`rtcVoiceClient/MEMORY.md`。
- 只保存可复用的陈述事实，不保存“你知道我喜欢什么吗？”这类问句。
- 已支持的典型表达包括：
  - “我喜欢看书” → “主人喜欢看书”
  - “我最近在看哈姆雷特” → “主人最近在看哈姆雷特”
  - “我叫……” / “我的爱好是……” / “我不喜欢……”
- 新记忆保存成功后，页面会显示“已记住：……”。
- 后续回答前，程序按关键词和字词重合度从 `MEMORY.md` 取最多 6 条相关记忆注入提示词。
- “清空对话”只清除当前短期会话，不删除长期记忆文件。

### 对话记录与日记

- 每轮完整问答会追加到 `rtcVoiceClient/conversation_history.md`。
- 点击“停止 AI 助手”后，本轮问答会被汇总为日记并写入 `rtcVoiceClient/conversation_summary.txt`。
- 若配置了完整的旧 `llm` OpenAI 兼容接口，会优先使用其中的 `messages` 提示词写日记；否则由方舟完成该步骤。

## 3. 当前工作 README

### 文件入口

| 文件 | 说明 |
| --- | --- |
| `rtcVoiceClient/DirectVoiceClient.py` | 当前应运行的本机直连主程序。 |
| `rtcVoiceClient/pi_audio_gateway.py` | 电脑端树莓派音频网关；只在 `audio_io.mode=raspberry_pi` 时启动。 |
| `rtcVoiceClient/pi_node/pi_audio_node.py` | 部署在树莓派的轻量音频节点，只采集和播放 PCM。 |
| `rtcVoiceClient/pi_node/config.example.json` | 树莓派节点配置模板，不含 token。 |
| `rtcVoiceClient/pi_node/rtcvoice-audio-node.service` | 树莓派 systemd 自启服务模板。 |
| `rtcVoiceClient/config.json` | 本机私密配置，不应分享。 |
| `rtcVoiceClient/config.example.json` | 无密钥的配置模板。 |
| `rtcVoiceClient/MEMORY.md` | 自动生成的长期记忆。 |
| `rtcVoiceClient/conversation_history.md` | 自动生成的问答记录。 |
| `rtcVoiceClient/conversation_summary.txt` | 自动生成的会话日记。 |
| `rtcVoiceClient/RTCVoiceClient_3.3.py` | 旧 RTC AI Agent 程序，不是当前直连运行入口。 |

### 启动

在 PowerShell 中运行：

```powershell
cd C:\Users\Meanieee\Desktop\RTC\RTCVoice_v1.0\rtcVoiceClient
python .\DirectVoiceClient.py
```

每次修改 Python 或配置后，请完全退出旧窗口后重新执行上述命令；仅刷新网页不会加载 Python 的新代码或新配置。

### 树莓派音频节点部署

树莓派不运行 `DirectVoiceClient.py`，只运行 `pi_audio_node.py`。以下示例按 Raspberry Pi OS 与 USB 麦克风/扬声器编写；使用 I2S HAT 时先在系统中将其设为默认输入/输出设备。

1. 将 `rtcVoiceClient/pi_node/` 整个目录复制到树莓派，例如 `/opt/rtcvoice/pi_node/`。
2. 在树莓派中确认设备：`arecord -l`、`aplay -l`；桌面版可用 `wpctl status` 选择默认输入/输出。
3. 从模板创建 `config.json`，填写电脑局域网地址、设备名称和采样率：

   ```bash
   cd /opt/rtcvoice/pi_node
   cp config.example.json config.json
   ```

4. 安装音频依赖并建立虚拟环境。Bookworm 系统建议保留 apt 提供的 PyAudio：

   ```bash
   sudo apt update
   sudo apt install -y python3-pyaudio python3-venv portaudio19-dev
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```

5. 仅在树莓派本机创建 token 环境文件 `/etc/rtcvoice-audio-node.env`：

   ```text
   RTCVOICE_NODE_TOKEN=与电脑audio_io.shared_token完全相同的随机字符串
   ```

6. 让树莓派运行用户能够读取 token 文件（systemd 仍可正常读取）：

   ```bash
   sudo chown "$USER":"$USER" /etc/rtcvoice-audio-node.env
   sudo chmod 600 /etc/rtcvoice-audio-node.env
   ```

7. 手动验证连接：

   ```bash
   set -a
   . /etc/rtcvoice-audio-node.env
   set +a
   .venv/bin/python pi_audio_node.py --config config.json
   ```

   电脑网页应显示“树莓派音频节点已连接”。此时点击电脑网页的“启动AI助手”，树莓派才开始采集；点击“停止AI助手”，树莓派停止采集。

8. 验证无误后，按实际用户和路径修改 `rtcvoice-audio-node.service` 中的 `User`、`WorkingDirectory`、`ExecStart`，再安装为 systemd 服务：

   ```bash
   sudo cp rtcvoice-audio-node.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now rtcvoice-audio-node
   sudo systemctl status rtcvoice-audio-node
   ```

### 电脑网页在树莓派模式中的行为

网页界面、字幕、开始/停止、清空对话和记忆显示均保留在电脑端。切换到树莓派模式后，网页不会申请电脑麦克风权限，也不会在电脑扬声器播放 TTS；顶部状态会显示树莓派节点的连接与采集状态。

### 验证清单

1. 启动后确认有欢迎语、状态显示“ASR 已连接，正在聆听”。
2. 说“我最近在看哈姆雷特。”，说完后自然停顿约一秒。
3. 页面应出现“已记住：主人最近在看哈姆雷特”。
4. 打开 `MEMORY.md`，应新增一条同含义记录。
5. 再说“你知道我最近在看什么书吗？”，圆宝应能基于该记忆回答。
6. 连续说两句话时，第二条用户消息不应包含第一句；若仍出现，先确认已经退出并重启了主程序。

### 已知边界

- 不提供 RTC 房间、多端加入或远端用户互通能力；树莓派模式是“单个受认证的音频外设”，并非 RTC 多端通话。
- 电脑和树莓派之间的 PCM 协议固定为：上传 16 kHz / 16-bit / 单声道，下载 24 kHz / 16-bit / 单声道。树莓派节点默认以硬件更常支持的 48 kHz 打开声卡，再在节点内转换协议 PCM；`device_capture_sample_rate`、`device_playback_sample_rate` 应使用设备支持、且分别为 16 kHz / 24 kHz 整数倍的采样率。
- 第一版采用半双工回声保护。圆宝播放期间，树莓派仍会向 ASR 连续上传静音 PCM 保活，避免流式 ASR 因连续约 8 秒未收到音频包而关闭会话；若必须支持圆宝说话时的自然插话，需要另行引入 WebRTC 或系统级回声消除。
- 长期记忆是规则抽取的本地 Markdown 文件，不是 VikingDB 的语义检索服务。它优先保证可见、可编辑和不记录问句。
- 本地记忆依赖识别结果最终定稿；说话未结束就立刻关闭窗口，仍可能来不及形成完整对话记录。点击页面“停止”已会尝试保存等待定稿的事实。
- 密钥一旦发到聊天、截图或代码仓库，应立刻在服务控制台撤销并更换。

## 维护状态

当前主路径已经完成：API Key 直连 ASR/TTS、方舟流式对话、欢迎语、低延迟思考关闭、本地长期记忆、对话记录、停止后日记，以及 ASR 累计文本导致的重复回答修复。
