import os, json, threading, logging, time, uuid, wave, concurrent.futures
from collections import deque
from typing import List, Set
from gevent import monkey; monkey.patch_all()
from geventwebsocket import WebSocketApplication, Resource, WebSocketServer
import numpy as np
import onnxruntime
from senddoubao import asr_once
from memorymanager import append_memory, append_vision_memory
from camelfunc import handle_user_text
import base64, requests, atexit
from io import BytesIO
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image
from vision_utils import process_base64_image, start_cleanup_scheduler
from llm_client import chat_with_dify
# Globals
WS_POOL: Set[WebSocketApplication] = set()
EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# VAD
_RATE = 16000; _MAX_WAV = 32767; _ONNX_PATH = "silero_vad_16k.onnx"
_CONTEXT_SIZE = 128; _CHUNK_SAMPLES = 512; _CHUNK_BYTES = _CHUNK_SAMPLES * 2
_SILENCE_THRESHOLD = 1.5; WINDOW_SIZE = 10; MIN_VOICE_FRAMES = 6

class SileroVAD:
    _shared_session = None
    def __init__(self, prob_threshold=0.6):
        self.prob_threshold = prob_threshold
        self._context = np.zeros((1, _CONTEXT_SIZE), dtype=np.float32)
        self._state   = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr      = np.array(_RATE, dtype=np.int64)
    @property
    def session(self):
        if not SileroVAD._shared_session:
            opts=onnxruntime.SessionOptions(); opts.intra_op_num_threads=opts.inter_op_num_threads=1
            SileroVAD._shared_session=onnxruntime.InferenceSession(_ONNX_PATH, providers=["CPUExecutionProvider"], sess_options=opts)
        return SileroVAD._shared_session
    def is_speech(self,audio:bytes)->bool:
        if len(audio)!=_CHUNK_BYTES: return False
        pcm=np.frombuffer(audio,dtype=np.int16).astype(np.float32)/_MAX_WAV
        inp=np.concatenate((self._context,pcm[np.newaxis,:]),axis=1)
        self._context=inp[:,-_CONTEXT_SIZE:]
        out,self._state=self.session.run(None,{"input":inp[:,:_CHUNK_SAMPLES+_CONTEXT_SIZE],"state":self._state,"sr":self._sr})
        return bool(out.squeeze()>=self.prob_threshold)

# Save wav

def save_frames_to_wav(frames: List[bytes]) -> str:
    tmpdir="tmp"; os.makedirs(tmpdir,exist_ok=True)
    for f in os.listdir(tmpdir): os.remove(os.path.join(tmpdir,f))
    path=os.path.join(tmpdir,f"asr_{uuid.uuid4().hex}.wav")
    with wave.open(path,"wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(_RATE)
        wf.writeframes(b"".join(frames))
    return path

# ASR callback

def _asr_callback(fut, path, ws):
    try:
        user_text=fut.result(); logger.info(f"[ASR] {user_text}")
        handle_user_text(user_text, ws)
    except Exception as e:
        logger.error(f"[ASR CALLBACK ERROR] {e}", exc_info=True)

# WebSocket app
class VADASRApp(WebSocketApplication):
    def on_open(self):
        WS_POOL.add(self.ws); self.vad=SileroVAD(); self.is_talking=False
        self.buffer=[]; self.silent_start=None; self.window=deque(maxlen=WINDOW_SIZE)
        logger.info("✅ 客户端连接，开始监听音频")
    def on_message(self,msg):
        if msg is None:
            WS_POOL.discard(self.ws); logger.info("Connection closed"); return
        if isinstance(msg,str):
            try: obj=json.loads(msg); label=obj.get("label")
            except: label=None
            if label == "screenshot_result":
                b64img = obj.get("image")
                if not b64img:
                    self.ws.send(json.dumps({"label":"error","error":"empty image"}))
                    return

                def _run():
                    try:
                        answer = process_base64_image(b64img)
                        append_vision_memory("voicememory", "我看到的图片内容", answer)
                        self.ws.send(json.dumps({"label":"chat","reply":"我看到了图片了！"}))
                        logger.info("🖼️ 已保存图像记忆=%s", answer)
                        mem_dir = os.path.join(os.getcwd(), "voicememory")
                        vision_path = os.path.join(mem_dir, "visionmemory.txt")
                        visionmem = open(vision_path, "r", encoding="utf-8").read() if os.path.exists(vision_path) else ""
                        chat_with_dify(f"$@$系统通知，非聊天：你刚才看到了：{visionmem}，结合之前的记忆回答聊天", user_id="user123", ws=self.ws)
                    except Exception as e:
                        logger.error("vision fail: %s", e, exc_info=True)
                        self.ws.send(json.dumps({"label":"error","error":str(e)}))

                EXECUTOR.submit(_run)
                return
            if label=="history_request":
                path="voicememory/voicememory.txt"; lines=[]
                if os.path.exists(path): lines=[l.strip() for l in open(path,encoding="utf-8")]
                self.ws.send(json.dumps({"label":"history","content":"\n".join(lines)}))
                return
            if label=="text_input":
                user_text=obj.get("text","" ).strip(); logger.info(f"[on_message] text_input: {user_text}")
                if user_text:
                    logger.info("[on_message] submitting handle_user_text")
                    EXECUTOR.submit(handle_user_text, user_text, self.ws)
                return
            return
        has_voice=self.vad.is_speech(msg); self.window.append(has_voice)
        speaking=sum(self.window)>=MIN_VOICE_FRAMES
        if speaking:
            if not self.is_talking:
                self.is_talking=True; self.buffer.clear(); self.silent_start=None
                logger.info(">>> start speaking"); self.ws.send(json.dumps({"label":"start"}))
            self.buffer.append(msg)
        else:
            if self.is_talking:
                if not self.silent_start: self.silent_start=time.time()
                self.buffer.append(msg)
                if time.time()-self.silent_start>=_SILENCE_THRESHOLD:
                    self.is_talking=False; logger.info("<<< end speaking, ASR")
                    self.ws.send(json.dumps({"label":"finish"}))
                    path=save_frames_to_wav(self.buffer)
                    fut=EXECUTOR.submit(asr_once,path)
                    fut.add_done_callback(lambda f:_asr_callback(f,path,self.ws))
                    self.buffer.clear(); self.silent_start=None
    def on_close(self,reason):
        WS_POOL.discard(self.ws); logger.info(f"⚠️ 关闭: {reason}")

if __name__=='__main__':
    logger.info("🚀 服务启动：:5001")
    # start_cleanup_scheduler()     
    server=WebSocketServer(('0.0.0.0',5001),Resource({'/vad_asr':VADASRApp}))
    server.serve_forever()