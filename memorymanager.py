import os
from datetime import datetime
import requests
import logging
from concurrent.futures import ThreadPoolExecutor

MAX_MEMORY = 30
SUMMARY_COUNT = 15
MEM_FILE = "voicememory.txt"
EPISODIC_FILE = "Episodicmemory.txt"

# ====== 请在环境变量或安全配置中保管 API_KEY ======
MEM_DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-vCu1LSFXivrfQPcs7H7jDC7g")
DIFY_CHAT_URL = "https://api.dify.ai/v1/chat-messages"

logger = logging.getLogger("MemoryManager")
logging.basicConfig(level=logging.INFO)
memory_executor = ThreadPoolExecutor(max_workers=1)



# ================= append_memory ================= #
def append_memory(mem_dir: str, role: str, text: str):
    #如何text含有系统通知$@$就直接跳过，然后debuglog：系统通知不计入短期记忆
    if "$@$系统通知" in text:
        logger.debug("系统通知不计入短期记忆")
        return
    os.makedirs(mem_dir, exist_ok=True)
    file_path = os.path.join(mem_dir, MEM_FILE)

    # 1. 读原短期记忆
    lines = []
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    # 2. 追加新行
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"[{timestamp}] {role}：{text}\n")

    # 3. 写回短期记忆
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    logger.info(f"✅ 已保存短期记忆（{role}，共 {len(lines)} 条）: {file_path}")

    # 4. 若达到阈值则 **异步** 提炼
    if len(lines) >= MAX_MEMORY:
        logger.info("🔁 满足提炼条件，提交情景记忆提炼任务到后台线程池")
        # 注意：refine_memory 现在只接受 mem_dir
        memory_executor.submit(refine_memory, mem_dir)
# ================================================= #


# ================ refine_memory =================== #
def refine_memory(mem_dir: str):
    """
    并行提炼长期情景记忆：
    1. 读取并输出（log）全部短期记忆；
    2. 删除最久远的 SUMMARY_COUNT 条，并写回剩余短期记忆；
    3. 将全部短期记忆 + 被删除的最久远短期记忆 + 旧情景记忆一起发给 Dify 生成更新后的情景记忆；
    4. 覆盖保存新的情景记忆。
    """
    import os
    from datetime import datetime

    file_path = os.path.join(mem_dir, MEM_FILE)
    if not os.path.exists(file_path):
        logger.info("⚠️ 没有找到短期记忆文件，跳过提炼")
        return

    # 1. 读取全部短期记忆
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 2. 输出全部短期记忆（日志）
    all_short_block = "".join(lines)
    logger.info(f"📒 全部短期记忆:\n{all_short_block}")

    # 3. 如果不足以提炼，则跳过
    if len(lines) < SUMMARY_COUNT:
        logger.info(f"📉 短期记忆不足 {SUMMARY_COUNT} 条，跳过提炼")
        return

    # 4. 提取最久远的 SUMMARY_COUNT 条
    oldest = lines[:SUMMARY_COUNT]
    remaining = lines[SUMMARY_COUNT:]

    # 5. 更新短期记忆文件，删除已发送的 oldest
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(remaining)
    logger.info(f"✂️ 已删除最久远的 {SUMMARY_COUNT} 条短期记忆")

    oldest_block = "".join(oldest)

    # 6. 读取旧的长期情景记忆
    epi_path = os.path.join(mem_dir, EPISODIC_FILE)
    episodic_block = ""
    if os.path.exists(epi_path):
        with open(epi_path, "r", encoding="utf-8") as f:
            episodic_block = f.read()

    # 7. 构造 prompt：先全部短期记忆，再最久远的对话，最后旧情景记忆
    prompt = (
        "这是近期完整的短期记忆：\n"
        f"{all_short_block}\n"
        "下面是最久远的对话片段，用于提炼：\n"
        f"{oldest_block}\n"
        "这是当前的情景记忆：\n"
        f"{episodic_block}\n\n"
        "请在保留原有情景记忆风格的基础上，"
        "根据上述最久远对话片段，修改并拓展情景记忆，"
        "然后输出更新后的完整情景记忆："
    )

    # 8. 调用 Dify 生成新情景记忆
    try:
        updated = call_dify_summary(prompt)
        if updated:
            # 9. 覆盖保存新的情景记忆
            with open(epi_path, "w", encoding="utf-8") as f:
                f.write(updated.strip() + "\n")
            logger.info(f"🧠 情景记忆已更新并保存: {epi_path}")
    except Exception as e:
        logger.error(f"❌ 情景记忆提炼失败: {e}", exc_info=True)

# ================================================== #



def call_dify_summary(prompt: str) -> str:
    """
    按 Dify 官方文档调用 /chat-messages（阻塞模式）生成摘要。
    返回 LLM 摘要字符串；若请求失败抛出异常。
    """
    headers = {
        "Authorization": f"Bearer {MEM_DIFY_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "query": prompt,
        "inputs": {},               # 如果有自定义变量可放这里
        "response_mode": "blocking",
        "conversation_id": "",      # 不续写历史会话
        "user": "memory_bot"        # 随便一个内部标识
    }

    resp = requests.post(DIFY_CHAT_URL, json=payload, headers=headers, timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"Dify Error {resp.status_code}: {resp.text}")

    data = resp.json()
    # 阻塞模式正常返回的 answer 在最外层
    summary = data.get("answer")
    if not summary:
        raise RuntimeError(f"Unexpected Dify response: {data}")

    return summary.strip()


def save_episodic_memory(mem_dir: str, summary: str):
    episodic_path = os.path.join(mem_dir, EPISODIC_FILE)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {summary}\n{'-'*40}\n"

    with open(episodic_path, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info(f"🧠 长期情景记忆已保存: {episodic_path}")
def append_vision_memory(mem_dir: str, description: str, event_time: datetime = None):
    """
    保存图像记忆：
    - 在 voicememory 同级创建 visionmemory 文件夹
    - 写入格式：[YYYY-MM-DD HH:MM:SS] [XmY秒前] 我看见：description
    - 最多保留最近三条
    - 相对事件码为当前时间与 event_time 间隔
    """
    base_dir = os.path.join(mem_dir, "visionmemory")
    os.makedirs(base_dir, exist_ok=True)
    file_path = os.path.join(base_dir, "visionmemory.txt")

    # 读取已有记忆
    entries = []
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            entries = f.readlines()

    # 计算时间码
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

    # 写回 visionmemory
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(entries)

    logger.info(f"📷 已保存图像记忆，共 {len(entries)} 条: {file_path}")
# ======================================================== #
