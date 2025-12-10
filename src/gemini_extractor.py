"""
Gemini Vision API를 사용하여 PDF를 페이지별 JSON으로 변환하는 모듈

PDF 파일을 이미지로 변환하고, Gemini Vision API로 각 페이지를 분석하여
구조화된 JSON 결과를 반환합니다. 캐시 기능을 통해 재현성을 보장합니다.
"""

import json
import re
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from pdf2image import convert_from_path
from PIL import Image, ImageFile

# DecompressionBombWarning 방지: 이미지 크기 제한 증가
Image.MAX_IMAGE_PIXELS = None  # 제한 없음 (또는 충분히 큰 값으로 설정)
ImageFile.LOAD_TRUNCATED_IMAGES = True  # 손상된 이미지도 로드 시도
import google.generativeai as genai

# .env 파일 로드
from dotenv import load_dotenv
# 프로젝트 루트의 .env 파일을 명시적으로 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)  # .env 파일에서 환경 변수 로드


class PDFProcessor:
    """PDF를 이미지로 변환하는 클래스"""
    
    def __init__(self, dpi: int = 300):
        """
        Args:
            dpi: PDF 변환 시 해상도 (기본값: 300)
        """
        self.dpi = dpi
    
    def convert_pdf_to_images(self, pdf_path: str) -> List[Image.Image]:
        """
        PDF 파일을 이미지 리스트로 변환
        
        Args:
            pdf_path: PDF 파일 경로
            
        Returns:
            PIL Image 객체 리스트 (각 페이지당 하나)
        """
        images = convert_from_path(pdf_path, dpi=self.dpi)  # PDF를 이미지로 변환
        return images
    
    def save_images(self, images: List[Image.Image], output_dir: str, prefix: str = "page") -> List[str]:
        """
        이미지들을 파일로 저장
        
        Args:
            images: PIL Image 객체 리스트
            output_dir: 저장할 디렉토리 경로
            prefix: 파일명 접두사 (기본값: "page")
            
        Returns:
            저장된 파일 경로 리스트
        """
        os.makedirs(output_dir, exist_ok=True)  # 디렉토리 생성
        saved_paths = []
        
        for idx, img in enumerate(images):
            filename = f"{prefix}_{idx+1}.png"
            filepath = os.path.join(output_dir, filename)
            try:
                # 이미지가 로드되지 않은 경우 강제로 로드
                img.load()
                # PNG로 저장 (최고 품질, 압축 없음)
                # optimize=False로 최적화 비활성화하여 원본 품질 유지
                img.save(filepath, "PNG", optimize=False)
                # 저장된 파일이 제대로 생성되었는지 확인
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    saved_paths.append(filepath)
                else:
                    print(f"⚠️ 이미지 저장 실패: {filepath} (파일 크기가 0입니다)")
            except Exception as e:
                print(f"⚠️ 이미지 저장 중 오류 발생 ({filepath}): {e}")
                # 오류가 발생해도 계속 진행
                if os.path.exists(filepath):
                    saved_paths.append(filepath)
        
        return saved_paths


class GeminiVisionParser:
    """Gemini Vision API를 사용하여 이미지를 구조화된 JSON으로 파싱"""
    
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3-pro-preview"):
        """
        Args:
            api_key: Google Gemini API 키 (None이면 환경변수에서 가져옴)
            model_name: 사용할 Gemini 모델 이름
        """
        if api_key is None:
            api_key = os.getenv("GEMINI_API_KEY")  # .env 파일에서 환경변수 가져오기
            if not api_key:
                raise ValueError("GEMINI_API_KEY가 필요합니다. .env 파일에 GEMINI_API_KEY를 설정하거나 api_key 파라미터를 제공하세요.")
        
        genai.configure(api_key=api_key)  # API 키 설정
        
        # 안전성 설정: 문서 분석을 위해 필터 완화
        safety_settings = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE"
            }
        ]
        
        self.model = genai.GenerativeModel(
            model_name=model_name,
            safety_settings=safety_settings
        )  # Gemini 모델 초기화
        self.model_name = model_name
    
    def get_parsing_prompt(self) -> str:
        """
        Gemini Vision을 위한 구조화 파싱 프롬프트
        
        Returns:
            파싱 프롬프트 문자열
        """
        prompt = """이 이미지는 일본어 조건청구서(条件請求書) 문서입니다.
자연어 기반 추론을 통해 다음 JSON 형식으로 구조화된 정보를 추출해주세요:

{
  "text": "전체 텍스트 내용 (모든 텍스트를 순서대로)",
  "document_number": "문서 번호 (문서 상단의 No, 請求書No 등 - 페이지당 하나)",
  "customer": "거래처(최종 판매처) - 상품을 최종 판매하는 소매 체인 (예: ファミリーマート, セブンイレブン, ロピア, スーパー 등) - 다양한 표현 인식 (得意先, 請求先, 納品先, 客先 등)",
  "issuer": "발행처 - 다양한 표현 인식 (発行者, 仕入先, 売方, 供給元 등)",
  "issue_date": "발행일 (作成日, 発行日 등)",
  "billing_period": "청구기간 (請求期間, ご請求期間 등)",
  "total_amount": "총 금액 (金額, 合計, 総額, 請求金額 등)",
  "items": [
    {
      "management_id": "관리번호 - 각 행/항목마다 다른 관리번호가 있을 수 있음 (請求No, 契約No, 管理番号, 伝票番号 등)",
      "product_name": "상품명 (商品名, 品名, 件名 등) - 제품번호(13자리 숫자 바코드, 예: 8801043157506)가 앞에 있으면 제외하고 순수 상품명만 추출",
      "quantity": "수량 (直接的な数量が記載されている 경우のみ、数値。ケース/バラで記載されている場合は null)",
      "case_count": "ケース数 (ケース単位の数量、例: 58ケース → 58, ない場合は null)",
      "bara_count": "バラ数 (バラ単位の数量、例: 6バラ → 6, ない場合は null)",
      "units_per_case": "ケース内入数 (케이스당 개수) - 예: 12x1이면 12, 30x1이면 30, 12x2이면 24 (없으면 null)",
      "amount": "금액 (金額, 税込金額 등)",
      "customer": "항목별 거래처(최종 판매처) - 해당 항목의 거래처가 다를 수 있음 (없으면 null)"
    }
  ],
  "page_role": "페이지 역할 판단: cover(표지), main(본문), detail(상세내역), reply(회신서)"
}

표 구조 인식 및 위치 기반 추출:
- 문서에 표(테이블)가 있는 경우, 표의 컬럼 헤더를 먼저 인식합니다.
- 표의 각 행(行)은 하나의 item에 해당합니다.
- 표의 컬럼 위치에 따라 값을 추출합니다:
  * "請求No", "契約No", "管理番号", "伝票番号" 등의 컬럼 → management_id (해당 행의 해당 컬럼 값)
  * "取引先", "得意先", "請求先", "納品先", "客先" 등의 컬럼 → customer (해당 행의 해당 컬럼 값, 위치상 맞으면 우선적으로 추출)
  * "商品名", "品名", "件名" 등의 컬럼 → product_name (해당 행의 해당 컬럼 값)
  * "ケース内入数" 컬럼 → units_per_case (해당 행의 해당 컬럼 값)
  * "数量" 컬럼의 "ケース" 하위 값 → case_count (해당 행의 해당 컬럼 값)
  * "数量" 컬럼의 "バラ" 하위 값 → bara_count (해당 행의 해당 컬럼 값)
  * "請求金額", "金額", "税込金額" 등의 컬럼 → amount (해당 행의 해당 컬럼 값)
- 표에서 "取引先" 컬럼에 있는 값은 그 위치상 거래처명이므로, 의미 판단보다 위치 정보를 우선하여 추출합니다.
- 같은 management_id를 가진 여러 행이 같은 customer 값을 공유할 수 있습니다 (그룹 단위로 표시되는 경우).

추출 가이드:
- customer는 최종 판매처(최종 소매 체인)를 중심으로 식별합니다. 예: ファミリーマート, セブンイレブン, ロピア, スーパー 등
- customer는 패밀리마트, 세븐일레븐, 슈퍼 등 최종 판매처를 중심으로 하며, 도매상(卸), 물류센터, 배송처는 customer로 분류되지 않습니다.
- 입출하센터(入出荷センター), 물류센터(物流センター), 배송처(配送先) 등의 정보는 결과에 포함되지 않습니다.
- management_id는 각 항목(items)마다 추출합니다. 한 페이지에 여러 관리번호가 있을 수 있습니다.
- 표나 테이블의 각 행마다 management_id(請求No, 契約No 등)를 추출합니다.
- 각 항목(items)마다 customer가 다를 수 있으므로, 항목별로 추출합니다.
- document_number는 문서 전체를 식별하는 번호이고, management_id는 각 항목/계약을 식별하는 번호입니다.
- quantity는 직접적인 수량이 명시되어 있을 때만 숫자로 추출합니다. 예: "100個" → 100, "50本" → 50. 케이스/바라로만 표시된 경우는 null입니다.
- case_count는 케이스 수를 의미합니다. 예: "58ケース 6バラ" → case_count: 58, "67ケース 0バラ" → case_count: 67. 케이스 정보가 없으면 null입니다.
- bara_count는 바라 수를 의미합니다. 예: "58ケース 6バラ" → bara_count: 6, "67ケース 0バラ" → bara_count: 0 또는 null. 바라 정보가 없으면 null입니다.
- units_per_case(ケース内入数)는 케이스당 개수를 의미합니다. "12x1"이면 12, "30x1"이면 30, "12x2"이면 24입니다. 테이블의 "ケース内入数" 컬럼에서 추출합니다.
- product_name에서 제품번호(13자리 숫자 바코드, 예: 8801043157506)가 앞에 있으면 제거하고 순수 상품명만 추출합니다. 예: "8801043157506 ノウシン 辛ラーメン 3食" → "ノウシン 辛ラーメン 3食", "8801043030694 農心 NEW辛ラーメンカップ 68g" → "農心 NEW辛ラーメンカップ 68g"
- 표현이 다양해도 의미가 같으면 같은 필드로 인식합니다 (예: 請求No와 契約No는 모두 management_id)
- 정보가 없으면 null을 사용합니다
- JSON 형식으로만 응답하고 추가 설명은 하지 않습니다.

추가 추출 규칙:
- 표에서 "取引先" 컬럼 위치에 있는 값은 위치상 거래처명이므로 우선적으로 추출합니다. 의미 판단보다 위치 정보를 중심으로 합니다.
- customer는 최종 판매처(최종 소매 체인)를 중심으로 하며, 표의 "取引先" 컬럼에 명시된 값은 그 위치상 거래처명이므로 추출합니다.
- 도매상(卸), 물류센터, 배송코드가 있는 사업소, 입출하센터(入出荷センター), 물류센터(物流センター)는 customer로 분류되지 않습니다.
- 표의 "取引先" 컬럼에 있는 값은 위치상 거래처명이므로 추출합니다.
"""
        return prompt
    
    def parse_image(self, image: Image.Image, max_size: int = 600) -> Dict[str, Any]:
        """
        이미지를 Gemini Vision으로 파싱하여 JSON 반환
        
        Args:
            image: PIL Image 객체
            max_size: Gemini API에 전달할 최대 이미지 크기 (픽셀, 기본값: 1024)
                      속도 개선을 위해 큰 이미지는 리사이즈됨
                      더 작게 하려면 800, 600 등으로 조정 가능
            
        Returns:
            파싱 결과 JSON 딕셔너리
        """
        # 원본 이미지 정보
        original_width, original_height = image.size
        
        # 이미지 리사이즈 (Gemini API 속도 개선을 위해)
        api_image = image
        if original_width > max_size or original_height > max_size:
            # 비율 유지하면서 리사이즈
            ratio = min(max_size / original_width, max_size / original_height)
            new_width = int(original_width * ratio)
            new_height = int(original_height * ratio)
            api_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            print(f"  이미지 리사이즈: {original_width}x{original_height}px → {new_width}x{new_height}px", end="", flush=True)
        else:
            print(f"  이미지 크기: {original_width}x{original_height}px", end="", flush=True)
        
        # Gemini API 호출: 재시도 로직 포함 (SAFETY 오류 대응)
        max_retries = 3  # 최대 재시도 횟수
        retry_delay = 2  # 재시도 전 대기 시간 (초)
        
        for attempt in range(max_retries):
            try:
                # 이미지만 먼저 전달하는 방식으로 시도
                chat = self.model.start_chat(history=[])
                # 1단계: 이미지만 먼저 전달 (프롬프트 없이)
                _ = chat.send_message([api_image])
                # 2단계: 프롬프트를 별도 메시지로 전달
                response = chat.send_message(self.get_parsing_prompt())
                break  # 성공하면 루프 탈출
            except Exception as e:
                error_msg = str(e)
                # SAFETY 오류인 경우 재시도
                if "SAFETY" in error_msg or "安全性" in error_msg or "finish_reason: SAFETY" in error_msg:
                    if attempt < max_retries - 1:
                        print(f"  ⚠️ SAFETY 필터 감지 (시도 {attempt + 1}/{max_retries}), {retry_delay}초 후 재시도...", end="", flush=True)
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 지수 백오프
                        continue
                    else:
                        # 마지막 시도도 실패하면 예외 발생
                        raise Exception(f"SAFETY 필터로 인해 {max_retries}회 시도 모두 실패: {error_msg}")
                else:
                    # SAFETY 오류가 아니면 즉시 예외 발생
                    raise
        
        # 응답 검증
        if not response.candidates:
            raise Exception("Gemini API 응답에 candidates가 없습니다.")
        
        candidate = response.candidates[0]
        
        # 응답 텍스트 추출 (content가 있으면 finish_reason과 관계없이 추출)
        if not candidate.content or not candidate.content.parts:
            raise Exception("Gemini API 응답에 content parts가 없습니다.")
        
        result_text = ""
        for part in candidate.content.parts:
            if hasattr(part, 'text') and part.text:
                result_text += part.text
        
        if not result_text:
            raise Exception("Gemini API 응답에 텍스트가 없습니다.")
        
        # JSON 추출 시도
        try:
            # JSON 부분만 추출 (마크다운 코드 블록 제거)
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)  # JSON 객체 추출
            if json_match:
                result_json = json.loads(json_match.group())  # JSON 파싱
                return result_json
            else:
                # JSON이 없으면 텍스트만 반환
                return {"text": result_text}
        except json.JSONDecodeError:
            # JSON 파싱 실패 시 텍스트만 반환
            return {"text": result_text}


def get_gemini_cache_path(pdf_path: str, history_dir: Optional[str] = None) -> str:
    """
    Gemini 결과 캐시 파일 경로 생성
    
    Args:
        pdf_path: PDF 파일 경로
        history_dir: 히스토리 디렉토리 (None이면 기본 경로 사용)
        
    Returns:
        캐시 파일 경로 (예: "조건청구서②_gemini_cache.json" 또는 "history/20240101_120000/조건청구서②_gemini_cache.json")
    """
    pdf_name = Path(pdf_path).stem  # 확장자 제거
    cache_filename = f"{pdf_name}_gemini_cache.json"
    
    if history_dir:
        return os.path.join(history_dir, cache_filename)
    
    # 프로젝트 루트 기준으로 절대 경로 생성
    # gemini_extractor.py는 Rebate/src/ 디렉토리에 있으므로 parent.parent가 프로젝트 루트
    project_root = Path(__file__).parent.parent.resolve()
    return str(project_root / cache_filename)


def create_history_dir(base_dir: str, pdf_name: str) -> str:
    """
    히스토리 디렉토리 생성 (타임스탬프 기반)
    
    Args:
        base_dir: 기본 디렉토리 (예: "raw_data" 또는 현재 디렉토리)
        pdf_name: PDF 파일명 (확장자 제외)
        
    Returns:
        생성된 히스토리 디렉토리 경로 (예: "raw_data/조건청구서②/history/20240101_120000")
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    history_base = os.path.join(base_dir, f"{pdf_name}_history")
    history_dir = os.path.join(history_base, timestamp)
    os.makedirs(history_dir, exist_ok=True)
    return history_dir


def migrate_existing_to_history(base_dir: str, pdf_name: str) -> Optional[str]:
    """
    기존 파싱 결과를 첫 번째 히스토리로 이동
    
    Args:
        base_dir: 기본 디렉토리
        pdf_name: PDF 파일명 (확장자 제외)
        
    Returns:
        생성된 히스토리 디렉토리 경로 (이미지가 없으면 None)
    """
    import shutil
    
    # 기존 캐시 파일 찾기
    cache_filename = f"{pdf_name}_gemini_cache.json"
    possible_cache_paths = [
        cache_filename,  # 현재 디렉토리
        os.path.join(base_dir, cache_filename),  # base_dir
        os.path.join("raw_data", cache_filename),  # raw_data
    ]
    
    existing_cache_path = None
    for cache_path in possible_cache_paths:
        if os.path.exists(cache_path):
            existing_cache_path = cache_path
            break
    
    # 기존 이미지 디렉토리 찾기
    image_dir_name = f"{pdf_name}_images"
    possible_image_dirs = [
        os.path.join(base_dir, image_dir_name),
        os.path.join("raw_data", image_dir_name),
        image_dir_name,
    ]
    
    existing_image_dir = None
    for img_dir in possible_image_dirs:
        if os.path.exists(img_dir) and os.path.isdir(img_dir):
            existing_image_dir = img_dir
            break
    
    # 기존 파일이 없으면 None 반환
    if not existing_cache_path and not existing_image_dir:
        return None
    
    # 히스토리 디렉토리 생성 (오래된 타임스탬프로 - 첫 번째 히스토리)
    # 파일 수정 시간을 사용하거나, 오래된 날짜로 설정
    if existing_cache_path:
        file_time = os.path.getmtime(existing_cache_path)
        timestamp = datetime.fromtimestamp(file_time).strftime("%Y%m%d_%H%M%S")
    elif existing_image_dir:
        # 이미지 디렉토리의 첫 번째 파일 시간 사용
        image_files = [f for f in os.listdir(existing_image_dir) if f.endswith('.png')]
        if image_files:
            first_image = os.path.join(existing_image_dir, sorted(image_files)[0])
            file_time = os.path.getmtime(first_image)
            timestamp = datetime.fromtimestamp(file_time).strftime("%Y%m%d_%H%M%S")
        else:
            timestamp = "19700101_000000"  # 기본값
    else:
        timestamp = "19700101_000000"  # 기본값
    
    history_base = os.path.join(base_dir, f"{pdf_name}_history")
    history_dir = os.path.join(history_base, timestamp)
    
    # 이미 히스토리가 있으면 스킵
    if os.path.exists(history_dir):
        return history_dir
    
    os.makedirs(history_dir, exist_ok=True)
    
    # 캐시 파일 복사
    if existing_cache_path:
        dest_cache = os.path.join(history_dir, cache_filename)
        if not os.path.exists(dest_cache):
            shutil.copy2(existing_cache_path, dest_cache)
            print(f"📦 기존 캐시를 히스토리로 복사: {existing_cache_path} → {dest_cache}")
    
    # 이미지 디렉토리 복사
    if existing_image_dir:
        dest_image_dir = os.path.join(history_dir, "images")
        if not os.path.exists(dest_image_dir):
            shutil.copytree(existing_image_dir, dest_image_dir)
            print(f"📦 기존 이미지를 히스토리로 복사: {existing_image_dir} → {dest_image_dir}")
    
    return history_dir


def list_history_dirs(base_dir: str, pdf_name: str) -> List[Dict[str, Any]]:
    """
    히스토리 디렉토리 목록 조회
    
    Args:
        base_dir: 기본 디렉토리
        pdf_name: PDF 파일명 (확장자 제외)
        
    Returns:
        히스토리 정보 리스트 [{"timestamp": "...", "path": "...", "datetime": datetime}]
    """
    history_base = os.path.join(base_dir, f"{pdf_name}_history")
    if not os.path.exists(history_base):
        return []
    
    histories = []
    for item in sorted(os.listdir(history_base), reverse=True):  # 최신순
        item_path = os.path.join(history_base, item)
        if os.path.isdir(item_path):
            try:
                # 타임스탬프 파싱
                dt = datetime.strptime(item, "%Y%m%d_%H%M%S")
                histories.append({
                    "timestamp": item,
                    "path": item_path,
                    "datetime": dt,
                    "display": dt.strftime("%Y-%m-%d %H:%M:%S")
                })
            except ValueError:
                continue
    
    return histories


def get_image_output_dir(pdf_path: str) -> str:
    """
    이미지 저장 디렉토리 경로 생성 (새 구조: img/{pdf_name}/)
    
    Args:
        pdf_path: PDF 파일 경로 (또는 파일명만)
        
    Returns:
        이미지 저장 디렉토리 경로 (img/{pdf_name}/)
    """
    from storage_utils import get_img_dir
    pdf_name = Path(pdf_path).stem  # 확장자 제거
    return get_img_dir(pdf_name)


def load_gemini_cache(cache_path: str) -> Optional[List[Dict[str, Any]]]:
    """
    Gemini 결과 캐시 파일 로드
    
    Args:
        cache_path: 캐시 파일 경로
        
    Returns:
        페이지 JSON 리스트 (파일이 없으면 None)
    """
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)  # JSON 로드
                # 리스트인지 확인
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "pages" in data:
                    return data["pages"]
                else:
                    return [data] if data else None
        except Exception as e:
            print(f"캐시 파일 로드 실패: {e}")
            return None
    return None


def save_gemini_cache(cache_path: str, page_jsons: List[Dict[str, Any]]):
    """
    Gemini 결과를 캐시 파일로 저장 (비활성화됨 - DB 사용)
    
    Args:
        cache_path: 캐시 파일 경로 (상대 또는 절대 경로) - 사용 안 함
        page_jsons: 페이지 JSON 리스트 - 사용 안 함
    
    Note:
        로컬 파일 저장을 최소화하기 위해 비활성화됨.
        모든 데이터는 DB에 저장됩니다.
    """
    # 로컬 파일 저장 비활성화 (DB 사용)
    pass


def extract_pages_with_gemini(
    pdf_path: str,
    gemini_api_key: Optional[str] = None,
    gemini_model: str = "gemini-3-pro-preview",
        dpi: int = 300,
    use_gemini_cache: bool = True,
    gemini_cache_path: Optional[str] = None,
    save_images: bool = True,
    image_output_dir: Optional[str] = None,
    use_history: bool = True,
    history_dir: Optional[str] = None
) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    PDF 파일을 Gemini로 분석하여 페이지별 JSON 결과 반환
    
    Gemini 호출 및 결과 저장까지만 수행하는 함수입니다.
    
    Args:
        pdf_path: PDF 파일 경로
        gemini_api_key: Gemini API 키 (None이면 환경변수 또는 기본값 사용)
        gemini_model: Gemini 모델 이름
        dpi: PDF 변환 해상도 (기본값: 300)
        use_gemini_cache: Gemini 캐시 사용 여부 (기본값: True)
        gemini_cache_path: Gemini 캐시 파일 경로 (None이면 자동 생성)
        save_images: 이미지를 파일로 저장할지 여부 (기본값: True)
        image_output_dir: 이미지 저장 디렉토리 (None이면 자동 생성)
        use_history: 히스토리 관리 사용 여부 (기본값: True)
        history_dir: 히스토리 디렉토리 (None이면 자동 생성)
        
    Returns:
        (페이지별 Gemini 파싱 결과 JSON 리스트, 이미지 파일 경로 리스트) 튜플
    """
    pdf_name = Path(pdf_path).stem
    base_dir = os.path.dirname(os.path.abspath(pdf_path)) or os.getcwd()
    if not base_dir:
        base_dir = os.getcwd()
    
    # 히스토리 디렉토리 생성 (새 파싱인 경우)
    if use_history and history_dir is None:
        history_dir = create_history_dir(base_dir, pdf_name)
        print(f"📚 히스토리 디렉토리 생성: {history_dir}")
    
    # 기존 캐시 파일을 히스토리로 마이그레이션 (첫 파싱인 경우)
    if use_history and history_dir:
        # 기존 캐시 파일이 프로젝트 루트에 있으면 히스토리로 이동
        existing_cache_path = get_gemini_cache_path(pdf_path)  # 히스토리 없이 생성
        if os.path.exists(existing_cache_path) and os.path.abspath(existing_cache_path) != os.path.abspath(get_gemini_cache_path(pdf_path, history_dir)):
            # 기존 캐시를 히스토리로 복사 (첫 번째 히스토리로)
            import shutil
            history_cache_path = get_gemini_cache_path(pdf_path, history_dir)
            if not os.path.exists(history_cache_path):
                try:
                    shutil.copy2(existing_cache_path, history_cache_path)
                    print(f"📦 기존 캐시를 히스토리로 복사: {existing_cache_path} → {history_cache_path}")
                except Exception as e:
                    print(f"⚠️ 히스토리 복사 실패: {e}")
    
    # Gemini 캐시 경로 결정 (히스토리 디렉토리 우선 사용)
    if use_history and history_dir:
        # use_history=True이고 history_dir이 있으면 항상 히스토리 디렉토리 내부 경로 사용
        gemini_cache_path = get_gemini_cache_path(pdf_path, history_dir)
    elif gemini_cache_path is None:
        # 히스토리를 사용하지 않거나 history_dir이 없으면 기본 경로 사용
        gemini_cache_path = get_gemini_cache_path(pdf_path)
    
    # 절대 경로로 변환하여 출력
    abs_cache_path = os.path.abspath(gemini_cache_path)
    print(f"📁 캐시 파일 경로: {abs_cache_path}")
    
    # 이미지 저장 디렉토리 결정 (히스토리 디렉토리 사용)
    if image_output_dir is None:
        if use_history and history_dir:
            image_output_dir = os.path.join(history_dir, "images")
        else:
            image_output_dir = get_image_output_dir(pdf_path)
    abs_image_dir = os.path.abspath(image_output_dir)
    print(f"🖼️ 이미지 저장 디렉토리: {abs_image_dir}")
    
    # 이미지 경로 리스트 초기화
    image_paths = []
    
    # 1. Gemini 결과 로드 또는 생성
    page_jsons = None
    if use_gemini_cache:
        page_jsons = load_gemini_cache(gemini_cache_path)  # 캐시에서 로드 시도
        if page_jsons:
            print(f"💾 기존 캐시 로드: {len(page_jsons)}개 페이지")
            
            # 저장된 이미지 경로 확인 (이미 저장되어 있으면)
            if save_images and os.path.exists(abs_image_dir):
                for idx in range(len(page_jsons)):
                    img_path = os.path.join(abs_image_dir, f"page_{idx+1}.png")
                    if os.path.exists(img_path):
                        image_paths.append(img_path)
                    else:
                        image_paths.append(None)  # 이미지가 없으면 None
    
    # 캐시가 없으면 Gemini API 호출
    if page_jsons is None:
        # PDF를 이미지로 변환
        pdf_processor = PDFProcessor(dpi=dpi)  # PDF 처리기 생성
        images = pdf_processor.convert_pdf_to_images(pdf_path)  # PDF → 이미지 변환
        print(f"PDF 변환 완료: {len(images)}개 페이지")
        
        # 이미지 저장 (고화질)
        if save_images:
            print(f"💾 이미지 저장 중... ({abs_image_dir})")
            image_paths = pdf_processor.save_images(images, abs_image_dir, prefix="page")
            print(f"✅ 이미지 저장 완료: {len(image_paths)}개 파일")
        else:
            image_paths = [None] * len(images)  # 저장하지 않으면 None 리스트
        
        # Gemini Vision으로 각 페이지 파싱
        gemini_parser = GeminiVisionParser(api_key=gemini_api_key, model_name=gemini_model)  # Gemini 파서 생성
        page_jsons = []
        
        # 기존 캐시가 있으면 로드 (부분적으로 저장된 경우 재개)
        existing_cache = None
        if use_gemini_cache and os.path.exists(gemini_cache_path):
            try:
                existing_cache = load_gemini_cache(gemini_cache_path)
                if existing_cache and len(existing_cache) > 0:
                    print(f"기존 캐시 발견: {len(existing_cache)}개 페이지. 재개합니다...")
                    page_jsons = existing_cache.copy()
            except Exception as e:
                print(f"기존 캐시 로드 실패: {e}. 처음부터 시작합니다.")
        
        # 각 페이지 파싱 (이미 파싱된 페이지는 스킵)
        start_idx = len(page_jsons)
        total_parse_time = 0.0
        
        # 페이지 수가 충분히 많을 때만 멀티스레딩 사용 (오버헤드 고려)
        use_parallel = (len(images) - start_idx) > 1
        
        if use_parallel:
            # 멀티스레딩으로 병렬 파싱
            cache_lock = Lock()  # 캐시 저장 시 동기화용
            completed_count = 0  # 완료된 페이지 수 추적
            results_lock = Lock()  # 결과 리스트 업데이트 시 동기화용
            
            def parse_single_page(idx: int) -> tuple[int, Dict[str, Any], float, Optional[str]]:
                """단일 페이지 파싱 함수 (스레드에서 실행) - 각 스레드마다 별도의 파서 인스턴스 생성"""
                parse_start_time = time.time()
                try:
                    # 각 스레드마다 별도의 파서 인스턴스 생성 (thread-safe)
                    thread_parser = GeminiVisionParser(api_key=gemini_api_key, model_name=gemini_model)
                    page_json = thread_parser.parse_image(images[idx])  # 각 페이지 파싱
                    parse_end_time = time.time()
                    parse_duration = parse_end_time - parse_start_time
                    return (idx, page_json, parse_duration, None)
                except Exception as e:
                    parse_end_time = time.time()
                    parse_duration = parse_end_time - parse_start_time
                    error_result = {"text": f"파싱 실패: {str(e)}", "error": True}
                    return (idx, error_result, parse_duration, str(e))
            
            # ThreadPoolExecutor로 병렬 처리 (최대 5개 스레드)
            max_workers = min(5, len(images) - start_idx)  # 최대 5개 스레드 또는 남은 페이지 수 중 작은 값
            print(f"🚀 멀티스레딩 파싱 시작 (최대 {max_workers}개 스레드)")
            
            # 결과를 저장할 딕셔너리 (인덱스 순서 보장)
            parsed_results = {}
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 모든 페이지에 대해 Future 제출
                future_to_idx = {
                    executor.submit(parse_single_page, idx): idx 
                    for idx in range(start_idx, len(images))
                }
                
                # 완료된 작업부터 처리
                for future in as_completed(future_to_idx):
                    idx, page_json, parse_duration, error = future.result()
                    total_parse_time += parse_duration
                    
                    # 결과를 딕셔너리에 저장 (인덱스 순서 보장)
                    with results_lock:
                        parsed_results[idx] = page_json
                        completed_count += 1
                    
                    # 진행 상황 출력
                    if error:
                        print(f"페이지 {idx+1}/{len(images)} 파싱 실패 (소요 시간: {parse_duration:.2f}초) - {error}")
                    else:
                        print(f"페이지 {idx+1}/{len(images)} 파싱 완료 (소요 시간: {parse_duration:.2f}초) [{completed_count}/{len(images) - start_idx}]")
                    
                    # 각 페이지 파싱 후 즉시 캐시에 저장 (동기화 필요)
                    if use_gemini_cache:
                        try:
                            with results_lock:
                                # 현재까지의 결과를 임시 리스트에 반영
                                temp_page_jsons = list(page_jsons)  # 기존 데이터 복사
                                for result_idx in sorted(parsed_results.keys()):
                                    if result_idx < len(temp_page_jsons):
                                        temp_page_jsons[result_idx] = parsed_results[result_idx]
                                    else:
                                        # 인덱스 순서를 맞추기 위해 None으로 채운 후 추가
                                        while len(temp_page_jsons) < result_idx:
                                            temp_page_jsons.append(None)
                                        temp_page_jsons.append(parsed_results[result_idx])
                            
                            with cache_lock:  # 캐시 저장 동기화
                                # None을 제거하지 않고 저장 (인덱스 순서 유지)
                                save_gemini_cache(gemini_cache_path, temp_page_jsons)  # 즉시 저장
                        except Exception as e:
                            print(f"  ⚠️ 페이지 {idx+1} 캐시 저장 실패: {e}")
            
            # 최종 결과를 인덱스 순서대로 page_jsons에 반영
            for idx in range(start_idx, len(images)):
                if idx in parsed_results:
                    if idx < len(page_jsons):
                        page_jsons[idx] = parsed_results[idx]  # 업데이트
                    else:
                        # 인덱스 순서를 맞추기 위해 None으로 채운 후 추가
                        while len(page_jsons) < idx:
                            page_jsons.append(None)
                        page_jsons.append(parsed_results[idx])  # 추가
            
        else:
            # 단일 페이지인 경우 순차 처리
            for idx in range(start_idx, len(images)):
                parse_start_time = time.time()  # 파싱 시간 측정 시작
                try:
                    print(f"페이지 {idx+1}/{len(images)} Gemini Vision 파싱 중...", end="", flush=True)
                    
                    page_json = gemini_parser.parse_image(images[idx])  # 각 페이지 파싱
                    parse_end_time = time.time()
                    parse_duration = parse_end_time - parse_start_time
                    total_parse_time += parse_duration
                    
                    # 페이지 결과를 리스트에 추가/업데이트
                    if idx < len(page_jsons):
                        page_jsons[idx] = page_json  # 업데이트
                    else:
                        page_jsons.append(page_json)  # 추가
                    
                    # 파싱 시간 출력
                    print(f" 완료 (소요 시간: {parse_duration:.2f}초)")
                    
                    # 각 페이지 파싱 후 즉시 캐시에 저장 (중간에 실패해도 손실 방지)
                    if use_gemini_cache:
                        try:
                            print(f"  💾 페이지 {idx+1} 캐시 저장 시도 중...", end="", flush=True)
                            save_gemini_cache(gemini_cache_path, page_jsons)  # 즉시 저장
                        except Exception as e:
                            print(f"\n  ⚠️ 페이지 {idx+1} 캐시 저장 실패: {e}")
                            import traceback
                            traceback.print_exc()
                    
                except Exception as e:
                    parse_end_time = time.time()
                    parse_duration = parse_end_time - parse_start_time
                    total_parse_time += parse_duration
                    print(f" 실패 (소요 시간: {parse_duration:.2f}초) - {e}")
                    # 실패한 페이지는 빈 결과로 추가 (나중에 재시도 가능)
                    if idx >= len(page_jsons):
                        page_jsons.append({"text": f"파싱 실패: {str(e)}", "error": True})
                    # 실패해도 캐시는 저장 (부분 결과라도 보존)
                    if use_gemini_cache:
                        try:
                            save_gemini_cache(gemini_cache_path, page_jsons)
                        except:
                            pass
                    # 에러가 발생해도 계속 진행
                    continue
        
        # 전체 파싱 시간 요약 출력
        if start_idx < len(images):
            parsed_count = len(images) - start_idx
            avg_time = total_parse_time / parsed_count if parsed_count > 0 else 0
            print(f"\n📊 파싱 통계:")
            print(f"  - 새로 파싱한 페이지: {parsed_count}개")
            print(f"  - 총 소요 시간: {total_parse_time:.2f}초")
            print(f"  - 평균 페이지당 시간: {avg_time:.2f}초")
            if start_idx > 0:
                print(f"  - 캐시에서 로드한 페이지: {start_idx}개")
    
    # 이미지 경로가 비어있으면 생성 (캐시에서 로드한 경우)
    if not image_paths and save_images:
        # 저장된 이미지 경로 확인
        if os.path.exists(abs_image_dir):
            for idx in range(len(page_jsons)):
                img_path = os.path.join(abs_image_dir, f"page_{idx+1}.png")
                if os.path.exists(img_path):
                    image_paths.append(img_path)
                else:
                    image_paths.append(None)
        else:
            image_paths = [None] * len(page_jsons) if page_jsons else []
    
    return page_jsons, image_paths

