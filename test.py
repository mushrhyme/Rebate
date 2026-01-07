"""
PDF 페이지를 엑셀로 변환하여 텍스트 추출 후 RAG로 레퍼런스 JSON 찾아서 LLM 호출하는 테스트 스크립트
"""

import os
import json
import re
from pathlib import Path
from openai import OpenAI

from modules.utils.pdf_utils import extract_text_from_pdf_page
from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_rag_config, get_project_root
from typing import Dict, Any, Optional


def get_prompt_file_path(version: str = "v3") -> Path:
    """
    프롬프트 파일 경로를 버전에 따라 생성
    
    Args:
        version: 프롬프트 버전 ("v3")
        
    Returns:
        프롬프트 파일 경로
    """
    project_root = get_project_root()
    prompts_dir = project_root / "prompts"
    return prompts_dir / f"rag_with_example_{version}.txt"


def reorder_json_keys(result_json: Dict[str, Any], reference_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    결과 JSON의 키 순서를 REFERENCE_JSON의 키 순서에 맞춰 재정렬
    
    Args:
        result_json: 재정렬할 JSON 딕셔너리
        reference_json: 참조용 JSON 딕셔너리 (키 순서 기준)
        
    Returns:
        키 순서가 재정렬된 JSON 딕셔너리
    """
    if not reference_json:
        return result_json
    
    # 최상위 레벨 키 순서 추출
    reference_top_keys = list(reference_json.keys())
    
    # 결과 JSON의 최상위 키 재정렬
    reordered_result = {}
    
    # 1. REFERENCE_JSON에 있는 키를 순서대로 추가
    for key in reference_top_keys:
        if key in result_json:
            if key == "items" and isinstance(result_json[key], list) and isinstance(reference_json.get(key), list):
                # items 배열 처리
                if len(reference_json[key]) > 0:
                    # items[0]의 키 순서 추출
                    reference_item_keys = list(reference_json[key][0].keys())
                    # items 배열 내부 객체들의 키 순서 재정렬
                    reordered_items = []
                    for item in result_json[key]:
                        if isinstance(item, dict):
                            reordered_item = {}
                            # REFERENCE_JSON의 키 순서대로 추가
                            for item_key in reference_item_keys:
                                if item_key in item:
                                    reordered_item[item_key] = item[item_key]
                            # REFERENCE_JSON에 없지만 결과에 있는 키 추가 (순서는 뒤로)
                            for item_key in item.keys():
                                if item_key not in reference_item_keys:
                                    reordered_item[item_key] = item[item_key]
                            reordered_items.append(reordered_item)
                        else:
                            reordered_items.append(item)
                    reordered_result[key] = reordered_items
                else:
                    reordered_result[key] = result_json[key]
            elif isinstance(result_json[key], dict) and isinstance(reference_json.get(key), dict):
                # 중첩된 딕셔너리도 재정렬
                reference_nested_keys = list(reference_json[key].keys())
                reordered_nested = {}
                for nested_key in reference_nested_keys:
                    if nested_key in result_json[key]:
                        reordered_nested[nested_key] = result_json[key][nested_key]
                # REFERENCE에 없지만 결과에 있는 키 추가
                for nested_key in result_json[key].keys():
                    if nested_key not in reference_nested_keys:
                        reordered_nested[nested_key] = result_json[key][nested_key]
                reordered_result[key] = reordered_nested
            else:
                reordered_result[key] = result_json[key]
    
    # 2. REFERENCE_JSON에 없지만 결과에 있는 키 추가 (순서는 뒤로)
    for key in result_json.keys():
        if key not in reference_top_keys:
            reordered_result[key] = result_json[key]
    
    return reordered_result


class RAGProcessor:
    """RAG 기반 JSON 추출 처리 클래스"""
    
    def __init__(self):
        """초기화"""
        self.rag_manager = get_rag_manager()
        self.config = get_rag_config()
    
    def extract_text_from_pdf(self, pdf_path: Path, page_num: int) -> str:
        """
        PDF 페이지에서 엑셀 변환 방식으로 텍스트 추출
        
        Args:
            pdf_path: PDF 파일 경로
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            추출된 텍스트
        """
        print(f"📄 PDF 텍스트 추출 중... (파일: {pdf_path}, 페이지: {page_num})")
        ocr_text = extract_text_from_pdf_page(
            pdf_path=pdf_path,
            page_num=page_num,
            method="excel"  # 엑셀 변환 방식 사용
        )
        
        if not ocr_text:
            raise ValueError(f"텍스트 추출 실패: {pdf_path} 페이지 {page_num}")
        
        print(f"✅ 텍스트 추출 완료 (길이: {len(ocr_text)} 문자)")
        return ocr_text
    
    def search_reference_examples(self, ocr_text: str) -> list:
        """
        RAG 벡터 DB에서 유사한 예제 검색
        
        Args:
            ocr_text: 검색할 OCR 텍스트
            
        Returns:
            검색된 예제 리스트
        """
        print(f"🔍 RAG 벡터 DB에서 유사 예제 검색 중...")
        
        # 벡터 DB 상태 확인
        example_count = self.rag_manager.count_examples()
        if example_count == 0:
            raise ValueError("벡터 DB에 예제가 없습니다. build_faiss_db.py를 먼저 실행하세요.")
        
        print(f"📊 벡터 DB 예제 수: {example_count}개")
        
        # 유사 예제 검색
        similar_examples = self.rag_manager.search_similar_advanced(
            query_text=ocr_text,
            top_k=self.config.top_k,
            similarity_threshold=self.config.similarity_threshold,
            search_method=self.config.search_method,
            hybrid_alpha=self.config.hybrid_alpha
        )
        
        # 검색 결과가 없으면 threshold를 낮춰서 재검색
        if not similar_examples:
            print(f"⚠️ 검색 결과 없음 (threshold: {self.config.similarity_threshold})")
            print("🔄 threshold를 0.0으로 낮춰 재검색...")
            similar_examples = self.rag_manager.search_similar_advanced(
                query_text=ocr_text,
                top_k=1,
                similarity_threshold=0.0,
                search_method=self.config.search_method,
                hybrid_alpha=self.config.hybrid_alpha
            )
        
        if not similar_examples:
            raise ValueError("검색된 예제가 없습니다.")
        
        print(f"✅ {len(similar_examples)}개 예제 발견")
        
        # 검색 결과 정보 출력
        for idx, ex in enumerate(similar_examples):
            score_info = []
            if 'hybrid_score' in ex:
                score_info.append(f"Hybrid: {ex['hybrid_score']:.4f}")
            if 'bm25_score' in ex:
                score_info.append(f"BM25: {ex['bm25_score']:.4f}")
            score_info.append(f"Similarity: {ex['similarity']:.4f}")
            
            metadata = ex.get('metadata', {})
            pdf_name = metadata.get('pdf_name', 'Unknown')
            page_num = metadata.get('page_num', 'Unknown')
            
            print(f"  [{idx+1}] {pdf_name} - Page{page_num} ({', '.join(score_info)})")
        
        return similar_examples
    
    def build_prompt(self, ocr_text: str, reference_example: dict) -> str:
        """
        프롬프트 템플릿을 사용하여 최종 프롬프트 생성
        
        Args:
            ocr_text: 대상 OCR 텍스트
            reference_example: 참조 예제 (ocr_text, answer_json 포함)
            
        Returns:
            완성된 프롬프트 문자열
        """
        print(f"📝 프롬프트 생성 중...")
        
        # 프롬프트 템플릿 로드
        prompt_template_path = get_prompt_file_path(version="v3")
        if not prompt_template_path.exists():
            raise FileNotFoundError(f"프롬프트 파일이 없습니다: {prompt_template_path}")
        
        with open(prompt_template_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
        
        # 참조 예제 정보 추출
        example_ocr = reference_example["ocr_text"]
        example_answer = reference_example["answer_json"]
        example_answer_str = json.dumps(example_answer, ensure_ascii=False, indent=2)
        
        # 프롬프트 완성
        prompt = prompt_template.format(
            example_ocr=example_ocr,
            example_answer_str=example_answer_str,
            ocr_text=ocr_text
        )
        
        print(f"✅ 프롬프트 생성 완료 (길이: {len(prompt)} 문자)")
        return prompt
    
    def call_llm(self, prompt: str, model_name: str = None, reference_example: dict = None) -> dict:
        """
        OpenAI LLM 호출하여 JSON 응답 받기
        
        Args:
            prompt: 완성된 프롬프트
            model_name: 사용할 모델명 (None이면 설정값 사용)
            reference_example: 참조 예제 (키 순서 재정렬용)
            
        Returns:
            파싱된 JSON 딕셔너리
        """
        if model_name is None:
            model_name = self.config.openai_model
        
        print(f"🤖 LLM 호출 중... (모델: {model_name})")
        
        # API 키 확인
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
        
        # OpenAI 클라이언트 생성
        client = OpenAI(api_key=api_key)
        
        # LLM 호출
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            timeout=120
        )
        
        result_text = response.choices[0].message.content
        
        print(f"✅ LLM 응답 받음 (길이: {len(result_text)} 문자)")
        
        # JSON 파싱
        result_text = result_text.strip()
        
        # 코드 블록 제거
        if result_text.startswith('```'):
            result_text = result_text.split('```', 1)[1]
            if result_text.startswith('json'):
                result_text = result_text[4:].strip()
            if result_text.endswith('```'):
                result_text = result_text.rsplit('```', 1)[0].strip()
        
        # Python None/True/False를 JSON null/true/false로 변환
        result_text = re.sub(r':\s*None\s*([,}])', r': null\1', result_text)
        result_text = re.sub(r':\s*True\s*([,}])', r': true\1', result_text)
        result_text = re.sub(r':\s*False\s*([,}])', r': false\1', result_text)
        
        # NaN 문자열을 null로 변환
        import math
        result_text = re.sub(r':\s*NaN\s*([,}])', r': null\1', result_text, flags=re.IGNORECASE)
        result_text = re.sub(r':\s*"NaN"\s*([,}])', r': null\1', result_text, flags=re.IGNORECASE)
        
        # JSON 파싱
        result_json = json.loads(result_text)
        
        # NaN 값 정규화 함수 (재귀적으로 딕셔너리와 리스트를 순회)
        def normalize_nan(obj):
            import math
            if isinstance(obj, dict):
                # Python 3.7+에서는 dict가 삽입 순서를 보존하므로 items() 순서대로 재생성하면 순서 유지
                return {k: normalize_nan(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [normalize_nan(item) for item in obj]
            elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return None
            else:
                return obj
        
        # NaN 값 정규화
        result_json = normalize_nan(result_json)
        
        # null 값 정규화
        if result_json.get("items") is None:
            result_json["items"] = []
            print(f"  ⚠️ items가 null이어서 빈 리스트로 변환했습니다.")
        if result_json.get("page_role") is None:
            result_json["page_role"] = "detail"
            print(f"  ⚠️ page_role이 null이어서 'detail'로 변환했습니다.")
        if not isinstance(result_json.get("items"), list):
            result_json["items"] = []
            print(f"  ⚠️ items가 리스트가 아닙니다. 빈 리스트로 변환합니다.")
        
        # items 내부의 각 항목에서 NaN 값 정규화
        if isinstance(result_json.get("items"), list):
            for item in result_json["items"]:
                if isinstance(item, dict):
                    for key in ['quantity', 'case_count', 'bara_count', 'units_per_case', 'amount']:
                        if key in item and isinstance(item[key], float) and (math.isnan(item[key]) or math.isinf(item[key])):
                            item[key] = None
                            print(f"  ⚠️ {key}가 NaN이어서 null로 변환했습니다.")
        
        # 키 순서 재정렬 (REFERENCE_JSON이 있는 경우)
        if reference_example and reference_example.get("answer_json"):
            # RAG에서 가져온 answer_json은 키 순서가 바뀔 수 있으므로,
            # 원본 파일에서 직접 읽어서 키 순서를 가져옴
            example_answer = None
            
            # 메타데이터에서 원본 파일 정보 가져오기
            metadata = reference_example.get("metadata", {})
            pdf_name = metadata.get("pdf_name")
            page_num = metadata.get("page_num")
            
            if pdf_name and page_num:
                # 원본 answer.json 파일 경로 구성
                project_root = get_project_root()
                img_dir = project_root / "img"
                
                # PDF 이름으로 폴더 찾기
                pdf_folders = list(img_dir.glob(f"*/{pdf_name}"))
                if not pdf_folders:
                    # PDF 이름이 폴더명에 포함된 경우
                    pdf_folders = [d for d in img_dir.iterdir() if d.is_dir() and pdf_name in d.name]
                
                if pdf_folders:
                    pdf_folder = pdf_folders[0]
                    answer_json_path = pdf_folder / f"Page{page_num}_answer.json"
                    
                    if answer_json_path.exists():
                        try:
                            with open(answer_json_path, 'r', encoding='utf-8') as f:
                                example_answer = json.load(f)
                        except Exception:
                            pass
            
            # 원본 파일을 읽지 못한 경우 RAG에서 가져온 것 사용
            if example_answer is None:
                example_answer = reference_example["answer_json"]
            
            result_json = reorder_json_keys(result_json, example_answer)
        
        print(f"✅ JSON 파싱 완료")
        return result_json


def main(filename, page_num):
    """메인 함수"""
    print("=" * 80)
    print("PDF 페이지 RAG 기반 JSON 추출 테스트")
    print("=" * 80)
    
    # PDF 경로와 페이지 번호 설정 (여기서 수정)
    pdf_path = Path(filename)
    
    # 경로 확인
    if not pdf_path.exists():
        print(f"❌ PDF 파일을 찾을 수 없습니다: {pdf_path}")
        print("💡 pdf_path 변수를 올바른 경로로 수정하세요.")
        return
    
    try:
        # RAG 프로세서 생성
        processor = RAGProcessor()
        
        # 1. PDF에서 텍스트 추출 (엑셀 변환 방식)
        ocr_text = processor.extract_text_from_pdf(pdf_path, page_num)
        print()
        
        # 2. RAG로 유사 예제 검색
        similar_examples = processor.search_reference_examples(ocr_text)
        reference_example = similar_examples[0]  # 최상위 예제 사용
        print()
        
        # 3. 프롬프트 생성
        prompt = processor.build_prompt(ocr_text, reference_example)
        print()
        
        # 4. LLM 호출 (reference_example 전달하여 키 순서 재정렬)
        result_json = processor.call_llm(prompt, reference_example=reference_example)
        print()
        
        # 5. 결과 출력
        print("=" * 80)
        print("📊 최종 결과")
        print("=" * 80)
        print(json.dumps(result_json, ensure_ascii=False, indent=2))
        print()
        
        # 결과 요약
        print("=" * 80)
        print("📋 결과 요약")
        print("=" * 80)
        print(f"page_role: {result_json.get('page_role', 'N/A')}")
        print(f"items 개수: {len(result_json.get('items', []))}")
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    filename = "img/02/조건청구서② M0059065511500-農心ジャパン202502/조건청구서② M0059065511500-農心ジャパン202502.pdf"
    
    main(filename=filename, page_num=4)

