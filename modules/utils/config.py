"""
공통 설정 및 초기화 모듈

PIL Image 설정, .env 로드, 프로젝트 루트 경로 계산 등
애플리케이션 전역 설정을 중앙에서 관리합니다.
"""

from pathlib import Path
from dotenv import load_dotenv
from PIL import Image, ImageFile
from dataclasses import dataclass

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


@dataclass
class RAGConfig:
    """
    RAG 파싱 설정값 클래스
    
    이 클래스의 값만 수정하면 전체 애플리케이션의 설정이 변경됩니다.
    """
    # PDF 변환 설정
    dpi: int = 300  # PDF를 이미지로 변환할 때의 DPI
    
    # RAG 검색 설정
    top_k: int = 3  # 벡터 DB에서 검색할 예제 수
    similarity_threshold: float = 0.7  # 최소 유사도 임계값 (0.0 ~ 1.0)
    
    # OpenAI 모델 설정
    openai_model: str = "gpt-4o-2024-08-06"  # 사용할 OpenAI 모델명
    
    # RAG 질문 텍스트
    question: str = "이 청구서의 상품별 내역을 JSON으로 추출해라"
    
    # 병렬 처리 설정
    max_parallel_workers: int = 1  # Upstage OCR 병렬 워커 수 (1 = 순차 처리, Rate limit 방지)
    rag_llm_parallel_workers: int = 3  # RAG+LLM 병렬 워커 수 (OpenAI는 병렬 처리 가능)


# 전역 설정 인스턴스 (이 값을 수정하면 전체 애플리케이션에 적용됨)
rag_config = RAGConfig()


def get_rag_config() -> RAGConfig:
    """
    RAG 설정값 반환
    
    Returns:
        RAGConfig 인스턴스
    """
    return rag_config

