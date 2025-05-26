# 封装向量数据库查询，chunking和存储的函数

from __future__ import annotations
import logging
import re
from typing import List

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from langchain.docstore.document import Document
from langchain.embeddings.base import Embeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores.pgvector import PGVector
from volcenginesdkarkruntime import Ark

# ───────────────────────────── Logger
_logger = logging.getLogger("lc_mem")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    _logger.addHandler(sh)

# ───────────────────────────── 全局状态
TABLE = "jiuchongmemory"
_vs = None          # type: PGVector | None
_splitter = None          # type: RecursiveCharacterTextSplitter | None
_ROLE = None          # 当前 user / device id

# ───────────────────────────── Embedding 适配


class ArkEmbedding(Embeddings):
    def __init__(self, ark_client: Ark, model_id: str):
        self.client = ark_client
        self.model_id = str(model_id)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(model=self.model_id, input=texts)
        return [d.embedding for d in resp.data]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]

# ───────────────────────────── 初始化


def init_store(*, pg_url: str, ark_client: Ark, ark_model_id: str, chunk_size: int, role_id: str):
    """
    幂等：第二次调用直接返回已创建的 PGVector。
    """
    global _vs, _splitter, _ROLE
    if _vs and _splitter:
        return _vs

    _ROLE = str(role_id)

    # 1) 保证 pgvector 扩展 / 表存在
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
        if hasattr(PGVector, "initialize"):
            try:
                PGVector.initialize(pg_url, collection_name=TABLE)
            except ProgrammingError as e:
                if "already exists" not in str(e):
                    raise

    # 2) 构造 PGVector
    embedder = ArkEmbedding(ark_client, ark_model_id)
    _vs = PGVector(connection_string=pg_url,
                   collection_name=TABLE,
                   embedding_function=embedder)
    _splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=0)

    _logger.info("PGVector ready  collection=%s  role=%s", TABLE, _ROLE)
    return _vs

# ───────────────────────────── 内部工具


def _clean(text: str) -> str:
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

# ───────────────────────────── 外部 API


def add_text(text: str) -> int:
    if not (_vs and _splitter):
        raise RuntimeError("lc_mem_store.init_store() 尚未调用")
    docs: list[Document] = []
    for seg in _splitter.split_text(_clean(text)):
        if len(seg) < 60:
            continue
        docs.append(Document(page_content=seg, metadata={"user_id": _ROLE}))
    if docs:
        _vs.add_documents(docs)
    _logger.debug("add_text() → %d segments", len(docs))
    return len(docs)


def similarity_search(query: str, *, k: int = 5):
    if not _vs:
        raise RuntimeError("lc_mem_store.init_store() 尚未调用")
    return _vs.similarity_search(query, k=k, filter={"user_id": _ROLE})


def clear_all(pg_url: str, role_id: str) -> int:
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        res = conn.execute(text("""
            DELETE FROM langchain_pg_embedding
            WHERE cmetadata->>'user_id' = :uid
        """), {"uid": role_id})
    return res.rowcount
