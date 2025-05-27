#------------初始化记忆系统------------
from camel.models import ModelFactory
from camel.types import ModelPlatformType
from mem0 import Memory

# 初始化火山引擎模型
VOLC_REGION = "cn-beijing"
ENDPOINT_ID = "ep-20250428190549-76rxg"  # 例如 "ep-20250506152643-v5wqm"
API_KEY = "a87ed8b0-0a14-4e85-8e2c-ab155445e1b0"
BASE_URL = f"https://ark.{VOLC_REGION}.volces.com/api/v3"

# 使用CAMEL库创建火山引擎模型客户端
volc_model = ModelFactory.create(
    model_platform=ModelPlatformType.VOLCANO,  # 使用火山引擎平台
    model_type=ENDPOINT_ID,                    # 模型端点ID
    api_key=API_KEY,                           # API密钥
    url=BASE_URL,                              # API基础URL
 )



#-------添加记忆------------

# 方法1：直接添加对话消息
messages = [
    {"role": "system", "content": "你是一个名叫阿紫的桌宠助手。"},
    {"role": "user", "content": "我喜欢听周杰伦的歌。"},
    {"role": "assistant", "content": "我记住了，你喜欢周杰伦的音乐！"}
]

# 添加记忆，关联到特定用户
memory.add(messages, user_id="user123")

# 方法2：添加单条记忆
memory.add_memory(
    memory="用户喜欢周杰伦的音乐",
    user_id="user123",
    metadata={"category": "user_preference", "subject": "music"}
)
