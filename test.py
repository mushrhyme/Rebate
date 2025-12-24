"""
RAG 벡터 DB 테스트 스크립트

1. PDF를 읽어서 (fitz) 텍스트를 추출
2. RAG로 정답지를 불러내서 참고 문서 정보 표시
3. 최종 프롬프트 생성하여 OpenAI에 요청
4. 최종 응답 표시

사용법:
    python test.py <pdf_file_path> [page_num]
    
예제:
    python test.py img/日本アクセス東京中央支店/日本アクセス東京中央支店.pdf 1
    python test.py img/日本アクセス東京中央支店/日本アクセス東京中央支店.pdf
"""

import os
import json
from pathlib import Path
import fitz  # PyMuPDF

from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_rag_config, get_project_root
from openai import OpenAI


def extract_text_from_pdf_page(pdf_path: Path, page_num: int) -> str:
    """
    fitz를 사용하여 PDF에서 특정 페이지의 텍스트를 추출합니다.
    
    Args:
        pdf_path: PDF 파일 경로
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        추출된 텍스트 (없으면 빈 문자열)
    """
    try:
        if not pdf_path.exists():
            return ""
        
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > doc.page_count:
            doc.close()
            return ""
        
        page = doc.load_page(page_num - 1)  # fitz는 0부터 시작
        text = page.get_text()
        doc.close()
        
        return text.strip() if text else ""
    except Exception as e:
        print(f"⚠️ PDF 텍스트 추출 실패 ({pdf_path}, 페이지 {page_num}): {e}")
        return ""


def main():
    print("="*70)
    print("🚀 RAG 벡터 DB 테스트 시작")
    print("="*70)
    
    pdf_file_path = "test_img/01/コゲツ産業2025.01 (1).pdf"
    page_num = 2
    
    # PDF 파일 경로 확인
    pdf_path = Path(pdf_file_path)
    if not pdf_path.is_absolute():
        # 상대 경로인 경우 프로젝트 루트 기준으로 변환
        project_root = get_project_root()
        pdf_path = project_root / pdf_file_path
    
    if not pdf_path.exists():
        print(f"\n❌ PDF 파일을 찾을 수 없습니다: {pdf_path}")
        return
    
    # 2. 벡터 DB 상태 확인
    print("\n📊 1단계: 벡터 DB 상태 확인")
    print("-"*70)
    rag_manager = get_rag_manager()
    example_count = rag_manager.count_examples()
    print(f"✅ 벡터 DB 예제 수: {example_count}개\n")
    
    if example_count == 0:
        print("⚠️ 벡터 DB에 예제가 없습니다. build_faiss_db.py를 먼저 실행하세요.")
        return
    
    # 3. PDF 파일 정보 표시
    print("📄 2단계: PDF 파일 선택 및 텍스트 추출")
    print("-"*70)
    print(f"📁 PDF 파일: {pdf_path.name}")
    print(f"📂 전체 경로: {pdf_path}")
    print(f"📄 페이지: {page_num}\n")
    
    # 3. PDF에서 텍스트 추출 (fitz 사용)
    print("🔄 fitz를 사용하여 PDF에서 텍스트 추출 중...")
    ocr_text = extract_text_from_pdf_page(pdf_path, page_num)
    
    if not ocr_text or not ocr_text.strip():
        print("❌ 텍스트를 추출할 수 없습니다.")
        return
    
    print(f"✅ 텍스트 추출 완료 (길이: {len(ocr_text)} 문자)\n")
    print("="*70)
    print("📝 추출된 텍스트")
    print("="*70)
    print(ocr_text[:500] + "..." if len(ocr_text) > 500 else ocr_text)
    print()
    
    # 4. RAG 검색
    print("="*70)
    print("🔍 3단계: RAG 벡터 DB에서 유사한 예제 검색")
    print("="*70)
    
    config = get_rag_config()
    similar_examples = rag_manager.search_similar_advanced(
        query_text=ocr_text,
        top_k=config.top_k,
        similarity_threshold=config.similarity_threshold,
        search_method=config.search_method,
        hybrid_alpha=config.hybrid_alpha,
        use_preprocessing=True
    )
    
    print(f"\n📊 검색 결과: {len(similar_examples)}개\n")
    
    # 참고 문서 정보 표시
    reference_docs = []
    for idx, ex in enumerate(similar_examples, 1):
        print(f"[{idx}] " + "="*60)
        print(f"  📌 유사도 점수:")
        if 'hybrid_score' in ex:
            print(f"     - Hybrid Score: {ex['hybrid_score']:.4f}")
        if 'bm25_score' in ex:
            print(f"     - BM25 Score: {ex['bm25_score']:.4f}")
        print(f"     - Similarity: {ex['similarity']:.4f}")
        
        # 참고 문서 정보 수집
        doc_info = {
            'rank': idx,
            'similarity': ex['similarity'],
            'hybrid_score': ex.get('hybrid_score', None),
            'pdf_name': 'Unknown',
            'page_num': 'Unknown',
            'page_role': ex['answer_json'].get('page_role', 'N/A')
        }
        
        # 메타데이터에서 PDF 정보 추출
        if 'id' in ex:
            doc_id = ex['id']
            all_examples = rag_manager.get_all_examples()
            for example in all_examples:
                if example['id'] == doc_id:
                    metadata = example.get('metadata', {})
                    doc_info['pdf_name'] = metadata.get('pdf_name', 'Unknown')
                    doc_info['page_num'] = metadata.get('page_num', 'Unknown')
                    break
        
        print(f"  📁 참고 문서: {doc_info['pdf_name']} - Page{doc_info['page_num']}")
        print(f"  🏷️  Page Role: {doc_info['page_role']}")
        print(f"  📝 OCR 텍스트 미리보기:")
        print(f"     {ex['ocr_text'][:200]}...")
        print()
        
        reference_docs.append(doc_info)
    
    # 5. 프롬프트 생성 (rag_extractor 참고)
    print("="*70)
    print("📝 4단계: 최종 프롬프트 생성")
    print("="*70)
    
    if similar_examples:
        # 예제가 있는 경우: Example-augmented RAG
        example = similar_examples[0]  # 가장 유사한 예제 사용
        example_ocr = example["ocr_text"]  # RAG 예제의 OCR 텍스트 (given_text)
        example_answer = example["answer_json"]  # RAG 예제의 정답 JSON (given_answer)
        example_answer_str = json.dumps(example_answer, ensure_ascii=False, indent=2)
        
        # prompting.py 형식: given_text(예제 OCR)와 given_answer(예제 정답)를 보여주고,
        # question(현재 페이지 OCR)에서 같은 형식으로 추출하도록 지시
        prompt = f"""GIVEN_TEXT:
{example_ocr}

위 글이 주어지면 아래의 내용이 정답이야! 
{example_answer_str}

MISSION:
1.너는 위 GIVEN_TEXT를 보고 아래에 주어지는 QUESTION에 대한 답을 찾아내세요.
2.답을 찾을때는 해당 값의 누락이 없어야 합니다.
3.임의로 글을 수정하거나 추가하지 말고 QUESTION의 단어 안에서 답을 찾아내세요.
4.출력형식은 **json** 형태여야 합니다
5.**중요**: items는 항상 배열([])이어야 합니다. 항목이 없으면 빈 배열 []을 반환하세요. null을 반환하지 마세요.
6.**중요**: page_role은 항상 문자열이어야 합니다. "cover", "detail", "summary" 중 하나를 반환하세요. null을 반환하지 마세요.

QUESTION:
{ocr_text}

ANSWER:
"""
    else:
        # 예제가 없는 경우: Zero-shot
        question = config.question
        prompt = f"""이미지는 일본어 조건청구서(条件請求書) 문서입니다.
OCR 추출 결과를 보고 다음 질문에 대한 답을 JSON 형식으로 추출해주세요.

OCR 추출 결과:
{ocr_text}

질문:
{question}

**중요**
- 답 출력 시에는 불필요한 설명 없이 JSON 형식으로만 출력
- 누락되는 값 없이 모든 제품을 추출
- **items는 항상 배열([])이어야 합니다. 항목이 없으면 빈 배열 []을 반환하세요. null을 반환하지 마세요. 항목이 없는 경우는 cover 또는 summary입니다.**
- **page_role은 항상 문자열이어야 합니다. "cover", "detail", "summary" 중 하나를 반환하세요. null을 반환하지 마세요.**

답:
"""
    
    print("\n📋 생성된 프롬프트:")
    print("-"*70)
    print(prompt[:1000] + "..." if len(prompt) > 1000 else prompt)
    print()
    
    # 6. OpenAI API 호출
    print("="*70)
    print("🤖 5단계: OpenAI API 호출")
    print("="*70)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ OPENAI_API_KEY가 설정되지 않았습니다.")
        print("   .env 파일에 OPENAI_API_KEY를 설정하세요.")
        return
    
    try:
        client = OpenAI(api_key=api_key)
        model_name = config.openai_model
        
        print(f"\n🔄 OpenAI API 호출 중...")
        print(f"   모델: {model_name}")
        print(f"   프롬프트 길이: {len(prompt)} 문자\n")
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.0,
            timeout=120
        )
        
        result_text = response.choices[0].message.content
        
        if not result_text:
            raise Exception("OpenAI API 응답에 텍스트가 없습니다.")
        
        print("✅ API 호출 완료!\n")
        
        # JSON 파싱
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
        
        # Python의 None을 JSON의 null로 치환
        import re
        result_text_cleaned = re.sub(r':\s*None\s*([,}])', r': null\1', result_text_cleaned)
        result_text_cleaned = re.sub(r':\s*True\s*([,}])', r': true\1', result_text_cleaned)
        result_text_cleaned = re.sub(r':\s*False\s*([,}])', r': false\1', result_text_cleaned)
        
        result_json = json.loads(result_text_cleaned)
        
        # 7. 최종 결과 표시
        print("="*70)
        print("✅ 6단계: 최종 결과")
        print("="*70)
        
        print("\n📋 OpenAI 원본 응답:")
        print("-"*70)
        print(result_text[:500] + "..." if len(result_text) > 500 else result_text)
        print()
        
        print("📊 파싱된 JSON 결과:")
        print("-"*70)
        print(json.dumps(result_json, ensure_ascii=False, indent=2))
        print()
        
        # 결과 요약
        page_role = result_json.get('page_role', 'N/A')
        items = result_json.get('items', [])
        items_count = len(items) if items else 0
        
        print("="*70)
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
        print()
        
        # 참고 문서 정보 요약
        if reference_docs:
            print("="*70)
            print("📚 활용한 참고 문서")
            print("="*70)
            for doc in reference_docs:
                print(f"  [{doc['rank']}] {doc['pdf_name']} - Page{doc['page_num']}")
                print(f"      - 유사도: {doc['similarity']:.4f}")
                if doc['hybrid_score']:
                    print(f"      - Hybrid Score: {doc['hybrid_score']:.4f}")
                print(f"      - Page Role: {doc['page_role']}")
            print()
        
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
    
    print("="*70)
    print("✅ 테스트 완료!")
    print("="*70)


if __name__ == "__main__":
    main()
