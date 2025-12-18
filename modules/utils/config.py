"""
공통 설정 및 초기화 모듈

PIL Image 설정, .env 로드, 프로젝트 루트 경로 계산 등
애플리케이션 전역 설정을 중앙에서 관리합니다.
"""

from pathlib import Path
from dotenv import load_dotenv
from PIL import Image, ImageFile

# PIL Image 설정 (DecompressionBombWarning 방지)
Image.MAX_IMAGE_PIXELS = None  # 제한 없음
ImageFile.LOAD_TRUNCATED_IMAGES = True  # 손상된 이미지도 로드 시도

# .env 파일 로드 (프로젝트 루트의 .env)
_env_loaded = False

def load_env():
    """프로젝트 루트의 .env 파일 로드 (한 번만 실행)"""
    global _env_loaded
    if not _env_loaded:
        env_path = get_project_root() / '.env'
        load_dotenv(env_path)
        _env_loaded = True

def get_project_root() -> Path:
    """
    프로젝트 루트 디렉토리 Path 반환
    
    Returns:
        프로젝트 루트 디렉토리 Path 객체
    """
    # 현재 파일 위치: modules/utils/config.py
    # modules/utils -> modules -> 프로젝트 루트
    current_file = Path(__file__).resolve()
    return current_file.parent.parent.parent

# 모듈 import 시 자동으로 .env 로드
load_env()

