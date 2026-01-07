"""
OpenAI API 유틸리티 모듈

정답지 편집 탭 등에서 사용하는 OpenAI API 호출 함수들을 모아둔 모듈입니다.
PDF 전체 분석은 RAG 기반 분석만 사용하며, 이 모듈은 정답지 편집 등 특수한 경우에만 사용됩니다.
"""

import os
import json
from typing import Dict, Any, Optional
from openai import OpenAI

# langchain_openai는 선택적 import (없어도 동작하도록)
try:
    from langchain_openai import ChatOpenAI
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    ChatOpenAI = None


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


def ask_openai_with_reference(
    ocr_text: str,
    answer_json: dict,
    question: str,
    model_name: str = "gpt-4o-2024-08-06",
    use_langchain: bool = False,
    temperature: float = 0.0
) -> dict:
    """
    OpenAI API를 사용하여 참조 정답을 기반으로 질문에 답변
    
    정답지 편집 탭에서 사용하는 함수입니다.
    
    Args:
        ocr_text: 참조용 텍스트 (OCR_TEXT)
        answer_json: 참조용 정답 JSON (GIVEN_ANSWER)
        question: 질문할 텍스트 (QUESTION)
        model_name: 사용할 OpenAI 모델명 (기본값: gpt-4o-2024-08-06)
        use_langchain: langchain_openai 사용 여부 (기본값: False, OpenAI 직접 호출)
        temperature: 모델 temperature (기본값: 0.0)
        
    Returns:
        OpenAI 응답 JSON 딕셔너리
    """
    # API 키 가져오기
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 필요합니다. .env 파일에 설정하세요.")
    
    # 프롬프트 구성 (prompting.py 형식)
    # ocr_text: 참조용 OCR 텍스트 (GIVEN_TEXT)
    # answer_json: 참조용 정답 JSON (GIVEN_ANSWER)
    # question: 추출할 대상 OCR 텍스트 (QUESTION)
    given_answer_str = json.dumps(answer_json, ensure_ascii=False, indent=2)
    prompt = f"""GIVEN_TEXT:
{ocr_text}

위 글이 주어지면 아래의 내용이 정답이야! 
{given_answer_str}

MISSION:
1.너는 위 GIVEN_TEXT를 보고 아래에 주어지는 QUESTION에 대한 답을 찾아내야 해
2.답을 찾을때는 해당 값의 누락이 없어야 해
3.임의로 글을 수정하거나 추가하지 말고 QUESTION의 단어 안에서 답을 찾아내야 해
4.QUESTION 안에 항목이 없는 것은 None으로 출력해야 해
5.출력형식은 **json** 형태여야 해

QUESTION:
{question}

ANSWER:
"""
    
    # OpenAI API 호출
    try:
        if use_langchain:
            # langchain_openai 방식
            if not LANGCHAIN_AVAILABLE:
                raise ImportError("langchain_openai가 설치되어 있지 않습니다. pip install langchain-openai로 설치하세요.")
            
            chat_model = ChatOpenAI(
                model_name=model_name,
                temperature=temperature,
                streaming=False,
            )
            result_text = chat_model.invoke(prompt).content
        else:
            # OpenAI 직접 호출 방식
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
        
        if not result_text:
            raise Exception("OpenAI API 응답에 텍스트가 없습니다.")
        
        # JSON 추출 (마크다운 코드 블록 제거)
        result_text = result_text.replace('```json', '').replace('```', '').strip()
        result_json = json.loads(result_text)
        
        # 키 순서 재정렬 (answer_json을 참조로 사용)
        result_json = reorder_json_keys(result_json, answer_json)
        
        return result_json
    
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 파싱 실패: {e}\n응답 텍스트: {result_text}")
    except Exception as e:
        raise Exception(f"OpenAI API 호출 실패: {e}")


