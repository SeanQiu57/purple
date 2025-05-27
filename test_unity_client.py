# test_unity_client.py

import websocket
import threading
import json
import time
import sys

# 服务器地址，根据实际情况修改
WS_URL = "ws://127.0.0.1:5001/vad_asr"

def on_message(ws, message):
    print(f"[Server] {message}")

def on_error(ws, error):
    print(f"[Error] {error}")

def on_close(ws, close_status_code, close_msg):
    print("[Connection closed]")
    # 退出程序
    sys.exit(0)

def on_open(ws):
    print("[Connected to server]")
    # 启动一个后台线程来读取命令行输入并发送
    def run_input_loop():
        while True:
            try:
                text = input()
            except EOFError:
                break
            if not text:
                continue
            payload = {
                "label": "text_input",
                "text": text
            }
            ws.send(json.dumps(payload, ensure_ascii=False))
            print(f"[Client] Sent: {text}")
        ws.close()
    threading.Thread(target=run_input_loop, daemon=True).start()

if __name__ == "__main__":
    # 避免因证书问题导致的报错（如果使用 wss://）
    websocket.enableTrace(False)
    ws_app = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    # run_forever 会阻塞当前线程，直到连接关闭
    ws_app.run_forever()
