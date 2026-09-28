# RTCVoiceClient 3.3

> 本机直连 API Key 语音助手（保留旧 RTC 语音聊天 Agent）。

> 推荐启动 `rtcVoiceClient/DirectVoiceClient.py`：它不创建 RTC 房间或 AI Bot，而是使用豆包语音 API Key 直连 ASR/TTS，并使用方舟 API Key 调用大模型。旧 RTC 版仍在 `RTCVoiceClient_3.3.py`。

---

## 本机直连版（推荐）

编辑 `rtcVoiceClient/config.json`：

```json
"direct_voice": {
  "api_key": "豆包语音控制台 API Key",
  "asr_resource_id": "volc.seedasr.sauc.duration",
  "tts_resource_id": "seed-tts-2.0",
  "tts_voice_type": "音色库中的音色 ID"
},
"ark": {
  "api_key": "火山方舟 API Key",
  "model": "已开通的方舟模型 ID"
}
```

豆包语音 API Key 必须具备流式 ASR 2.0 与 TTS 2.0 权限。方舟的 `model` 填已开通模型 ID。所有 Key 只保存在本机 Python 后端，WebView 页面不会收到 Key。

```bash
pip install -r rtcVoiceClient/requirements.txt
python rtcVoiceClient/DirectVoiceClient.py
```

点击“开始说话”后授权麦克风：浏览器采集 16 kHz 单声道 PCM，后端转发至豆包 ASR，ASR 文本触发方舟流式回答，再按句送入 TTS 并在本机播放。

直连版没有 RTC 房间、AI Bot、RTC 控制台 VoiceChat 回调，也不向远端用户发布 AI 音频；但保留本机字幕、语音打断、人格文件与短期对话历史。

### 树莓派作为音频外设

直连版还支持让树莓派只提供麦克风和扬声器，电脑继续运行 ASR、方舟模型、TTS、记忆、日记和网页控制界面。将电脑 `config.json` 中 `audio_io.mode` 改为 `raspberry_pi` 后，电脑会在固定端口启动经 token 认证的音频 WebSocket；树莓派运行 `rtcVoiceClient/pi_node/pi_audio_node.py` 主动连接电脑。

- 豆包/方舟 API Key 只保存在电脑，不复制到树莓派。
- 电脑网页仍用于启动、停止、字幕和状态；树莓派没有控制页面。
- 树莓派模式说明、TLS、Windows 防火墙和 systemd 自启步骤见 [CURRENT_DIRECT_VOICE_README.md](CURRENT_DIRECT_VOICE_README.md)。

---

## 一、拉取最新代码

```bash
git fetch && git status
git pull
```

拉取完成后，当前目录结构应包含：

```
RTCVoice_v1.0/
├── rtcVoiceClient/
│   ├── RTCVoiceClient_3.3.py      # 主程序（当前使用这个）
│   ├── RTCVoiceClient_BM25.py     # 备用/检索版本
│   ├── config.json                # 主配置文件（需自行填写 key）
│   ├── config_client.json         # 旧版客户端配置（3.3 不读取）
│   ├── requirements.txt           # Python 依赖
│   ├── Web_4.68.1.min.js          # Web SDK
│   └── ...
├── rtcVoiceClient_v2/             # 旧/备用版本目录（含人设示例）
│   ├── SOUL.md
│   ├── AGENTS.md
│   ├── MEMORY.md
│   └── RTCVoiceClient_BM25.py
├── rtcVoiceServer/                # 服务器端目录（3.3 客户端不读取）
│   └── Custom.json
└── 使用说明.md                     # 本文档
```

### 版本说明

- **RTCVoiceClient_3.3.py** 是最新版，只读取 `rtcVoiceClient/config.json`。
- `rtcVoiceClient/config_client.json` 和 `rtcVoiceServer/Custom.json` **不被 3.3 版读取**。
- `rtcVoiceClient_v2/` 是另一个分支版本，里面的小圆宝人设文件可以复制到 `rtcVoiceClient/` 使用。

---

## 二、准备 Python 环境

`requirements.txt` 中的 `numpy>=2.2.6` 需要 **Python 3.10 或更高版本**。如果系统默认是 Python 3.9，建议新建一个环境。

### 使用 conda（推荐）

```bash
conda create -n rtcvoice python=3.11 -y
conda activate rtcvoice
pip install -r rtcVoiceClient/requirements.txt
```

### 验证安装

```bash
python -c "import webview, requests, flask; print('OK')"
```

---

## 三、补齐缺失文件

`RTCVoiceClient_3.3.py` 运行时需要同目录下的 `AccessToken.py`。如果 `rtcVoiceClient/` 下没有，从 `rtcVoiceClient_v2/` 复制一份：

```bash
cp rtcVoiceClient_v2/AccessToken.py rtcVoiceClient/AccessToken.py
```

---

## 四、从火山引擎控制台获取 Key

### 1. RTC 的 App ID / App Key

控制台菜单路径：**视频云 → 视频服务 → 实时音视频**

进入后按以下步骤查找：

1. 点击 **应用管理** / **应用列表**
2. 点击具体应用进入 **应用详情**
3. 在详情页复制 `App ID` 和 `App Key`

### 2. Access Key / Secret Key

这是账号级密钥，不在 RTC 产品内。

路径：**控制台右上角头像/账号 → 访问控制 → 密钥管理**（或 **Access Key 管理**）

1. 如果没有密钥，点击 **创建 Access Key**
2. 创建后会得到 `Access Key ID` 和 `Secret Access Key`
3. **Secret Key 只会显示一次，创建时务必保存**

### 3. 记忆库（VikingDB）的 API Key

当前代码使用的记忆库服务是 **VikingDB 向量数据库** 中的 **记忆库**，不是独立的“记忆库 Mem0”产品。

控制台菜单路径：**数据库 → VikingDB 向量数据库 → 记忆库**

直接访问地址示例：

```text
https://console.volcengine.com/vikingdb/region:vikingdb+cn-beijing/home?projectName=default
```

进入 VikingDB 控制台后，选择 **记忆库** 标签。

如果显示 **未开通**，需要先点击开通。开通后：

1. 创建或进入项目（如 `default`）
2. 创建记忆库实例，记录实例名称（如 `memo_test_1p`）
3. 进入 **API Key 管理**，创建并复制 API Key
4. 创建 API Key 时，**必须授权该 Key 可访问你创建的记忆库实例**

> **注意：** 当前代码只调用 VikingDB 的「记忆库」接口（`/api/memory/...`），不调用「向量数据库」的原始接口。因此只需开通 **记忆库**，不需要单独开通 **向量数据库**。

---

## 五、填写 config.json

打开 `rtcVoiceClient/config.json`，填入以下字段。

### 1. 火山引擎 RTC（语音房间与 Agent）

```json
"volcano": {
  "access_key": "你的 Access Key ID",
  "secret_key": "你的 Secret Access Key",
  "app_id": "你的 App ID",
  "app_key": "你的 App Key"
}
```

> 这四个字段为空时，程序无法连接火山 RTC 服务，必须填写。
>
> **安全提醒：** `rtcVoiceClient/config.json` 已加入 `.gitignore`，不会进入 Git 历史。仓库中只保留 `config.example.json` 作为模板。本地填写真实密钥后请勿提交到远程仓库；如已泄露，请立即到对应平台轮换密钥。

### 2. 日记/总结 LLM

默认使用 DeepSeek，但也可以换成其他 OpenAI 兼容接口（如阿里云百炼）。

**DeepSeek 示例：**

```json
"llm": {
  "api_key": "sk-xxx",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com",
  "max_tokens": 8000,
  "temperature": 0.3,
  "stream": false
}
```

**阿里云百炼示例：**

```json
"llm": {
  "api_key": "你的百炼 API Key",
  "model": "qwen3.6-flash",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "max_tokens": 8000,
  "temperature": 0.3,
  "stream": false
}
```

- 阿里云百炼华北2（北京）的 OpenAI 兼容地址就是 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- 模型可以换：`qwen-plus`、`qwen3.6-plus`、`qwen3.6-flash` 等

### 3. 火山记忆库（如启用）

```json
"memory": {
  "api_key": "你的 VikingDB 记忆库 API Key",
  "Enable": true,
  "Provider": "volc",
  "ProviderParams": {
    "collection_name": "你的记忆库实例名称",
    "limit": 8,
    "filter": {
      "memory_type": ["event_v1"]
    }
  },
  "Score": 0.5
}
```

- `collection_name` **必须和你创建的记忆库实例名称一致**，例如 `memo_test_1p`
- 若保持 `"Enable": true`，则必须填写 `api_key`
- 若不需要记忆库功能，可将 `"Enable"` 改为 `false`，此时无需填写该 key

### 4. 声纹 ID（可选）

```json
"agentconfig": {
  "VoicePrint": {
    "IdList": ["", ""]
  }
}
```

- 如需声纹识别，填入对应的声纹 ID；不需要可保持为空。

---

## 六、人设与记忆文件

程序运行时，会通过 `build_system_prompt()` 读取以下三个本地 Markdown 文件来构建大模型人设：

| 文件 | 作用 | 说明 |
|------|------|------|
| `SOUL.md` | 核心人格 | 定义智能体的基础性格 |
| `AGENTS.md` | 智能体能力/指令 | 定义可执行的能力和系统指令 |
| `MEMORY.md` | 本地记忆条目 | 程序自动写入的关键句记忆 |

### 当前状态

- `rtcVoiceClient/` 目录下**默认没有**这三个文件。
- 如果三个文件都不存在，系统会回退到默认提示：

  > “你是一个全能的超级助手”

- 仓库里 `rtcVoiceClient_v2/` 目录下有示例版本（“小圆宝”人设），可复制到 `rtcVoiceClient/` 使用，或自行创建新的人设文件。

### 如何生效

将 `SOUL.md`、`AGENTS.md`、`MEMORY.md` 放到 `rtcVoiceClient/` 目录下，与 `RTCVoiceClient_3.3.py` 同级即可。

---

## 七、记忆保存机制

### 1. 实时关键词记忆

当用户语句中包含以下关键词时：

> 喜欢、爱、讨厌、不喜欢、想、需要、是、在、工作、职业、叫、名字是、爱好、擅长、害怕、希望、记住这、记住这个

程序会提取包含关键词的整句，保存到：

- 本地 `MEMORY.md`（格式：`- [YYYY-MM-DD HH:MM] [火山记忆] 内容`）
- 火山引擎记忆库（云端，需开启并配置 key）

### 2. 火山引擎记忆库

- 集合名：你在 `config.json` 中配置的 `collection_name`
- 检索类型：`event_v1`（事件记忆）
- 对话时会将检索到的 `summary` 数组注入上下文

### 3. 对话历史

- 保存在内存中（`VoiceChatCore.conversation_history`）
- 最多保留最近 100 条
- **停止 Agent 时用于生成日记**，程序退出后丢失

---

## 八、日记生成形式

### 方式一：通过程序正常停止生成（推荐）

点击停止 AI Agent 后，程序会在 `rtcVoiceClient/` 目录下生成两份文件：

#### 1. `conversation_summary.txt`

- 调用 `llm` 配置中的模型
- 使用 `config.json` 中 `llm.messages` 的提示词
- 输出格式：

  ```text
  [YYYY-MM-DD HH:MM:SS]
  日记: <生成的日记内容>
  --------------------------------------------------
  ```

#### 2. `conversation_list.txt`

- 调用同一个 LLM
- 固定提示词：

  > “请将输入的对话内容整理成以下格式：每个时间点下描述在该时刻与主人发生的一切互动行为或事件。允许不同时间点重复相同的事件描述。需要添加带情绪的评价。”

- 输出格式：

  ```text
  [YYYY-MM-DD]日记:
  <整理后的内容>
  --------------------------------------------------
  ```

### 方式二：基于 MEMORY.md 手动生成

如果程序已经退出，完整对话历史丢失，但 `MEMORY.md` 中保存了关键词触发的记忆。可以用脚本基于这些记忆生成日记：

```bash
python rtcVoiceClient/generate_diary.py
```

> 注意：这种方式生成的是基于记忆碎片的日记，不是完整对话版。

---

## 九、运行程序

完成以上配置后，在 `rtcVoiceClient/` 目录下运行：

```bash
python RTCVoiceClient_3.3.py
```

程序会打开一个 Webview 窗口。点击 **“启动 AI Agent”** 开始语音对话，点击 **“停止 AI Agent”** 生成日记。

---

## 十、运行前检查清单

- [ ] 已执行 `git pull` 拉取最新代码
- [ ] 已创建 Python 3.10+ 环境并安装 `requirements.txt` 中的依赖
- [ ] 已确认 `rtcVoiceClient/AccessToken.py` 存在
- [ ] 已从火山引擎控制台获取 RTC 的 `App ID`、`App Key`
- [ ] 已从访问控制获取 `Access Key` / `Secret Key`
- [ ] `config.json` 中火山 RTC 的 4 个 key 已填写
- [ ] `config.json` 中 LLM 的 `api_key`、`model`、`base_url` 已填写
- [ ] （如需要记忆库）已在 VikingDB 控制台的 **记忆库** 页面创建实例并获取 `memory.api_key`
- [ ] （如需要记忆库）`config.json` 中 `memory.api_key` 已填写，`collection_name` 与控制台实例名称一致，且 `"Enable": true`
- [ ] 已准备或复制 `SOUL.md`、`AGENTS.md`、`MEMORY.md` 到 `rtcVoiceClient/` 目录（可选，无则使用默认人设）
- [ ] `Web_4.68.1.min.js` 文件已存在于 `rtcVoiceClient/` 目录

---

## 十一、常见问题

**Q：为什么程序没有使用我的人设？**
A：请检查 `rtcVoiceClient/` 目录下是否存在 `SOUL.md`、`AGENTS.md`、`MEMORY.md`。不存在时系统会回退到默认提示。

**Q：启动时报 `ModuleNotFoundError: No module named 'webview'`？**
A：依赖未安装，或当前 Python 环境不对。请确认在正确的 conda 环境中运行，并已执行 `pip install -r requirements.txt`。

**Q：启动时报 `ModuleNotFoundError: No module named 'AccessToken'`？**
A：请将 `rtcVoiceClient_v2/AccessToken.py` 复制到 `rtcVoiceClient/AccessToken.py`。

**Q：RTC 报 401 `SignatureDoesNotMatch`？**
A：`access_key` / `secret_key` 可能错误，或该 key 没有 RTC 权限。请重新创建 Access Key 并核对。

**Q：记忆库报 400 `api key auth failed`？**
A：可能原因：
1. `memory.api_key` 不是 VikingDB 记忆库的 key
2. API Key 没有授权访问对应的记忆库实例
3. `collection_name` 与控制台中的实例名称不一致

**Q：日记没有生成或生成失败？**
A：请检查 `config.json` 中 `llm.api_key`、`base_url`、`model` 是否填写正确。如果使用阿里云百炼，`base_url` 必须是 `https://dashscope.aliyuncs.com/compatible-mode/v1`。

**Q：需要开通向量数据库吗？**
A：不需要。代码只调用 VikingDB 记忆库的接口（`/api/memory/session/streaming_write`、`/api/memory/event/search`、`/api/memory/profile/search`），不调用向量数据库原始接口，因此只需开通 **记忆库**。

**Q：记忆库费用怎么看？**
A：VikingDB 记忆库的计费以火山引擎 **费用中心** 实际账单为准。
