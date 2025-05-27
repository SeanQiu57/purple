"""Camel Desktop‑Pet Command Parser
=================================

This module integrates **CAMEL** function‑calling with the existing Dify
conversation pipeline.  It wraps Volcengine (Doubao) behind an
OpenAI‑compatible endpoint and adds deterministic tool selection for your
three desktop‑pet commands.  When a user utterance matches one of the tool
rules, the agent *must* populate the `tool_calls` field (or `tool_call`) with a valid function call record.  All other
queries are sent to Dify for normal chatbot responses.
"""

from __future__ import annotations
import json
import logging
import os
import sys
import threading
from typing import Any, Dict
from types import SimpleNamespace

from dotenv import load_dotenv
sys.path.insert(0, os.path.dirname(__file__))

from camel.agents import ChatAgent
from camel.models import ModelFactory
from camel.toolkits import FunctionTool
from camel.types import ModelPlatformType

from emotion_controller import EmotionController
from llm_client import chat_with_dify
from memorymanager import append_memory

# 在文件顶部导入mem0管理器
from mem0_manager import Mem0Manager

# 全局变量，但不立即初始化
mem0_mgr: Optional['Mem0Manager'] = None

# Runtime config
load_dotenv()
VOLC_REGION = os.getenv("VOLC_ENGINE_REGION", "cn-beijing")
ENDPOINT_ID = os.getenv("VOLC_ENGINE_ENDPOINT_ID", "ep-20250506152643-v5wqm")
API_KEY = os.getenv("VOLC_ENGINE_API_KEY", "8b4e1f4a-c8eb-46dd-9d69-47e248988770")
BASE_URL = f"https://ark.{VOLC_REGION}.volces.com/api/v3"
llm_funcsametime = True
volc_model = ModelFactory.create(
    model_platform=ModelPlatformType.VOLCANO,
    model_type=ENDPOINT_ID,
    api_key=API_KEY,
    url=BASE_URL,
)

# Logger
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
)

# Tool implementations

def play_music(
    identifier: str = "",
    name: str = "",
    is_exact: str = "fuzzy",
    song_type: str = "single",
) -> str:
    """
    当输入中含有“播放音乐”、“播放专辑”、“播放歌单”这些关键词指令时就调用此函数
    song_type 决定播放内容类型：
      • 单曲播放（song_type='single'，默认）
        触发场景：用户说“播放+歌曲名”、“来一首+歌曲名”或“播放音乐”等。
      • 专辑播放（song_type='album'）
        触发场景：用户说“播放专辑+专辑名”。
      • 歌单播放（song_type='playlist'）
        触发场景：用户说“播放歌单+歌单名”。
    参数说明：
      identifier : str  
        会话唯一 ID，由调用方传入。

      name       : str  
        要播放的单曲/专辑/歌单名称（必要时根据拼音或语境自动纠正 ASR 错误）。

      is_exact   : {"exact","fuzzy"}  
        匹配类型，用户强调“精确搜索”、“原版”时填 'exact'，其余情况一律用 'fuzzy'。

      song_type  : {"single","album","playlist"}  
        播放类型，默认为 'single'；用户说“专辑”或“歌单”时分别用相应值。
    """
    logger.info(
        "[play_music] identifier=%s | name=%s | is_exact=%s | song_type=%s",
        identifier,
        name,
        is_exact,
        song_type,
    )
    return f"播放『{name or identifier}』成功"


def screenshot(
    identifier: str = "",
    camera_type: str = "screen",
) -> str:
    """当用户说截屏 / 屏幕截图 /睁开眼看屏幕时，调用此函数，camera_type='screen'；当用户说打开摄像头拍照 / 自拍 /睁开眼看看我/睁开眼看镜头时，调用此函数，camera_type='camera'"""
    logger.info(
        "[screenshot] identifier=%s | camera_type=%s", identifier, camera_type
    )
    action = "屏幕截图" if camera_type == "screen" else "camera"
    return f"{action}成功"


def recite_poem(
    topic: str = "",
) -> str:
    """当用户让你背诗，吟诗时，调用此函数"""
    logger.info("[recite_poem] topic=%s", topic)
    poem = (
        f"《{topic or '无题'}》\n"
        "苍松傲雪入云端，\n月洒银辉照大川。\n情寄诗中如水韵，\n心随风去到天边。"
    )
    return poem

# Register tools without 'name' kwarg
music_tool = FunctionTool(play_music)
screenshot_tool = FunctionTool(screenshot)
poem_tool = FunctionTool(recite_poem)
TOOL_MAP: Dict[str, Any] = {
    "play_music": play_music,
    "screenshot": screenshot,
    "recite_poem": recite_poem,
}

# System prompt
SYSTEM_PROMPT = (
    ""
)

# Agent
agent = ChatAgent(
    model=volc_model,
    tools=[music_tool, screenshot_tool, poem_tool],
    system_message=SYSTEM_PROMPT,
    memory=None,
)
llm_lock = threading.Lock()
em_ctrl = EmotionController()

# Extract tool calls
def _extract_tool_calls(res: Any):
    if getattr(res, "tool_calls", None):
        tc = res.tool_calls
        return tc if isinstance(tc, list) else [tc]
    if getattr(res, "tool_call", None):
        return [res.tool_call]
    if hasattr(res, "info") and isinstance(res.info, dict) and res.info.get("tool_calls"):
        return res.info["tool_calls"]
    for msg in getattr(res, "messages", []):
        if getattr(msg, "tool_calls", None):
            tc = msg.tool_calls
            return tc if isinstance(tc, list) else [tc]
    # Fallback: parse content JSON
    text = getattr(res, "content", "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
            from types import SimpleNamespace
            return [SimpleNamespace(name=obj["name"], arguments=obj["arguments"])]
    except json.JSONDecodeError:
        pass
    return []


# ────────────────────────────────────────────────────────────────────────────
# Main entry: handle_user_text
# ────────────────────────────────────────────────────────────────────────────

# def handle_user_text(user_text: str, ws, session_id: str = "user123") -> None:
#     chat_with_dify(user_text, user_id=session_id, ws=ws)
#     # CAMEL request
#     with llm_lock:
#         agent.reset()
#         res = agent.step(user_text)
#     calls = _extract_tool_calls(res)
#     # logger.info(f"{res}")
#     logger.info("Extracted tool_calls: %s", calls)

#     if calls:
#         call = calls[0]
#         name = getattr(call, "name", None) or getattr(call, "tool_name", None)
#         args = getattr(call, "arguments", None) or getattr(call, "args", {})
#         if isinstance(args, str):
#             try:
#                 args = json.loads(args)
#             except:
#                 pass
#         # inject identifier
#         if name in ("play_music","screenshot") and not args.get("identifier"):
#             args["identifier"] = session_id
#         logger.info("Executing %s with %s", name, args)
#         result = TOOL_MAP.get(name, lambda **_: "")( **args)
#         payload = {"label":"function","name":name,"arguments":args,"result":result}
#         ws.send(json.dumps(payload, ensure_ascii=False))
#         #如果llm_funcsametime为True，则直接返回


# 在文件顶部导入 mem0 管理器
from mem0_manager import Mem0Manager

# 全局变量，但不立即初始化
mem0_mgr = None

# 在 handle_user_text 函数中初始化 mem0 管理器
def handle_user_text(user_text: str, ws, session_id: str = "user123") -> None:
    global mem0_mgr
    
    # 初始化 mem0 管理器（如果尚未初始化）
    if mem0_mgr is None:
        mem0_mgr = Mem0Manager(local_mode=True)  # 或者使用 API 密钥
    
    # 调用 chat_with_dify 处理对话
    chat_with_dify(user_text, user_id=session_id, ws=ws)
    
    # CAMEL 请求处理...（保持原有代码不变）
    # CAMEL request
    with llm_lock:
        agent.reset()
        res = agent.step(user_text)
    calls = _extract_tool_calls(res)
    # logger.info(f"{res}")
    logger.info("Extracted tool_calls: %s", calls)

    if calls:
        call = calls[0]
        name = getattr(call, "name", None) or getattr(call, "tool_name", None)
        args = getattr(call, "arguments", None) or getattr(call, "args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except:
                pass
        # inject identifier
        if name in ("play_music","screenshot") and not args.get("identifier"):
            args["identifier"] = session_id
        logger.info("Executing %s with %s", name, args)
        result = TOOL_MAP.get(name, lambda **_: "")( **args)
        payload = {"label":"function","name":name,"arguments":args,"result":result}
        ws.send(json.dumps(payload, ensure_ascii=False))
        #如果llm_funcsametime为True，则直接返回

