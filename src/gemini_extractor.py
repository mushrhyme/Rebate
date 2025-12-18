"""
Gemini Vision API를 사용하여 PDF를 페이지별 JSON으로 변환하는 모듈

PDF 파일을 이미지로 변환하고, Gemini Vision API로 각 페이지를 분석하여
구조화된 JSON 결과를 반환합니다. 캐시 기능을 통해 재현성을 보장합니다.
"""

import json
import re
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from pdf2image import convert_from_path
from PIL import Image, ImageFile

# DecompressionBombWarning 방지: 이미지 크기 제한 증가
Image.MAX_IMAGE_PIXELS = None  # 제한 없음 (또는 충분히 큰 값으로 설정)
ImageFile.LOAD_TRUNCATED_IMAGES = True  # 손상된 이미지도 로드 시도
import google.generativeai as genai

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


class GeminiVisionParser:
    """Gemini Vision API를 사용하여 이미지를 구조화된 JSON으로 파싱"""
    
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3-pro-preview", prompt_version: str = "v1"):
        """
        Args:
            api_key: Google Gemini API 키 (None이면 환경변수에서 가져옴)
            model_name: 사용할 Gemini 모델 이름
            prompt_version: 프롬프트 버전 (기본값: "v1", prompts/prompt_v1.txt 파일 사용)
        """
        if api_key is None:
            api_key = os.getenv("GEMINI_API_KEY")  # .env 파일에서 환경변수 가져오기
            if not api_key:
                raise ValueError("GEMINI_API_KEY가 필요합니다. .env 파일에 GEMINI_API_KEY를 설정하거나 api_key 파라미터를 제공하세요.")
        
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
        
        self.model = genai.GenerativeModel(
            model_name=model_name,
            safety_settings=safety_settings
        )  # Gemini 모델 초기화
        self.model_name = model_name
        self.prompt_version = prompt_version  # 프롬프트 버전 저장
    
    def get_parsing_prompt(self) -> str:
        """
        Gemini Vision을 위한 구조화 파싱 프롬프트
        
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
        return prompt
    
    def parse_image(self, image: Image.Image, max_size: int = 1000, timeout: int = 120) -> Dict[str, Any]:
        """
        이미지를 Gemini Vision으로 파싱하여 JSON 반환
        
        Args:
            image: PIL Image 객체
            max_size: Gemini API에 전달할 최대 이미지 크기 (픽셀, 기본값: 600)
                      속도 개선을 위해 큰 이미지는 리사이즈됨
            timeout: API 호출 타임아웃 (초, 기본값: 120초 = 2분)
                    주의: 직접 호출하므로
                    실제 타임아웃은 Gemini API의 기본 타임아웃에 의존합니다.
            
        Returns:
            파싱 결과 JSON 딕셔너리
        """
        # 원본 이미지 정보
        original_width, original_height = image.size
        
        # 이미지 리사이즈 (Gemini API 속도 개선을 위해)
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
        
        # Gemini API 호출: 재시도 로직 포함 (SAFETY 오류 대응)
        # 직접 호출 (ThreadPoolExecutor 제거)
        max_retries = 3  # 최대 재시도 횟수
        retry_delay = 2  # 재시도 전 대기 시간 (초)
        response = None
        
        for attempt in range(max_retries):
            try:
                # 직접 호출
                chat = self.model.start_chat(history=[])
                # 1단계: 이미지만 먼저 전달 (프롬프트 없이)
                _ = chat.send_message([api_image])
                # 2단계: 프롬프트를 별도 메시지로 전달
                response = chat.send_message(self.get_parsing_prompt())
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
        
        # 응답 텍스트 추출 (content가 있으면 finish_reason과 관계없이 추출)
        if not candidate.content or not candidate.content.parts:
            raise Exception("Gemini API 응답에 content parts가 없습니다.")
        
        result_text = ""
        for part in candidate.content.parts:
            if hasattr(part, 'text') and part.text:
                result_text += part.text
        
        if not result_text:
            raise Exception("Gemini API 응답에 텍스트가 없습니다.")
        
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


def get_image_output_dir(pdf_path: str) -> str:
    """
    이미지 저장 디렉토리 경로 생성 (새 구조: img/{pdf_name}/)
    
    Args:
        pdf_path: PDF 파일 경로 (또는 파일명만)
        
    Returns:
        이미지 저장 디렉토리 경로 (img/{pdf_name}/)
    """
    pdf_name = Path(pdf_path).stem  # 확장자 제거
    # 프로젝트 루트 기준으로 img/{pdf_name}/ 디렉토리 경로 생성
    project_root = Path(__file__).parent.parent
    img_dir = project_root / "img" / pdf_name
    return str(img_dir)


def extract_pages_with_gemini(
    pdf_path: str,
    gemini_api_key: Optional[str] = None,
    gemini_model: str = "gemini-3-pro-preview",
        dpi: int = 300,
    use_gemini_cache: bool = False,  # 캐시 비활성화 (DB 사용)
    gemini_cache_path: Optional[str] = None,
    save_images: bool = False,  # 로컬 저장 비활성화 (기본값: False)
    image_output_dir: Optional[str] = None,
    use_history: bool = False,  # 히스토리 비활성화
    history_dir: Optional[str] = None
) -> tuple[List[Dict[str, Any]], List[str], Optional[List[Image.Image]]]:
    """
    PDF 파일을 Gemini로 분석하여 페이지별 JSON 결과 반환
    
    DB를 우선 사용하며, DB에 데이터가 없을 때만 Gemini API를 호출합니다.
    캐시 파일은 사용하지 않습니다.
    
    Args:
        pdf_path: PDF 파일 경로
        gemini_api_key: Gemini API 키 (None이면 환경변수 또는 기본값 사용)
        gemini_model: Gemini 모델 이름
        dpi: PDF 변환 해상도 (기본값: 300)
        use_gemini_cache: Gemini 캐시 사용 여부 (기본값: False, 사용 안 함)
        gemini_cache_path: Gemini 캐시 파일 경로 (사용 안 함)
        save_images: 이미지를 파일로 저장할지 여부 (기본값: False, 사용 안 함)
        image_output_dir: 이미지 저장 디렉토리 (사용 안 함)
        use_history: 히스토리 관리 사용 여부 (기본값: False, 사용 안 함)
        history_dir: 히스토리 디렉토리 (사용 안 함)
        
    Returns:
        (페이지별 Gemini 파싱 결과 JSON 리스트, 이미지 파일 경로 리스트, PIL Image 객체 리스트) 튜플
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
    
    # 2. DB에 데이터가 없으면 Gemini API 호출
    # PDF를 이미지로 변환
    pdf_processor = PDFProcessor(dpi=dpi)  # PDF 처리기 생성
    images = pdf_processor.convert_pdf_to_images(pdf_path)  # PDF → 이미지 변환
    pil_images = images  # PIL Image 객체 리스트 저장 (DB 저장용)
    print(f"PDF 변환 완료: {len(images)}개 페이지")
    
    # 로컬 저장 비활성화 (DB에만 저장)
    image_paths = [None] * len(images)  # 항상 None 리스트
    
    # Gemini Vision으로 각 페이지 파싱
    gemini_parser = GeminiVisionParser(api_key=gemini_api_key, model_name=gemini_model, prompt_version="v2")  # Gemini 파서 생성
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
                thread_parser = GeminiVisionParser(api_key=gemini_api_key, model_name=gemini_model)
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
                print(f"페이지 {idx+1}/{len(images)} Gemini Vision 파싱 중...", end="", flush=True)
                
                page_json = gemini_parser.parse_image(images[idx])  # 각 페이지 파싱
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


class GeminiTwoStageParser:
    """
    2단계 파이프라인을 사용하는 Gemini 파서
    
    Step 1: Vision 모델로 이미지에서 raw text 추출 (행 누락 0%)
    Step 2: Text 모델로 raw text를 JSON으로 구조화 (행 누락 0%)
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None, 
        vision_model: str = "gemini-3-pro-preview",
        text_model: str = "gemini-3-pro-preview"
    ):
        """
        Args:
            api_key: Google Gemini API 키 (None이면 환경변수에서 가져옴)
            vision_model: Step 1에 사용할 Vision 모델 이름
            text_model: Step 2에 사용할 Text 모델 이름
        """
        if api_key is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY가 필요합니다. .env 파일에 GEMINI_API_KEY를 설정하거나 api_key 파라미터를 제공하세요.")
        
        genai.configure(api_key=api_key)
        
        # 안전성 설정: 문서 분석을 위해 필터 완화
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
        
        # Vision 모델 (Step 1용)
        self.vision_model = genai.GenerativeModel(
            model_name=vision_model,
            safety_settings=safety_settings
        )
        
        # Text 모델 (Step 2용)
        self.text_model = genai.GenerativeModel(
            model_name=text_model,
            safety_settings=safety_settings
        )
        
        self.vision_model_name = vision_model
        self.text_model_name = text_model
    
    def extract_raw_text(self, image: Image.Image, max_retries: int = 2) -> str:
        """
        Step 1: 이미지에서 raw text 추출 (행 누락 0%)
        
        Args:
            image: PIL Image 객체
            max_retries: 최대 재시도 횟수
            
        Returns:
            raw_text: 줄 단위로 추출된 원본 텍스트 문자열
            예: "管理番号\t商品名\t数量\t金額\n001\t商品A\t10\t1000\n002\t商品B\t20\t2000"
        """
        step1_prompt = """이 이미지에 있는 모든 텍스트를 줄 단위로 순서를 유지하여 그대로 출력해주세요.
해석하지 말고 원본 텍스트를 그대로 반환하세요.
요약, 구조화, 통합, 삭제를 하지 마세요.
이미지에서 감지된 모든 텍스트 라인을 1행도 빠짐없이 출력하세요."""
        
        retry_delay = 2  # 원래 설정으로 복원
        for attempt in range(max_retries):
            try:
                # 원래 방식 유지: chat을 사용한 2단계 전송 (이 방식이 더 빠름)
                chat = self.vision_model.start_chat(history=[])
                _ = chat.send_message([image])  # 이미지 먼저 전달
                response = chat.send_message(step1_prompt)  # 프롬프트 전달
                
                # 응답 텍스트 추출
                if not response.candidates or not response.candidates[0].content:
                    raise Exception("Gemini API 응답에 content가 없습니다.")
                
                result_text = ""
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        result_text += part.text
                
                if not result_text:
                    raise Exception("Gemini API 응답에 텍스트가 없습니다.")
                
                return result_text.strip()
                
            except Exception as e:
                error_msg = str(e)
                if "SAFETY" in error_msg or attempt < max_retries - 1:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)  # 출력 제거로 속도 개선
                        retry_delay *= 2
                        continue
                raise Exception(f"Step 1 실패 ({max_retries}회 시도): {error_msg}")
    
    def build_json_from_raw_text(self, raw_text: str, max_retries: int = 2) -> Dict[str, Any]:
        """
        Step 2: raw text를 JSON으로 구조화 (행 누락 0%)
        
        Args:
            raw_text: Step 1에서 추출된 raw text 문자열
            max_retries: 최대 재시도 횟수
            
        Returns:
            json_result: 구조화된 JSON 딕셔너리
            예: {
                "text": "...",
                "document_number": "...",
                "items": [{"management_id": "...", ...}, ...],
                ...
            }
        """
        step2_prompt = f"""다음은 일본어 条件請求書 문서의 OCR 텍스트입니다.
이 텍스트를 아래 JSON 스키마에 맞게 구조화해주세요.

---
{raw_text}
---

아래 구조로 JSON만 출력하세요:

{{
  "items": [
    {{
      "management_id": "...",
      "product_name": "...",
      "quantity": ...,
      "case_count": ...,
      "bara_count": ...,
      "units_per_case": ...,
      "amount": ...,
      "customer": "..."
    }}
  ],
  "page_role": "cover | main | detail | reply"
}}

규칙:
- items는 raw_text 내 테이블의 모든 행과 1:1로 대응해야 합니다.
- 같은 관리番号가 반복되어도 각 행을 개별 item으로 생성하세요.
- 바코드(13자리 숫자)로 시작하면 상품명에서 제거하세요.
- 수량이 케이스/바라 형식이면 quantity는 null로 설정하세요.
- 정보가 없으면 null을 사용하세요.

JSON 외 추가 설명은 출력하지 않습니다."""
        
        retry_delay = 2  # 원래 설정으로 복원
        for attempt in range(max_retries):
            try:
                response = self.text_model.generate_content(step2_prompt)
                
                # 응답 텍스트 추출
                if not response.candidates or not response.candidates[0].content:
                    raise Exception("Gemini API 응답에 content가 없습니다.")
                
                result_text = ""
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text:
                        result_text += part.text
                
                if not result_text:
                    raise Exception("Gemini API 응답에 텍스트가 없습니다.")
                
                # JSON 추출 (마크다운 코드 블록 제거)
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    json_result = json.loads(json_match.group())
                    return json_result
                else:
                    raise Exception("응답에서 JSON을 찾을 수 없습니다.")
                    
            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)  # 출력 제거로 속도 개선
                    retry_delay *= 2
                    continue
                raise Exception(f"Step 2 JSON 파싱 실패 ({max_retries}회 시도): {e}")
            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)  # 출력 제거로 속도 개선
                    retry_delay *= 2
                    continue
                raise Exception(f"Step 2 실패 ({max_retries}회 시도): {error_msg}")
    
    def parse_image_two_stage(
        self, 
        image: Image.Image, 
        max_size: int = 600,  # 원래 설정으로 복원
        max_retries: int = 2  # 재시도 횟수 감소: 3 → 2
    ) -> Dict[str, Any]:
        """
        2단계 파이프라인으로 이미지를 JSON으로 파싱
        
        Args:
            image: PIL Image 객체
            max_size: Gemini API에 전달할 최대 이미지 크기 (픽셀, 기본값: 800)
            max_retries: 각 단계별 최대 재시도 횟수
            
        Returns:
            json_result: 구조화된 JSON 딕셔너리
        """
        # 이미지 리사이즈 (속도 개선: 작은 이미지가 더 빠름)
        original_width, original_height = image.size
        api_image = image
        if original_width > max_size or original_height > max_size:
            ratio = min(max_size / original_width, max_size / original_height)
            new_width = int(original_width * ratio)
            new_height = int(original_height * ratio)
            api_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Step 1: Raw Text 추출
        step1_start = time.time()
        raw_text = self.extract_raw_text(api_image, max_retries=max_retries)
        step1_duration = time.time() - step1_start
        
        # Step 2: JSON 구조화
        step2_start = time.time()
        json_result = self.build_json_from_raw_text(raw_text, max_retries=max_retries)
        step2_duration = time.time() - step2_start
        
        # 소요시간만 출력
        total_duration = step1_duration + step2_duration
        print(f"소요 시간: {total_duration:.1f}초 (Step 1: {step1_duration:.1f}초, Step 2: {step2_duration:.1f}초)", end="", flush=True)
        
        return json_result


def extract_raw_text(image_path: str, api_key: Optional[str] = None, vision_model: str = "gemini-3-pro-preview") -> str:
    """
    Step 1만 실행: 이미지에서 raw text 추출
    
    Args:
        image_path: 이미지 파일 경로
        api_key: Gemini API 키 (None이면 환경변수에서 가져옴)
        vision_model: Vision 모델 이름
        
    Returns:
        raw_text: 추출된 raw text 문자열
    """
    parser = GeminiTwoStageParser(api_key=api_key, vision_model=vision_model)
    image = Image.open(image_path)
    return parser.extract_raw_text(image)


def build_json_from_raw_text(raw_text: str, api_key: Optional[str] = None, text_model: str = "gemini-3-pro-preview") -> Dict[str, Any]:
    """
    Step 2만 실행: raw text를 JSON으로 구조화
    
    Args:
        raw_text: Step 1에서 추출된 raw text 문자열
        api_key: Gemini API 키 (None이면 환경변수에서 가져옴)
        text_model: Text 모델 이름
        
    Returns:
        json_result: 구조화된 JSON 딕셔너리
    """
    parser = GeminiTwoStageParser(api_key=api_key, text_model=text_model)
    return parser.build_json_from_raw_text(raw_text)


def parse_image_two_stage(
    image_path: str,
    api_key: Optional[str] = None,
    vision_model: str = "gemini-3-pro-preview",
    text_model: str = "gemini-3-pro-preview",
    max_size: int = 1000
) -> Dict[str, Any]:
    """
    2단계 파이프라인으로 이미지를 JSON으로 파싱 (편의 함수)
    
    Args:
        image_path: 이미지 파일 경로
        api_key: Gemini API 키 (None이면 환경변수에서 가져옴)
        vision_model: Step 1에 사용할 Vision 모델 이름
        text_model: Step 2에 사용할 Text 모델 이름
        max_size: 최대 이미지 크기 (픽셀)
        
    Returns:
        json_result: 구조화된 JSON 딕셔너리
    """
    parser = GeminiTwoStageParser(api_key=api_key, vision_model=vision_model, text_model=text_model)
    image = Image.open(image_path)
    return parser.parse_image_two_stage(image, max_size=max_size)

