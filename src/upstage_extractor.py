"""
Upstage와 OpenAI API를 사용하여 이미지에서 텍스트를 추출하는 모듈
"""

import os
import time
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
    
    def extract_text(self, filename: str, optimize_image: bool = True, max_retries: int = 3) -> str:
        """
        이미지 파일에서 텍스트를 추출 (재시도 로직 포함)
        
        Args:
            filename: 이미지 파일 경로
                     예: "img/日本アクセスＣＶＳ/page_3.jpg"
            optimize_image: 이미지 최적화 여부 (기본값: True)
                           True면 큰 이미지를 JPEG로 변환하고 리사이즈
            max_retries: 최대 재시도 횟수 (기본값: 3)
        
        Returns:
            추출된 텍스트 문자열
            예: "管理番号\t商品名\t数量\t金額\n001\t商品A\t10\t1000"
        
        Raises:
            Exception: API 호출 실패 시
        """
        headers = {"Authorization": f'Bearer {self.api_key}'}
        
        # 파일 크기 확인 (디버깅용)
        file_size = os.path.getsize(filename) / (1024 * 1024)  # MB
        
        # 재시도 로직
        retry_delay = 2  # 초기 재시도 대기 시간 (초)
        
        for attempt in range(max_retries):
            file_handle = None
            try:
                if optimize_image:
                    # 이미지 최적화 (큰 이미지를 JPEG로 변환하고 리사이즈)
                    image_data = self._prepare_image_for_upstage(filename)
                    files = {"document": ("image.jpg", image_data, "image/jpeg")}
                else:
                    # 원본 파일 그대로 사용
                    file_handle = open(filename, "rb")
                    files = {"document": file_handle}
                
                data = {"model": self.model}
                
                response = requests.post(self.url, headers=headers, files=files, data=data, timeout=60)
                
                # 파일 닫기 (성공/실패 관계없이)
                if file_handle:
                    try:
                        file_handle.close()
                    except Exception:
                        pass
                
                # Rate limit (429) 에러 처리
                if response.status_code == 429:
                    # Retry-After 헤더 확인
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        wait_time = int(retry_after)
                    else:
                        wait_time = retry_delay * (2 ** attempt)  # 지수 백오프
                    
                    if attempt < max_retries - 1:
                        print(f"  ⚠️ Rate limit 도달 (시도 {attempt + 1}/{max_retries}), {wait_time}초 후 재시도...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise Exception(f"Upstage API Rate limit 초과 ({max_retries}회 시도)")
                
                response.raise_for_status()  # HTTP 에러 체크
                result = response.json()
                
                # 에러 응답 체크
                if "error" in result:
                    error_msg = result.get("error", {}).get("message", str(result.get("error")))
                    raise Exception(f"Upstage API 에러: {error_msg}")
                
                return result.get("text", "")
                
            except requests.exceptions.HTTPError as e:
                # 파일 닫기
                if file_handle:
                    try:
                        file_handle.close()
                    except Exception:
                        pass
                
                # 429 외의 HTTP 에러는 재시도
                if e.response and e.response.status_code != 429 and attempt < max_retries - 1:
                    print(f"  ⚠️ HTTP 에러 (시도 {attempt + 1}/{max_retries}), {retry_delay}초 후 재시도...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                    continue
                else:
                    raise Exception(f"Upstage API HTTP 에러 (파일 크기: {file_size:.2f}MB): {str(e)}")
                    
            except requests.exceptions.RequestException as e:
                # 파일 닫기
                if file_handle:
                    try:
                        file_handle.close()
                    except Exception:
                        pass
                
                # 네트워크 에러 등은 재시도
                if attempt < max_retries - 1:
                    print(f"  ⚠️ 요청 실패 (시도 {attempt + 1}/{max_retries}), {retry_delay}초 후 재시도...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                    continue
                else:
                    raise Exception(f"Upstage API 요청 실패 (파일 크기: {file_size:.2f}MB): {str(e)}")
                    
            except Exception as e:
                # 파일 닫기
                if file_handle:
                    try:
                        file_handle.close()
                    except Exception:
                        pass
                
                # 기타 에러는 재시도하지 않고 즉시 실패
                raise Exception(f"Upstage API 처리 실패 (파일 크기: {file_size:.2f}MB): {str(e)}")
        
        # 모든 재시도 실패
        raise Exception(f"Upstage API 호출 실패 ({max_retries}회 시도, 파일 크기: {file_size:.2f}MB)")
    
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