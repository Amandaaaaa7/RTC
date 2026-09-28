#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器人智能体RTC语音聊天Demo（文件路径基于脚本所在目录，使用火山引擎）
"""

import json
import os
import sys
import time
import uuid
import tempfile
import shutil
import threading
import http.server
import socketserver
import requests
import webview
import hashlib
import hmac
import struct
import base64
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
# import jieba
# from rank_bm25 import BM25Okapi
# ==================== 路径辅助函数 ====================
def get_basepath_fullfilename(filename: str) -> str:
    """返回脚本所在目录下文件的绝对路径"""
    if getattr(sys, 'frozen', False):
       base = sys._MEIPASS 
    else:
       base = os.path.dirname(__file__) # os.path.abspath(__file__)
    return os.path.join(base, filename)
def load_config():
    default_config = {
        "volcano": {
            "access_key": "",
            "secret_key": "",
            "app_id": "",
            "app_key": "",
            "region": "cn-north-1",
            "service": "rtc"
        },
        "scene": {
            "id": "Custom",
            "welcome_message": "你好！"
        }
    }
    config_path = get_basepath_fullfilename("config.json")
    if not os.path.exists(config_path):
        example_path = get_basepath_fullfilename("config.example.json")
        if os.path.exists(example_path):
            shutil.copy2(example_path, config_path)
        else:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
        print(f"创建默认配置文件: {config_path}")
        print("请编辑配置文件，填写 RTC、ASR、TTS 和方舟推理接入点凭证")
        with open(config_path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    
    try:
        with open(config_path, 'r', encoding='utf-8-sig') as f:
            config = json.load(f)
        print(f"加载配置文件: {config_path}")
        return config
    except Exception as e:
        print(f"加载配置文件失败: {e}，使用默认配置")
        return default_config
CONFIG = load_config()
# ==================== 配置区域 ====================
# 火山引擎账户配置
VOLCANO_CONFIG = CONFIG.get("volcano", {})
SCENE_CONFIG = CONFIG.get("scene", {"id": "Custom", "welcome_message": "你好！"})
LLM_CONFIG = CONFIG.get("llm", {})
RTC_AGENT_CONFIG = CONFIG.get("rtc_agent", {})
MEMORY_CONFIG = CONFIG.get("memory", {"Enable": False, "ProviderParams": {"filter": {}}})
AGENT_CONFIG = CONFIG.get("agentconfig", {})
# 记忆文件路径（使用绝对路径）
MEMORY_FILE = get_basepath_fullfilename("MEMORY.md")
SOUL_FILE = get_basepath_fullfilename("SOUL.md")
AGENTS_FILE = get_basepath_fullfilename("AGENTS.md")
RTC_SDK_PATH = get_basepath_fullfilename("Web_4.68.1.min.js")

# 音频配置
RATE = 16000
CHANNELS = 1
CHUNK = 1600
User_id = f"user_A"  #{timestamp}_{uuid.uuid4().hex[:8]}
Agent_id = f"agent_A" #{timestamp}
Room_id = f"room_A"  #{timestamp}_{uuid.uuid4().hex[:8]}
Task_id = f"task_A"  #{timestamp}

# 记忆库配置
CONVERSATION_ID = "conversation_001"

# 标题
TITLE = "机器人智能体RTC语音聊天Demo";
# ==================== 火山引擎签名工具 ====================
def hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def sha256_hash(msg: str) -> str:
    return hashlib.sha256(msg.encode('utf-8')).hexdigest()

def get_canonical_request(method: str, uri: str, query: str, headers: Dict, body: str) -> str:
    canonical_uri = uri if uri else '/'
    canonical_querystring = query
    signed_headers_list = ['content-type', 'host', 'x-date']
    signed_headers = ';'.join(signed_headers_list)
    canonical_headers = ''
    for header in signed_headers_list:
        if header == 'content-type':
            canonical_headers += f'content-type:{headers["Content-Type"]}\n'
        elif header == 'host':
            canonical_headers += f'host:{headers["Host"]}\n'
        elif header == 'x-date':
            canonical_headers += f'x-date:{headers["X-Date"]}\n'
    payload_hash = sha256_hash(body)
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    return canonical_request

def build_volcano_signature_v4(
    method: str, url: str, headers: Dict[str, str], body: str,
    access_key: str, secret_key: str, region: str, service: str
) -> Dict[str, str]:
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    host = parsed_url.netloc
    path = parsed_url.path if parsed_url.path else '/'
    query = parsed_url.query
    canonical_request = get_canonical_request(method, path, query, headers, body)
    amz_date = headers['X-Date']
    date_stamp = amz_date[:8]
    credential_scope = f"{date_stamp}/{region}/{service}/request"
    hashed_canonical_request = sha256_hash(canonical_request)
    string_to_sign = f"HMAC-SHA256\n{amz_date}\n{credential_scope}\n{hashed_canonical_request}"
    def sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
    k_date = sign(secret_key.encode('utf-8'), date_stamp)
    k_region = sign(k_date, region)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    authorization = f"HMAC-SHA256 Credential={access_key}/{credential_scope}, SignedHeaders=content-type;host;x-date, Signature={signature}"
    return {'Authorization': authorization}

def call_rtc_api(action: str, version: str, body: Dict[str, Any]) -> Dict[str, Any]:
    method = "POST"
    endpoint = "https://rtc.volcengineapi.com"
    query_params = {"Action": action, "Version": version}
    query_string = '&'.join([f"{k}={v}" for k, v in query_params.items()])
    url = f"{endpoint}/?{query_string}"
    body_json = json.dumps(body, ensure_ascii=False)
    now = datetime.now(timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    headers = {
        'Content-Type': 'application/json',
        'Host': endpoint.replace('https://', ''),
        'X-Date': amz_date,
    }
    auth_headers = build_volcano_signature_v4(
        method, url, headers, body_json,
        VOLCANO_CONFIG["access_key"], VOLCANO_CONFIG["secret_key"],
        VOLCANO_CONFIG["region"], VOLCANO_CONFIG["service"]
    )
    headers.update(auth_headers)
    print(f"\n请求信息: URL={url}, Action={action}, Body={body_json[:800]}...")
    try:
        response = requests.post(url, headers=headers, data=body_json.encode('utf-8'), timeout=30)
        print(f"   Response Status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(f"   Response: {json.dumps(result, ensure_ascii=False)[:800]}...")
            return result
        else:
            print(f"   Error Response: {response.text}")
            return {"error": response.text, "status_code": response.status_code}
    except Exception as e:
        print(f"API调用异常: {e}")
        return {"error": str(e)}

def send_to_long_memory_api(user_text: str): # 火山记忆库
    """发送对话记录到记忆库 API"""
    if not MEMORY_CONFIG["Enable"]:
        return
    now_ts = int(time.time() * 1000)
    api_key = MEMORY_CONFIG["api_key"]
    headers = {
        "Authorization": f"Bearer { api_key }",
        "Content-Type": "application/json"
    }
    data = {
             "collection_name": MEMORY_CONFIG['ProviderParams']['collection_name'], #需跟火山的记忆库名称一致
             "project_name": "default", #默认
             "conversation_id": CONVERSATION_ID, # 获取Context(检索)时也需要用到
             "messages":[
                {
                   "role": "user",
                   "role_name": "user",
                   "role_id": User_id,
                   "content": user_text, #使用总结更节省记忆库资源
                   "time": now_ts
                },
                # {
                #    "role": "assistant",
                #    "role_name": "assistant",
                #    "role_id": "assistant1",
                #    "content": "",
                #    "time": now_ts
                # }
             ],
             "extract_trigger": {
               "token_count": 1000, # 触发长期记忆抽取的token阈值，默认1000，最大不超过50000
               "wait_timeout": 86400, # 抽取等待超时时间（秒），同一对话未达阈值时超时触发抽取，默认86400秒（1天），最大不超过7天
               "message_count": 1 # 设置为1表示每次都触发长期记忆  #火山Help：当同一个对话ID（conversation_id）：触发长期记忆抽取的消息条数阈值,默认50，最大不超过500
             }
           }
    
    try:
        response = requests.post("https://api-knowledgebase.mlp.cn-beijing.volces.com/api/memory/session/streaming_write", headers=headers, json=data, timeout=5)
        if response.status_code == 200:
            print(f"对话已写入记忆库: {user_text[:800]}...")
        else:
            #json.loads(response.content.decode('utf-8'))["message"]
            print(f"记忆库写入失败: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"记忆库请求异常: {e}")

def get_event_memory_api(user_text: str) -> List[str]:  # 检索事件记忆，返回summary数组
    if not MEMORY_CONFIG["Enable"]:
        return []
    
    api_key = MEMORY_CONFIG["api_key"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "collection_name": MEMORY_CONFIG['ProviderParams']['collection_name'],
        "project_name": "default",
        "query": user_text,
        "filter": {
            "user_id": User_id,
            "memory_type": MEMORY_CONFIG['ProviderParams']['filter']['memory_type'],
        },
        "limit": 100,
        "time_decay_config": {
            "weight": 0.5,
            "no_decay_period": 1
        }
    }
    
    try:
        response = requests.post(
            "https://api-knowledgebase.mlp.cn-beijing.volces.com/api/memory/event/search",
            headers=headers,
            json=data,
            timeout=5
        )
        
        if response.status_code == 200:
            response_data = json.loads(response.content.decode('utf-8'))
            count = response_data['data']['count']
            result_list = response_data['data']['result_list']
            
            # 提取 summary 数组
            summary_list = []
            for idx, item in enumerate(result_list[:count]):
                summary = item['memory_info'].get('summary', '')
                if summary:  # 只添加非空的 summary
                    summary_list.append(summary)
                print(f"      {idx+1}: {summary}")
            
            print(f"Viking事件共{count}条，提取到{len(summary_list)}条有效summary")
            return summary_list
        else:
            print(f"检索记忆库失败: {response.status_code} - {response.text}")
            return []
            
    except Exception as e:
        print(f"记忆库请求异常: {e}")
        return []
# def get_event_memory_api(user_text: str): # 检索事件记忆
#     if not MEMORY_CONFIG["Enable"]:
#         return
#     api_key = MEMORY_CONFIG["api_key"]
#     headers = {
#         "Authorization": f"Bearer { api_key }",
#         "Content-Type": "application/json"
#     }
#     data = {
#              "collection_name": MEMORY_CONFIG['ProviderParams']['collection_name'], #需跟火山的记忆库名称一致
#              "project_name": "default", #默认
#              "query": user_text, # 检索关键词：天气、性格、喜好 等
#              "filter": 
#              {
#                 "user_id": User_id,
#                 "memory_type": MEMORY_CONFIG['ProviderParams']['filter']['memory_type'],                  
#              },
#              "limit": 100, # limit是分页查询参数，含义是本次查询请求最多返回的结果条数
#              "time_decay_config":{
#                "weight":0.5, # 权重
#                "no_decay_period":1 #在设置的时长范围内，所有记忆的时间权重相同，不会随时间推移而衰减；超出该时长的记忆才会按照time_decay_config.weight设置的权重系数进行时间衰减计算
#              }   
#            }
    
#     try:
#         response = requests.post("https://api-knowledgebase.mlp.cn-beijing.volces.com/api/memory/event/search", headers=headers, json=data, timeout=5)
#         if response.status_code == 200:
#             count = json.loads(response.content.decode('utf-8'))['data']['count']
#             result_list = json.loads(response.content.decode('utf-8'))['data']['result_list']  # json.dumps(json.loads(response.content.decode('utf-8'))['data']['result_list'])
#             print(f"Viking事件共{count}条")
#             for idx, item in enumerate(result_list[:count]):
#                 original_messages = item['memory_info']['summary'] # item['memory_info']['original_messages']
#                 print(f"      {idx+1}: {original_messages}")
#         else:
#             print(f"检索记忆库失败: {response.status_code} - {response.text}")
#     except Exception as e:
#         print(f"记忆库请求异常: {e}")

def get_profile_memory_api(user_text: str): # 检索画像记忆
    if not MEMORY_CONFIG["Enable"]:
        return
    api_key = MEMORY_CONFIG["api_key"]
    headers = {
        "Authorization": f"Bearer { api_key }",
        "Content-Type": "application/json"
    }
    data = {
             "collection_name": MEMORY_CONFIG['ProviderParams']['collection_name'], #需跟火山的记忆库名称一致
             "project_name": "default", #默认
             "query": user_text, # 检索关键词：天气、性格、喜好 等
             "filter": 
             {
                "user_id": User_id,
                "memory_type":  ["profile_v1"],                  
             },
             "limit": 100, # limit是分页查询参数，含义是本次查询请求最多返回的结果条数
           }
  
    try:
        response = requests.post("https://api-knowledgebase.mlp.cn-beijing.volces.com/api/memory/profile/search", headers=headers, json=data, timeout=5)
        if response.status_code == 200:
            count = json.loads(response.content.decode('utf-8'))['data']['count']
            result_list = json.loads(response.content.decode('utf-8'))['data']['result_list']  # json.dumps(json.loads(response.content.decode('utf-8'))['data']['result_list'])
            print(f"Viking画像共{count}条")
            for idx, item in enumerate(result_list[:count]):
                original_messages = item['memory_info']['user_profile']
                print(f"      {idx+1}: {original_messages}")
        else:
            print(f"检索记忆库失败: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"记忆库请求异常: {e}")
# ==================== AccessToken 生成 ====================
import AccessToken
from AccessToken import AccessToken as RTC_Token, PrivSubscribeStream, PrivPublishStream

def generate_rtc_token(app_id: str, app_key: str, room_id: str, user_id: str) -> str:
    token_obj = RTC_Token(app_id, app_key, room_id, user_id)
    token_obj.add_privilege(PrivSubscribeStream, 0)
    token_obj.add_privilege(PrivPublishStream, int(time.time()) + 3600)
    token_obj.expire_time(int(time.time()) + 24 * 3600)
    return token_obj.serialize()

# ==================== 记忆系统 ====================
def load_markdown_file(filepath: str) -> str:
    """加载 markdown 文件（已传入绝对路径）"""
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                print(f"加载 {os.path.basename(filepath)}: {len(content)} 字符")
                return content
        except Exception as e:
            print(f"读取 {filepath} 失败: {e}")
    else:
        print(f"未找到 {filepath}")
    return ""

def build_system_prompt(max_chars: int = 8000,query: str = "") -> List[str]:
    sections = []
    soul = load_markdown_file(SOUL_FILE)
    if soul:
        sections.append(f"【核心人格】\n{soul}")
    agents = load_markdown_file(AGENTS_FILE)
    if agents:
        sections.append(f"【智能体能力】\n{agents}")

    sections.append(f"【声纹识别】\n请根据对话中系统自动识别的标签判断身份。\
    如果是 user_wenwen，请用活泼的语气；如果是 user_shijie，\
    请用简练的语气；如果没有身份标签，则使用通用、中立的语气。")

    #memory = load_markdown_file(MEMORY_FILE)
    #memory="";
 
    # entries = load_memory_entries()
    # relevant = retrieve_relevant_memories(query, entries, top_k=8)

    # if relevant:
    #     sections.append(f"【主人喜好等相关记忆】\n" + "\n".join(relevant))

    if not sections:
        return ["你是一个全能的超级助手"]
    full_prompt = "\n\n".join(sections)
    if len(full_prompt) > max_chars:
        print(f"Prompt 超长 ({len(full_prompt)} 字符)，截断记忆部分")
        core = "\n\n".join(sections[:10])
        return [core[:max_chars]]
    return [full_prompt]
#用于读取Memory 进行BM25 权重计算做准备
def load_memory_entries():
    entries = []
    with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and line.startswith('-'):
                # 提取内容部分（去掉 - [时间] [类别]）
                content = line.split(']', 2)[-1].strip()
                entries.append(content)
    return entries
# 结合问句做权重处理
# def retrieve_relevant_memories(query, entries, top_k=5):
#     tokenized_corpus = [list(     .cut(entry)) for entry in entries]
#     bm25 = BM25Okapi(tokenized_corpus)
#     tokenized_query = list(jieba.cut(query))
#     scores = bm25.get_scores(tokenized_query)
#     # 获取 top_k 索引
#     top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
#     return [entries[i] for i in top_indices]
# ==================== LLM 客户端（用于结束总结）====================
class LLMClient:
    def __init__(self, config: Dict):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "claude-3-5-sonnet-20241022")
        self.max_tokens = config.get("max_tokens", 500)
        self.temperature = config.get("temperature", 0.3)
        raw_url = config.get("base_url", "https://api.anthropic.com")
        self.base_url = raw_url.rstrip('/')
        self.is_anthropic = "anthropic" in self.base_url.lower()
        self.summary_message = config.get("message", "总结对话：")
        if self.api_key:
            print(f"LLM 总结客户端已初始化")

    def _call_anthropic(self, system: str, user: str) -> str:
        url = f"{self.base_url}/v1/messages"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        payload = {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature, "system": system, "messages": [{"role": "user", "content": user}]}
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        content = data.get("content", [])
        if content and len(content) > 0:
            result = content[0].get("text", "").strip()
            return "" if result in ["无", "None", "null", ""] else result
        return ""

    def _call_openai_compatible(self, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if choices and len(choices) > 0:
            result = choices[0].get("message", {}).get("content", "").strip()
            return "" if result in ["无", "None", "null", ""] else result
        return ""

# ==================== 记忆管理器（实时关键词 + 结束总结）====================
class MemoryManager:
    REALTIME_KEYWORDS = ["喜欢", "爱", "讨厌", "不喜欢", "想", "需要", "是", "在", "工作", "职业", "叫", "名字是", "爱好", "擅长", "害怕", "希望", "记住这", "记住这个"]
    def __init__(self, llm_config: Optional[Dict] = None):
        print(f"实时记忆已启用，关键词: {self.REALTIME_KEYWORDS[:5]}...")

    def append_memory(self, content: str, category: str = "记忆") -> bool:
        """保存记忆到本地文件和火山记忆库"""
        if not content or len(content.strip()) < 3:
            return False
        
        try:
            # 格式化记忆内容
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            clean_content = content.replace('\n', ' ').replace('\r', '').strip()
            entry = f"- [{timestamp}] [{category}] {clean_content}\n"

            # 保存到本地 MEMORY.md 文件
            with open(MEMORY_FILE, "a", encoding="utf-8") as f:
                f.write(entry)
            
            # 如果是火山记忆类别，同步到火山记忆库 API
            if category == "火山记忆":
                send_to_long_memory_api(clean_content)
            
            print(f"记忆已保存: {entry[:500]}...")
            return True
            
        except Exception as e:
            print(f"记忆保存失败: {e}")
            return False

    def check_and_save_realtime(self, user_text: str) -> bool:
        if not user_text:
            return False
        matched = None
        for keyword in self.REALTIME_KEYWORDS:
            if keyword in user_text:
                matched = keyword
                break
        if not matched:
            return False
        idx = user_text.find(matched)
        start = 0
        for i in range(idx - 1, -1, -1):
            if user_text[i] in '。！？，；.!?;,':
                start = i + 1
                break
        end = len(user_text)
        for i in range(idx + len(matched), len(user_text)):
            if user_text[i] in '。！？，；.!?;,':
                end = i
                break
        phrase = user_text[start:end].strip()
        if len(phrase) < 5:
            return False
        return self.append_memory(phrase, "火山记忆")
    
    def generate_final_list(self, conversation_history: List[Dict[str, str]]) -> str:
        # lines = get_event_memory_api("")  #从VibkingDB中下载事件记忆
        # conversation_text = "\n".join(lines)
        # "请将输入的对话内容整理成以下格式："\
        # "每个时间点下描述在该时刻与主人发生的一切互动行为或事件。"\
        # "允许不同时间点重复相同的事件描述。"\
        # "需要添加带情绪的评价。"
       

        summary_message = "请将输入的对话内容整理成以下格式："\
                                     "每个时间点下描述在该时刻与主人发生的一切互动行为或事件。"\
                                     "允许不同时间点重复相同的事件描述。"\
                                     "需要添加带情绪的评价。"
    
        lines = []
        textllm = LLMClient(LLM_CONFIG)  # 当前使用deepseek
        for msg in conversation_history:
            # 处理用户、AI的消息
            if msg.get('user'):
                lines.append(f"[{msg.get('time')}] 主人说{msg['user']}")
            if msg.get('ai'):
                lines.append(f"[{msg.get('time')}] 我说{msg['ai']}")
        conversation_text = "\n".join(lines)
        summary = textllm._call_openai_compatible(summary_message, conversation_text)
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d") # %Y-%m-%d %H:%M:%S
            conversation_file = get_basepath_fullfilename("conversation_list.txt")
            # 简洁格式
            file_content = f"[{timestamp}]日记: \n{summary if summary else '无'}\n{'-'*50}\n"
            with open(conversation_file, 'a', encoding='utf-8') as f:
                f.write(file_content)
            print(f"对话记录已追加到: {conversation_file}")
        
        except Exception as e:
            print(f"保存对话记录失败: {e}")
    
        return conversation_text

    def generate_final_summary(self, conversation_history: List[Dict[str, str]]) -> str:
        
        summary_message = LLM_CONFIG.get("messages")
        # 从 conversation_history 构建对话文本
        lines = []
        for msg in conversation_history:
            # 处理用户、AI的消息
            if msg.get('user'):
                lines.append(f"用户: {msg['user']}")
            if msg.get('ai'):
                lines.append(f"AI: {msg['ai']}")

        conversation_text = "\n".join(lines)
    
        # 使用对话历史填充 user_prompt
        user_prompt = f"请总结以下对话：\n\n{conversation_text}"
        textllm = LLMClient(LLM_CONFIG)  # 当前使用deepseek
        summary = textllm._call_openai_compatible(summary_message, user_prompt)
    
        # 合并存储 conversation_text 和 summary 到文件（简洁版）
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conversation_file = get_basepath_fullfilename("conversation_summary.txt")
        
            # 简洁格式
            file_content = f"[{timestamp}]\n日记: {summary if summary else '无'}\n{'-'*50}\n"
           #f"[{timestamp}]\n对话记录:\n{conversation_text}\n总结: {summary if summary else '无'}\n{'-'*50}\n"
        
            with open(conversation_file, 'a', encoding='utf-8') as f:
                f.write(file_content)
        
            print(f"对话记录已追加到: {conversation_file}")
        
        except Exception as e:
            print(f"保存对话记录失败: {e}")
    
        return summary

# ==================== 核心业务类 ====================
class VoiceChatCore:
 
    def __init__(self):
        self.conversation_history = [] #全部历史对话
        self.is_agent_started = False
        self.start_time = None
        self.app_id = VOLCANO_CONFIG.get("app_id", "")
        self.app_key = VOLCANO_CONFIG.get("app_key", "")
        self.welcome_message = SCENE_CONFIG.get("welcome_message", "你好！")
        llm_config = LLM_CONFIG # 如需总结，请在外部设置
        self.memory = MemoryManager(llm_config)
    
    @staticmethod
    def _is_configured(value: Any) -> bool:
        """拒绝将示例占位文字当作真实凭证提交到火山引擎。"""
        return bool(value and str(value).strip() and "填写" not in str(value) and "你的" not in str(value))

    def _missing_rtc_agent_settings(self) -> List[str]:
        asr = RTC_AGENT_CONFIG.get("asr", {})
        tts = RTC_AGENT_CONFIG.get("tts", {})
        llm = RTC_AGENT_CONFIG.get("llm", {})
        required = {
            "volcano.access_key": VOLCANO_CONFIG.get("access_key"),
            "volcano.secret_key": VOLCANO_CONFIG.get("secret_key"),
            "volcano.app_id": self.app_id,
            "volcano.app_key": self.app_key,
            "rtc_agent.asr.app_id": asr.get("app_id"),
            "rtc_agent.asr.access_token": asr.get("access_token"),
            "rtc_agent.tts.app_id": tts.get("app_id"),
            "rtc_agent.tts.access_token": tts.get("access_token"),
            "rtc_agent.llm.endpoint_id": llm.get("endpoint_id"),
        }
        return [name for name, value in required.items() if not self._is_configured(value)]

    def start_agent(self) -> Dict[str, Any]:
        print("启动AI Agent...")
        missing = self._missing_rtc_agent_settings()
        if missing:
            message = "请先填写 config.json: " + ", ".join(missing)
            print(message)
            return {"success": False, "message": message}

        # 每次会话使用新的房间、Agent 和任务 ID，避免重复登录或任务冲突。
        global User_id, Agent_id, Room_id, Task_id
        suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        User_id = RTC_AGENT_CONFIG.get("user_id", "user_A")
        Agent_id = f"{RTC_AGENT_CONFIG.get('agent_id_prefix', 'rtcvoice_agent')}_{suffix}"
        Room_id = f"{RTC_AGENT_CONFIG.get('room_id_prefix', 'rtcvoice_room')}_{suffix}"
        Task_id = f"{RTC_AGENT_CONFIG.get('task_id_prefix', 'rtcvoice_task')}_{suffix}"
        asr = RTC_AGENT_CONFIG.get("asr", {})
        tts = RTC_AGENT_CONFIG.get("tts", {})
        llm = RTC_AGENT_CONFIG.get("llm", {})
        body = {
            "AppId": self.app_id,
            "RoomId": Room_id,
            "TaskId": Task_id,
            "Config": {
                "ASRConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "Mode": "bigmodel",
                        "AppId": asr["app_id"],
                        "AccessToken": asr["access_token"],
                        "ApiResourceId": asr.get("resource_id", "volc.seedasr.sauc.duration"),
                        "StreamMode": asr.get("stream_mode", 2),
                        "VolcanoASRParameters": json.dumps(asr.get("parameters", {}), ensure_ascii=False, separators=(",", ":")),
                    },
                    "VADConfig": {"SilenceTime": asr.get("silence_time", 600)},
                    "InterruptConfig": {}
                },
                "LLMConfig": {
                    "Mode": "ArkV3",
                    "EndPointId": llm["endpoint_id"],
                    "MaxTokens": llm.get("max_tokens", 1024),
                    "Temperature": llm.get("temperature", 0.1),
                    "TopP": llm.get("top_p", 0.3),
                    "SystemMessages": build_system_prompt(),
                    "HistoryLength": llm.get("history_length", 10),
                    "ThinkingType": llm.get("thinking_type", "disabled"),
                },
                "TTSConfig": {
                    "Provider": "volcano_bidirection",
                    "ProviderParams": {
                        "app": {"appid": tts["app_id"], "token": tts["access_token"]},
                        "audio": {
                            "voice_type": tts.get("voice_type", "zh_female_vv_uranus_bigtts"),
                            "speech_rate": tts.get("speech_rate", 0),
                            "loudness_rate": tts.get("loudness_rate", 0),
                        },
                        "ResourceId": tts.get("resource_id", "seed-tts-1.0"),
                    }
                },
                "SubtitleConfig": {"DisableRTSSubtitle": False, "SubtitleMode": RTC_AGENT_CONFIG.get("subtitle_mode", 1)},
                "InterruptMode": 0,
            },
            "AgentConfig": {
                "TargetUserId": [User_id],
                "UserId": Agent_id,
                "WelcomeMessage": self.welcome_message,
                "IdleTimeout": RTC_AGENT_CONFIG.get("idle_timeout", 30),
            }
        }

        if MEMORY_CONFIG.get("Enable", False):
            provider_params = MEMORY_CONFIG.get("ProviderParams", {})
            body["Config"]["MemoryConfig"] = {
                "Enable": True,
                "Provider": MEMORY_CONFIG.get("Provider", "volc"),
                "ProviderParams": {
                    "collection_name": provider_params.get("collection_name", ""),
                    "limit": provider_params.get("limit", 8),
                    "filter": {
                        "user_id": [User_id],
                        "memory_type": provider_params.get("filter", {}).get("memory_type", ["event_v1"]),
                    },
                    "transition_words": "根据您的历史记录：",
                },
            }
        if AGENT_CONFIG.get("Burst"):
            body["AgentConfig"]["Burst"] = AGENT_CONFIG["Burst"]
        if AGENT_CONFIG.get("VoicePrint", {}).get("Mode", 0) == 2:
            body["AgentConfig"]["VoicePrint"] = AGENT_CONFIG["VoicePrint"]

        token = generate_rtc_token(self.app_id, self.app_key, Room_id, User_id)

        result = call_rtc_api("StartVoiceChat", "2025-06-01", body)
        if "ResponseMetadata" in result and result["ResponseMetadata"].get("Error"):
            error = result["ResponseMetadata"]["Error"]
            error_msg = error.get("Message", str(error))
            print(f"启动失败: {error_msg}")
            return {"success": False, "message": error_msg}
        elif result.get("error"):
            error = result["error"]
            error_msg = error.get("Message", str(error)) if isinstance(error, dict) else str(error)
            print(f"启动失败: {error_msg}")
            return {"success": False, "message": error_msg}
        print(f"{result['Result']}")
        self.is_agent_started = True
        self.start_time = time.time()
        self.conversation_history = []
        return {
            "success": True,
            "message": "AI Agent已启动",
            "welcome_message": self.welcome_message,
            "room_id": Room_id,
            "user_id": User_id,
            "token": token
        }

    def stop_agent(self) -> Dict[str, Any]:
        print("停止AI Agent...")
        summary = ""
        if self.conversation_history:
            summary = self.memory.generate_final_summary(self.conversation_history)
            convList = self.memory.generate_final_list(self.conversation_history)
        self.is_agent_started = False

        stopvoicebody = {
               "AppId": self.app_id,
               "RoomId": Room_id,
               "TaskId": Task_id
           }
        result = call_rtc_api("StopVoiceChat", "2025-06-01", stopvoicebody)

        if result.get("error") or result.get("ResponseMetadata", {}).get("Error"):
            error = result.get("error") or result["ResponseMetadata"]["Error"]
            return {"success": False, "message": f"停止 AI Agent 失败: {error}"}
        return {"success": True, "message": "AI Agent已停止"}

    def save_conversation(self, user_text: str, ai_text: str, is_user: bool) -> Dict[str, Any]:
        if user_text or ai_text:
            self.conversation_history.append({
                "user": user_text,
                "ai": ai_text,
                "isuser":is_user,
                "time": datetime.now().strftime("%H:%M:%S")
            })
            if len(self.conversation_history) > 100:
                self.conversation_history = self.conversation_history[-100:]  #获取最后100条
            if user_text:
                self.memory.check_and_save_realtime(user_text)
        return {"success": True, "history_count": len(self.conversation_history)}

    def clear_history(self) -> Dict[str, Any]:
        self.conversation_history = []
        return {"success": True, "message": "本轮对话已清空"}

# ==================== Python 后端 API 封装（供 WebView 调用）====================
class VoiceChatAPI:
    def __init__(self):
        self.core = VoiceChatCore()
        self.current_room_id = ""
        self.current_user_id = ""
        self.current_token = ""

    def get_config(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "app_id": self.core.app_id,
            "scene_id": SCENE_CONFIG["id"],
            "server_available": True,
            "welcome_message": self.core.welcome_message
        }

    def get_scene_config(self, *args, **kwargs) -> Dict[str, Any]:
        return {"success": True, "config": {
            "app_id": self.core.app_id,
            "room_id": "",
            "user_id": "",
            "token": ""
        }}

    def start_agent(self, *args, **kwargs) -> Dict[str, Any]:
        params = kwargs.get('params', {})
        scene_id = params.get('scene_id', SCENE_CONFIG["id"])
        result = self.core.start_agent()
        if result.get("success"):
            self.current_room_id = result.get("room_id", "")
            self.current_user_id = result.get("user_id", "")
            self.current_token = result.get("token", "")
        return result

    def stop_agent(self, *args, **kwargs) -> Dict[str, Any]:
        return self.core.stop_agent()

    def save_conversation(self, *args, **kwargs) -> Dict[str, Any]:
        user_text = args[0].get('user_text', '')
        ai_text = args[0].get('ai_text', '')
        is_user = args[0].get('is_user', '')
        return self.core.save_conversation(user_text, ai_text,is_user)

    def clear_history(self, *args, **kwargs) -> Dict[str, Any]:
        return self.core.clear_history()

    def get_room_info(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "room_id": self.current_room_id,
            "user_id": self.current_user_id,
            "token": self.current_token
        }

# ==================== SDK 文件检查与 HTML 生成 ====================
def validate_sdk_file(filepath: str) -> tuple:
    if not os.path.exists(filepath):
        return False, f"文件不存在: {filepath}"
    try:
        size = os.path.getsize(filepath)
        if size < 1000:
            return False, f"文件太小 ({size} bytes)"
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        if 'VERTC' not in content:
            return False, "文件内容无效（未找到 VERTC 对象）"
        return True, f"有效 ({size} bytes)"
    except Exception as e:
        return False, f"读取失败: {e}"

def get_html_content() -> str:
    sdk_content = ""
    if os.path.exists(RTC_SDK_PATH):
        with open(RTC_SDK_PATH, 'r', encoding='utf-8') as f:
            sdk_content = f.read()
        print(f"读取本地 SDK: {RTC_SDK_PATH}")
    else:
        print(f"SDK不存在: {RTC_SDK_PATH}")

    sdk_tag = f"""
    <script>
        window.__VERTC_LOADING__ = true;
        console.log("开始加载本地 SDK...");
    </script>
    <script>{sdk_content}</script>
    <script>
        window.__VERTC_LOADING__ = false;
        if (typeof VERTC !== 'undefined') {{
            console.log("VERTC 已定义，版本:", VERTC.getSdkVersion());
        }} else {{
            console.error("VERTC 未定义");
        }}
    </script>
    """

    window_title = TITLE
    scene_id = SCENE_CONFIG["id"]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{window_title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }}
        .container {{
            width: 100%;
            height: 100vh;
            background: white;
            border-radius: 24px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow-y: auto;
            display: flex;
            flex-direction: column;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-align: center;
            flex-shrink: 0;
        }}
        .header h1 {{ font-size: 1.5rem; margin-bottom: 8px; }}
        .status {{ font-size: 0.85rem; display: flex; align-items: center; justify-content: center; gap: 8px; }}
        .status-dot {{
            width: 10px; height: 10px; border-radius: 50%;
            background: #94a3b8; transition: all 0.3s;
        }}
        .status-dot.active {{ background: #4ade80; animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.5}} }}
        .server-status {{ font-size: 0.7rem; margin-top: 8px; padding: 4px 8px; background: rgba(0,0,0,0.2); border-radius: 20px; display: inline-block; }}
        .video-area {{
            background: #1e293b;
            height: 180px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        .video-placeholder {{ color: #94a3b8; text-align: center; }}
        .chat-area {{
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            background: #385a7c;
        }}
        .message {{ margin-bottom: 16px; display: flex; animation: fadeIn 0.3s ease; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .message.user {{ justify-content: flex-end; }}
        .message.ai {{ justify-content: flex-start; }}
        .message-content {{
            max-width: 75%;
            padding: 12px 16px;
            border-radius: 18px;
            word-wrap: break-word;
            line-height: 1.5;
        }}
        .user .message-content {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom-right-radius: 4px;
        }}
        .ai .message-content {{
            background: white;
            color: #1e293b;
            border: 1px solid #e2e8f0;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }}
        .message-time {{
            font-size: 0.7rem;
            color: #94a3b8;
            margin-top: 4px;
            text-align: center;
        }}
        .control-area {{
            padding: 20px;
            background: white;
            border-top: 1px solid #e2e8f0;
            display: flex;
            gap: 12px;
            flex-shrink: 0;
        }}
        .btn {{
            flex: 1;
            padding: 12px 20px;
            border: none;
            border-radius: 40px;
            font-size: 0.9rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-primary {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
        .btn-primary:hover:not(:disabled) {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(102,126,234,0.4); }}
        .btn-danger {{ background: #ef4444; color: white; }}
        .btn-danger:hover:not(:disabled) {{ background: #dc2626; }}
        .btn-secondary {{ background: #f1f5f9; color: #475569; }}
        .btn-secondary:hover:not(:disabled) {{ background: #e2e8f0; }}
        .btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .thinking {{ display: inline-flex; gap: 4px; align-items: center; }}
        .thinking span {{
            width: 8px; height: 8px; background: #94a3b8; border-radius: 50%;
            animation: bounce 1.4s infinite;
        }}
        .thinking span:nth-child(2) {{ animation-delay: 0.2s; }}
        .thinking span:nth-child(3) {{ animation-delay: 0.4s; }}
        @keyframes bounce {{ 0%,60%,100%{{transform:translateY(0)}} 30%{{transform:translateY(-10px)}} }}
        .chat-area::-webkit-scrollbar {{ width: 6px; }}
        .chat-area::-webkit-scrollbar-track {{ background: #e2e8f0; border-radius: 3px; }}
        .chat-area::-webkit-scrollbar-thumb {{ background: #94a3b8; border-radius: 3px; }}
        .chat-area::-webkit-scrollbar-thumb:hover {{ background: #667eea; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h3>{window_title}</h3>
            <div class="status">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">初始化中...</span>
            </div>
            <div class="server-status" id="serverStatus">检查服务端...</div>
        </div>
        <div class="video-area" style="display:none">
            <div id="localVideo" class="video-placeholder">等待加入房间...</div>
        </div>
        <div class="chat-area" id="chatArea">
            <div class="message ai">
                <div class="message-content">点击「启动AI助手」开始对话</div>
            </div>
        </div>
        <div class="control-area">
            <button class="btn btn-primary" id="startBtn">启动AI助手</button>
            <button class="btn btn-danger" id="stopBtn" disabled>停止AI助手</button>
            <button class="btn btn-secondary" id="clearBtn">清空对话</button>
        </div>
    </div>
    {sdk_tag}
    <script>
        let rtcEngine = null, isJoined = false, isAgentStarted = false;
        let roomId = "";
        let userId = "";
        let token = "";
        let currentUserId = "";

        const startBtn = document.getElementById('startBtn');
        const stopBtn = document.getElementById('stopBtn');
        const clearBtn = document.getElementById('clearBtn');
        const statusText = document.getElementById('statusText');
        const statusDot = document.getElementById('statusDot');
        const serverStatus = document.getElementById('serverStatus');
        const chatArea = document.getElementById('chatArea');
        
        function tlv2String(buffer) {{
            const view = new DataView(buffer);
            let type = '';
            for (let i = 0; i < 4; i++) {{
                const code = view.getUint8(i);
                if (code === 0) break;
                type += String.fromCharCode(code);
            }}
            const length = view.getUint32(4, false);
            const valueBytes = new Uint8Array(buffer, 8, length);
            const value = new TextDecoder('utf-8').decode(valueBytes);
            return {{ type, value }};
        }}

        function addMessage(text, isUser, isThinking = false) {{
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${{isUser ? 'user' : 'ai'}}`;
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            if (isThinking) {{
                contentDiv.innerHTML = '<div class="thinking"><span></span><span></span><span></span></div>';
            }} else {{
                contentDiv.textContent = text;
            }}
            messageDiv.appendChild(contentDiv);
            const timeDiv = document.createElement('div');
            timeDiv.className = 'message-time';
            timeDiv.textContent = new Date().toLocaleTimeString();
            messageDiv.appendChild(timeDiv);
            chatArea.appendChild(messageDiv);
            messageDiv.scrollIntoView({{ behavior: 'smooth', block: 'end' }});
            return messageDiv;
        }}
        
        function updateStatus(text, isActive = false) {{
            statusText.textContent = text;
            if (isActive) statusDot.classList.add('active');
            else statusDot.classList.remove('active');
        }}
        
        async function callPythonAPI(apiName, params = {{}}) {{
            if (window.pywebview && window.pywebview.api) {{
                try {{
                    return await window.pywebview.api[apiName](params);
                }} catch(e) {{
                    console.error('API调用失败:', e);
                    return {{ success: false, message: e.message }};
                }}
            }}
            return {{ success: false, message: "无法调用Python API" }};
        }}
        let activeMessages = {{}};
        async function initRTC() {{
            try {{
                const config = await callPythonAPI('get_config');
                serverStatus.textContent = config.server_available ? '服务端已连接' : '服务端未连接';
                let vertc = null;
                for (let i = 0; i < 10; i++) {{
                    if (typeof VERTC !== 'undefined') {{
                        vertc = VERTC;
                        break;
                    }}
                    await new Promise(r => setTimeout(r, 10));
                }}
                if (!vertc) {{
                    addMessage("RTC SDK 加载失败（VERTC 未定义）", false);
                    return false;
                }}
                console.log("VERTC 已定义", Object.keys(vertc));
                if (typeof vertc.createEngine === 'function') {{
                    rtcEngine = vertc.createEngine(config.app_id);
                }} else if (vertc.default && typeof vertc.default.createEngine === 'function') {{
                    rtcEngine = vertc.default.createEngine(config.app_id);
                }} else {{
                    addMessage("无法找到 createEngine 方法", false);
                    return false;
                }}
                
                rtcEngine.on(VERTC.events.onRoomBinaryMessageReceived, (event) => {{
                    const {{ userId: speakerId, message }} = event;
                    try {{
                        const {{ type, value }} = tlv2String(message);
                        if (type === 'subv') {{
                            const data = JSON.parse(value);
                            const subtitle = data?.data?.[0];
                            if (!subtitle) return;
                            const {{ text, definite, paragraph }} = subtitle;
                            const isSelf = (subtitle.userId === currentUserId);
                            const isAi = !isSelf;
                            let active = activeMessages[speakerId];
                            if (!active) {{
                                const element = addMessage(text, isSelf);
                                active = {{ element, currentText: text, isUser: isSelf }};
                                activeMessages[speakerId] = active;
                            }} else {{
                                    active.element.querySelector('.message-content').textContent = text;
                                    active.currentText = text;
                            }}

                            const isFinal = (definite || paragraph);
                            if (isFinal) {{
                                 
                                callPythonAPI('save_conversation', {{
                                    user_text: isSelf ? active.currentText : '',
                                    ai_text: isSelf ? '' : active.currentText,
                                    is_user: isSelf
                                }});
                                
                                delete activeMessages[speakerId];
                            }}
                        }}
                    }} catch (err) {{
                        console.error('解析字幕失败', err);
                    }}
                }});
                return true;
            }} catch (error) {{
                console.error('RTC初始化失败:', error);
                addMessage(`RTC初始化失败: ${{error.message}}`, false);
                return false;
            }}
        }}
        
        async function getSceneConfig() {{
            const result = await callPythonAPI('get_scene_config');
            if (result && result.success) {{
                return result.config;
            }}
            return null;
        }}
        
        async function joinRoom() {{
            if (!rtcEngine) await initRTC();
            if (isJoined) return true;
            if (!token) {{
                addMessage("Token 为空，无法加入房间", false);
                return false;
            }}
            try {{
                await rtcEngine.joinRoom(token, roomId, {{ userId: userId }}, {{ isAutoPublish: true, isAutoSubscribeAudio: true }});
                isJoined = true;
                currentUserId = userId;
                updateStatus(`已加入房间`, true);
                addMessage(`已加入房间: ${{roomId}}`, false);
                await rtcEngine.startAudioCapture();
                addMessage("麦克风已启用", false);
                return true;
            }} catch (error) {{
                console.error('加入房间失败:', error);
                addMessage(`加入房间失败: ${{error.message}}`, false);
                return false;
            }}
        }}
        
        async function startAgent() {{
            if (isAgentStarted) return;
            addMessage("正在启动AI助手...", false);
            const result = await callPythonAPI('start_agent', {{ scene_id: '{scene_id}' }});
            if (!result || !result.success) {{
                addMessage(`启动失败: ${{result.message}}`, false);
                return;
            }}
            const roomInfo = await callPythonAPI('get_room_info');
            if (roomInfo && roomInfo.room_id) {{
                roomId = roomInfo.room_id;
                userId = roomInfo.user_id;
                token = roomInfo.token;
            }} else {{
                addMessage("获取房间信息失败", false);
                return;
            }}
            const joined = await joinRoom();
            if (!joined) {{
                addMessage("加入房间失败", false);
                return;
            }}
            isAgentStarted = true;
            startBtn.disabled = true;
            stopBtn.disabled = false;
            updateStatus("AI助手运行中", true);
            addMessage(result.welcome_message, false);
        }}
        
        async function stopAgent() {{
            if (!isAgentStarted) return;
            if (!rtcEngine || !isJoined) return;
            try {{
                await rtcEngine.stopAudioCapture();
                addMessage("麦克风已关闭", false);
            }} catch (e) {{
                addMessage("停止音频采集失败", false);
            }}
            await rtcEngine.unpublishStream(VERTC.MediaType.AUDIO);
            const result = await callPythonAPI('stop_agent');
            if (result && result.success) {{
                isAgentStarted = false;
                startBtn.disabled = false;
                stopBtn.disabled = true;
                updateStatus("已停止", false);
                addMessage("AI助手已停止", false);
            }}
            await rtcEngine.leaveRoom();
            isJoined = false;
            updateStatus("已离开房间", false);
            addMessage("已退出房间", false);
        }}
        
        async function clearChat() {{
            chatArea.innerHTML = '';
            addMessage("对话已清空", false);
            await callPythonAPI('clear_history');
        }}
        
        startBtn.onclick = startAgent;
        stopBtn.onclick = stopAgent;
        clearBtn.onclick = clearChat;
    </script>
</body>
</html>
"""

# ==================== 启动 HTTP 服务器与 WebView ====================
class QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        # 处理 favicon.ico 请求
        if self.path == '/favicon.ico':
            self.send_response(200)
            self.send_header('Content-Type', 'image/x-icon')
            self.end_headers()
            self.wfile.write(b'')  # 返回空内容
            return
        # 其他请求正常处理
        super().do_GET()

def start_http_server(directory: str, port: int):
    os.chdir(directory)
    with socketserver.TCPServer(("", port), QuietHTTPHandler) as httpd:
        httpd.serve_forever()

class VoiceChatApp:
    def __init__(self):
        self.api = VoiceChatAPI()
        self.temp_dir = None
        self.server_thread = None

    def start(self):
        get_event_memory_api("")
        get_profile_memory_api("")
        # 检查SDK文件
        valid, msg = validate_sdk_file(RTC_SDK_PATH)
        if not valid:
            print(f"SDK文件无效: {msg}")
            print("请确保 SDK 存在且有效")
            return
        print(f"SDK文件: {RTC_SDK_PATH} ({msg})")

        # 创建临时目录
        self.temp_dir = tempfile.mkdtemp(prefix="rtc_webview_")
        print(f"临时目录: {self.temp_dir}")

        # 复制 SDK 文件
        dest_sdk = os.path.join(self.temp_dir, "Web_4.68.1_index.min.js")
        shutil.copy2(RTC_SDK_PATH, dest_sdk)

        # 生成 HTML 文件
        html_content = get_html_content()
        html_path = os.path.join(self.temp_dir, "index.html")
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"HTML 已生成: {html_path}")

        # 启动 HTTP 服务器
        port = 9090
        self.server_thread = threading.Thread(
            target=start_http_server,
            args=(self.temp_dir, port),
            daemon=True
        )
        self.server_thread.start()
        time.sleep(1)

        url = f"http://localhost:{port}/index.html"
        print(f"加载页面: {url}")

        webview.create_window(
            title=TITLE,
            url=url,
            width=1000,
            height=800,
            resizable=True,
            js_api=self.api
        )
        webview.start(debug=False)

        print("正在清理临时文件...")
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        print("清理完成")

if __name__ == "__main__":
    app = VoiceChatApp()
    app.start()
