"""
Database singleton registry

Provides a single `DatabaseManager` instance for the application to reuse.
"""
import os
from .db_manager import DatabaseManager


# 전역 DB 인스턴스 (애플리케이션 스코프, Streamlit single-process에 적합)
_APP_DB = DatabaseManager(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '5432')),
    database=os.getenv('DB_NAME', 'rebate_db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', ''),
    min_conn=int(os.getenv('DB_MIN_CONN', '1')),
    max_conn=int(os.getenv('DB_MAX_CONN', '10'))
)


def get_db() -> DatabaseManager:
    """애플리케이션 전역 DatabaseManager 인스턴스 반환"""
    return _APP_DB


def close_db() -> None:
    """애플리케이션 종료 시 연결 풀 닫기(선택적 호출)"""
    try:
        _APP_DB.close()
    except Exception:
        pass


