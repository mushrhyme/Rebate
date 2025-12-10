"""
Vision Parser 모듈: Gemini Vision API를 사용한 이미지 파싱
기존 gemini_extractor.py의 GeminiVisionParser를 재사용
"""

import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# src 디렉토리를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from gemini_extractor import (
    GeminiVisionParser as BaseGeminiVisionParser,
    extract_pages_with_gemini
)

__all__ = ['VisionParser']


class VisionParser:
    """
    Gemini Vision API를 사용하여 PDF를 파싱하는 클래스
    기존 gemini_extractor.py의 함수들을 래핑
    """
    
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3-pro-preview"):
        """
        Args:
            api_key: Google Gemini API 키
            model_name: 사용할 Gemini 모델 이름
        """
        self.api_key = api_key
        self.model_name = model_name
    
    def parse_pdf(
        self,
        pdf_path: str,
        dpi: int = 300,
        use_cache: bool = True,
        cache_path: Optional[str] = None,
        save_images: bool = True,
        image_output_dir: Optional[str] = None,
        use_history: bool = True,
        history_dir: Optional[str] = None
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """
        PDF 파일을 Gemini로 분석하여 페이지별 JSON 결과 반환
        
        Args:
            pdf_path: PDF 파일 경로 (임시 파일일 수 있음)
            dpi: PDF 변환 해상도 (기본값: 300)
            use_cache: 캐시 사용 여부
            cache_path: 캐시 파일 경로 (None이면 pdf_path 기반으로 자동 생성, 원본 파일명 기반 경로를 전달하는 것을 권장)
            save_images: 이미지를 파일로 저장할지 여부 (기본값: True)
            image_output_dir: 이미지 저장 디렉토리 (None이면 자동 생성)
            use_history: 히스토리 관리 사용 여부 (기본값: True)
            history_dir: 히스토리 디렉토리 (None이면 자동 생성)
            
        Returns:
            (페이지별 Gemini 파싱 결과 JSON 리스트, 이미지 파일 경로 리스트) 튜플
        """
        return extract_pages_with_gemini(
            pdf_path=pdf_path,
            gemini_api_key=self.api_key,
            gemini_model=self.model_name,
            dpi=dpi,
            use_gemini_cache=use_cache,
            gemini_cache_path=cache_path,  # 명시적으로 전달된 캐시 경로 사용
            save_images=save_images,
            image_output_dir=image_output_dir,
            use_history=use_history,
            history_dir=history_dir
        )

