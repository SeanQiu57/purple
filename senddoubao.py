import websocket
import gzip
import json
import struct
import uuid

# ====== 配置 ======
WS_URL    = "wss://openspeech.bytedance.com/api/v2/asr"   # 控制台提供的 WS 地址
APPID     = "4911487570"                                 # 你的 AppID
TOKEN     = "zpAFYBQB7Nuy0stP6jPITrXPvCtN7VF4"           # 你的 Token
AUTH_HDR  = f"Authorization: Bearer; {TOKEN}"            # 握手鉴权头
UID       = "user123"                                    # 任意非空的用户/设备标识

# 音频参数
RATE     = 16000    # 采样率
BITS     = 16       # 采样位数
CHANNEL  = 1        # 声道数
FORMAT   = "wav"    # 容器格式
CODEC    = "raw"    # 编码格式
CHUNK_MS = 200      # 每包时长（毫秒）


def make_header(version: int, hdr_size: int, msg_type: int,
                flags: int, serialization: int, compression: int) -> bytes:
    b0 = ((version & 0xF) << 4) | (hdr_size & 0xF)
    b1 = ((msg_type  & 0xF) << 4) | (flags     & 0xF)
    b2 = ((serialization & 0xF) << 4) | (compression & 0xF)
    return bytes([b0, b1, b2, 0x00])


def build_full_request(reqid: str) -> bytes:
    body = {
        "app":     {"appid": APPID, "token": TOKEN, "cluster": "volcengine_input_common"},
        "user":    {"uid": UID},
        "audio":   {"format": FORMAT, "codec": CODEC, "rate": RATE, "bits": BITS, "channel": CHANNEL},
        "request": {"reqid": reqid, "sequence": 1, "nbest": 1, "show_utterances": True}
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    comp = gzip.compress(raw)
    hdr  = make_header(1, 1, 1, 0, 1, 1)  # JSON+Gzip
    size = struct.pack(">I", len(comp))
    return hdr + size + comp


def build_audio_request(chunk: bytes, seq: int, last: bool) -> bytes:
    comp  = gzip.compress(chunk)
    flags = 0b0010 if last else 0b0000
    hdr   = make_header(1, 1, 2, flags, 0, 1)  # raw+Gzip
    size  = struct.pack(">I", len(comp))
    return hdr + size + comp


def asr_once(path: str) -> str:
    """
    一个文件一句话识别：打开 WS，发 Full Req + Audio Req，等待最终 Result。
    返回识别出的文本。
    """
    data = open(path, "rb").read()

    # 1. 建立 WS 连接并鉴权
    ws = websocket.create_connection(WS_URL, header=[AUTH_HDR])

    # 2. 发送 Full Client Request
    reqid = uuid.uuid4().hex
    ws.send(build_full_request(reqid), opcode=websocket.ABNF.OPCODE_BINARY)

    # 3. 分包发送音频
    per_byte = RATE * CHANNEL * (BITS//8) // 1000
    chunk   = per_byte * CHUNK_MS
    off, seq = 0, 1
    while off < len(data):
        end = min(off + chunk, len(data))
        last = (end == len(data))
        ws.send(build_audio_request(data[off:end], seq, last),
                opcode=websocket.ABNF.OPCODE_BINARY)
        off += chunk
        seq += 1

    # 4. 接收直到最终包 (sequence<0 && code==1000)
    result = ""
    while True:
        msg = ws.recv()
        # 如果是二进制
        if isinstance(msg, (bytes, bytearray)) and len(msg) >= 8:
            size    = struct.unpack(">I", msg[4:8])[0]
            payload = msg[8:8+size]
            raw     = gzip.decompress(payload)
            resp    = json.loads(raw.decode("utf-8", errors="ignore"))
            if resp.get("code")==1000 and resp.get("sequence",0)<0:
                result = resp["result"][0]["text"]
                break
        # 否则跳过
    ws.close()
    return result
