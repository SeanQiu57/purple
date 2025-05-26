import os
import json
import logging
import requests
from typing import Tuple, Generator
from memorymanager import append_memory
from emotion_controller import EmotionController
from websocket import create_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LLM_DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-5jjjKuPVe3fV675MGtzGR6rD")
DIFY_API_URL = os.getenv("DIFY_API_URL", "https://api.dify.ai/v1/chat-messages")
DEFAULT_TIMEOUT = float(os.getenv("DIFY_TIMEOUT", "10"))  # seconds

# Initialize emotion controller
em_ctrl = EmotionController()



def chat_with_dify(query: str, user_id: str = "default-user",ws=None) -> Tuple[str, str]:
    """Send a single blocking chat request to Dify, integrating memory and prompt.
    Returns `(answer, conversation_id)`.
    Raises `requests.RequestException` on network error.
    """
    # Prepare memory directory
    ws: any = None,
    mem_dir = os.path.join(os.getcwd(), "voicememory")
    os.makedirs(mem_dir, exist_ok=True)
    # Read episodic and short-term memory
    epi_path = os.path.join(mem_dir, "Episodicmemory.txt")
    short_path = os.path.join(mem_dir, "voicememory.txt")
    epi = open(epi_path, "r", encoding="utf-8").read() if os.path.exists(epi_path) else ""
    short = open(short_path, "r", encoding="utf-8").read() if os.path.exists(short_path) else ""
    append_memory(mem_dir, "主人说", query)
    # Build prompt
    prompt = (
        f"===情景记忆===\n{epi}\n$@$"
        f"===短期对话===\n{short}\n$@$"
        f"===用户说===\n{query}\n"
        "请以阿紫口吻回复："
    )

    # Prepare request
    headers = {
        "Authorization": f"Bearer {LLM_DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "query": query,
        "inputs": {"prompt": prompt},
        "user": user_id,
        "response_mode": "blocking",
    }

    # Send request
    try:
        resp = requests.post(
            DIFY_API_URL,
            headers=headers,
            json=body,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("[Dify] Network error", exc_info=True)
        raise

    # Parse response
    data = resp.json()
    answer = data.get("answer", "")
    conv_id = data.get("conversation_id", "")
    logger.info(f"[Dify] ← {resp.status_code} | answer={answer!r} | conv_id={conv_id}")

    # Append to memory
    append_memory(mem_dir, "我（阿紫）说", answer)
    # Emotion feedback
    try:
        ws = create_connection(os.getenv("WS_URL", "ws://localhost:8000/ws"))
        em_ctrl.submit(prompt=answer, ws=ws)
        ws.send(json.dumps({"label": "chat", "reply": answer}, ensure_ascii=False))
        ws.close()
    except Exception:
        logger.debug("WebSocket send failed, skipping emotion feedback.")

    return answer, conv_id


# # 导入 mem0 管理器
# from mem0_manager import Mem0Manager

# # 初始化 mem0 管理器
# mem0_mgr = Mem0Manager(local_mode=True)  # 或者使用 API 密钥: Mem0Manager(api_key="your_api_key", local_mode=False)

# def chat_with_dify(query: str, user_id: str = "default-user", ws=None) -> Tuple[str, str]:
#     """Send a single blocking chat request to Dify, integrating memory and prompt.
#     Returns `(answer, conversation_id)`.
#     Raises `requests.RequestException` on network error.
#     """
#     # 准备记忆目录（兼容性代码）
#     mem_dir = os.path.join(os.getcwd(), "voicememory")
#     os.makedirs(mem_dir, exist_ok=True)
    
#     # 使用 mem0 添加用户查询到记忆
#     mem0_mgr.append_memory(mem_dir, "主人说", query)
    
#     # 使用 mem0 检索相关记忆
#     memories = mem0_mgr.get_formatted_memories(query, user_id=user_id)
#     epi = memories["long_term"]
#     short = memories["short_term"]
    
#     # 构建提示词
#     prompt = (
#         f"===情景记忆===\n{epi}\n$@$"
#         f"===短期对话===\n{short}\n$@$"
#         f"===用户说===\n{query}\n"
#         "请以阿紫口吻回复："
#     )

#     # 准备请求
#     headers = {
#         "Authorization": f"Bearer {LLM_DIFY_API_KEY}",
#         "Content-Type": "application/json",
#     }
#     body = {
#         "query": query,
#         "inputs": {"prompt": prompt},
#         "user": user_id,
#         "response_mode": "blocking",
#     }

#     # 发送请求
#     try:
#         resp = requests.post(
#             DIFY_API_URL,
#             headers=headers,
#             json=body,
#             timeout=DEFAULT_TIMEOUT,
#         )
#         resp.raise_for_status()
#     except Exception as e:
#         logger.error("[Dify] Network error", exc_info=True)
#         raise

#     # 解析响应
#     data = resp.json()
#     answer = data.get("answer", "")
#     conv_id = data.get("conversation_id", "")
#     logger.info(f"[Dify] ← {resp.status_code} | answer={answer!r} | conv_id={conv_id}")

#     # 使用 mem0 添加回复到记忆
#     mem0_mgr.append_memory(mem_dir, "我（阿紫）说", answer)
    
#     # 情感反馈
#     try:
#         ws = create_connection(os.getenv("WS_URL", "ws://localhost:8000/ws"))
#         em_ctrl.submit(prompt=answer, ws=ws)
#         ws.send(json.dumps({"label": "chat", "reply": answer}, ensure_ascii=False))
#         ws.close()
#     except Exception:
#         logger.debug("WebSocket send failed, skipping emotion feedback.")

#     return answer, conv_id

