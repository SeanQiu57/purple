import os
from mem0 import Memory

# 设置火山引擎的环境变量
os.environ['VOLC_API_KEY'] = "a87ed8b0-0a14-4e85-8e2c-ab155445e1b0"
os.environ['VOLC_ENDPOINT_ID'] = "ep-20250428190549-76rxg"
os.environ['VOLC_REGION'] = "cn-beijing"

class Mem0Manager:
    def __init__(self):
        # 如果mem0支持火山引擎，使用这种配置
        config = {
            "embedder": {
                "provider": "volcengine",
                "config": {
                    "api_key": os.getenv('VOLC_API_KEY'),
                    "endpoint_id": os.getenv('VOLC_ENDPOINT_ID'),
                    "region": os.getenv('VOLC_REGION')
                }
            }
        }
        
        self.memory = Memory(config=config)
