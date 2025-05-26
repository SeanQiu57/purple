# 主函数，负责主要的记忆逻辑，如果不依赖小智模块；
# 里面有的逻辑可以移到函数封装脚本里面，配置火山等也可以移动到另外的脚本
import os
import re
import asyncio
import traceback
from datetime import datetime
from typing import List, Sequence
from urllib.parse import quote_plus
from sqlalchemy import (
    create_engine, select, func, text, String, exc as sa_exc
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from volcenginesdkarkruntime import Ark
from ..base import MemoryProviderBase, logger          # ↖ 你的框架基类
from .lc_mem_store import (                            # ☆ 仅这一行 import
    init_store, add_text, similarity_search
)
import threading
import uvicorn

TAG = "[JiuchongMem]"

# ────────────────────────  SQLAlchemy Base ────────────────────────


class Base(DeclarativeBase):
    ...


class MemoryDoc(Base):
    __tablename__ = "memory_doc"
    id:        Mapped[int] = mapped_column(primary_key=True)
    user_id:   Mapped[str] = mapped_column(String)
    mem_type:  Mapped[str] = mapped_column(String)     # short / working
    content:   Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

# （可选）MemoryVec 仍保留；如果已全部用 pgvector 可直接删掉


class MemoryVec(Base):
    __tablename__ = "memory_vec"
    id:        Mapped[int] = mapped_column(primary_key=True)
    user_id:   Mapped[str] = mapped_column(String)
    embedding: Mapped[List[float]] = mapped_column(Vector(2560))
    content:   Mapped[str]
    meta:      Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# ────────────────────────  常量配置 ────────────────────────
DB_URL = (
    "postgresql+psycopg2://postgres:"
    f"{quote_plus('sean')}@127.0.0.1:5432/azi_db"
)

# ────────────────────────  Provider 核心 ────────────────────────


class MemoryProvider(MemoryProviderBase):
    provider_name = "jiuchongmem"
    _debug_api_started = False

    @classmethod
    def _ensure_debug_api(cls):
        if cls._debug_api_started:
            return
        from .jiuchongmem_debug_api import app as _debug_app

        def _run():
            uvicorn.run(
                _debug_app,
                host="0.0.0.0",
                port=8081,
                log_level="debug",
                access_log=False
            )

        t = threading.Thread(
            target=_run,
            daemon=True,
            name="JiuchongMemDebugAPI"
        )
        t.start()
        cls._debug_api_started = True

    def __init__(
        self,
        config: dict,
        summary_memory: str | None = None
    ):
        super().__init__(config)
        self.SHORT_KEEP = int(config.get("short_keep", 20))
        self.LONG_BATCH = int(config.get("long_batch", 10))
        self.EmbeddingID = config.get("embedding_model_id", 10)
        self.SECTION_SIZE = 1
        self._summary = summary_memory or ""

        # 本地 DB：短/工作记忆
        self.engine = create_engine(DB_URL, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)

        # Ark 客户端
        ark_key = config.get("ARK_API_KEY")
        model_id = config.get("MODEL_ENDPOINT")
        if not ark_key or not model_id:
            raise RuntimeError("ARK_API_KEY / MODEL_ENDPOINT 缺失")
        self.ark_client = Ark(api_key=ark_key)
        self.ARK_MODEL_ID = model_id

        # 自动启动 Debug API 服务
        self._ensure_debug_api()

    def init_memory(self, role_id, llm, **kwargs):
        super().init_memory(role_id, llm, **kwargs)
        init_store(
            pg_url=DB_URL,
            ark_client=self.ark_client,
            ark_model_id=self.EmbeddingID,
            chunk_size=500,
            role_id=self.role_id,
        )
        logger.info(f"{TAG} init_store ✅ role_id={self.role_id}")

    # ---------- query_memory ----------
    async def query_memory(self, query: str) -> str:
        """拼 prompt：短记忆 + 工作记忆 + 长记忆(similarity_search)"""
        with Session(self.engine) as s:
            shorts = s.scalars(
                select(MemoryDoc.content)
                .where(MemoryDoc.user_id == str(self.role_id),
                       MemoryDoc.mem_type == 'short')
                .order_by(MemoryDoc.id.desc())
                .limit(self.SHORT_KEEP)
            ).all()
            working = s.scalars(
                select(MemoryDoc.content)
                .where(MemoryDoc.user_id == str(self.role_id),
                       MemoryDoc.mem_type == 'working')
                .order_by(MemoryDoc.id.desc())
            ).first()

        # ☆ 长期记忆检索
        long_docs = similarity_search(query, k=3)
        long_memory = "\n".join(f"- {d.page_content}" for d in long_docs)

        short_memory = "\n".join(shorts[::-1])
        prompt = (
            f"【短期记忆】\n{short_memory}\n\n"
            f"【工作记忆】\n{working}\n\n"
            f"【当前输入】\n{query}\n\n"
            f"【长期记忆】\n{long_memory}\n"
        )
        return prompt

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
