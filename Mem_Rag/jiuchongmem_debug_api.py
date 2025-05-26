# jiuchongmem_debug_api.py
# 独立的fastapi服务，目前设计的是记忆模块启动，自动也跟着启动，之后可以设置为其他方式
"""
FastAPI 调试服务，支持按用户 ID 操作记忆
"""
import os
import uvicorn
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

from .lc_mem_store import (
    init_store, add_text, similarity_search, clear_all, _ROLE
)

LOGGER = logging.getLogger("jiuchongmem_api")
TAG = "[JiuchongMem-API]"

# 默认数据库 URL
DB_URL = os.getenv(
    "DB_URL",
    "postgresql+psycopg2://postgres:sean@127.0.0.1:5432/azi_db"
)

# FastAPI 应用
app = FastAPI(title="九重Memory 调试 API", version="1.0.0")

# 静态前端（可选）
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/debug-ui", StaticFiles(directory=static_dir,
              html=True), name="debug-ui")

# ----------- Pydantic 模型 -----------


class BaseRequest(BaseModel):
    user_id: str


class QueryRequest(BaseRequest):
    q: str


class ImportRequest(BaseRequest):
    text: Optional[str] = None
    texts: Optional[List[str]] = None

# ----------- 路由定义 -----------


@app.post("/memory/query")
async def memory_query(req: QueryRequest):
    # 动态设置全局 ROLE
    _ROLE = req.user_id
    # 确保向量库已初始化 (可选，可在服务启动时预 init)
    init_store(pg_url=DB_URL, ark_client=None,
               ark_model_id=None, chunk_size=500, role_id=_ROLE)
    hits = [d.page_content for d in similarity_search(req.q, k=5)]
    return {"user_id": _ROLE, "query": req.q, "hits": hits}


@app.post("/memory/import")
async def memory_import(req: ImportRequest):
    if not (req.text or req.texts):
        raise HTTPException(422, "请提供 text 或 texts")
    _ROLE = req.user_id
    init_store(pg_url=DB_URL, ark_client=None,
               ark_model_id=None, chunk_size=500, role_id=_ROLE)
    full_text = req.text or "\n".join(req.texts)
    total = add_text(full_text)
    return {"user_id": _ROLE, "total_segments": total}


@app.delete("/memory/clear")
async def memory_clear(user_id: str):
    # 接收查询参数 ?user_id=xxx
    if not user_id:
        raise HTTPException(422, "请提供 user_id 查询参数")
    count = clear_all(DB_URL, user_id)
    return {"user_id": user_id, "deleted": count}

# ----------- 应用入口 -----------
if __name__ == "__main__":
    LOGGER.info(f"{TAG} 调试 API 启动 → http://0.0.0.0:8081/docs")
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
