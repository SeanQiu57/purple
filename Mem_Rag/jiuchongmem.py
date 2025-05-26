# 稳定查询存储500字一chunk版本111
import os
import asyncio
import traceback
from datetime import datetime
from typing import List, Sequence, Optional
from urllib.parse import quote_plus
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from volcenginesdkarkruntime import Ark
from openai import OpenAI
from ..base import MemoryProviderBase, logger
from pgvector.psycopg2 import register_vector
import logging
import threading
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from langchain.text_splitter import RecursiveCharacterTextSplitter
import re
from .lc_mem_store import init_store, add_text, similarity_search, clear_all
# Constant tag for logging
TAG = "[JiuchongMem]"

# def search_long_memory(session: Session, role_id: str, emb: List[float], top_k: int = 3):
#     """
#     emb: 已经通过 self.embed_text(...) 得到的向量
#     返回 [(score, content, meta), ...]
#     """
#     sql = text("""
#     SELECT
#         embedding <-> (:emb)::vector AS score,
#         content
#     FROM memory_vec
#     WHERE user_id = :uid
#     ORDER BY score
#     LIMIT :k
#     """)
#     rows = session.execute(sql, {"emb": emb, "uid": str(role_id), "k": top_k}).fetchall()

#     if not rows:
#         logger.bind(tag=TAG).info("[LongMemory] no hits found")
#     else:
#         for idx, (score, content) in enumerate(rows, 1):
#             snippet = content.replace("\n", " ")[:200]
#             logger.bind(tag=TAG).info(
#                 f"[LongMemory] hit#{idx:02d} score={score:.4f} content='{snippet}…'"
#             )
#     return rows

# --------------------------------------
#  SQLAlchemy models
# --------------------------------------


class Base(DeclarativeBase):
    pass


class MemoryDoc(Base):
    __tablename__ = "memory_doc"
    id:        Mapped[int] = mapped_column(primary_key=True)
    user_id:   Mapped[str] = mapped_column(String)
    mem_type:  Mapped[str] = mapped_column(String)  # short / working
    content:   Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MemoryVec(Base):
    __tablename__ = "memory_vec"
    id:        Mapped[int] = mapped_column(primary_key=True)
    user_id:   Mapped[str] = mapped_column(String)
    embedding: Mapped[List[float]] = mapped_column(Vector(2560))
    content:   Mapped[str]
    meta:      Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# --------------------------------------
#  Hard‑coded DB creds → engine
# --------------------------------------
DB_USER = "postgres"
DB_PASSWORD = "sean"
DB_HOST = "127.0.0.1"
DB_PORT = "5432"
DB_NAME = "azi_db"
DB_URL = f"postgresql+psycopg2://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# --------------------------------------
#  Provider
# --------------------------------------


class MemoryProvider(MemoryProviderBase):
    provider_name = "jiuchongmem"

    def embed_text(self, text: str) -> List[float]:
        """
        用火山 Ark SDK 调用 embedding 模型，将单条文本转成向量。
        """
        try:
            # Ark embedding 接口，input 接受 list
            resp = self.ark_client.embeddings.create(
                model=str(self.EmbeddingID),
                input=[text]
            )
            # 返回首条结果的 embedding 向量
            return resp.data[0].embedding
        except Exception as e:
            logger.bind(tag=TAG).error(f"Embedding failed: {e}")
            # 容错返回全 0 向量
            return [0.0] * 2560

    def __init__(self, config: dict, summary_memory: str | None = None):
        super().__init__(config)
        self.SHORT_KEEP = int(config.get("short_keep", 20))
        self.LONG_BATCH = int(config.get("long_batch", 10))   # 每次弹出多少条
        self.EmbeddingID = config.get("embedding_model_id", 10)
        self.SECTION_SIZE = 1                 # 拆成两段存向量
        logger.info(f"[JiuchongMem] 使用的 role_id = {self.role_id!r}")
        # db
        try:
            self.engine = create_engine(DB_URL, pool_pre_ping=True)
            with self.engine.connect() as c:
                c.execute(text("SELECT 1"))
        except OperationalError as e:
            logger.error(f"DB connect failed: {e}")
            raise
        Base.metadata.create_all(self.engine)
        ark_key = config.get("ARK_API_KEY")
        model_id = config.get("MODEL_ENDPOINT")
        if not ark_key or not model_id:
            raise RuntimeError("ARK_API_KEY / MODEL_ENDPOINT 缺失")
        self.ark_client = Ark(api_key=ark_key)
        self.ARK_MODEL_ID = model_id
        # state
        self._turn = 0
        self._summary = summary_memory or ""

        # ------------------------ 初始化（框架在建立 WS/HTTP 连接时调用） ------------------------
    def init_memory(self, role_id, llm, **kwargs):
        super().init_memory(role_id, llm, **kwargs)

        if not self.role_id:
            raise RuntimeError("init_memory 未收到有效 role_id！")

        logger.info("[JiuchongMem] init_memory ⏩ 开始 init_store")
        try:
            init_store(
                pg_url=DB_URL,
                ark_client=self.ark_client,
                ark_model_id=self.EmbeddingID,
                chunk_size=500,
                role_id=self.role_id
            )
            logger.info("[JiuchongMem] init_store ✅ 成功返回")
        except Exception as e:
            logger.error(
                f"[JiuchongMem] init_store 出错, 跳过后续逻辑: {e}", exc_info=True)
            # 如果你希望哪怕失败也继续后面的调试服务，就不要在这儿 re-raise
        # 再启动调试 API
        try:
            logger.info("[JiuchongMem] _start_debug_api ⏩ 调用中…")
            self._start_debug_api()
            logger.info("[JiuchongMem] _start_debug_api ✅ 成功")
        except Exception as e:
            logger.error(f"[JiuchongMem] 启动调试 API 失败: {e}", exc_info=True)

        logger.info(f"[JiuchongMem] init_memory 完成，role_id={self.role_id}")

    def _clean_text(self, t: str) -> str:
        t = re.sub(r"-\s*\n\s*", "", t)              # 连字符断行
        t = re.sub(r"[•■▪︎●\u2022]+", " ", t)        # bullet
        t = re.sub(r"\bPage \d+ of \d+\b", " ", t, flags=re.I)
        t = re.sub(r"[\r\n\t]+", " ", t)             # 换行→空格
        t = re.sub(r"\s{2,}", " ", t)                # 压缩空格
        return t.strip()

    def _start_debug_api(self):
        """内联启动一个 FastAPI+uvicorn 服务，用来做内存调试"""
        app = FastAPI(
            title="九重Memory 调试",
            description="长短期记忆手动查询 / 批量导入",
            version="1.0.0",
        )
        static_dir = os.path.join(
            os.path.dirname(__file__),  # core/providers/memory/jiuchongmem
            "static"
        )
        app.mount(
            "/debug-ui",
            StaticFiles(directory=static_dir, html=True),
            name="debug-ui",
        )
        # 查询接口，直接返回 hits

        class QueryRequest(BaseModel):
            q: str

        @app.post("/memory/query")
        async def debug_query(req: QueryRequest):
            # # 1. 生成向量
            # emb = self.embed_text(req.q)
            # # 2. 检索 top_k（用你原来的 search_long_memory）
            # with Session(self.engine) as s:
            #     rows = search_long_memory(s, self.role_id, emb, top_k=5)
            # # 3. 组装成列表
            # hits = [{"score": float(r[0]), "content": r[1]} for r in rows]
            docs = similarity_search(req.q, k=5)
            hits = [{"content": d.page_content} for d in docs]
            return {"query": req.q, "hits": hits}

        # 批量导入接口：支持 text 或 texts，500 字符一段

        class ImportRequest(BaseModel):
            text:  Optional[str] = None
            texts: Optional[List[str]] = None

        @app.post("/memory/import")
        async def debug_import(req: ImportRequest):
            # 1) 合并所有输入为一个大块文本
            if req.text:
                full_text = req.text
            elif req.texts:
                # 用换行符串联多段
                full_text = "\n".join(req.texts)
            else:
                raise HTTPException(
                    status_code=422,
                    detail="请提供 text 或 texts 之一"
                )
            logger.debug(">>> 合并后待切分文本长度：%d", len(full_text))
            logger.debug(">>> 文本预览：%r", full_text[:200])
            # 2) 只传这一整段给 add_text
            added = add_text(full_text)
            return {"total_segments": added}
            # # 1) 清洗 + 按 500 字符拆分
            # segments = []
            # for raw in all_texts:
            #     clean = self._clean_text(raw)
            #     for i in range(0, len(clean), 500):
            #         seg = clean[i:i+500]
            #         if len(seg) < 60:     # 太短就丢弃
            #             continue
            #         segments.append(seg)

            # 2) SECTION_SIZE = 1，每段 500 字直接写向量
            # with Session(self.engine) as s:
            #     for idx, seg in enumerate(segments):
            #         emb = self.embed_text(seg)
            #         s.add(MemoryVec(
            #             user_id=str(self.role_id),
            #             embedding=emb,
            #             content=seg,
            #             meta={"seg": idx, "src": "import"}
            #         ))
            #     s.commit()

            # return {
            #     "total_segments": len(segments)
            # }
        @app.delete("/memory/clear")
        async def clear_memory():
            deleted = clear_all(DB_URL, str(self.role_id))
            return {"deleted_count": deleted}

        # 启动服务
        def run_uvicorn():
            uvicorn.run(app, host="0.0.0.0", port=8081, log_level="debug")

        t = threading.Thread(target=run_uvicorn, daemon=True)
        t.start()
        logger.info(f"{TAG} 调试 API 已启动 → http://<host>:8081/")

    async def _batch_import(self, texts: list[str]) -> int:
        """示例：把每段 text 分成 500 字，插入长期记忆库"""
        count = 0
        for text in texts:
            for i in range(0, len(text), 500):
                segment = text[i: i + 500]
                await self.save_memory(segment)
                count += 1
        return count
    # ------------------------ 查询 ------------------------

    async def query_memory(self, query: str) -> str:
        with Session(self.engine) as s:
            shorts = s.scalars(
                select(MemoryDoc.content)
                .where(MemoryDoc.user_id == str(self.role_id), MemoryDoc.mem_type == 'short')
                .order_by(MemoryDoc.id.desc())
                .limit(self.SHORT_KEEP)
            ).all()
            working = s.scalars(
                select(MemoryDoc.content)
                .where(MemoryDoc.user_id == str(self.role_id), MemoryDoc.mem_type == 'working')
                .order_by(MemoryDoc.id.desc())
            ).first()
            # 1) 用 Ark SDK 或者 self.embed_text 生成 query 向量
            emb = self.embed_text(query)
            # 2) 从 memory_vec 表里检索 top 3
            # long_hits =
            long_hits = ""
            # 3) 把命中内容拼进 prompt
            long_memory = "\n".join(f"- {hit[1]}" for hit in long_hits)
        # logger.info(f"[Memory] query_memory turn={self._turn} query={query} shorts={shorts} working={working}")
        # Construct a comprehensive memory prompt
        short_memory = "\n".join(shorts[::-1])  # Newest first
        memory_prompt = f"下面是输入：这是短期记忆-你和主人近期聊天记录\n{short_memory}\n\n"
        memory_prompt += f"### 工作记忆-你的性格，遐想和脑子里近期回荡的事情\n{working}\n\n"
        memory_prompt += f"### 当前主人说的话\n{query}"
        memory_prompt += f"\n\n### 长期记忆-以往的记忆碎片\n{long_memory}\n\n"
        logger.info(
            f"[Memory] Constructed memory prompt with {len(shorts)} short items")
        return memory_prompt

    # ------------------------ 写入 ------------------------
    async def save_memory(self, msgs: Sequence):
        """
        只保存「最新一轮」：1 条 user + 若干 assistant 片段。
        假设 msgs 顺序 = [..., user, assistant-seg1, assistant-seg2, ...]
        """
        # 找到最后一条 user
        last_user_idx = next(
            (i for i in range(len(msgs) - 1, -1, -1)
             if msgs[i].role == "user"), None
        )
        if last_user_idx is None:
            return  # 没有 user，不保存

        # 收集从该 user 到结尾的所有 assistant
        last_turn = msgs[last_user_idx:]
        qa_block = "\n".join(
            f"{m.role.capitalize()}: {m.content}" for m in last_turn
        )

        with Session(self.engine) as s:
            s.add(
                MemoryDoc(
                    user_id=str(self.role_id),
                    mem_type="short",
                    content=qa_block,
                )
            )
            s.commit()

            # 仅统计短期条数，超阈值再弹出
            short_cnt = s.scalar(
                select(func.count()).where(
                    MemoryDoc.user_id == str(
                        self.role_id), MemoryDoc.mem_type == "short"
                )
            )
        if short_cnt > self.SHORT_KEEP:
            asyncio.create_task(self._async_consolidate())

    # ------------------------ 后台摘要 + 弹出 ------------------------
    def _consolidate_sync(self):
        try:
            with Session(self.engine) as s:
                shorts_all = s.scalars(
                    select(MemoryDoc)
                    .where(MemoryDoc.user_id == str(self.role_id), MemoryDoc.mem_type == 'short')
                    .order_by(MemoryDoc.id.asc())
                ).all()

                pop_rows = shorts_all[: self.LONG_BATCH] if len(
                    shorts_all) > self.SHORT_KEEP else []
                remaining_short = shorts_all[len(pop_rows):]

                # save to vec, two sections
                for idx, start in enumerate(range(0, len(pop_rows), self.SECTION_SIZE)):
                    section = pop_rows[start:start+self.SECTION_SIZE]
                    if not section:
                        continue
                    text_block = "\n".join(r.content for r in section)
                    s.add(MemoryVec(
                        user_id=str(self.role_id),
                        embedding=self.embed_text(text_block),
                        content=text_block,
                        meta={"section": idx, "source": "short_pop"}
                    ))
                for r in pop_rows:
                    s.delete(r)
                s.commit()

                # build prompt and get summary
                short_block = "\n".join(
                    r.content for r in remaining_short[-self.SHORT_KEEP:])
                user_query = (
                    f"""### 请根据：
                    ，也即检索文献中与 SDT（自我决定理论）相关的实践研究或理论章节，并根据——用户和阿紫之间对话中用户遇到的问题或困惑……
                    当前短期记忆\n{short_block}\n\n###，迭代更新上一次的 旧工作记忆\n{self._summary}\n\n：
                    输出要求如下：
                    仅基于 SDT 理论进行分析，不可引入其他理论或主观扩展，保持精简，用最少的字蕴含最多的信息
                    仅当用户在对话中暴露出具体问题或心理机制时，才生成对应解释；若无明确问题，可不输出；
                    输出格式必须为如下结构化编号形式：
                    理论解释一：
                    理论解释二：
                    理论依据：请输出新的<工作记忆>："""
                )
                resp = self.ark_client.chat.completions.create(
                    model=self.ARK_MODEL_ID,
                    messages=[
                        {"role": "system", "content": "你正在根据记忆进行反思"},
                        {"role": "user",  "content": user_query},
                    ],
                )
                self._summary = resp.choices[0].message.content.strip()
                # ① 先删除旧工作记忆
                s.execute(
                    text(
                        "DELETE FROM memory_doc "
                        "WHERE user_id = :uid AND mem_type = 'working'"
                    ),
                    {"uid": str(self.role_id)},
                )
                # ② 再插入新的工作记忆
                s.add(
                    MemoryDoc(
                        user_id=str(self.role_id),
                        mem_type="working",
                        content=self._summary,
                    )
                )
                s.commit()
            logger.info(f"[Memory] consolidate done for {self.role_id}")
        except Exception as e:
            logger.exception(
                f"[Memory] consolidate crash for {self.role_id}: {e}\n" + traceback.format_exc())

    async def _async_consolidate(self):
        await asyncio.to_thread(self._consolidate_sync)
