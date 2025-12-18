"""
RAG 기반 JSON 추출 모듈

OCR 텍스트를 입력받아 벡터 DB에서 유사한 예제를 검색하고,
그 예제를 컨텍스트로 사용하여 LLM으로 JSON을 추출합니다.
"""

import os
import json
from typing import Dict, Any, Optional, Callable
from openai import OpenAI

from modules.core.rag_manager import get_rag_manager


def extract_json_with_rag(
    ocr_text: str,
    question: str,
    model_name: str = "gpt-4o-2024-08-06",
    temperature: float = 0.0,
    top_k: int = 1,
    similarity_threshold: float = 0.7,
    progress_callback: Optional[Callable[[str], None]] = None,
    debug_dir: Optional[str] = None,
    page_num: Optional[int] = None
) -> Dict[str, Any]:
    """
    RAG 기반 JSON 추출
    
    Args:
        ocr_text: OCR 추출 결과 텍스트
        question: 질문 텍스트 (예: "이 청구서의 상품별 내역을 JSON으로 추출해라")
        model_name: 사용할 OpenAI 모델명 (기본값: gpt-4o-2024-08-06)
        temperature: 모델 temperature (기본값: 0.0)
        top_k: 검색할 예제 수 (기본값: 1)
        similarity_threshold: 최소 유사도 임계값 (기본값: 0.7)
        
    Returns:
        추출된 JSON 딕셔너리
    """
    # API 키 확인
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 필요합니다. .env 파일에 설정하세요.")
    
    # RAG Manager 가져오기
    rag_manager = get_rag_manager()
    
    # 1. Retrieval: 유사한 예제 검색
    if progress_callback:
        progress_callback("벡터 DB에서 유사한 예제 검색 중...")
    
    similar_examples = rag_manager.search_similar(
        query_text=ocr_text,
        top_k=top_k,
        similarity_threshold=similarity_threshold
    )
    
    if progress_callback:
        if similar_examples:
            progress_callback(f"유사한 예제 {len(similar_examples)}개 발견 (유사도: {similar_examples[0].get('similarity', 0):.2f})")
        else:
            progress_callback("유사한 예제 없음 (Zero-shot 모드로 진행)")
    
    # 디버깅: OCR 텍스트 저장
    if debug_dir and page_num:
        try:
            # 디버깅 폴더가 없으면 생성
            os.makedirs(debug_dir, exist_ok=True)
            if not os.path.exists(debug_dir):
                raise Exception(f"디버깅 폴더 생성 실패: {debug_dir}")
            
            ocr_file = os.path.join(debug_dir, f"page_{page_num}_ocr_text.txt")
            with open(ocr_file, 'w', encoding='utf-8') as f:
                f.write(ocr_text)
            print(f"  💾 디버깅: OCR 텍스트 저장 완료 - {ocr_file}")
            
            # RAG 검색 결과 저장
            if similar_examples:
                rag_example_file = os.path.join(debug_dir, f"page_{page_num}_rag_example.json")
                with open(rag_example_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        "similarity": similar_examples[0].get('similarity', 0),
                        "ocr_text": similar_examples[0].get('ocr_text', ''),
                        "answer_json": similar_examples[0].get('answer_json', {})
                    }, f, ensure_ascii=False, indent=2)
                print(f"  💾 디버깅: RAG 예제 저장 완료 - {rag_example_file}")
            else:
                print(f"  💾 디버깅: RAG 예제 없음 (Zero-shot 모드)")
        except Exception as debug_error:
            import traceback
            print(f"⚠️ 디버깅 정보 저장 실패: {debug_error}")
            print(f"  상세:\n{traceback.format_exc()}")
    
    # 2. 프롬프트 구성
    if similar_examples:
        # 예제가 있는 경우: Example-augmented RAG
        example = similar_examples[0]  # 가장 유사한 예제 사용
        example_ocr = example["ocr_text"]
        example_answer = example["answer_json"]
        example_answer_str = json.dumps(example_answer, ensure_ascii=False, indent=2)
        
        prompt = f"""OCR 추출 결과:
{ocr_text}

정답:
{example_answer_str}

**중요**
- ocr_text를 보고 question에 대한 답을 추출
- 답 출력 시에는 불필요한 설명 없이 정답과 같은 JSON 형식으로 출력
- 누락되는 값 없이 모든 제품을 추출
- 추출할 항목이 없는 것은 지어내지 않고 None으로 출력(예: 케이스 개수가 없는 경우에는 None)

질문:
{question}

답:
"""
    else:
        # 예제가 없는 경우: Zero-shot
        prompt = f"""이미지는 일본어 조건청구서(条件請求書) 문서입니다.
OCR 추출 결과를 보고 다음 질문에 대한 답을 JSON 형식으로 추출해주세요.

OCR 추출 결과:
{ocr_text}

질문:
{question}

**중요**
- 답 출력 시에는 불필요한 설명 없이 JSON 형식으로만 출력
- 누락되는 값 없이 모든 제품을 추출
- 추출할 항목이 없는 것은 지어내지 않고 None으로 출력

답:
"""
    
    # 디버깅: 프롬프트 저장
    if debug_dir and page_num:
        try:
            prompt_file = os.path.join(debug_dir, f"page_{page_num}_prompt.txt")
            with open(prompt_file, 'w', encoding='utf-8') as f:
                f.write(prompt)
            print(f"  💾 디버깅: 프롬프트 저장 완료 - {prompt_file}")
        except Exception as debug_error:
            import traceback
            print(f"⚠️ 프롬프트 저장 실패: {debug_error}")
            print(f"  상세:\n{traceback.format_exc()}")
    
    # 3. OpenAI API 호출
    if progress_callback:
        progress_callback(f"🤖 LLM ({model_name})에 요청 중...")
    
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=temperature,
            timeout=120
        )
        result_text = response.choices[0].message.content
        
        # 디버깅: LLM 원본 응답 저장
        if debug_dir and page_num:
            try:
                llm_response_file = os.path.join(debug_dir, f"page_{page_num}_llm_response.txt")
                with open(llm_response_file, 'w', encoding='utf-8') as f:
                    f.write(result_text)
                print(f"  💾 디버깅: LLM 응답 저장 완료 - {llm_response_file}")
            except Exception as debug_error:
                import traceback
                print(f"⚠️ LLM 응답 저장 실패: {debug_error}")
                print(f"  상세:\n{traceback.format_exc()}")
        
        if progress_callback:
            progress_callback("LLM 응답 수신 완료, JSON 파싱 중...")
        
        if not result_text:
            raise Exception("OpenAI API 응답에 텍스트가 없습니다.")
        
        # JSON 추출 (마크다운 코드 블록 제거 및 정리)
        result_text = result_text.strip()
        
        # 마크다운 코드 블록 제거
        if result_text.startswith('```'):
            # 첫 번째 ``` 제거
            result_text = result_text.split('```', 1)[1]
            # json 또는 다른 언어 태그 제거
            if result_text.startswith('json'):
                result_text = result_text[4:].strip()
            elif result_text.startswith('\n'):
                result_text = result_text[1:]
            # 마지막 ``` 제거
            if result_text.endswith('```'):
                result_text = result_text.rsplit('```', 1)[0].strip()
        
        # 앞뒤 공백 및 불필요한 문자 제거
        result_text = result_text.strip()
        
        # Python의 None을 JSON의 null로 치환 (LLM이 None을 출력하는 경우 대비)
        # 단, 문자열 내의 "None"은 치환하지 않도록 주의
        import re
        # "key": None 패턴을 "key": null로 치환
        result_text = re.sub(r':\s*None\s*([,}])', r': null\1', result_text)
        # True/False도 JSON 표준에 맞게 처리
        result_text = re.sub(r':\s*True\s*([,}])', r': true\1', result_text)
        result_text = re.sub(r':\s*False\s*([,}])', r': false\1', result_text)
        
        # JSON 파싱 시도
        try:
            result_json = json.loads(result_text)
            
            # 디버깅: 파싱된 JSON 저장
            if debug_dir and page_num:
                try:
                    parsed_json_file = os.path.join(debug_dir, f"page_{page_num}_llm_response_parsed.json")
                    with open(parsed_json_file, 'w', encoding='utf-8') as f:
                        json.dump(result_json, f, ensure_ascii=False, indent=2)
                    print(f"  💾 디버깅: 파싱된 JSON 저장 완료 - {parsed_json_file}")
                except Exception as debug_error:
                    import traceback
                    print(f"⚠️ 파싱된 JSON 저장 실패: {debug_error}")
                    print(f"  상세:\n{traceback.format_exc()}")
        except json.JSONDecodeError as e:
            # 파싱 실패 시 더 자세한 정보 제공
            error_pos = e.pos if hasattr(e, 'pos') else None
            if error_pos:
                start = max(0, error_pos - 50)
                end = min(len(result_text), error_pos + 50)
                context = result_text[start:end]
                raise Exception(
                    f"JSON 파싱 실패: {e}\n"
                    f"오류 위치 근처 텍스트: ...{context}...\n"
                    f"전체 응답 텍스트:\n{result_text[:500]}..."
                )
            else:
                raise Exception(f"JSON 파싱 실패: {e}\n응답 텍스트:\n{result_text[:500]}...")
        
        return result_json
        
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 파싱 실패: {e}\n응답 텍스트: {result_text}")
    except Exception as e:
        raise Exception(f"OpenAI API 호출 실패: {e}")

