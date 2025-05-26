# 稳定查询存储500字一chunk版本111
"""
LangChain + pgvector 最小长期记忆存储实现。
------------------------------------------------
修复要点
~~~~~~~~
1. **metadata 写入**
   - 每条向量写入 `{"metadata": {"user_id": _ROLE}}`，后续可精准过滤。
2. **幂等初始化**
   - `_vs`、`_splitter`、`_ROLE` 同时就绪才写回全局，避免半初始化。
3. **自动建扩展 / 表**
   - 兼容旧版 `PGVector` 无 `initialize()` 的情况，手动 `CREATE TABLE`。
4. **清理接口**
   - `clear_all()` 删除自定义表 `jiuchongmemory` 中符合 `user_id` 的记录。
"""
from __future__ import annotations

import logging
import re
from typing import List

from fastapi import logger
from langchain.docstore.document import Document
from langchain.embeddings.base import Embeddings
from langchain.text_splitter import CharacterTextSplitter
from langchain.vectorstores.pgvector import PGVector
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from langchain.text_splitter import CharacterTextSplitter
from langchain.text_splitter import RecursiveCharacterTextSplitter

_logger = logging.getLogger("lc_mem")
# 设置全局 handler（如果还没配置的话）
if not _logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    _logger.addHandler(handler)
# 强制设为 DEBUG 级别
_logger.setLevel(logging.DEBUG)
TABLE = "jiuchongmemory"                # collection_name

_vs = None                                # type: PGVector | None
_splitter = None                           # type: CharacterTextSplitter | None
_ROLE = None                              # 当前设备 / 用户 ID

# ---------------------------------------------------------------------------
# 1. Ark Embedding 适配
# ---------------------------------------------------------------------------


class ArkEmbedding(Embeddings):
    def __init__(self, ark_client, model_id: str):
        self.client = ark_client
        self.model_id = str(model_id)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(model=self.model_id, input=texts)
        return [d.embedding for d in resp.data]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

# ---------------------------------------------------------------------------
# 2. 初始化
# ---------------------------------------------------------------------------


def init_store(*, pg_url: str, ark_client, ark_model_id: str, chunk_size: int, role_id: str) -> PGVector:
    """创建全局 PGVector & splitter；幂等。"""
    global _vs, _splitter, _ROLE
    if _vs and _splitter:
        return _vs

    _ROLE = str(role_id)

    # 保证 pgvector 扩展 & 表存在。
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
        if hasattr(PGVector, "initialize"):
            try:
                PGVector.initialize(pg_url, collection_name=TABLE)
            except ProgrammingError as e:
                if "already exists" not in str(e):
                    raise
        else:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                  id            BIGSERIAL PRIMARY KEY,
                  collection_id BIGINT,
                  embedding     VECTOR(1536),
                  document      TEXT,
                  cmetadata     JSONB,
                  custom_id     VARCHAR,
                  uuid          UUID DEFAULT gen_random_uuid()
                );
            """))

    # PGVector 实例
    embedder = ArkEmbedding(ark_client, ark_model_id)
    vector_store = PGVector(
        connection_string=pg_url,
        collection_name=TABLE,
        embedding_function=embedder,
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=0,
    )
    # 原子赋值
    _vs = vector_store
    _splitter = splitter
    _logger.info("PGVector ready → collection=%s role=%s", TABLE, _ROLE)
    return _vs

# ---------------------------------------------------------------------------
# 3. 内部工具
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    # 只做“压缩”，保留换行与空格
    # 1) 统一换行符
    text = re.sub(r"\r\n|\r", "\n", text)
    # 2) 多个换行压缩到最多两个，保留段落边界
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 3) 多个空格/制表符压缩成 1 个
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def brute_force_chunk(text: str, chunk_size: int = 500, min_size: int = 60) -> list[str]:
    # 先去掉所有空白（如果你想保留换行，去掉这行）
    clean = re.sub(r"\s+", "", text)
    segments = []
    for i in range(0, len(clean), chunk_size):
        seg = clean[i: i + chunk_size]
        if len(seg) >= min_size:
            segments.append(seg)
        # else: 丢掉残余
    return segments


# ---------------------------------------------------------------------------
# 4. 对外 API
# ---------------------------------------------------------------------------

def add_text(text: str) -> int:
    """
    接收一整段 text，清洗后统一走 splitter.split_text，生成 Document 列表并写入。
    """
    if not (_vs and _splitter):
        raise RuntimeError(
            f"init_store() must run first_vs={_vs} _splitter={_splitter}")

    docs: List[Document] = []
    clean = _clean(text)

    # 逐段打印：长度 + 预览
    for idx, seg in enumerate(_splitter.split_text(clean), 1):
        _logger.debug("seg%02d  len=%4d  %.60s…", idx, len(seg), seg)
        if len(seg) < 60:
            continue
        docs.append(Document(page_content=seg, metadata={"user_id": _ROLE}))

    if docs:
        _vs.add_documents(docs)

    _logger.info("✂️  total segments written: %d", len(docs))
    _logger.info("✂️  splitter stats: %s", vars(_splitter))
    return len(docs)


def similarity_search(query: str, *, k: int = 5):
    if not _vs:
        raise RuntimeError("init_store() must run first")
    return _vs.similarity_search(
        query,
        k=k,
        filter={"user_id": _ROLE}
    )


def clear_all(pg_url: str, role_id: str) -> int:
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        res = conn.execute(
            text("""
                DELETE FROM langchain_pg_embedding
                WHERE cmetadata->>'user_id' = :uid
            """),
            {"uid": role_id},
        )
    return res.rowcount
