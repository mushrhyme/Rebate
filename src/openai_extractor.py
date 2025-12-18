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

from openai import OpenAI

# 공통 설정 로드 (PIL 설정, .env 로드 등)
from modules.utils.config import load_env
load_env()  # 명시적으로 .env 로드

# 공통 PDFProcessor 모듈 import
from src.pdf_processor import PDFProcessor


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