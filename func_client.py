# func_client.py
import os
import json
import requests
from sseclient import SSEClient
from llm_client import chat_with_dify  # blocking 模式调用 Dify
import time


timeout_sec = 2.0          # 可调


# Dify 和播放接口配置
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-8rn27hvk8R3oUj5xATwchcvQ")
DIFY_URL     = "https://api.dify.ai/v1/chat-messages"

# OpenAPI 定义：playmusic
FUNCTION_DEF = {
    "name": "playmusic",
    "description": "播放指定歌曲，例如 用户说“播放音乐 山海”。",
    "parameters": {
        "type": "object",
        "properties": {
            "musicname": {
                "type": "string",
                "description": "歌曲名称"
            }
        },
        "required": ["musicname"]
    }
}

def call_dify(query: str, user_id: str = "default-user"):
    """
    向 Dify 发送 streaming 请求，优先捕获函数调用：
      - 捕获到 playmusic() → 返回 {"label":"function", ...}
      - 连续 1s 没收到流/或流结束  → 返回 {"label":"none"}
    """
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "query":          query,
        "inputs":         {},
        "user":           user_id,
        "response_mode":  "streaming",
        "functions":      [FUNCTION_DEF],
        "function_call":  "auto"
    }

    resp = requests.post(
        DIFY_URL, headers=headers, json=body,
        stream=True, timeout=60
    )
    resp.raise_for_status()

    last_recv = time.time()        # 最近一次真正收到流数据的时间

    for raw in resp.iter_lines(decode_unicode=True):
        # ---- 0) 超时：1 s 内一个字节都没到 ----
        if time.time() - last_recv > timeout_sec:
            print(f"⚠️  {timeout_sec}s 未收到任何流数据 → 认为未触发函数")
            break

        if not raw:                # keep-alive 空行
            continue

        last_recv = time.time()    # 收到数据，刷新计时

        if not raw.startswith("data:"):
            continue

        payload = json.loads(raw[len("data:"):])

        # ---- 1) 捕获 function_call ----
        if payload.get("event") == "agent_thought" and payload.get("tool_input"):
            tool_input = json.loads(payload["tool_input"])
            name, args = next(iter(tool_input.items()))
            print("✅ 捕捉到函数调用")
            return {
                "label":      "function",
                "name":       name,
                "arguments":  args
            }

        # ---- 2) 流自然结束也没捕获 ----
        if payload.get("event") == "message_end":
            print("ℹ️ 流结束未捕获函数")
            break

    # 走到这里代表：超时 / message_end / 其它情况均未触发函数
    return {"label": "none"}


# 测试
if __name__ == "__main__":
    result = call_dify("今天天气不错")
    print(json.dumps(result, ensure_ascii=False, indent=2))
