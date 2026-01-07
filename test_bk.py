"""
RAG 벡터 DB 테스트 스크립트

1. PDF를 읽어서 (fitz) 텍스트를 추출
2. RAG로 정답지를 불러내서 참고 문서 정보 표시 (또는 JSON 파일에서 직접 읽기)
3. 최종 프롬프트 생성하여 OpenAI에 요청
4. 최종 응답 표시

사용법:
    python test.py
"""

import os
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_rag_config, get_project_root
from modules.utils.pdf_utils import extract_text_from_pdf_page
from openai import OpenAI


# ============================================================================
# 설정 섹션 (하드코딩)
# ============================================================================

@dataclass
class TestConfig:
    """테스트 설정값"""
    # PDF 파일 설정
    pdf_file_path: str = "test_img/01/三菱食品東日本_2025.01 (1).pdf"  # PDF 파일 경로 (상대 또는 절대)
    page_num: int = 1  # 페이지 번호 (1부터 시작)
    
    # RAG 모드 설정
    rag_mode: str = "json"  # "json" 또는 "rag"
    # - "json": JSON 파일에서 예제를 직접 읽기
    # - "rag": RAG 벡터 DB에서 검색하여 예제 가져오기
    
    # JSON 모드 설정 (rag_mode가 "json"일 때 사용)
    json_example_path: str = "img/01/조건청구서① 20250206002380938001_46558204002_加藤産業株式?社(福岡支店)/Page1_answer.json"  # 예제 JSON 파일 경로
    json_example_ocr_path: Optional[str] = None  # 예제 OCR 텍스트 파일 경로 (선택적, 없으면 PDF에서 추출)
    
    # 프롬프트 파일 설정
    prompt_file_path: str = "prompts/rag_with_example_v3.txt"  # 프롬프트 파일 경로 (상대 경로)
    
    # OCR 텍스트 직접 입력 (선택적, None이면 PDF에서 추출)
    ocr_text_override: Optional[str] = None


# 전역 설정 인스턴스
test_config = TestConfig(
    pdf_file_path="test_img/01/三菱食品東日本_2025.01 (1).pdf",
    page_num=1,
    rag_mode="json",
    json_example_path="img/01/조건청구서① 20250206002380938001_46558204002_加藤産業株式?社(福岡支店)/Page1_answer.json",
    json_example_ocr_path=None,
    prompt_file_path="prompts/rag_with_example_v3.txt",
    ocr_text_override=None
)


# ============================================================================
# 유틸리티 클래스
# ============================================================================

class PromptLoader:
    """프롬프트 파일 로더"""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
    
    def load_prompt(self, prompt_file_path: str) -> str:
        """
        프롬프트 파일을 읽어옵니다.
        
        Args:
            prompt_file_path: 프롬프트 파일 경로 (상대 또는 절대)
            
        Returns:
            프롬프트 텍스트
        """
        prompt_path = Path(prompt_file_path)
        if not prompt_path.is_absolute():
            prompt_path = self.project_root / prompt_file_path
        
        if not prompt_path.exists():
            raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {prompt_path}")
        
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    @staticmethod
    def extract_version(prompt_file_path: str) -> str:
        """
        프롬프트 파일명에서 버전을 추출합니다.
        
        Args:
            prompt_file_path: 프롬프트 파일 경로
            
        Returns:
            버전 문자열 (예: "v1", "v2", "v3")
        """
        prompt_name = Path(prompt_file_path).stem  # 확장자 제거
        # rag_with_example_v2.txt -> v2
        # rag_with_example_v3.txt -> v3
        # rag_with_example.txt -> v1 (기본값)
        
        import re
        match = re.search(r'_v(\d+)$', prompt_name)
        if match:
            return f"v{match.group(1)}"
        else:
            return "v1"  # 기본값


class ExampleLoader:
    """예제 JSON 파일 로더"""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
    
    def load_example_json(self, json_path: str) -> Dict[str, Any]:
        """
        예제 JSON 파일을 읽어옵니다.
        
        Args:
            json_path: JSON 파일 경로 (상대 또는 절대)
            
        Returns:
            JSON 데이터 (dict)
        """
        json_file_path = Path(json_path)
        if not json_file_path.is_absolute():
            json_file_path = self.project_root / json_path
        
        if not json_file_path.exists():
            raise FileNotFoundError(f"JSON 파일을 찾을 수 없습니다: {json_file_path}")
        
        with open(json_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def load_example_ocr(self, ocr_path: Optional[str], pdf_path: Path, page_num: int) -> str:
        """
        예제 OCR 텍스트를 읽어옵니다.
        
        Args:
            ocr_path: OCR 텍스트 파일 경로 (None이면 PDF에서 추출)
            pdf_path: PDF 파일 경로
            page_num: 페이지 번호
            
        Returns:
            OCR 텍스트
        """
        if ocr_path:
            # 파일에서 읽기
            ocr_file_path = Path(ocr_path)
            if not ocr_file_path.is_absolute():
                ocr_file_path = self.project_root / ocr_path
            
            if not ocr_file_path.exists():
                raise FileNotFoundError(f"OCR 파일을 찾을 수 없습니다: {ocr_file_path}")
            
            with open(ocr_file_path, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            # PDF에서 추출
            return extract_text_from_pdf_page(pdf_path, page_num)


class RAGProcessor:
    """RAG 처리 클래스 (직접 수행 또는 JSON 파일 읽기)"""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.rag_manager = None
        self.example_loader = ExampleLoader(project_root)
    
    def get_example_from_json(
        self,
        json_path: str,
        ocr_path: Optional[str],
        pdf_path: Path,
        page_num: int
    ) -> Dict[str, Any]:
        """
        JSON 파일에서 예제를 읽어옵니다.
        
        Args:
            json_path: 예제 JSON 파일 경로
            ocr_path: 예제 OCR 텍스트 파일 경로 (None이면 PDF에서 추출)
            pdf_path: PDF 파일 경로 (OCR 추출용)
            page_num: 페이지 번호 (OCR 추출용)
            
        Returns:
            예제 딕셔너리 (ocr_text, answer_json 포함)
        """
        answer_json = self.example_loader.load_example_json(json_path)
        ocr_text = self.example_loader.load_example_ocr(ocr_path, pdf_path, page_num)
        
        return {
            'ocr_text': ocr_text,
            'answer_json': answer_json,
            'id': f"json_{json_path}",
            'similarity': 1.0,  # JSON 모드에서는 유사도 1.0으로 설정
            'pdf_name': Path(json_path).parent.name,
            'page_num': page_num
        }
    
    def get_example_from_rag(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        search_method: str = "hybrid",
        hybrid_alpha: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        RAG 벡터 DB에서 예제를 검색합니다.
        
        Args:
            query_text: 검색 쿼리 텍스트
            top_k: 검색할 예제 수
            similarity_threshold: 최소 유사도 임계값
            search_method: 검색 방식 ("vector", "hybrid")
            hybrid_alpha: 하이브리드 검색 가중치
            
        Returns:
            검색된 예제 리스트
        """
        if self.rag_manager is None:
            self.rag_manager = get_rag_manager()
        
        # 벡터 DB 상태 확인
        example_count = self.rag_manager.count_examples()
        if example_count == 0:
            raise ValueError("벡터 DB에 예제가 없습니다. build_faiss_db.py를 먼저 실행하세요.")
        
        if self.rag_manager.index is None or self.rag_manager.index.ntotal == 0:
            raise ValueError("인덱스가 비어있습니다. 벡터 DB를 다시 구축해야 합니다.")
        
        # 검색 수행
        similar_examples = self.rag_manager.search_similar_advanced(
            query_text=query_text,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            search_method=search_method,
            hybrid_alpha=hybrid_alpha
        )
        
        # 검색 결과가 없으면 threshold를 낮춰서 재검색
        if not similar_examples:
            print(f"⚠️ 검색 결과 없음 (threshold: {similarity_threshold})")
            print("🔄 threshold를 0.0으로 낮춰 재검색...")
            similar_examples = self.rag_manager.search_similar_advanced(
                query_text=query_text,
                top_k=1,
                similarity_threshold=0.0,
                search_method=search_method,
                hybrid_alpha=hybrid_alpha
            )
        
        return similar_examples


class PromptBuilder:
    """프롬프트 빌더"""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.prompt_loader = PromptLoader(project_root)
    
    def build_prompt(
        self,
        ocr_text: str,
        prompt_file_path: str,
        example: Optional[Dict[str, Any]] = None,
        question: Optional[str] = None
    ) -> str:
        """
        프롬프트를 생성합니다.
        
        Args:
            ocr_text: 현재 페이지 OCR 텍스트
            prompt_file_path: 프롬프트 파일 경로
            example: 예제 딕셔너리 (ocr_text, answer_json 포함)
            question: Zero-shot 모드용 질문 (예제가 없을 때)
            
        Returns:
            생성된 프롬프트 텍스트
        """
        prompt_template = self.prompt_loader.load_prompt(prompt_file_path)
        
        if example:
            # Example-augmented RAG 모드
            example_ocr = example['ocr_text']
            example_answer = example['answer_json']
            example_answer_str = json.dumps(example_answer, ensure_ascii=False, indent=2)
            
            # 프롬프트 템플릿의 플레이스홀더 치환
            prompt = prompt_template.format(
                example_ocr=example_ocr,
                example_answer_str=example_answer_str,
                ocr_text=ocr_text
            )
        else:
            # Zero-shot 모드
            if question is None:
                question = "이 페이지의 상품명, 수량, 금액 등 항목 정보를 모두 추출해줘"
            
            prompt = prompt_template.format(
                ocr_text=ocr_text,
                question=question
            )
        
        return prompt


class OpenAIClient:
    """OpenAI API 클라이언트"""
    
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다. .env 파일에 OPENAI_API_KEY를 설정하세요.")
        self.client = OpenAI(api_key=api_key)
    
    def call_api(
        self,
        prompt: str,
        # model_name: str = "gpt-4o-2024-11-20", 
        model_name: str = "gpt-4o-2024-08-06",
        temperature: float = 0.0,
        timeout: int = 120
    ) -> str:
        """
        OpenAI API를 호출합니다.
        
        Args:
            prompt: 프롬프트 텍스트
            model_name: 모델명
            temperature: 온도
            timeout: 타임아웃 (초)
            
        Returns:
            API 응답 텍스트
        """
        response = self.client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=temperature,
            timeout=timeout
        )
        
        result_text = response.choices[0].message.content
        if not result_text:
            raise Exception("OpenAI API 응답에 텍스트가 없습니다.")
        
        return result_text
    
    @staticmethod
    def parse_json_response(result_text: str) -> Dict[str, Any]:
        """
        OpenAI API 응답을 JSON으로 파싱합니다.
        
        Args:
            result_text: API 응답 텍스트
            
        Returns:
            파싱된 JSON 딕셔너리
        """
        result_text_cleaned = result_text.strip()
        
        # 마크다운 코드 블록 제거
        if result_text_cleaned.startswith('```'):
            result_text_cleaned = result_text_cleaned.split('```', 1)[1]
            if result_text_cleaned.startswith('json'):
                result_text_cleaned = result_text_cleaned[4:].strip()
            elif result_text_cleaned.startswith('\n'):
                result_text_cleaned = result_text_cleaned[1:]
            if result_text_cleaned.endswith('```'):
                result_text_cleaned = result_text_cleaned.rsplit('```', 1)[0].strip()
        
        result_text_cleaned = result_text_cleaned.strip()
        
        # Python의 None/True/False를 JSON의 null/true/false로 치환
        result_text_cleaned = re.sub(r':\s*None\s*([,}])', r': null\1', result_text_cleaned)
        result_text_cleaned = re.sub(r':\s*True\s*([,}])', r': true\1', result_text_cleaned)
        result_text_cleaned = re.sub(r':\s*False\s*([,}])', r': false\1', result_text_cleaned)
        
        return json.loads(result_text_cleaned)


# ============================================================================
# 메인 함수
# ============================================================================

def main():
    """메인 실행 함수"""
    print("="*70)
    print("🚀 RAG 벡터 DB 테스트 시작")
    print("="*70)
    
    config = test_config
    project_root = get_project_root()
    rag_config = get_rag_config()
    
    # 1. PDF 파일 경로 확인
    print("\n📄 1단계: PDF 파일 확인")
    print("-"*70)
    pdf_path = Path(config.pdf_file_path)
    if not pdf_path.is_absolute():
        pdf_path = project_root / config.pdf_file_path
    
    if not pdf_path.exists():
        print(f"❌ PDF 파일을 찾을 수 없습니다: {pdf_path}")
        return
    
    print(f"📁 PDF 파일: {pdf_path.name}")
    print(f"📂 전체 경로: {pdf_path}")
    
    # 페이지 번호 검증 및 수정
    page_num = config.page_num
    if page_num < 1:
        print(f"⚠️ 페이지 번호가 1보다 작습니다 ({page_num}). 1로 변경합니다.")
        page_num = 1
    
    print(f"📄 페이지: {page_num}")
    
    # PDF 페이지 수 확인
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count
        doc.close()
        
        if page_num > total_pages:
            print(f"❌ 페이지 번호가 PDF 페이지 수를 초과합니다. (요청: {page_num}, 전체: {total_pages})")
            return
        
        print(f"📊 PDF 전체 페이지 수: {total_pages}")
    except Exception as e:
        print(f"⚠️ PDF 페이지 수 확인 실패: {e}")
    
    # 2. OCR 텍스트 추출
    print("\n📝 2단계: OCR 텍스트 추출")
    print("-"*70)
    
    if config.ocr_text_override:
        ocr_text = config.ocr_text_override
        print("✅ 하드코딩된 OCR 텍스트 사용")
    else:
        print("🔄 PDF에서 텍스트 추출 중...")
        ocr_text = extract_text_from_pdf_page(pdf_path, page_num)
        
        if not ocr_text or not ocr_text.strip():
            print("❌ 텍스트를 추출할 수 없습니다.")
            print(f"   - PDF 경로: {pdf_path}")
            print(f"   - 페이지 번호: {page_num}")
            print(f"   - PDF 존재 여부: {pdf_path.exists()}")
            return
        
        print(f"✅ 텍스트 추출 완료 (길이: {len(ocr_text)} 문자)")
    
    # print(f"\n📝 추출된 텍스트 미리보기:")
    # print(ocr_text[:500] + "..." if len(ocr_text) > 500 else ocr_text)        
    
    
    # 3. 예제 가져오기 (JSON 또는 RAG)
    print("\n🔍 3단계: 예제 가져오기")
    print("-"*70)
    print(f"🔧 RAG 모드: {config.rag_mode}")
    
    rag_processor = RAGProcessor(project_root)
    similar_examples = []
    reference_docs = []
    
    if config.rag_mode == "json":
        # JSON 파일에서 예제 읽기
        print(f"📂 JSON 파일 경로: {config.json_example_path}")
        
        try:
            # JSON 파일의 페이지 번호 추출 (파일명에서)
            json_page_num = config.page_num  # 기본값
            json_path_str = str(config.json_example_path)
            if "Page" in json_path_str:
                import re
                match = re.search(r'Page(\d+)', json_path_str)
                if match:
                    json_page_num = int(match.group(1))
            
            example = rag_processor.get_example_from_json(
                json_path=config.json_example_path,
                ocr_path=config.json_example_ocr_path,
                pdf_path=pdf_path,
                page_num=json_page_num
            )
            similar_examples = [example]
            
            print(f"✅ JSON 파일에서 예제 로드 완료")
            print(f"   - PDF: {example.get('pdf_name', 'Unknown')}")
            print(f"   - Page: {example.get('page_num', 'Unknown')}")
            print(f"   - Page Role: {example['answer_json'].get('page_role', 'N/A')}")
            
            reference_docs.append({
                'rank': 1,
                'similarity': 1.0,
                'pdf_name': example.get('pdf_name', 'Unknown'),
                'page_num': example.get('page_num', 'Unknown'),
                'page_role': example['answer_json'].get('page_role', 'N/A')
            })
            
        except Exception as e:
            print(f"❌ JSON 파일 로드 실패: {e}")
            return
    
    elif config.rag_mode == "rag":
        # RAG 벡터 DB에서 검색
        print("🔄 RAG 벡터 DB에서 검색 중...")
        
        try:
            similar_examples = rag_processor.get_example_from_rag(
                query_text=ocr_text,
                top_k=rag_config.top_k,
                similarity_threshold=rag_config.similarity_threshold,
                search_method=rag_config.search_method,
                hybrid_alpha=rag_config.hybrid_alpha
            )
            
            if not similar_examples:
                print("❌ 검색 결과가 없습니다.")
                return
            
            print(f"✅ 검색 완료: {len(similar_examples)}개 예제 발견")
            
            # 참고 문서 정보 수집
            for idx, ex in enumerate(similar_examples, 1):
                doc_info = {
                    'rank': idx,
                    'similarity': ex.get('similarity', 0.0),
                    'hybrid_score': ex.get('hybrid_score', None),
                    'pdf_name': 'Unknown',
                    'page_num': 'Unknown',
                    'page_role': ex['answer_json'].get('page_role', 'N/A')
                }
                
                # 메타데이터에서 PDF 정보 추출
                if 'id' in ex:
                    doc_id = ex['id']
                    rag_manager = rag_processor.rag_manager
                    all_examples = rag_manager.get_all_examples()
                    for example in all_examples:
                        if example['id'] == doc_id:
                            metadata = example.get('metadata', {})
                            doc_info['pdf_name'] = metadata.get('pdf_name', 'Unknown')
                            doc_info['page_num'] = metadata.get('page_num', 'Unknown')
                            break
                
                reference_docs.append(doc_info)
                
                print(f"\n[{idx}] 예제 정보:")
                print(f"   - 유사도: {doc_info['similarity']:.4f}")
                if doc_info['hybrid_score']:
                    print(f"   - Hybrid Score: {doc_info['hybrid_score']:.4f}")
                print(f"   - PDF: {doc_info['pdf_name']} - Page{doc_info['page_num']}")
                print(f"   - Page Role: {doc_info['page_role']}")
        
        except Exception as e:
            print(f"❌ RAG 검색 실패: {e}")
            return
    
    else:
        print(f"❌ 잘못된 RAG 모드: {config.rag_mode} (지원: 'json', 'rag')")
        return
    
    # 4. 프롬프트 생성
    print("\n📝 4단계: 프롬프트 생성")
    print("-"*70)
    print(f"📂 프롬프트 파일: {config.prompt_file_path}")
    
    prompt_builder = PromptBuilder(project_root)
    
    try:
        example = similar_examples[0] if similar_examples else None
        question = rag_config.question if not example else None
        
        prompt = prompt_builder.build_prompt(
            ocr_text=ocr_text,
            prompt_file_path=config.prompt_file_path,
            example=example,
            question=question
        )
        
        print(f"✅ 프롬프트 생성 완료 (길이: {len(prompt)} 문자)")
        # print(f"\n📋 프롬프트 미리보기:")
        # print(prompt[:1000] + "..." if len(prompt) > 1000 else prompt)
        with open("tmp.txt", "w", encoding="utf-8") as f:
            f.write(prompt)
    except Exception as e:
        print(f"❌ 프롬프트 생성 실패: {e}")
        return
    
    # 5. OpenAI API 호출
    print("\n🤖 5단계: OpenAI API 호출")
    print("-"*70)
    
    try:
        openai_client = OpenAIClient()
        
        print(f"🔄 OpenAI API 호출 중...")
        print(f"   모델: {rag_config.openai_model}")
        print(f"   프롬프트 길이: {len(prompt)} 문자")
        
        result_text = openai_client.call_api(
            prompt=prompt,
            model_name=rag_config.openai_model,
            temperature=0.0,
            timeout=120
        )
        
        print("✅ API 호출 완료!")
        
        # JSON 파싱
        result_json = openai_client.parse_json_response(result_text)
        
        # 6. 결과 파일 저장
        print("\n💾 6단계: 결과 파일 저장")
        print("-"*70)
        
        # 프롬프트 파일명에서 버전 추출
        prompt_loader = PromptLoader(project_root)
        version = prompt_loader.extract_version(config.prompt_file_path)
        
        # 저장 경로 생성: PDF 파일과 같은 디렉토리
        pdf_dir = pdf_path.parent  # PDF 파일이 있는 디렉토리
        pdf_name_without_ext = pdf_path.stem  # 확장자 제거한 파일명
        
        # PDF 파일명으로 하위 디렉토리 생성 (없으면 생성)
        output_dir = pdf_dir / pdf_name_without_ext
        output_dir.mkdir(exist_ok=True)
        
        # 결과 파일명: Page{page_num}_answer_{version}.json
        output_filename = f"Page{page_num}_answer_{version}.json"
        output_path = output_dir / output_filename
        
        # JSON 파일 저장
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_json, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 결과 파일 저장 완료:")
        print(f"   📁 경로: {output_path}")
        print(f"   📄 파일명: {output_filename}")
        print(f"   🏷️  버전: {version}")
        
        # 7. 최종 결과 표시
        print("\n✅ 7단계: 최종 결과")
        print("="*70)
        
        print("\n📋 OpenAI 원본 응답:")
        print("-"*70)
        print(result_text[:500] + "..." if len(result_text) > 500 else result_text)
        
        print("\n📊 파싱된 JSON 결과:")
        print("-"*70)
        print(json.dumps(result_json, ensure_ascii=False, indent=2))
        
        # 결과 요약
        page_role = result_json.get('page_role', 'N/A')
        items = result_json.get('items', [])
        items_count = len(items) if items else 0
        
        print("\n" + "="*70)
        print("📊 결과 요약")
        print("="*70)
        print(f"  📄 Page Role: {page_role}")
        print(f"  📦 Items 개수: {items_count}개")
        if items_count > 0:
            print(f"\n  📝 첫 번째 항목:")
            first_item = items[0]
            for key, value in first_item.items():
                if isinstance(value, (str, int, float)) and len(str(value)) < 100:
                    print(f"     - {key}: {value}")
        
        # 참고 문서 정보 요약
        if reference_docs:
            print("\n" + "="*70)
            print("📚 활용한 참고 문서")
            print("="*70)
            for doc in reference_docs:
                print(f"  [{doc['rank']}] {doc['pdf_name']} - Page{doc['page_num']}")
                print(f"      - 유사도: {doc['similarity']:.4f}")
                if doc.get('hybrid_score'):
                    print(f"      - Hybrid Score: {doc['hybrid_score']:.4f}")
                print(f"      - Page Role: {doc['page_role']}")
        
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON 파싱 실패: {e}")
        print(f"\n원본 응답:")
        print(result_text)
        import traceback
        traceback.print_exc()
    
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*70)
    print("✅ 테스트 완료!")
    print("="*70)


if __name__ == "__main__":
    main()
