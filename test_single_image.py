"""
단일 이미지에 대해 Gemini Vision API를 테스트하는 스크립트
"""

import json
import os
import sys
import time
from pathlib import Path
from PIL import Image
import google.generativeai as genai
from dotenv import load_dotenv

# .env 파일 로드
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)


def extract_text_with_ocr(image: Image.Image, max_size: int = 600) -> str:
    """
    Gemini Vision API를 사용하여 이미지에서 텍스트를 OCR로 추출
    
    Args:
        image: PIL Image 객체
        max_size: Gemini API에 전달할 최대 이미지 크기 (픽셀, 기본값: 600)
        
    Returns:
        추출된 텍스트 문자열
    """
    # API 키 확인
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY가 필요합니다. .env 파일에 GEMINI_API_KEY를 설정하세요.")
    
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
    
    model = genai.GenerativeModel(
        model_name="gemini-3-pro-preview",
        safety_settings=safety_settings
    )
    
    # 이미지 리사이즈 (Gemini API 속도 개선을 위해)
    original_width, original_height = image.size
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
    
    # OCR 프롬프트: 텍스트만 추출
    ocr_prompt = """이 이미지에서 모든 텍스트를 정확하게 추출해주세요.
이미지에 있는 모든 텍스트를 순서대로, 줄바꿈과 공백을 최대한 유지하여 그대로 추출하세요.
추가 설명이나 해석 없이 순수 텍스트만 반환해주세요."""
    
    # Gemini API 호출: 재시도 로직 포함
    max_retries = 3  # 최대 재시도 횟수
    retry_delay = 2  # 재시도 전 대기 시간 (초)
    
    for attempt in range(max_retries):
        try:
            # 이미지만 먼저 전달하는 방식으로 시도
            chat = model.start_chat(history=[])
            # 1단계: 이미지만 먼저 전달 (프롬프트 없이)
            _ = chat.send_message([api_image])
            # 2단계: 프롬프트를 별도 메시지로 전달
            response = chat.send_message(ocr_prompt)
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
    
    # 응답 텍스트 추출
    if not candidate.content or not candidate.content.parts:
        raise Exception("Gemini API 응답에 content parts가 없습니다.")
    
    result_text = ""
    for part in candidate.content.parts:
        if hasattr(part, 'text') and part.text:
            result_text += part.text
    
    if not result_text:
        raise Exception("Gemini API 응답에 텍스트가 없습니다.")
    
    return result_text


def test_single_image(image_path: str):
    """
    단일 이미지를 Gemini Vision API로 OCR하여 텍스트 추출
    
    Args:
        image_path: 테스트할 이미지 파일 경로
    """
    print("=" * 60)
    print(f"이미지 OCR 테스트: {image_path}")
    print("=" * 60)
    
    # 이미지 로드
    try:
        image = Image.open(image_path)
        print(f"✅ 이미지 로드 성공: {image.size[0]}x{image.size[1]}px")
    except Exception as e:
        print(f"❌ 이미지 로드 실패: {e}")
        return
    
    # OCR로 텍스트 추출
    print("\n🔄 OCR 텍스트 추출 시작...")
    try:
        extracted_text = extract_text_with_ocr(image, max_size=600)
        print("\n✅ OCR 성공!")
        print("\n" + "=" * 60)
        print("추출된 텍스트:")
        print("=" * 60)
        print(extracted_text)
        print("=" * 60)
        
        # 결과를 텍스트 파일로 저장
        output_path = Path(image_path).parent / f"{Path(image_path).stem}_ocr.txt"
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(extracted_text)
        print(f"\n💾 결과 저장: {output_path}")
        
        # JSON 형식으로도 저장 (메타데이터 포함)
        json_output_path = Path(image_path).parent / f"{Path(image_path).stem}_ocr.json"
        result_data = {
            "image_path": str(image_path),
            "image_size": {"width": image.size[0], "height": image.size[1]},
            "extracted_text": extracted_text,
            "text_length": len(extracted_text)
        }
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        print(f"💾 JSON 결과 저장: {json_output_path}")
        
    except Exception as e:
        print(f"\n❌ OCR 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 명령줄 인자로 이미지 경로 받기
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        # 기본 이미지 경로 (사용자가 지정한 경로)
        image_path = "image.png"
    
    test_single_image(image_path)

