import atexit
import os, uuid, time, base64, requests, logging
from io import BytesIO
from PIL import Image
from apscheduler.schedulers.gevent import GeventScheduler   # ← 重点
from typing import Tuple
from config import (
    UPLOAD_FOLDER, PUBLIC_BASE_URL,
    TARGET_BYTES, MIN_QUALITY,
    ARK_API_KEY,  ARK_REGION, ARK_ENDPOINT
)

log = logging.getLogger("vision")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ────────── 清理 uploads/ ──────────
_sched = GeventScheduler()

# ─────────────────── 清理 uploads/ ───────────────────


def _clear_upload_folder():
    now = time.time()
    for fn in os.listdir(UPLOAD_FOLDER):
        p = os.path.join(UPLOAD_FOLDER, fn)
        try:
            if os.path.isfile(p) and now - os.path.getmtime(p) > 30:
                os.remove(p)
                log.debug("🗑️  rm %s", p)
        except Exception as e:
            log.warning("rm %s fail: %s", p, e)

def start_cleanup_scheduler():
    _sched.add_job(_clear_upload_folder, "interval", seconds=120)
    _sched.start()
    atexit.register(lambda: _sched.shutdown(wait=False))

# ─────────────────── 基础步骤封装 ───────────────────
def build_prompt():
    mem_dir = os.path.join(os.getcwd(), "voicememory")
    os.makedirs(mem_dir, exist_ok=True)
    # fallback to Dify
    epi = open(os.path.join(mem_dir, "Episodicmemory.txt"), "r", encoding="utf-8").read() if os.path.exists(os.path.join(mem_dir, "Episodicmemory.txt")) else ""
    short = open(os.path.join(mem_dir, "voicememory.txt"), "r", encoding="utf-8").read() if os.path.exists(os.path.join(mem_dir, "voicememory.txt")) else ""
    prompt = f"非常详细而且富有创造力的描述图片的内容，对看到的人脸需要做详细描述，可以发挥想象和创意里面都有什么"
    return prompt
def _decode_and_resize(b64: str) -> Tuple[bytes, str]:
    """Base64 → 压缩 JPEG → 写 uploads/ → 返回 (bytes, public_url)"""
    _, encoded = (b64.split(",", 1) if b64.startswith("data:") else (None, b64))
    raw = base64.b64decode(encoded)
    img = Image.open(BytesIO(raw)).convert("RGB")

    quality = 90
    buf = BytesIO()
    while quality >= MIN_QUALITY:
        buf.truncate(0); buf.seek(0)
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= TARGET_BYTES:
            break
        quality -= 10
    else:
        raise RuntimeError("image too large even @40%")

    fname = f"{uuid.uuid4()}.jpg"
    path  = os.path.join(UPLOAD_FOLDER, fname)
    with open(path, "wb") as f:
        f.write(buf.getvalue())

    url = f"{PUBLIC_BASE_URL}/uploads/{fname}"
    log.info("📸 saved %s (%d KB)", fname, buf.tell()//1024)
    return buf.getvalue(), url

def _ask_vision_llm(public_url: str) -> str:
    """调用 Ark/Dify，返回纯文本答案"""
    prompt = build_prompt()
    payload = {
        "model": ARK_ENDPOINT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type":"text","text":prompt},
                    {"type":"image_url","image_url":{"url": public_url}}
                ]
            }
        ]
    }
    r = requests.post(
        ARK_REGION,
        headers={"Authorization": f"Bearer {ARK_API_KEY}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=180
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# ─────────────────── 对外主函数 ───────────────────
def process_base64_image(b64: str) -> str:
    """
    • 写 uploads/
    • 调 vision LLM
    • 返回 text answer
    """
    _, url = _decode_and_resize(b64)
    answer = _ask_vision_llm(url)
    return answer
