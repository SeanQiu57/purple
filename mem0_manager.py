"""
mem0_manager.py - mem0长期记忆管理模块

本模块提供与阿紫桌宠集成的mem0长期记忆管理功能，替代或增强原有的memorymanager.py。
通过mem0的智能记忆层，为阿紫桌宠提供更强大的记忆能力，包括记忆添加、检索和管理。

作者: Manus AI
日期: 2025-05-24
"""

import os
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Union

# 导入mem0 SDK
try:
    from mem0 import Memory, MemoryClient

except ImportError:
    raise ImportError("请先安装mem0 SDK: pip install mem0ai")

# 配置日志
logger = logging.getLogger("Mem0Manager")
logging.basicConfig(level=logging.INFO)

# 创建线程池
memory_executor = ThreadPoolExecutor(max_workers=1)

class Mem0Manager:
    """
    mem0记忆管理器
    
    用于管理阿紫桌宠的短期和长期记忆，使用mem0作为底层存储和检索引擎。
    提供与原有memorymanager.py兼容的API，同时增强记忆管理能力。
    """
    
    def __init__(self, api_key: Optional[str] = None, local_mode: bool = True):
        """
        初始化mem0记忆管理器
        
        参数:
            api_key: mem0 API密钥，如果使用mem0平台则需提供
            local_mode: 是否使用本地模式，默认为True
        """
        self.api_key = api_key or os.getenv("MEM0_API_KEY", "")
        self.local_mode = local_mode
        
        # 初始化mem0客户端
        # try:
        #     self.mem0 = Memory(api_key=self.api_key if not local_mode else None)
        #     logger.info("✅ mem0记忆管理器初始化完成")
        #---
        try:
            if self.local_mode:
                # 彻底离线：本地 HuggingFace 嵌入 + 本地 Qdrant
                local_cfg = {
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": "BAAI/bge-small-en-v1.5"   # 换成你喜欢的本地 embedding 模型
                }
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {"host": "localhost", "port": 6333}
            }
        }
                self.mem0 = Memory.from_config(local_cfg)      # ✅ 不再读取 OPENAI_API_KEY
            else:
        # 使用 Mem0 Cloud（还是完全跳过 OpenAI；只需你的 Mem0 平台密钥）
                self.mem0 = MemoryClient(
                    api_key=self.api_key,
                    org_id=os.getenv("MEM0_ORG_ID"),
                    project_id=os.getenv("MEM0_PROJECT_ID")
                )
            logger.info("✅ mem0记忆管理器初始化完成")
        
        #---

        except Exception as e:
            logger.error(f"❌ mem0记忆管理器初始化失败: {e}", exc_info=True)
            raise
    
    def append_memory(self, mem_dir: str, role: str, text: str) -> None:
        """
        添加对话记忆到mem0
        
        参数:
            mem_dir: 记忆目录（兼容性参数，实际不使用）
            role: 对话角色（"主人说"或"我（阿紫）说"）
            text: 对话内容
        """
        # 跳过系统通知
        if "$@$系统通知" in text:
            logger.debug("系统通知不计入记忆")
            return
        
        # 构建消息格式
        message_role = "user" if role == "主人说" else "assistant"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 构建消息
        message = {
            "role": message_role,
            "content": text
        }
        
        # 添加到mem0
        try:
            # 使用用户ID作为区分不同用户的标识
            user_id = "default_user"  # 可以从参数中获取或使用默认值
            
            # 添加元数据
            metadata = {
                "type": "conversation",
                "timestamp": timestamp,
                "original_role": role
            }
            
            # 添加到mem0
            result = self.mem0.add(
                [message],  # mem0接受消息列表
                user_id=user_id,
                metadata=metadata
            )
            
            logger.info(f"✅ 已保存记忆（{role}）: {text[:30]}...")
            
            # 兼容性：如果需要保持原有文件记录，可以添加以下代码
            if not os.path.exists(mem_dir):
                os.makedirs(mem_dir, exist_ok=True)
            file_path = os.path.join(mem_dir, "voicememory.txt")
            
            # 追加到文件
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {role}：{text}\n")
            
        except Exception as e:
            logger.error(f"❌ 保存记忆失败: {e}", exc_info=True)
            
            # 失败回退：确保至少写入本地文件
            try:
                if not os.path.exists(mem_dir):
                    os.makedirs(mem_dir, exist_ok=True)
                file_path = os.path.join(mem_dir, "voicememory.txt")
                
                # 追加到文件
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}] {role}：{text}\n")
                logger.info(f"✅ 已保存记忆到本地文件（{role}）")
            except Exception as e2:
                logger.error(f"❌ 本地文件保存也失败: {e2}", exc_info=True)
    
    def get_memories(self, query: str, user_id: str = "default_user", limit: int = 10) -> List[Dict[str, Any]]:
        """
        检索相关记忆
        
        参数:
            query: 查询文本
            user_id: 用户ID
            limit: 返回结果数量限制
            
        返回:
            相关记忆列表
        """
        try:
            # 使用mem0搜索相关记忆
            memories = self.mem0.search(
                query=query,
                user_id=user_id,
                limit=limit
            )
            
            logger.info(f"✅ 已检索记忆，找到{len(memories)}条相关记忆")
            return memories
            
        except Exception as e:
            logger.error(f"❌ 检索记忆失败: {e}", exc_info=True)
            return []
    
    def get_formatted_memories(self, query: str, user_id: str = "default_user") -> Dict[str, str]:
        """
        获取格式化的记忆，用于构建提示词
        
        参数:
            query: 查询文本
            user_id: 用户ID
            
        返回:
            包含短期记忆和长期记忆的字典
        """
        try:
            # 检索相关记忆
            memories = self.get_memories(query, user_id)
            
            # 分离短期记忆和长期记忆
            short_term = []
            long_term = []
            
            for memory in memories:
                # 根据记忆类型或时间分类
                if "metadata" in memory and memory["metadata"].get("type") == "summary":
                    long_term.append(memory)
                else:
                    short_term.append(memory)
            
            # 格式化短期记忆
            short_term_text = ""
            for memory in short_term:
                content = memory.get("content", "")
                role = memory.get("metadata", {}).get("original_role", "未知")
                timestamp = memory.get("metadata", {}).get("timestamp", "")
                short_term_text += f"[{timestamp}] {role}：{content}\n"
            
            # 格式化长期记忆
            long_term_text = ""
            for memory in long_term:
                content = memory.get("content", "")
                long_term_text += f"{content}\n"
            
            return {
                "short_term": short_term_text,
                "long_term": long_term_text
            }
            
        except Exception as e:
            logger.error(f"❌ 获取格式化记忆失败: {e}", exc_info=True)
            
            # 失败回退：尝试从本地文件读取
            try:
                mem_dir = os.path.join(os.getcwd(), "voicememory")
                epi_path = os.path.join(mem_dir, "Episodicmemory.txt")
                short_path = os.path.join(mem_dir, "voicememory.txt")
                
                short_term_text = ""
                if os.path.exists(short_path):
                    with open(short_path, "r", encoding="utf-8") as f:
                        short_term_text = f.read()
                
                long_term_text = ""
                if os.path.exists(epi_path):
                    with open(epi_path, "r", encoding="utf-8") as f:
                        long_term_text = f.read()
                
                logger.info("✅ 已从本地文件读取记忆")
                return {
                    "short_term": short_term_text,
                    "long_term": long_term_text
                }
            except Exception as e2:
                logger.error(f"❌ 本地文件读取也失败: {e2}", exc_info=True)
                return {
                    "short_term": "",
                    "long_term": ""
                }
    
    def refine_memory(self, mem_dir: str) -> None:
        """
        提炼长期记忆（兼容性函数）
        
        在mem0中，记忆提炼是自动进行的，此函数主要用于兼容现有代码
        
        参数:
            mem_dir: 记忆目录（兼容性参数，实际不使用）
        """
        logger.info("🔁 mem0自动管理记忆，无需手动提炼")
        # 实际上mem0会自动管理记忆，无需手动提炼
        pass
    
    def append_vision_memory(self, mem_dir: str, description: str, event_time: datetime = None) -> None:
        """
        添加视觉记忆
        
        参数:
            mem_dir: 记忆目录（兼容性参数，实际不使用）
            description: 视觉描述
            event_time: 事件时间，默认为当前时间
        """
        try:
            # 使用当前时间
            now = datetime.now()
            if event_time is None:
                event_time = now
                
            # 计算时间差
            delta = now - event_time
            minutes = delta.seconds // 60
            seconds = delta.seconds % 60
            rel = f"{minutes}分钟{seconds}秒前"
            
            # 构建消息
            message = {
                "role": "system",
                "content": f"[{rel}] 我看见：{description}"
            }
            
            # 添加元数据
            metadata = {
                "type": "vision",
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "description": description
            }
            
            # 添加到mem0
            user_id = "default_user"  # 可以从参数中获取或使用默认值
            result = self.mem0.add(
                [message],
                user_id=user_id,
                metadata=metadata
            )
            
            logger.info(f"📷 已保存视觉记忆: {description[:30]}...")
            
            # 兼容性：如果需要保持原有文件记录，可以添加以下代码
            base_dir = os.path.join(mem_dir, "visionmemory")
            os.makedirs(base_dir, exist_ok=True)
            file_path = os.path.join(base_dir, "visionmemory.txt")
            
            # 读取已有记忆
            entries = []
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    entries = f.readlines()
            
            # 添加新记忆
            timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
            entry = f"[{timestamp}] [{rel}] 我看见：{description}\n"
            entries.append(entry)
            
            # 保留最后三条
            if len(entries) > 3:
                entries = entries[-3:]
            
            # 写回文件
            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(entries)
            
        except Exception as e:
            logger.error(f"❌ 保存视觉记忆失败: {e}", exc_info=True)
            
            # 失败回退：确保至少写入本地文件
            try:
                base_dir = os.path.join(mem_dir, "visionmemory")
                os.makedirs(base_dir, exist_ok=True)
                file_path = os.path.join(base_dir, "visionmemory.txt")
                
                # 读取已有记忆
                entries = []
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        entries = f.readlines()
                
                # 添加新记忆
                now = datetime.now()
                if event_time is None:
                    event_time = now
                delta = now - event_time
                minutes = delta.seconds // 60
                seconds = delta.seconds % 60
                rel = f"{minutes}分钟{seconds}秒前"
                timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
                entry = f"[{timestamp}] [{rel}] 我看见：{description}\n"
                entries.append(entry)
                
                # 保留最后三条
                if len(entries) > 3:
                    entries = entries[-3:]
                
                # 写回文件
                with open(file_path, "w", encoding="utf-8") as f:
                    f.writelines(entries)
                
                logger.info(f"📷 已保存视觉记忆到本地文件")
            except Exception as e2:
                logger.error(f"❌ 本地文件保存也失败: {e2}", exc_info=True)
    
    def save_episodic_memory(self, mem_dir: str, summary: str) -> None:
        """
        保存情景记忆（兼容性函数）
        
        参数:
            mem_dir: 记忆目录（兼容性参数，实际不使用）
            summary: 情景记忆摘要
        """
        try:
            # 构建消息
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = {
                "role": "system",
                "content": summary
            }
            
            # 添加元数据
            metadata = {
                "type": "summary",
                "timestamp": timestamp
            }
            
            # 添加到mem0
            user_id = "default_user"  # 可以从参数中获取或使用默认值
            result = self.mem0.add(
                [message],
                user_id=user_id,
                metadata=metadata
            )
            
            logger.info(f"🧠 已保存情景记忆: {summary[:30]}...")
            
            # 兼容性：如果需要保持原有文件记录，可以添加以下代码
            episodic_path = os.path.join(mem_dir, "Episodicmemory.txt")
            entry = f"[{timestamp}] {summary}\n{'-'*40}\n"
            
            with open(episodic_path, "a", encoding="utf-8") as f:
                f.write(entry)
            
        except Exception as e:
            logger.error(f"❌ 保存情景记忆失败: {e}", exc_info=True)
            
            # 失败回退：确保至少写入本地文件
            try:
                episodic_path = os.path.join(mem_dir, "Episodicmemory.txt")
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry = f"[{timestamp}] {summary}\n{'-'*40}\n"
                
                with open(episodic_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                
                logger.info(f"🧠 已保存情景记忆到本地文件")
            except Exception as e2:
                logger.error(f"❌ 本地文件保存也失败: {e2}", exc_info=True)


# 单例模式，方便全局访问
_instance = None

def get_instance(api_key: Optional[str] = None, local_mode: bool = True) -> Mem0Manager:
    """
    获取Mem0Manager单例
    
    参数:
        api_key: mem0 API密钥
        local_mode: 是否使用本地模式
        
    返回:
        Mem0Manager实例
    """
    global _instance
    if _instance is None:
        _instance = Mem0Manager(api_key=api_key, local_mode=local_mode)
    return _instance


# 兼容性函数，与原memorymanager.py保持一致的API
def append_memory(mem_dir: str, role: str, text: str) -> None:
    """
    添加对话记忆（兼容性函数）
    
    参数:
        mem_dir: 记忆目录
        role: 对话角色
        text: 对话内容
    """
    manager = get_instance()
    manager.append_memory(mem_dir, role, text)

def refine_memory(mem_dir: str) -> None:
    """
    提炼长期记忆（兼容性函数）
    
    参数:
        mem_dir: 记忆目录
    """
    manager = get_instance()
    manager.refine_memory(mem_dir)

def append_vision_memory(mem_dir: str, description: str, event_time: datetime = None) -> None:
    """
    添加视觉记忆（兼容性函数）
    
    参数:
        mem_dir: 记忆目录
        description: 视觉描述
        event_time: 事件时间
    """
    manager = get_instance()
    manager.append_vision_memory(mem_dir, description, event_time)

def save_episodic_memory(mem_dir: str, summary: str) -> None:
    """
    保存情景记忆（兼容性函数）
    
    参数:
        mem_dir: 记忆目录
        summary: 情景记忆摘要
    """
    manager = get_instance()
    manager.save_episodic_memory(mem_dir, summary)


if __name__ == "__main__":
    # 简单测试
    print("测试mem0记忆管理器...")
    
    try:
        # 初始化
        manager = Mem0Manager(local_mode=True)
        
        # 添加记忆
        manager.append_memory("./test_memory", "主人说", "你好，阿紫！")
        manager.append_memory("./test_memory", "我（阿紫）说", "你好，主人！有什么可以帮助你的吗？")
        
        # 检索记忆
        memories = manager.get_formatted_memories("你好")
        print("短期记忆:", memories["short_term"])
        print("长期记忆:", memories["long_term"])
        
        print("测试完成！")
    except Exception as e:
        print(f"测试失败: {e}")
