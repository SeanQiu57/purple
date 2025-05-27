阿紫是一个AI虚拟桌宠项目，目前正在进行中，以及有了成熟的模块，需要进一步的整理和封装


各模块介绍：
//emotion_controller.py	作为独立线程运行：1. 从主线程收到 (prompt, ws) 队列项；2. 调用 Dify 生成“Face%@% … / Act%@% …”情绪标记；3. 解析出 faces、acts 并补齐长度、做范围校验；4. 通过 WebSocket 向前端发送 {"label":"emotion","faces":[…],"acts":[…]}。	使用 queue.Queue 做线程安全排队，任何异常时回退到默认表情/动作。

//func_client.py	演示脚本：用 SSE 流式接口调用 Dify，并捕捉 OpenAI-style function-calling。若 1 秒内流数据中出现 agent_thought.tool_input 就返回 {"label":"function", …}，否则返回 {"label":"none"}。	定义了单个函数规格 playmusic 并设置 timeout_sec = 2.0。
llm_client.py	对 Dify 的 阻塞式(chat_with_dify) 和 流式(chat_stream_dify) 封装，带详细日志；屏蔽了 API Key / URL / 超时等配置。	chat_with_dify() 返回 (answer, conversation_id)；流式版本用 sseclient 逐块产出文本。

//memorymanager.py	记忆系统：• append_memory 把每句对话追加到 voicememory.txt（短期记忆），超过 MAX_MEMORY(30) 时异步触发 refine_memory；• refine_memory 把最早的 SUMMARY_COUNT(15) 条对话、剩余短期记忆、已有情景记忆拼成 prompt，请 Dify 生成新的情景记忆并写入 Episodicmemory.txt。	异步提炼通过 ThreadPoolExecutor 完成，避免阻塞主流程。

//senddoubao.py	一次性 ASR 调用字节跳动飞书「豆包」语音服务：• 建立 WS；• 先发完整“Full Client Request”，随后按 200 ms 分包发送 PCM；• 等待 code==1000 && sequence<0 的最终结果并返回识别文本。	把音频压缩为 gzip，再加自定义 4 字节头与长度字段。

//test_unity_client.py	本地测试客户端：连接 ws://127.0.0.1:5001/vad_asr，把命令行输入包装成 {"label":"text_input","text":…} 发给服务器；也打印服务器回包，便于在没有 Unity 时调试。	在 on_open 中启动后台线程持续读取 stdin。

//vad_asr.py	核心实时语音服务器：1. 监听 /vad_asr WS；2. 每 512 采样做 Silero VAD，检测讲话段落；3. 讲话结束后把缓存帧保存成 wav（tmp/asr_*.wav），丢给 senddoubao.asr_once；4. 获得 user_text 后调用 camelfunc.handle_user_text；5. 同时支持文本输入(text_input)和历史查询(history_request)。	细节：滑窗长度 10，需 ≥6 帧判为语音；静音 1.5 秒触发“结束说话”。

//camelfunc.py	整体对话-指令管线：• 定义桌宠三大指令工具：play_music、screenshot、recite_poem，注册到 CAMEL ChatAgent；• handle_user_text 负责：① 把对话写入记忆 → ② 用 Dify 生成阿紫口吻回复 → ③ 让 EmotionController 解析情绪并回传 → ④ 用 CAMEL 判断是否应调用工具，若有则执行并把 {"label":"function",…} 结果发回前端。	执行工具前自动补充 identifier 会话 ID；所有操作通过同一个 WebSocket 推送。

总体流程说明：
1. 收音与端点检测
 Unity 前端把麦克风原始 PCM 通过 WebSocket 发送给 vad_asr.py。Silero VAD 持续判断声音帧，进入“讲话中”与“静音”两种状态；静音超过 1.5 秒触发语音段结束。
2. ASR
 完整语音段保存为 wav，调用 senddoubao.asr_once() 经字节跳动豆包 API 识别文字。
3. 对话处理
 识别文本交给 camelfunc.handle_user_text：
  - 把对话写入短期记忆（必要时在后台线程汇总为情景记忆）。
  - 组织 prompt 送 Dify 生成阿紫口吻回复。
  - 最终文本交给 EmotionController 抽取表情/动作并立即推送。
4. 桌宠工具调用
 同时，ChatAgent（CAMEL）检查用户意图是否符合三大桌宠指令；若匹配则返回 OpenAI-style tool_calls，执行对应 Python 函数并将执行结果再推送给前端。
5. 记忆回圈
 当短期记忆累计到 30 条后，memorymanager.py 新起线程把较旧 15 条对话与旧情景记忆发给 Dify，总结成新的情景记忆并保存，保持长期人格连贯。
6. 测试与调试
 没有 Unity 时，可用 test_unity_client.py 直接和服务器文本对话；func_client.py 提供了捕捉 Dify 函数调用的独立示例，但在端到端流程中已由 CAMEL 工具链取代。




VAD_ASR.py
/////////////////////
1. 简易工作流程
1. 启动服务器：程序启动时，创建一个WebSocket服务器，等待用户连接
2. 用户连接：当用户通过网页或app连接到服务器后，系统准备接收用户的语音
3. 实时语音处理：
  - 用户的语音被切分成小片段发送到服务器
  - 服务器使用VAD（语音活动检测）模型判断是否有人在说话
  - 当检测到开始说话时，系统开始记录语音
  - 当检测到停止说话（静音超过1.5秒）时，系统停止记录
4. 语音转文字：系统将记录的语音转换成文字（ASR过程）
5. 处理文字：系统处理转换后的文字，可能会生成回复或执行其他操作

2. 分段解释
我将把简易工作流程中的每个部分对应到实现这些功能的具体代码，并进行简单讲解：
1. 启动服务器
if __name__ == '__main__':
    logger.info("🚀 服务启动：:5001")
    # 创建WebSocket服务器，监听5001端口
    server = WebSocketServer(
        ('0.0.0.0', 5001),
        Resource({'/vad_asr': VADASRApp})  # 路由配置
    )
    # 启动服务器
    server.serve_forever()
这段代码：
- 创建了一个WebSocket服务器，监听所有网络接口(0.0.0.0)的5001端口
- 将路径/vad_asr映射到VADASRApp类，这样当用户连接到ws://服务器地址:5001/vad_asr时，会创建VADASRApp的实例
- serve_forever()让服务器持续运行，等待连接
2. 用户连接
def on_open(self):
    """当WebSocket连接打开时调用"""
    # 将连接添加到连接池
    WS_POOL.add(self.ws)
    # 初始化VAD模型
    self.vad = SileroVAD()
    # 初始化状态变量
    self.is_talking = False      # 是否正在说话
    self.buffer = []             # 音频缓冲区
    self.silent_start = None     # 静音开始时间
    self.window = deque(maxlen=WINDOW_SIZE)  # 语音检测滑动窗口
    logger.info("✅ 客户端连接，开始监听音频")
当用户连接到服务器时：
- on_open函数被自动调用
- 连接被添加到WS_POOL（连接池）以便管理
- 创建一个新的VAD模型实例，用于检测语音
- 初始化一些变量，如音频缓冲区和状态标志
- 记录日志表示客户端已连接
3. 实时语音处理
def on_message(self, msg):
    # ... [处理文本消息的代码省略] ...
    
    # 处理二进制音频数据
    # 使用VAD检测是否有语音
    has_voice = self.vad.is_speech(msg)
    self.window.append(has_voice)
    # 如果窗口中的语音帧数超过阈值，判定为说话中
    speaking = sum(self.window) >= MIN_VOICE_FRAMES
    
    if speaking:
        # 开始说话
        if not self.is_talking:
            self.is_talking = True
            self.buffer.clear()
            self.silent_start = None
            logger.info(">>> start speaking")
            self.ws.send(json.dumps({"label": "start"}))
        # 添加音频帧到缓冲区
        self.buffer.append(msg)
    else:
        # 如果之前在说话，现在可能是静音
        if self.is_talking:
            # 记录静音开始时间
            if not self.silent_start:
                self.silent_start = time.time()
            # 继续添加音频帧（包括静音）
            self.buffer.append(msg)
            # 如果静音时间超过阈值，认为说话结束
            if time.time() - self.silent_start >= _SILENCE_THRESHOLD:
                self.is_talking = False
                logger.info("<<< end speaking, ASR")
                self.ws.send(json.dumps({"label": "finish"}))
                # 保存音频并进行ASR
                path = save_frames_to_wav(self.buffer)
                fut = EXECUTOR.submit(asr_once, path)
                fut.add_done_callback(lambda f: _asr_callback(f, path, self.ws))
                # 重置状态
                self.buffer.clear()
                self.silent_start = None
当服务器接收到音频数据时：
- on_message函数被调用，接收到的是二进制音频数据
- 使用VAD模型检测这段音频是否包含语音：has_voice = self.vad.is_speech(msg)
- 使用滑动窗口技术减少误判：将最近的10个检测结果存在窗口中，如果有6个以上是语音，就认为用户在说话
- 如果检测到说话：
  - 如果之前没在说话，标记开始说话，发送"start"消息给客户端
  - 将音频数据添加到缓冲区
- 如果检测到静音：
  - 如果之前在说话，记录静音开始时间
  - 如果静音持续超过1.5秒，认为说话结束，发送"finish"消息给客户端
  - 将收集的音频保存为WAV文件，并提交给ASR进行处理

4. 语音转文字

def save_frames_to_wav(frames: List[bytes]) -> str:
    """将音频帧保存为WAV文件"""
    tmpdir = "tmp"
    os.makedirs(tmpdir, exist_ok=True)
    # 清理临时目录中的旧文件
    for f in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, f))
    
    # 生成唯一文件名
    path = os.path.join(tmpdir, f"asr_{uuid.uuid4().hex}.wav")
    
    # 写入WAV文件
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)       # 单声道
        wf.setsampwidth(2)       # 16位音频 (2字节)
        wf.setframerate(_RATE)   # 采样率
        wf.writeframes(b"".join(frames))  # 写入音频数据
    
    return path

# 在on_message中的这部分代码:
path = save_frames_to_wav(self.buffer)
fut = EXECUTOR.submit(asr_once, path)
fut.add_done_callback(lambda f: _asr_callback(f, path, self.ws))

语音转文字的过程：
- save_frames_to_wav函数将收集的音频数据保存为WAV文件
- 使用线程池提交ASR任务：EXECUTOR.submit(asr_once, path)
  - asr_once是从senddoubao模块导入的函数，负责实际的语音识别
  - 这样做可以避免阻塞主线程，保持服务器响应
- 设置回调函数，当ASR完成时会调用_asr_callback

5. 处理文字

def _asr_callback(fut, path, ws):
    """
    ASR（自动语音识别）完成后的回调函数
    """
    try:
        # 获取ASR结果
        user_text = fut.result()
        logger.info(f"[ASR] {user_text}")
        # 处理用户文本
        handle_user_text(user_text, ws)
    except Exception as e:
        logger.error(f"[ASR CALLBACK ERROR] {e}", exc_info=True)

文字处理过程：
- 当ASR完成时，_asr_callback函数被调用
- 从Future对象中获取ASR结果（即转换后的文本）
- 调用handle_user_text函数处理这段文本
  - 这个函数来自camelfunc模块，可能包含对话管理、意图识别等功能
  - 它会生成回复并通过WebSocket发送回客户端

补充：文本输入处理

代码中还支持直接的文本输入处理：

# 处理文本输入
if label == "text_input":
    user_text = obj.get("text", "").strip()
    logger.info(f"[on_message] text_input: {user_text}")
    if user_text:
        logger.info("[on_message] submitting handle_user_text")
        EXECUTOR.submit(handle_user_text, user_text, self.ws)
    return

这部分允许用户直接发送文本消息，而不是通过语音。系统会直接处理这些文本，跳过语音识别步骤。


---

总结来说，这个系统的核心是通过WebSocket实时接收音频，使用VAD技术检测语音活动，然后使用ASR将语音转换为文本，最后处理文本并生成回复。整个过程是流畅的、实时的，就像与真人对话一样。


2.camelfunc.py
1. 整体工作流程
这个代码文件(camelfunc.py)实现了一个桌面宠物的命令解析系统，它结合了两种AI处理方式：
1. 工具调用模式：使用CAMEL框架识别特定命令（播放音乐、截图/拍照、背诗）并执行对应功能
2. 聊天模式：对于非特定命令，使用Dify API生成聊天回复
工作流程如下：
1. 接收用户输入文本
2. 将用户输入保存到记忆系统
3. 使用Dify生成聊天回复并发送到WebSocket
4. 同时，使用CAMEL代理分析用户输入是否包含特定命令
5. 如果识别到特定命令，执行对应工具函数并将结果发送到WebSocket
这种双轨设计确保了系统既能执行特定功能，又能维持自然的对话能力。
2. 代码各部分详细解析
第1部分：导入和基础设置
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
功能解析：
- 导入标准库：
  - json: 用于处理JSON数据格式
  - logging: 提供日志记录功能
  - os和sys: 处理文件路径和系统操作
  - threading: 提供线程锁功能，确保并发安全
  - typing: 提供类型提示功能
- 导入CAMEL相关模块：
  - ChatAgent: CAMEL框架的核心代理类
  - ModelFactory: 用于创建不同平台的语言模型
  - FunctionTool: 用于注册可调用的工具函数
  - ModelPlatformType: 定义支持的模型平台类型
- 导入自定义模块：
  - EmotionController: 控制桌面宠物的情感表达
  - chat_with_dify: 与Dify API通信的函数
  - append_memory: 管理对话记忆的函数
这部分代码主要是准备工作，导入系统所需的各种模块和库。
第2部分：配置和初始化
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

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
)
功能解析：
- 环境变量加载：
  - 使用load_dotenv()从.env文件加载环境变量
  - 获取火山引擎(Volcengine)的配置参数，包括区域、端点ID和API密钥
  - 设置API基础URL
- 全局设置：
  - llm_funcsametime = True: 控制是否同时执行LLM和函数调用(虽然定义了但代码中未完全实现这个功能)
- 模型初始化：
  - 使用ModelFactory.create()创建火山引擎模型实例
  - 指定模型平台、类型、API密钥和URL
- 日志配置：
  - 创建日志记录器并设置格式
  - 配置日志级别为INFO
这部分代码主要是配置系统运行环境和初始化语言模型。
第3部分：工具函数定义
def play_music(
    identifier: str = "",
    name: str = "",
    is_exact: str = "fuzzy",
    song_type: str = "single",
) -> str:
    """
    当输入中含有"播放音乐"、"播放专辑"、"播放歌单"这些关键词指令时就调用此函数
    ...
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
    action = "屏幕截图" if camera_type == "screen" else "拍照"
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
功能解析：
- 播放音乐功能(play_music)：
  - 参数：
    - identifier: 会话唯一ID
    - name: 要播放的音乐/专辑/歌单名称
    - is_exact: 匹配类型(精确/模糊)
    - song_type: 播放类型(单曲/专辑/歌单)
  - 记录函数调用信息
  - 返回播放成功的消息
- 截图/拍照功能(screenshot)：
  - 参数：
    - identifier: 会话唯一ID
    - camera_type: 相机类型(屏幕/摄像头)
  - 根据camera_type确定操作类型
  - 返回操作成功的消息
- 背诗功能(recite_poem)：
  - 参数：
    - topic: 诗歌主题
  - 根据主题生成一首简单的诗
  - 返回生成的诗歌
这部分代码定义了三个核心工具函数，每个函数对应一个特定的用户命令。这些函数目前只是简单模拟功能，实际应用中可能需要与真实的音乐播放器、截图工具等集成。
第4部分：工具注册和CAMEL代理初始化
music_tool = FunctionTool(play_music)
screenshot_tool = FunctionTool(screenshot)
poem_tool = FunctionTool(recite_poem)
TOOL_MAP: Dict[str, Any] = {
    "play_music": play_music,
    "screenshot": screenshot,
    "recite_poem": recite_poem,
}

SYSTEM_PROMPT = (
    ""
)

agent = ChatAgent(
    model=volc_model,
    tools=[music_tool, screenshot_tool, poem_tool],
    system_message=SYSTEM_PROMPT,
    memory=None,
)
llm_lock = threading.Lock()
em_ctrl = EmotionController()
功能解析：
- 工具注册：
  - 使用FunctionTool将三个函数包装为CAMEL工具
  - 创建TOOL_MAP字典，用于通过名称查找对应函数
- 系统提示词：
  - 定义SYSTEM_PROMPT，当前为空字符串
  - 在实际应用中，可以在这里添加指导AI行为的系统提示
- CAMEL代理初始化：
  - 创建ChatAgent实例，使用火山引擎模型
  - 注册三个工具函数
  - 设置系统提示词
  - 不使用内置记忆功能(memory=None)
- 其他初始化：
  - 创建线程锁llm_lock，用于保护代理访问，确保并发安全
  - 初始化情感控制器em_ctrl，用于控制桌面宠物的情感表达
这部分代码将工具函数注册到CAMEL框架，并初始化代理和其他必要组件。
第5部分：工具调用提取函数
def _extract_tool_calls(res: Any):
    """从CAMEL代理响应中提取工具调用信息"""
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
    text = getattr(res, "content", "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
            from types import SimpleNamespace
            return [SimpleNamespace(name=obj["name"], arguments=obj["arguments"])]
    except json.JSONDecodeError:
        pass
    return []
功能解析：
- 功能概述：
  - 从CAMEL代理响应中提取工具调用信息
  - 由于不同模型返回格式可能不同，函数尝试多种方式提取工具调用
- 提取策略：
  1. 检查res.tool_calls属性
  2. 检查res.tool_call属性
  3. 检查res.info字典中的tool_calls
  4. 检查res.messages列表中每个消息的tool_calls属性
  5. 尝试解析res.content字段中的JSON
- 返回值：
  - 返回工具调用列表，每个工具调用包含名称和参数
  - 如果没有找到工具调用，返回空列表
这个函数处理了多种可能的响应格式，确保能够从不同模型的输出中正确提取工具调用信息。
第6部分：主要处理函数
def handle_user_text(user_text: str, ws, session_id: str = "user123") -> None:
    """处理用户输入文本：调用工具或回退到Dify"""
    # 持久化记忆
    mem_dir = os.path.join(os.getcwd(), "voicememory")
    os.makedirs(mem_dir, exist_ok=True)
    append_memory(mem_dir, "主人说", user_text)
    
    # 构建给dify的提示词
    epi = open(os.path.join(mem_dir, "Episodicmemory.txt"), "r", encoding="utf-8").read() if os.path.exists(os.path.join(mem_dir, "Episodicmemory.txt")) else ""
    short = open(os.path.join(mem_dir, "voicememory.txt"), "r", encoding="utf-8").read() if os.path.exists(os.path.join(mem_dir, "voicememory.txt")) else ""
    prompt = f"===情景记忆===\n{epi}\n===短期对话===\n{short}\n===用户说===\n{user_text}\n请以阿紫口吻回复："
    
    # 使用Dify获取回复
    reply = ""
    try:
        reply = chat_with_dify(prompt, user_id=session_id)[0]
    except Exception as e:
        logger.error("Dify error", exc_info=e)
        reply = "抱歉，聊天服务暂时不可用。"
    
    # 记录回复到记忆
    append_memory(mem_dir, "我（阿紫）说", reply)
    
    # 提交情感控制和发送聊天回复
    em_ctrl.submit(prompt=reply, ws=ws)
    ws.send(json.dumps({"label":"chat","reply":reply}, ensure_ascii=False))
    
    # 使用CAMEL代理处理用户输入
    with llm_lock:
        agent.reset()
        res = agent.step(user_text)
    calls = _extract_tool_calls(res)
    logger.info("Extracted tool_calls: %s", calls)

    # 如果有工具调用
    if calls:
        call = calls[0]
        name = getattr(call, "name", None) or getattr(call, "tool_name", None)
        args = getattr(call, "arguments", None) or getattr(call, "args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except:
                pass
        
        # 注入会话标识符
        if name in ("play_music","screenshot") and not args.get("identifier"):
            args["identifier"] = session_id
        
        # 执行工具函数
        logger.info("Executing %s with %s", name, args)
        result = TOOL_MAP.get(name, lambda **_: "")(**args)
        
        # 发送结果到WebSocket
        payload = {"label":"function","name":name,"arguments":args,"result":result}
        ws.send(json.dumps(payload, ensure_ascii=False))
功能解析：
- 记忆管理：
  - 创建记忆目录(voicememory)
  - 使用append_memory函数记录用户输入
- Dify聊天处理：
  - 读取情景记忆和短期对话记忆
  - 构建提示词，包含记忆内容和用户输入
  - 调用chat_with_dify获取回复
  - 处理可能的异常，提供备用回复
  - 记录AI回复到记忆系统
- 情感控制和回复发送：
  - 使用情感控制器处理回复
  - 将聊天回复发送到WebSocket，标签为"chat"
- CAMEL工具调用处理：
  - 使用线程锁保护代理访问
  - 重置代理状态并处理用户输入
  - 提取工具调用信息
  - 如果识别到工具调用：
    - 获取工具名称和参数
    - 注入会话标识符(对于需要的工具)
    - 执行对应的工具函数
    - 将结果发送到WebSocket，标签为"function"
这个函数是整个系统的核心，它处理用户输入，同时支持聊天回复和工具调用两种模式。
总结
这个代码文件实现了一个桌面宠物命令解析系统，具有以下特点：
1. 双轨处理机制：
  - 使用Dify生成自然聊天回复
  - 使用CAMEL识别和执行特定命令(播放音乐、截图/拍照、背诗)
2. 记忆系统：
  - 保存用户输入和AI回复
  - 使用情景记忆和短期对话记忆提升对话连贯性
3. 情感表达：
  - 通过情感控制器管理桌面宠物的情感表达
4. 工具调用：
  - 定义和注册三个核心工具函数
  - 实现复杂的工具调用提取逻辑，适应不同模型输出
5. 并发安全：
  - 使用线程锁保护代理访问，确保并发安全
这种设计使桌面宠物既能执行特定功能，又能维持自然的对话能力，提供更加丰富的用户交互体验。
