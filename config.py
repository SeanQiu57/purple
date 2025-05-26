# ── config.py ───────────────────────────────────────────────────────────
# 只把易变的东西抽出来，主程序更干净
ARK_API_KEY = "8b4e1f4a-c8eb-46dd-9d69-47e248988770"        # 别暴露线上密钥
ARK_REGION = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
ARK_ENDPOINT = "ep-20250507225117-rrrkz"      # ep-20250507225117-rrrkz
PUBLIC_BASE_URL = "https://seangaming.site"     # 你的 https 域名，结尾不要斜杠
UPLOAD_FOLDER = '/var/www/seangaming/tmp'
TARGET_BYTES = 4_900_000                             # <=4.9 MB
MIN_QUALITY = 40
# Dify 固定地址，不再用 /files/upload
DIFY_CHAT_URL = "https://api.dify.ai/v1/chat-messages"