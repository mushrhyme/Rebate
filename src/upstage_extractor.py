"""
Upstage와 OpenAI API를 사용하여 이미지에서 텍스트를 추출하는 모듈
"""

import os
import requests
from pathlib import Path
from typing import Optional
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# 공통 설정 로드 (.env 로드)
from modules.utils.config import load_env
load_env()


class UpstageExtractor:
    """Upstage OCR API를 사용하여 이미지에서 텍스트를 추출하는 클래스"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Upstage API 키 (None이면 환경변수에서 가져옴)
        """
        if api_key is None:
            api_key = os.getenv("UPSTAGE_API_KEY")  # .env 파일에서 환경변수 가져오기
            if not api_key:
                raise ValueError("UPSTAGE_API_KEY가 필요합니다. .env 파일에 UPSTAGE_API_KEY를 설정하거나 api_key 파라미터를 제공하세요.")
        
        self.api_key = api_key
        self.url = "https://api.upstage.ai/v1/document-digitization"
        self.model = "ocr"
    
    def _prepare_image_for_upstage(self, filename: str, max_width: int = 3500, max_height: int = 5000, quality: int = 85) -> BytesIO:
        """
        Upstage API에 전송하기 전에 이미지를 최적화 (리사이즈 및 JPEG 변환)
        
        Args:
            filename: 원본 이미지 파일 경로
            max_width: 최대 너비 (기본값: 3500px, 약 300 DPI A4 기준)
            max_height: 최대 높이 (기본값: 5000px)
            quality: JPEG 품질 (1-100, 기본값: 85)
        
        Returns:
            최적화된 이미지 BytesIO 객체
        """
        if not PIL_AVAILABLE:
            # PIL이 없으면 원본 파일 그대로 반환
            with open(filename, "rb") as f:
                return BytesIO(f.read())
        
        try:
            img = Image.open(filename)
            original_width, original_height = img.size
            original_format = img.format
            
            # RGB 모드로 변환 (JPEG는 RGB만 지원)
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            # 해상도가 너무 크면 리사이즈
            needs_resize = original_width > max_width or original_height > max_height
            
            if needs_resize:
                # 비율 유지하면서 리사이즈
                ratio = min(max_width / original_width, max_height / original_height)
                new_width = int(original_width * ratio)
                new_height = int(original_height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                print(f"  이미지 리사이즈: {original_width}x{original_height}px → {new_width}x{new_height}px")
            
            # JPEG로 변환하여 BytesIO에 저장
            output = BytesIO()
            img.save(output, format="JPEG", quality=quality, optimize=True)
            output.seek(0)
            
            if needs_resize or original_format != "JPEG":
                print(f"  이미지 최적화: {original_format} → JPEG (품질: {quality})")
            
            return output
        except Exception as e:
            # 이미지 처리 실패 시 원본 파일 반환
            print(f"  ⚠️ 이미지 최적화 실패, 원본 사용: {e}")
            with open(filename, "rb") as f:
                return BytesIO(f.read())
    
    def extract_text(self, filename: str, optimize_image: bool = True) -> str:
        """
        이미지 파일에서 텍스트를 추출
        
        Args:
            filename: 이미지 파일 경로
                     예: "img/日本アクセスＣＶＳ/page_3.jpg"
            optimize_image: 이미지 최적화 여부 (기본값: True)
                           True면 큰 이미지를 JPEG로 변환하고 리사이즈
        
        Returns:
            추출된 텍스트 문자열
            예: "管理番号\t商品名\t数量\t金額\n001\t商品A\t10\t1000"
        
        Raises:
            Exception: API 호출 실패 시
        """
        headers = {"Authorization": f'Bearer {self.api_key}'}
        
        # 파일 크기 확인 (디버깅용)
        file_size = os.path.getsize(filename) / (1024 * 1024)  # MB
        
        try:
            if optimize_image:
                # 이미지 최적화 (큰 이미지를 JPEG로 변환하고 리사이즈)
                image_data = self._prepare_image_for_upstage(filename)
                files = {"document": ("image.jpg", image_data, "image/jpeg")}
            else:
                # 원본 파일 그대로 사용
                files = {"document": open(filename, "rb")}
            
            data = {"model": self.model}
            
            response = requests.post(self.url, headers=headers, files=files, data=data, timeout=60)
            response.raise_for_status()  # HTTP 에러 체크
            result = response.json()
            
            # 파일 닫기
            if hasattr(files["document"], "close"):
                files["document"].close()
            
            # 에러 응답 체크
            if "error" in result:
                error_msg = result.get("error", {}).get("message", str(result.get("error")))
                raise Exception(f"Upstage API 에러: {error_msg}")
            
            return result.get("text", "")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Upstage API 요청 실패 (파일 크기: {file_size:.2f}MB): {str(e)}")
        except Exception as e:
            raise Exception(f"Upstage API 처리 실패 (파일 크기: {file_size:.2f}MB): {str(e)}")
    
    def extract_and_save(self, filename: str, output_path: Optional[str] = None) -> str:
        """
        이미지에서 텍스트를 추출하고 파일로 저장
        
        Args:
            filename: 이미지 파일 경로
                     예: "img/日本アクセスＣＶＳ/page_3.jpg"
            output_path: 저장할 텍스트 파일 경로 (None이면 자동 생성)
                        예: "img/日本アクセスＣＶＳ/page_3_upstage.txt"
        
        Returns:
            저장된 파일 경로
        """
        text = self.extract_text(filename)
        
        if output_path is None:
            # 원본 파일명에서 확장자 제거 후 "_upstage.txt" 추가
            output_path = os.path.splitext(filename)[0] + "_upstage.txt"
        
        # 디렉토리가 없으면 생성
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        
        with open(output_path, "wb") as f:
            f.write(text.encode("utf-8"))
        
        return output_path