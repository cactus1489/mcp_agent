import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"

def setup_logging(level: int = logging.INFO) -> None:
    """애플리케이션의 공통 로깅 설정 초기화"""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # uvicorn 재실행 등으로 핸들러 중복 등록 방지
    if root_logger.handlers:
        return

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """모듈 이름 사용하는 로거 반환"""
    return logging.getLogger(name)