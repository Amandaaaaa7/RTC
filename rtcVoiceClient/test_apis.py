#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单点测试：验证本地配置，并按需验证记忆库、日记 LLM 的可用性。

注意：不直接调用 StartVoiceChat，避免测试脚本意外创建计费中的 AI 对话任务。
"""

import json
import os
import sys
import time
import uuid
import requests
from datetime import datetime, timezone

# 将脚本所在目录加入路径，确保能导入本地模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 文件名包含点号，不能直接用 import，使用 importlib 加载
import importlib.util
spec = importlib.util.spec_from_file_location(
    "rtcvoice_client",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "RTCVoiceClient_3.3.py")
)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def is_configured(value):
    """示例占位文字不是实际凭证。"""
    return bool(value and str(value).strip() and "填写" not in str(value) and "你的" not in str(value) and "可选" not in str(value))


def test_memory_api():
    """测试 VikingDB 记忆库 API 鉴权"""
    print("\n[测试] 记忆库 API")
    if not client.MEMORY_CONFIG.get("Enable", False):
        print("  记忆库已禁用，跳过")
        return True

    api_key = client.MEMORY_CONFIG.get("api_key", "")
    if not is_configured(api_key):
        print("  [FAIL] memory.api_key 为空")
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "collection_name": client.MEMORY_CONFIG['ProviderParams']['collection_name'],
        "project_name": "default",
        "query": "测试",
        "filter": {
            "user_id": client.User_id,
            "memory_type": client.MEMORY_CONFIG['ProviderParams']['filter']['memory_type'],
        },
        "limit": 1
    }

    try:
        response = requests.post(
            "https://api-knowledgebase.mlp.cn-beijing.volces.com/api/memory/event/search",
            headers=headers,
            json=data,
            timeout=10
        )
        print(f"  状态码: {response.status_code}")
        if response.status_code == 200:
            print("  [OK] 记忆库 API 鉴权通过")
            return True
        else:
            print(f"  [FAIL] 记忆库 API 鉴权失败: {response.text[:500]}")
            return False
    except Exception as e:
        print(f"  [FAIL] 请求异常: {e}")
        return False


def test_llm_api():
    """测试 LLM API 鉴权"""
    print("\n[测试] LLM API")
    api_key = client.LLM_CONFIG.get("api_key", "")
    base_url = client.LLM_CONFIG.get("base_url", "")
    model = client.LLM_CONFIG.get("model", "")

    if not is_configured(api_key):
        print("  本地日记 LLM 未配置，跳过（不影响 RTC AI 对话）")
        return True

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 10
    }

    # 兼容阿里云百炼 / OpenAI 兼容接口
    url = base_url.rstrip("/") + "/chat/completions"

    try:
        response = requests.post(url, headers=headers, json=data, timeout=15)
        print(f"  状态码: {response.status_code}")
        print(f"  请求地址: {url}")
        print(f"  使用模型: {model}")
        if response.status_code == 200:
            print("  [OK] LLM API 鉴权通过")
            return True
        else:
            print(f"  [FAIL] LLM API 鉴权失败: {response.text[:500]}")
            return False
    except Exception as e:
        print(f"  [FAIL] 请求异常: {e}")
        return False


def test_rtc_configuration():
    """预检 StartVoiceChat 所需的四类凭证；不发送会产生任务的 API 请求。"""
    print("\n[测试] RTC AI 对话配置")
    core = client.VoiceChatCore()
    missing = core._missing_rtc_agent_settings()
    if missing:
        print("  [FAIL] 以下字段未填写或仍是示例值: " + ", ".join(missing))
        return False
    print("  [OK] RTC、ASR、TTS、方舟推理接入点配置已填写")
    print("  请启动客户端，并在 RTC 控制台 VoiceChat 事件回调中确认 AI Bot 入房状态")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("RTCVoiceClient API 单点测试")
    print("=" * 50)

    results = {
        "记忆库": test_memory_api(),
        "LLM": test_llm_api(),
        "RTC 配置": test_rtc_configuration(),
    }

    print("\n" + "=" * 50)
    print("测试结果汇总:")
    for name, ok in results.items():
        status = "[OK] 通过" if ok else "[FAIL] 失败"
        print(f"  {name}: {status}")
    print("=" * 50)
