from fastapi import FastAPI
from pydantic import BaseModel
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)

app = FastAPI(title="Desktop-Pet Tool Receiver")

# ── 数据模型 ──────────────────────────────────────────
class PlayMusicReq(BaseModel):
    identifier: str
    name: str
    is_exact: str   # exact / fuzzy
    song_type: str  # album / single / playlist

class ScreenshotReq(BaseModel):
    identifier: str
    camera_type: str  # screen / camera

class RecitePoemReq(BaseModel):
    topic: str

# ── 路由 ──────────────────────────────────────────────
@app.post("/tools/play_music")
async def play_music(req: PlayMusicReq):
    logging.info(f"[play_music] {req.json()}")
    return {"status": "queued"}          # 先只回 OK

@app.post("/tools/screenshot")
async def screenshot(req: ScreenshotReq):
    logging.info(f"[screenshot] {req.json()}")
    return {"status": "queued"}

@app.post("/tools/recite_poem")
async def recite_poem(req: RecitePoemReq):
    logging.info(f"[recite_poem] {req.json()}")
    return {"status": "queued"}

# ── main ─────────────────────────────────────────────
# 终端运行：
#   uvicorn server:app --host 0.0.0.0 --port 8000 --workers 2
