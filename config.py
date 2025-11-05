 # 环境变量读取（OpenAI、S2、RPS等）
# paper_survey/config.py
import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")

S2_API_KEY = os.getenv("S2_API_KEY") or os.getenv("SEMANTIC_SCHOLAR_API_KEY") or ""
S2_RPS = float(os.getenv("S2_RPS", "0.5"))  # 无 key 默认更保守
S2_BASE = "https://api.semanticscholar.org/graph/v1"

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
