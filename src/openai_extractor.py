"""
OpenAI Vision API를 사용하여 PDF를 페이지별 JSON으로 변환하는 모듈

PDF 파일을 이미지로 변환하고, OpenAI Vision API로 각 페이지를 분석하여
구조화된 JSON 결과를 반환합니다. Gemini extractor와 동일한 인터페이스를 제공합니다.
"""

import json
import re
import os
import time
import base64
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from pdf2image import convert_from_path
from PIL import Image, ImageFile
from openai import OpenAI

# DecompressionBombWarning 방지: 이미지 크기 제한 증가
Image.MAX_IMAGE_PIXELS = None  # 제한 없음 (또는 충분히 큰 값으로 설정)
ImageFile.LOAD_TRUNCATED_IMAGES = True  # 손상된 이미지도 로드 시도

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
            filename = f"{prefix}_{idx+1}.jpg"  # JPEG 형식으로 저장
            filepath = os.path.join(output_dir, filename)
            try:
                # 이미지가 로드되지 않은 경우 강제로 로드
                img.load()
                # JPEG로 저장 (품질 95로 고품질 유지)
                # RGB 모드로 변환 (JPEG는 RGB만 지원)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(filepath, "JPEG", quality=95, optimize=True)
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


class OpenAIVisionParser:
    """OpenAI Vision API를 사용하여 이미지를 구조화된 JSON으로 파싱"""
    
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gpt-5-mini-2025-08-07", prompt_version: str = "v2"):
        """
        Args:
            api_key: OpenAI API 키 (None이면 환경변수에서 가져옴)
            model_name: 사용할 OpenAI 모델 이름 (기본값: "gpt-5-mini-2025-08-07")
            prompt_version: 프롬프트 버전 (기본값: "v2", prompts/prompt_v2.txt 파일 사용)
        """
        if api_key is None:
            api_key = os.getenv("OPENAI_API_KEY")  # .env 파일에서 환경변수 가져오기
            if not api_key:
                raise ValueError("OPENAI_API_KEY가 필요합니다. .env 파일에 OPENAI_API_KEY를 설정하거나 api_key 파라미터를 제공하세요.")
        
        self.client = OpenAI(api_key=api_key)  # OpenAI 클라이언트 초기화
        self.model_name = model_name
        self.prompt_version = prompt_version  # 프롬프트 버전 저장
    
    def get_parsing_prompt(self) -> str:
        """
        OpenAI Vision을 위한 구조화 파싱 프롬프트
        
        Returns:
            파싱 프롬프트 문자열
        """
        # 프롬프트 파일 경로 생성
        prompt_file = Path(__file__).parent.parent / "prompts" / f"prompt_{self.prompt_version}.txt"
        
        # 파일이 존재하면 읽기, 없으면 기본 프롬프트 사용
        if prompt_file.exists():
            try:
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    prompt = f.read()
                print(f"📄 프롬프트 파일 로드: {prompt_file.name}")
                return prompt
            except Exception as e:
                print(f"⚠️ 프롬프트 파일 로드 실패 ({prompt_file.name}): {e}. 기본 프롬프트 사용.")
        
        # 기본 프롬프트 (prompt_v2.txt 내용 기반)
        return """이 이미지는 일본어 조건청구서(条件請求書) 문서입니다.
자연어 기반 추론을 통해 다음 JSON 형식으로 구조화된 정보를 추출해주세요.

**중요: 이미지 안에 존재하는 모든 텍스트를 그대로 기반으로 하여 구조화해야 하며,
모델이 임의로 행을 삭제·생략·통합·축약하면 안 됩니다.
표의 모든 행은 반드시 1행도 빠짐없이 개별 item으로 추출해야 합니다.
OCR로 인식된 행은 어떤 이유로도 제거하거나 묶어서는 안 됩니다.

또한 다음 규칙을 강하게 적용하세요:
표의 행은 이미지 상에서 보이는 순서대로, 한 행당 하나의 item 으로 1:1 대응해야 합니다.
유사한 제품행이라도 절대 통합하거나 대표행만 선택하지 마세요.
관리番号(品目No, 請求No 등)가 동일하더라도, 각 행을 별도의 item으로 반드시 생성해야 합니다.
텍스트가 희미하거나 선에 가까워도 반드시 존재하는 행으로 간주하고 추출하세요.
행 단위 스캔(line-by-line scan)을 수행하고, 이미지에 존재하는 모든 행을 누락 없이 items 배열에 포함해야 합니다.

🔷 출력 JSON 스키마

{
"items": [
{
"management_id": "관리번호 (請求No, 契約No, 管理番号, 伝票番号 등)",
"product_name": "상품명 (바코드 제거 후)",
"quantity": "직접 수량이 있을 때만 숫자, 케이스/바라만 있을 경우 null",
"case_count": "케이스 수",
"bara_count": "바라 수",
"units_per_case": "케이스 내 입수",
"amount": "금액",
"customer": "해당 행의 거래처 (없으면 null)"
}
],
"page_role": "cover | detail"
}

🔷 테이블 인식 규칙
표의 모든 행(行)은 반드시 item으로 출력합니다.
같은 management_id가 여러 행에 반복되어도 각 행은 독립 item입니다.
절대 생략, 축약, 통합, 요약, 대표행 선택 등을 하지 마세요.
이미지에 있는 행 수와 items 배열의 행 수는 반드시 일치해야 합니다.
모델이 추론으로 보정하거나 삭제하지 않고, 이미지 시각 정보 기반으로 정확히 추출하세요.

컬럼 위치 기반 추출:
管理番号 계열 → management_id
取引先 계열 → customer
商品名 계열 → product_name
ケース内入数 → units_per_case
数量 → case_count / bara_count
金額 계열 → amount

바코드(13자리 숫자)가 있을 경우 product_name에서 제거하고 순수 상품명만 추출하세요.

🔷 추가 규칙 (행 누락 방지 강화)
이미지의 표 구조를 재해석하거나 모델이 판단하여 행을 제거하는 행동을 금지합니다.
표의 얇은 글자, 희미한 글자, 세로선에 가까운 글자도 모두 텍스트로 인식하여 item으로 반드시 포함합니다.
items는 이미지의 각 행(line)과 정확히 1:1로 대응되어야 합니다.
모델이 필요하다고 판단하여 구성 변경을 하지 않습니다.
오직 이미지에 보이는 텍스트와 위치만을 기준으로 추출합니다."""
    
    def _image_to_base64(self, image: Image.Image) -> str:
        """
        PIL Image를 base64 문자열로 변환
        
        Args:
            image: PIL Image 객체
            
        Returns:
            base64 인코딩된 이미지 문자열
        """
        from io import BytesIO
        buffered = BytesIO()
        # JPEG 형식으로 변환 (RGB 모드 필요)
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(buffered, format="JPEG", quality=95)
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return img_str
    
    def parse_image(self, image: Image.Image, max_size: int = 1000, timeout: int = 120) -> Dict[str, Any]:
        """
        이미지를 OpenAI Vision으로 파싱하여 JSON 반환
        
        Args:
            image: PIL Image 객체
            max_size: OpenAI API에 전달할 최대 이미지 크기 (픽셀, 기본값: 1000)
                      속도 개선을 위해 큰 이미지는 리사이즈됨
            timeout: API 호출 타임아웃 (초, 기본값: 120초 = 2분)
            
        Returns:
            파싱 결과 JSON 딕셔너리
        """
        # 원본 이미지 정보
        original_width, original_height = image.size
        
        # 이미지 리사이즈 (OpenAI API 속도 개선을 위해)
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
        
        # 이미지를 base64로 변환
        image_base64 = self._image_to_base64(api_image)
        
        # OpenAI API 호출: 재시도 로직 포함
        max_retries = 3  # 최대 재시도 횟수
        retry_delay = 2  # 재시도 전 대기 시간 (초)
        
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": self.get_parsing_prompt()
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    timeout=timeout
                )
                
                # 응답 텍스트 추출
                result_text = response.choices[0].message.content
                
                if not result_text:
                    raise Exception("OpenAI API 응답에 텍스트가 없습니다.")
                
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
                
            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries - 1:
                    print(f"  ⚠️ API 호출 실패 (시도 {attempt + 1}/{max_retries}), {retry_delay}초 후 재시도...", end="", flush=True)
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                    continue
                else:
                    # 마지막 시도도 실패하면 예외 발생
                    raise Exception(f"OpenAI API 호출 실패 ({max_retries}회 시도): {error_msg}")


class OpenAITextParser:
    """OpenAI Chat API를 사용하여 텍스트를 구조화된 JSON으로 파싱"""
    
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gpt-4o-mini", prompt_version: str = "v2"):
        """
        Args:
            api_key: OpenAI API 키 (None이면 환경변수에서 가져옴)
            model_name: 사용할 OpenAI 모델 이름 (기본값: "gpt-4o-mini")
            prompt_version: 프롬프트 버전 (기본값: "v2", prompts/prompt_v2.txt 파일 사용)
        """
        if api_key is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY가 필요합니다. .env 파일에 OPENAI_API_KEY를 설정하거나 api_key 파라미터를 제공하세요.")
        
        self.client = OpenAI(api_key=api_key)
        self.model_name = model_name
        self.prompt_version = prompt_version
    
    def get_parsing_prompt(self) -> str:
        """
        텍스트를 JSON으로 변환하기 위한 프롬프트
        
        Returns:
            파싱 프롬프트 문자열
        """
        # 프롬프트 파일 경로 생성
        prompt_file = Path(__file__).parent.parent / "prompts" / f"prompt_{self.prompt_version}.txt"
        
        # 파일이 존재하면 읽기
        if prompt_file.exists():
            try:
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    prompt = f.read()
                return prompt
            except Exception as e:
                print(f"⚠️ 프롬프트 파일 로드 실패 ({prompt_file.name}): {e}")
        
        # 기본 프롬프트 (prompt_v2.txt 내용 기반, 텍스트용으로 수정)
        return """다음은 일본어 조건청구서(条件請求書) 문서의 OCR 추출 텍스트입니다.
이 텍스트를 분석하여 다음 JSON 형식으로 구조화된 정보를 추출해주세요:

{
  "items": [
    {
      "management_id": "관리번호 - 각 행/항목마다 다른 관리번호가 있을 수 있음 (請求No, 契約No, 管理番号, 伝票番号 등). 같은 management_id가 여러 행에 있으면 각 행을 별도의 item으로 추출해야 함",
      "product_name": "상품명 (商品名, 品名, 件名 등) - 제품번호(13자리 숫자 바코드, 예: 8801043157506)가 앞에 있으면 제외하고 순수 상품명만 추출",
      "quantity": "수량 (直接的な数量が記載されている場合のみ、数値。ケース/バラで記載されている場合は null)",
      "case_count": "ケース数 (ケース単位の数量、例: 58ケース → 58, ない場合は null)",
      "bara_count": "バラ数 (バラ単位の数量、例: 6バラ → 6, ない場合は null)",
      "units_per_case": "ケース内入数 (케이스당 개수) - 예: 12x1이면 12, 30x1이면 30, 12x2이면 24 (없으면 null)",
      "amount": "금액 (金額, 税込金額 등)",
      "customer": "항목별 거래처(최종 판매처) - 해당 항목의 거래처가 다를 수 있음 (없으면 null)"
    }
  ],
  "page_role": "페이지 역할 판단: cover(표지), detail(상세내역)"
}

**중요: items 배열에는 표의 모든 행이 포함되어야 합니다. 같은 management_id를 가진 행이 여러 개 있어도 각각 별도의 item으로 추출해야 합니다. 누락 없이 모든 행을 추출하세요.**

표 구조 인식 및 위치 기반 추출:
- 문서에 표(테이블)가 있는 경우, 표의 컬럼 헤더를 먼저 인식합니다.
- 표의 각 행(行)은 하나의 item에 해당합니다.
- **중요: 같은 management_id를 가진 모든 행을 반드시 추출해야 합니다. 최상단 한 개만 추출하지 말고, 같은 management_id를 가진 모든 행을 items 배열에 포함시켜야 합니다.**
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
- **같은 management_id가 여러 행에 반복되는 경우, 각 행을 별도의 item으로 추출해야 합니다. 누락 없이 모든 행을 추출하세요.**

추출 가이드:
- customer는 최종 판매처(최종 소매 체인)를 중심으로 식별합니다. 예: ファミリーマート, セブンイレブン, ロピア, スーパー 등
- customer는 패밀리마트, 세븐일레븐, 슈퍼 등 최종 판매처를 중심으로 하며, 도매상(卸), 물류센터, 배송처는 customer로 분류되지 않습니다.
- 입출하센터(入出荷センター), 물류센터(物流センター), 배송처(配送先) 등의 정보는 결과에 포함되지 않습니다.
- management_id는 각 항목(items)마다 추출합니다. 한 페이지에 여러 관리번호가 있을 수 있습니다.
- 표나 테이블의 각 행마다 management_id(請求No, 契約No 등)를 추출합니다.
- **중요: 같은 management_id를 가진 여러 행이 있으면, 각 행을 별도의 item으로 추출해야 합니다. 한 개만 추출하지 말고 모든 행을 추출하세요.**
- 각 항목(items)마다 customer가 다를 수 있으므로, 항목별로 추출합니다.
- **표의 모든 행을 빠짐없이 추출해야 합니다. 같은 management_id가 반복되어도 각 행은 별도의 item입니다.**
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
- 표의 "取引先" 컬럼에 있는 값은 위치상 거래처명이므로 추출합니다."""
    
    def parse_text(self, text: str, timeout: int = 120, reference_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        텍스트를 OpenAI Chat API로 파싱하여 JSON 반환
        
        Args:
            text: Upstage 등에서 추출한 텍스트
            timeout: API 호출 타임아웃 (초, 기본값: 120초 = 2분)
            reference_json: 기준 페이지의 JSON 정보 (다른 페이지 추출 시 참조용, 기본값: None)
            
        Returns:
            파싱 결과 JSON 딕셔너리
        """
        # 프롬프트 구성
        prompt = self.get_parsing_prompt()
        
        # 기준 JSON이 있으면 프롬프트에 포함
        reference_section = ""
        if reference_json:
            reference_json_str = json.dumps(reference_json, ensure_ascii=False, indent=2)
            reference_section = f"\n\n**기준 페이지 정보 (참조용)**:\n다음은 같은 문서의 다른 페이지(기준 페이지)에서 추출한 JSON 정보입니다. 이 정보를 참고하여 동일한 형식과 구조로 추출하되, 현재 페이지의 실제 내용에 맞게 추출하세요:\n\n```json\n{reference_json_str}\n```\n\n위 기준 페이지의 구조와 필드 형식을 참고하여, 현재 페이지의 텍스트를 동일한 형식으로 추출하세요."
        
        full_prompt = f"{prompt}{reference_section}\n\n다음은 OCR로 추출한 텍스트입니다:\n\n{text}\n\n위 텍스트를 분석하여 JSON 형식으로 추출해주세요."
        
        # OpenAI API 호출: 재시도 로직 포함
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "user",
                            "content": full_prompt
                        }
                    ],
                    timeout=timeout
                )
                
                # 응답 텍스트 추출
                result_text = response.choices[0].message.content
                
                if not result_text:
                    raise Exception("OpenAI API 응답에 텍스트가 없습니다.")
                
                # JSON 추출 시도
                try:
                    # JSON 부분만 추출 (마크다운 코드 블록 제거)
                    json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                    if json_match:
                        result_json = json.loads(json_match.group())
                        return result_json
                    else:
                        # JSON이 없으면 텍스트만 반환
                        return {"text": result_text, "error": "JSON을 찾을 수 없습니다."}
                except json.JSONDecodeError as e:
                    # JSON 파싱 실패 시 텍스트만 반환
                    return {"text": result_text, "error": f"JSON 파싱 실패: {e}"}
                
            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries - 1:
                    print(f"  ⚠️ API 호출 실패 (시도 {attempt + 1}/{max_retries}), {retry_delay}초 후 재시도...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    raise Exception(f"OpenAI API 호출 실패 ({max_retries}회 시도): {error_msg}")


def extract_json_from_text(text: str, api_key: Optional[str] = None, model_name: str = "gpt-4o-mini", prompt_version: str = "v2", reference_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    텍스트를 OpenAI API로 분석하여 JSON 결과 반환 (편의 함수)
    
    Args:
        text: Upstage 등에서 추출한 텍스트
        api_key: OpenAI API 키 (None이면 환경변수에서 가져옴)
        model_name: OpenAI 모델 이름 (기본값: "gpt-4o-mini")
        prompt_version: 프롬프트 버전 (기본값: "v2")
        reference_json: 기준 페이지의 JSON 정보 (다른 페이지 추출 시 참조용, 기본값: None)
        
    Returns:
        파싱 결과 JSON 딕셔너리
    """
    parser = OpenAITextParser(api_key=api_key, model_name=model_name, prompt_version=prompt_version)
    return parser.parse_text(text, reference_json=reference_json)


def extract_pages_with_openai(
    pdf_path: str,
    openai_api_key: Optional[str] = None,
    openai_model: str = "gpt-5-mini-2025-08-07",
    dpi: int = 300,
    use_openai_cache: bool = False,  # 캐시 비활성화 (DB 사용)
    openai_cache_path: Optional[str] = None,
    save_images: bool = False,  # 로컬 저장 비활성화 (기본값: False)
    image_output_dir: Optional[str] = None,
    use_history: bool = False,  # 히스토리 비활성화
    history_dir: Optional[str] = None
) -> tuple[List[Dict[str, Any]], List[str], Optional[List[Image.Image]]]:
    """
    PDF 파일을 OpenAI로 분석하여 페이지별 JSON 결과 반환
    
    DB를 우선 사용하며, DB에 데이터가 없을 때만 OpenAI API를 호출합니다.
    캐시 파일은 사용하지 않습니다.
    
    Args:
        pdf_path: PDF 파일 경로
        openai_api_key: OpenAI API 키 (None이면 환경변수 또는 기본값 사용)
        openai_model: OpenAI 모델 이름 (기본값: "gpt-5-mini-2025-08-07")
        dpi: PDF 변환 해상도 (기본값: 300)
        use_openai_cache: OpenAI 캐시 사용 여부 (기본값: False, 사용 안 함)
        openai_cache_path: OpenAI 캐시 파일 경로 (사용 안 함)
        save_images: 이미지를 파일로 저장할지 여부 (기본값: False, 사용 안 함)
        image_output_dir: 이미지 저장 디렉토리 (사용 안 함)
        use_history: 히스토리 관리 사용 여부 (기본값: False, 사용 안 함)
        history_dir: 히스토리 디렉토리 (사용 안 함)
        
    Returns:
        (페이지별 OpenAI 파싱 결과 JSON 리스트, 이미지 파일 경로 리스트, PIL Image 객체 리스트) 튜플
        이미지 파일 경로는 항상 None 리스트 (로컬 저장 비활성화)
        PIL Image 객체 리스트는 새로 변환한 경우에만 반환
    """
    pdf_name = Path(pdf_path).stem
    pdf_filename = f"{pdf_name}.pdf"
    
    # 이미지 경로 리스트 초기화 (로컬 저장 비활성화로 항상 None 리스트)
    image_paths = []
    pil_images = None  # PIL Image 객체 리스트 (새로 변환한 경우에만)
    
    # 1. DB에서 먼저 확인
    page_jsons = None
    try:
        from database.registry import get_db
        db_manager = get_db()
        page_jsons = db_manager.get_page_results(
            pdf_filename=pdf_filename,
            session_id=None,
            is_latest=True
        )
        if page_jsons and len(page_jsons) > 0:
            print(f"💾 DB에서 기존 파싱 결과 로드: {len(page_jsons)}개 페이지")
            # DB에서 로드한 경우 이미지는 None (이미 DB에 저장되어 있음)
            image_paths = [None] * len(page_jsons)
            return page_jsons, image_paths, None
    except Exception as db_error:
        print(f"⚠️ DB 확인 실패: {db_error}. 새로 파싱합니다.")
    
    # 2. DB에 데이터가 없으면 OpenAI API 호출
    # PDF를 이미지로 변환
    pdf_processor = PDFProcessor(dpi=dpi)  # PDF 처리기 생성
    images = pdf_processor.convert_pdf_to_images(pdf_path)  # PDF → 이미지 변환
    pil_images = images  # PIL Image 객체 리스트 저장 (DB 저장용)
    print(f"PDF 변환 완료: {len(images)}개 페이지")
    
    # 로컬 저장 비활성화 (DB에만 저장)
    image_paths = [None] * len(images)  # 항상 None 리스트
    
    # OpenAI Vision으로 각 페이지 파싱
    openai_parser = OpenAIVisionParser(api_key=openai_api_key, model_name=openai_model, prompt_version="v2")  # OpenAI 파서 생성
    page_jsons = []
    
    # 각 페이지 파싱 (처음부터 시작)
    start_idx = 0
    total_parse_time = 0.0
    
    # 페이지 수가 충분히 많을 때만 멀티스레딩 사용 (오버헤드 고려)
    use_parallel = (len(images) - start_idx) > 1
    
    if use_parallel:
        # 멀티스레딩으로 병렬 파싱
        completed_count = 0  # 완료된 페이지 수 추적
        results_lock = Lock()  # 결과 리스트 업데이트 시 동기화용
        
        def parse_single_page(idx: int) -> tuple[int, Dict[str, Any], float, Optional[str]]:
            """단일 페이지 파싱 함수 (스레드에서 실행) - 각 스레드마다 별도의 파서 인스턴스 생성"""
            parse_start_time = time.time()
            try:
                # 각 스레드마다 별도의 파서 인스턴스 생성 (thread-safe)
                thread_parser = OpenAIVisionParser(api_key=openai_api_key, model_name=openai_model)
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
                print(f"페이지 {idx+1}/{len(images)} OpenAI Vision 파싱 중...", end="", flush=True)
                
                page_json = openai_parser.parse_image(images[idx])  # 각 페이지 파싱
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
                
            except Exception as e:
                parse_end_time = time.time()
                parse_duration = parse_end_time - parse_start_time
                total_parse_time += parse_duration
                print(f" 실패 (소요 시간: {parse_duration:.2f}초) - {e}")
                # 실패한 페이지는 빈 결과로 추가
                if idx >= len(page_jsons):
                    page_jsons.append({"text": f"파싱 실패: {str(e)}", "error": True})
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
    
    # 로컬 저장 비활성화로 image_paths는 항상 None 리스트
    if not image_paths and page_jsons:
        image_paths = [None] * len(page_jsons)
    
    return page_jsons, image_paths, pil_images
