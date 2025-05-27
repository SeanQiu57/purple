import json
import queue
import threading
import re
import requests
from typing import List, Tuple

# ---------------- Configuration ----------------
EMOTION_API_KEY = "app-eR0rcpr1ifV7LoUzLtmdEXEU"
API_URL = "https://api.dify.ai/v1/chat-messages"
USER_ID = "emotion-broker"

# 默认当解析失败时使用
DEFAULT_FACES: List[int] = [4]
DEFAULT_ACTS: List[int] = [2]

# 正则用于快速定位行前缀（并不在 _parse 中使用——那边直接 findall）
FACE_PREFIX = "Face%@%"
ACT_PREFIX = "Act%@%"


class EmotionController(threading.Thread):
    """独立线程：
    1. 接收 (prompt, ws) 元组，排队
    2. 调用 Dify 生成情绪序列
    3. 解析出 faces / acts 列表
    4. 统一以  {label:"emotion", faces:[], acts:[]}  JSON 发送回前端
    """

    def __init__(self,
                 api_key: str = EMOTION_API_KEY,
                 api_url: str = API_URL,
                 user_id: str = USER_ID,
                 daemon: bool = True):
        super().__init__(daemon=daemon)
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.api_url = api_url
        self.user_id = user_id
        self._q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.start()

    # ------------  PUBLIC  -------------
    def submit(self, prompt: str, ws):
        """供主线程调用；只做排队，线程安全"""
        self._q.put((prompt, ws))

    # ------------  THREAD LOOP  --------
    def run(self):
        while True:
            prompt, ws = self._q.get()
            try:
                faces, acts = self._get_sequence(prompt)
            except Exception as e:
                print("[EmotionController] fallback default:", e)
                faces, acts = DEFAULT_FACES[:], DEFAULT_ACTS[:]

            payload = json.dumps({
                "label": "emotion",
                "faces": faces,
                "acts":  acts
            })
            try:
                ws.send(payload)
            except Exception as e:
                print("[EmotionController] ws.send failed:", e)

    # ------------  INTERNAL  -----------
    def _get_sequence(self, prompt: str) -> Tuple[List[int], List[int]]:
        raw_answer = self._call_dify(prompt)
        return self._parse(raw_answer)

    def _call_dify(self, prompt: str) -> str:
        body = {
            "query": prompt,
            "inputs": {},
            "user": self.user_id,
            "response_mode": "blocking",
        }
        resp = requests.post(self.api_url, headers=self.headers,
                             json=body, timeout=90)
        resp.raise_for_status()
        return resp.json().get("answer", "").strip()

    # -------------------------------------------------------
    #  Robust parser: 提取两行中的所有数字，自动补齐 / 校验范围
    # -------------------------------------------------------
    def _parse(self, answer: str) -> Tuple[List[int], List[int]]:
        face_seq: List[int] | None = None
        act_seq:  List[int] | None = None

        for line in (ln.strip() for ln in answer.splitlines() if ln.strip()):
            if line.startswith(FACE_PREFIX):
                face_seq = [int(n) for n in re.findall(r"\d+", line)]
            elif line.startswith(ACT_PREFIX):
                act_seq = [int(n) for n in re.findall(r"\d+", line)]

        # ---------- fallback ----------
        if not face_seq:
            face_seq = DEFAULT_FACES[:]
        if not act_seq:
            act_seq = DEFAULT_ACTS[:]

        # ---------- length align ----------
        diff = len(face_seq) - len(act_seq)
        if diff > 0:
            act_seq.extend([DEFAULT_ACTS[0]] * diff)
        elif diff < 0:
            face_seq.extend([DEFAULT_FACES[0]] * (-diff))

        # ---------- sanity check ----------
        if (not all(0 <= f <= 7 for f in face_seq) or
                not all(2 <= a <= 8 for a in act_seq) or
                not 1 <= len(face_seq) <= 5):
            raise ValueError(f"非法编号或数量：faces={face_seq} / acts={act_seq}")

        return face_seq, act_seq
