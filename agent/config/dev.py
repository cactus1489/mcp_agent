"""Development settings loaded from the project root .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# OS에 이미 설정된 환경변수는 유지한다.
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    # 프로파일 및 서버 기동 설정
    profile = os.getenv("profile", "dev")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8021"))

    # 로깅 설정
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_dir = os.getenv("LOG_DIR", "logs")

    # 에이전트 및 MCP 연결에 필요한 필수 설정
    agent_api_key = os.environ["AGENT_API_KEY"]
    mcp_api_key = os.environ["MCP_API_KEY"]
    mcp_servers = os.environ["MCP_SERVERS"]

    # LLM 연결에 필요한 필수 설정
    llm_base_url = os.environ["LLM_BASE_URL"]
    # llm_key = os.environ["LLM_KEY"]
    llm_model = os.environ["LLM_MODEL"]


settings = Settings()
