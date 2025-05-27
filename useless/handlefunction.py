# playmusic_api.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ① 定义请求体
class PlayMusicIn(BaseModel):
    musicname: str

app = FastAPI(title="PlayMusic local API")

# ② 依赖注入你的 WebSocket 管理器（示例用全局列表）
from vad_asr import WS_POOL   # ⇦ 你在 vad_asr.py 里维护的连接集合

def broadcast_to_ws(payload: dict):
    for ws in list(WS_POOL):          # 复制一份，避免遍历时被移除
        try:
            ws.send_json(payload)     # gevent-websocket 的 send() 也行
        except Exception:
            WS_POOL.discard(ws)

# ③ 实际接口
@app.post("/playmusic", summary="播放指定歌曲")
def playmusic(data: PlayMusicIn):
    if not data.musicname.strip():
        raise HTTPException(400, "musicname 不能为空")

    payload = {
        "label": "function",
        "name":  "playmusic",
        "arguments": {"musicname": data.musicname}
    }
    broadcast_to_ws(payload)
    return {"status": "ok"}
