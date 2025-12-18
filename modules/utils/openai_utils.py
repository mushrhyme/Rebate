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
    
    # 프롬프트 구성
    given_answer_str = json.dumps(answer_json, ensure_ascii=False, indent=2)
    prompt = f"""
    OCR 추출 결과:
    {ocr_text}
    
    정답:
    {given_answer_str}
    
    **중요**
    - ocr_text를 보고 question에 대한 답을 추출
    - 답 출력 시에는 불필요한 설명 없이 given_answer_str와 같이 json 형식으로 출력
    - 누락되는 값 없이 모든 제품을 추출
    - 추출할 항목이 없는 것은 지어내지 않고 None으로 출력(예: 케이스 개수가 없는 경우에는 None)
    
    질문:
    {question}
    
    답:
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
        return result_json
    
    except json.JSONDecodeError as e:
        raise Exception(f"JSON 파싱 실패: {e}\n응답 텍스트: {result_text}")
    except Exception as e:
        raise Exception(f"OpenAI API 호출 실패: {e}")


def extract_json_from_text(
    text: str,
    api_key: Optional[str] = None,
    model_name: str = "gpt-4o-mini",
    prompt_version: str = "v2",
    reference_json: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    텍스트를 OpenAI API로 분석하여 JSON 결과 반환
    
    정답지 편집 탭 등에서 사용하는 편의 함수입니다.
    
    Args:
        text: Upstage 등에서 추출한 텍스트
        api_key: OpenAI API 키 (None이면 환경변수에서 가져옴)
        model_name: OpenAI 모델 이름 (기본값: "gpt-4o-mini")
        prompt_version: 프롬프트 버전 (기본값: "v2")
        reference_json: 기준 페이지의 JSON 정보 (다른 페이지 추출 시 참조용, 기본값: None)
        
    Returns:
        파싱 결과 JSON 딕셔너리
    """
    # src.openai_extractor의 OpenAITextParser를 사용
    from src.openai_extractor import OpenAITextParser
    parser = OpenAITextParser(api_key=api_key, model_name=model_name, prompt_version=prompt_version)
    return parser.parse_text(text, reference_json=reference_json)

