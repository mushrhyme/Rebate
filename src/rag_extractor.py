"""
RAG 기반 JSON 추출 모듈

OCR 텍스트를 입력받아 벡터 DB에서 유사한 예제를 검색하고,
그 예제를 컨텍스트로 사용하여 LLM으로 JSON을 추출합니다.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from openai import OpenAI
import numpy as np

from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_project_root


def convert_numpy_types(obj: Any) -> Any:
    """
    NumPy 타입을 Python 네이티브 타입으로 변환 (JSON 직렬화를 위해)
    
    Args:
        obj: 변환할 객체 (딕셔너리, 리스트, 또는 단일 값)
        
    Returns:
        변환된 객체
    """
    if isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, (np.integer, np.floating)):
        return float(obj) if isinstance(obj, np.floating) else int(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    else:
        return obj


def extract_json_with_rag(
    ocr_text: str,
    question: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.0,
    top_k: Optional[int] = None,
    similarity_threshold: Optional[float] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    debug_dir: Optional[str] = None,
    page_num: Optional[int] = None
) -> Dict[str, Any]:
    """
    RAG 기반 JSON 추출
    
    Args:
        ocr_text: OCR 추출 결과 텍스트
        question: 질문 텍스트 (None이면 config에서 가져옴)
        model_name: 사용할 OpenAI 모델명 (None이면 config에서 가져옴)
        temperature: 모델 temperature (기본값: 0.0)
        top_k: 검색할 예제 수 (None이면 config에서 가져옴)
        similarity_threshold: 최소 유사도 임계값 (None이면 config에서 가져옴)
        
    Returns:
        추출된 JSON 딕셔너리
    """
    # API 키 확인
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 필요합니다. .env 파일에 설정하세요.")
    
    # RAG Manager 및 설정 가져오기 (한 번만 호출)
    rag_manager = get_rag_manager()
    from modules.utils.config import get_rag_config
    config = get_rag_config()  # 설정 한 번만 로드
    
    # 파라미터가 None이면 config에서 가져오기 (notepad 예제와 동일하게 설정값 사용)
    question = question or config.question
    model_name = model_name or config.openai_model
    top_k = top_k if top_k is not None else config.top_k
    similarity_threshold = similarity_threshold if similarity_threshold is not None else config.similarity_threshold
    search_method = getattr(config, 'search_method', 'hybrid')  # 기본값: hybrid
    hybrid_alpha = getattr(config, 'hybrid_alpha', 0.5)  # 기본값: 0.5
    
    if progress_callback:
        if search_method == "hybrid":
            progress_callback("벡터 DB에서 유사한 예제 검색 중 (하이브리드: BM25 + 벡터)...")
        elif search_method == "rerank":
            progress_callback("벡터 DB에서 유사한 예제 검색 중 (Re-ranking)...")
        else:
            progress_callback("벡터 DB에서 유사한 예제 검색 중...")
    
    # 하이브리드 검색 사용
    similar_examples = rag_manager.search_similar_advanced(
        query_text=ocr_text,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        search_method=search_method,
        hybrid_alpha=hybrid_alpha,
        use_preprocessing=True
    )
    
    # 검색 결과가 없으면 threshold를 낮춰서 재검색 (notepad 예제와 동일하게 최상위 결과 사용)
    if not similar_examples:
        print(f"  ⚠️ 검색 결과 없음 (threshold: {similarity_threshold}), threshold를 0.0으로 낮춰 재검색...")
        similar_examples = rag_manager.search_similar_advanced(
            query_text=ocr_text,
            top_k=1,  # 최상위 1개만
            similarity_threshold=0.0,  # threshold 무시
            search_method=search_method,
            hybrid_alpha=hybrid_alpha,
            use_preprocessing=True
        )
        if similar_examples:
            score_key = "hybrid_score" if "hybrid_score" in similar_examples[0] else \
                       "final_score" if "final_score" in similar_examples[0] else \
                       "similarity"
            score_value = similar_examples[0].get(score_key, 0)
            print(f"  ✅ 재검색 성공: {score_key}: {score_value:.4f} (threshold 무시하고 최상위 결과 사용)")
    
    if progress_callback:
        if similar_examples:
            # 점수 키 확인 (hybrid_score, final_score, similarity 중 하나)
            score_key = "hybrid_score" if "hybrid_score" in similar_examples[0] else \
                       "final_score" if "final_score" in similar_examples[0] else \
                       "similarity"
            score_value = similar_examples[0].get(score_key, 0)
            progress_callback(f"유사한 예제 {len(similar_examples)}개 발견 ({score_key}: {score_value:.2f})")
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
                # NumPy 타입을 Python 네이티브 타입으로 변환
                example_data = {
                    "similarity": similar_examples[0].get('similarity', 0),
                    "ocr_text": similar_examples[0].get('ocr_text', ''),
                    "answer_json": similar_examples[0].get('answer_json', {})
                }
                # 추가 점수 필드도 포함 (hybrid_score, bm25_score 등)
                if 'hybrid_score' in similar_examples[0]:
                    example_data["hybrid_score"] = similar_examples[0].get('hybrid_score', 0)
                if 'bm25_score' in similar_examples[0]:
                    example_data["bm25_score"] = similar_examples[0].get('bm25_score', 0)
                if 'final_score' in similar_examples[0]:
                    example_data["final_score"] = similar_examples[0].get('final_score', 0)
                
                # NumPy 타입 변환 후 JSON 저장
                example_data = convert_numpy_types(example_data)
                with open(rag_example_file, 'w', encoding='utf-8') as f:
                    json.dump(example_data, f, ensure_ascii=False, indent=2)
                print(f"  💾 디버깅: RAG 예제 저장 완료 - {rag_example_file}")
            else:
                print(f"  💾 디버깅: RAG 예제 없음 (Zero-shot 모드)")
        except Exception as debug_error:
            import traceback
            print(f"⚠️ 디버깅 정보 저장 실패: {debug_error}")
            print(f"  상세:\n{traceback.format_exc()}")
    
    # 2. 프롬프트 구성 (별도 파일에서 로드)
    project_root = get_project_root()
    prompts_dir = project_root / "prompts"
    
    if similar_examples:
        # 예제가 있는 경우: Example-augmented RAG
        example = similar_examples[0]  # 가장 유사한 예제 사용
        example_ocr = example["ocr_text"]  # RAG 예제의 OCR 텍스트 (given_text)
        example_answer = example["answer_json"]  # RAG 예제의 정답 JSON (given_answer)
        example_answer_str = json.dumps(example_answer, ensure_ascii=False, indent=2)
        
        # 프롬프트 템플릿 파일 로드
        prompt_template_path = prompts_dir / "rag_with_example.txt"
        if prompt_template_path.exists():
            with open(prompt_template_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            prompt = prompt_template.format(
                example_ocr=example_ocr,
                example_answer_str=example_answer_str,
                ocr_text=ocr_text
            )
        else:
            # 파일이 없으면 기본 프롬프트 사용 (하위 호환성)
            prompt = f"""GIVEN_TEXT:
{example_ocr}

위 글이 주어지면 아래의 내용이 정답이야! 
{example_answer_str}

MISSION:
1.너는 위 GIVEN_TEXT를 보고 아래에 주어지는 QUESTION에 대한 답을 찾아내야 해
2.답을 찾을때는 해당 값의 누락이 없어야 해
3.임의로 글을 수정하거나 추가하지 말고 QUESTION의 단어 안에서 답을 찾아내야 해(일본어를 네맘대로 한글로 번역하지 마)
4.출력형식은 **json** 형태여야 해
5.**중요**: items는 항상 배열([])이어야 합니다. 항목이 없으면 빈 배열 []을 반환하세요. null을 반환하지 마세요.
6.**중요**: page_role은 항상 문자열이어야 합니다. "cover", "detail", "summary" 중 하나를 반환하세요. null을 반환하지 마세요.

QUESTION:
{ocr_text}

ANSWER:
"""
    else:
        # 예제가 없는 경우: Zero-shot
        prompt_template_path = prompts_dir / "rag_zero_shot.txt"
        if prompt_template_path.exists():
            with open(prompt_template_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            prompt = prompt_template.format(
                ocr_text=ocr_text,
                question=question
            )
        else:
            # 파일이 없으면 기본 프롬프트 사용 (하위 호환성)
            prompt = f"""이미지는 일본어 조건청구서(条件請求書) 문서입니다.
OCR 추출 결과를 보고 다음 질문에 대한 답을 JSON 형식으로 추출해주세요.

OCR 추출 결과:
{ocr_text}

질문:
{question}

**중요**
- 답 출력 시에는 불필요한 설명 없이 JSON 형식으로만 출력
- 누락되는 값 없이 모든 제품을 추출
- **items는 항상 배열([])이어야 합니다. 항목이 없으면 빈 배열 []을 반환하세요. null을 반환하지 마세요.**
- **page_role은 항상 문자열이어야 합니다. "cover", "detail", "summary", "main" 중 하나를 반환하세요. null을 반환하지 마세요.**

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
        
        # API 호출 전 프롬프트 길이 확인
        print(f"  📝 API 호출: 프롬프트 길이={len(prompt)} 문자, 모델={model_name}, temperature={temperature}")
        
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
        
        # 응답 길이 확인
        print(f"  📥 API 응답: 길이={len(result_text) if result_text else 0} 문자")
        
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
            # NaN 문자열을 null로 변환 (JSON 표준에 맞게)
            import math
            result_text = re.sub(r':\s*NaN\s*([,}])', r': null\1', result_text, flags=re.IGNORECASE)
            result_text = re.sub(r':\s*"NaN"\s*([,}])', r': null\1', result_text, flags=re.IGNORECASE)
            
            result_json = json.loads(result_text)
            
            # NaN 값 정규화 함수 (재귀적으로 딕셔너리와 리스트를 순회)
            def normalize_nan(obj):
                import math
                if isinstance(obj, dict):
                    return {k: normalize_nan(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [normalize_nan(item) for item in obj]
                elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                    return None
                else:
                    return obj
            
            # NaN 값 정규화
            result_json = normalize_nan(result_json)
            
            # null 값 정규화: items가 null이면 빈 리스트로, page_role이 null이면 기본값으로
            if result_json.get("items") is None:
                result_json["items"] = []
                print(f"  ⚠️ items가 null이어서 빈 리스트로 변환했습니다.")
            
            if result_json.get("page_role") is None:
                result_json["page_role"] = "detail"  # 기본값
                print(f"  ⚠️ page_role이 null이어서 'detail'로 변환했습니다.")
            
            # items가 리스트가 아닌 경우 빈 리스트로 변환
            if not isinstance(result_json.get("items"), list):
                print(f"  ⚠️ items가 리스트가 아닙니다 ({type(result_json.get('items'))}). 빈 리스트로 변환합니다.")
                result_json["items"] = []
            
            # items 내부의 각 항목에서 NaN 값 정규화
            if isinstance(result_json.get("items"), list):
                for item in result_json["items"]:
                    if isinstance(item, dict):
                        for key in ['quantity', 'case_count', 'bara_count', 'units_per_case', 'amount']:
                            if key in item and isinstance(item[key], float) and (math.isnan(item[key]) or math.isinf(item[key])):
                                item[key] = None
                                print(f"  ⚠️ {key}가 NaN이어서 null로 변환했습니다.")
            
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

