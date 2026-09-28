#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 MEMORY.md 中的真实记忆生成日记
"""

import os
import sys
import re
import importlib.util
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    "rtcvoice_client",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "RTCVoiceClient_3.3.py")
)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def read_memory_entries(memory_file: str):
    """读取 MEMORY.md，返回 (timestamp, content) 列表"""
    entries = []
    if not os.path.exists(memory_file):
        return entries

    with open(memory_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # 匹配: - [2026-06-17 16:53] [火山记忆] 内容
            match = re.match(r'- \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] \[火山记忆\] (.+)', line)
            if match:
                entries.append((match.group(1), match.group(2)))
    return entries


def generate_from_memory():
    memory_file = client.get_basepath_fullfilename("MEMORY.md")
    entries = read_memory_entries(memory_file)

    if not entries:
        print("MEMORY.md 为空，没有可生成的记忆。")
        return

    print(f"从 MEMORY.md 读取到 {len(entries)} 条记忆")

    # 构造对话文本（把记忆当作用户的陈述）
    conversation_lines = []
    for ts, content in entries:
        conversation_lines.append(f"[{ts}] 主人说：{content}")

    conversation_text = "\n".join(conversation_lines)

    textllm = client.LLMClient(client.LLM_CONFIG)

    # 1. 生成 conversation_summary.txt（用 config.json 里的日记 prompt）
    print("\n正在生成日记总结...")
    summary_message = client.LLM_CONFIG.get("messages")
    user_prompt = f"请根据以下与主人的互动碎片，写一篇第一人称日记：\n\n{conversation_text}"
    summary = textllm._call_openai_compatible(summary_message, user_prompt)

    summary_file = client.get_basepath_fullfilename("conversation_summary.txt")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(summary_file, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}]\n日记: {summary if summary else '无'}\n{'-'*50}\n")
    print(f"日记总结已保存到: {summary_file}")

    # 2. 生成 conversation_list.txt（按时间整理 + 情绪评价）
    print("\n正在生成对话列表...")
    list_message = (
        "请将输入的内容整理成以下格式："
        "每个时间点下描述在该时刻与主人发生的一切互动行为或事件。"
        "允许不同时间点重复相同的事件描述。"
        "需要添加带情绪的评价。"
    )
    list_content = textllm._call_openai_compatible(list_message, conversation_text)

    list_file = client.get_basepath_fullfilename("conversation_list.txt")
    date_str = datetime.now().strftime("%Y-%m-%d")
    with open(list_file, 'a', encoding='utf-8') as f:
        f.write(f"[{date_str}]日记: \n{list_content if list_content else '无'}\n{'-'*50}\n")
    print(f"对话列表已保存到: {list_file}")


if __name__ == "__main__":
    generate_from_memory()
    print("\n基于真实记忆的日记生成完成。")
